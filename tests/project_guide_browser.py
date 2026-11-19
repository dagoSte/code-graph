"""Project guide and explicit handoff to an agent, with real local data and delayed responses."""
import argparse
import json
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
from urllib.parse import urlsplit
from playwright.sync_api import expect,sync_playwright
from test_workbench import SOURCES

REPO=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);parser.add_argument('--screenshot',type=Path);args=parser.parse_args()
    checks=[];errors=[];external=[]
    def check(ok,label):
        if not ok:raise AssertionError(label)
        checks.append(label)
    with tempfile.TemporaryDirectory(prefix='cg-guide-browser-') as directory:
        workspace=Path(directory);a=workspace/'catalogo-demo';a.mkdir();b=workspace/'altro-progetto';b.mkdir()
        for name,text in SOURCES.items():
            path=a/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        (a/'README.md').write_text('# Catalogo demo\nUna pipeline alimenta sito e MCP. Questa è documentazione dichiarata.\n')
        (b/'main.py').write_text('def alternate():return 1\n')
        marker=workspace/'project-executed';(a/'never_import.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")\n')
        process=subprocess.Popen([str(REPO/'scripts/cg'),'explore','--workspace',str(workspace),'--no-open'],cwd=REPO,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        try:
            selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
            if not selector.select(15):raise RuntimeError('Explorer did not start')
            line=process.stdout.readline();url=re.search(r'http://127\.0\.0\.1:\d+/#\S+',line).group(0);token=urlsplit(url).fragment
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=args.browser);context=browser.new_context(viewport={'width':1500,'height':1300},accept_downloads=True);page=context.new_page()
                page.on('pageerror',lambda err:errors.append(str(err)))
                page.on('request',lambda req:external.append(req.url) if req.url.startswith(('http:','https:')) and urlsplit(req.url).hostname!='127.0.0.1' else None)
                with page.expect_response(lambda response:response.url.endswith('/api/bootstrap'),timeout=30000) as bootstrap:
                    page.goto(url)
                check(bootstrap.value.status==200,'authenticated bootstrap is ready');expect(page.locator('#project-input')).not_to_have_value('',timeout=30000);check(not errors,'dashboard scripts initialize without errors');check('Prepara la modifica.' in page.locator('#start').inner_text(),'user landing copy is preserved')
                page.fill('#project-input',str(a));page.click('#analyze',no_wait_after=True);expect(page.locator('#overview')).to_be_visible(timeout=30000)
                check('Inizia da qui' in page.locator('#overview').inner_text(),'overview opens with the start-here questions')
                page.click('#guide-button');expect(page.locator('#guide-intent')).to_be_visible();check(page.locator('.guide-process li').count()==6,'guide explains all six steps of the agent workflow')
                check('pkg/site.py' in page.locator('#guide').inner_text() and 'pkg/mcp_server.py' in page.locator('#guide').inner_text(),'guide uses actual indexed entrypoints with file evidence')
                page.get_by_role('button',name='Prepara incarico per l’agente',exact=True).click();check('Scrivi prima' in page.locator('#guide').inner_text(),'an empty request does not produce a handoff')
                page.get_by_role('button',name='Prepara modifica',exact=True).click();check('possibili conseguenze e test pertinenti' in page.locator('#guide-intent').input_value(),'recommended prompt uses the requested wording')
                page.get_by_role('button',name='Impatto',exact=True).click();check(page.locator('#guide-intent').input_value()=='Se cambio questa funzione, cosa potrebbe risentirne?','quick impact action fills the request without executing it')
                request='Aggiungi una categoria al catalogo preservando i campi letti dal sito.';page.fill('#guide-intent',request);page.get_by_role('button',name='Prepara incarico per l’agente',exact=True).click()
                expect(page.get_by_role('button',name='Scarica guida per agente',exact=True)).to_be_visible()
                with page.expect_download() as download:page.get_by_role('button',name='Scarica guida per agente',exact=True).click()
                target=workspace/'agent-guide.txt';download.value.save_as(target);text=target.read_text();facts=json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
                check(Path(facts['path']).resolve()==a.resolve() and facts['request']==request and facts['index_revision'],'download identifies project, request and index revision')
                check('prepare' in text and '--verify-symbol' in text and 'review' in text and 'Esegui i test' in text,'handoff directs the agent to source, impact, tests and comparison')
                check(token not in text and '127.0.0.1' not in text,'download contains no session token or service URL')
                check(all(item.get('selector') or item.get('feature') for item in facts['entrypoint_candidates']) and all('id' not in item for item in facts['entrypoint_candidates']),'handoff uses stable selectors rather than revision-specific SQLite IDs')
                if args.screenshot:
                    page.set_viewport_size({'width':1500,'height':1700});page.locator('#guide').evaluate('(node)=>{for(let item=node;item;item=item.parentElement)item.scrollTop=0;}');args.screenshot.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(args.screenshot),full_page=True)
                hostile='</pre><img src="https://invalid.example/x" onerror="alert(1)">';page.fill('#guide-intent',hostile);page.get_by_role('button',name='Prepara incarico per l’agente',exact=True).click()
                rendered=page.locator('#guide pre').last.inner_text();rendered_facts=json.JSONDecoder().raw_decode(rendered[rendered.index('{'):])[0]
                check(page.locator('#guide img').count()==0 and rendered_facts['request']==hostile,'untrusted request remains literal text')
                page.select_option('#language','en');expect(page.locator('#guide h2').first).to_have_text('What the agent will do');expect(page.locator('#guide-intent')).to_have_value(hostile)
                check(page.get_by_role('button',name='Prepare task for the agent',exact=True).count()==1,'guide controls switch to English and keep the user request')
                page.select_option('#theme','dark');expect(page.locator('#guide-intent')).to_be_visible();check(page.locator('html').get_attribute('data-theme')=='dark','guide supports the dark theme')
                page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(100);check(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'guide fits mobile width')
                page.select_option('#language','it');page.click('#navigation-toggle');page.click('#overview-button')
                check(page.locator('#overview').is_visible() and page.locator('#navigation').evaluate('(node)=>node.inert'),'mobile navigation returns to the overview and closes the menu')
                held=[]
                def hold(route):
                    if not held:held.append((route,route.fetch()))
                    else:route.continue_()
                page.route('**/api/features?**',hold);page.click('#navigation-toggle');page.click('#guide-button');page.wait_for_timeout(100);check(bool(held),'guide response is held before switching projects')
                page.click('#change');page.fill('#project-input',str(b));page.click('#analyze',no_wait_after=True);expect(page.locator('#overview')).to_be_visible(timeout=30000);page.click('#navigation-toggle');page.click('#guide-button');expect(page.locator('#guide-intent')).to_be_visible()
                held[0][0].fulfill(response=held[0][1]);page.unroute('**/api/features?**',hold);page.wait_for_timeout(100)
                check(page.locator('#guide h1').inner_text()==b.name and 'pkg/site.py' not in page.locator('#guide').inner_text(),'late guide data cannot cross into another project')
                check(page.locator('#guide-intent').input_value()=='','new project starts with its own request')
                check(not marker.exists(),'guide preparation never imports project code')
                check(not errors and not external,'guide has no JavaScript errors or external HTTP requests')
                browser.close()
        except BaseException:
            print(json.dumps(dict(errors=errors,external=external)),flush=True);raise
        finally:process.terminate();process.wait(timeout=10)
    print(json.dumps(dict(status='PASS',checks=checks,errors=errors,external_requests=external),indent=2))


if __name__=='__main__':main()
