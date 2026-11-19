"""Compare engines on one frozen source copy; never run application code or tests."""
import argparse
import collections
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import shutil
import statistics
import tempfile
import time


def load(skill,label):
    spec=importlib.util.spec_from_file_location('cg_real_'+label,skill/'scripts/cg.py');cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg)
    return cg,runpy.run_path(str(skill/'scripts/project_data.py'))['ProjectData']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--baseline-root',type=Path,required=True);parser.add_argument('--skill-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    old,_=load(args.baseline_root,'inventory');cg,Data=load(args.skill_root,'inventory_new')
    result=dict(format='codegraph-real-project-benchmark/1',project_name=args.project.name,source_execution=False,test_execution=False,tokenizer=None,repetitions=3,environment='macOS arm64 / Python 3.12 / tree-sitter 0.25.2',versions={})
    with tempfile.TemporaryDirectory(prefix='cg-real-frozen-') as folder:
        frozen=Path(folder)/'sources';frozen.mkdir()
        names=old.list_files(str(args.project));names+=list(cg.configuration_files(args.project))
        hashes={}
        for name in sorted(set(names)):
            path=cg.local_path(args.project,name)
            raw=path.read_bytes()
            if len(raw)>cg.MAX_BYTES:continue
            target=frozen/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);hashes[name]=hashlib.sha256(raw).hexdigest()
        result['source_snapshot_sha256']=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest();result['copied_files']=len(hashes)
        for label,skill in [('before',args.baseline_root),('after',args.skill_root)]:
            engine,ProjectData=load(skill,label);root=Path(folder)/label;shutil.copytree(frozen,root)
            started=time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()):engine.cmd_index(argparse.Namespace(root=str(root),full=False))
            row=dict(analysis_code_sha256={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((skill/'scripts').glob('*.py'))},engine_version=engine.VERSION,engine_sha256=hashlib.sha256((skill/'scripts/cg.py').read_bytes()).hexdigest(),index_ms=(time.perf_counter()-started)*1000,index_repetitions=1,check_repetitions=1)
            with ProjectData(engine,root) as p:
                row['counts']=dict(files=len(p.files),definitions=p.db.execute('SELECT COUNT(*) FROM symbols WHERE kind!="module"').fetchone()[0],edges=p.db.execute('SELECT COUNT(*) FROM edges').fetchone()[0])
                target=p.db.execute('SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.name="serve_export" AND f.path="pipeline/servedb.py"').fetchone()
                if target is None:raise ValueError('serve_export non trovato nel perimetro atteso: aggiornare il caso reale esplicitamente.')
                sid=target[0]
                row['qualified_consumer_present']=any('pipeline/integrate.py' in location for _,location in engine.neighbors(p.db,sid,'in','high').values())
                full=p.detail(sid,'high');selected=p.detail(sid,'high',fields=['source','references']) if label=='after' else full
                row['detail_full_bytes']=len(json.dumps(full,ensure_ascii=False).encode());row['source_tab_bytes']=len(json.dumps(selected,ensure_ascii=False).encode());row['source_preserved']=full['source']==selected['source']
                legacy=p.impact(sid,'high',depth=3);packed=p.impact(sid,'high',depth=3,compact=True) if label=='after' else legacy
                row['impact_bytes']=len(json.dumps(packed,ensure_ascii=False).encode());row['impact_legacy_bytes']=len(json.dumps(legacy,ensure_ascii=False).encode());row['impact_items']=len(packed['items']);row['recognized_test_cases']=len(packed['tests'])
                row['callback_registered']=p.db.execute('SELECT COUNT(*) FROM symbol_refs r JOIN symbols s ON s.id=r.target_id WHERE s.name="_close_pins"').fetchone()[0]>0
                if label=='after':
                    arch=p.architecture();row['architecture_totals']=arch['totals'];row['architecture_declarations_dynamic']=sum(item.get('status')=='dynamic_declaration' for item in arch['declarations'])
                revision=p.meta['revision']
            with contextlib.redirect_stdout(io.StringIO()):engine.cmd_index(argparse.Namespace(root=str(root),full=False))
            with ProjectData(engine,root) as p:row['noop_revision_stable']=revision==p.meta['revision']
            times=[]
            for _ in range(3):
                started=time.perf_counter()
                with ProjectData(engine,root) as p:p.resources(sid=sid)
                times.append((time.perf_counter()-started)*1000)
            row['resources_query_ms']=dict(samples=times,median=statistics.median(times),min=min(times),max=max(times))
            started=time.perf_counter();capture=io.StringIO()
            with contextlib.redirect_stdout(capture):engine.cmd_check(argparse.Namespace(root=str(root),limit=100000,json=label=='after'))
            row['check_ms']=(time.perf_counter()-started)*1000
            if label=='after':
                findings=json.loads(capture.getvalue());row['check_count']=findings['total'];row['check_categories']=dict(collections.Counter(item['category'] for item in findings['findings']))
            else:row['check_count']=sum(line.startswith('[') for line in capture.getvalue().splitlines())
            result['versions'][label]=row
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
