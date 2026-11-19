'use strict';
/* «Modifica» section: six-step change flow (contract 1.6 § D). Text only: DOM nodes and textContent. */
/* Mirrors ChangeFlow._relative: paths the engine refuses (empty, absolute, '..', backslash, NUL, .git, .code-graph)
   are shown as excluded and never sent with Approve. A valid path whose symbol is unresolved stays selectable. */
function editablePath(path){if(typeof path!=='string'||!path||path.includes('\0')||path.includes('\\')||path.startsWith('/'))return false;const parts=path.split('/').filter(part=>part&&part!=='.');return parts.length>0&&!parts.includes('..')&&parts[0]!=='.git'&&parts[0]!=='.code-graph';}
window.createCgChange=function({state,el,tr,api,hideViews,showError,selectSymbol}){
  const $=id=>document.getElementById(id);
  const T=text=>tr(text);
  const L=text=>text; /* label translated when rendered */
  const K=key=>window.cgI18n&&window.cgI18n.label?window.cgI18n.label(key):key; /* keyed label, see workbench-i18n.js */
  const STEPS=[['analyze',L('Analizza')],['understand',L('Capisci')],['plan',L('Pianifica')],['edit',L('Modifica')],['verify','change.verify'],['review',L('Rileggi e integra')]];
  const STEP_OF={draft:'understand',planning:'plan',planned:'plan',approved:'edit',baseline:'edit',editing:'edit',edited:'verify',verifying:'verify',verified:'review',reviewing:'review',reviewed:'review',integrated:'review',discarded:'review'};
  const ACTIVE=new Set(['planning','baseline','editing','verifying','reviewing']);
  const TERMINAL=new Set(['integrated','discarded']);
  const STATE_LABEL={draft:L('Bozza'),planning:L('Piano in preparazione'),planned:L('Piano pronto'),approved:L('Piano approvato'),baseline:L('Test prima della modifica'),editing:L('Modifica in corso'),edited:L('Modificata'),verifying:L('Verifica in corso'),verified:L('Verificata'),reviewing:L('Rilettura in corso'),reviewed:L('Riletta'),integrated:L('Integrata'),discarded:L('Scartata')};
  const KIND_LABEL={touch:L('Cambia'),affected:L('Ne risente'),test:L('Test'),hidden:L('Non visibile al grafo')};
  const TEST_STATUS={passed:L('superati'),failed:L('falliti'),error:L('errore'),timeout:L('tempo scaduto')};
  const VERDICT={accept:L('accetta'),changes:L('chiede modifiche'),reject:L('rifiuta')};
  const ui={generation:'',changes:[],change:null,id:null,view:null,events:[],next:0,agents:null,consent:false,client:null,symbol:null,asks:[],drafts:{},timer:null,askTimer:null,epoch:0,busy:false};

  function node(tag,cls,...children){const n=document.createElement(tag);if(cls)n.className=cls;for(const child of children)if(child!==null&&child!==undefined&&child!==false)n.append(child instanceof Node?child:String(child));return n;}
  function button(label,onclick,cls){const b=node('button',cls,label);b.type='button';b.onclick=()=>{Promise.resolve(onclick()).catch(showError);};return b;}
  function field(label,input,id){const box=node('div','change-field');input.id=id;const l=node('label',null,label);l.htmlFor=id;box.append(l,input);return box;}
  /* Drafts belong to one analysis and one change: switching either never carries a draft over. */
  function slot(key){return ui.generation+'|'+(ui.id||'new')+'|'+key;}
  function draft(input,key,fallback='',shared=false){const k=shared?key:slot(key);input.value=ui.drafts[k]??fallback;input.oninput=()=>{ui.drafts[k]=input.value;};return input;}
  function textarea(rows){const t=document.createElement('textarea');t.rows=rows;return t;}
  /* The Claude spending cap is a text field: a number input would turn unparsable text into '' and a redraw
     (theme, language, view, change) would then send "no cap". The raw text stays in the draft and cap() decides. */
  function money(){const n=document.createElement('input');n.type='text';n.inputMode='decimal';n.autocomplete='off';n.spellcheck=false;n.placeholder='0.50';return n;}
  const DECIMAL=/^\+?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$/;
  /* {} only for a really empty field, {budget_usd} for a finite number > 0 (one comma is read as the decimal point), an error otherwise. */
  function cap(input){const raw=input.value.trim();if(!raw)return {};const value=DECIMAL.test(raw)?Number(raw.replace(',','.')):NaN;if(!Number.isFinite(value)||value<=0)throw new Error(T('Il tetto di spesa deve essere un numero maggiore di zero.'));return {budget_usd:value};}
  function select(options,value){const s=document.createElement('select');for(const [v,label,disabled] of options){const o=node('option',null,label);o.value=v;o.disabled=!!disabled;s.append(o);}if(value!==undefined)s.value=value;return s;}
  function list(items,render,empty){const ul=node('ul','change-list');for(const item of items||[])ul.append(render(item));if(!ul.children.length&&empty)return node('p','empty',empty);return ul;}
  function clip(text,limit=600){text=String(text??'');return text.length>limit?text.slice(0,limit)+'…':text;}
  function where(item){return [item.path||item.file,item.line?':'+item.line:''].join('')+(item.symbol||item.name?' '+(item.symbol||item.name):'');}
  function clock(t){return typeof t==='string'&&t.length>=19?t.slice(11,19):'';}
  async function post(path,body){return api(path,{method:'POST',body:JSON.stringify({generation:state.generation,...body})});}
  function current(){return state.view==='change'&&ui.generation===state.generation;}

  async function open(preserve=false){
    state.navigation++;state.inspection++;state.selection++;state.graphSequence++;state.view='change';hideViews();
    $('change-flow').hidden=false;$('inspector').hidden=true;$('workspace').classList.remove('has-symbol');
    if(ui.generation!==state.generation){Object.assign(ui,{generation:state.generation,changes:[],change:null,id:null,view:null,events:[],next:0,asks:[],symbol:null,drafts:{}});}
    if(!preserve&&state.detail)ui.symbol={path:state.detail.file,line:state.detail.line,symbol:state.detail.name};
    const epoch=++ui.epoch;render();
    try{
      const [agents,changes]=await Promise.all([api('/api/agents'),api('/api/changes?'+new URLSearchParams({generation:state.generation}))]);
      if(epoch!==ui.epoch||!current())return;
      ui.agents=agents.clients;ui.consent=agents.consent;ui.changes=changes.data;
      const usable=ui.agents.filter(c=>c.available).map(c=>c.client);if(!ui.client||!usable.includes(ui.client))ui.client=usable[0]||'codex';
      if(ui.id&&!ui.changes.some(c=>c.id===ui.id))ui.id=null;
      if(!ui.id&&ui.changes.length)ui.id=ui.changes.find(c=>!TERMINAL.has(c.state))?.id||null;
      if(ui.id)await load(ui.id,true);else render();
    }catch(error){showError(error);}
    schedule();
  }

  async function load(id,keepView=false){
    const result=await api('/api/change?'+new URLSearchParams({generation:state.generation,id}));
    if(!current())return;
    if(ui.id!==id||!keepView){ui.events=[];ui.next=0;ui.view=null;}
    ui.id=id;ui.change=result.data;await pull();render();
  }

  async function pull(){
    if(!ui.id)return false;
    const result=await api('/api/change/events?'+new URLSearchParams({generation:state.generation,id:ui.id,since:String(ui.next)}));
    if(!current())return false;
    const fresh=result.data.events||[];ui.events.push(...fresh);if(ui.events.length>400)ui.events.splice(0,ui.events.length-400);
    ui.next=Math.max(ui.next,result.data.next||0);return fresh.length>0;
  }

  function schedule(){
    clearTimeout(ui.timer);if(!current())return;
    const active=ui.change&&ACTIVE.has(ui.change.state);
    ui.timer=setTimeout(async()=>{
      if(!current())return;
      try{
        const before=ui.change&&ui.change.state+'|'+ui.change.updated;const fresh=await pull();
        if(fresh||active){const result=await api('/api/change?'+new URLSearchParams({generation:state.generation,id:ui.id}));if(!current())return;ui.change=result.data;}
        if(ui.change&&ui.change.state+'|'+ui.change.updated!==before)render();else if(fresh)renderEvents();
      }catch(error){showError(error);}
      schedule();
    },ui.id?(active?1000:5000):5000);
  }

  function act(action,extra={}){
    return async()=>{
      if(['plan','edit','review'].includes(action)&&!ui.consent){await askConsent();if(!ui.consent)return;}
      ui.busy=true;render();
      try{const result=await post('/api/change/action',{id:ui.id,action,...extra});if(!current())return;ui.change=result.data;ui.view=null;await pull();}
      catch(error){if(error.code==='consent_required'){ui.consent=false;}showError(error);}
      finally{ui.busy=false;if(current()){render();schedule();}}
    };
  }

  /* ---------- consent ---------- */
  function askConsent(){
    return new Promise(resolve=>{
      const dialog=node('dialog','change-consent');dialog.setAttribute('aria-labelledby','change-consent-title');
      const title=node('h2',null,T('Consenso per gli agenti'));title.id='change-consent-title';
      const clients=list(ui.agents||[],c=>node('li',null,node('strong',null,c.client==='codex'?'Codex':'Claude'),' · ',c.available?[c.version||T('versione non rilevata'),c.path||''].filter(Boolean).join(' · '):T('non disponibile')+(c.reason?' · '+c.reason:'')));
      const table=node('table','change-table');
      const head=node('tr',null,node('th',null,T('Client')),node('th',null,T('Modalità')),node('th',null,T('Cartella')),node('th',null,T('Confinamento')));
      table.append(node('thead',null,head));const body=node('tbody');
      for(const [client,mode,folder,sandbox] of [
        ['Codex',T('Lettura'),T('Radice del progetto'),T('sandbox read-only · senza shell · approvazioni disattivate (never) · MCP di code-graph')],
        ['Codex',T('Scrittura'),T('Solo la copia isolata .code-graph/worktrees/<id>, mai il tuo checkout'),T('sandbox workspace-write · senza shell: i test li esegue solo code-graph · approvazioni disattivate (never) · MCP di code-graph')],
        ['Claude',T('Lettura'),T('Radice del progetto'),T('permission-mode plan · strumenti Read, Grep, Glob e solo l’MCP di code-graph · nessun hook o impostazione del progetto')],
        ['Claude',T('Scrittura'),T('Solo la copia isolata .code-graph/worktrees/<id>, mai il tuo checkout'),T('permission-mode acceptEdits · Read, Grep, Glob, Edit, Write, senza Bash · solo l’MCP di code-graph · nessun hook o impostazione del progetto')]])
        body.append(node('tr',null,node('td',null,client),node('td',null,mode),node('td',null,folder),node('td',null,sandbox)));
      table.append(body);
      const rules=list([T('I test del progetto li esegue solo code-graph, con il comando che approvi al passo Pianifica.'),T('L’agente riceve la richiesta da stdin; i log non contengono le variabili d’ambiente.'),T('Nessun push: l’integrazione resta locale e la decidi tu.'),T('Ogni esecuzione ha un tempo massimo e usa l’account del client sul tuo computer: può consumare la sua quota.')],text=>node('li',null,text));
      const close=value=>{ui.consent=value;dialog.close();dialog.remove();resolve(value);};
      const grant=button(T('Do il consenso per questa sessione'),async()=>{try{const result=await post('/api/agents/consent',{consent:true});close(result.consent===true);render();}catch(error){close(false);showError(error);}},'primary');
      const cancel=button(T('Annulla'),()=>close(false));
      dialog.addEventListener('cancel',event=>{event.preventDefault();close(false);});
      dialog.append(title,node('p','panel-lead',T('Un agente parte solo dopo il tuo consenso. Vale per questa sessione della dashboard; puoi revocarlo in qualsiasi momento.')),node('h3',null,T('Client rilevati')),clients,table,rules,node('div','change-actions',grant,cancel));
      document.body.append(dialog);dialog.showModal();grant.focus();
    });
  }
  async function revoke(){const result=await post('/api/agents/consent',{consent:false});ui.consent=result.consent;render();}

  /* ---------- rendering ---------- */
  function stepOf(change){return change?STEP_OF[change.state]||'understand':'understand';}
  function render(){
    if(!current())return;
    const box=$('change-flow');const change=ui.change,active=change&&ACTIVE.has(change.state);
    if(change)for(const item of ui.changes)if(item.id===change.id)Object.assign(item,{state:change.state,intent:change.intent});
    const head=node('div','section-head',node('div',null,node('span','eyebrow','CODE-GRAPH / '+T('Modifica con un agente')),node('h1',null,change?clip(change.intent,140):T('Nuova modifica')),node('p',null,change?T(STATE_LABEL[change.state]||change.state)+(change.round?' · '+T('giro')+' '+change.round+'/3':'')+' · '+T('autore')+' '+change.author+' · '+T('lettore')+' '+change.reviewer+(change.budget_usd?' · '+T('tetto Claude')+' '+change.budget_usd+' USD':''):T('Descrivi la modifica: un agente prepara il piano, tu lo approvi, poi modifica, verifica e rilettura dell’altro modello.'))));
    const picker=select([['',T('— Nuova modifica —')],...ui.changes.map(c=>[c.id,clip(c.intent,60)+' · '+T(STATE_LABEL[c.state]||c.state)])],ui.id||'');picker.setAttribute('aria-label',T('Modifiche del progetto'));picker.onchange=()=>{if(picker.value)load(picker.value).catch(showError);else{ui.id=null;ui.change=null;ui.events=[];ui.next=0;ui.view=null;render();}};
    head.append(node('div','actions',picker));
    box.replaceChildren(head,stepper(change),node('div','change-grid',mapColumn(change),stepColumn(change,active),agentColumn(change)));
  }

  function stepper(change){
    const now=stepOf(change),index=STEPS.findIndex(([key])=>key===now),done=change&&change.state==='integrated';
    const ol=node('ol','change-stepper');ol.setAttribute('aria-label',T('Passi della modifica'));
    STEPS.forEach(([key,label],i)=>{
      const status=done||i<index?'done':i===index?(change&&ACTIVE.has(change.state)?'active':'current'):'todo';
      const gate=key==='plan'||key==='review'?' ✅':'';
      const b=node('button','change-step '+status,node('b',null,String(i+1).padStart(2,'0')),node('span',null,(label.startsWith('change.')?K(label):T(label))+gate));b.type='button';
      b.setAttribute('aria-current',i===index?'step':'false');b.disabled=status==='todo'||key==='analyze';b.onclick=()=>{ui.view=key;render();};
      if((ui.view||now)===key)b.classList.add('shown');ol.append(node('li',null,b));
    });
    return ol;
  }

  /* map: change / affected / tests / not visible to the graph */
  function mapColumn(change){
    const col=node('section','change-column change-map');col.setAttribute('aria-label',T('Mappa dell’impatto'));
    col.append(node('h2',null,T('Mappa dell’impatto')));
    const legend=node('div','change-legend');for(const [kind,label] of Object.entries(KIND_LABEL))legend.append(node('span','change-chip '+kind,T(label)));
    col.append(legend);
    const plan=change&&change.plan;
    if(!plan){col.append(node('p','empty',T('La mappa compare quando l’agente ha preparato il piano.')));if(ui.symbol)col.append(node('p','hint',T('Simbolo selezionato')+': '+where(ui.symbol)));return col;}
    const agent=plan.agent||{},graph=plan.graph||{},files=new Map();
    const add=(kind,item)=>{const path=item.path||item.file||(item.symbol&&item.symbol.file)||'?';if(!files.has(path))files.set(path,[]);files.get(path).push([kind,item]);};
    for(const item of agent.touch||[])add('touch',item);
    for(const item of [...(agent.affected||[]),...(graph.affected||[])])add('affected',item.symbol&&typeof item.symbol==='object'?{...item.symbol,path:item.symbol.file,why:item.why}:item);
    for(const item of [...(agent.tests||[]),...(graph.tests||[])])add('test',item.symbol&&typeof item.symbol==='object'?{...item.symbol,path:item.symbol.file}:{...item,symbol:item.symbol||item.name});
    const order={touch:0,affected:1,test:2};
    for(const [path,items] of [...files.entries()].sort((a,b)=>Math.min(...a[1].map(i=>order[i[0]]))-Math.min(...b[1].map(i=>order[i[0]]))||a[0].localeCompare(b[0])).slice(0,80)){
      const card=node('div','change-file',node('strong',null,path));const seen=new Set();
      for(const [kind,item] of items){
        const label=(item.symbol||item.name||'')+(item.line?':'+item.line:'');const key=kind+label;if(seen.has(key))continue;seen.add(key);
        const chip=node('button','change-chip '+kind,label||T('file intero'));chip.type='button';chip.title=[T(KIND_LABEL[kind]),item.why,item.confidence].filter(Boolean).join(' · ');
        chip.onclick=()=>{ui.symbol={path,line:item.line,symbol:item.symbol||item.name};render();};
        if(ui.symbol&&ui.symbol.path===path&&ui.symbol.symbol===(item.symbol||item.name)&&ui.symbol.line===item.line)chip.setAttribute('aria-pressed','true');
        card.append(chip);
      }
      col.append(card);
    }
    if(files.size>80)col.append(node('p','warning',T('Mappa limitata a 80 file.')));
    const hidden=[...(plan.checked?.unresolved||[]).map(item=>T('Non risolto nell’indice')+': '+(typeof item==='string'?item:where(item))),...(graph.limits||[]).map(item=>typeof item==='string'?item:JSON.stringify(item)),...(agent.unknowns||[]).map(item=>T('Da chiarire')+': '+item)];
    if(hidden.length){col.append(node('h3',null,T('Non visibile al grafo')),list(hidden,text=>node('li','change-chip-row',node('span','change-chip hidden','?'),' ',clip(text,300))));}
    if(ui.symbol){const open=button(T('Apri nel grafo'),()=>openInGraph(ui.symbol));col.append(node('p','hint',T('Simbolo selezionato')+': '+where(ui.symbol)),open);}
    return col;
  }
  async function openInGraph(symbol){
    const result=await api('/api/symbols?'+new URLSearchParams({generation:state.generation,area:'.',q:symbol.symbol||symbol.path,offset:'0'}));
    const match=result.data.items.find(item=>item.file===symbol.path&&(!symbol.line||item.line===symbol.line))||result.data.items.find(item=>item.file===symbol.path);
    if(!match)throw new Error(T('Simbolo non trovato nell’indice attuale: aggiorna l’analisi.'));
    await selectSymbol(match.id);
  }

  function stepColumn(change,active){
    const col=node('section','change-column change-step-panel');col.setAttribute('aria-live','polite');
    const view=ui.view||stepOf(change);
    if(change&&change.error)col.append(node('p','warning',T('Errore')+' · '+(change.error.code||'')+' · '+T(change.error.message||'')));
    if(active)col.append(node('p','change-running',node('span','change-pulse'),' ',T(STATE_LABEL[change.state]||change.state)+'…'),button(T('Interrompi il passo'),act('cancel')));
    if(!change){col.append(...createForm());return col;}
    const panel={understand:understandPanel,plan:planPanel,edit:editPanel,verify:verifyPanel,review:reviewPanel}[view]||understandPanel;
    col.append(...panel(change,active));
    if(!TERMINAL.has(change.state)&&!active)col.append(node('div','change-footer',button(T('Scarta la modifica'),act('discard'),'change-danger')));
    if(change.state==='discarded')col.append(node('p','warning',T('Modifica scartata: la copia isolata e il suo branch sono stati rimossi; l’archivio resta.')));
    if(ui.busy)for(const b of col.querySelectorAll('button'))b.disabled=true;
    return col;
  }

  function createForm(){
    const intent=draft(textarea(5),'intent');const usable=(ui.agents||[]).map(c=>[c.client,(c.client==='codex'?'Codex':'Claude')+(c.available?'':' · '+T('non disponibile')),!c.available]);
    const author=draft(select(usable.length?usable:[['codex','Codex'],['claude','Claude']]),'author',ui.client||'codex');
    const reviewer=draft(select(usable.length?usable:[['codex','Codex'],['claude','Claude']]),'reviewer',(ui.drafts[slot('author')]||ui.client||'codex')==='codex'?'claude':'codex');
    author.onchange=()=>{ui.drafts[slot('author')]=author.value;if(reviewer.value===author.value){reviewer.value=author.value==='codex'?'claude':'codex';ui.drafts[slot('reviewer')]=reviewer.value;}};
    const budget=draft(money(),'budget');
    const create=button(T('Crea la modifica'),async()=>{
      if(!intent.value.trim())throw new Error(T('Descrivi la modifica.'));
      const limit=cap(budget);
      if(!ui.consent){await askConsent();if(!ui.consent)return;}
      const result=await post('/api/changes',{intent:intent.value,author:author.value,reviewer:reviewer.value,...limit});
      delete ui.drafts[slot('intent')];ui.changes.unshift({id:result.data.id,intent:result.data.intent,state:result.data.state});await load(result.data.id);schedule();
    },'primary');
    return [node('h2',null,T('Capisci e descrivi')),node('p','hint',T('Fai domande all’agente nel pannello a destra, poi descrivi la modifica. La modifica lavora in una copia isolata del progetto, mai nel tuo checkout.')),
      field(T('Cosa vuoi cambiare'),intent,'change-intent'),node('div','change-row',field(T('Autore (scrive)'),author,'change-author'),field(T('Lettore (rilegge)'),reviewer,'change-reviewer')),
      node('p','hint',T('Il lettore dovrebbe essere l’altro modello: Codex scrive e Claude rilegge, o viceversa.')),
      field(T('Tetto di spesa per Claude (USD, facoltativo)'),budget,'change-budget'),node('p','hint',T('Vale per ogni esecuzione di Claude in questa modifica. Codex non ha un tetto di spesa: conta sulla quota del suo account.')),create];
  }

  function understandPanel(change,active){
    return [node('h2',null,T('Capisci')),node('p','hint',T('Chiedi all’agente come funziona il codice coinvolto; seleziona un simbolo nella mappa per fare domande mirate. Quando sei pronto, fai preparare il piano.')),
      node('p',null,node('strong',null,T('Richiesta')+': '),change.intent),
      ...(change.state==='draft'&&!active?[button(T('Prepara il piano'),act('plan'),'primary')]:[])];
  }

  function planPanel(change,active){
    const out=[node('h2',null,T('Pianifica'))];const plan=change.plan;
    if(!plan){out.push(node('p','empty',active?T('L’agente sta leggendo il codice con code-graph…'):T('Nessun piano ancora.')));return out;}
    const agent=plan.agent||{};
    out.push(node('p',null,agent.summary||''));
    const editable=change.state==='planned'&&!active;
    const proposed=[...new Set((agent.touch||[]).map(item=>item.path).filter(Boolean))];
    const paths=proposed.filter(editablePath),refused=proposed.filter(path=>!editablePath(path));
    const touchKey=slot('touch'),chosen=new Set(ui.drafts[touchKey]||paths);
    const perimeter=node('fieldset','change-perimeter',node('legend',null,T('Perimetro: file che l’agente può cambiare')));
    for(const path of paths){const box=document.createElement('input');box.type='checkbox';box.checked=chosen.has(path);box.disabled=!editable;box.id='touch-'+path;box.value=path;box.onchange=()=>{const set=new Set(ui.drafts[touchKey]||paths);if(box.checked)set.add(path);else set.delete(path);ui.drafts[touchKey]=[...set];};perimeter.append(node('label','change-check',box,' ',path));}
    if(!paths.length)perimeter.append(node('p','empty',T('Il piano non indica file da cambiare.')));
    out.push(perimeter);
    if(refused.length)out.push(node('div','warning',node('strong',null,T('Percorsi non validi, esclusi dal perimetro')),list(refused,path=>node('li',null,node('code',null,path)))));
    out.push(node('h3',null,T('Cosa cambia')),list(agent.touch,item=>node('li',null,node('code',null,where(item)),' — ',item.why||'')));
    out.push(node('h3',null,T('Rischi')),list(agent.risks,text=>node('li',null,text),T('Nessun rischio indicato.')));
    if((agent.out_of_scope||[]).length)out.push(node('h3',null,T('Fuori perimetro')),list(agent.out_of_scope,text=>node('li',null,text)));
    const approved=change.approved;
    if(approved){out.push(node('h3',null,T('Approvato')),node('pre','source','argv: '+JSON.stringify(approved.test_argv)+(approved.no_tests_reason?'\n'+T('Senza test')+': '+approved.no_tests_reason:'')));}
    if(!editable){if(change.state==='planned'&&active)out.push(node('p','hint',T('Attendi la fine del passo.')));return out;}
    const argv=draft(textarea(3),'argv',(agent.test_command||[]).filter(Boolean).join('\n'));
    const none=document.createElement('input');none.type='checkbox';none.id='change-no-tests';none.checked=!!ui.drafts[slot('none')];none.onchange=()=>{ui.drafts[slot('none')]=none.checked;render();};
    const reason=draft(textarea(2),'reason');const scope=draft(textarea(2),'scope',(agent.out_of_scope||[]).join('\n'));
    out.push(node('h3',null,T('Test da eseguire prima e dopo')),node('p','hint',T('Un argomento per riga, senza shell. Li esegue solo code-graph, nella copia isolata, con un tempo massimo.')),
      field(T('Comando dei test (argv)'),argv,'change-argv'),node('label','change-check',none,' ',T('Nessun test per questa modifica')));
    if(none.checked)out.push(field(T('Perché non servono test'),reason,'change-no-tests-reason'));
    out.push(field(T('Fuori perimetro (uno per riga)'),scope,'change-scope'));
    const lines=text=>text.split('\n').map(s=>s.trim()).filter(Boolean);
    out.push(node('div','change-actions',
      button('✅ '+T('Approva il piano'),()=>act('approve',{test_argv:none.checked?[]:lines(argv.value),no_tests_reason:none.checked?reason.value:undefined,touch:[...perimeter.querySelectorAll('input[type=checkbox]:checked')].map(box=>box.value),out_of_scope:lines(scope.value)})(),'primary'),
      button(T('Rifai il piano'),act('plan'))));
    return out;
  }

  function editPanel(change,active){
    const out=[node('h2',null,T('Modifica'))];
    out.push(node('p','hint',T('L’autore lavora solo nella copia isolata e solo nel perimetro approvato. Prima della modifica code-graph esegue i test approvati.')));
    if(change.worktree)out.push(node('p',null,node('strong',null,T('Copia isolata')+': '),node('code',null,change.worktree)));
    const edit=change.edit;
    if(edit){out.push(node('h3',null,T('File cambiati')),list(edit.files,item=>node('li',null,node('span','tag',item.change),' ',node('code',null,item.path)),T('Nessun file cambiato.')));
      const text=edit.result&&(edit.result.text||edit.result.summary);if(text)out.push(node('pre','source',clip(text,4000)));}
    if(change.state==='approved'&&!active)out.push(button(T('Avvia la modifica'),act('edit'),'primary'));
    if(change.state==='edited'&&!active)out.push(button(K('change.verify'),act('verify'),'primary'));
    return out;
  }

  function testRecord(record){return record?[TEST_STATUS[record.status]?T(TEST_STATUS[record.status]):String(record.status||'—'),record.summary&&record.summary.total!=null?record.summary.total:'—',record.summary&&record.summary.failed!=null?record.summary.failed:'—',record.summary&&record.summary.errors!=null?record.summary.errors:'—',record.duration_s!=null?Number(record.duration_s).toFixed(1)+' s':'—']:['—','—','—','—','—'];}
  function verifyPanel(change,active){
    const out=[node('h2',null,K('change.verify'))];const tests=change.tests||{},verify=change.verify;
    const table=node('table','change-table');table.append(node('thead',null,node('tr',null,node('th',null,''),node('th',null,T('Esito')),node('th',null,T('Totali')),node('th',null,T('Falliti')),node('th',null,T('Errori')),node('th',null,T('Durata')))));
    const body=node('tbody');for(const [label,record] of [[T('Prima'),tests.baseline],[T('Dopo'),tests.after]])body.append(node('tr',null,node('th',null,label),...testRecord(record).map(value=>node('td',null,value))));table.append(body);out.push(table);
    if(change.approved)out.push(node('p','hint','argv: '+JSON.stringify(change.approved.test_argv)));
    if(verify){
      const t=verify.tests||{};
      for(const [key,label,cls] of [['newly_failing',L('Nuovi test falliti'),'bad'],['fixed',L('Test riparati'),'good'],['still_failing',L('Ancora falliti'),'']])if((t[key]||[]).length)out.push(node('h3',cls?'change-'+cls:null,T(label)+' · '+t[key].length),list(t[key],name=>node('li',null,node('code',null,name))));
      const p=verify.perimeter||{};
      out.push(node('h3',null,T('Perimetro')),list([...(p.outside||[]).map(path=>[T('Fuori perimetro'),path]),...(p.planned_untouched||[]).map(path=>[T('Previsto ma non toccato'),path])],([label,path])=>node('li',null,node('span','tag',label),' ',node('code',null,path)),T('Tutti i file cambiati sono nel perimetro.')));
      out.push(verify.ok?node('p','change-ok',T('Nessun blocco: si può chiedere la rilettura.')):node('div','warning',node('strong',null,T('Blocchi')),list(verify.blocking,text=>node('li',null,text))));
    }
    if(change.state==='edited'&&!active)out.push(button(K('change.verify'),act('verify'),'primary'));
    if(change.state==='verified'&&!active)out.push(button(T('Chiedi la rilettura'),act('review',reviewExtra(change)),'primary'));
    return out;
  }

  function reviewExtra(change){return change.author===change.reviewer?{same_client_reason:ui.drafts[slot('same')]||''}:{};}
  function reviewPanel(change,active){
    const out=[node('h2',null,T('Rileggi e integra'))];const review=change.review,verdictData=review&&(review.result||review);
    if(change.state==='verified'&&!active){
      if(change.author===change.reviewer)out.push(field(T('Perché rilegge lo stesso modello'),draft(textarea(2),'same'),'change-same-reason'));
      out.push(button(T('Chiedi la rilettura'),()=>act('review',reviewExtra(change))(),'primary'));
    }
    if(verdictData&&verdictData.verdict){
      const verdict=verdictData.verdict;out.push(node('p','change-verdict '+verdict,T('Verdetto del lettore')+': ',node('strong',null,VERDICT[verdict]?T(VERDICT[verdict]):verdict)),node('p',null,verdictData.summary||''));
      out.push(list(verdictData.findings,item=>node('li',null,node('span','tag '+(item.severity==='grave'?'change-grave':''),(item.severity==='grave'?T('grave'):T('minore'))),' ',node('code',null,where(item)),' ',item.text||''),T('Nessun rilievo.')));
    }
    if(change.integration){out.push(node('h3',null,T('Integrazione')),node('pre','source',typeof change.integration==='string'?change.integration:JSON.stringify(change.integration,null,2)));}
    if(['verified','reviewed'].includes(change.state)&&!active&&(change.round||0)<3){
      const feedback=draft(textarea(3),'feedback');
      out.push(node('h3',null,T('Rimanda all’autore')),field(T('Cosa correggere'),feedback,'change-feedback'),button(T('Nuovo giro di modifica'),()=>act('edit',{feedback:feedback.value})()));
    }
    if(change.state==='reviewed'&&!active){
      const blocked=(change.verify&&(change.verify.blocking||[]).length)||(verdictData&&verdictData.verdict==='reject');
      const mode=draft(select([['branch',T('Lascia la modifica sul suo branch (predefinito)')],['merge',T('Unisci nel tuo checkout (solo se pulito, stesso branch, base contenuta)')]]),'mode','branch');
      const force=draft(textarea(2),'force');
      out.push(node('h3',null,T('Integra')),field(T('Come integrare'),mode,'change-mode'));
      if(blocked)out.push(field(T('Motivo per integrare nonostante il verdetto o i blocchi'),force,'change-force'),node('p','hint',T('Il motivo resta nello storico. Non salta i controlli Git: checkout pulito, stesso branch e commit di base restano obbligatori.')));
      out.push(node('div','change-actions',button('✅ '+T('Integra'),()=>act('integrate',{mode:mode.value,...(blocked?{force_reason:force.value}:{})})(),'primary')));
    }
    if(change.state==='integrated')out.push(node('p','change-ok',T('Modifica integrata. Nessun push: pubblicarla resta una tua scelta.')));
    return out;
  }

  /* ---------- agent panel ---------- */
  function agentColumn(change){
    const col=node('section','change-column change-agent');col.setAttribute('aria-label',T('Agente'));
    col.append(node('h2',null,T('Agente')));
    const clients=node('div','change-clients');clients.setAttribute('role','radiogroup');clients.setAttribute('aria-label',T('Scegli il client'));
    for(const c of ui.agents||[]){const radio=document.createElement('input');radio.type='radio';radio.name='change-client';radio.value=c.client;radio.id='change-client-'+c.client;radio.checked=ui.client===c.client;radio.disabled=!c.available;radio.onchange=()=>{ui.client=c.client;};
      clients.append(node('label','change-client'+(c.available?'':' off'),radio,' ',c.client==='codex'?'Codex':'Claude',node('small',null,c.available?(c.version||''):T('non disponibile'))));}
    if(!(ui.agents||[]).length)clients.append(node('p','hint',T('Cerco i client installati…')));
    col.append(clients);
    col.append(node('p','change-consent-state',ui.consent?T('Consenso dato per questa sessione.'):T('Nessun consenso: nessun agente può partire.'),' ',ui.consent?button(T('Revoca'),revoke):button(T('Dai il consenso…'),askConsent)));
    const question=draft(textarea(3),'question','',true);
    const askCap=draft(money(),'ask-budget','',true);
    const ask=button(T('Chiedi'),async()=>{
      if(!question.value.trim())throw new Error(T('Scrivi una domanda.'));
      const limit=ui.client==='claude'?cap(askCap):{};
      if(!ui.consent){await askConsent();if(!ui.consent)return;}
      const result=await post('/api/ask',{client:ui.client,question:question.value,...(ui.symbol?{symbol:where(ui.symbol)}:{}),...limit});
      ui.asks.unshift({run:result.run,client:result.client,question:question.value,status:result.status,events:[],next:0,result:null});ui.asks=ui.asks.slice(0,6);ui.drafts.question='';render();pollAsks();
    },'primary');
    col.append(node('h3',null,T('Domanda in sola lettura')),ui.symbol?node('p','hint',T('Sul simbolo')+': '+where(ui.symbol)):node('p','hint',T('Seleziona un simbolo nella mappa per una domanda mirata.')),field(T('La tua domanda'),question,'change-question'),field(T('Tetto per le domande a Claude (USD, facoltativo)'),askCap,'change-ask-budget'),ask);
    const asks=node('div','change-asks');for(const run of ui.asks)asks.append(askBlock(run));col.append(asks);
    col.append(node('h3',null,T('Eventi in diretta')));
    const feed=node('ol','change-events');feed.id='change-events';feed.setAttribute('aria-live','polite');col.append(feed);fillEvents(feed);
    return col;
  }
  function eventLine(event){
    const inner=event.kind==='agent'&&event.data&&event.data.kind?event.data:event;
    const label=event.kind==='agent'?(inner.kind||'agent'):event.kind;
    const text=inner.text||(inner.data&&(inner.data.name||inner.data.command||inner.data.path))||'';
    return node('li','change-event '+label,node('time',null,clock(event.t)),node('span','tag',label),' ',clip(text));
  }
  function fillEvents(feed){feed.replaceChildren(...ui.events.slice(-200).map(eventLine));if(!ui.events.length)feed.append(node('li','empty',ui.change?T('Nessun evento ancora.'):T('Gli eventi compaiono quando parte un passo.')));feed.scrollTop=feed.scrollHeight;}
  function renderEvents(){const feed=$('change-events');if(feed)fillEvents(feed);}
  function askBlock(run){
    const box=node('details','change-ask');box.open=run===ui.asks[0];
    box.append(node('summary',null,(run.client==='codex'?'Codex':'Claude')+' · '+(run.status==='done'?T('completata'):['running','starting'].includes(run.status)?T('in corso'):run.status)+' · '+clip(run.question,80)));
    if(run.result&&(run.result.text||run.result.summary))box.append(node('pre','source',clip(run.result.text||run.result.summary,8000)));
    box.append(node('ol','change-events',...run.events.slice(-60).map(eventLine)));
    return box;
  }
  async function pollAsks(){
    clearTimeout(ui.askTimer);const open=ui.asks.filter(run=>['starting','running'].includes(run.status));if(!open.length||!current())return;
    try{for(const run of open){const result=await api('/api/ask/events?'+new URLSearchParams({run:run.run,since:String(run.next)}));run.events.push(...result.events);run.next=result.events.length?result.events[result.events.length-1].seq:run.next;run.status=result.status;run.result=result.result;}
      if(current()){const asks=document.querySelector('#change-flow .change-asks');if(asks)asks.replaceChildren(...ui.asks.map(askBlock));}}
    catch(error){showError(error);}
    ui.askTimer=setTimeout(pollAsks,1000);
  }

  return {open};
};
/* Exposed for the shared path-rule table in tests/change_browser.py. */
window.createCgChange.editable=editablePath;
