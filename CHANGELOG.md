# Changelog

Recorded code-graph versions, newest first. Entries follow the repository's
version changes and review records. Version 1.1.0 describes the original snapshot
reviewed before the first commit; Git history starts with 1.1.1.

**1.9.0 — locate, readable answers, Django scale, a full guide and field fixes**

- **Two maps.** The technical graph stays available; a separate functional map starts from
  project-declared names such as Payment and links them to code with direct-call witnesses.
  CLI `flows`, MCP `cg_flows` (15 tools) and the dashboard share the same bounded overview;
  the header opens a visual usage guide with shortcuts to the dashboard views. Details, impact and proposed tests open on the selected feature.
- **Fresh working-tree reviews.** `review` refuses sources or engine inputs changed after
  indexing, including added and deleted source files, before attributing diff lines to symbols.
  Re-index first; staged reviews keep the separate Git-index snapshot behavior.
- **`locate`.** One command from a request, an issue or a stack trace to the places to
  change, with their source, callers and tests, within a character budget; frames are
  matched to indexed functions by path suffix and name. MCP `cg_locate`.
  Five candidates by default (`--max-chars 5000`, at most 25% more text than before);
  kebab-case names, paths and qualified names count as code terms; source URLs keep only
  their indexed path. On the 23 development cases the fix file is in the top 3 for 14
  (was 11) and the function in the top 5 for 9 of 19 (was 6); Go drops from 7/7 to 5/7.
- **Tests to run and readable answers.** `verify` and `review` propose ready commands for
  pytest, unittest (with the project's own virtualenv Python), Go, Jest/Vitest and PHPUnit
  (one file per run, filter anchored on the full test name), and print a short summary unless `--json` is given; `review --summary --json` is a compact
  answer for agents (`codegraph-review-summary/1`, also MCP `cg_review` with `summary`).
- **Django scale.** Python import resolution is indexed and `review --base` starts from a
  copy of the current index: on Django, full index 98.5 s to 17.6 s with identical facts,
  `review --base` from over 600 s to 40 s ([receipt](benchmarks/django-scale.json)).
- **Documentation and language.** A guide in `docs/guide` with real examples and a reference
  generated from the code; command line, engine messages and SKILL.md in English, while the
  Italian dashboard keeps Italian wording for engine texts. License FSL-1.1-MIT.
- **Agent study.** 23 historical bugs in Django, typescript-eslint and Prometheus: the same
  bugs were fixed with and without code-graph (1/23 as recorded, 4/23 after a disclosed
  test-preservation correction) and planning with the graph used 45% more Codex input
  tokens. All results are in `benchmarks/LIVE_RESULTS.md`.
- **PHP enums (field fix).** `check` and `review` no longer report the methods PHP 8.1 gives every
  enum: `cases()` on any enum, `from()` and `tryFrom()` only on a backed enum
  (`enum X: string` or `: int`), also through `self::` and `static::`. The backing type
  is read from the declaration's syntax tree, so attributes and a type on the next line
  count; a pure enum calling `from()` is still reported. A native PHP run checks the
  fixture. On the PHP 8.3 project where this was found, these methods were 60 of the
  62 `check` candidates; the other two are file cycles.
- **Long requests.** `locate` accepts up to 200,000 characters (was 20,000; a real 20,201-character
  issue was refused). Above 20,000 the text search uses the first 20,000 characters, stack frames are
  still found anywhere, and JSON declares `input_chars`, `analysed_chars` and `trimmed`; a 512-character
  query budget applies at every length, with `omitted_terms` declared.
- **45-bug localization bench.** Frozen and measured once on 45 other historical bugs, chosen by a rule
  fixed in advance: fix file first in 23 of 44 (main before: 10), among the first five in 30 (17), changed
  function among the first five in 16 of 41 (7). A review fix made after the freeze ranked files named in
  the request after the best text matches and lost 7 first-place hits; it was on main with the frozen
  numbers. 1.9.0 restores the evaluated order: 23, 28 and 30 of 44 at ranks 1, 3 and 5 and 18 of 41 for the
  function, re-measured after seeing the results (not blind). `locate_bench.py compare` regenerates the
  comparison from receipts whose hashes are pinned. See `benchmarks/locate-bench/RESULTS.md`.
- **`cg start` on ambiguous names.** When the example function's qualified name was shared by two
  declarations, `start` exited with status 2 and no output (found on typescript-eslint); the example is
  now skipped.
- **Tests without Git history.** The suite passes in a single-commit copy of the repository: the
  Italian engine-text map is checked against a committed fixture.

**1.8.0 — guided start, Go at SDK scale, and measured results on real code**

- **Guided start.** `cg start` indexes a project, says whether it is large enough for
  the graph to pay its way, picks a real function with callers and tests as an example
  and prints three recipes (impact and tests, find the code, review a change) with
  commands that run as printed; `--json` for agents, English or Italian. The dashboard
  overview opens with the same three questions, and the READMEs gain Start here and
  When it is worth it sections.
- **Go at SDK scale.** Import resolution indexes candidates by package instead of
  rescanning every file for each import: the full Go SDK (2.2 million lines) indexes in
  52 s instead of reaching the 600-second limit, with identical facts on the comparison
  subset. A regression test counts lookup work and covers the `_test.go` guard.
- **Real-repository pilot.** On four historical SymPy bugs selected by a rule fixed in
  advance, Codex with code-graph fixed 3 and without it 2, with both arms given the same
  literal text search; code-graph used 15–23% more input tokens per phase. A scoring
  defect in the test-retention check was found, corrected and re-scored offline; both
  scorings are published. See `benchmarks/LIVE_RESULTS.md`.
- **Second fixture pilot.** On four small tasks both arms succeeded in all eight paths;
  code-graph added 27% time and 35% input tokens, so no benefit is claimed at that size.
- **README for a public audience.** Hybrid search explained with diagrams, a worked
  example and a retrieval chart; a Benchmarks section with a scale chart built from
  committed receipts; fresh screenshots of the real dashboard; development leftovers
  and links to Italian-only contracts removed. `tools/readme_charts.py` regenerates the
  charts and `tests/test_readme.py` checks links, EN/IT parity and chart freshness.
- **Grammar investigation.** All 29 partial Dart SDK files trace to eight parser
  limitations, documented. Newer grammars were evaluated and rejected: the 1.25 language
  pack downloads parsers at runtime; the standalone Dart grammar, verified byte for byte
  against upstream, silently loses calls such as `leaf<()>()`. The 65 partial Go SDK
  files are classified by cause.
- **Smaller fixes.** The English dashboard no longer shows Italian labels for registered
  callbacks and per-language file counts; Codex `cache_write_input_tokens` is kept as its
  own usage field and never added to input tokens; the project-guide browser test opens
  the mobile menu before navigating.

**1.7.0 — language adapters, Go, Dart, Java, Swift, SQLite SQL and local search**

- Added dashboard lexical/hybrid search backed by the existing readonly retrieval
  engine, with source hashes, ranking/witness evidence, explicit fallback and stale
  response guards. Symbol lookup remains the default.
- Rendered SQLite resource observations and same-script candidates separately in
  Architecture, including source windows, file status, truncation and coverage limits.
- Added reproducible scale measurements on eight-language generated workloads and
  existing public SDK sources, with exact synthetic target checks, query timings and
  peak worker RSS. The million-line generated case completes; the larger Go SDK full
  index reaches its 600-second deadline. Dart SDK partial analysis remains explicit;
  see `benchmarks/SCALING.md` for all results and limitations.
- Ran the bounded paired live pilot with installed clients. Both refund edits pass
  frozen checks; Claude weekly quota prevents review and integration, leaving the
  comparison inconclusive. Retained all seven starts, cancelled/unstarted paths,
  native usage and tool failures. Fixed recognition of the observed weekly-limit
  message without rerunning the experiment; see `benchmarks/LIVE_RESULTS.md`.

- Refreshed the local dashboard with a dark navigation sidebar, top-bar symbol
  search, a purple graph palette and a closable source inspector. Mobile navigation
  supports keyboard focus and Escape; Cmd/Ctrl+K focuses search. Existing project
  data, actions and consent controls remain in use.

- Go retains concrete receiver types from direct outer-body `T{}` / `&T{}`
  variable initializers, for calls and method values. Declaration/RHS uses,
  implicit switch/select case blocks and nested bindings cannot borrow those hints;
  interface, alias and generic exclusions remain. Six native cases extend the matrix.
  Oversized call-head identities remain unresolved rather than selecting a different
  symbol through a clipped diagnostic name.
- Added bounded Dart `package:` bindings from an existing root JSON configuration,
  with URI-library ambiguity and relative-context guards. Ignored/invalid boundaries,
  config-only updates, root moves and stale readers are checked without Pub or SDK
  discovery; see `docs/DART_PACKAGES.md`. Four native observations extend the matrix.
- Added SQLite SQL source symbols and explicit resource observations. Architecture
  exposes bounded same-script name candidates with source hashes, separate from
  call edges; CTEs, schema/temp blockers and unsupported syntax remain visible.
  No user database or migration is executed; see `docs/SQL.md`.
- Added native in-memory SQLite authorizer regressions for CTE/alias reads, DML
  targets, views and parser boundaries, without changing call/reference oracles.
- Added a bounded Swift language-mode-5 subset: complete declarations, source owners,
  callsites and function references; file-local private functions/static methods with
  SDK-name, shadowing and overload guards. Imports/member contexts abstain. No Xcode
  or build execution is needed for indexing; see `docs/SWIFT.md`.
- Added eight recorded Apple Swift 6.4.2 native observations and an optional direct
  compiler rerun with an explicit SDK and isolated cache/output.
- Added the declared Java SE 17 subset: declarations, complete spans, explicit method
  references and unique static/private/final source bindings; overloads, virtual
  dispatch and JPMS cross-file resolution abstain. Indexing needs no JDK/build tool.
- Added ten OpenJDK 21.0.2 native cases compiled with --release 17 and processors off;
  recorded tests require no JDK. See `docs/JAVA.md` for scope and boundaries.

- Added the first Dart subset: standalone library declarations, relative imports with
  prefix/show/hide/privacy, explicit local static methods and separate function-value
  references. Part libraries and uncertain instance/extension/mixin dispatch abstain.
  Indexing requires no Dart/Flutter SDK or package downloads; see `docs/DART.md`.
- Added eight recorded Dart 3.6.2 native observations with an optional direct-VM rerun.
- Added the first Go analysis subset: declarations, package/import resolution and
  directly declared methods on explicit concrete receiver types, with dedicated call
  and reference resolution. Module/workspace inputs participate in freshness; uncertain
  dispatch/build variants abstain. Indexing requires no Go compiler or module downloads.
- Added Go test discovery candidates and eight recorded native Go 1.27.4 oracle cases
  (six resolved, two deliberate abstentions); see `docs/GO.md` for the coverage boundary.
- Connected search guidance to Codex/Claude planning, editing, review and dashboard
  questions. The host refreshes the worktree index before each change-flow agent start;
  later edits receive the preceding verification, test output and review findings.
  Existing approvals and round limits remain; the incomplete live pilot establishes
  no general reasoning-quality, time or token benefit.
- Added local `search` / read-only `cg_search`: FTS5/BM25 over symbol names, paths,
  signatures and bounded body prefixes, with optional bounded graph expansion and
  SQL reciprocal rank fusion. Lexical mode is retained for comparison; no model is called.
- Search cache updates share the graph transaction; source hash checks, explicit limits
  and JSON budgets preserve evidence boundaries. SQLite without FTS5 keeps static indexing.
- Extracted Python file loaders, PHP namespace lookup, JS/TS module resolution and
  call handlers into built-in language modules with a shared registry. Existing
  source extensions and CLI/MCP entry points are preserved.
- Added adapter loading/cache regressions and a GitHub test workflow for Linux/macOS
  with Python 3.12/3.13. Updated stale JS/TS limitations and security release notes.
- Documented the adapter boundaries, official language references and acceptance
  checks for the declared Go, Dart, Java, Swift and SQLite SQL subsets, plus
  LLM-assisted search. Compare legacy fixtures separately from new-language coverage.

**1.6.5 — API catalog and more precise references**

- Local API catalog: decorators, callback positions, dynamic calls and start files
  are described in data. `cg catalog check` inventories gaps; `cg catalog validate`
  validates the catalog. A project can extend it with `.code-graph-catalog.json`;
  invalid extensions are rejected whole with a warning, preserving the base catalog.
  Catalog changes trigger reindexing. See [the catalog contract](docs/CATALOG.md).
- Python decorators: proven aliases of pure decorators and simple local identity
  decorators no longer hide unused functions. Uncertain bindings remain conservative;
  an f-string in a decorator body is code, not an inert docstring. Shadowed module
  names no longer create unrelated callback references or dynamic `getattr` reach.
- Python entry points: FastAPI/APIRouter `head`, `options`, `trace`, `api_route` and
  `websocket_route`, plus Flask Blueprint route decorators, are recognized when the
  constructor and receiver bindings are proven. Local homonyms do not establish
  framework origin. Long `elif` chains no longer overflow the loader interpreter.
- JS/TS module bindings cover destructured `require`, `module.exports = class`,
  compiled calls such as `(0, mod.fn)()`, escaped names and writes through wrappers.
  CommonJS export slots retain possible later writes; shadowed `module` and `exports`
  do not export. Lone UTF-16 surrogates neither crash indexing nor collide with names.
  Changes to `tsconfig.json`, `jsconfig.json` and their `extends` inputs invalidate the index.
- JS/TS module members passed as arguments are references only through proven export
  slots, respecting aliases, default values and namespaces. An unexported homonym is
  not enough; uncertain values and accessors remain unresolved.
- PHP callbacks use catalog argument positions and proven origins, respecting
  function/class aliases and namespace blocks; ordinary data arrays are not callbacks.
  WordPress `add_shortcode` callbacks are recognized.
  Dynamic method reach retains possible receiver classes, including properties, so
  an alternative assignment does not incorrectly make a reachable method look dead.
- `impact --json` adds `relation: "call" | "reference"` to items and chain steps,
  including the corresponding MCP output, with the same distinction as text output.
- Agent streams keep the event limit even when errors arrive concurrently. Reserved
  terminal diagnostics preserve the outcome when cleanup of an exited process fails
  at the exact event cap, instead of reporting a false event-limit failure.
- Indexing time: a full index of code-graph's own repository takes about 25% less
  than in 1.6.4 (2.8 s against 3.7 s, median of 7 runs on one machine) but still
  about 25% more than in 1.6.3 (2.2 s).

**1.6.4 — more callers found, fewer false dead-code candidates**

- Python re-exports: calls through a package's `__init__.py` (`from .impl import f`
  or `from .impl import *`) reach the defining module. A package re-exports its
  last module-level import, or the arms of one `try`/`except`. An import into the
  project that cannot be followed falls back to the name at low only when the module
  serves names through a module-level `__getattr__` (PEP 562); an external import
  never links a project function with the same name.
- Python receivers: `self.x = param` keeps the parameter's annotation and a
  module-level instance keeps its type inside functions, unless a parameter,
  assignment, `for`, `with`, `except`, `:=` or `match` rebinds the name; such calls
  are linked by type even when many methods share the name.
- Python imports: both arms of `try`/`except ImportError` are targets (medium), a
  later import replaces an earlier one, `from m import *` prefers `m` over a
  homonym, and `module.Class.method()` is resolved.
- Modules loaded from files: calls are resolved inside `if`, `for`, `while`,
  `with`, `try` and `match` blocks, `assert`, `raise` and comparisons, with
  `Path(...).parents[N]` paths. A loader executed in one branch never counts in
  another; a method called directly on what a loaded function returns is never
  linked to a same-named method by guess; a call to a loaded class also links its
  `__init__` and `Class(...).method()` links that class's method.
  code-graph now finds its own callers (its scripts load each other this way).
- References: functions passed as arguments (Python, JS; PHP `[$this, 'm']`,
  `[X::class, 'm']` and string callables of `array_map`, `usort`, `add_action` and
  similar) and functions registered by a decorator (`@cli.command()`,
  `@app.route`, `@bp.route`, `@command("x")`) are references: `impact` lists them at
  medium as `riferisce`, and `dead` no longer proposes them. Pure decorators such as
  `lru_cache`, `staticmethod` or `property` do not count when written with their own
  name; any other decorator, an alias of these included, counts as a registration.
- `dead`: `getattr(module, ...)` in any file and `$obj->$m()` on a known class make
  the reached functions `AMBIGUOUS`; `Procfile`, `Dockerfile`, `Containerfile`,
  `Makefile` and `Justfile` count in the text scan.
- JS/TS modules: `export * from`, `export { a as b } from`, `module.exports =
  require(...)` and re-exported objects are followed (up to four levels). Names bound
  by `require`, `import * as`, default imports and `import()` are module receivers
  (`ns.f()` reaches that module) with block scope: parameters and locals hide them, a
  name written again is dropped and `require` counts only as the loader. Re-exports
  expose only exported names, the last unconditional `module.exports` wins, and a
  package `exports` map limits its subpaths;
  TypeScript `paths`/`baseUrl` with `extends` and workspace packages resolve; unknown
  packages are never guessed by name.
- JS dynamic loads and JSX: `import()`, `require(expr)` and `f()()` are dynamic calls;
  `impact --json` on a JS/TS target adds a `dynamic` field with the load sites of its
  file. JSX elements are calls of the component (medium at most), and
  `module.exports = function f(){}` marks `f` as exported.
- PHP function imports: `use function Ns\f;`, its alias and grouped
  `use function Ns\{f, g}` link calls to the function of that namespace, also when
  more than three namespaces define the same name; an imported function outside the
  project no longer gives the call to a project homonym. Function and class imports
  keep separate aliases, and an import counts only in its own namespace block.
- Change flow: the cleanup after a test run no longer fails with a permission error
  on macOS when the test process has already exited; the test result is kept and a
  persistent cleanup error is recorded as `cleanup_error` (deferral 30 of 1.6.3).

**1.6.3 — callers of modules loaded at run time, and clearer docs**

- Static resolution of calls to functions of modules loaded with
  `importlib.util.spec_from_file_location` + `exec_module` or `runpy.run_path`,
  when the path is a literal or a literal joined to a known name (medium for exact
  paths, low for derived ones); undeterminable paths are marked as dynamic imports.
  Calls inside `if`/`try`/`for`/`with` blocks are not resolved yet.
- `detect()` uses the shared group-signal rule: no EPERM from the version check on
  macOS; cleanup problems are reported in `reason`.
- `prepare` suggests the exact `declared:` ID, in English and Italian.
- README: exact `--json` command list, MCP capture fidelity, Italian-only outputs.

**1.6.2 — steadier edges around paths, links, signals and output**

- The dashboard Plan panel excludes invalid plan paths (the rule of
  `ChangeFlow._relative`) from the approved scope and shows them apart; a shared
  table of cases checks the panel rule against the engine rule.
- Symbolic links created by test runs in the change worktree are recorded as
  inert notes of their target and removed, instead of failing the step.
- MCP server capture: byte-faithful line endings, Python buffers flushed before
  descriptors are restored, bounded wait with an explicit cut when a background
  process keeps the descriptors open.
- Agent runner probe signal and parallel runner cleanup no longer fail with
  EPERM on macOS when the process group has already ended.

**1.6.1 — confirmed agent starts, a Claude spending cap and a steadier dashboard**

- `cg change plan`, `edit` and `review` ask for confirmation before starting an
  agent; `--yes` confirms and, without a terminal, they refuse with exit 2.
- Claude spending cap in USD from the dashboard (change form and session
  questions) and `cg change new --budget-usd X`, passed as `--max-budget-usd` to
  every Claude run of the change; invalid values are refused, also after a redraw.
- Files generated by the engine's test runs (such as `__pycache__/*.pyc`) stay out
  of the change commit and of the scope check.
- Invalid paths in an agent plan go to `checked.unresolved` instead of failing
  verify or approve.
- Dashboard server listen queue raised from 5 to 128: under load the page no
  longer loses its assets (48 concurrent requests: 33/16/5 failures before, 0 after).
- Agent runner: no permission error on macOS when cancelling or cleaning up an
  exited agent's process group; schema validator limited to the keywords in use.
- MCP server captures command output at file-descriptor level, so non-Python
  writes cannot corrupt the protocol; end-to-end test of dashboard questions.
- «Verify» label and a separator in impact chains; README notes that the
  end-to-end tests need the skill `.venv`.
- `tools/parallel_tests.py`: the unittest suite in isolated module processes,
  failing unless collected equals executed (178 s serial against 95 s parallel
  in one measured pair on a busy machine).

**1.6.0 — plan, edit, verify and review with Codex and Claude**

- Added the dashboard change flow and `change` CLI: an approved plan, an isolated
  Git worktree, approved tests before and after editing, review by the other
  client, and explicit local integration or discard. No automatic push.
- Added the stdio MCP server, including numbered file reads with `cg_read`.
  MCP analysis does not edit source files or execute project tests.
- Added setup, privacy and security documentation for public distribution under
  the project [license](LICENSE.md).
- Added dashboard session consent checked at the actual agent start, at most three
  concurrent read-only questions, textual event rendering and change-specific
  approval drafts. Codex runs without its shell tool; Claude runs without Bash.
  The CLI has no consent prompt: `cg change plan`, `edit` and `review` start
  clients and send context to their providers when the step can run. Use them
  only on the user's explicit request for that step with that agent.
- Added recovery of interrupted workflow steps, explicit unresolved plan paths,
  and preservation of bounded static-review JSON during verification.
- Documented the limits of Git isolation: the change flow's Git helper disables
  hooks and signing, but configured merge drivers and filters remain active.
  Static review uses a separate Git helper and can also invoke clean filters.
- The release candidate passed 206 automated tests. One authorized native smoke
  run recorded Codex planning/editing and Claude review with verdict `changes`.
  Test-created `__pycache__` files entered the edit commit; verification reported
  them outside the scope and a changed worktree. No integration is recorded;
  this does not demonstrate a failed merge attempt. The cleanup fix is deferred
  to 1.6.1.
  These checks do not establish general reliability or savings in time or tokens.

**1.5.1 — bounded source windows and guidance through a change**

- Added source windows with Unicode column ranges, hashes, explicit truncation
  and continuation, including very long single lines.
- Improved the feature catalogue: exclude tests before applying its limit and
  use registration and configuration evidence for backend entry points.
- Prioritized context linked to a feature's roots and allowed selected
  collaborators to contribute to a combined test proposal.
- Added direct `file:symbol` navigation and a project guide that prepares a
  handoff for an agent. Character budgets remain distinct from measured tokens.

**1.5.0 — feature cards, AI preparation and revision comparison**

- Added `features` and `prepare`, with navigable flows, contracts, recognized tests
  and a bounded context packet for an agent.
- Distinguished declared purpose, code-derived entry points and planned features
  without an implementation. Preserved uncertainty, stale evidence and omissions.
- Added comparison with a previous preparation, including signatures, source,
  callsites and test evidence that remains available after a function is removed.

**1.4.0 — focused analysis and explicit measurements**

- Added targeted source/context reads, verification proposals, Git baseline
  comparisons and static observations of files, databases and processes.
- Added architecture views, English/Italian UI and light/dark themes.
- Measured comparable payload sizes in the efficiency view, including regressions;
  character-based token estimates are not claims about whole-task model costs.
- Updated the index schema and made stale-source checks and evidence limits
  explicit. Older indices are rebuilt as caches.

**1.3.0 — guided local project explorer**

- Added `explore`, a loopback dashboard with project selection, indexing progress,
  overview, search, source, callsites, impact and test proposals.
- Added a session token and workspace limits, a native folder picker with a local
  browser fallback, and checks for stale or out-of-order responses.
- Kept static exploration separate from running project commands or tests.

**1.2.0 — offline interactive HTML call graphs**

- Added `export` with search, zoom, draggable nodes, module/confidence filters and
  navigation through callers and callees.
- Preserved callsite details while limiting visible nodes and total export size.
  Added explicit partial-result messages and source-safe text rendering.
- Read the index without changing it, confined output paths and refused symlinks
  or replacement of an existing export without `--force`.

**1.1.1 — audited engine and isolated installation**

- Added the isolated launcher and setup script, pinned binary dependencies and
  installation instructions for the complete skill directory.
- Corrected reproduced resolution and selftest defects, including named imports,
  aliases and receiver handling; kept uncertain calls explicit.
- Checked source content hashes during incremental indexing, removed obsolete
  records and made indexing failures roll back instead of leaving partial updates.
- Added regression tests and documented the limits of static analysis and dead-code
  candidates. A candidate is not authorization to delete code.

**1.1.0 — original engine snapshot**

- Provided local tree-sitter/SQLite analysis of PHP, Python, JavaScript and
  TypeScript, with symbols, calls, imports, inheritance and command-line queries.
- The initial review found correctness and packaging problems. This snapshot was
  not accepted for installation; the reproduced defects were addressed in 1.1.1.
