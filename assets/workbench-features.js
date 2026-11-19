'use strict';
window.createCgFeatures = ({state,el,tr,api,projectUrl,sourceBlock,showError,hideViews,selectSymbol}) => {
  const $=id=>document.getElementById(id);
  let listSequence=0,epoch=0,selected=null,symbolSelector=null,prepared=null,blob=null,frame=null,graphIds=new Set(),catalog=null,tab='understand',screen=0;
  const current=stamp=>stamp===epoch&&state.view==='features'&&!$('features').hidden;
  const status=value=>tr({planned:'Progettata',active:'Attiva',project_declaration:'Dichiarata',code_candidate:'Punto di partenza',resolved:'Riferimento risolto',missing:'Riferimento mancante',ambiguous:'Riferimento ambiguo',referenced_declaration:'Dichiarazione con evidenze',unverified_declaration:'Dichiarazione da verificare',stale_declaration:'Dichiarazione obsoleta',verified_declaration:'Evidenza verificata',unversioned_declaration:'Evidenza senza hash',unresolved_evidence:'Evidenza non risolta'}[value]||value);
  function problem(error,stamp){if(current(stamp))showError(error);}
  function button(text,action,cls){const item=el('button',tr(text),cls);item.onclick=action;return item;}
  function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value)],{type:'application/json'}));const link=el('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function showProof(box,proof){box.append(el('small',`${proof.file||'?'}:${proof.line||'?'} · ${status(proof.status)}`));if(proof.source)box.append(sourceBlock(proof.source));}
  function showTests(box,verification){box.append(el('h3',tr('Test pertinenti')));for(const item of verification.tests.slice(0,30)){const node=item.symbol;box.append(button(`${node.name} · ${node.file}:${node.line}`,()=>selectSymbol(node.id).catch(showError),'relation'));}
    if(!verification.tests.length)box.append(el('p',tr('Nessun test riconosciuto nel perimetro: controlla anche la suite.'),'warning'));
    if(verification.tests.length>30)box.append(el('p',tr('Elenco test parziale: espandi con verify dalla CLI.'),'warning'));
    if(verification.broader_verification_required)box.append(el('p',tr('Servono verifiche più ampie: il grafo incontra confini o dati non risolti.'),'warning'));
  }
  function showReview(result,box){
    box.replaceChildren(el('h2',tr('Confronto della modifica')),el('p',prepared?.packet.feature.title||result.feature,'tag'),el('p',tr('Il confronto riguarda il sottografo preparato. Nessun test è stato eseguito.'),'hint'));
    box.append(el('p',result.status,'tag'));
    for(const item of result.changes||[])box.append(el('div',`${item.identity.file} · ${item.identity.name} · ${item.kind}`,'resource'));
    if(result.callsites_added!==undefined)box.append(el('p',`${tr('Callsite aggiunte')}: ${result.callsites_added} · ${tr('Callsite assenti')}: ${result.callsites_removed}`));
    for(const item of result.contracts||[])box.append(el('div',`${item.id} · ${tr('Contratto da ricontrollare')}`,'resource'));
    if(result.reference||result.status==='declaration_not_available')box.append(el('p',tr('La definizione precedente è mancante o ambigua: gli ID non vengono riutilizzati.'),'warning'));
    if(result.verification?.tests)showTests(box,result.verification);
    for(const test of result.prior_tests||[])box.append(el('p',`${tr('Test della preparazione precedente')}: ${test.identity.file} · ${test.identity.name}`,'hint'));
    if(result.prior_tests_truncated)box.append(el('p',`${tr('Elenco precedente parziale')}: ${(result.prior_tests||[]).length} / ${result.prior_tests_total}`,'warning'));
    if(result.feature_evidence_changed)box.append(el('p',tr('Evidenze della scheda cambiate: ricontrollale.'),'warning'));
    if(result.selection_truncated)box.append(el('p',tr('Flusso parziale: restringi o approfondisci il perimetro.'),'warning'));
    box.append(button('Scarica confronto JSON',()=>download(result,'feature-review.json')));
  }
  async function review(box,stamp){
    if(!prepared||prepared.root!==state.project.path)return;
    const preparation=prepared;
    try{const response=await api('/api/feature-review',{method:'POST',body:JSON.stringify({generation:state.generation,preparation_id:preparation.id})});if(current(stamp)&&box.isConnected&&prepared===preparation)showReview(response.data,box);}catch(error){if(box.isConnected)problem(error,stamp);}
  }
  function draw(card,detailBox,stamp){
    const drawEpoch=++screen;detailBox.replaceChildren();
    const heading=el('div',undefined,'section-head');heading.append(el('h2',card.title),el('span',`${status(card.provenance)} · ${status(card.stage)}`,'tag'));detailBox.append(heading);
    if(card.summary)detailBox.append(el('p',card.summary,'panel-lead'));
    const tabs=el('div',undefined,'tabs');
    for(const [key,label] of [['understand','Capisci'],['prepare','Prepara per AI'],['review','Rivedi modifica']]){const control=button(label,()=>{tab=key;draw(card,detailBox,stamp);});control.setAttribute('aria-selected',String(tab===key));tabs.append(control);}detailBox.append(tabs);
    const content=el('div',undefined,'feature-content');detailBox.append(content);
    if(tab==='understand'){
      content.append(el('p',tr('Le schede dichiarano uno scopo; le chiamate nel grafo mostrano i collegamenti risolti.'),'hint'));
      for(const ref of card.references){const item=el('div',undefined,'resource');item.append(el('strong',`${ref.reference.file} · ${ref.reference.symbol||tr('Modulo')}`),el('span',status(ref.status),'tag'));
        for(const node of ref.candidates)item.append(button(`${node.name} · ${node.file}:${node.line}`,()=>selectSymbol(node.id).catch(showError),'relation'));content.append(item);}
      if(!card.roots.length)content.append(el('p',tr('Funzionalità progettata o riferimenti ancora mancanti. Non è una prova di implementazione.'),'warning'));
      if(card.graph_view.node_ids.length){frame=el('iframe');frame.title=tr('Flusso della funzionalità');frame.className='feature-graph';frame.setAttribute('sandbox','allow-scripts');frame.dataset.epoch=String(stamp);graphIds=new Set(card.graph_view.node_ids);if(blob)URL.revokeObjectURL(blob);blob=URL.createObjectURL(new Blob([card.graph_view.html],{type:'text/html'}));frame.src=blob;content.append(frame);}
      if(card.graph_view.truncated)content.append(el('p',tr('Flusso parziale: restringi o approfondisci il perimetro.'),'warning'));
      if(card.evidence.length){content.append(el('h3',tr('Evidenze della scheda')));for(const proof of card.evidence)showProof(content,proof);}
      content.append(el('h3',tr('Contratti dichiarati')));
      if(!card.contracts.length)content.append(el('p',tr('Nessun contratto dichiarato. Le firme non descrivono tutte le regole di comportamento.'),'hint'));
      for(const contract of card.contracts){const item=el('article',undefined,'resource-link');item.append(el('strong',contract.description),el('p',status(contract.status),'tag'));for(const proof of contract.evidence)showProof(item,proof);content.append(item);}
      content.append(el('h3',tr('Risorse condivise')));
      for(const resource of card.resources){const item=el('article',undefined,'resource-link');item.append(el('strong',resource.path),el('p',status(resource.status),'tag'));for(const proof of [...resource.producers,...resource.consumers])showProof(item,proof);content.append(item);}
      for(const missing of card.missing_resources)content.append(el('p',`${tr('Risorsa non risolta')}: ${missing}`,'warning'));
      showTests(content,card.verification);
    }else if(tab==='prepare'){
      const label=el('label',tr('Cosa vuoi modificare?'));label.htmlFor='feature-intent';const intent=el('textarea');intent.id='feature-intent';intent.maxLength=1000;intent.rows=3;intent.placeholder=tr('Esempio: aggiungere un campo al catalogo');
      const budgetLabel=el('label',tr('Budget in caratteri'));budgetLabel.htmlFor='feature-budget';const budget=el('input');budget.id='feature-budget';budget.type='number';budget.min=512;budget.max=100000;budget.value=12000;
      const subjects=el('fieldset'),legend=el('legend',tr('Collaboratori che modificherai (massimo 8)'));subjects.append(legend);
      for(const node of card.collaborators||[]){const label=el('label'),check=el('input');check.type='checkbox';check.value=node.file+':'+node.name;check.className='feature-verify';label.append(check,el('span',node.file+' · '+node.name));subjects.append(label);}
      const output=el('div');let request=0;intent.oninput=budget.oninput=()=>{request++;output.replaceChildren();};
      const generate=button('Genera pacchetto AI',async()=>{const seq=++request,maximum=Number(budget.value),description=intent.value;try{const response=await api('/api/prepare',{method:'POST',body:JSON.stringify({generation:state.generation,feature:card.id,intent:description,max_chars:maximum,confidence:$('feature-confidence').value,depth:Number($('feature-depth').value),verification_symbols:[...subjects.querySelectorAll('input:checked')].map(input=>input.value)})});if(!current(stamp)||seq!==request||tab!=='prepare'||drawEpoch!==screen)return;const packet=response.data;output.replaceChildren();
          if(packet.status==='budget_insufficient'){output.append(el('p',tr('Budget insufficiente: aumenta il limite per conservare i metadati.'),'warning'));return;}
          prepared={id:packet.preparation_id,root:state.project.path,packet,feature:card.id};
          output.append(el('p',`${packet.budget.actual_chars} / ${packet.budget.max_chars} · ${tr('Token stimati')}: ${Math.ceil(packet.budget.actual_chars/4)}`,'tag'));
          if(packet.status==='partial')output.append(el('p',tr('Pacchetto parziale: controlla le omissioni e richiedi gli approfondimenti.'),'warning'));
          output.append(button('Scarica pacchetto JSON',()=>download(packet,'feature-context.json')));
          output.append(el('pre',JSON.stringify(packet),'source'));
        }catch(error){if(seq===request&&drawEpoch===screen&&tab==='prepare')problem(error,stamp);}} ,'primary');
      subjects.onchange=()=>{request++;output.replaceChildren();};content.append(label,intent,subjects,budgetLabel,budget,generate,output);
    }else{
      content.append(el('p',tr('Dopo la modifica aggiorna l’analisi, poi confronta con la preparazione precedente.'),'hint'));
      if(prepared?.root===state.project.path&&prepared.feature===card.id)content.append(button('Confronta con preparazione',()=>review(content,stamp),'primary'));
      else content.append(el('p',tr('Genera prima un pacchetto nella sessione. La preparazione scade alla chiusura del servizio.'),'hint'));
    }
  }
  async function select(id,detailBox,symbol=null){
    const stamp=++epoch;selected=id;symbolSelector=symbol;
    detailBox.replaceChildren(el('p',tr('Carico la funzionalità…'),'hint'));
    try{const response=await api(projectUrl('/api/feature',{...(symbol?{symbol}:{id}),view:String(stamp),confidence:$('feature-confidence').value,depth:$('feature-depth').value}));if(current(stamp)){selected=response.data.id;draw(response.data,detailBox,stamp);}}catch(error){problem(error,stamp);}
  }
  async function open(preserve=false,featureId=null){
    state.navigation++;state.inspection++;state.selection++;state.graphSequence++;state.view='features';hideViews();$('features').hidden=false;$('inspector').hidden=true;$('workspace').classList.remove('has-symbol');
    const stamp=++epoch,box=$('features'),generation=state.generation,navigation=state.navigation;catalog=null;if(prepared&&prepared.root!==state.project.path)prepared=null;
    if(!preserve){selected=null;symbolSelector=null;tab='understand';}
    if(featureId){selected=featureId;symbolSelector=null;}
    box.replaceChildren(el('span','CODE-GRAPH / '+tr('Funzionalità'),'eyebrow'),el('h1',tr('Capisci e modifica')),el('p',tr('Individua il flusso e l’impatto, prepara il contesto per l’agente e confronta il risultato dopo la modifica.'),'panel-lead'));
    const controls=el('div',undefined,'feature-controls'),query=el('input');query.id='feature-search';query.type='search';query.placeholder=tr('Cerca una funzionalità o un punto d’ingresso');query.setAttribute('aria-label',query.placeholder);
    const confidence=el('select');confidence.id='feature-confidence';confidence.setAttribute('aria-label',tr('Confidenza'));for(const [value,label] of [['high','Risoluzioni dirette'],['medium','Dirette e inferite'],['low','Includi ipotesi']]){const option=el('option',tr(label));option.value=value;confidence.append(option);}confidence.value='medium';
    const depth=el('select');depth.id='feature-depth';depth.setAttribute('aria-label',tr('Profondità'));for(const value of [1,2,3]){const option=el('option',String(value));option.value=String(value);depth.append(option);}depth.value='2';controls.append(query,confidence,depth);box.append(controls);
    const cards=el('div',undefined,'cards'),detailBox=el('div');detailBox.id='feature-detail';box.append(cards,detailBox);
    const symbolRow=el('div',undefined,'feature-symbol-row'),symbol=el('input');symbol.id='feature-symbol';symbol.placeholder=tr('Oppure apri file:simbolo');symbol.setAttribute('aria-label',symbol.placeholder);const bySymbol=button('Apri simbolo',()=>{tab='understand';select(null,detailBox,symbol.value);});symbolRow.append(symbol,bySymbol);box.insertBefore(symbolRow,cards);
    function renderCards(){cards.replaceChildren();if(!catalog)return;for(const item of catalog.items){const card=button('',()=>{tab='understand';select(item.id,detailBox);},'area-card feature-card');card.dataset.feature=item.id;card.append(el('strong',item.title),el('small',`${status(item.provenance)} · ${status(item.stage)}`));if(item.summary)card.append(el('p',item.summary));for(const proof of item.entrypoint_evidence||[])card.append(el('small',proof.kind+' · '+proof.file+(proof.line?':'+proof.line:'')));cards.append(card);}}
    let queryTimer;query.oninput=()=>{const seq=++listSequence,words=query.value;catalog=null;cards.replaceChildren(el('p',tr('Ricerca…'),'hint'));clearTimeout(queryTimer);queryTimer=setTimeout(async()=>{const active=()=>seq===listSequence&&cards.isConnected&&state.view==='features'&&state.generation===generation&&state.navigation===navigation&&query.value===words;try{const response=await api(projectUrl('/api/features',{q:words}));if(active()){catalog=response.data;renderCards();}}catch(error){if(active())showError(error);}},180);};confidence.onchange=depth.onchange=()=>{if(selected||symbolSelector)select(selected,detailBox,symbolSelector);};
    try{const response=await api(projectUrl('/api/features'));if(!current(stamp))return;const initial=response.data;if(query.value)query.oninput();else{catalog=initial;renderCards();}if(initial.candidates_truncated)box.append(el('p',tr('Catalogo parziale: usa anche la ricerca per simbolo.'),'hint'));for(const issue of initial.issues)box.append(el('p',issue.message,'warning'));
      if(initial.introduction){const details=el('details'),summary=el('summary',tr('Descrizione dal README'));details.append(summary,el('pre',initial.introduction.text,'source'));box.insertBefore(details,cards);}
      if(!query.value&&!initial.items.length)cards.append(el('p',tr('Nessuna scheda disponibile: puoi dichiarare una funzionalità progettata nel manifest.'),'hint'));
      if(prepared?.root===state.project.path)box.insertBefore(button(tr('Rivedi la preparazione precedente')+' · '+prepared.packet.feature.title,()=>{++epoch;review(detailBox,epoch);}),detailBox);
      if((preserve||featureId)&&(selected||symbolSelector))select(selected,detailBox,symbolSelector);
    }catch(error){problem(error,stamp);}
  }
  window.addEventListener('message',event=>{if(!frame||!frame.isConnected||event.source!==frame.contentWindow||!current(Number(frame.dataset.epoch))||event.data?.type!=='cg-select'||event.data.generation!==state.generation||event.data.view!==String(epoch)||!Number.isInteger(event.data.id)||!graphIds.has(event.data.id))return;selectSymbol(event.data.id).catch(showError);});
  return {open};
};
