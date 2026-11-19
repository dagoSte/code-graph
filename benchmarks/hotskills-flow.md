# Flusso HotSkills: evidenze e dichiarazioni

Verifica statica del 4 novembre 2026; le posizioni vanno ricontrollate se i file cambiano. Non è una traccia d'esecuzione. Il manifest di esempio conserva gli hash esatti dei file letti; se divergono il tool segnala stale_declaration. Non è installato nel progetto applicativo.

| Passaggio | Evidenza nel sorgente | Conclusione / limite |
|---|---|---|
| Produzione catalogo | pipeline/hotskills.py:1567, _write_json su EXPORT/catalog.json | La destinazione dipende dalla configurazione EXPORT |
| Orchestrazione export | pipeline/integrate.py:284–288, catalog_json, export_all e servedb.serve_export | Il collegamento di chiamata Python qualificato è risolto high; i percorsi derivano da ambiente/config |
| Lettura del catalogo nel sito | site/build.php:19 passa export a Builder; site/src/Site.php:2238–2241 legge source/catalog.json | Confermata nel codice la natura del consumer; il percorso deploy concreto non è dedotto |
| Produzione DB MCP | pipeline/servedb.py:35 riceve out_path, :77 legge catalogo, :151 sostituisce l'output atomicamente | È un producer di serve.db nel contesto passato da integrate; non apriamo il DB |
| Lettura DB nel servizio | mcp/public/index.php:12 seleziona HOTSKILLS_SERVE_DB/var, mcp/src/Service.php:156 costruisce Catalog, mcp/src/Catalog.php:41 apre SQLite3 read-only | Collegamento fra processi dipendente da configurazione; non è un arco di chiamata pipeline→MCP |
| Comandi deploy | deploy/commands.json:5 e altre frequenze contengono site/build.php, export/output e placeholder | Sono dichiarazioni analizzate come dati, non comandi eseguiti dal tool |

Il manifest della prova dichiarava aree pipeline/sito/MCP e due risorse con prove da entrambi i lati. I percorsi {export}/catalog.json e {serve_db} restano intenzionalmente dinamici: il tool mostra relazioni manuali non verificate come percorsi certi. Per ottenere un candidato di percorso statico serve una dichiarazione del progetto con le radici effettive e prove aggiornate.

Questo esempio mostra il limite e il modo di approfondirlo; non trasforma stringhe omonime o variabili d'ambiente in una prova di identità fra risorse.
