"""Real browser journey for cg explore. Optional development dependency: Playwright."""
import argparse
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect
from test_workbench import SOURCES

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', required=True)
    parser.add_argument('--overview-screenshot', type=Path)
    parser.add_argument('--detail-screenshot', type=Path)
    parser.add_argument('--efficiency-screenshot', type=Path)
    parser.add_argument('--dark-screenshot', type=Path)
    parser.add_argument('--usage-guide-screenshot', type=Path)
    args = parser.parse_args()
    checks, errors, external = [], [], []

    def check(value, label):
        if not value:
            raise AssertionError(label)
        checks.append(label)

    with tempfile.TemporaryDirectory(prefix='cg-guided-browser-') as directory:
        workspace = Path(directory)
        root = workspace / 'progetto-demo'
        root.mkdir()
        for name, content in SOURCES.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (root / 'hostile.py').write_text('def unsafe(value="<img src=https://invalid.example/a onerror=alert(1)>"): return value\n')
        marker = workspace / 'project-executed'
        (root / 'never_import.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("bad")\n')
        process = subprocess.Popen([str(REPO / 'scripts/cg'), 'explore', '--workspace', str(workspace), '--no-open'], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(15):
                raise RuntimeError('local explorer did not start')
            line = process.stdout.readline()
            match = re.search(r'http://127\.0\.0\.1:\d+/#\S+', line)
            if not match:
                raise RuntimeError('unexpected startup: ' + line)
            url = match.group(0)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
                context = browser.new_context(viewport={'width': 1600, 'height': 1000}, accept_downloads=True)
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
                page.on('request', lambda req: external.append(req.url) if req.url.startswith(('http:', 'https:')) and urlsplit(req.url).hostname != '127.0.0.1' else None)
                page.goto(url)
                expect(page.locator('#project-input')).not_to_have_value('')
                check(page.locator('#start').is_visible(), 'folder selection is the entry screen')
                check(page.evaluate('location.hash') == '', 'session token is removed from the address bar')
                page.click('#usage-guide-button')
                usage = page.locator('#usage-guide')
                expect(usage).to_be_visible()
                check(usage.locator('.usage-guide-card').count() == 4, 'the usage guide opens as an illustrated dashboard panel')
                expect(usage.locator('[data-guide-action="search"]')).to_be_hidden()
                usage.locator('#usage-guide-project').click()
                expect(page.locator('#project-input')).to_be_focused()
                page.route('**/api/choose', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps({'native': False})))
                page.click('#choose')
                page.wait_for_selector('#folder-dialog[open]')
                page.locator('#directory-list button').filter(has_text='progetto-demo').click()
                page.click('#use-folder')
                check(Path(page.locator('#project-input').input_value()).resolve() == root.resolve(), 'folder browser selects a real project path')
                page.click('#analyze')
                page.wait_for_selector('#workspace', state='visible', timeout=30000)
                check(page.locator('#project-name').inner_text() == root.name, 'project overview opens after analysis')
                page.click('#usage-guide-button')
                expect(usage.locator('#usage-guide-start')).to_be_hidden()
                if args.usage_guide_screenshot:
                    args.usage_guide_screenshot.parent.mkdir(parents=True, exist_ok=True)
                    usage.screenshot(path=str(args.usage_guide_screenshot))
                usage.locator('[data-guide-action="search"]').click()
                expect(page.locator('#search')).to_be_focused()
                expect(page.locator('#search-mode')).to_have_value('hybrid')
                page.select_option('#search-mode', 'symbols')
                page.click('#usage-guide-button')
                usage.locator('[data-guide-action="technical-button"]').click()
                expect(page.locator('#graph-panel')).to_be_visible()
                page.click('#overview-button')
                page.click('#usage-guide-button')
                page.keyboard.press('Escape')
                expect(usage).to_be_hidden()
                expect(page.locator('#usage-guide-button')).to_be_focused()
                check(not marker.exists(), 'analyzing a project never imports its modules')
                for area in ['MCP', 'Sito / API', 'Comandi e pipeline', 'Pubblicazione dichiarata']:
                    check(area in page.locator('#semantic-areas').inner_text(), 'verified area appears: ' + area)
                check('non misurano la completezza' in page.locator('#overview').inner_text(), 'coverage is described without a completeness claim')
                if args.overview_screenshot:
                    args.overview_screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.overview_screenshot), full_page=True)
                page.locator('#folders [data-area="folder:pkg"]').click()
                frame = page.frame_locator('#graph')
                frame.locator('.node').first.wait_for(timeout=15000)
                check(frame.locator('body').evaluate('(node)=>node.classList.contains("embedded")'), 'the existing graph is embedded in the guided interface')
                check(page.locator('#area-name').inner_text() == 'pkg', 'real folder selection scopes the graph')
                page.fill('#search', 'work')
                result = page.locator('#search-results .symbol-result').filter(has=page.locator('strong', has_text=re.compile('^work$')))
                result.first.wait_for()
                result.first.click()
                expect(page.locator('#symbol-name')).to_have_text('work')
                check('def work()' in page.locator('#inspection-content').inner_text(), 'selected function displays its verified source body')
                if args.detail_screenshot:
                    frame.locator('.node').first.wait_for()
                    args.detail_screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.detail_screenshot), full_page=True)
                page.locator('[data-tab="calls"]').click()
                expect(page.locator('#inspection-content')).to_contain_text('get_value')
                check('get_value' in page.locator('#inspection-content').inner_text(), 'callers include a real module-qualified source path')
                check('return work()' in page.locator('#inspection-content').inner_text(), 'callsite source text accompanies the relation')
                page.locator('[data-tab="impact"]').click()
                expect(page.locator('#inspection-content')).to_contain_text('tests/test_core.py')
                check('tests/test_core.py' in page.locator('#inspection-content').inner_text(), 'impact identifies connected tests with evidence')
                page.click('#efficiency-button')
                expect(page.locator('#efficiency svg')).to_have_count(2)
                check('caratteri Unicode/4' in page.locator('#efficiency').inner_text(), 'efficiency labels token figures as estimates')
                page.get_by_role('button', name='Misura la scheda sorgente selezionata').click()
                expect(page.locator('#efficiency')).to_contain_text('source_tab_vs_legacy_all_fields')
                check(page.locator('#efficiency .measure-table tr').count() >= 3, 'efficiency compares real impact and source-tab samples')
                page.fill('#forecast-count','25')
                check('NaN' not in page.locator('#efficiency .projection').inner_text(), 'forecast uses a finite median from observed samples')
                if args.efficiency_screenshot:page.screenshot(path=str(args.efficiency_screenshot),full_page=True)
                page.select_option('#language','en')
                expect(page.locator('#efficiency h1')).to_have_text('Efficiency')
                check(page.locator('#search').get_attribute('placeholder') == 'Name, class or path…', 'English applies to controls and dynamic panels')
                page.select_option('#theme','dark')
                check(page.locator('html').get_attribute('data-theme') == 'dark', 'dark theme is applied explicitly')
                if args.dark_screenshot:page.screenshot(path=str(args.dark_screenshot),full_page=True)
                with page.expect_download() as measures_download:
                    page.get_by_role('button',name='Download measurements JSON').click()
                measures_path=workspace/'measures.json';measures_download.value.save_as(measures_path);measures=json.loads(measures_path.read_text())
                check(measures['measured_tokens'] is None and len(measures['samples']) >= 2, 'downloaded efficiency measurements preserve provenance and units')
                page.click('#overview-button')
                page.fill('#search','work')
                page.locator('#search-results .symbol-result').filter(has=page.locator('strong',has_text=re.compile('^work$'))).first.click()
                expect(page.locator('#symbol-name')).to_have_text('work')
                expect(page.frame_locator('#graph').locator('html')).to_have_attribute('data-theme','dark')
                check('def work()' in page.locator('#inspection-content').inner_text(), 'theme and language preserve literal source text')
                page.click('[data-tab="agent"]')
                page.click('#inspection-content button')
                expect(page.locator('#inspection-content pre')).to_contain_text('## TARGET')
                check(len(page.locator('#inspection-content pre').inner_text()) <= 4000, 'agent context respects the selected character budget')
                page.select_option('#language','it');page.select_option('#theme','light')
                page.click('[data-tab="code"]')
                expect(page.locator('#inspection-content')).to_contain_text('def work()')
                with page.expect_download() as downloaded:
                    page.click('#download')
                artifact = workspace / 'downloaded.html'
                downloaded.value.save_as(artifact)
                content = artifact.read_text()
                check('"embedded":false' in content and 'Content-Security-Policy' in content, 'download is a standalone offline snapshot')
                page.click('#overview-button')
                page.get_by_role('button', name='Mostra le osservazioni sulle risorse').click()
                expect(page.locator('#overview')).to_contain_text('Connessione database')
                text = page.locator('#overview').inner_text()
                check('Lettura file' in text and 'Scrittura file' in text and 'Avvio processo' in text, 'file/database/process evidence remains separate from call edges')
                page.fill('#search', 'unsafe')
                page.locator('#search-results .symbol-result').filter(has=page.locator('strong', has_text=re.compile('^unsafe$'))).click()
                expect(page.locator('#symbol-name')).to_have_text('unsafe')
                check('<img src=' in page.locator('#inspection-content').inner_text(), 'hostile source is displayed literally')
                check(page.locator('img').count() == 0, 'source text does not create HTML elements')
                expect(page.locator('#inspection-content')).to_be_focused()
                (root / 'hostile.py').write_text('def changed(): return 1\n')
                page.locator('#search').focus()
                page.locator('#search-results .symbol-result').first.click()
                page.wait_for_selector('#message', state='visible')
                check('cambiato' in page.locator('#message').inner_text(), 'stale source produces an explicit reindex message')
                page.click('#refresh')
                page.wait_for_selector('#workspace', state='visible', timeout=30000)
                page.fill('#search', 'changed')
                page.locator('#search-results .symbol-result').first.wait_for()
                check(page.locator('#search-results .symbol-result strong').first.inner_text() == 'changed', 'reindex refreshes symbol data and generation')
                # Hold genuine responses to reproduce out-of-order navigation deterministically.
                page.fill('#search', 'work')
                page.locator('#search-results .symbol-result').filter(has=page.locator('strong', has_text=re.compile('^work$'))).first.click()
                expect(page.locator('#symbol-name')).to_have_text('work')
                held = []
                def delay_impact(route):
                    if 'confidence=low' in route.request.url:
                        held.append((route, route.fetch()))
                    else:
                        route.continue_()
                page.route('**/api/impact?*', delay_impact)
                page.select_option('#confidence', 'low')
                expect(page.locator('[data-tab="code"]')).to_have_attribute('aria-selected', 'true')
                page.click('[data-tab="impact"]')
                page.wait_for_timeout(150)
                check(bool(held), 'low-confidence impact response is held before navigation')
                page.select_option('#confidence', 'high')
                expect(page.locator('[data-tab="code"]')).to_have_attribute('aria-selected', 'true')
                page.click('[data-tab="impact"]')
                expect(page.locator('#inspection-content')).to_contain_text('Test collegati')
                route, response = held.pop()
                route.fulfill(response=response)
                page.wait_for_timeout(150)
                check(page.locator('#inspection-content h3').filter(has_text='Test collegati').count() == 1, 'late impact cannot append results under another confidence')
                page.unroute('**/api/impact?*', delay_impact)

                held = []
                def delay_resources(route):
                    held.append((route, route.fetch()))
                page.route('**/api/resources?*', delay_resources)
                page.click('#overview-button')
                page.get_by_role('button', name='Mostra le osservazioni sulle risorse').click()
                page.wait_for_timeout(150)
                check(bool(held), 'resource response is held before project change')
                other = workspace / 'altro-progetto'
                other.mkdir()
                (other / 'app.py').write_text('def beta(): return 2\n')
                (other / 'resources.py').write_text('def resource_reader():\n'+''.join(f'    open("resource_{i}.json")\n' for i in range(120)))
                page.click('#change')
                page.fill('#project-input', str(other))
                page.click('#analyze')
                expect(page.locator('#project-name')).to_have_text(other.name)
                route, response = held.pop()
                route.fulfill(response=response)
                page.wait_for_timeout(150)
                check('data/site.db' not in page.locator('#overview').inner_text(), 'late resources cannot cross into another project')
                page.unroute('**/api/resources?*', delay_resources)

                # A delayed stale error must not clear a newer symbol's inspector.
                held = []
                page.route('**/api/resources?*', delay_resources)
                page.get_by_role('button', name='Mostra le osservazioni sulle risorse').click()
                page.wait_for_timeout(150)
                page.fill('#search', 'beta')
                page.locator('#search-results .symbol-result').filter(has=page.locator('strong', has_text=re.compile('^beta$'))).click()
                expect(page.locator('#symbol-name')).to_have_text('beta')
                route, _ = held.pop()
                route.fulfill(status=409, content_type='application/json', body=json.dumps({'code':'stale_index','error':'obsolete synthetic failure'}))
                page.wait_for_timeout(150)
                check('def beta()' in page.locator('#inspection-content').inner_text() and not page.locator('#message').is_visible(), 'late stale errors cannot clear the current inspector')
                page.unroute('**/api/resources?*', delay_resources)
                page.click('[data-tab="agent"]')
                held_context=[]
                def delay_context(route):
                    if 'max_chars=1000' in route.request.url:held_context.append((route,route.fetch()))
                    else:route.continue_()
                page.route('**/api/context?*',delay_context)
                page.fill('#context-budget','1000');page.get_by_role('button',name='Genera contesto',exact=True).click()
                page.wait_for_timeout(150)
                check(bool(held_context),'context response is held before budget change')
                page.fill('#context-budget','4000');page.get_by_role('button',name='Genera contesto',exact=True).click()
                expect(page.locator('#inspection-content pre')).to_contain_text('max_chars=4000')
                route,response=held_context.pop();route.fulfill(response=response);page.wait_for_timeout(150)
                check('max_chars=4000' in page.locator('#inspection-content pre').inner_text(),'late context cannot replace the newer budget result')
                page.unroute('**/api/context?*',delay_context)
                page.fill('#search','resource_reader')
                page.locator('#search-results .symbol-result').filter(has=page.locator('strong',has_text='resource_reader')).click()
                expect(page.locator('#symbol-name')).to_have_text('resource_reader')
                page.click('[data-tab="resources"]')
                expect(page.locator('#inspection-content .resource')).to_have_count(80)
                page.get_by_role('button',name='Altre osservazioni',exact=True).click()
                expect(page.locator('#inspection-content .resource')).to_have_count(120)
                check('resource_119.json' in page.locator('#inspection-content').inner_text(),'resource pagination reaches the final observation without loss')
                page.set_viewport_size({'width': 390, 'height': 844})
                check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'guided UI has no horizontal overflow on mobile')
                page.select_option('#language','en');page.select_option('#theme','dark')
                page.reload()
                expect(page.locator('#workspace')).to_be_visible(timeout=15000)
                check(page.locator('#language').input_value()=='en' and page.locator('html').get_attribute('data-theme')=='dark', 'language and theme persist across reload')
                check(page.locator('#project-name').inner_text()==other.name, 'authenticated session resumes after reload without reindex')
                page.click('#usage-guide-button')
                expect(usage.locator('h2')).to_have_text('How to use code-graph')
                check(usage.evaluate('node => node.scrollWidth <= node.clientWidth'), 'visual guide fits an English mobile viewport in dark mode')
                expect(usage.get_by_role('link',name='Full manual, commands and examples')).to_have_attribute('href','/guide/README.md')
                usage.locator('#usage-guide-close').click()
                expect(usage).to_be_hidden()
                check(not external, 'no non-local HTTP requests: ' + repr(external))
                # Stale source is intentionally rejected with HTTP409; it can appear as a browser console resource error.
                unexpected = [message for message in errors if '409' not in message]
                check(not unexpected, 'no unexpected JavaScript or CSP errors: ' + repr(unexpected))
                browser.close()
        finally:
            if errors:
                print('Browser diagnostics:', json.dumps(errors))
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print(f'{len(checks)}/{len(checks)} guided browser checks passed')
    for label in checks:
        print('PASS ' + label)


if __name__ == '__main__':
    main()
