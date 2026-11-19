# Local search: FTS5, BM25 and graph RRF

Development contract, based on commit `6978d20`. This increment retrieves code evidence;
it does not call an LLM, execute project code, introduce embeddings or enable new languages.

## Interface

`cg --root PROJECT search QUERY [--mode hybrid|lexical] [--limit 10]
[--depth 2] [--min-conf medium] [--max-chars 9000] [--json]`

The CLI and the read-only MCP tool `cg_search` return the same `codegraph-search/2`
JSON. `--json` is accepted for consistency; CLI output is JSON by default.
`max_chars` accepts 64–16000 characters in both interfaces, leaving room for the MCP
wire envelope and escaped JSON while keeping `content.text` parseable.
Queries are plain words, not raw FTS syntax. At most 512 characters and 16 distinct
terms, combined with OR so a result may match a subset; quoted/punctuated input cannot inject operators or SQL. Identifier components
are split at underscores and ASCII camel-case boundaries; Unicode words are retained.
This is lexical retrieval plus graph expansion, not semantic translation of a question.
The [agent workflow](AGENT_SEARCH.md) supplies query-formulation guidance to the selected
Codex/Claude client and passes verification feedback to subsequent edits.

## Dashboard and HTTP

The top-bar selector retains symbol/path lookup as its default. **Text + graph**
and **Text only** use the existing hybrid and lexical engine across the whole
selected project, regardless of the folder previously displayed. Enter or **Show
search evidence** opens the full result view: declaration windows and SHA-256,
BM25/RRF ranks, graph witness callsites and static confidence, revision and explicit
omissions. The ranking does not assign confidence or imply translation of a question.

The authenticated readonly `GET /api/search` accepts `generation`, `q`, `mode`,
`confidence`, `limit`, `depth` and `max_chars`, with the CLI/MCP ranges and defaults.
Its `{generation,data}` envelope contains the unchanged `codegraph-search/2` object;
the Unicode budget applies to that object, not HTTP encoding or its envelope. The
dashboard requests up to ten results at depth two within 16000 characters. A click
on a result uses the existing verified symbol/source/graph endpoint.

Invalid input returns HTTP400; obsolete configuration, source, engine or session
returns HTTP409. An index without ready FTS returns HTTP503 with
`code=search_unavailable`; the user can explicitly choose symbol lookup. No query
creates or migrates the index. New input, mode, project and view changes prevent
older responses from replacing the current results. The dashboard does not start
a model, grant agent consent or execute analyzed sources.

## Query widening (1.9)

Exact terms are searched first, exactly as before. If they return fewer than 50
candidates, a second FTS5 query widens the request and its new results are appended
after every exact match, marked `lexical.match = "expanded"`:

- **Word roots.** One common English suffix is removed (`cancelling`, `cancellation` →
  `cancel*`; `parsing` → `pars*`) and the root of at least four characters is searched
  as a prefix. A shorter prefix only widens the match.
- **Programming synonyms.** A fixed list of groups (for example *cancel / abort / stop /
  terminate / kill*, *config / configuration / settings / options*, *release / free /
  unlock*, *lock / mutex*) adds the other members of a group containing a term or its root.

In hybrid mode widened matches never take part in fusion while exact matches exist: graph seeds and
RRF use the exact matches only, exactly as without widening, and widened matches that are not graph
neighbours follow the fused list. Without exact matches, the widened matches take their place.
`query.expansions` lists every added term, its kind (`prefix` or `synonym`) and the query
term it came from; `retrieval.exact_candidates` counts the exact matches. Widening is
deterministic, local and model-free; it does not translate questions or use embeddings.
Development cases: `benchmarks/search-paraphrase.json` (results before and after in
`search-paraphrase-before.json` and `search-paraphrase-results.json`).

## Compact result format (`codegraph-search/2`)

Each item's `source` holds `start_line`, `text` (the window's lines joined with `\n`),
`truncated`, `source_sha256` and, only when it carries information, `window`
(`continuation`, `cuts`, `reason`, `focus`). The window limits and column unit shared by
every item are given once in `retrieval.source_window`. The file path is in `symbol.file`.
Graph witness steps keep file, line, direction, relation, confidence and the two symbol
ids; per-step Git blob ids were removed. BM25 scores keep four significant digits. With
the same evidence per item, the default budget is 9000 characters instead of 12000.

## Index and source boundary

`index` maintains a derived FTS5 table inside `.code-graph/graph.db`, in the same
transaction as symbols. Each non-module symbol contributes its qualified name, path,
signature and at most the first 2000 source characters of its declaration. Body-prefix
coverage is reported; matches beyond that prefix are not promised. Documents/configs
are not added to the search corpus in this increment.

Changes, deletions and exclusions must remove old search rows; a failed index must
retain the previous graph and search snapshot. An unchanged index does not rebuild FTS.
Changing engine code or enabling FTS5 on a previously unsupported SQLite rebuilds the
derived entries. Without compiled-in FTS5, ordinary indexing remains available and
search reports its unavailable state; no extension is downloaded or loaded.

Search reads the index through the existing read-only project interface. It checks
the engine fingerprint, current configuration and hashes of consulted source files,
including graph witnesses. Modified/deleted/symlinked evidence is rejected until
reindexing. New files require `index`, as with other graph queries.

## Retrieval and fusion

1. FTS5 `bm25()` orders up to 50 lexical candidates. Column weights: name 8, path 2,
   signature 3, body 1. Lower SQLite BM25 scores rank first; scores are not confidence.
2. Hybrid mode expands the top three lexical seeds in both directions over resolved
   calls/references, respecting the confidence threshold. Default depth 2; allowed 1–3.
   Limits: 500 visited nodes, 100 nodes per frontier and 1000 processed edge rows per
   query, shared across seeds and depths. Each batch may read one extra lookahead row
   to detect truncation; `edges_scanned` reports the processed rows.
   Reaching a limit is reported, never described as a complete graph exploration.
3. Graph candidates rank by shortest distance, number of supporting seeds, strongest
   witness confidence, then source identity. Seed nodes themselves are excluded from
   the graph ranking to avoid counting their lexical position twice. Keep at most
   100 graph candidates, with an explicit omission marker.
4. RRF combines one-based positions: `sum(1 / (60 + rank))`. Each symbol contributes
   at most once per channel. Equal scores use path, line and qualified name as tie-breaks.
   Lexical mode skips graph traversal and fusion, preserving a measurable baseline.

Results distinguish lexical rank/BM25, graph rank/witness and RRF score. Every graph
witness records the original edge direction, call/reference relation, source location
and static confidence. Rank scores never change the graph's confidence or create edges.
Bounded source windows reuse the existing source reader and report hashes, cuts and
continuations. The complete serialized output, including metadata and newline, respects
`max_chars`; an insufficient metadata budget returns `budget_insufficient`.

## Verification

Required: positive/negative retrieval cases, camel-case/Unicode/operator input, stable
ties, lexical-vs-hybrid comparison, call/reference witnesses and confidence filtering;
incremental update/delete/exclude/full equivalence and rollback; stale/symlink evidence;
FTS5-unavailable behavior; budget and truncation; real CLI/MCP read-only parity.

Keep the baseline test suite and call/reference oracle green. Measure retrieval quality
on explicit expected-symbol cases, retaining regressions as well as gains; do not infer
real-task time/token improvements from a synthetic retrieval experiment.

References checked 11 November 2026: [SQLite FTS5](https://www.sqlite.org/fts5.html),
[BM25 and column weights](https://www.sqlite.org/fts5.html#the_bm25_function), and
[Cormack et al., RRF, SIGIR 2009](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf).
