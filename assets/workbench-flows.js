'use strict';
window.createCgFlows = ({state,el,tr,api,projectUrl,showError,hideViews,selectSymbol,openFeature}) => {
  const $=id=>document.getElementById(id);
  let sequence=0,query='',confidence='medium',timer,observer;
  const label=value=>tr({resolved:'Risolto',partial:'Parziale',not_resolved:'Da collegare',planned:'Progettata',active:'Attiva'}[value]||value);
  function button(text,action,cls){const node=el('button',tr(text),cls);node.onclick=action;return node;}
  function download(data){const url=URL.createObjectURL(new Blob([JSON.stringify(data)],{type:'application/json'}));const link=el('a');link.href=url;link.download='functional-map.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function schema(box){
    const details=el('details',undefined,'flow-declaration'),summary=el('summary',tr('Dai un nome ai flussi del progetto'));
    details.append(summary,el('p',tr('Aggiungi le funzionalità a .code-graph-manifest.json, conservando gli altri campi. Usa file e simboli verificati nel sorgente, poi aggiorna l’analisi.'),'hint'));
    details.append(el('pre',JSON.stringify({format:'codegraph-manifest/1',features:[{id:'payment',title:'Payment',summary:'Charge an order',entrypoints:[{file:'payments.py',symbol:'charge'}]}]},null,2),'source'));
    details.append(el('p',tr('L’agente può proporre questi nomi leggendo il codice. La mappa distingue le dichiarazioni dai collegamenti statici verificati.'),'hint'));box.append(details);
  }
  function draw(data,box){
    box.replaceChildren();
    if(data.status==='budget_insufficient'){box.append(el('p',tr('Budget insufficiente: restringi la ricerca.'),'warning'));return;}
    box.append(el('p',tr('I nomi descrivono lo scopo dichiarato dal progetto. Le frecce mostrano solo chiamate dirette con evidenze, non un ordine di esecuzione.'),'hint'));
    const tools=el('div',undefined,'flow-summary');tools.append(el('span',data.nodes.length+' '+tr('funzionalità'),'tag'),el('span',data.links.length+' '+tr('collegamenti mostrati'),'tag'),button('Scarica mappa JSON',()=>download(data)));box.append(tools);
    if(data.status==='partial')box.append(el('p',tr('Mappa parziale: controlla le omissioni nel JSON o restringi la ricerca.'),'warning'));
    for(const issue of data.issues)box.append(el('p',issue.message,'warning'));
    if(!data.nodes.length)box.append(el('p',tr(data.declarations_status==='no_declared_features'?'Nessun flusso dichiarato: i candidati tecnici qui sotto non ne stabiliscono lo scopo.':'Nessuna funzionalità corrispondente.'),'hint'));
    const board=el('div',undefined,'flow-board'),cards=el('div',undefined,'flow-cards'),byId=new Map();
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('aria-hidden','true');board.append(svg,cards);box.append(board);
    for(const node of data.nodes){
      byId.set(node.id,node);const card=el('article',undefined,'flow-card');card.dataset.flow=node.id;
      const heading=button('',()=>openFeature(node.id),'flow-title');heading.append(el('strong',node.title),el('span','↗'));card.append(heading);
      card.append(el('small',tr('Dichiarata')+' · '+label(node.stage)+' · '+label(node.source_status),'hint'));
      if(node.summary)card.append(el('p',node.summary));
      const files=el('div',undefined,'flow-files');for(const file of node.files.slice(0,3))files.append(el('code',file));if(node.files.length>3)files.append(el('small','+ '+(node.files.length-3)));card.append(files);
      for(const ref of node.references){if(ref.status!=='resolved')card.append(el('small',ref.reference.file+' · '+ref.status,'warning'));}
      card.append(button('File, impatto e test',()=>openFeature(node.id),'flow-detail'));cards.append(card);
    }
    function connections(){
      if(!board.isConnected)return;svg.replaceChildren();const bounds=board.getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${bounds.width} ${bounds.height}`);
      const defs=document.createElementNS(svg.namespaceURI,'defs'),marker=document.createElementNS(svg.namespaceURI,'marker');marker.id='flow-arrow';marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','9');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','6');marker.setAttribute('markerHeight','6');marker.setAttribute('orient','auto-start-reverse');const triangle=document.createElementNS(svg.namespaceURI,'path');triangle.setAttribute('d','M 0 0 L 10 5 L 0 10 z');marker.append(triangle);defs.append(marker);svg.append(defs);
      const positions=new Map([...cards.children].map(card=>[card.dataset.flow,card.getBoundingClientRect()]));
      for(const [index,link] of data.links.entries()){const from=positions.get(link.source),to=positions.get(link.target);if(!from||!to)continue;
        const path=document.createElementNS(svg.namespaceURI,'path'),x1=from.left+from.width/2-bounds.left,x2=to.left+to.width/2-bounds.left,y1=from.top-bounds.top,y2=to.top-bounds.top;
        // Route around cards: a line hidden behind a third card would imply a false link.
        const adjacent=Math.abs(from.top-to.top)<2&&to.left>=from.right&&to.left-from.right<40;
        path.setAttribute('d',adjacent?`M${from.right-bounds.left} ${from.top+from.height/2-bounds.top} H${to.left-bounds.left}`:
          Math.abs(from.top-to.top)<2?`M${x1} ${y1} V${y1-12-index%4*2} H${x2} V${y2}`:
          `M${x1} ${y1} V${y1-10} H${6+index%4*3} V${y2-10} H${x2} V${y2}`);
        path.setAttribute('marker-end','url(#flow-arrow)');svg.append(path);
      }
    }
    observer?.disconnect();observer=new ResizeObserver(connections);observer.observe(cards);connections();
    if(data.links.length){box.append(el('h2',tr('Perché sono collegati')));for(const link of data.links){const item=el('details',undefined,'flow-witness');item.append(el('summary',byId.get(link.source).title+' → '+byId.get(link.target).title));for(const witness of link.witnesses)item.append(el('p',witness.caller.name+' → '+witness.callee.name+' · '+witness.file+':'+witness.line+' · '+witness.confidence,'hint'));box.append(item);}}
    if(data.shared_references.length){box.append(el('h2',tr('Codice condiviso')));for(const item of data.shared_references)box.append(el('p',item.symbol.identity.file+' · '+item.symbol.identity.name+' · '+item.features.map(id=>byId.get(id)?.title||id).join(', '),'hint'));}
    schema(box);
    box.append(el('h2',tr('Candidati tecnici')),el('p',tr('Ingressi indicizzati o definizioni: da leggere e nominare, senza attribuzione automatica a una funzionalità.'),'hint'));
    const candidates=el('div',undefined,'flow-candidates');for(const item of data.entrypoint_candidates){const candidate=button('',()=>selectSymbol(item.technical.symbol_id).catch(showError),'symbol-result');candidate.append(el('strong',item.name),el('small',item.technical.identity.file+':'+item.technical.line));candidates.append(candidate);}box.append(candidates);
    const evidence=el('details');evidence.append(el('summary',tr('Limiti e provenienza')),el('p',tr('Freschezza controllata sui sorgenti consultati. I test si calcolano aprendo una funzionalità; nessun test è eseguito dalla mappa.'),'hint'),el('pre',JSON.stringify({revision:data.revision,omissions:data.omissions,scan:data.scan},null,2),'source'));box.append(evidence);
  }
  function open(preserve=false){
    clearTimeout(timer);observer?.disconnect();if(!preserve)query='';
    state.navigation++;state.inspection++;state.selection++;state.graphSequence++;hideViews();state.view='flows';$('flows').hidden=false;$('inspector').hidden=true;$('workspace').classList.remove('has-symbol');
    const generation=state.generation,navigation=state.navigation,box=$('flows');box.replaceChildren(el('span','CODE-GRAPH / '+tr('DUE MAPPE'),'eyebrow'),el('h1',tr('Mappa funzionale')),el('p',tr('Parti da “Payment”, poi apri le funzioni, i file e i test del flusso. La mappa tecnica resta disponibile per i dettagli.'),'panel-lead'));
    const controls=el('div',undefined,'feature-controls'),input=el('input');input.id='flow-query';input.type='search';input.maxLength=200;input.value=query;input.placeholder=tr('Cerca uno scopo o un nome tecnico');input.setAttribute('aria-label',input.placeholder);
    const select=el('select');select.id='flow-confidence';select.setAttribute('aria-label',tr('Confidenza'));for(const [value,text] of [['high','Risoluzioni dirette'],['medium','Dirette e inferite'],['low','Includi ipotesi']]){const option=el('option',tr(text));option.value=value;select.append(option);}select.value=confidence;controls.append(input,select);box.append(controls);
    const output=el('div');box.append(output);
    function queue(delay=0){const seq=++sequence,words=input.value,rank=select.value;query=words;confidence=rank;observer?.disconnect();clearTimeout(timer);output.replaceChildren(el('p',tr('Carico la mappa…'),'hint'));
      timer=setTimeout(async()=>{const current=()=>seq===sequence&&generation===state.generation&&navigation===state.navigation&&state.view==='flows'&&output.isConnected&&!$('workspace').hidden&&input.value===words&&select.value===rank;
        try{const response=await api(projectUrl('/api/flows',{q:words,confidence:rank,limit:'12',max_chars:'16000'}));if(current())draw(response.data,output);}catch(error){if(current())showError(error);}
      },delay);
    }
    input.oninput=()=>queue(180);select.onchange=()=>queue();queue();
  }
  return {open};
};
