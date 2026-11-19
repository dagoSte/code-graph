"""Bounded Unicode source windows with caller context and explicit positions."""
import hashlib
import re


def call_columns(project,path,line,name,owner=None):
    if not hasattr(project,'window_call_columns'):project.window_call_columns={}
    cache=project.window_call_columns
    if path not in cache:
        text='\n'.join(project._read(path));raw=text.encode()
        tree=project.cg.get_parser_for(project.files[path]['lang']).parse(raw)
        extracted=project.cg.Extractor(project.files[path]['lang'],raw,path,project.root).run(tree.root_node)
        rows={}
        for call in extracted.calls:
            prefix=project._read(path)[call['line']-1].encode()[:call['column_bytes']]
            position=len(prefix.decode('utf-8','replace'));scope=extracted.syms[call['caller']]['qname']
            rows.setdefault((call['line'],call['name'],scope),[]).append(position)
            rows.setdefault((call['line'],call['name'],None),[]).append(position)
        cache[path]=rows
    return cache[path].get((line,name,owner),[])


def window(project, source, max_chars=640, line_chars=320, focus=None, column=None):
    if type(max_chars) is not int or not 64 <= max_chars <= 24000 or type(line_chars) is not int or not 32 <= line_chars <= 24000:
        raise ValueError('Invalid window limits.')
    if column is not None and (type(column) is not int or column < 0):
        raise ValueError('Invalid column.')
    originals=project._read(source['file'])
    if not originals:
        return dict(file=source['file'],lines=[],truncated=False,empty=True)
    result=[];remaining=max_chars;cuts=[]
    owner=focus.get('owner') if isinstance(focus,dict) else None
    name=focus.get('name','') if isinstance(focus,dict) else focus or ''
    token=re.split(r'[.\\:]+',name)[-1]
    for entry in source['lines']:
        if not remaining:break
        number=entry['number'];text=originals[number-1]
        width=min(remaining,line_chars);start=0;matches=[]
        if token:
            matches=call_columns(project,source['file'],number,token,owner) if len(text)>width else []
        if column is not None:
            start=min(column,len(text))
        elif len(text)>width and matches:
            start=max(0,matches[0]-width//3)
        stop=min(len(text),start+width)
        record=dict(number=number,text=text[start:stop]);remaining-=stop-start
        if start or stop<len(text):
            record.update(start_column=start,end_column=stop,original_chars=len(text),original_text_sha256=hashlib.sha256(text.encode()).hexdigest())
            cuts.append(dict(line=number,start_column=start,end_column=stop,original_chars=len(text),focus_matches=len(matches)))
        result.append(record)
    truncated=bool(cuts) or source.get('truncated',False) or len(result)<len(source['lines'])
    out=dict(file=source['file'],lines=result,truncated=truncated)
    if truncated:
        last=result[-1] if result else source['lines'][0]
        next_line=last['number'];next_column=last.get('end_column',len(last['text']))
        if next_column>=len(originals[next_line-1]):next_line+=1;next_column=0
        out['window']=dict(reason='source_width_or_budget',columns='unicode_codepoints_zero_based_half_open',max_chars=max_chars,line_chars=line_chars,cuts=cuts,
                           focus='ast_call_candidates' if token else None,focus_owner=owner,focus_status='ambiguous' if any(cut['focus_matches']>1 for cut in cuts) else 'unique' if any(cut['focus_matches']==1 for cut in cuts) else 'not_requested_or_unresolved',
                           continuation=dict(file=source['file'],line=next_line,column=next_column,
                               available=next_line<=len(originals),argv=['source',source['file'],'--line',str(next_line),'--column',str(next_column),'--max-chars',str(max_chars)] if next_line<=len(originals) else None))
    return out


def read_window(project,path,line=1,column=0,max_chars=1200):
    if type(line) is not int or line<1:raise ValueError('Invalid line.')
    lines=project._read(path)
    if line>len(lines):raise ValueError('Line beyond the end of the file.')
    result=window(project,dict(file=path,lines=[dict(number=line,text=lines[line-1])],truncated=False),max_chars,max_chars,column=column)
    result.update(source_revision=project.files[path]['sha'],source_sha256=project.source_hashes[path])
    return result
