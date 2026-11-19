# Live pilots

## Three repositories, 23 historical bugs: equal success counts; no aggregate input-token saving

Run on **14 November 2026**, under the [study protocol](LIVE_PROTOCOL_STUDY.md) accepted
before any live outcome. The cohort is **eight Django, eight typescript-eslint and seven
Prometheus bugs**. The prospective 1,500-PR bound yielded seven Go cases; the eight-per-repository
target was not met, and the previously authorized minimum of 20 permits this 23-case study.
The [selection ledger](study-cases/selection-ledger.json) keeps all candidate dispositions;
the [case manifest](study-cases/cases.json) freezes issue text, historical parents, selectors
and hashes. No cases were replaced after results were observed.

**All 46 assigned paths finished**, using **138 CLI starts** (92 Codex, 46 Claude), with no
retry, repair round or provider substitution. Claude reported **7.124895 USD** against the
30 USD cap, with no missing costs; Codex used account quota. No stop rule fired, no path is
missing, and cleanup was confirmed for every path. Costs are CLI reports, not invoices.
Each review stayed within its 0.60 USD cap, and every model call within 600 seconds.

**Primary, as recorded: one successful bug per arm (1/23).** The only successful pair is
Prometheus #18631. A separately authorized offline correction to the structural test-retention
guard yields **four per arm (4/23)**, adding the same three TypeScript bugs to each arm.
Neither scoring has a discordant successful pair. This run provides no evidence of improved
workflow success or aggregate input-token savings from graph access. A failed workflow is
not necessarily an incorrect edit: review, hidden tests and preservation are independent gates.

Public evidence: [original receipt](study-live-original.json),
[separate offline rescore](study-live-rescored.json),
[localization receipt](study-localization.json), and
[execution/correction/load provenance](study-provenance.json). Raw streams, prompts, private
paths, session IDs, detailed test output, working trees and index databases stay private.

### What was compared

Both arms received the original issue, historical parent code, the same inventory and the
same public-test command, with literal `text_search` and `cg_read`. The assisted arm also
received code-graph MCP tools. Plan, edit and review were separate calls; neither model
could run shell commands, tests or network requests. The common host workflow indexed,
verified and ran frozen tests for both arms. This is an **evidence-access ablation inside
that workflow**, not a comparison with an unrestricted shell agent or persistent chat.
All tracked parent `.y` grammars were writable in both Go arms, as agreed before live calls;
they were not code-graph localization targets.

Engine **`580cd6b3cb1ac03674fc0fc021d92603619740ac`**, tree-sitter 0.25.2 /
language-pack 0.13.0, with the [34-file engine manifest](study-cases/engine-freeze.json).
The executed harness is **`774651080dec9e8a4637f56a6e243666e4b37d4e`**; engine,
case and harness hashes are in the original receipt. Clients were `codex-cli 0.169.0`
and Claude Code 2.1.325, with no model override. Claude reported `claude-opus-5-5`;
the captured Codex events did not expose a resolved model identifier, which stays null.
Repositories were interleaved in the fixed protocol order and the first arm alternated
by pair. Dependencies were prepared once outside the product environment; native checks
ran offline in disposable host copies.

### Original and corrected classification, every path

`S` = text search + read; `G` = the same plus graph access. `pass` requires all gates,
integration and another hidden-test pass. `fail` is a completed workflow that did not meet
all those conditions. Preservation is shown **original → corrected**. Other failed gates
are unchanged: `H` = historical hidden tests, `P` = full public-test run, `V` = host
verification, `R` = independent review, `A` = Python top-level API guard. `H(a/b)` gives
passed/selected hidden cases; native errors and full-module totals remain in the JSON.
A `no` preservation value also blocks success, independently of the last column.

| Historical fix PR | Arm | As recorded | Offline correction | Tests preserved | Other failed gates |
|---|---|---|---|---|---|
| [django #20959](https://github.com/django/django/pull/20959) | S | fail | fail | yes → yes | R(changes) |
| [django #20959](https://github.com/django/django/pull/20959) | G | fail | fail | yes → yes | R(changes) |
| [typescript-eslint #13062](https://github.com/typescript-eslint/typescript-eslint/pull/13062) | G | fail | fail | no → yes | H(12/14) |
| [typescript-eslint #13062](https://github.com/typescript-eslint/typescript-eslint/pull/13062) | S | fail | fail | no → yes | H(12/14) |
| [prometheus #19403](https://github.com/prometheus/prometheus/pull/19403) | S | fail | fail | yes → yes | H(0/1), R(reject) |
| [prometheus #19403](https://github.com/prometheus/prometheus/pull/19403) | G | fail | fail | no → yes | H(0/1), R(changes) |
| [django #20837](https://github.com/django/django/pull/20837) | G | fail | fail | yes → yes | H(0/1), R(changes) |
| [django #20837](https://github.com/django/django/pull/20837) | S | fail | fail | yes → yes | H(0/1) |
| [typescript-eslint #13058](https://github.com/typescript-eslint/typescript-eslint/pull/13058) | S | fail | pass | no → yes | — |
| [typescript-eslint #13058](https://github.com/typescript-eslint/typescript-eslint/pull/13058) | G | fail | pass | no → yes | — |
| [prometheus #19368](https://github.com/prometheus/prometheus/pull/19368) | G | fail | fail | no → no | — |
| [prometheus #19368](https://github.com/prometheus/prometheus/pull/19368) | S | fail | fail | no → no | — |
| [django #20696](https://github.com/django/django/pull/20696) | S | fail | fail | no → no | H(0/1) |
| [django #20696](https://github.com/django/django/pull/20696) | G | fail | fail | yes → yes | H(0/1) |
| [typescript-eslint #12995](https://github.com/typescript-eslint/typescript-eslint/pull/12995) | G | fail | pass | no → yes | — |
| [typescript-eslint #12995](https://github.com/typescript-eslint/typescript-eslint/pull/12995) | S | fail | pass | no → yes | — |
| [prometheus #18631](https://github.com/prometheus/prometheus/pull/18631) | S | pass | pass | yes → yes | — |
| [prometheus #18631](https://github.com/prometheus/prometheus/pull/18631) | G | pass | pass | yes → yes | — |
| [django #20395](https://github.com/django/django/pull/20395) | G | fail | fail | yes → yes | H(0/1) |
| [django #20395](https://github.com/django/django/pull/20395) | S | fail | fail | yes → yes | R(changes) |
| [typescript-eslint #12991](https://github.com/typescript-eslint/typescript-eslint/pull/12991) | S | fail | fail | yes → yes | H(1/2) |
| [typescript-eslint #12991](https://github.com/typescript-eslint/typescript-eslint/pull/12991) | G | fail | fail | yes → yes | H(1/2), R(changes) |
| [prometheus #18401](https://github.com/prometheus/prometheus/pull/18401) | G | fail | fail | yes → yes | H(0/1), R(changes) |
| [prometheus #18401](https://github.com/prometheus/prometheus/pull/18401) | S | fail | fail | yes → yes | H(0/1), R(changes) |
| [django #20288](https://github.com/django/django/pull/20288) | S | fail | fail | no → no | A |
| [django #20288](https://github.com/django/django/pull/20288) | G | fail | fail | no → no | A |
| [typescript-eslint #12977](https://github.com/typescript-eslint/typescript-eslint/pull/12977) | G | fail | pass | no → yes | — |
| [typescript-eslint #12977](https://github.com/typescript-eslint/typescript-eslint/pull/12977) | S | fail | pass | no → yes | — |
| [prometheus #18375](https://github.com/prometheus/prometheus/pull/18375) | S | fail | fail | no → no | — |
| [prometheus #18375](https://github.com/prometheus/prometheus/pull/18375) | G | fail | fail | no → no | — |
| [django #20183](https://github.com/django/django/pull/20183) | G | fail | fail | no → no | — |
| [django #20183](https://github.com/django/django/pull/20183) | S | fail | fail | no → no | — |
| [typescript-eslint #12974](https://github.com/typescript-eslint/typescript-eslint/pull/12974) | S | fail | fail | no → yes | V, H(0/3), R(changes) |
| [typescript-eslint #12974](https://github.com/typescript-eslint/typescript-eslint/pull/12974) | G | fail | fail | no → yes | V, H(0/3), R(changes) |
| [prometheus #18323](https://github.com/prometheus/prometheus/pull/18323) | G | fail | fail | no → no | — |
| [prometheus #18323](https://github.com/prometheus/prometheus/pull/18323) | S | fail | fail | no → no | — |
| [django #19645](https://github.com/django/django/pull/19645) | S | fail | fail | yes → yes | H(0/1) |
| [django #19645](https://github.com/django/django/pull/19645) | G | fail | fail | yes → yes | H(0/1) |
| [typescript-eslint #12953](https://github.com/typescript-eslint/typescript-eslint/pull/12953) | G | fail | fail | no → yes | H(11/13) |
| [typescript-eslint #12953](https://github.com/typescript-eslint/typescript-eslint/pull/12953) | S | fail | fail | no → yes | H(10/13) |
| [prometheus #17312](https://github.com/prometheus/prometheus/pull/17312) | S | fail | fail | no → no | H(0/2) |
| [prometheus #17312](https://github.com/prometheus/prometheus/pull/17312) | G | fail | fail | no → no | H(0/2), R(changes) |
| [django #19361](https://github.com/django/django/pull/19361) | G | fail | fail | yes → yes | H(0/2), R(changes) |
| [django #19361](https://github.com/django/django/pull/19361) | S | fail | fail | yes → yes | H(0/2) |
| [typescript-eslint #12941](https://github.com/typescript-eslint/typescript-eslint/pull/12941) | S | fail | fail | no → yes | V, H(2/3), P, R(changes) |
| [typescript-eslint #12941](https://github.com/typescript-eslint/typescript-eslint/pull/12941) | G | fail | fail | no → yes | V, H(2/3), P, R(changes) |

All 46 plans, edits and reviews terminated normally. Host verification was green on 42
paths; 31 reviews accepted, 14 requested changes and one rejected. The untouched external
public check was green on 44 paths; selected public cases alone passed on all 46, but the
complete Vitest module run for TypeScript #12941 found another failure in both arms and
was correctly kept red. Hidden checks were green on 11 source-only and 10 assisted edits.
The original preservation guard rejected 14 paths per arm; the corrected guard rejects
seven source-only and six assisted paths. Python's API guard rejects both #20288 edits.
These counts overlap and are not additive failure categories. No plan/edit startup,
model timeout, quota stop, missing review, out-of-scope candidate or uncertain cleanup
was recorded. The original two integrations and all six additional offline integrations
passed their post-integration hidden checks.

### Why there are two scorings

The original guard fingerprints whole test statements. Adding new cases inside a
RuleTester `valid`/`invalid` array changes the fingerprint even when every original case
remains. On TypeScript #13058, for example, the saved edit adds 138 lines and removes none,
but the old guard rejects it. The original receipt and classifications were not rewritten.

Decision **`studio-ricalcolo-retention`**, registered at **13:03:49 UTC after 17 of the 46
path results had been observed**, fixed this secondary rule before the remaining 29:
an original test statement is preserved when the same syntax tree remains, allowing only
inserted elements in literal arrays, objects, lists, slices or composite literals. Original
elements retain order and multiplicity; statements, assertions, headers and clauses may
not be removed or changed. Every other gate remains unchanged. This is a **post-observation
sensitivity analysis**, not a prospectively fixed replacement primary endpoint.

The separately reviewed corrector **`b6a2ac3`** ([source](study_rescore.py)) was approved
before application and applied to **all 46 saved candidates in both arms**. No case was
undecided. Fifteen preservation decisions change from no to yes, but nine still fail other
gates; only both arms of TypeScript **#13058, #12995 and #12977** become successful.
Those six candidates were merged into new isolated host copies, with candidate/integrated
Git tree equality checked and hidden tests rerun successfully. The two original successes
reuse their observed integrations. No model, prompt, candidate or original timing changed.
The JSON retains both rows, per-path integration provenance, scorer hashes and separate
offline durations. Remaining preservation failures are not assumed semantically wrong:
the guard is a finite structural check and cannot establish arbitrary semantic equivalence.

The predeclared paired analysis gives, as recorded, **both 1 / source-only 0 / graph-only 0 /
neither 22**, difference 0/23 and exact McNemar **p=1**. Offline sensitivity gives
**both 4 / source-only 0 / graph-only 0 / neither 19**, also p=1. Both retain 23 complete
pairs. The stratified 10,000-replicate bootstrap, seed 20261113, returns **[0, 0]** for both
success-rate differences because every observed within-pair difference is zero. This
degenerate empirical interval **does not establish equivalence, a tight population bound
or absence of a benefit on other tasks**. Repository breakdowns are exploratory.

### Native tokens, by phase

Each entry below is summed from the native counter across 23 calls. Codex `input_tokens`
already includes its cached input; the cached column is a subset, not an addition.
Claude reports uncached input, cache reads and cache creation separately. In particular,
its 158/166 uncached tokens are **not its total prompt volume**. Missing fields remain null;
different providers' counters/tokenizers are not pooled into a cost or token-saving claim.

| Client / phase | Arm | Input tokens | Cached input (included) | Cache read (separate) | Cache creation (separate) | Cache write field | Output tokens |
|---|---|---:|---:|---:|---:|---:|---:|
| codex / plan | S | 6,689,936 | 5,395,968 | null | null | 0 | 35,096 |
| codex / plan | G | 9,703,925 | 8,226,816 | null | null | 0 | 42,392 |
| codex / edit | S | 4,165,697 | 3,604,864 | null | null | 0 | 49,383 |
| codex / edit | G | 4,355,022 | 3,813,760 | null | null | 0 | 47,408 |
| claude / review | S | 158 | null | 987,848 | 298,791 | null | 42,741 |
| claude / review | G | 166 | null | 1,328,645 | 304,224 | null | 49,068 |

| Codex input comparison (G versus S) | Aggregate ratio | Median paired ratio | Median paired difference | Pairs with both counters |
|---|---:|---:|---:|---:|
| plan | 1.4505 | 1.4184 | 136,975 | 23/23 |
| edit | 1.0454 | 0.9489 | -7,045 | 23/23 |

The assisted plan uses **45.05% more aggregate input**. Edit input is **4.54% higher in
aggregate**, although the median paired edit ratio is **5.11% lower**. These are different
summaries; the lower median does not demonstrate an aggregate saving. Claude's complete
cache components are shown above rather than interpreting its uncached-only ratio as
total review input. All 23 pairs have the native input counters used in the paired
calculation; detailed output/reasoning/cache fields are retained per call in the receipts.
This does not measure savings in subsequent turns of a persistent conversation.

### Tool use and file reading

| Phase | Arm | Successful tools | Failed tools | Distinct explicit file reads, summed per call | Returned file mentions, summed per call |
|---|---|---:|---:|---:|---:|
| plan | S | 311 | 13 | 115 | 125 |
| plan | G | 347 | 32 | 107 | 183 |
| edit | S | 177 | 22 | 78 | 16 |
| edit | G | 207 | 19 | 74 | 3 |
| review | S | 98 | 1 | 41 | 29 |
| review | G | 100 | 4 | 42 | 18 |

All 23 assisted plans attempted graph tools: `cg_locate` succeeded on **18 of 25 calls**,
`cg_search` on **52 of 53**, `cg_impact` on **22 of 22**, `cg_verify_plan` on **21 of 21**,
`cg_find` on **6 of 6**, and `cg_source` on **1 of 1**. **41 search responses** contained
graph witnesses; this is a count of calls, not bug pairs. During editing, one assisted
call used `cg_source`; the other recorded evidence calls were reads/text search. Neither
reviewer arm invoked graph-specific tools. Source-only plan/edit used only `cg_read`
and `text_search`; review telemetry also includes the client's structured-output tool.
The assisted reviewer additionally used native Read/Grep/Glob. The table includes these
client tools and structured output, and keeps their failures rather than counting starts
as successful evidence. No tool remains pending in the saved native outcomes.

Distinct relative paths read, deduplicated within each full workflow and then summed,
are **133 S / 123 G**; deduplicating per call instead gives the phase totals above.
Returned path mentions are separate and do not prove a read. Tool success, a graph witness,
or a smaller read count does not prove how the model reasoned or that evidence caused its
fix. All 138 call metadata records were redecoded from the private native streams/events
and match the public receipt, including costs, cache counters, tools, paths and durations.

### Time and cost boundaries

Times below are descriptive sums over 23 paths per arm. Setup ends before `flow.create`;
workflow time runs from creation through host scoring, any original integration and drain,
excluding setup. A workflow stage can include host checks as well as client time. Separate
native call durations are shown so they are not mistaken for whole-workflow time.

| Measured seconds | S | G |
|---|---:|---:|
| Host setup | 91.119 | 90.630 |
| Original workflow, including scoring/cleanup | 4,603.796 | 4,806.094 |
| Workflow stage: plan | 1,358.894 | 1,672.502 |
| Workflow stage: edit | 1,673.911 | 1,566.903 |
| Workflow stage: verify | 452.046 | 453.208 |
| Workflow stage: review | 703.013 | 703.812 |
| Native client call: plan | 1,115.674 | 1,434.540 |
| Native client call: edit | 1,496.625 | 1,384.590 |
| Native client call: review | 682.753 | 684.288 |

Claude reported **3.4433496 USD S / 3.6815450 USD G**, rounded to **7.124895 USD** in the
study total. The original receipt keeps every call; unknown-cost reviews are zero. Offline
correction/merge/retest durations are separate and excluded from all live comparisons.

**Concurrent-load disclosure:** the coordinator reported heavy host activity during
approximately **12:04–12:16 and 12:28–12:38 UTC**. The provenance file identifies the
**27 overlapping workflow phases** with their UTC intervals (end minute included). Keep
those observations: no path was discarded or rerun. Caches, latency and desktop load were
not controlled, so these times do not establish an isolated speed effect. Model outcomes,
native counters and CLI costs are retained as actually observed; their arithmetic is not
a claim that experimental conditions were otherwise controlled.

### Deterministic localization, no models

Before live calls, `cg locate` was run once on each of the **23 historical parents**,
using the original title/body, the same frozen engine and its defaults (limit 5,
4,000-character answer budget), without query tuning. Every response was valid, with no
failed query; **13 answers returned three candidates and ten returned four** under that
budget. The historical production diff supplies ground truth, not the agent's chosen edit.

| Endpoint | Any target hit | All targets hit | Denominator |
|---|---:|---:|---:|
| File among first 1 | 7 | 5 | 23 bugs |
| File among first 3 | 11 | 9 | 23 bugs |
| File among first 5 | 11 | 9 | 23 bugs |
| Callable among first 5 | 6 | — | 19 bugs with mapped parent declarations |

Seven of **52 mapped target declarations** were matched; **four bugs have no mapped
parent callable** and are explicitly outside the callable denominator, not the file
denominator. The median `reading.returned_chars` is **3,630**, and median `reading.file_chars`
is **40,882**. These are characters in the answer and in the complete referenced files,
not tokens, required reading, observed agent savings or a percentage of the whole repository.
No response here reports `reading.exact=false`; the engine's upper-bound flag is preserved
when present. Comparing separate medians does not compute an aggregate savings ratio.

The first nine localization rows ran without an exclusive CPU reservation after a failed
reservation attempt was overlooked. The same process was then paused for **84.03 seconds**,
the reservation acquired and the process resumed; the tenth timing includes that pause.
All 23 responses were retained, with no query restart or repeat. No localization timing
claim is made. Full candidate payloads were not retained; the receipt preserves the
computed hit metrics, candidate counts, reading counters and input/engine hashes.

### Interpretation and limits

The graph was used during planning and found a historical target file in 11/23 model-free
queries within the first five candidates. That capability did **not** translate into a
different successful bug pair or an aggregate input saving in this run. The preservation
false positives materially depress the primary absolute success counts; the separately
reported correction increases both arms equally. Both classifications must remain visible.

Only 23 selected public bugs from three repositories, one run per arm, fixed interleaving,
uncontrolled caches/load and possible memorization of public fixes limit generalization.
Issue text, inventories and public-test modules can already reveal location. Hidden
historical cases and a finite retention/API guard are not a proof of general correctness.
Reviews requesting changes and structural-preservation failures are not automatically
evidence that an edit fails the real bug. Selection/qualification failures remain in the
ledger; there was no post-outcome retuning, case weighting, discarded pair, new model run
or replacement primary endpoint. The offline correction is the explicitly disclosed
exception for a scoring defect, with the original result preserved.

## Real repository (SymPy): 3 of 4 with code-graph, 2 of 4 without; no token saving

Run on 13 November 2026 under the [real-repository protocol](LIVE_PROTOCOL_REAL.md), fixed and reviewed
before any live call. Four historical SymPy bugs were selected by a rule recorded before looking at
them (the first four eligible pull requests merged before 1 November 2026); each agent received the
original issue text and the code as it was before the fix, and the fix's own tests were kept hidden
as the acceptance check. Codex wrote, Claude reviewed. **Both arms had the same literal text search
and file reading**; the assisted arm also had the code-graph tools. Engine `16698fb`, 24 live starts,
Claude reported **3.47 USD** of the 5 USD cap. [Receipt with recorded and corrected scoring](real-pilot-results.json).

| Bug (issue) | Arm | Outcome | Workflow s² | Codex plan input | Codex edit input | Claude review input | Claude USD |
|---|---|---|---:|---:|---:|---:|---:|
| [#30884](https://github.com/sympy/sympy/issues/30884) negated interval in `simplify` | source-only | **success** | 272 | 258,967 | 254,815 | 127,380 | 0.41 |
| [#30884](https://github.com/sympy/sympy/issues/30884) negated interval in `simplify` | graph-assisted | **success** | 292 | 304,248 | 260,162 | 182,783 | 0.46 |
| [#9154](https://github.com/sympy/sympy/issues/9154) `ln(p) > 1` for an odd prime | graph-assisted | hidden test failed | 336 | 330,187 | 383,868 | 297,911 | 0.53 |
| [#9154](https://github.com/sympy/sympy/issues/9154) `ln(p) > 1` for an odd prime | source-only | hidden test failed | 298 | 439,405 | 300,708 | 223,123 | 0.46 |
| [#26652](https://github.com/sympy/sympy/issues/26652) LaTeX of a wide `Array` | source-only | hidden test failed | 263 | 193,615 | 228,493 | 130,831 | 0.38 |
| [#26652](https://github.com/sympy/sympy/issues/26652) LaTeX of a wide `Array` | graph-assisted | **success**¹ | 293 | 250,931 | 265,895 | 141,535 | 0.39 |
| [#26503](https://github.com/sympy/sympy/issues/26503) NaN comparison in `integrate` | graph-assisted | **success** | 350 | 462,990 | 360,156 | 144,302 | 0.43 |
| [#26503](https://github.com/sympy/sympy/issues/26503) NaN comparison in `integrate` | source-only | **success** | 329 | 200,887 | 318,194 | 181,486 | 0.41 |

¹ Recorded as failed by a scoring defect: the test-retention check compared only top-level statements,
so assertions the agent *added* inside an existing `for` loop looked like a weakened test. The protocol
only requires existing statements to stay, and all 17 did. The check was corrected and the eight saved
candidates were re-scored offline, with no new model call; this is the only changed classification,
and its hidden test also passes after integration. Both scorings are in the receipt.

² Times of the original live run; the offline re-scoring and its integration are not included.

| Arm | Bugs fixed | Workflow time | Codex input, plan / edit | Claude review input | Claude USD |
|---|---:|---:|---:|---:|---:|
| Text search + read | 2 of 4 | 1,162 s | 1,092,874 / 1,102,210 | 662,820 | 1.66 |
| Text search + read + code-graph | **3 of 4** | 1,270 s (+9%) | 1,348,356 (+23%) / 1,270,081 (+15%) | 766,531 (+16%) | 1.81 |

**What this shows, and what it does not.**

- *Outcome.* The only difference is one bug: on #26652 the assisted fix passed the hidden historical test
  and the text-only fix did not. Two bugs were fixed by both arms and one by neither. One case out of four
  is a signal worth a larger study, not evidence of a general improvement.
- *Tokens and time.* Code-graph did **not** save tokens in later calls: assisted input was higher in every
  phase (plan +23%, edit +15%, review +16%) and the workflow took 9% longer. Both arms read a similar number
  of source files explicitly (36 and 38, summing the distinct files each call read across the three phases).
- *A miss both arms shared.* The historical fix for #9154 changed two files (`core/add.py` and
  `core/exprtools.py`); both arms changed only `exprtools.py` and both failed the hidden test. Impact
  information was available to the assisted author but did not lead it to the second file.
- *Limits.* Four bugs from one public project, one run per arm, fixed order within a pair, provider caches
  not controlled; models may have seen these public fixes. The issue text and the public test modules
  already point near the code. This compares evidence access inside code-graph's own workflow, not
  code-graph against an agent with a full shell.

## Second pilot (1.8): all eight paths succeed; no measured benefit on these small tasks

Run on 13 November 2026 under the [1.8 protocol](LIVE_PROTOCOL_1.8.md), fixed before any
live call, on engine commit `5106aa5` (installed clients `codex-cli 0.167.1` as author and
Claude Code `2.1.323` as reviewer, no model override). The later Dart grammar evaluation
does not touch these Python fixtures. [Sanitized receipt](live-pilot-1.8.json).

**Every path completed**: 24 live starts, eight plans, edits, verifications, independent
Claude reviews (all *accept*) and integrations. Every candidate passes the untouched external
public tests and the held-out cases, keeps the original test assertions and public
signatures, and adds a regression test that fails on the original sources. Claude reported
**0.68 USD** in total (cap 5 USD); no stop rule fired.

| Task | Arm | Outcome | Review | Workflow s | Codex input (cached) | Codex output | Claude USD (CLI) | Successful code-graph tool calls (author) | Failed tool calls (author) |
|---|---|---|---|---:|---:|---:|---:|---|---:|
| refund | source-only | success | accept | 59.8 | 172,942 (133,888) | 1,416 | 0.098 | cg_read 15 | 1 |
| refund | graph-assisted | success | accept | 72.7 | 203,513 (167,808) | 1,971 | 0.123 | cg_impact 2, cg_read 8, cg_search 2, cg_verify_plan 2 | 2 |
| falsy | graph-assisted | success | accept | 82.2 | 224,683 (188,672) | 2,080 | 0.073 | cg_impact 2, cg_read 9, cg_search 2, cg_source 4, cg_verify_plan 2 | 4 |
| falsy | source-only | success | accept | 56.7 | 172,741 (143,872) | 1,262 | 0.068 | cg_read 14 | 1 |
| discount | source-only | success | accept | 71.9 | 158,630 (132,480) | 1,543 | 0.070 | cg_read 19 | 1 |
| discount | graph-assisted | success | accept | 79.0 | 208,157 (169,600) | 2,054 | 0.079 | cg_impact 2, cg_read 10, cg_search 2, cg_source 1, cg_verify_plan 2 | 3 |
| reserve | graph-assisted | success | accept | 100.8 | 273,243 (232,192) | 2,590 | 0.094 | cg_impact 2, cg_read 12, cg_search 2, cg_source 5, cg_verify_plan 2 | 4 |
| reserve | source-only | success | accept | 74.5 | 169,535 (141,184) | 1,748 | 0.074 | cg_read 18 | 1 |

| Arm | Successful paths | Workflow time | Codex input tokens | Codex output tokens | Claude USD |
|---|---:|---:|---:|---:|---:|
| Source-only (`cg_read`) | 4 / 4 | 263.0 s | 673,848 | 5,969 | 0.311 |
| Graph-assisted | 4 / 4 | 334.7 s (+27%) | 909,596 (+35%) | 8,695 (+46%) | 0.370 |

**What this shows.** On these four fixtures, both arms fixed every task, including the two
tasks whose defect sits below the entry point next to a decoy. The assisted author did use
the graph — each of its eight calls ran `cg_search`, `cg_impact` and `cg_verify_plan`, and the
eight searches returned graph witnesses — but that added tool round-trips, time and input
tokens without changing the outcome. The fixtures have 10 to 13 Python files each (three to seven task files, their tests and six
unrelated helper modules), so reading every file is cheap: they test whether the agent can use the evidence, not whether the graph
saves reading in a large codebase. This pilot gives **no evidence of a benefit** and some
evidence of overhead on small projects. Four task pairs, fixed order within a task, warm
caches and provider latency still prevent any general conclusion.

Failed tool calls (for example a `cg_read` of a path that does not exist) are counted
separately: 4 in the source-only arm and 13 in the assisted arm, all kept in the receipt.
Token counts are native client reports (Codex `input_tokens` already include cached input);
Claude USD values are the CLI's own `total_cost_usd`, not invoices.

## First pilot (1.7.0): inconclusive because of account quota

The fixed two-pair pilot stopped at the Claude account's weekly limit. **No path
completed independent review or integration.** This is not evidence of better
correction quality, fewer errors, lower token use or faster work with code-graph.
The two refund edits pass the frozen checks, but a passing author edit cannot replace
the missing review. The falsy-configuration comparison was not completed.

The [protocol](LIVE_PROTOCOL.md) was fixed before live calls. The exact executed
runner is preserved in commit `aad270a35e48a79aeb69b5393b197b5aa775f28e`;
its source engine is identical to `167bd3c` and reports 1.6.5. The later 1.7.0 label
does not constitute another experiment. The [sanitized receipt](live-pilot-1.7.0.json)
binds script/task hashes, versions, native counters, stages and every scheduled path.
Raw client logs and temporary worktrees stay private.

### Observed paths, 12 November 2026

| Task / arm | Author plan/edit and host verification | Independent review | Integration |
| --- | --- | --- | --- |
| Refund, source-only | Completed; original public tests and holdouts pass | Failed: Claude weekly limit | Not run |
| Refund, graph-assisted | Completed; original public tests and holdouts pass | Failed: same weekly limit | Not run |
| Falsy, graph-assisted | Planning cancelled after manual quota stop | Not run | Not run |
| Falsy, source-only | Not started after quota stop | Not run | Not run |

Original API signatures and test assertions are retained in both refund edits;
only the declared source/test files changed. Post-stop offline scoring used the
same frozen criteria, with no model calls: each new regression method fails on the
original sources and passes on its candidate. Both candidates pass the untouched
external public suite and the independent held-out cases. These checks qualify
the observed edits, not the missing model review or full workflow outcome.

### Native measurements

Installed clients: `codex-cli 0.167.1` and Claude Code `2.1.322`, using their signed-in
accounts and no model override. Codex did not expose a resolved model identifier in
the captured native events; it remains null. Claude reported `claude-opus-5-5` in
metadata but generated zero review tokens. An identifier does not prove execution.

| Completed Codex call | Input tokens | Cached input (included) | Output tokens |
| --- | ---: | ---: | ---: |
| Refund source-only, plan | 49,823 | 32,768 | 643 |
| Refund source-only, edit | 122,687 | 111,104 | 763 |
| Refund assisted, plan | 74,650 | 52,352 | 989 |
| Refund assisted, edit | 145,223 | 127,744 | 932 |

The source-only workflow recorded 56.512 s and the assisted workflow 88.198 s through
the failed review/cleanup; neither is time to successful integration. This one
observed pair has more elapsed time and reported input tokens with assistance;
it cannot establish a general causal effect. Task size, warm caches, fixed order,
provider latency and an uncontrolled desktop limit comparisons. Token counts are
native client reports, not character estimates, unique source tokens or invoices.

The two failed Claude calls each report zero input/output/cache tokens and
`cli_reported_cost_usd=0`; these are CLI reports, not billing reconciliation.
The cancelled Codex call has no final usage: its counts remain null. Totals across
all seven starts would therefore be incomplete. Native cache-read/create counters
remain distinct from the workflow's normalized usage fields in the JSON.
Per-call client durations are host measurements recovered from the wrapper's `end`
events, including 14.447 s for the cancelled call. Its total task/stage duration was not saved
and remains absent; client time is not interchangeable with full workflow time.

The refund assisted plan/edit each completed `cg_search` with a returned graph
witness, plus impact/plan checks. The source-only path used only `cg_read` over MCP.
Recorded MCP outcomes retain failures too: one read error in the source-only edit
and two in the assisted edit. Tool success and returned evidence do not demonstrate
how the model reasoned or whether evidence improved its correction.

### Stop-rule defect and containment

The executed v5 guard recognized quota/usage/rate-limit phrases but missed the
client's **weekly limit** message. Consequently four further starts occurred after
the first quota failure before the operator detected the message and interrupted
the pilot. This is a harness defect, not a successful automatic quota stop.
The 12-start ceiling was not exceeded: seven attempts occurred, consisting of four
completed Codex calls, two zero-token Claude failures and one cancelled Codex call.
No client process remained after cancellation. No failed case was retried, no
provider was substituted and no credits were purchased; the remaining path was
never started.

The published harness now recognizes weekly/monthly/daily limits, with an offline
regression for the exact observed message. That fix was **not** used for a rerun or
to replace the observed failures. The interrupted path was reconciled from its raw
run logs and cancelled workflow state; its cleanup verification is labelled manual,
and missing timing/usage stays null. A complete paired comparison through review
and integration requires a separately scoped future experiment with available quota.

### Reproduction boundary

`live_agent.py` defaults to a dry-run with zero live starts:

```sh
.venv/bin/python -I benchmarks/live_agent.py --output /tmp/pilot-dry.json
```

`--live` explicitly starts installed clients, consumes account quota and requires a
new `--work-dir`. It retains the protocol's 12-start/180s limits and 1 USD Claude cap
per review (at most 4 USD in configured caps). Do not rerun this stopped experiment
to select better outcomes. The execution commit above preserves the original guard;
the receipt separately hashes the corrected published runner.
