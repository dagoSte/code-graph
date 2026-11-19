"""Local-only repeatable paired benchmark. Exported JSON contains metadata/hashes, no source text."""
import argparse,ast,hashlib,importlib.util,json,os,pathlib,sqlite3,statistics,subprocess,sys,time
parser=argparse.ArgumentParser();parser.add_argument('--capture',required=True);parser.add_argument('--repeats',type=int,default=3);parser.add_argument('--engines',nargs='+',default=['baseline','current']);a=parser.parse_args();base=pathlib.Path(a.capture);capture=json.loads((base/'capture.json').read_text());python=pathlib.Path(sys.executable);common=base/'common-input';rawdir=base/'private-raw';rawdir.mkdir(exist_ok=True)
env={**os.environ,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_COUNT':'2','GIT_CONFIG_KEY_0':'core.fsmonitor','GIT_CONFIG_VALUE_0':'false','GIT_CONFIG_KEY_1':'core.hooksPath','GIT_CONFIG_VALUE_1':'/dev/null','TMPDIR':'/private/tmp'}
nav='site/compiled-js/navigation.js';servedb='pipeline/servedb.py';source=(common/nav).read_bytes()
# Establish source oracles before either prepare output is read.
spec=importlib.util.spec_from_file_location('cg_quality_oracle',pathlib.Path(capture['engines']['baseline']['path'])/'scripts/cg.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
tree=m.get_parser_for('javascript').parse(source);calls=[]
for node in m._iter(tree.root_node):
 if node.type!='call_expression' or m.txt(node.child_by_field_name('function'))!='publicPage':continue
 owner='<module>';ancestor=node.parent
 while ancestor is not None:
  if ancestor.type in ('function_declaration','generator_function_declaration'):
   name=m.txt(ancestor.child_by_field_name('name'))
   if name:owner=name;break
  if ancestor.type=='variable_declarator':
   val=ancestor.child_by_field_name('value')
   if val is not None and val.type in ('function_expression','arrow_function','function'):owner=m.txt(ancestor.child_by_field_name('name'));break
  ancestor=ancestor.parent
 calls.append({'file':nav,'caller':owner,'callee':'publicPage','line':node.start_point.row+1,'column_zero_based':node.start_point.column,'end_column_zero_based':node.end_point.column,'call_sha256':hashlib.sha256(node.text).hexdigest()})
serve_tree=ast.parse((common/servedb).read_text());fn=next(n for n in serve_tree.body if isinstance(n,ast.FunctionDef)and n.name=='serve_export')
fixed_calls=[]
for n in ast.walk(fn):
 if isinstance(n,ast.Call)and ast.unparse(n.func)in('export.export_all','discovery.populate'):fixed_calls.append({'file':servedb,'caller':'serve_export','callee':ast.unparse(n.func).split('.')[-1],'line':n.lineno,'column_zero_based':n.col_offset,'end_column_zero_based':n.end_col_offset})
direct_tests=[]
for path in sorted((common/'pipeline/tests').glob('*.py')):
 text=path.read_text()
 if 'serve_export'not in text:continue
 t=ast.parse(text);parents={c:p for p in ast.walk(t)for c in ast.iter_child_nodes(p)}
 for node in ast.walk(t):
  if not isinstance(node,ast.Call)or not isinstance(node.func,ast.Attribute)or node.func.attr!='serve_export':continue
  owner=node;names=[]
  while owner in parents:
   owner=parents[owner]
   if isinstance(owner,(ast.FunctionDef,ast.ClassDef)):names.append(owner.name)
  direct_tests.append({'file':str(path.relative_to(common)),'owner':'.'.join(reversed(names)),'line':node.lineno,'column_zero_based':node.col_offset})
oracles={'navigation':{'path':nav,'line_count':len(source.splitlines()),'longest_line_bytes':max(map(len,source.splitlines())),'publicPage_calls':sorted(calls,key=lambda c:c['column_zero_based'])},'serve_export':{'path':servedb,'definition_line':fn.lineno,'end_line':fn.end_lineno,'fixed_outgoing_calls':fixed_calls,'production_caller':{'file':'pipeline/integrate.py','line':288,'callee':'serve_export'},'direct_test_calls':direct_tests},'established_before_queries':True};(base/'oracles.json').write_text(json.dumps(oracles,indent=2))
report={'format':'codegraph-paired-quality/1','baseline_commit':capture['baseline_commit'],'capture':{'input_manifest_sha256':capture['input_manifest_sha256'],'files':len(capture['input_manifest']),'bytes':capture['capture_bytes'],'source_counts':capture['source_counts'],'configuration_counts':capture['configuration_counts'],'changed_during_capture':capture['changed_during_capture'],'historical_index_comparison':capture['historical_index_comparison']},'engine_hashes':{name:info['hashes']for name,info in capture['engines'].items()},'input_manifest':capture['input_manifest'],'oracles':oracles,'measurement':{'unicode_characters':True,'bytes':'UTF-8','serialization':'exact CLI stdout incl newline','measured_tokens':None,'tokenizer':None,'model':None,'remote_calls':0,'application_executed':False,'timing_scope':'process launch + local query + serialization, repeat queries against the same indexed inputs','repetitions':a.repeats},'engines':{}}
def cli(name,args):
 cmd=[str(python),'-I','-B',str(pathlib.Path(capture['engines'][name]['path'])/'scripts/cg.py'),'--root',capture['projects'][name]]+args;start=time.perf_counter();r=subprocess.run(cmd,cwd=base,env=env,capture_output=True,text=True,timeout=150);elapsed=time.perf_counter()-start
 if r.returncode:raise RuntimeError((name,args,r.returncode,r.stdout,r.stderr))
 return r.stdout,elapsed

def summarize_packet(data,raw,elapsed):
 participants=data.get('participants',[]);lookup={node['id']:node.get('identity',{})for node in participants};edges=[]
 for edge in data.get('calls',[]):
  edges.append({'source':lookup.get(edge['source']), 'target':lookup.get(edge['target']), 'callsites':[{k:v for k,v in call.items()if k!='source'}for call in edge['calls']]})
 snippets=data.get('snippets',{});snippet_metadata={}
 for ident,item in snippets.items():
  metadata={k:v for k,v in item.items()if k not in('lines','source','text')};metadata['line_records']=[{k:v for k,v in line.items()if k!='text'}for line in item.get('lines',[])];metadata['text_characters']=sum(len(line.get('text',''))for line in item.get('lines',[]));snippet_metadata[ident]=metadata
 def references(value):
  if isinstance(value,dict):
   for k,v in value.items():
    if k=='snippet_ref':yield v
    else:yield from references(v)
  elif isinstance(value,list):
   for item in value:yield from references(item)
 refs=list(references(data));missing=[ref for ref in refs if ref not in snippets]
 return {'status':data.get('status'),'characters':len(raw),'utf8_bytes':len(raw.encode()),'seconds':elapsed,'budget':data.get('budget'),'omissions':data.get('omissions'),'participants':len(participants),'participant_identities':[dict(identity=n.get('identity'),root=n.get('root'),has_source='snippet_ref'in n)for n in participants],'links':len(edges),'callsites':sum(len(e['callsites'])for e in edges),'edges':edges,'tests':len(data.get('tests',[])),'test_identities':[{'identity':t['symbol'].get('name'),'file':t['symbol'].get('file'),'line':t['symbol'].get('line'),'distance':t.get('distance'),'chain_length':len(t.get('chain',[]))}for t in data.get('tests',[])],'snippets':len(snippets),'snippet_section_characters':len(json.dumps(snippets,ensure_ascii=False,sort_keys=True,separators=(',',':'))),'snippet_metadata':snippet_metadata,'unknown_calls':len(data.get('unknown_calls',[])),'verification_required':data.get('verification_required'),'broader_verification_required':data.get('broader_verification_required'),'feature_provenance':data.get('feature',{}).get('provenance'),'unresolved_snippet_refs':missing,'whole_output_within_budget':len(raw)<=data.get('budget',{}).get('max_chars',0)}
if (base/'benchmark-safe.json').exists():
 previous=json.loads((base/'benchmark-safe.json').read_text());report['engines']=previous['engines']
for name in a.engines:
 print('INDEX',name,flush=True);selftest,_=cli(name,['selftest']);assert '25/25'in selftest
 output,index_seconds=cli(name,['index']);stats,_=cli(name,['stats','--json']);stats=json.loads(stats)
 db=sqlite3.connect(pathlib.Path(capture['projects'][name])/'.code-graph/graph.db');module_id=db.execute("SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path=? AND s.kind='module'",(nav,)).fetchone()[0];indexed=dict(db.execute('SELECT path,sha FROM files'));db.close();manifest={r['path']:r['sha1']for r in capture['input_manifest']};assert all(manifest[p]==h for p,h in indexed.items())
 result={'index_seconds':index_seconds,'stats':stats,'indexed_files':len(indexed),'all_indexed_hashes_match_common_capture':True,'catalog':{},'queries':{}}
 times=[];catalog=None
 for repeat in range(a.repeats):
  text,elapsed=cli(name,['features','--json']);times.append(elapsed)
  if repeat==0:catalog=json.loads(text);(rawdir/(name+'-catalog.json')).write_text(text)
 result['catalog']={'total':catalog.get('total'),'listed':len(catalog.get('items',[])),'characters':len(text),'utf8_bytes':len(text.encode()),'seconds':times,'median_seconds':statistics.median(times),'items':[{k:v for k,v in item.items()if k in('id','title','provenance','stage','location','candidate_reason','reason','entrypoint_evidence','generated_path_hint','source_status')}for item in catalog.get('items',[])],'issues_count':len(catalog.get('issues',[])),'candidate_total':catalog.get('candidate_total'),'candidates_truncated':catalog.get('candidates_truncated')}
 cases=[('navigation12000',['prepare','symbol:'+str(module_id),'--max-chars','12000','--json']),('navigation24000',['prepare','symbol:'+str(module_id),'--max-chars','24000','--json']),('serve_export12000',['prepare','--symbol','pipeline/servedb.py:serve_export','--max-chars','12000','--json'])]
 for case,args in cases:
  measurements=[]
  for repeat in range(a.repeats):
   print('QUERY',name,case,repeat+1,flush=True);text,elapsed=cli(name,args);payload=json.loads(text);measurements.append(summarize_packet(payload,text,elapsed))
   if repeat==0:(rawdir/(name+'-'+case+'.json')).write_text(text)
  first=measurements[0];assert all((row['characters'],row['utf8_bytes'],row['status'])==(first['characters'],first['utf8_bytes'],first['status'])for row in measurements)
  result['queries'][case]={'summary':first,'seconds':[row['seconds']for row in measurements],'median_seconds':statistics.median(row['seconds']for row in measurements),'same_payload_sizes_across_repeats':True}
  print('SUMMARY',name,case,first['status'],first['characters'],first['participants'],first['links'],first['tests'],first['snippets'],flush=True)
 report['engines'][name]=result;(base/'benchmark-safe.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
# Verify common source bytes remained identical after all executions.
for name,project in capture['projects'].items():
 for row in capture['input_manifest']:assert hashlib.sha256((pathlib.Path(project)/row['path']).read_bytes()).hexdigest()==row['sha256']
report['source_files_unchanged']=True;(base/'benchmark-safe.json').write_text(json.dumps(report,indent=2,ensure_ascii=False));print('DONE',base/'benchmark-safe.json',flush=True)
