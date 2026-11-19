# Localization on additional historical bugs

This model-free benchmark evaluates `cg locate` on **45 additional bugs**, 15 each
from Django, typescript-eslint and Prometheus. None of the 23 live-study bugs is
included. It does not execute project code, native tests, model clients or a build.

## Prospective selection

Decision `locate-bench-regola`, registered on 14 November 2026 at 15:41:06 UTC,
fixes [selection-rule.json](selection-rule.json) before any benchmark-case query.
Use the first 15 rows per repository in the stored order of the study's frozen
selection ledger, with a verified linked bug issue, fix commit and nonempty
`production_files`. Exclude the 23 study cases. Original native-test dispositions
do not affect selection: test environments and red/green qualification are not
needed for this endpoint. All original metadata rows get an explicit disposition
in the bank's own selection ledger; no replacement follows a localization miss.

There are enough candidates in all three repositories, so no fourth repository
is needed. File targets include `other_production` when present, including mixed
language fixes. The frozen first-parent production diff supplies ground truth.
Callable mapping reuses `study_localization.targets` on that parent's syntax,
with language selected by extension. Added/absent files, partial syntax,
unsupported files and changes outside parent callables remain explicitly
unmapped for callable scoring. They are not dropped from file scoring.

Issue text, parents, production diffs, target spans and input hashes are frozen
**before** measurement. The historical PR description and fix are not queries.
The recorded query was exactly the original issue title, two newlines and
original body. The published case manifest masks local account names in three
issue bodies. `compare` recognizes that exact copy by SHA-256, keeps the original
input hash in the comparison and scores the recorded candidates against the
same ground truth. A new measurement records the hash of its actual inputs.
Do not tune terms, ranking or budgets on these cases.

## Prepare from already cached public inputs

`CACHE_ROOT` contains `<repository>/cache` local Git repositories with historical
objects, plus the public metadata snapshots collected for the study:
`pr-N.json`, Django `ticket-N.csv`, and GitHub `issue-N.json`. Preparation and
measurement perform no fetch or dependency installation. The source and patch
licenses remain in `../study-cases/licenses/` (BSD, MIT and Apache-2.0).

```sh
.venv/bin/python -I benchmarks/locate_bench.py prepare \
  --skill-root /path/to/frozen/code-graph \
  --cache-root /path/to/cached-inputs \
  --output /tmp/new-locate-bank
```

Preparation refuses an existing output directory or changed selection inputs.
It writes cases, per-file historical hunks/parent declaration spans, production
diffs and a complete selection-disposition ledger. A preparation error is kept
as an error to reconcile before measurement, not permission to change the cohort.

## Repeat on a different engine

```sh
.venv/bin/python -I benchmarks/locate_bench.py measure \
  --cases benchmarks/locate-bench/cases.json \
  --rule benchmarks/locate-bench/selection-rule.json \
  --cache-root /path/to/cached-inputs \
  --skill-root /path/to/engine-to-evaluate \
  --output /tmp/new-locate-results.json
```

The output must be new. Each historical parent is exported to a fresh isolated
Git tree and verified against its frozen tree hash. `locate` is called once per
case, with its default limit and budget; `--skill-root` chooses the engine.
Cases and ground truth are not rebuilt when comparing another engine. Receipts
bind the case/rule/tool hashes, engine script/catalog hashes and parser versions.
Drift or uncertain subprocess cleanup stops further queries; remaining cases are
explicitly listed as not started. Invalid answers and query timeouts count as
failed localizations with zero hits. The JSON is saved after every case and
contains no raw response, source excerpt, private path or session identifier.
The ordered candidate file/start/end-line projection is retained so every hit
can be recomputed from the receipt and the frozen ground truth without a rerun.

The baseline uses the main engine's defaults (4,000 characters). Decision
`l2-default-5000`, fixed before observing this bank, prescribes the frozen L2
candidate once at 4,000 and once at 5,000 characters against that baseline, so
ranking gains and the 25% larger maximum answer can be reported separately.
For those two evaluations add `--max-chars 4000` or `--max-chars 5000`; the
receipt records the requested override and `defaults=false`. This does not
authorize a replay, tuning on this bank, or starting L2 before its freeze.

Decision `l1-caso-12886`, registered before any query, retains TypeScript #12886
with unavailable ground truth: its first-parent diff changes only test tsconfig
JSON, not the production file listed in the PR metadata. No case or parent is
replaced. [The disposition](ground-truth-dispositions.json) is hashed into the
case bundle and receipts; its query still runs once, but its file hit is null.
The original listed file remains in the case metadata for audit.

All 45 assigned cases remain in execution/error/missingness counts. File any/all
hits at ranks 1/3/5 have **44 evaluable cases and one missing ground truth**.
An empty ground truth never counts as an automatic all-target success. Callable
any hits at rank 5 use only cases with mapped parent declarations; declaration
matched/total counts and unmapped cases stay visible. Exact file and declaration
start/end lines must match, not just names. A 4,000-character default answer can
return fewer than five candidates; the actual count is retained.

`reading` stays in the engine's native character units, including `exact=false`
when the returned count is an upper bound. Missing counters stay missing. Answer
size versus complete referenced-file size is not measured agent behavior, token
saving or proof of correctness. This endpoint evaluates localization of a
historical fix, not whether that fix is unique or an agent can correct the bug.

The selection rule was registered on `2cfb459`. After the owner's pause, decision
`riprendi-giro-3` resumes preparation and measurement on main `aa1af2f`.
[baseline-engine.json](baseline-engine.json) pins that engine's full commit,
script/catalog hashes and parser versions. Selection criteria and order are
unchanged. The earlier partial preparation produced no historical-case query;
all ground truth is prepared again before the first measurement.
Individual baseline results remain private until the coordinator declares the
L2 engine candidate frozen, so its author receives no per-case feedback.


## Frozen baseline and candidate

The single default-budget main measurement is retained unchanged in
[baseline.json](baseline.json). The [comparison receipt](comparison.json)
recounts all three runs, and [RESULTS.md](RESULTS.md) reports every assigned case,
paired gains and regressions, missingness and native reading counters. The
coordinator declared L2 frozen in
`l2-freeze-bb6da5e` before any L2 query. [candidate-engine.json](candidate-engine.json)
pins commit `bb6da5e4072f9d585d4d01c39d0c2d48a0975dfa`, snapshot
`ceab8d0784e93f9746c8ef61d735cc28813d3c49119edaf03ffcc8493114168c`,
all engine hashes and the two budgets prescribed before measurement. All 734
tracked candidate files were verified against that commit before execution.
No result from this additional bank informed the candidate's development.

## Rebuild the comparison without another query

Regenerate the committed comparison from the saved receipts, candidate spans,
ground truth and engine pins. This needs no cache or skill-root argument and
does not run `locate`, project code, a model, Git or network operations:

```sh
.venv/bin/python -I benchmarks/locate_bench.py compare \
  --output /tmp/new-locate-comparison.json
cmp benchmarks/locate-bench/comparison.json /tmp/new-locate-comparison.json
```

Choose a new output path: existing files are refused. The generated JSON is
byte-identical to the committed comparison, which is not read as an input.
This action reproduces only this frozen 45-case bank, with its fixed receipt
filenames, baseline budget of 4,000 and candidate budgets of 4,000 and 5,000.
The command validates the recorded case/rule/disposition hashes, engine pins,
case order and all saved scores and summaries before writing. The baseline
receipt is bound to its hash in `candidate-engine.json`; the two candidate
receipts are bound to the SHA-256 values published in `RESULTS.md` and pinned
in `locate_bench.py`. It reconstructs
paired changes and per-repository summaries without changing any receipt or
ground-truth file.

`tool_sha256` identifies the historical measurement runner and is checked against
the receipts and candidate pin, rather than the current generator's source.
Likewise, `baseline_budget=4000` comes from the frozen protocol above: the old
baseline receipt records `defaults=true` and no numeric override. Current engine
defaults and installed parser versions are not consulted.
