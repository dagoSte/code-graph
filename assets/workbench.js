'use strict';
(() => {
  const tr = text => window.cgI18n ? window.cgI18n.translate(text) : text;
  const $ = id => document.getElementById(id);
  let token=location.hash.slice(1);
  try{if(token)sessionStorage.setItem('cg-session-token',token);else token=sessionStorage.getItem('cg-session-token')||'';}catch{}
  history.replaceState(null, '', location.pathname);
  const state = {generation:'', project:null, area:'.', symbol:null, detail:null, tab:'code', selection:0, graphSequence:0, graphIds:new Set(), blob:null, folder:null, searchSequence:0, folderSequence:0, navigation:0, inspection:0, snippets:{}, samples:[], requests:[], lastSymbol:null, view:"overview"};
  const resourceNames = {'file.read':tr("Lettura file"),'file.write':tr("Scrittura file"),'database.connect':tr("Connessione database"),'process.start':tr("Avvio processo"),'registration.mcp':tr("Registrazione MCP"),'registration.site':tr("Registrazione sito / API")};
  function el(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
  function showError(error) { if(error.obsolete)return; if(error.code==='stale_index'){state.inspection++;state.detail=null;$('inspection-content').replaceChildren(el('p',tr("Aggiorna l’analisi prima di leggere il sorgente."),'warning'));} $('message').replaceChildren(el('span',tr(error.message || String(error)))); $('message').hidden=false; }
  function recordEfficiency(measurement) { if(measurement){state.samples.push(measurement);if(state.samples.length>100)state.samples.shift();} }
  function clearError() { $('message').hidden=true; }
  async function api(path, options={}) {
    const generation=state.generation,navigation=state.navigation,inspection=state.inspection,confidence=$('confidence').value;
    const started=performance.now();
    const response=await fetch(path,{...options,headers:{'X-CG-Token':token,...(options.body?{'Content-Type':'application/json'}:{}),...(options.headers||{})}});
    const raw=await response.text();const value=JSON.parse(raw);
    if(response.ok&&generation===state.generation&&/^\/api\/(symbol|impact|resources|architecture|context|search)\?/.test(path)){
      state.requests.push({route:path.split('?')[0],chars:Array.from(raw).length,bytes:new TextEncoder().encode(raw).length,ms:performance.now()-started});if(state.requests.length>1000)state.requests.shift();
    }
    if(!response.ok){const error=new Error(value.error || tr("Richiesta non riuscita."));error.code=value.code;error.obsolete=generation!==state.generation||navigation!==state.navigation||confidence!==$('confidence').value||(path.startsWith('/api/impact?')&&inspection!==state.inspection);throw error;}
    return value;
  }
  function projectUrl(route, extra={}) { return route+'?'+new URLSearchParams({generation:state.generation,area:state.area,confidence:$('confidence').value,language:window.cgI18n.language,theme:window.cgI18n.theme,...extra}); }
  function busy(value) { for(const id of ['analyze','choose','refresh','change'])$(id).disabled=value; }
  function sourceBlock(source) { const box=el('div'); const lines=source.lines||String(source.text||'').split('\n').map((text,i)=>({number:source.start_line+i,text})); const pre=el('pre',lines.map(line=>String(line.number).padStart(4)+'  '+line.text).join('\n'),'source'); box.append(pre); if(source.truncated)box.append(el('p',tr("Estratto troncato: apri il file per leggere il resto."),'warning')); return box; }
  function areaButton(area, cls='area-button') { const b=el('button',undefined,cls); b.dataset.area=area.id; b.append(el('strong',area.kind==='evidence'?tr(area.title):area.title),el('small',tr(`${area.file_count} file · ${area.symbol_count} definizioni`))); b.addEventListener('click',()=>chooseArea(area.id).catch(showError)); return b; }
  function navigation() {
    $('semantic-areas').replaceChildren(); $('folders').replaceChildren();
    for(const area of state.project.areas) { if(area.id==='.')continue; $(area.kind==='evidence'?'semantic-areas':'folders').append(areaButton(area)); }
    if(!$('semantic-areas').children.length)$('semantic-areas').append(el('p',tr("Nessuna area semantica confermata. Esplora le cartelle reali."),'hint'));
    if(state.project.areas_truncated)$('folders').append(el('p',tr("Elenco cartelle limitato: usa anche la ricerca dei file."),'hint'));
  }
  function hideViews(){setNavigation(false,false);for(const id of ['overview','graph-panel','efficiency','architecture','features','guide','change-flow','search-evidence','flows'])$(id).hidden=true;}
  function focusSearch(mode,hint) {
    $('search-mode').value=mode;
    $('search-mode').dispatchEvent(new Event('change'));
    $('search').placeholder=hint;
    $('search').focus();
  }
  function overview() {
    hideViews();state.view='overview';
    state.navigation++; state.inspection++;
    $('graph-panel').hidden=true; $('inspector').hidden=true; $('workspace').classList.remove('has-symbol'); $('overview').hidden=false; state.symbol=null; state.area='.';
    const p=state.project, box=$('overview'); box.replaceChildren(el('span',tr("PANORAMICA"),'eyebrow'),el('h1',p.name),el('p',p.path));
    box.append(el('h2',tr('Inizia da qui')),el('p',tr('Tre domande a cui code-graph risponde su questo progetto. Ogni risposta indica il sorgente da leggere: il grafo guida la lettura, i test decidono.'),'panel-lead'));
    if(p.files<30)box.append(el('p',tr('Progetto piccolo: leggere direttamente i file è di solito altrettanto rapido. code-graph è pensato per codice più grande o sconosciuto.'),'warning'));
    const recipes=el('div',undefined,'cards start-here');
    for(const [title,text,label,action] of [
      [tr('Cosa si rompe se lo cambio?'),tr('Cerca una funzione per nome, poi apri la scheda Impatto e test: chiamanti fino a tre livelli e test che la raggiungono.'),tr('Cerca una funzione'),()=>focusSearch('symbols',tr('Nome della funzione o della classe…'))],
      [tr('Dov’è il codice di una richiesta?'),tr('Scrivi parole che possono comparire nel codice: la ricerca Testo + grafo aggiunge le funzioni collegate che non le contengono.'),tr('Cerca nel codice'),()=>focusSearch('hybrid',tr('Parole della richiesta, ad esempio: cancel order'))],
      [tr('La modifica ha toccato altro?'),tr('Prima prepara il contesto di una funzionalità, poi dopo la modifica aggiorna l’analisi e usa Rivedi modifica.'),tr('Capisci e modifica'),()=>featureUI.open()]]) {
      const card=el('div',undefined,'area-card');const go=el('button',label,'more');go.onclick=action;card.append(el('h3',title),el('p',text),go);recipes.append(card);
    }
    box.append(recipes);
    const guideStart=el('button',tr('Prepara un incarico per l’agente'),'primary');guideStart.onclick=()=>guideUI.open();box.append(guideStart);
    const metrics=el('div',undefined,'stats');
    for(const [number,label] of [[p.files,tr("file indicizzati")],[p.symbols,tr("definizioni")],[p.resolved_calls,tr("chiamate risolte")],[p.callbacks,tr("riferimenti registrati")]]) { const card=el('div',undefined,'stat'); card.append(el('strong',String(number)),el('span',label)); metrics.append(card); }
    box.append(metrics,el('p',p.languages.map(l=>tr(`${l.language}: ${l.files} file`)).join(' · '),'hint'));
    box.append(el('p',tr(`${p.resolved_calls} chiamate risolte su ${p.calls} incontrate. ${p.dynamic_calls} dinamiche riconosciute; ${p.hypothetical_edges} collegamenti ipotizzati. Questi conteggi non misurano la completezza dell’architettura.`), 'hint'));
    box.append(el('p',tr(`Ricognizione: ${p.discovered_files} file visibili · ${p.unsupported_files} con formato non indicizzato · ${p.excluded_files} esclusi dalle regole · ${p.skipped_files} sorgenti saltati · ${p.partial_files} con sintassi parziale.`), 'hint'));
    const semantic=p.areas.filter(a=>a.kind==='evidence');
    if(semantic.length) { box.append(el('h2',tr("Aree rilevate con evidenze"))); const cards=el('div',undefined,'cards'); for(const area of semantic)cards.append(areaButton(area,'area-card')); box.append(cards); }
    box.append(el('h2',tr("Esplora la struttura"))); const cards=el('div',undefined,'cards');
    const folders=p.areas.filter(a=>a.kind==='folder'&&a.id!=='.');
    for(const area of (folders.length?folders:[p.areas[0]]).slice(0,12))cards.append(areaButton(area,'area-card'));
    box.append(cards,el('h2',tr("Passaggi oltre le chiamate")),el('p',tr("File, database e processi sono osservazioni separate. Un percorso scritto nel codice non prova da solo un collegamento tra due processi."),'hint'));
    const b=el('button',tr("Mostra le osservazioni sulle risorse")); b.addEventListener('click',()=>showResources().catch(showError)); box.append(b);
  }
  async function chooseArea(id) {
    hideViews();state.view='graph';clearError(); state.navigation++; state.inspection++; state.area=id; state.symbol=null; state.detail=null; state.selection++;
    $('overview').hidden=true; $('graph-panel').hidden=false; $('inspector').hidden=true; $('workspace').classList.remove('has-symbol');
    const area=state.project.areas.find(a=>a.id===id);
    $('area-name').textContent=area.kind==='evidence'?tr(area.title):area.title; $('area-caption').textContent=area.kind==='evidence'?tr("AREA CON EVIDENZE"):tr("CARTELLA REALE");
    $('scope-note').textContent=tr(`${area.file_count} file · ${area.symbol_count} definizioni`);
    $('area-evidence').replaceChildren();
    for(const proof of area.evidence.slice(0,8))$('area-evidence').append(el('p',`${proof.file}${proof.line?':'+proof.line:''} · ${tr(proof.reason)}`,'hint'));
    for(const b of document.querySelectorAll('[data-area]'))b.classList.toggle('selected',b.dataset.area===id);
    await Promise.all([renderGraph(), search()]);
  }
  async function renderGraph() {
    const sequence=++state.graphSequence, generation=state.generation;
    $('graph-loading').hidden=false;
    try {
      const result=await api(projectUrl('/api/graph',{view:String(sequence),...(state.symbol===null?{}:{id:state.symbol})}));
      if(sequence!==state.graphSequence||generation!==state.generation)return;
      state.graphIds=new Set(result.data.node_ids);
      const old=state.blob; state.blob=URL.createObjectURL(new Blob([result.data.html],{type:'text/html'})); $('graph').src=state.blob;
      if(old)URL.revokeObjectURL(old);
    } finally { if(sequence===state.graphSequence)$('graph-loading').hidden=true; }
  }
  let searchEvidence=null;
  function symbolResult(symbol) {
    const button=el('button',undefined,'symbol-result');button.dataset.symbol=symbol.id;
    button.append(el('strong',symbol.name),el('small',symbol.file+':'+symbol.line));
    button.onclick=()=>selectSymbol(symbol.id).catch(showError);return button;
  }
  function searchRanks(item) {
    const parts=[];
    if(item.lexical)parts.push(tr('Testo')+' #'+item.lexical.rank+' · BM25 '+Number(item.lexical.bm25).toPrecision(4));
    if(item.graph)parts.push(tr('Grafo')+' #'+item.graph.rank+' · '+tr('Confidenza')+' '+item.graph.confidence);
    if(item.rrf_score!==null)parts.push('RRF '+Number(item.rrf_score).toPrecision(4));
    return parts.join(' · ');
  }
  function showSearchEvidence(result,focus=false) {
    state.navigation++;state.inspection++;state.selection++;state.graphSequence++;hideViews();state.view='search';searchEvidence=result;
    $('inspector').hidden=true;$('workspace').classList.remove('has-symbol');
    const box=$('search-evidence');box.hidden=false;box.replaceChildren(el('h1',tr('Evidenze della ricerca')));
    if(result.status==='budget_insufficient'){box.append(el('p',tr('Budget insufficiente per le evidenze.'),'warning'));return;}
    box.append(el('h2',result.query.text),el('p',tr('Ricerca in tutto il progetto. Parole letterali nel codice, senza traduzione automatica.'),'hint'),
      el('p',result.query.mode+' · '+result.omissions.shown+'/'+result.omissions.ranked+' · '+result.budget.actual_chars+'/'+result.budget.max_chars+' '+tr('caratteri'),'hint'),
      el('p',tr('Revisione')+': '+result.content_revision,'search-revision'));
    box.append(el('p',tr('BM25: minore è migliore. RRF combina i ranghi; i punteggi non sono confidenze statiche.'),'hint'));
    if(result.status==='partial')box.append(el('p',tr('Risultato parziale: controlla omissioni, tagli e limiti prima di concludere.'),'warning'));
    if(!result.items.length)box.append(el('p',tr('Nessun risultato per queste parole.'),'empty'));
    const limits=el('details');limits.append(el('summary',tr('Limiti della ricerca')));
    limits.append(el('p',tr('Prefisso del corpo indicizzato')+': '+result.retrieval.body_prefix_chars+' · '+tr('Omissioni')+': '+result.omissions.omitted+' · '+tr('Archi esaminati')+': '+result.retrieval.graph.edges_scanned+'/'+result.retrieval.graph.edge_limit,'hint'));
    for(const text of result.limitations)limits.append(el('p',tr(text),'hint'));box.append(limits);
    for(const item of result.items){
      const card=el('article',undefined,'evidence-card'),open=el('button',item.symbol.name,'search-open');open.dataset.symbol=item.symbol.id;
      open.onclick=()=>selectSymbol(item.symbol.id).catch(showError);
      card.append(open,el('p',item.symbol.file+':'+item.symbol.line,'hint'),el('p',searchRanks(item),'hint'),sourceBlock(item.source));
      const provenance=el('details');provenance.append(el('summary',tr('Hash del sorgente')),el('code',item.source.source_sha256,'source-hash'));card.append(provenance);
      if(item.graph){
        card.append(el('p',tr('Distanza')+': '+item.graph.distance+' · '+tr('Semi di supporto')+': '+item.graph.supporting_seeds.length,'hint'));
        const chain=el('ol',undefined,'search-chain');
        for(const step of item.graph.chain)chain.append(el('li',step.file+':'+step.line+' · '+step.relation+' · '+step.direction+' · '+step.confidence));
        card.append(chain);
      }
      box.append(card);
    }
    if(focus){box.setAttribute('tabindex','-1');box.focus({preventScroll:true});}
  }
  async function search(offset=0,open=false) {
    const sequence=++state.searchSequence,generation=state.generation,navigation=state.navigation,mode=$('search-mode').value,query=$('search').value,confidence=$('confidence').value;
    const current=()=>sequence===state.searchSequence&&generation===state.generation&&navigation===state.navigation&&mode===$('search-mode').value&&confidence===$('confidence').value;
    const box=$('search-results');
    if(mode!=='symbols'&&!query.trim()){box.replaceChildren();searchEvidence=null;if(state.view==='search')$('search-evidence').replaceChildren(el('p',tr('Scrivi le parole da cercare nel codice.'),'hint'));return;}
    try {
      const result=await api(projectUrl(mode==='symbols'?'/api/symbols':'/api/search',mode==='symbols'?{q:query,offset}:{q:query,mode,limit:10,max_chars:16000}));
      if(!current())return;
      if(offset===0)box.replaceChildren();else box.querySelector('.more')?.remove();
      if(mode==='symbols'){
        for(const symbol of result.data.items)box.append(symbolResult(symbol));
        if(result.data.more){const more=el('button',tr('Altri risultati'),'more');more.onclick=async()=>{try{await search(offset+80);box.querySelectorAll('[data-symbol]')[offset+80]?.focus();}catch(error){showError(error);}};box.append(more);}
      }else{
        searchEvidence=result.data;
        if(result.data.status==='budget_insufficient')box.append(el('p',tr('Budget insufficiente per le evidenze.'),'warning'));
        else{
          box.append(el('p',tr('Ricerca in tutto il progetto')+' · '+result.data.omissions.shown+'/'+result.data.omissions.ranked,'hint'));
          for(const item of result.data.items){const button=symbolResult(item.symbol);button.append(el('small',searchRanks(item)));box.append(button);}
          if(result.data.status==='empty')box.append(el('p',tr('Nessun risultato per queste parole.'),'empty'));
          if(result.data.status==='partial')box.append(el('p',tr('Risultato parziale: controlla omissioni, tagli e limiti prima di concludere.'),'warning'));
          const evidence=el('button',tr('Mostra evidenze della ricerca'),'more');evidence.onclick=()=>search(0,true).catch(showError);box.append(evidence);
        }
        if(open||state.view==='search')showSearchEvidence(result.data,open);
      }
    }catch(error){
      if(!current())return;
      searchEvidence=null;box.replaceChildren(el('p',tr(error.message),'warning'));
      if(state.view==='search')$('search-evidence').replaceChildren(el('p',tr(error.message),'warning'));
      if(mode!=='symbols'){const fallback=el('button',tr('Usa ricerca simboli'),'more');fallback.onclick=()=>{$('search-mode').value='symbols';$('search-mode').dispatchEvent(new Event('change'));};box.append(fallback);}
      throw error;
    }
  }
  async function selectSymbol(id) {
    clearError(); state.navigation++; state.inspection++; const sequence=++state.selection, generation=state.generation; $('inspection-content').replaceChildren(el('p',tr("Carico il sorgente…"),'hint'));
    const result=await api(projectUrl('/api/symbol',{id,fields:'source,references'}));
    if(sequence!==state.selection||generation!==state.generation)return;
    hideViews();state.view='graph';state.lastSymbol=id;state.symbol=id; state.detail=result.data; state.tab='code';
    $('overview').hidden=true; $('graph-panel').hidden=false; $('inspector').hidden=false; $('workspace').classList.add('has-symbol');
    $('symbol-kind').textContent=`${state.detail.kind} · ${state.detail.language}`; $('symbol-name').textContent=state.detail.name; $('symbol-location').textContent=`${state.detail.file}:${state.detail.line}`;
    $('area-name').textContent=state.detail.name; $('area-caption').textContent=tr("VICINATO DEL SIMBOLO"); $('scope-note').textContent=tr("Collegamenti risolti e inferiti; scegli «Includi ipotesi» per i ricevitori sconosciuti.");
    renderInspection(); await renderGraph();
    if(sequence!==state.selection||generation!==state.generation||$('inspector').hidden)return;
    $('inspection-content').setAttribute('tabindex','-1'); $('inspection-content').focus({preventScroll:true});
  }
  function relation(item) {
    const b=el('button',undefined,'relation'); b.append(el('strong',item.symbol.name),el('small',`${item.symbol.file}:${item.symbol.line}`));
    for(const call of item.callsites) { b.append(el('span',call.confidence,'tag'),el('small',`${call.file}:${call.line}`),sourceBlock(call.source)); }
    b.addEventListener('click',()=>selectSymbol(item.symbol.id).catch(showError)); return b;
  }
  function unpackImpact(data){
    Object.assign(state.snippets,data.snippets);
    const items=new Map(data.items.map(item=>[item.symbol.id,item]));
    data.tests=data.tests.map(item=>item.item_ref!==undefined?items.get(item.item_ref)||data.test_records[String(item.item_ref)]:item);
    for(const item of [...data.items,...data.tests])for(const step of item.chain)step.source=state.snippets[step.snippet_ref];
    return data;
  }
  async function renderInspection() {
    const inspection=++state.inspection, selection=state.selection, confidence=$('confidence').value;
    const id=state.symbol, generation=state.generation, tab=state.tab;
    const current=()=>id===state.symbol&&generation===state.generation&&tab===state.tab&&inspection===state.inspection&&selection===state.selection&&confidence===$('confidence').value;
    for(const b of document.querySelectorAll('[data-tab]'))b.setAttribute('aria-selected',String(b.dataset.tab===tab));
    const d=state.detail, box=$('inspection-content'); if(!d)return; box.replaceChildren();
    if(tab==='agent'){
      const controls=el('div',undefined,'agent-controls'),label=el('label',tr('Budget in caratteri')),budget=el('input');budget.type='number';budget.min='128';budget.max='100000';budget.value='4000';label.htmlFor='context-budget';budget.id='context-budget';
      const profile=el('select');profile.id='context-profile';for(const [value,name] of [['understand','Comprensione'],['change','Modifica'],['debug','Debug'],['review','Review']]){const option=el('option',tr(name));option.value=value;profile.append(option);}profile.value='change';const profileLabel=el('label',tr('Profilo'));profileLabel.htmlFor=profile.id;
      const button=el('button',tr('Genera contesto'));const output=el('pre',undefined,'source');let request=0;budget.oninput=profile.onchange=()=>{request++;output.textContent='';};button.onclick=async()=>{const epoch=++request,maximum=budget.value,kind=profile.value;try{const result=await api(projectUrl('/api/context',{id,max_chars:maximum,profile:kind}));if(current()&&epoch===request&&maximum===budget.value&&kind===profile.value)output.textContent=result.data.text;}catch(error){if(current()&&epoch===request)showError(error);}};controls.append(label,budget,profileLabel,profile,button,output);box.append(controls);return;
    }
    if(tab==='code') { box.append(sourceBlock(d.source)); for(const ref of d.references||[])box.append(el('p',tr(`${ref.kind==='callback'?'Registrata come callback':'Entrypoint registrato'} · ${ref.file}:${ref.line}`),'tag'),sourceBlock(ref.source));if(d.references_truncated)box.append(el('p',tr('Riferimenti limitati a 100: altri riferimenti sono omessi.'),'warning')); return; }
    try {
      if(tab==='calls') {
        for(const [key,title] of [['callers',tr("Chiamata da")],['callees',tr("Chiama")]]) {
          const section=el('section'); box.append(section);
          const load=async(cursor)=>{
            const result=await api(projectUrl('/api/symbol',{id,fields:key,...(cursor?{cursor}:{})}));
            if(!current())return;
            const data=result.data, page=data.pages[key];
            section.querySelector('.more')?.remove();
            if(!cursor)section.append(el('h3',`${tr(title)} · ${page.distinct_symbols} ${tr('vicini')} · ${page.total} callsite`));
            for(const item of data[key])section.append(relation(item));
            if(!page.total)section.append(el('p',tr("Nessun collegamento trovato a questa soglia."),'empty'));
            if(page.next_cursor){const more=el('button',tr("Altri risultati"),'more');more.onclick=()=>load(page.next_cursor).catch(showError);section.append(more);}
          };
          await load(null);
        }
      }
      if(tab==='resources') {
        const load=async(cursor)=>{const response=await api(projectUrl('/api/symbol',{id,fields:'observations',...(cursor?{cursor}:{})}));if(!current())return;box.querySelector('.more')?.remove();box.querySelector('.resource-page-note')?.remove();renderResources(response.data.observations,box,load);};
        await load(null);
      }
      if(tab==='impact') {
        box.append(el('p',tr("Seguo i chiamanti fino a tre passaggi. I test elencati sono raggiunti attraverso queste chiamate; non vengono eseguiti."),'hint'));
        const response=await api(projectUrl('/api/impact',{id,depth:3,compact:'1',limit:100}));
        if(!current())return;
        const data=unpackImpact(response.data);
        recordEfficiency(data.measurement);
        box.append(el('h3',`${tr("Test collegati")} · ${data.pages.tests.total}`));
        if(!data.tests.length)box.append(el('p',tr("Nessun test collegato trovato nel grafo. Controlla anche la suite e i test d’integrazione del progetto."),'empty'));
        for(const item of data.tests)box.append(impactItem(item));
        box.append(el('h3',`${tr("Impatto")} · ${data.pages.items.total} ${tr("simboli")}`));
        for(const item of data.items)box.append(impactItem(item));
        for(const key of ['tests','items'])if(data.pages[key].next_cursor){const more=el('button',tr("Altri risultati")+' · '+key,'more');more.onclick=async()=>{try{const next=await api(projectUrl('/api/impact',{id,depth:3,compact:'1',limit:100,[key==='tests'?'test_cursor':'cursor']:data.pages[key].next_cursor}));if(!current())return;unpackImpact(next.data);for(const item of next.data[key]){box.insertBefore(impactItem(item),more);}data.pages[key]=next.data.pages[key];if(!data.pages[key].next_cursor)more.remove();}catch(error){showError(error);}};box.append(more);}
        if(data.truncated)box.append(el('p',tr("Risultato parziale: usa un perimetro più piccolo."),'warning'));
      }
    } catch(error) { if(current())showError(error); }
  }
  function impactItem(item) {
    const b=el('button',undefined,'relation'); b.append(el('strong',item.symbol.name),el('small',tr(`${item.symbol.file}:${item.symbol.line} · distanza ${item.distance}`)));
    for(const step of item.chain)b.append(el('small',` → ${step.file}:${step.line} [${step.confidence}]`),sourceBlock(step.source));
    b.addEventListener('click',()=>selectSymbol(item.symbol.id).catch(showError)); return b;
  }
  function renderResources(data,box,loadMore=null) {
    box.append(el('p',tr("Osservazioni nel sorgente, distinte dalle chiamate. I percorsi dinamici e i passaggi fra processi richiedono conferma."),'hint'));
    if(!data.items.length)box.append(el('p',tr("Nessuna osservazione riconosciuta nel perimetro selezionato."),'empty'));
    for(const item of data.items) { const card=el('div',undefined,'resource'); card.append(el('strong',tr(resourceNames[item.kind]||item.kind)),el('small',`${item.file}:${item.line} · ${item.callee}`)); if(item.value)card.append(el('p',item.value)); else if(item.value_kind==='dynamic')card.append(el('p',tr("Risorsa o comando determinato a runtime."),'hint')); card.append(sourceBlock(item.source)); box.append(card); }
    const shown=(data.page?.offset||0)+data.items.length;
    if(shown<data.total)box.append(el('p',`${shown} / ${data.total}`,'resource-page-note'));
    if(data.page?.next_cursor&&loadMore){const more=el('button',tr('Altre osservazioni'),'more');more.onclick=()=>loadMore(data.page.next_cursor).catch(showError);box.append(more);}
  }
  async function showResources() {
    const generation=state.generation, area=state.area, navigation=state.navigation;
    const box=el('div',undefined,'evidence-card');
    const load=async(cursor)=>{const response=await api(projectUrl('/api/resources',cursor?{cursor}:{}));if(generation!==state.generation||area!==state.area||navigation!==state.navigation||$('overview').hidden)return;box.querySelector('.more')?.remove();box.querySelector('.resource-page-note')?.remove();renderResources(response.data,box,load);if(!box.isConnected)$('overview').append(box);};
    await load(null);
  }
  async function openProject(path) {
    clearError(); busy(true); state.navigation++; state.inspection++; state.selection++; state.graphSequence++;state.searchSequence++;searchEvidence=null;$('search-results').replaceChildren();
    try { const result=await api('/api/open',{method:'POST',body:JSON.stringify({path})}); state.generation=result.generation; $('start').hidden=true; $('workspace').hidden=true; syncUsageGuide(); $('progress-panel').hidden=false; $('progress-path').textContent=path; await poll(); }
    catch(error){busy(false); showError(error);}
  }
  async function poll() {
    const current=state.generation;
    try {
      const status=await api('/api/status'); if(current!==state.generation)return; if(status.generation!==current)throw new Error(tr("Il progetto attivo è cambiato in un’altra finestra. Riapri il progetto."));
      const p=status.status;
      $('progress-title').textContent={scanning:tr("Esamino la cartella…"),indexing:tr("Indicizzo i sorgenti…"),resolving:tr("Collego simboli e riferimenti…")}[p.phase]||tr("Preparo la panoramica…");
      if(p.total){$('progress').max=p.total; $('progress').value=p.completed;} else $('progress').removeAttribute('value');
      $('progress-detail').textContent=`${p.completed} / ${p.total || '…'} file ${p.file||''}`;
      if(p.phase==='ready') {
        state.project=status.project;state.snippets={};state.samples=[];state.requests=[];state.lastSymbol=null; state.area='.'; state.symbol=null; state.detail=null;
        $('project-name').textContent=state.project.name; $('project-path').textContent=state.project.path; $('project-indexed').textContent=tr('Analizzato '+state.project.indexed_at); $('project-bar').hidden=false; $('progress-panel').hidden=true; $('workspace').hidden=false; syncUsageGuide(); busy(false); navigation(); overview(); await search(); return;
      }
      if(p.phase==='error') { $('progress-panel').hidden=true; $('start').hidden=false; syncUsageGuide(); busy(false); throw new Error(p.message); }
      setTimeout(()=>poll(),400);
    } catch(error){busy(false); showError(error);}
  }
  async function folders(path) {
    const sequence=++state.folderSequence; $('use-folder').disabled=true;
    try {
      const result=await api('/api/directories?'+new URLSearchParams({path}));
      if(sequence!==state.folderSequence)return;
      state.folder=result.path; $('folder-path').textContent=result.path;
      $('parent-folder').hidden=!result.parent; $('parent-folder').onclick=()=>folders(result.parent).catch(showError); $('directory-list').replaceChildren();
      for(const folder of result.items) { const b=el('button','▸ '+folder.name); b.addEventListener('click',()=>folders(folder.path).catch(showError)); $('directory-list').append(b); }
      if(result.truncated)$('directory-list').append(el('p',tr("Elenco limitato a 250 cartelle: puoi anche incollare un percorso."),'hint'));
    } finally { if(sequence===state.folderSequence)$('use-folder').disabled=false; }
  }
  const panels=window.createCgPanels({state,el,tr,api,projectUrl,sourceBlock,showError,hideViews});
  const featureUI=window.createCgFeatures({state,el,tr,api,projectUrl,sourceBlock,showError,hideViews,selectSymbol});
  const flowUI=window.createCgFlows({state,el,tr,api,projectUrl,showError,hideViews,selectSymbol,openFeature:id=>featureUI.open(false,id)});
  const guideUI=window.createCgGuide({state,el,tr,api,projectUrl,hideViews,showError,openFeatures:()=>featureUI.open(),chooseArea});
  const changeUI=window.createCgChange?.({state,el,tr,api,projectUrl,hideViews,showError,selectSymbol});
  const usageGuide=$('usage-guide');
  function syncUsageGuide() {
    const ready=!$('workspace').hidden;
    $('usage-guide-start').hidden=ready||$('start').hidden;
    for(const action of usageGuide.querySelectorAll('[data-guide-action]')) action.hidden=!ready;
  }
  $('usage-guide-button').onclick=()=>{
    syncUsageGuide();
    usageGuide.showModal();
    usageGuide.querySelector('.usage-guide-body').scrollTop=0;
  };
  $('usage-guide-close').onclick=()=>usageGuide.close();
  $('usage-guide-project').onclick=()=>{
    usageGuide.close();
    $('project-input').focus();
  };
  for(const button of usageGuide.querySelectorAll('[data-guide-action]')) {
    button.onclick=()=>{
      const action=button.dataset.guideAction;
      usageGuide.close();
      if (action==='search') {
        focusSearch('hybrid',tr('Parole della richiesta, ad esempio: cancel order'));
      } else {
        $(action).click();
        $('main-stage').setAttribute('tabindex','-1');
        $('main-stage').focus({preventScroll:true});
      }
    };
  }
  $('guide-button').onclick=()=>guideUI.open();
  $('features-button').onclick=()=>featureUI.open();
  $('flows-button').onclick=()=>flowUI.open();
  $('technical-button').onclick=()=>chooseArea('.').catch(showError);
  $('change-flow-button').onclick=()=>changeUI?.open();
  $('efficiency-button').onclick=()=>panels.open('efficiency');
  $('architecture-button').onclick=()=>panels.open('architecture');
  window.addEventListener('cg-preferences',()=>{
    if(!state.project)return;
    state.inspection++;navigation();$('project-indexed').textContent=tr('Analizzato '+state.project.indexed_at);
    if(state.view==='guide')guideUI.open();
    else if(state.view==='features')featureUI.open(true);
    else if(state.view==='flows')flowUI.open(true);
    else if(state.view==='efficiency')panels.efficiency();
    else if(state.view==='architecture')panels.architecture().catch(showError);
    else if(state.view==='overview')overview();
    else if(state.view==='search'&&searchEvidence)showSearchEvidence(searchEvidence);
    else if(state.view==='change')changeUI?.open(true);
    else{if(state.symbol!==null)renderInspection();renderGraph().catch(showError);}
    if($('search-mode').value!=='symbols'&&$('search').value.trim())search().catch(showError);
  });
  $('open-form').addEventListener('submit',event=>{event.preventDefault(); openProject($('project-input').value);});
  $('choose').addEventListener('click',async()=>{ $('choose').disabled=true; clearError(); try{const result=await api('/api/choose',{method:'POST',body:'{}'}); if(result.cancelled)return; if(result.path)$('project-input').value=result.path; else{await folders(''); $('folder-dialog').showModal();}}catch(error){showError(error);}finally{$('choose').disabled=false;} });
  $('close-picker').addEventListener('click',()=>$('folder-dialog').close());
  $('use-folder').addEventListener('click',()=>{$('project-input').value=state.folder; $('folder-dialog').close();});
  $('change').addEventListener('click',()=>{state.navigation++;state.inspection++;state.selection++;state.graphSequence++;state.searchSequence++;$('workspace').hidden=true; $('start').hidden=false; clearError();});
  $('refresh').addEventListener('click',()=>openProject(state.project.path));
  $('overview-button').addEventListener('click',()=>{state.selection++; state.graphSequence++; overview(); search().catch(showError);});
  let searchTimer;
  function queueSearch(){state.searchSequence++;searchEvidence=null;$('search-results').replaceChildren(el('p',tr('Ricerca…'),'hint'));if(state.view==='search')$('search-evidence').replaceChildren(el('p',tr('Ricerca…'),'hint'));clearTimeout(searchTimer);searchTimer=setTimeout(()=>search().catch(showError),180);}
  $('search').addEventListener('input',queueSearch);
  $('search-mode').onchange=()=>{if(state.view==='search'&&$('search-mode').value==='symbols'){state.selection++;state.graphSequence++;overview();}queueSearch();};
  $('search').addEventListener('keydown',event=>{if(event.key==='Enter'&&$('search-mode').value!=='symbols'){event.preventDefault();clearTimeout(searchTimer);search(0,true).catch(showError);}});
  $('confidence').addEventListener('change',async()=>{
    state.searchSequence++;searchEvidence=null;clearTimeout(searchTimer);$('search-results').replaceChildren();
    const confidence=$('confidence').value;
    try{
      if(state.view==='search')await search(0,true);
      else{
        const update=state.symbol!==null?selectSymbol(state.symbol):renderGraph(),navigation=state.navigation,generation=state.generation;
        await update;
        if(navigation===state.navigation&&generation===state.generation&&confidence===$('confidence').value)await search();
      }
    }catch(error){showError(error);}
  });
  for(const button of document.querySelectorAll('[data-tab]'))button.addEventListener('click',()=>{state.tab=button.dataset.tab; renderInspection();});
  window.addEventListener('message',event=>{if(event.source!==$('graph').contentWindow||event.data?.type!=='cg-select'||event.data.generation!==state.generation||event.data.view!==String(state.graphSequence)||$('graph-panel').hidden||!Number.isInteger(event.data.id)||!state.graphIds.has(event.data.id))return; selectSymbol(event.data.id).catch(showError);});
  $('download').addEventListener('click',async()=>{try{const generation=state.generation,navigation=state.navigation,name=state.project.name;const result=await api(projectUrl('/api/graph',{standalone:'1',...(state.symbol!==null?{id:state.symbol}:{})})); if(generation!==state.generation||navigation!==state.navigation)return;const url=URL.createObjectURL(new Blob([result.data.html],{type:'text/html'})); const a=el('a'); a.href=url; a.download=name.replace(/[^\w.-]/g,'_')+'-graph.html'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){showError(error);}});
  const mobileNavigation=matchMedia('(max-width:720px)');
  function setNavigation(open,restoreFocus=true){
    open=open&&mobileNavigation.matches;
    const wasOpen=document.body.classList.contains('navigation-open');
    document.body.classList.toggle('navigation-open',open);
    $('navigation-toggle').setAttribute('aria-expanded',String(open));
    $('navigation').inert=mobileNavigation.matches&&!open;
    for(const node of [document.querySelector('body>header'),$('main-stage'),$('inspector'),document.querySelector('.workspace-tools')])node.inert=open;
    if(open)$('navigation-close').focus();
    else if(wasOpen){const target=restoreFocus?$('navigation-toggle'):$('main-stage');if(!restoreFocus)target.setAttribute('tabindex','-1');target.focus({preventScroll:true});}
  }
  $('navigation-toggle').onclick=()=>setNavigation(true);
  $('navigation-close').onclick=()=>setNavigation(false);
  $('navigation-backdrop').onclick=()=>setNavigation(false);
  mobileNavigation.addEventListener('change',()=>setNavigation(false,false));
  setNavigation(false,false);
  $('close-inspector').onclick=()=>{state.inspection++;state.selection++;$('inspector').hidden=true;$('workspace').classList.remove('has-symbol');$('main-stage').setAttribute('tabindex','-1');$('main-stage').focus({preventScroll:true});};
  document.addEventListener('keydown',event=>{
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'&&!$('workspace').hidden&&!document.querySelector('dialog[open]')){event.preventDefault();setNavigation(false,false);$('search').focus();$('search').select();}
    if(document.body.classList.contains('navigation-open')){
      if(event.key==='Escape'){event.preventDefault();setNavigation(false);}
      if(event.key==='Tab'){const items=[...$('navigation').querySelectorAll('button:not(:disabled)')];const first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
    }else if(event.key==='Escape'&&document.querySelector('.search-box').contains(document.activeElement))document.activeElement.blur();
  });
  api('/api/bootstrap').then(data=>{$('workspace-hint').textContent='Workspace: '+data.workspace; $('project-input').value=data.initial;if(data.generation&&data.phase!=='idle'&&data.phase!=='error'){state.generation=data.generation;$('start').hidden=true;$('progress-panel').hidden=false;poll();}}).catch(showError);
})();
