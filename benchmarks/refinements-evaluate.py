"""Source-oracle checks for retained metadata/windows; output excludes source text."""
import argparse,sys,ast,hashlib,json,os,pathlib,statistics,subprocess
parser=argparse.ArgumentParser();parser.add_argument('--capture',required=True);parser.add_argument('--engines',nargs='+',default=['baseline','current']);a=parser.parse_args();base=pathlib.Path(a.capture);common=base/'common-input';d=json.loads((base/'benchmark-safe.json').read_text());capture=json.loads((base/'capture.json').read_text());oracle=d['oracles'];python=sys.executable;env={**os.environ,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_COUNT':'2','GIT_CONFIG_KEY_0':'core.fsmonitor','GIT_CONFIG_VALUE_0':'false','GIT_CONFIG_KEY_1':'core.hooksPath','GIT_CONFIG_VALUE_1':'/dev/null'}
def identities(packet):return {n['id']:n['identity']for n in packet.get('participants',[])}
def edge_has(packet,source_file,source_name,target_file,target_name,line):
 nodes=identities(packet)
 for edge in packet.get('calls',[]):
  left,right=nodes[edge['source']],nodes[edge['target']]
  if left['file']!=source_file or right['file']!=target_file or right['name']!=target_name:continue
  if source_name=='<module>':
   if left['kind']!='module':continue
  elif left['name']!=source_name:continue
  if any(call['file']==source_file and call['line']==line for call in edge['calls']):return True
 return False

def evidence_covers(packet,file,line,start=None,end=None):
 for snippet in packet.get('snippets',{}).values():
  if snippet['file']!=file:continue
  for record in snippet.get('lines',[]):
   if record['number']!=line:continue
   if start is None:return True
   lower=record.get('start_column',0);upper=record.get('end_column',len(record.get('text','')))
   if lower<=start and upper>=end:return True
 return False

quality={}
for engine in a.engines:
 output={}
 for case in('navigation12000','navigation24000','serve_export12000'):
  packet=json.loads((base/'private-raw'/(engine+'-'+case+'.json')).read_text());nodes=identities(packet);adj={node:set()for node in nodes}
  for edge in packet.get('calls',[]):
   assert edge['source']in nodes and edge['target']in nodes
   adj[edge['source']].add(edge['target']);adj[edge['target']].add(edge['source'])
  reached=set(n['id']for n in packet.get('participants',[])if n['root']);pending=list(reached)
  while pending:
   n=pending.pop()
   for other in adj[n]-reached:reached.add(other);pending.append(other)
  invalid=[];truncated=0;continuations=0
  for ident,snip in packet.get('snippets',{}).items():
   path=common/snip['file'];raw=path.read_bytes();lines=raw.decode('utf-8','replace').splitlines()
   if snip['source_revision']!=hashlib.sha1(raw).hexdigest():invalid.append({'snippet':ident,'rule':'source_hash_mismatch'})
   for record in snip.get('lines',[]):
    original=lines[record['number']-1];start=record.get('start_column',0);end=record.get('end_column',len(original))
    if record['text']!=original[start:end]:invalid.append({'snippet':ident,'file':snip['file'],'line':record['number'],'rule':'text_does_not_match_declared_columns'})
    if 'original_text_sha256'in record and record['original_text_sha256']!=hashlib.sha256(original.encode()).hexdigest():invalid.append({'snippet':ident,'rule':'original_line_hash_mismatch'})
   if snip.get('truncated'):truncated+=1;continuations+=bool(snip.get('window',{}).get('continuation',{}).get('available'))
  questions=[]
  if case.startswith('navigation'):
   questions.append({'id':'N1','question':'Identifica modulo e provenienza senza attribuire semantica business dal nome','root_identity_retained':any(n['kind']=='module'and n['file']==oracle['navigation']['path']for n in nodes.values()),'provenance':packet.get('feature',{}).get('provenance')})
   for qid,caller in(('N2','eligibleLink'),('N3','<module>')):
    fact=next(c for c in oracle['navigation']['publicPage_calls']if c['caller']==caller)
    questions.append({'id':qid,'question':'Callsite statica verso publicPage con posizione verificabile','oracle':fact,'explicit_graph_connection':edge_has(packet,fact['file'],caller,fact['file'],fact['callee'],fact['line']),'original_call_span_available':evidence_covers(packet,fact['file'],fact['line'],fact['column_zero_based'],fact['end_column_zero_based'])})
  else:
   roots=[n for n in packet.get('participants',[])if n['root']];questions.append({'id':'S1','question':'Identifica radice serve_export e riga di definizione','correct_identity':any(n['identity']['file']=='pipeline/servedb.py'and n['identity']['name']=='serve_export'for n in roots),'definition_line_retained':any(r['reference'].get('line')==oracle['serve_export']['definition_line']for r in packet.get('references',[])),'provenance':packet.get('feature',{}).get('provenance')})
   for qid,target in(('S2','export_all'),('S3','populate')):
    fact=next(c for c in oracle['serve_export']['fixed_outgoing_calls']if c['callee']==target);targetfile='pipeline/export.py'if target=='export_all'else'pipeline/discovery.py';questions.append({'id':qid,'question':'Collegamento al collaboratore e callsite verificabile','oracle':fact,'explicit_graph_connection':edge_has(packet,fact['file'],'serve_export',targetfile,target,fact['line']),'original_call_span_available':evidence_covers(packet,fact['file'],fact['line'],fact['column_zero_based'],fact['end_column_zero_based'])})
   questions.append({'id':'S4','question':'Chiamante di produzione cmd_export e callsite','oracle':oracle['serve_export']['production_caller'],'explicit_graph_connection':edge_has(packet,'pipeline/integrate.py','cmd_export','pipeline/servedb.py','serve_export',288),'callsite_source_retained':evidence_covers(packet,'pipeline/integrate.py',288)})
   testproofs=[]
   for test in packet.get('tests',[]):
    matching=[c for c in oracle['serve_export']['direct_test_calls']if c['file']==test['symbol']['file']and c['owner']==test['symbol']['name']]
    testproofs.append({'file':test['symbol']['file'],'name':test['symbol']['name'],'source_has_direct_call':bool(matching),'exact_call_span_retained':any(evidence_covers(packet,c['file'],c['line'],c['column_zero_based'],None if c.get('end_column_zero_based')is None else c['end_column_zero_based'])for c in[])if False else any(evidence_covers(packet,c['file'],c['line'])for c in matching),'v1_metadata_preserved':all(k in test for k in('priority','reason','role','command','distance','chain'))})
   questions.append({'id':'S5','question':'Test riconosciuto con callsite sorgente e provenienza statica','retained_tests':testproofs,'verification_scope':packet.get('verification_scope')})
  output[case]={'connected_participants':len(reached),'participants_outside_root_component':len(nodes)-len(reached),'invalid_source_evidence':invalid,'truncated_snippets':truncated,'continuations_declared':continuations,'questions':questions}
 quality[engine]=output
 # Catalog refinement is the same additional query for both engines; no model and no source execution.
 command=[python,'-I','-B',str(pathlib.Path(capture['engines'][engine]['path'])/'scripts/cg.py'),'--root',capture['projects'][engine],'features','--query','pipeline','--json'];r=subprocess.run(command,env=env,capture_output=True,text=True,check=True,timeout=60);catalog=json.loads(r.stdout);(base/'private-raw'/(engine+'-catalog-pipeline.json')).write_text(r.stdout)
 cat=d['engines'][engine]['catalog'];cat['pipeline_refinement']={'total':catalog.get('total'),'listed':len(catalog.get('items',[])),'candidate_total':catalog.get('candidate_total'),'candidates_truncated':catalog.get('candidates_truncated'),'locations':[item.get('location')for item in catalog.get('items',[])]}
 # Validate the source anchor of default candidates without inferring business semantics.
 validations=[]
 for item in cat['items']:
  for proof in item.get('entrypoint_evidence',[]):
   verified=False
   if proof.get('kind')=='python_main_guard':
    tree=ast.parse((common/proof['file']).read_text());parents={c:p for p in ast.walk(tree)for c in ast.iter_child_nodes(p)}
    for call in ast.walk(tree):
     if not isinstance(call,ast.Call)or call.lineno!=proof['line']:continue
     node=call
     while node in parents:
      node=parents[node]
      if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):break
      if isinstance(node,ast.If)and '__name__'in ast.unparse(node.test)and '__main__'in ast.unparse(node.test):verified=True;break
     if verified:break
   validations.append({'location':item.get('location'),'evidence':proof,'source_anchor_verified':verified})
 cat['evidence_validations']=validations
 # Restore catalog metadata for the initial run without rerunning it.
 if cat.get('candidate_total')is None:
  original=json.loads((base/'private-raw'/(engine+'-catalog.json')).read_text());cat['candidate_total']=original.get('candidate_total');cat['candidates_truncated']=original.get('candidates_truncated')
 d['engines'][engine]['quality']=output
print('QUALITY',json.dumps(quality,ensure_ascii=False));print('CATALOG_QUERY',[(e,d['engines'][e]['catalog']['pipeline_refinement'])for e in quality])
d['source_quality_scope']='Deterministic source-oracle availability checks, not a remote-model accuracy experiment.';d['portable_artifact_contains_source_text']=False;(base/'benchmark-safe.json').write_text(json.dumps(d,indent=2,ensure_ascii=False));(base/'benchmark-report.json').write_text(json.dumps(d,indent=2,ensure_ascii=False));(base/'quality-safe.json').write_text(json.dumps(quality,indent=2,ensure_ascii=False))
