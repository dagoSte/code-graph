'use strict';
window.createCgPanels = ({state,el,tr,api,projectUrl,sourceBlock,showError,hideViews}) => {
  const $=id=>document.getElementById(id), SVG='http://www.w3.org/2000/svg';
  const number=n=>new Intl.NumberFormat(document.documentElement.lang,{maximumFractionDigits:1}).format(n);
  function svg(tag,attrs={}){const node=document.createElementNS(SVG,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);return node;}
  function label(parent,text,x,y){const node=svg('text',{x,y,class:'chart-label'});node.textContent=text;parent.append(node);}
  function chart(samples,trend=false){
    const plot=svg('svg',{viewBox:'0 0 620 290',role:'img','aria-label':tr(trend?'Andamento cumulativo':'Confronto per lettura')});
    const points=samples.slice(-8),base=points.map(s=>s.baseline_chars/4),actual=points.map(s=>s.actual_chars/4);
    let cumulative=0;const totals=samples.map(s=>(cumulative+=(s.baseline_chars-s.actual_chars)/4)).slice(-8);
    const low=trend?Math.min(0,...totals):0,high=Math.max(1,...(trend?totals:base.concat(actual))),span=high-low||1;
    const py=value=>240-(value-low)/span*200;
    for(let i=0;i<5;i++){const value=low+span*i/4,y=py(value);plot.append(svg('line',{x1:52,x2:600,y1:y,y2:y,class:'chart-gridline'}));label(plot,number(value),2,y+4);}
    if(trend){const dots=totals.map((value,i)=>[76+i*65,py(value)]);plot.append(svg('polyline',{points:dots.map(point=>point.join(',')).join(' '),class:'chart-line'}));for(const [x,y] of dots)plot.append(svg('circle',{cx:x,cy:y,r:4,class:'chart-point'}));}
    else for(let i=0;i<points.length;i++){const x=65+i*65;for(const [value,offset,cls] of [[base[i],0,'chart-baseline'],[actual[i],23,'chart-optimized']])plot.append(svg('rect',{x:x+offset,y:py(value),width:20,height:Math.max(0,240-py(value)),rx:3,class:cls}));}
    for(let i=0;i<points.length;i++)label(plot,String(Math.max(0,samples.length-8)+i+1),77+i*65,267);
    return plot;
  }
  function stats(box,items){const cards=el('div',undefined,'stats');for(const [value,title,unit] of items){const card=el('div',undefined,'stat');card.append(el('strong',number(value)),el('span',tr(title)),el('div',unit||'', 'metric-unit'));cards.append(card);}box.append(cards);}
  function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const link=el('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function efficiency(){
    const box=$('efficiency');box.replaceChildren(el('span','CODE-GRAPH / '+tr('Efficienza'),'eyebrow'),el('h1',tr('Efficienza')));
    box.append(el('p',tr('Le misure riguardano i payload dati, non l’intera conversazione. Token stimati da caratteri Unicode/4; tokenizer non configurato. La proiezione usa la mediana dei campioni osservati e non garantisce un risparmio futuro.'),'panel-lead'));
    const samples=state.samples,requests=state.requests||[],saved=samples.reduce((sum,sample)=>sum+(sample.baseline_chars-sample.actual_chars)/4,0);
    stats(box,[[requests.reduce((sum,r)=>sum+r.bytes,0)/1024,'Dati delle letture','KiB / UTF-8'],[saved,'Token stimati risparmiati','Unicode chars / 4'],[Object.keys(state.snippets).length,'Estratti unici',String(samples.length)+' '+tr('Campioni confrontabili')],[requests.length?requests.reduce((sum,r)=>sum+r.ms,0)/requests.length:0,'Latenza media','ms']]);
    const toolbar=el('div',undefined,'toolbar'),measure=el('button',tr('Misura la scheda sorgente selezionata'));
    measure.disabled=!state.lastSymbol;
    measure.onclick=async()=>{measure.disabled=true;const generation=state.generation,navigation=state.navigation;try{const result=await api(projectUrl('/api/efficiency',{id:state.lastSymbol}));if(generation!==state.generation||navigation!==state.navigation||$('efficiency').hidden)return;if(result.data.evidence_preserved===true){state.samples.push(result.data);efficiency();}else showError(new Error(tr('Risultato parziale: usa un perimetro più piccolo.')));}catch(error){showError(error);}finally{measure.disabled=!state.lastSymbol;}};
    const save=el('button',tr('Scarica misure JSON'));save.onclick=()=>download({format:'codegraph-efficiency/1',revision:state.project.content_revision,estimator:'unicode_characters/4',measured_tokens:null,requests,samples},'codegraph-efficiency.json');
    const clear=el('button',tr('Cancella misure'));clear.onclick=()=>{state.samples=[];state.requests=[];efficiency();};toolbar.append(measure,save,clear);box.append(toolbar,el('p',tr('La calibrazione legge una volta tutte le sezioni per confrontarle con la sola scheda sorgente. Non esegue codice.'),'hint'));
    if(!state.lastSymbol)box.append(el('p',tr('Leggi prima un simbolo per misurare la scheda sorgente.'),'hint'));
    if(!samples.length){box.append(el('p',tr('Nessun campione confrontabile. Apri Impatto e test oppure misura una scheda sorgente.'),'empty'));return;}
    const row=el('div',undefined,'chart-grid');for(const trend of [false,true]){const card=el('div',undefined,'chart-card');card.append(el('h2',tr(trend?'Andamento cumulativo':'Confronto per lettura')),el('p','Unicode chars / 4','metric-unit'),chart(samples,trend));if(!trend){const legend=el('div',undefined,'chart-legend');legend.append(el('span',tr('Baseline')),el('span',tr('Ottimizzato')));card.append(legend);}row.append(card);}box.append(row);
    if(samples.some(s=>s.saved_chars<0))box.append(el('p',tr('In questo campione il formato strutturato aggiunge dati: il delta può essere negativo.'),'warning'));
    const deltas=samples.map(s=>(s.baseline_chars-s.actual_chars)/4).sort((a,b)=>a-b),middle=Math.floor(deltas.length/2),median=deltas.length%2?deltas[middle]:(deltas[middle-1]+deltas[middle])/2;
    const projection=el('div',undefined,'projection'),label=el('label',tr('Prossime letture')),count=el('input'),value=el('strong');count.type='number';count.id='forecast-count';count.min='1';count.max='1000';count.value=String(state.forecast||10);label.htmlFor=count.id;
    const update=()=>{state.forecast=Math.max(1,Math.min(1000,Number(count.value)||1));value.textContent=number(median*state.forecast)+' '+tr('Token stimati risparmiati');};count.oninput=update;update();projection.append(label,count,el('span',tr('Proiezione stimata')),value);box.append(projection);
    const table=el('table',undefined,'measure-table'),head=el('tr');for(const title of ['#','Scope',tr('Baseline'),tr('Ottimizzato'),'Δ chars'])head.append(el('th',title));table.append(head);
    samples.slice(-20).forEach((sample,index)=>{const row=el('tr');for(const cell of [String(index+1),sample.scope,number(sample.baseline_chars),number(sample.actual_chars),number(sample.saved_chars)])row.append(el('td',cell));table.append(row);});box.append(table);
  }
  function sqlProof(item){
    const box=el('div',undefined,'sql-proof'),hash=el('p',undefined,'hint');
    box.append(el('p',`${item.file}:${item.line}`,'hint'));hash.append(el('span','SHA-256: '),el('code',item.sha256));box.append(hash);
    const details=el('details');details.append(el('summary',tr('Finestra sorgente SQL')));let loaded=false;
    details.addEventListener('toggle',()=>{if(details.open&&!loaded){loaded=true;details.append(sourceBlock(item.source));}});box.append(details);return box;
  }
  function sqlSection(box,data){
    const section=el('section',undefined,'sql-architecture');section.id='architecture-sql';
    section.append(el('h2',tr('Risorse SQL')),el('p',tr('Dialetto SQL')+': '+(data.dialect==='sqlite'?'SQLite':data.dialect),'tag'),
      el('p',tr('Dichiarazioni e accessi espliciti del sottoinsieme SQLite, separati dagli archi di chiamata. CTE e sintassi non supportata possono omettere accessi: non è un inventario completo.'),'panel-lead'));
    stats(section,[[new Set(data.items.map(item=>item.file)).size,'File SQL mostrati',''],
      [data.items.filter(item=>item.kind==='sql.declare').length,'Dichiarazioni SQL mostrate',''],
      [data.items.filter(item=>item.kind==='sql.read').length,'Letture SQL mostrate',''],
      [data.items.filter(item=>item.kind==='sql.write').length,'Scritture SQL mostrate','']]);
    section.append(el('p',tr('Evidenze SQL mostrate')+': '+data.items.length+' / '+data.total+'. '+tr('Limite massimo: 500 evidenze, incluso lo stato dei file. I conteggi descrivono solo i record restituiti.'),'hint'));
    if(data.truncated)section.append(el('p',tr('Risultato SQL parziale: altre evidenze sono omesse. Restringi il progetto indicizzato o approfondisci dalla CLI.'),'warning'));
    const unsupported=data.items.filter(item=>item.kind==='sql.file'&&!item.value.supported).length;
    if(unsupported)section.append(el('p',tr('File SQL fuori sottoinsieme nei record mostrati')+': '+unsupported+'. '+tr('Sintassi SQL fuori dal sottoinsieme: accessi omessi per questo file.'),'warning'));
    const labels={'sql.file':'Stato del file SQL','sql.declare':'Dichiarazione SQL','sql.read':'Lettura SQL','sql.write':'Scrittura SQL'};
    const resource=value=>(value.schema?value.schema+'.':'')+value.name;
    if(!data.items.some(item=>item.kind!=='sql.file'))section.append(el('p',tr('Nessuna dichiarazione o accesso SQL nei record restituiti.'),'empty'));
    const observations=el('details',undefined,'evidence-card sql-records');observations.append(el('summary',tr('Evidenze nel sorgente SQL')+' · '+data.items.length));section.append(observations);
    for(const item of data.items){
      const card=el('article',undefined,'evidence-card sql-observation');card.append(el('strong',tr(labels[item.kind])+(item.kind==='sql.file'?'':' · '+resource(item.value))));
      if(item.kind==='sql.file')card.append(el('p',tr(item.value.supported?'Sottoinsieme sintattico riconosciuto; non prova la completezza degli accessi.':'Sintassi SQL fuori dal sottoinsieme: accessi omessi per questo file.'),item.value.supported?'hint':'warning'));
      else{
        card.append(el('p',tr('Osservazione sintattica SQL'),'tag'),el('p',tr('Schema SQL')+': '+(item.value.schema||tr('Non qualificato')),'hint'));
        if(item.value.object_kind)card.append(el('span',tr(item.value.object_kind==='view'?'Vista SQL':'Tabella SQL'),'tag'));
        if(item.value.temporary)card.append(el('span',tr('Risorsa SQL temporanea'),'tag'));
      }
      card.append(sqlProof(item));observations.append(card);
    }
    section.append(el('p',tr('I candidati coprono solo gli accessi mostrati, nello stesso script. Non collegano file o database e non provano connessioni, ordine delle migrazioni o legami a runtime.'),'hint'));
    if(!data.links.length)section.append(el('p',tr('Nessuna relazione candidata SQL nei record restituiti.'),'empty'));
    const links=el('details',undefined,'evidence-card sql-links');links.append(el('summary',tr('Relazioni candidate SQL')+' · '+data.links.length));section.append(links);
    for(const link of data.links){
      const card=el('article',undefined,'resource-link evidence-card sql-candidate');card.append(el('strong',resource(link.resource)),el('p',tr('Candidato SQL'),'tag'));
      for(const [label,item] of [['Dichiarazione SQL',link.producer],[labels[link.consumer.kind],link.consumer]])card.append(el('h3',tr(label)),sqlProof(item));
      for(const assumption of link.assumptions)card.append(el('p',tr(assumption),'hint'));links.append(card);
    }
    box.append(section);
  }
  async function architecture(){
    const generation=state.generation,navigation=state.navigation,box=$('architecture');box.replaceChildren(el('h1',tr('Architettura e risorse')),el('p',tr('Le risorse non sono archi di chiamata. Le ipotesi sul percorso e le dichiarazioni manuali restano visibili.'),'panel-lead'));
    const result=await api(projectUrl('/api/architecture'));if(generation!==state.generation||navigation!==state.navigation||box.hidden)return;
    const data=result.data;if(data.sql){sqlSection(box,data.sql);box.append(el('h2',tr('Altre risorse e dichiarazioni')));}stats(box,[[data.totals.observations,'Osservazioni',''],[data.totals.declarations,'Dichiarazioni',''],[data.totals.links,'Relazioni candidate','']]);
    if(!data.links.length)box.append(el('p',tr('Nessuna relazione candidata riconosciuta.'),'empty'));
    for(const link of data.links){const card=el('article',undefined,'resource-link');card.append(el('strong',link.resource||'?'),el('p',`${link.status} · ${link.rule}`,'tag'));
      for(const [direction,proof] of [['producer',link.producer],['consumer',link.consumer]]){card.append(el('p',`${direction} → ${proof.file||'?'}:${proof.line||'?'} · ${proof.status||proof.normalization||'unknown'}`));if(proof.source)card.append(sourceBlock(proof.source));}
      for(const assumption of link.assumptions||[])card.append(el('p',assumption,'hint'));box.append(card);}
    const details=el('details'),summary=el('summary',tr('Dichiarazioni'));details.append(summary);for(const item of data.declarations){const card=el('div',undefined,'resource');card.append(el('strong',`${item.kind||item.id||'?'} · ${item.status}`),el('pre',JSON.stringify(item,null,2)));details.append(card);}box.append(details);
    for(const issue of data.issues)box.append(el('p',issue.message||issue.status,'warning'));
    if(data.truncated)box.append(el('p',tr('Risultato parziale: usa un perimetro più piccolo.'),'warning'));
  }
  function open(view){state.navigation++;state.inspection++;state.selection++;state.graphSequence++;state.view=view;hideViews();$(view).hidden=false;$('inspector').hidden=true;$('workspace').classList.remove('has-symbol');if(view==='efficiency')efficiency();else architecture().catch(showError);}
  return {open,efficiency,architecture};
};
