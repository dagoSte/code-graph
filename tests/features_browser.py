"""Feature map → bounded AI packet → refreshed change review in real Chromium."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[1]
APP = 'def save(value):return value\ndef publish(value):return save(value)\ndef run():return publish(1)\ndef archive():return "archived"\n'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True)
    parser.add_argument('--flow-screenshot',type=Path);parser.add_argument('--prepare-screenshot',type=Path)
    args=parser.parse_args();checks=[];errors=[];external=[]
    def check(ok,label):
        if not ok:raise AssertionError(label)
        checks.append(label)
    def await_response(items):
        for _ in range(200):
            if items:return
            page.wait_for_timeout(25)
        raise AssertionError('held HTTP response did not complete')
    with tempfile.TemporaryDirectory(prefix='cg-features-browser-') as directory:
        workspace=Path(directory);root=workspace/'catalogo-demo';root.mkdir();(root/'app.py').write_text(APP)
        (root/'tests').mkdir();(root/'tests/test_save.py').write_text('from app import save\ndef test_save():return save(1)\n');(root/'tests/test_app.py').write_text('from app import publish\ndef helper():return publish(1)\ndef test_publish():return helper()\n')
        (root/'README.md').write_text('# Catalogo demo\nPubblica un catalogo e prepara il prossimo archivio.\n')
        proof=dict(file='app.py',line=1,sha256=hashlib.sha256((root/'app.py').read_bytes()).hexdigest())
        manifest={'format':'codegraph-manifest/1','features':[
            dict(id='publish',title='Pubblicazione catalogo',summary='Trasforma e salva il catalogo; verifica i consumatori prima di cambiare il formato.',entrypoints=[dict(file='app.py',symbol='publish')],evidence=[proof],contracts=[dict(id='value',description='Il valore pubblicato deve arrivare al salvataggio.',evidence=[proof])]),
            dict(id='archive',title='Archiviazione',entrypoints=[dict(file='app.py',symbol='archive')]),
            dict(id='future',title='Ricerca futura',stage='planned',entrypoints=[dict(file='search.py',symbol='search')])
        ]}
        (root/'.code-graph-manifest.json').write_text(json.dumps(manifest))
        (root/'long.js').write_text('function leaf(value){return value;}const pad="'+'x'*17000+'";function caller(){return leaf(1);}caller();\n')
        marker=workspace/'executed';(root/'never_import.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")\n')
        process=subprocess.Popen([str(REPO/'scripts/cg'),'explore','--workspace',str(workspace),'--no-open'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,cwd=REPO)
        try:
            selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
            if not selector.select(15):raise RuntimeError('Explorer did not start')
            line=process.stdout.readline();match=re.search(r'http://127\.0\.0\.1:\d+/#\S+',line)
            if not match:raise RuntimeError(line)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=args.browser)
                context=browser.new_context(viewport={'width':1500,'height':1000},accept_downloads=True);page=context.new_page()
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' else None)
                page.on('request',lambda request:external.append(request.url) if request.url.startswith(('http:','https:')) and urlsplit(request.url).hostname!='127.0.0.1' else None)
                page.goto(match.group(0));page.fill('#project-input',str(root));page.click('#analyze');page.wait_for_selector('#workspace',state='visible',timeout=30000)
                page.click('#features-button');expect(page.locator('[data-feature^="declared:"]')).to_have_count(3)
                check('README' in page.locator('#features').inner_text(),'versioned README is available as project documentation')
                def card(name='publish'):
                    page.locator('[data-feature="declared:'+name+'"]').click();expect(page.locator('#feature-detail h2').first).to_have_text({'publish':'Pubblicazione catalogo','archive':'Archiviazione','future':'Ricerca futura'}[name])
                card();initial_catalog=[]
                def hold_catalog(route):
                    if not initial_catalog:initial_catalog.append((route,route.fetch()))
                    else:route.continue_()
                page.route('**/api/features?**',hold_catalog);page.select_option('#language','en');await_response(initial_catalog)
                check(bool(initial_catalog),'catalog is held while language changes on a selected feature')
                page.fill('#feature-search','Archiviazione');initial_catalog[0][0].fulfill(response=initial_catalog[0][1]);page.wait_for_timeout(250)
                expect(page.locator('[data-feature="declared:archive"]')).to_be_visible();expect(page.locator('#feature-detail h2').first).to_have_text('Pubblicazione catalogo')
                check(page.locator('#message').is_hidden(),'typing during catalog refresh preserves selection without a null-catalog error')
                page.unroute('**/api/features?**',hold_catalog);page.select_option('#language','it');expect(page.locator('[data-feature="declared:publish"]')).to_be_visible()
                delayed=[]
                def hold_feature(route):
                    if 'symbol=' in route.request.url and 'confidence=medium' in route.request.url:delayed.append((route,route.fetch()))
                    else:route.continue_()
                page.route('**/api/feature?**',hold_feature)
                page.fill('#feature-symbol','long.js:caller');page.get_by_role('button',name='Apri simbolo',exact=True).click();await_response(delayed)
                check(bool(delayed),'direct symbol response is held before confidence and depth change')
                page.select_option('#feature-confidence','high')
                with page.expect_response(lambda response:'/api/feature?' in response.url and 'confidence=high' in response.url and 'depth=3' in response.url) as latest:
                    page.select_option('#feature-depth','3')
                html=latest.value.json()['data']['graph_view']['html'];current_graph=re.search(r'<script id="graph-data" type="application/json">(.*?)</script>',html,re.S).group(1)
                graph=page.frame_locator('.feature-graph').locator('#graph-data');expect(graph).to_have_js_property('textContent',current_graph)
                delayed[0][0].fulfill(response=delayed[0][1]);page.unroute('**/api/feature?**',hold_feature);page.wait_for_timeout(100)
                check(graph.text_content()==current_graph,'late direct-symbol result cannot replace the current filter selection')
                page.select_option('#feature-confidence','medium');page.select_option('#feature-depth','2');expect(page.locator('#feature-detail h2').first).to_have_text('caller')
                page.get_by_role('button',name='Prepara per AI',exact=True).click();page.get_by_role('button',name='Genera pacchetto AI',exact=True).click();expect(page.get_by_role('button',name='Scarica pacchetto JSON',exact=True)).to_be_visible()
                long_packet=json.loads(page.locator('#feature-detail pre').inner_text())
                check(long_packet['budget']['actual_chars']<=12000 and long_packet['source_windows']['truncated']>0,'UI prepares a required 17KB-line callsite within the default budget')
                check(any('leaf(1)' in row['text'] and row.get('start_column',0)>16000 for item in long_packet['snippets'].values() for row in item['lines']),'long-line source window shows the actual call and its Unicode columns')
                card('future');check('Riferimento mancante' in page.locator('#feature-detail').inner_text(),'planned feature keeps missing implementation explicit')
                check(page.locator('#feature-detail iframe').count()==0,'planned feature has no invented call graph')
                card();page.get_by_role('button',name='Prepara per AI',exact=True).click()
                held=[]
                def hold(route):held.append((route,route.fetch()))
                page.route('**/api/prepare',hold);page.get_by_role('button',name='Genera pacchetto AI',exact=True).click()
                await_response(held);check(bool(held),'real preparation response can be delayed for a navigation race')
                card('archive');held[0][0].fulfill(response=held[0][1]);page.unroute('**/api/prepare',hold);page.wait_for_timeout(100)
                page.get_by_role('button',name='Rivedi modifica',exact=True).click()
                check(page.get_by_role('button',name='Confronta con preparazione',exact=True).count()==0,'late preparation A does not attach to feature B')
                card();page.frame_locator('.feature-graph').locator('.node').first.wait_for(timeout=15000)
                check(page.locator('.feature-graph').get_attribute('sandbox')=='allow-scripts','feature graph uses an opaque sandbox')
                check('test_publish' in page.locator('#feature-detail').inner_text(),'feature flow includes recognized test evidence')
                check('Evidenza verificata' in page.locator('#feature-detail').inner_text(),'feature proofs are visible alongside contracts')
                if args.flow_screenshot:
                    args.flow_screenshot.parent.mkdir(parents=True,exist_ok=True);page.set_viewport_size({'width':1500,'height':1700});page.locator('#features').evaluate('(node)=>{for(let item=node;item;item=item.parentElement)item.scrollTop=0;}');page.screenshot(path=str(args.flow_screenshot),full_page=True);page.set_viewport_size({'width':1500,'height':1000})
                page.frame_locator('.feature-graph').locator('.node').first.click(force=True)
                expect(page.locator('#inspector')).to_be_visible();check(bool(page.locator('#symbol-name').inner_text()),'graph node opens the verified source inspector')
                page.click('#features-button');card();page.get_by_role('button',name='Prepara per AI',exact=True).click();page.fill('#feature-intent','Aggiungere un campo senza perdere il contratto di salvataggio.')
                page.locator('.feature-verify[value="app.py:save"]').check()
                page.fill('#feature-budget','14000');page.get_by_role('button',name='Genera pacchetto AI',exact=True).click()
                expect(page.get_by_role('button',name='Scarica pacchetto JSON',exact=True)).to_be_visible()
                packet=json.loads(page.locator('#feature-detail pre').inner_text());canonical=json.dumps(packet,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
                check(len(canonical)==packet['budget']['actual_chars']<=14000,'whole canonical packet fits the declared character budget')
                check(packet['calls'] and packet['tests'] and packet['contracts'] and packet['evidence'],'AI packet contains calls, tests, contracts and card proofs')
                check('test_save' in [item['symbol']['name'] for item in packet['tests']] and len(packet['verification_scope']['subjects'])==2,'UI unions the selected collaborator plan with root tests and declares the subjects')
                if args.prepare_screenshot:
                    page.select_option('#theme','dark');expect(page.locator('[data-feature="declared:publish"]')).to_be_visible()
                    page.get_by_role('button',name='Prepara per AI',exact=True).click();page.locator('.feature-verify[value="app.py:save"]').check();page.fill('#feature-intent','Aggiungere un campo al catalogo.');page.get_by_role('button',name='Genera pacchetto AI',exact=True).click();expect(page.get_by_role('button',name='Scarica pacchetto JSON',exact=True)).to_be_visible()
                    args.prepare_screenshot.parent.mkdir(parents=True,exist_ok=True);page.set_viewport_size({'width':1500,'height':1700});page.locator('#features').evaluate('(node)=>{for(let item=node;item;item=item.parentElement)item.scrollTop=0;}');page.screenshot(path=str(args.prepare_screenshot),full_page=True);page.set_viewport_size({'width':1500,'height':1000})
                with page.expect_download() as downloaded:page.get_by_role('button',name='Scarica pacchetto JSON',exact=True).click()
                path=workspace/'packet.json';downloaded.value.save_as(path);packet=json.loads(path.read_text())
                check(packet['verification_required'] and packet['budget']['measured_tokens'] is None,'download is a proposal with estimated tokens, never an executed verification')
                card('archive');page.get_by_role('button',name='Rivedi modifica',exact=True).click()
                check(page.get_by_role('button',name='Confronta con preparazione',exact=True).count()==0,'preparation of A is unavailable in review tab of B')
                page.get_by_role('button',name=re.compile('Rivedi la preparazione precedente')).click();expect(page.locator('#feature-detail h2')).to_have_text('Confronto della modifica')
                check('Pubblicazione catalogo' in page.locator('#feature-detail').inner_text(),'global review explicitly names its prepared feature')
                check('no_selected_graph_change' in page.locator('#feature-detail').inner_text(),'unchanged selected graph does not claim tests passed')
                (root/'app.py').write_text(APP.replace('publish(value):','publish(value, extra=None):'))
                page.click('#refresh');expect(page.locator('#overview')).to_be_visible(timeout=30000);page.click('#features-button')
                page.get_by_role('button',name=re.compile('Rivedi la preparazione precedente')).click();expect(page.locator('#feature-detail')).to_contain_text('signature_changed')
                check('Evidenze della scheda cambiate' in page.locator('#feature-detail').inner_text(),'changed proof is visible in the before-after comparison')
                card();check('Dichiarazione obsoleta' in page.locator('#feature-detail').inner_text(),'refreshed feature marks stale source hashes')
                (root/'app.py').unlink();page.click('#refresh');expect(page.locator('#overview')).to_be_visible(timeout=30000);page.click('#features-button')
                page.get_by_role('button',name=re.compile('Rivedi la preparazione precedente')).click();expect(page.locator('#feature-detail')).to_contain_text('Test della preparazione precedente')
                check('test_publish' in page.locator('#feature-detail').inner_text(),'deleted root retains the previous recognized tests')
                page.select_option('#language','en');expect(page.locator('#features h1')).to_have_text('Understand and change');card()
                check(page.get_by_role('button',name='Prepare for AI',exact=True).count()==1,'English applies to new feature controls')
                page.select_option('#theme','dark');check(page.locator('html').get_attribute('data-theme')=='dark','new feature view supports dark theme')
                page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(100)
                check(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'feature view fits mobile width')
                check(not marker.exists(),'feature analysis never executes project source')
                check(not external,'feature browsing makes no external HTTP request')
                check(not errors,'feature journey has no JavaScript or CSP errors')
                browser.close()
        finally:
            process.terminate();process.wait(timeout=10)
    print(json.dumps(dict(status='PASS',checks=checks,errors=errors,external_requests=external),indent=2))


if __name__=='__main__':main()
