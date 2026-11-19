"""Repeatable holdout tasks. No analyzed source or test is executed."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import time


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--skill-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    skill=args.skill_root.resolve();spec=importlib.util.spec_from_file_location('cg_bench',skill/'scripts/cg.py');cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg)
    ProjectData=runpy.run_path(str(skill/'scripts/project_data.py'))['ProjectData']
    modern=(skill/'scripts/change_analysis.py').exists()
    output=dict(format='codegraph-task-benchmark/1',engine_version=cg.VERSION,engine_sha256=hashlib.sha256((skill/'scripts/cg.py').read_bytes()).hexdigest(),
                tokenizer=None,estimated_token_method='unicode_characters/4',conversation_cost_measured=False,tool_calls_definition='Top-level commands and API reads in the harness; not model-session telemetry.',tasks=[])
    with tempfile.TemporaryDirectory(prefix='cg-task-benchmark-') as directory:
        root=Path(directory)
        sources={
          'one.php':'<?php namespace Demo; class Repo{function save($x){return $x;}}',
          'two.php':'<?php namespace Client; use Demo\\Repo as Storage; class Consumer{function run(Storage $r){return $r->save(3);}}',
          'service.py':'def serve_export(x):return x\n',
          'entrypoint.py':'import service as servedb\ndef run():return servedb.serve_export(3)\n',
          'app.py':'import atexit\ndef leaf(x):return x+1\ndef work(x):\n'+''.join(f'    block_{i}=x+{i}\n' for i in range(211))+'    return leaf(block_210)\ndef entry(x):return work(x)\ndef gateway(x):return entry(x)\n@atexit.register\ndef cleanup():pass\n',
          'tests/test_app.py':'from app import gateway\ndef helper():return gateway(1)\ndef test_contract():return helper()\n',
          'mixins.py':'class Provider:\n    def verify(self):pass\nclass Mixin:\n    def execute(self):self.verify();self.veriffy()\nclass Concrete(Mixin,Provider):pass\n',
          'resources.py':'def producer():open("artifact.json","w")\ndef consumer():return open("artifact.json")\n',
          'bad.py':'def required(x):return x\ndef call_bad():return required()\n',
          '.gitignore':'.code-graph/\n',
        }
        for name,text in sources.items():path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        sha=hashlib.sha256((root/'resources.py').read_bytes()).hexdigest()
        manifest=dict(format='codegraph-manifest/1',resources=[dict(id='artifact',path='artifact.json',producers=[dict(file='resources.py',line=1,sha256=sha)],consumers=[dict(file='resources.py',line=2,sha256=sha)])])
        sources['.code-graph-manifest.json']=json.dumps(manifest)
        (root/'.code-graph-manifest.json').write_text(sources['.code-graph-manifest.json'])
        input_hash=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest();output['source_fixture_sha256']=input_hash
        def git(*values):return subprocess.check_output(['git','-c','core.fsmonitor=false','-C',str(root),*values],stderr=subprocess.DEVNULL)
        git('init','-q');git('config','user.name','Benchmark');git('config','user.email','benchmark@example.invalid');git('add','.');git('commit','-qm','fixture')
        def index():
            with contextlib.redirect_stdout(io.StringIO()):cg.cmd_index(argparse.Namespace(root=str(root),full=False))
        index()
        def invoke(fn,**kwargs):
            capture=io.StringIO()
            with contextlib.redirect_stdout(capture):fn(argparse.Namespace(root=str(root),**kwargs))
            return capture.getvalue()
        def sid(p,name):return p.db.execute('SELECT id FROM symbols WHERE qname=?',(name,)).fetchone()[0]
        def record(task,fn,oracle,calls=1):
            started=time.perf_counter();value=fn();elapsed=(time.perf_counter()-started)*1000
            text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            output['tasks'].append(dict(id=task,correct=bool(oracle(value,text)),tool_calls=calls,characters=len(text),utf8_bytes=len(text.encode()),estimated_tokens=(len(text)+3)//4,measured_tokens=None,latency_ms=elapsed))
        # cmd_edges takes the direction separately in both versions.
        def callers(target):
            capture=io.StringIO()
            with contextlib.redirect_stdout(capture):cg.cmd_edges(argparse.Namespace(root=str(root),symbol=target,min_conf='high'),'in')
            return capture.getvalue()
        record('T1',lambda:callers('Repo.save'),lambda value,text:'Consumer.run' in text)
        record('T2',lambda:callers('serve_export'),lambda value,text:'run' in text and 'entrypoint.py' in text)
        with ProjectData(cg,root) as p:
            changes=runpy.run_path(str(skill/'scripts/change_analysis.py')) if modern else None
            def context():return invoke(cg.cmd_context,symbol='work',budget=250,min_conf='medium')
            record('T3',lambda:changes['plan'](p,[sid(p,'work')],depth=5) if modern else p.impact(sid(p,'work'),depth=5),lambda value,text:'gateway' in text and 'test_contract' in text)
            record('T4',lambda:p.detail(sid(p,'cleanup'),fields=['source','references']) if modern else p.detail(sid(p,'cleanup')),lambda value,text:bool(value['references']) and value['references'][0]['kind']=='callback')
            record('T5',context,lambda value,text:all(word in text for word in ['entry','leaf','work']) and
                   any(all(word in text for word in headings) for headings in [('CALLERS','CALLEES'),('CHIAMANTI','CHIAMATI')]) and len(text)<=1000)
            record('T6',lambda:changes['plan'](p,[sid(p,'work')],depth=5) if modern else p.impact(sid(p,'work'),depth=5),lambda value,text: 'test_contract' in {item['symbol']['name'] for item in value['tests']} and 'helper' not in {item['symbol']['name'] for item in value['tests']})
            record('T7',lambda:p.architecture() if modern else p.resources(),lambda value,text:modern and any(link['status']=='declared_candidate' for link in value['links']))
        def revision_task():
            with ProjectData(cg,root) as p:before=p.meta['revision']
            index()
            with ProjectData(cg,root) as p:after=p.meta['revision']
            (root/'service.py').write_text('def serve_export(x):return x+1\n');index()
            with ProjectData(cg,root) as p:changed=after!=p.meta['revision']
            return dict(stable_noop=before==after,changed_content=changed)
        record('T8',revision_task,lambda value,text:all(value.values()),calls=5)
        def check_output():return invoke(cg.cmd_check,limit=100,json=modern)
        record('T9',check_output,lambda value,text:'veriffy' in text and not (
               'non definisce verify()' in text or 'non lo definisce' in text and 'verify()' in text or
               any(phrase in text for phrase in ('does not define verify()', 'do not define verify()'))))
        (root/'bad.py').write_text('\n\ndef required(x):return x\ndef call_bad():return required()\n');index()
        if modern:
            review_args=argparse.Namespace(root=str(root),base='HEAD',staged=False,baseline=None,save_baseline=None,force=False,max_chars=100000,min_conf='medium',depth=3)
            record('T10',lambda:changes['review'](cg,review_args),lambda value,text:value['summary']['persistent']>=1 and value['summary']['new']==0)
        else:record('T10',check_output,lambda value,text:False)
    output['summary']=dict(passed=sum(task['correct'] for task in output['tasks']),total=len(output['tasks']),characters=sum(task['characters'] for task in output['tasks']),estimated_tokens=sum(task['estimated_tokens'] for task in output['tasks']))
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2));print(json.dumps(output['summary']))


if __name__=='__main__':main()
