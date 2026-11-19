# Confronto dopo la prova HotSkills1.5

Il confronto indipendente usa **gli stessi input per entrambi i motori**:846file,7.274.052byte,804candidati sorgente e42configurazioni. Entrambi indicizzano803file,7146definizioni e7949nodi. Il progetto nel frattempo differisce dal report utente originale da800file: le variazioni globali non vengono attribuite alla skill.

Baseline Git2f501fd (1.5.0) e moduliCLI1.5.1 finali congelati con hash. Tre ripetizioni per query, processo Python nuovo, stesso ambiente offline macOS arm64/Python3.12.8/tree-sitter0.25.2/language-pack0.13.0/SQLite3.54.1. Ogni fonte è ricontrollata dopo le query. I cambi successivi della guida e dei testi UI sono fuori da questi taskCLI; il filtro catalogo è verificato in un addendum separato.

| Task e budget |1.5.0|1.5.1|
|---|---|---|
|Navigation12000|budget insufficiente|11996caratteri,21/48collegamenti|
|Navigation24000|23954caratteri,1/48collegamenti|23990caratteri,48/48collegamenti|
|serve_export12000|11921caratteri,2/105collegamenti|11998caratteri,21/105collegamenti|
|Catalogo predefinito|1scheda compilata|24/170candidati, troncamento esplicito|
|Ricerca pipeline|0schede|11/11candidati, filtro prima del cap|

La rigaJS è sempre16556caratteri. La sezione snippet del caso24000 passa da17537 a1276caratteri; il resto del budget viene riusato per altre informazioni. Il pacchetto complessivo **non è più corto** e non si tratta di un confronto a evidenze identiche. Il nuovo ritaglio mostra320caratteri con coordinate, hash, troncamento e continuazione.

La componente collegata alla radice serve_export passa da3a20nodi: il nuovo pacchetto evita41metadati di nodi senza percorso conservato. Mantiene21link e1/41test con3estratti; molte callsite restano solo riferimenti da approfondire. Il collegamento verso discovery.populate è ora presente con posizione, senza il suo estratto. A24000Navigation conserva48coppie ma49/256occorrenze: non significa sorgente o posizioni completi. Gli oracoli e gli esiti negativi pubblicabili sono nella [ricevuta dei risultati](refinements-1.5.1.json); i report di revisione interni non sono inclusi nella distribuzione.

Le medianeCLI in secondi sono0.526→0.422 (Navigation12000),0.570→0.608 (Navigation24000) e1.942→0.306 (serve_export12000). Il secondo caso peggiora leggermente e conserva molti più metadati. Catalogo0.146→0.257s con1contro24schede; index singolo8.707→12.226s non è un campione ripetuto sufficiente a classificare una regressione. Nessun modello, tokenizer o test applicativo è stato eseguito: **token effettivi, costo, errori e tempi di compiti AI completi non sono misurati**.

Artefatti senza testi sorgente o firme applicative:

- [Payload, oracoli, hash e tempi](refinements-1.5.1.json): baseline, primoWIP e current_final separati; usare current_final per la tabella.
- [Manifest comune degli input](refinements-1.5.1-inputs.json).
- [Addendum del catalogo corretto](refinements-1.5.1-catalog.json).

Per ripetere con un checkout locale di HotSkills e questo cloneGit, usando l'ambiente della skill:

```bash
.venv/bin/python -I benchmarks/refinements-capture.py --project /percorso/hotskiils
# Usa la directory BASE stampata; la cattura rimane fuori dai repository.
.venv/bin/python -I benchmarks/refinements-measure.py --capture /directory/BASE --repeats 3
.venv/bin/python -I benchmarks/refinements-evaluate.py --capture /directory/BASE
```

I runner derivano da quelli eseguiti dal reviewer; sono parametrizzati solo per percorsi e destinazioni. Il benchmark è specifico dei tre quesiti su HotSkills, non un test generale per ogni progetto. `common-input` e `private-raw` contengono codice privato: restano nella directory temporanea. Pubblicare soltanto le metriche/manifest senza sorgente dopo verifica. Il contenuto reale corrente può differire dal manifest storico; un nuovo risultato è una nuova cattura, non la stessa prova.
