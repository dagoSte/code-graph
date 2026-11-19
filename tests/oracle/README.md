# Shared edge comparison and native oracle

Run these tools with code-graph's Python environment. They require no network or paid model.
Engine inputs are trusted code: a checkout directory, `scripts/`, `cg.py`, or a Git revision
in `--repo`. Each version's scripts and data are frozen and indexed in a separate process.
Project inputs are copied once for both engines; their source files are never imported or
executed by `edge_diff.py`. The tools leave original repositories and caches unchanged.

```sh
.venv/bin/python -I tools/edge_diff.py --before v1.6.3 --after v1.6.4 --output /tmp/edges.json
.venv/bin/python -I tools/edge_diff.py --before /tmp/base --after . \
  --no-fixtures --project npm=/path/to/npm --project terser=/path/to/terser \
  --output /tmp/corpora.json
.venv/bin/python -I tools/oracle_matrix.py --output /tmp/oracle.json
.venv/bin/python -I tools/oracle_matrix.py --engine v1.6.4 --language js --rerun \
  --output /tmp/oracle-native.json
.venv/bin/python -I tools/oracle_matrix.py --language go --rerun --go /path/to/go \
  --output /tmp/go-native.json
```

## Edge comparison

Default projects are `tests/fixtures/`: one group per immediate directory, one project
per child directory; a family with top-level source files stays together. Standalone
files are separate projects in `standalone`. For projects whose root spans more than one
directory, supply `--no-fixtures --project GROUP=PATH` instead. Repeated `--project`
arguments add optional local corpora. Missing explicit paths are errors, not silently
skipped projects. `--project-list` reads a JSON array of `{id, group, path}` records
for exact survey groupings; IDs must be unique. No corpus is downloaded.

Rows are sorted tuples, independent of SQLite IDs. Duplicates are counted as a multiset:

```text
caller_file, caller_symbol, line, called_name, target_file, target_symbol, confidence, relation,
caller_line, target_line, receiver, receiver_name, nargs, reference_kind
```

Paths are relative to each project. Symbol declaration lines distinguish same-name symbols;
receiver and argument count distinguish calls on the same line. Exact duplicate rows retain
their multiplicity. References use nargs=-1 and preserve kind and receiver. The database
has no column offsets, so identical sites on one line cannot be distinguished beyond
these recorded fields. Output schema is edge-diff/2. Relation is `call` or `reference`. Resolved
`symbol_refs` have `medium` confidence, matching the engine's `neighbors()` contract;
an older database without `symbol_refs` contributes no references. Unresolved references
are not edges. Group summaries count calls and references separately and together;
project details contain every added/removed row plus indexed file lists. A confidence,
callsite, target or relation change is a removal and an addition. A zero delta means
identical measured rows, not that those rows are correct.

Input manifests and engine hashes accompany the report. Git-selected tracked/untracked
nonignored files are used when available; otherwise the directory is walked. Symlinks,
Git metadata, virtual environments, node_modules/vendor and analysis caches are excluded.
The root `.code-graph-catalog.json` is preserved as project configuration even when
Git ignores it; `.code-graph/` remains excluded as cache. Fresh temporary Git
indexes preserve selected dot-directory files without loading project/global Git hooks
or filter configuration. Other engine skips (size, syntax, language, `.cgignore`) remain
the engine's own behavior; inspect indexed file lists when interpreting a missing edge.
Empty selected inputs, empty indexes and partial analyses reported in engine metadata
are errors, even when both engines report zero edges. Error messages name partial paths
(or their count if an older engine only records that).
Engine errors/timeouts abort with exit 2; observed differences themselves return 0.
Identical engine content produces a console warning. Worker scratch directories live
inside the parent measurement directory, which is removed even after a timeout.

## Oracle observations

`js.json` preserves exactly the 102 program sources from the final R164c/R164d matrix,
verified with Node v25.2.1. Each observation names its **site**: source file, caller,
line, called name and relation. Every strong edge from that site is assessed, including
targets in app.js or any other file. Site location matching does not trust the edge
caller: an edge assigned to another caller at that location is wrong even with
the expected target. Only an edge with the full recorded site identity satisfies
an expected target; an edge from another site cannot supply it. The additional domain a.js/b.js/decoy.js assesses edges from any site. Other
helper/factory calls outside both domains are excluded. The recorded site, including
its caller, must also exist among extracted calls, independently of resolved edges;
absence is an error even for an empty expected target set.
The js-polish protocol maps `A` to `a.js:polish`; `SHADOW` and `NOT_CALLABLE` expect
no named static target at that site. The shadow implementations are anonymous fixture
functions; this bounded oracle does not claim whole-program call coverage.

`php.json` preserves the three valid native H2b programs. It assesses **references**
from the declared `drive`/`polish` sites: first-class `use function`, and the two namespace-block
cases. The latter use native `is_callable`, not an executed callback (array_map gets
an empty array). Namespace-block positive expects `lib.php:polish`; negative expects
no such reference. The PHP sites are witnessed in extracted references, including unresolved ones;
the additional target domain is lib.php/global.php. These observations do not claim runtime call coverage.

Every program records source hashes, native argv/exit/stdout/stderr, expected targets,
required indexed files, observed sites, target domain, protocol and its reviewed baseline. An additional
evidence hash covers the complete case except that hash itself. The loader checks
expected targets against the native output protocol even when a hash is recomputed.
Hashes detect unreviewed edits; they do not authenticate an author or replace review.
Adding a new native output protocol requires an explicit interpreter and review. Classification:

- `wrong`: any observed edge above `low` points outside the expected target set,
  even if a correct target is also present.
- `abstention`: no wrong strong edge, but an expected target is missing.
- `ok`: no wrong strong edge and every expected target is present (including the
  valid empty-target case). `low` rows remain hypotheses and do not satisfy a target.

On v1.6.4 the JS totals are **68 ok / 17 wrong / 17 abstention**. Lane B-js of 1.6.5 fixed the 17 JS
known wrongs (module objects handed on, written through wrappers or named with escapes): **85 ok / 0 wrong /
17 abstention**, with those 17 baselines now `ok`. The independent PHP
totals were **2 wrong / 1 abstention** in 1.6.4; B-php fixes the import and namespace-block cases to **3 ok / 0 wrong / 0 abstention**, confirmed by rerunning PHP 8.3.23. Known wrong entries name `dec-rinvio-1.6.4`
and pin the exact wrong rows. A changed wrong row, changed missing-target set or a corrected known wrong fails
until that entry is explicitly updated after review. Other entries fail on a worse
classification; an abstention becoming ok is allowed. Counts alone are never the gate.
When a later lane fixes a case, update its `baseline` with that lane's reviewed evidence
and coordinate ownership of these files. Do not relabel observed wrong targets as expected.

Default suite execution uses recorded native observations and does not even locate Node
or PHP. `--rerun` explicitly executes the bundled programs in temporary directories,
one process per program, with a 10-second timeout. If a runtime is absent the report
states `recorded-runtime-unavailable` and prints a console warning. Installed runtimes must reproduce exit/stdout/stderr;
differences fail and are saved for review. It does not silently rewrite the source,
expected targets or baseline. Output JSON contains regenerated observations for an
explicit reviewed update. Native execution is for these trusted fixtures, not arbitrary
untrusted corpora. The graph is measured before native execution so runtime writes cannot
contaminate its inputs. Missing required indexed files are errors, never abstentions.

Oracle exit 0 means the reviewed baseline has not worsened and known wrongs are unchanged;
it does **not** mean all programs are correct. Exit 1 means a regression, changed known
wrong or native mismatch. Exit 2 means an invalid input, engine failure or runtime error.

Dart recorded cases need no SDK. To rerun only the bundled trusted Dart programs:

```sh
.venv/bin/python -I tools/oracle_matrix.py --language dart --rerun --dart /path/to/dart --output /tmp/dart-oracle.json
```

The programs invoke the VM with `--disable-dart-dev`; no pub, hooks or CLI analytics.

Java recorded cases require no JDK. Native reruns compile only bundled trusted sources:

```sh
.venv/bin/python -I tools/oracle_matrix.py --language java --rerun --java-home /path/to/jdk --output /tmp/java-oracle.json
```

Compilation uses --release 17 -proc:none, isolated source/class paths and temporary output.

Swift recorded cases require no Xcode installation. Native reruns compile only the
bundled trusted sources, without SwiftPM or project builds:

```sh
.venv/bin/python -I tools/oracle_matrix.py --language swift --rerun --swiftc /path/to/swiftc --swift-sdk /path/to/sdk --output /tmp/swift-oracle.json
```

Compilation uses language mode 5, an explicit SDK and isolated module cache/output.
