# Benchmarks and evaluations

- [Scaling and language coverage](SCALING.md) reports reproducible measurements on generated workloads of 10,000, 100,000 and 1 million lines and on Go and Dart SDK source. It covers time, memory, updates, query latency and measurement limits.
- [Agent studies and pilots](LIVE_RESULTS.md) reports the study of 23 historical bugs from Django, typescript-eslint and Prometheus. It includes the original and corrected classifications, provider-reported token and cache counts, costs and limitations. Both approaches fixed the same number of bugs, and the study found no aggregate input-token saving.
- [Original pilot protocol](LIVE_PROTOCOL.md) records the first 1.7 evaluation. That run was inconclusive because of a provider quota. The results page also reports later experiments.

## Historical comparison from 1.3.0 to 1.4.0

For the changes following 1.5, see the [1.5.1 comparison](refinements-1.5.1.md).

The comparison used two copies of the same HotSkills snapshot, including source and configuration, identified by SHA-256 `ee569616400de2a31d52145a153597ac42436e10574050fa50ee65dc2c910fad`. The benchmark ran static analysis without executing HotSkills modules, entry points or tests.

## Fixture results

| Check | 1.3.0 | 1.4.0 |
|---|---|---|
| Short context, 250 estimated tokens or 1,000 characters | 1,027 characters, with callers and callees missing | 976 characters, with entry point, leaf, revision, call site and omissions |
| Converging call paths | 25 excerpt occurrences, 13 unique | 25 references, each of the 13 unique excerpts sent once |
| Source tab for the fixture | 11,700 bytes for the full payload | 11,126 bytes for the selected fields, excluding relationship source excerpts |
| Index run with no changes | Revision changes | `content_revision` and `revision` stay stable, while `run_id` changes |

The revised check uses the same fixture and accounts for field selection and references. Sizes measure the UTF-8 JSON written by each runner, excluding HTTP wrapping. [Baseline data](baseline-1.3.0.json) · [1.4.0 data](after-1.4.0.json)

## HotSkills case study

Both versions indexed 795 source files and found 7,089 definitions. The number of call-site edges went from 45,429 to 45,419 because the newer version treats recognized PHP callable expressions as references.

| Query and comparison | Before | After | Change |
|---|---:|---:|---:|
| Source tab for `serve_export`, full 1.3 payload versus selected 1.4 fields | 31,877 bytes | 13,614 bytes | −57.3% |
| Impact, full versus deduplicated payloads in 1.4 with identical call paths | 74,466 bytes | 51,505 bytes | −30.8% |

The source tab returned the same source text. Impact retained 52 entries. The newer classifier recognized 39 tests, compared with 49 symbols in test files under the old classifier. Helpers and support code still appear in impact results. The deduplication comparison holds the set of 1.4 results constant.

The newer results retained the qualified consumer in `pipeline/integrate.py` and the reference to `_close_pins`. They exposed 794 observations and 378 configuration and include entries, of which 17 declarations were dynamic. The analysis found zero resource links and could not infer a complete pipeline-to-site-to-MCP connection automatically. [Source evidence](hotskills-flow.md)

## Task coverage and response size

The [ten tasks](tasks.json) used held-out fixtures and deterministic expectations for PHP and Python callers, signatures and tests, callbacks, a long function, test discovery, declared resources, stability, a mistyped mixin and review after line shifts.

- [Baseline](tasks-before-1.3.0.json) completed **4/10** tasks. Some required features were unavailable.
- [Version 1.4.0](tasks-after-1.4.0.json) completed **10/10** tasks.
- Total response length grew from 12,117 to 14,525 characters as the expanded coverage added test plans and evidence. The tasks do not establish a token saving.

Each task records characters, bytes, estimated tokens (characters divided by four), command or API steps and elapsed time. T8 runs both the unchanged and changed index cases. The data does not include tokenizer measurements or the cost of an entire conversation.

## Timings and limits

| Operation | Baseline | 1.4.0 | Runs |
|---|---:|---:|---|
| Index the snapshot | 6.05 s | 6.65 s | 1 per version |
| Run `check` | 0.63 s | 3.02 s | 1 per version |
| Open `ProjectData` and query resources by owner, median | 6.5 ms | 18.6 ms | 3 per version |

More complete binding and configuration analysis added work. The per-run resource timings and minimum, median and maximum are in the [recorded results](hotskills-before-after.json). These samples are too small to establish statistical significance. They did not measure peak release memory or throughput at multi-million-line scale.

An [early development measurement](development-profile.json) took about 130 s and returned thousands of diagnostic lines. It motivated in-memory AST lookup and grouped diagnostics. On this snapshot `check` reports six maintenance findings. The `--include-diagnostics` option exposes the groups, counts and examples. The tests and independent oracles assess accuracy.

The resolver runs globally and is checked for equivalence between incremental and full runs. Engine or parser changes force a rebuild before the index reports a new revision.

## Reproduce the checks

```bash
.venv/bin/python -I benchmarks/improvement_probes.py --skill-root . --output /tmp/probes.json
.venv/bin/python -I benchmarks/run_tasks.py --skill-root . --output /tmp/tasks.json
.venv/bin/python -I benchmarks/real_project.py --project /path/to/HotSkills \
  --baseline-root /path/to/code-graph-1.3.0 --skill-root . --output /tmp/real.json
```

The historical baseline was the private commit `b09fcb7`. The JSON receipts include hashes of the engines and inputs. The real-project runner copies sources and configuration to a temporary directory. To compare separate runs, first verify that their input hashes match.
