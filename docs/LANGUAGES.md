# Language adapters and the next analysis increments

The initial extraction from the 1.6.5 engine preserves database and CLI/MCP formats.
Additional languages include the declared [Go](GO.md), [Dart](DART.md), [Java](JAVA.md)
and [Swift](SWIFT.md) subsets and separate [SQLite SQL resource evidence](SQL.md).
No adapter claims complete language or cross-dialect semantics.

## Implemented boundary

`scripts/language_adapters.py` loads eight built-in modules by absolute sibling path:

| Family | Module | Extracted responsibilities |
| --- | --- | --- |
| Python | `language_python.py` | Call extraction and bounded interpretation of file-module loaders |
| PHP | `language_php.py` | Call extraction and namespace-aware function/class lookup |
| JavaScript / TypeScript / TSX | `language_javascript.py` | Call extraction, module bindings, exports and module resolution |
| Go | `language_go.py` | Package declarations, receiver facts and isolated local-module resolution |
| Dart | `language_dart.py` | Library declarations, relative/local package imports with URI identity guards and explicit local static methods |
| Java | `language_java.py` | Source-root/package type binding and non-overloaded static/private/final methods |
| Swift | `language_swift.py` | File-local private functions/static methods, explicit SDK and overload abstentions |
| SQL (SQLite subset) | `language_sql.py` | Source declarations and resource read/write observations, separate from call edges |

Each module exports `EXTENSIONS` (suffix to indexed language), `DEFINITIONS` (AST node
type to symbol kind) and `CALL_TYPES`. Call-handling adapters also export
`handle_call(extractor, node, function_id, class_id)`; SQL uses its dedicated
extraction with `CALL_TYPES=()` and emits no call edges.
The registry supplies only `CLASSLIKE`, `LANG_BY_EXT`, `fam`, `txt` and
`last_seg`. It loads no module selected by project data or by the working directory.
There is no third-party adapter discovery.

Go, Dart, Java, Swift and SQL supply `extract(extractor, root)` for their concrete declaration/scope rules;
the existing families keep the shared walker. Each resolver receives an exclusive
dispatch for both calls and references, with no generic name fallback.

`Extractor` remains the shared context: it owns symbols, calls, imports, references,
observations, type hints and scope state. It records callback references before
delegating the call node. Adapters add extraction facts, while resolution assigns
targets and confidence. A function reference remains distinct from an invocation;
dynamic or uncertain targets must not gain certainty through the adapter boundary.

The existing `cg.py` entry points and the `PythonFileLoads`, `PhpNames`, `JsModuleFacts`,
`JsModules`, `js_name` and `js_name_text` names remain available. All adapter files live
directly under `scripts/`, so the existing engine fingerprint includes their contents
and changing one invalidates the index. No database migration is required.

This is an incremental boundary: declaration walking, some type/import rules and the
global resolver still have language-specific branches in `cg.py`. Registering a file
extension alone is not enough to implement another language. Move further behavior
when a concrete adapter needs it, with equivalence evidence for the current languages.

## Semantic references and required probes

Sources below were consulted on 11 November 2026. They describe language rules, not
the coverage implemented by code-graph. A new adapter must state its supported
language version or SQL dialect and turn the relevant rules into positive and negative
fixtures. Record compiler/runtime versions when native observations are collected;
reading a specification is not a substitute for checking an implementation.

| Language | Official reference | Rules the adapter must distinguish and probe |
| --- | --- | --- |
| Python, present | [Python 3.13 execution model](https://docs.python.org/3.13/reference/executionmodel.html) | Function and class scopes, binding order, imports and shadowing |
| PHP, present | [Namespace resolution](https://www.php.net/manual/en/language.namespaces.rules.php) | Function/class import spaces, qualification and namespace blocks |
| JavaScript, present | [ECMAScript environment records](https://tc39.es/ecma262/#sec-environment-records) | Lexical bindings, scope and module exports |
| TypeScript, present | [Module resolution](https://www.typescriptlang.org/docs/handbook/modules/reference.html) and [type compatibility](https://www.typescriptlang.org/docs/handbook/type-compatibility) | ESM/CommonJS resolution and structural compatibility; syntax support is not full type checking |
| Go, subset implemented | [Go specification](https://go.dev/ref/spec) and [coverage](GO.md) | Packages and concrete receivers; interface dispatch and promoted embedded methods abstain |
| Dart, subset implemented | [Specification status](https://dart.dev/resources/language/spec) and [coverage](DART.md) | Standalone libraries and relative imports; instance/extension/mixin dispatch abstains; the Dart 3 specification is still in progress |
| Java, subset implemented | [Java SE 17 method invocation](https://docs.oracle.com/javase/specs/jls/se17/html/jls-15.html#jls-15.12) | [Coverage](JAVA.md): overload groups and virtual dispatch abstain; static/private/final bindings are separate facts |
| Swift, subset implemented | [The Swift Programming Language](https://docs.swift.org/swift-book/documentation/the-swift-programming-language/) and [official extension reference source](https://github.com/swiftlang/swift-book/blob/main/TSPL.docc/LanguageGuide/Extensions.md) | [Coverage](SWIFT.md): distinct extension owners; bounded private/static binding excludes overloads, SDK collisions and member contexts |
| SQL, SQLite resource subset | [SQLite syntax](https://www.sqlite.org/lang.html) and [CTEs](https://www.sqlite.org/lang_with.html) | [Coverage](SQL.md): source declarations, explicit FROM/DML resource candidates; no runtime binding or cross-file migration inference. Other dialects, including PostgreSQL, remain future work |

The installed grammar pack contains parsers for Go, Dart, Java, Swift and SQL. That is
parser availability, not implemented indexing, resolution or test discovery. Keep these
support declarations limited to the implemented contracts above, rather than
treating parser availability as complete semantic support. Go targets a declared Go 1.23 subset, with native
Go 1.27.4 observations and a bounded standard-library source case. Dart targets a 3.6
subset with native Dart 3.6.2 observations and bounded SDK sources. Java targets a declared
Java SE 17 subset, with OpenJDK 21.0.2 native probes compiled using --release 17. Swift targets
language mode 5 with local Apple Swift 6.4.2 native observations, not all SDK/platform semantics. The SQLite subset has in-memory native authorizer probes; further SQL dialects require
separate acceptance cases.

## LLM-assisted search and correction

The existing change flow already uses an agent for planning, editing and review, with
graph evidence, a worktree and tests before/after. The next increment should reuse that
flow for evidence-driven search: interpret the request, retrieve symbols, expand missing
sources and expose uncertainty before proposing an edit. The [local retrieval increment](SEARCH.md)
adds FTS5/BM25 and graph/RRF evidence through `search` and `cg_search`. The existing
agents now receive [retrieval guidance and correction feedback](AGENT_SEARCH.md):
they can reformulate queries and inspect sources before proposing changes. Live model
quality and task-level benefits remain to be evaluated; retries retain the existing gates.

Model suggestions must remain attributable to the sources and revision they used.
They must not silently become statically resolved edges. Compiler/type-checker facts,
static candidates and model hypotheses need explicit provenance when combined. Optional
semantic backends require a separate implementation decision; none is introduced here.

Measure the whole task with and without assisted search: correct locations found,
successful corrections, regressions, elapsed time and measured tokens. Preserve failed
cases. Do not infer better stability or lower cost from model agreement or payload size.

## Acceptance checks

For each increment, use its preceding accepted commit and identical frozen inputs:

```sh
scripts/cg selftest
scripts/cg catalog validate
.venv/bin/python -I tools/parallel_tests.py --jobs 4
.venv/bin/python -I tools/oracle_matrix.py --output /tmp/adapters-oracle.json
```

Require every collected test to execute, unchanged normalized call/reference rows and
indexed file lists on the comparison fixtures, and no worse recorded oracle results.
Run `edge_diff.py` with `--before BASE --after . --no-fixtures --project-list LIST.json`
on an explicit list of projects the baseline already indexes. The list uses objects
with `id`, `group` and `path`, as returned by `fixture_projects` in that tool; freeze
it with the proof. Current fixtures include new languages that older baselines cannot
index, so an unfiltered comparison against v1.6.5 is no longer valid. Measure new
language/resource coverage separately; do not accept an empty baseline as equivalence.
Adapter regressions also cover loading from the engine directory and cache invalidation.
The recorded oracle retains known abstentions; it does not prove whole-language accuracy.
Native oracle collection and browser tests remain separate, explicitly run checks.

The GitHub workflow runs setup/selftest, catalog validation and the whole unittest suite
on Linux/macOS with Python 3.12/3.13. Its remote execution must pass before treating those
platform combinations as verified by CI.
