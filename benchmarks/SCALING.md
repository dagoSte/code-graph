# Static indexing at scale

This experiment measures the accepted engine at `167bd3c` (the engine still reports
1.6.5). The later release label is not a second performance run. Inputs are copied
and hashed before measurement; analyzed sources are never imported, compiled or
executed. SDK source coverage is not a language conformance or precision score.

The runner is [`scale.py`](scale.py). Each workload gets a separate Python worker,
one full index, one unchanged index and one index after appending a comment to one
file. Constructor instrumentation counts parsed files. Query measurements include
a fresh readonly reader, freshness checks, search and close; three samples use the
warm OS cache and exclude HTTP. Peak RSS covers the worker's lifetime, including
engine loading, all index phases and queries. It is not the memory of the desktop
app or total system memory. No cache flush, repeated indexing average or statistical
significance is claimed. Every worker has a 600-second deadline; failures and missing
measurements remain in the receipt.

Generated inputs alternate Python, PHP, TypeScript, Go, Dart, Java, Swift and SQLite
SQL. They contain simple declarations and same-file calls, not realistic dependency
or type complexity. SQL contributes declarations and resource observations, not call
edges. Swift contributes function declarations and local variables. Exact declaration
and call counts are fixed by the generator. The recorded target audit additionally
compares caller file/name, target file/name and confidence as a multiset, after the
incremental phase. This checks the generated pattern only; it does not establish
accuracy on SDK sources or arbitrary code.

## Recorded results, 12 November 2026

Host: macOS 27.2, ARM64, Python 3.13.14. Times below are single observations on a
running desktop; background activity is not controlled. Query columns are medians
of three samples of one deterministic symbol query, not a query-distribution study.
Own UI test servers/headless browser were stopped before measurement.

| Input (source lines) | Indexed files | Full s | Unchanged s | One-file change s | Peak MiB | Lexical / hybrid ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Generated 10,000 | 62 | 0.3624 | 0.1055 | 0.1283 | 59.9 | 30.735 /30.774 |
| Generated 100,110 | 623 | 2.4872 | 0.2108 | 0.4173 | 118.3 | 49.940 /50.760 |
| Generated 1,000,091 | 6,226 | 26.9938 | 1.2383 | 3.5753 | 731.4 | 208.850 /233.743 |
| Dart SDK 208,759 | 433 | 4.3264 | 0.1947 | 0.4847 | 143.0 | 70.079 /76.575 |
| Go SDK 2,233,479 | Not completed | Timeout 600 | Not run | Not run | Not measured | Not run |

All four completed workloads parsed zero files on the unchanged index and one file
after the comment change; unchanged content revisions remained stable. The three
generated workloads had zero partial files and exact expected declaration/call counts.
The post-incremental audit found respectively 2,145, 21,294 and 212,472 expected call
relationships, with zero missing/unexpected target tuples. SQL has no call edges.

The Dart capture contains 425 Dart and 8 JavaScript files, with 29 partial files;
all six query samples report `partial`. Its 3,978 resolved callsites out of 34,920
registered calls are coverage counts, not a precision/recall score. This does not
claim complete SDK analysis. The Go capture copied 7,058 source/config files and
excluded 3 inputs under the copy guards; the full phase reached its 600-second deadline
before a completed phase receipt, so its index counts, memory, incremental and query
results remain missing. The larger real Go tree is a concrete performance limit;
the million-line generated result must not be extrapolated to it.

The [sanitized receipt](scaling-1.7.0.json) contains all samples, failures, source
snapshot hashes, engine script hashes and the target audit. It binds the measured
`scale.py` worker to the v2 freeze; later scoring changes affect only the LLM pilot.
No failed workload was removed or rerun with a larger deadline.

The two real-source captures use public SDKs already available locally:

| Capture | Version | Archive SHA-256 | License |
| --- | --- | --- | --- |
| Go `src` | go1.27.4, darwin/arm64 | `91a44a2b8a716600ad94a4f143e156116620db8828086a6e3602291be9bd0bb4` | Go BSD license |
| Dart `lib` | 3.6.2, macOS/arm64 | `453bbfa3d73f28e090b644268e2c809b899d11a3a6507874715e1a4042166295` | Dart BSD license |

Archive origins: [Go](https://go.dev/dl/go1.27.4.darwin-arm64.tar.gz) and
[Dart](https://storage.googleapis.com/dart-archive/channels/stable/release/3.6.2/sdk/dartsdk-macos-arm64-release.zip).
These are provenance links; the benchmark neither downloads nor runs an SDK.
Only eligible source/configuration files are copied, preserving the indexer's
size, ignore and path rules. The receipt distinguishes copied files from indexed
sources and reports partial analysis explicitly. Native positive/negative language
oracles in [`docs/LANGUAGES.md`](../docs/LANGUAGES.md) remain separate evidence.

## Go SDK follow-up, 13 November 2026

The Go timeout above is preserved as the historical result. A follow-up on the
same host and exact SDK capture fixes repeated whole-project scans in
`GoPackages.imported`. The baseline is `c42aab8`; the production change only
groups file IDs by directory and declared package, checks the requested alias
before candidate lookup, and tests eligible package members. Existing excluded-file,
test-file and module filters remain. No parser, dependency, resolver policy or
schema changes, and no source execution, are involved.

All 7,058 copied source/config files match the original capture and SDK hashes.
The canonical input-map SHA-256 remains
`7fe297462b2016cadfcd358d07d51bf3ac0009b0914d4fd74acb7022bd9cdb8a`.
Each measurement uses a fresh copy and a process deadline. The profiling results
are separate from the unprofiled `scale.py` worker results:

| Full SDK under cProfile | Full s | Parse s | Extraction s | FTS store s | Resolve s |
| --- | ---:| ---:| ---:| ---:| ---:|
| Before | Timeout 600.060 | 3.874 | 49.289 | 5.479 | 532.977, interrupted |
| After | 85.558 | 4.125 | 51.747 | 5.879 | 11.873, completed |

Before interruption, import lookup consumed 531.381 seconds and invoked
`package()` 410,099,680 times. After the change the entire resolve completes,
import lookup takes 3.344 seconds, and total `package()` calls are 2,089,931.
These counts cover different amounts of completed work: the baseline never
finished resolution. They diagnose repeated scans; they do not give an exact
completed-run speedup ratio. No larger deadline or additional memoization was used.

One new unprofiled worker runs the original full/unchanged/comment-change/query
protocol, with a 600-second process deadline:

| Input (source lines) | Indexed files | Full s | Unchanged s | One-file change s | Peak MiB | Lexical / hybrid ms |
| --- | ---:| ---:| ---:| ---:| ---:| ---:|
| Go SDK 2,233,479 | 7,035 | 52.3055 | 3.1508 | 9.1573 | 1,037.7 | 814.045 / 1,036.582 |

The index contains 7,034 Go files and one Python file; the single JavaScript
candidate is skipped by the existing minification guard. There are 97,569
non-module definitions, 600,140 calls and 128,158 resolved callsites. **65 files
have parser errors**, and all six query samples report `partial`. These are
coverage counts, not accuracy scores or complete Go support. The unchanged phase
parses zero files and retains its content revision; the comment change parses one.
Memory is the worker lifetime peak, including all phases and queries. Query medians
use `Test All`, fresh readonly readers and warm OS caches, as in the original
protocol. Background desktop activity remains uncontrolled; no repeated-run average
or comparison between profiled and unprofiled durations is claimed.

Semantic equivalence is checked separately on a fixed SDK subset: 279 source/config
files, 277 indexed sources, including compiler and standard-library packages.
Its input-map SHA-256 is
`d3b8fa4d75be484739ee103ab8126808759ccf1cc409e6218f2aeb55e8c4c391`.
`go/types/trie_test.go` is excluded symmetrically before either comparison run
because it has a parser error; it remains in the full SDK workload. The existing
strict normalizer compares all 13 fact tables, retaining multiplicities: **123,706
facts and 4,185 call/reference relations are identical**, with no additions or
removals. After a one-file comment change, incremental and full rebuilds also have
identical normalized facts. The 82 fixture projects retain 3,504 facts and 390
relations. The new regression counts lookup work and checks negative import cases;
it does not depend on a timing threshold. These checks do not establish equivalence
or semantic completeness for the entire SDK.

## Reproduce

```sh
.venv/bin/python -I benchmarks/scale.py --skill-root . \
  --output /tmp/scale.json --work-dir /tmp/new-scale-run \
  --lines 10000 100000 1000000 \
  --real-project go-stdlib /path/to/go/src \
  --real-project dart-sdk /path/to/dart-sdk/lib --timeout 600
```

The work directory must be new. It preserves input hash maps, per-worker receipts
and indexed copies, including failed workloads. A fresh run measures the current
machine/engine, so compare engine and input hashes before comparing times. Sources
and local absolute paths are not part of the published result.

The generated target audit can be reproduced against each retained generated copy
(replace the path with its retained directory). This compares identities, not only totals:

```python
from collections import Counter
from pathlib import Path
import sqlite3

root = Path('/tmp/new-scale-run/0').resolve()
expected = Counter()
for path in root.rglob('unit*'):
    if not path.is_file() or path.suffix == '.sql':
        continue
    unit = int(path.stem.removeprefix('unit'))
    file = path.relative_to(root).as_posix()
    expected.update((file, f'bench{unit*40+i}', file, f'bench{unit*40+i-1}', 'high')
                    for i in range(1, 40))
with sqlite3.connect((root / '.code-graph/graph.db').as_uri() + '?mode=ro', uri=True) as db:
    actual = Counter(db.execute('''SELECT cf.path,c.name,tf.path,t.name,e.conf
        FROM edges e JOIN symbols c ON c.id=e.caller_id
        JOIN symbols t ON t.id=e.callee_id JOIN files cf ON cf.id=c.file_id
        JOIN files tf ON tf.id=t.file_id'''))
assert actual == expected, (expected-actual, actual-expected)
```

## Grammar investigation after 1.7.0 (13 November 2026)

The package update was investigated on the original captured source bytes in a
separate environment. The table describes **raw parser coverage**, not a new
performance run, compiler validation, or a new production benchmark. These parsed
sets contain 425 Dart sources and 7,034 Go sources respectively; auxiliary
JavaScript/Python files in the earlier index workloads are not counted here.

| Candidate | Source files | Partial before | Partial with candidate | Disposition |
|---|---:|---:|---:|---|
| `tree-sitter-go` 0.25.0 | 7,034 | 65 | 65 | All 7,034 named trees identical; no dependency added |
| `tree-sitter-dart` 0.1.0 | 425 | 29 | 0 | Rejected: call extraction regresses on previously complete input |
| `tree-sitter-language-pack` 1.25.0 | Not run | — | — | Excluded: parser downloads at runtime and Python 3.11+ requirement |

The per-source SHA-256 maps match before and after. Their JSON map fingerprints
(sorted keys, standard separators) are
`021255ec27a277f9a3f62b790a231b75cbd8ecb9ab42bffc5bec207307db2b86`
for Go and
`88768dca529310b05da9cda1ec155cbcbf22ff6a7bfdfe5d0b1f07bd8336f760`
for Dart. They identify these parsed source sets, not the larger workload manifests.

The Dart wheel was verified against an independent build of upstream commit
`a9bdfa3db2fbc9b9f12c93450d04a671f33a5102`: complete trees, positions and fields
match on all 425 SDK sources, static/oracle fixtures, and inputs generated by the
51 Dart/package tests. Nevertheless, two callsites disappear from previously
complete SDK files. A minimal `leaf<()>` example loses its resolved call while
remaining syntactically complete. See [Dart provenance and per-file deltas](../docs/DART.md#candidate-grammar-rejected-after-source-verification).
The [65-file Go diagnosis](../docs/GO.md#sdk-grammar-investigation-13-november-2026)
distinguishes parser gaps, negative test fixtures and non-source templates.

Production remains on language-pack 0.13.0 and tree-sitter 0.25.2. Historical
scaling JSON and timing tables are unchanged; the reduction to zero Dart parser
errors is **not shipped** and must not be presented as production coverage. No SDK
source was executed and no source byte was normalized for adoption.
