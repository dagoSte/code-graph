import argparse,hashlib,importlib.util,io,json,os,pathlib,shutil,sqlite3,subprocess,sys,tarfile,tempfile,time
sys.dont_write_bytecode=True
parser=argparse.ArgumentParser();parser.add_argument('--project',required=True,type=pathlib.Path);parser.add_argument('--repo',type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[1]);parser.add_argument('--output',type=pathlib.Path);args=parser.parse_args()
REPO=args.repo.resolve();APP=args.project.resolve();BASE=args.output.resolve() if args.output else pathlib.Path(tempfile.mkdtemp(prefix='cg-paired-quality-'))
if BASE.is_relative_to(APP) or BASE.is_relative_to(REPO):raise ValueError('Use an output directory outside the project and skill repository.')
BASE.mkdir(parents=True,exist_ok=True);print('BASE',BASE,flush=True)
os.environ.update({'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_COUNT':'2','GIT_CONFIG_KEY_0':'core.fsmonitor','GIT_CONFIG_VALUE_0':'false','GIT_CONFIG_KEY_1':'core.hooksPath','GIT_CONFIG_VALUE_1':'/dev/null','TMPDIR':'/private/tmp'})
old=BASE/'engines/baseline';new=BASE/'engines/current';old.mkdir(parents=True);new.mkdir(parents=True)
archive=subprocess.check_output(['git','-C',str(REPO),'archive','2f501fd','scripts','assets','requirements.txt'])
with tarfile.open(fileobj=io.BytesIO(archive),mode='r:')as tar:
 for member in tar.getmembers():
  target=old/member.name
  if member.isdir():target.mkdir(parents=True,exist_ok=True)
  elif member.isfile():target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(tar.extractfile(member).read())
for folder in('scripts','assets'):
 for source in(REPO/folder).iterdir():
  if source.is_file()and source.suffix in('.py','.js','.css','.html'):
   target=new/folder/source.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
(REPO/'requirements.txt').read_bytes();(new/'requirements.txt').write_bytes((REPO/'requirements.txt').read_bytes())
def load(name,root):
 spec=importlib.util.spec_from_file_location(name,root/'scripts/cg.py');m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
engines={'baseline':load('cg_quality_baseline',old),'current':load('cg_quality_current',new)}
selected={name:set(engine.list_files(str(APP)))for name,engine in engines.items()};configs={name:engine.configuration_files(APP)for name,engine in engines.items()}
paths=set.union(*selected.values())|set().union(*(set(c)for c in configs.values()))
if(APP/'.cgignore').is_file():paths.add('.cgignore')
files={};manifest=[]
for rel in sorted(paths):
 source=APP/rel
 if source.is_symlink()or not source.is_file():continue
 source.resolve().relative_to(APP.resolve());raw=source.read_bytes();files[rel]=raw;manifest.append({'path':rel,'bytes':len(raw),'sha1':hashlib.sha1(raw).hexdigest(),'sha256':hashlib.sha256(raw).hexdigest(),'source_candidate':{k:rel in v for k,v in selected.items()},'configuration':{k:rel in v for k,v in configs.items()}})
changed_during_capture=[rel for rel,raw in files.items()if hashlib.sha256((APP/rel).read_bytes()).digest()!=hashlib.sha256(raw).digest()]
assert not changed_during_capture,changed_during_capture
common=BASE/'common-input';common.mkdir()
for rel,raw in files.items():p=common/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
# Track the same capture so hidden tracked sources retain their Git listing semantics.
for args in(['git','init','-q',str(common)],['git','-C',str(common),'add','-f','.']):subprocess.run(args,check=True,capture_output=True)
clones={}
for name in engines:
 target=BASE/'projects'/name;target.parent.mkdir(exist_ok=True);shutil.copytree(common,target);clones[name]=str(target)
 for row in manifest:assert hashlib.sha256((target/row['path']).read_bytes()).hexdigest()==row['sha256']
historical={}
try:
 db=sqlite3.connect((APP/'.code-graph/graph.db').as_uri()+'?mode=ro',uri=True);meta=dict(db.execute('SELECT k,v FROM meta'));known=dict(db.execute('SELECT path,sha FROM files'));db.close();historical={'content_revision':meta.get('content_revision'),'matches_user_record':meta.get('content_revision')=='22c4eedb220312b632b19c040413fdd84c4e02705a2d67eda012e179c49ab3bc','indexed_files':len(known),'same_hash_count':sum(rel in files and hashlib.sha1(files[rel]).hexdigest()==sha for rel,sha in known.items()),'changed_paths':[rel for rel,sha in known.items()if rel in files and hashlib.sha1(files[rel]).hexdigest()!=sha],'missing_paths':[rel for rel in known if rel not in files]}
except Exception as error:historical={'unavailable':str(error)}
record={'base':str(BASE),'source_root':str(APP),'baseline_commit':'2f501fd938db765ba5c34c128cdcb32cd54a8d91','engines':{name:{'path':str(root),'version':engines[name].VERSION,'hashes':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()for p in root.rglob('*')if p.is_file()}}for name,root in [('baseline',old),('current',new)]},'projects':clones,'input_manifest':manifest,'input_manifest_sha256':hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'capture_bytes':sum(len(raw)for raw in files.values()),'changed_during_capture':changed_during_capture,'historical_index_comparison':historical,'source_counts':{k:len(v)for k,v in selected.items()},'configuration_counts':{k:len(v)for k,v in configs.items()},'measured_tokens':None,'model':None}
(BASE/'capture.json').write_text(json.dumps(record,indent=2,ensure_ascii=False));print(json.dumps({k:record[k]for k in('capture_bytes','source_counts','configuration_counts','historical_index_comparison','input_manifest_sha256')},ensure_ascii=False),flush=True)
