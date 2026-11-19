# Locate development log — L2

Resumed by owner decision `riprendi-giro-3`. The starting checkpoint `8b3a532` contained diagnosis
only. Main `aa1af2f` was merged into the lane as `3ee421e` before implementation. The implementation was frozen at
`6c7d166` after the L1 baseline-ready notice and coordinator mandate (ACK event 1724). Later changes
complete this log, generated guide examples and the translation catalog described below; the engine
is unchanged before evaluation.

## Cohort, method and boundary

Only the 23 published bugs in `benchmarks/study-cases/cases.json` are used for development: eight
Django, eight typescript-eslint and seven Prometheus. The separate L1 cohort, its files and its
results have not been inspected or requested. No model is called and no project code is executed.

The offline replay uses the existing parent trees and indexes from the published study. It checks
each index's analysis fingerprint with the frozen `580cd6b` engine, opens it read-only through
`ProjectData`, then injects the locate/search implementation being compared. This isolates retrieval
from indexing; **these are not fresh-index or latency measurements**. Search is unchanged. The
same original issue title/body and the study's `study_localization.score` are used for every run.
Function recall has 19 eligible bugs with mapped production callables, not all 23.

The first full replay exactly reproduced the published baseline: file@3 11/23 (Django 3/8,
typescript-eslint 1/8, Prometheus 7/7), callable@5 6/19, with three candidates in 13 responses and
four in ten. Each replay also verifies its declared JSON character budget and the reading counter (exact, or
explicitly an upper bound). Baseline and A–H use 4,000 unless the row explicitly names another budget.

## Measured variants

Each row is cumulative unless stated otherwise. These are development results, not independent
validation or evidence of agent token savings. Median size includes the full serialized JSON.

| Variant | Fix file in first 3 | Fix callable in first 5 | Five candidates | Median characters |
|---|---:|---:|---:|---:|
| Baseline | 11/23 | 6/19 | 0/23 | 3630 |
| A: 3 × 120, one caller/test | 11/23 | 7/19 | 6/23 | 3724 |
| B: A + code terms / URL cleanup | 11/23 | 7/19 | 2/23 | 3759 |
| C: B + broad explicit names | 10/23 | 4/19 | 3/23 | 3633 |
| D: C, only structured names | 13/23 | 9/19 | 2/23 | 3633 |
| E: D, one declaration per file first | 14/23 | 6/19 | 1/23 | 3685 |
| F: E, ignore ambiguous basenames | 14/23 | 6/19 | 1/23 | 3690 |
| G: F, up to two declarations per file first | 14/23 | 9/19 | 1/23 | 3655 |
| H: G, 2 × 100 + shorter descriptions | 14/23 | 9/19 | 9/23 | 3624 |
| H, budget 5,000 (chosen) | 14/23 | 9/19 | 23/23 | 4045 |
| H, budget 6,000 (rejected) | 14/23 | 9/19 | 23/23 | 4045 |

### A — compact previews

Six source lines of up to 160 characters and two shown callers/tests become three lines of up to
120 characters and one shown caller/test. Counts, source hashes, locations and truncation flags
remain. Callable recall rises by one because an additional candidate fits. This **does not solve
the five-candidate goal alone**: only 6/23 answers retain five. A behavioral fixture with five
ordinary Python handlers fails before the change (four returned) and passes after it.

### B — strong code terms and URL cleanup

Recognize bare kebab-case, scoped rule names, paths and qualified names before plain words; keep
a scoped name's leaf and discard URL hosts/query/hash payloads. A URL ending in a source filename
keeps its path as evidence. This corrects the diagnosed typescript-eslint failure mode: encoded
playground settings previously displaced the requested rule from the 16-term query. Generic
fixtures cover kebab/scoped names and source links; both fail on the earlier implementation.

As a standalone improvement over A, this variant is insufficient: typescript-eslint file@3 rises
from 1/8 to 2/8, Prometheus falls from 7/7 to 6/7, and overall file@3 stays 11/23. Function recall
remains 7/19. Cleaner terms alone do not resolve a rule file whose callable has the generic name
`create`, nor the global lexical candidate cap.

### C–G — explicit indexed-file evidence and diversity

C resolved every code-looking name before the global lexical cap, returning up to three BM25-ranked
declarations per named file. TypeScript file@3 rose to 5/8 and callable@5 to 2/8, but ordinary names
such as `response` and `main`, and code names mentioned as context, displaced useful results elsewhere.
**Rejected:** overall 10/23 files and 4/19 callables, below the baseline.

D restricts file hints to source filenames, indexed paths and compound rule names, and symbol hints
to qualified names. Bare directory names and ordinary words do not count as file evidence. This
reaches 13/23 files and 9/19 callables. A synthetic rule file with a generic `create` declaration is
found even with 55 competing symbols; the earlier implementation fails that fixture. A second
fixture checks a source-link suffix and a qualified symbol.

E puts one declaration per file before any repeats (backfilling when there are too few matching
files). File coverage rises to 14/23 but callable coverage falls to 6/19. **Rejected as too strict:**
correct second declarations in a file are lost. F additionally abstains when a bare basename names
multiple production files; this changes no aggregate metric but avoids arbitrary path promotion.

G relaxes diversification to two declarations per file, then backfills remaining declarations if
needed. It keeps 14/23 file hits and recovers callable hits to 9/19. A fixture demonstrates that five
results are not monopolised by one file, while a one-file project can still return five declarations.

### H — two-line previews

H keeps two source lines of up to 100 characters and shorter limitation/reading descriptions. It
retains the source hash, exact location, explicit truncation, caller/test totals and one shown relation
of each type. At 4,000 characters it keeps five candidates in only 9/23 cases (13 retain four and one
retains three), with no accuracy change from G. **Insufficient as the complete budget solution**;
compare larger budgets explicitly rather than silently claiming that compaction alone fixes it.

### Chosen budget and accuracy limits

At 5,000 characters, all 23 answers contain five candidates: median 4,045, range 3,418–4,825. A 6,000
ceiling produces the exact same candidates and sizes, so the larger ceiling is rejected. The chosen
ceiling costs at most +25% relative to 4,000; the measured median answer is +11.4% larger than the
baseline but contains five rather than three/four candidates. This is a character tradeoff, **not** a
claim about tokens used by an agent or eventual bug-fix success. Two-line windows require follow-up
reads when more source context is needed.

| Repository | Baseline file@3 | Chosen file@3 | Baseline callable@5 | Chosen callable@5 |
|---|---:|---:|---:|---:|
| Django | 3/8 | 3/8 | 2/4 | 2/4 |
| typescript-eslint | 1/8 | 6/8 | 0/8 | 3/8 |
| Prometheus | 7/7 | 5/7 | 4/7 | 4/7 |
| All | 11/23 | 14/23 | 6/19 | 9/19 |

The change improves the totals and the diagnosed TypeScript failure, but **regresses Go file@3 in
two cases**. Explicit references can describe a symptom/helper, and URL noise could previously match
by accident. No per-case exception is added to recover these development cases. These selected
results do not establish generalisation: the separate L1 evaluation must follow an engine freeze.

## Freeze and independent evaluation

Coordinator decision `l2-default-5000` permits only the two CLI/MCP default lines outside the lane's
original owned files. The separate evaluation must run the **same frozen candidate** at both 4,000
and 5,000 against the baseline at 4,000. This separates the ranking change from the budget change.
No holdout case/result is used to choose these variants or to revise the frozen engine.

The initial full gate found two obsolete translation keys for the shortened limitation messages.
Coordinator exception `l2-i18n-catalog` allows only their removal from `engineItalian.exact` and the
two current Italian translations in `manual`, in `assets/workbench-i18n.js`. All other catalog pairs
are unchanged. The six translation tests pass on that catalog; neither tests nor frozen engine are
modified to accommodate the gate.

## Original frozen-candidate confirmation (bb6da5e)

Final replays of the exact frozen implementation at both 4,000 and 5,000 reproduce H above, including
all per-repository totals. All 23 reading counters pass the exact/declared-upper-bound assertion at
both budgets. CLI and MCP defaults agree at 5,000; the real stdio parity fixture also shows the
candidate reduction when 4,000 is requested explicitly. Guide examples and references are regenerated.

`search.py` is byte-for-byte unchanged. Both existing search benchmarks were rerun: all hit lists,
including their order, are identical to the published receipts in both lexical and hybrid modes.
Retrieval covers 8 queries / 15 relevant definitions; paraphrases 11 queries / 22 relevant definitions.
Hybrid recall@5 is 1.0 in both. These are existing development fixtures, not the separate L1 evaluation.

The engine selftest is 25/25 TRUST. Native full-suite execution and its exact-candidate coverage are
recorded in the Squadra proof attached to delivery; this log does not substitute for that proof.
No repository/case-specific rule is introduced and no L1 evaluation result has been opened.

### Per-case development comparison

`yes` means at least one mapped production fix file/callable was returned at the stated rank; `—`
means the case has no mapped callable target. The chosen column uses the new default of 5,000.

| Published development case | Baseline file@3 | Chosen file@3 | Baseline callable@5 | Chosen callable@5 | Chosen candidates | Characters |
|---|---|---|---|---|---:|---:|

| django-20959 | no | no | — | — | 5 | 4213 |
| django-20837 | no | no | — | — | 5 | 4036 |
| django-20696 | yes | yes | yes | yes | 5 | 4295 |
| django-20395 | no | no | no | no | 5 | 4456 |
| django-20288 | yes | yes | yes | yes | 5 | 3779 |
| django-20183 | yes | yes | — | — | 5 | 4281 |
| django-19645 | no | no | — | — | 5 | 4047 |
| django-19361 | no | no | no | no | 5 | 3990 |
| typescript-eslint-13062 | no | yes | no | yes | 5 | 4251 |
| typescript-eslint-13058 | no | yes | no | yes | 5 | 4043 |
| typescript-eslint-12995 | no | yes | no | yes | 5 | 4492 |
| typescript-eslint-12991 | no | no | no | no | 5 | 3755 |
| typescript-eslint-12977 | yes | yes | no | no | 5 | 3901 |
| typescript-eslint-12974 | no | yes | no | no | 5 | 4825 |
| typescript-eslint-12953 | no | no | no | no | 5 | 4169 |
| typescript-eslint-12941 | no | yes | no | no | 5 | 4498 |
| prometheus-19403 | yes | yes | no | yes | 5 | 4045 |
| prometheus-19368 | yes | yes | yes | yes | 5 | 3996 |
| prometheus-18631 | yes | yes | yes | yes | 5 | 3934 |
| prometheus-18401 | yes | no | no | no | 5 | 3917 |
| prometheus-18375 | yes | yes | yes | no | 5 | 3418 |
| prometheus-18323 | yes | no | yes | no | 5 | 3986 |
| prometheus-17312 | yes | yes | no | yes | 5 | 4311 |

### Receipt identity

The following receipts are retained as immutable artifacts in the shared registry (all development
variants are attached to output 1706). Final receipts bind the frozen locate/search source hashes;
the code, metadata and exact gate proof are bound again by the delivery packet.

| Final receipt | SHA-256 |
|---|---|
| `l2-final-4000-01a1d4ec.json` | `c226ddda77035d8d2fa8c8b75ad6a5c2c9b7fd987e86a068c5bbde726e15cb44` |
| `l2-final-5000-01a1d4ec.json` | `410418a1a36baf50d6afd73258aec0b5c4da5c851d7dad2b3569eac2805d4e59` |
| `l2-final-search-retrieval-01a1d4ec.json` | `f469538916123c03f6217bf8206d99b0d4189506e3a042b30f8967d329ab7e66` |
| `l2-final-search-paraphrase-01a1d4ec.json` | `5dd85ba75285eea68094583c8a8fa2c2986e511cc312e82f7ec5e111ca9655f7` |

## Post-review corrections — development only (d696510)

Claude's independent review of `bb6da5e` requested changes (review event 1774). The correction branch
`cg/locate-3-review-fix` starts at that frozen commit. The evaluation on the separate 45 cases remains
bound to **bb6da5e**: these corrections must not be rerun or selected on that cohort. Neither its
cases nor its results have been opened. The before-results for this delta are the final 23-case
receipts above; their locate source hash has been verified against bb6da5e.

- **G1:** Protect the two best text matches before file hints, while retaining exact qualified-symbol
  priority. Remove the fallback that invented up to three declarations by line number when a named
  file had no lexical match. Add the review's generic three-file Go reproduction and a no-usable-word
  case; adjust the file-hint fixtures to require entry among the first three rather than displacement
  of the best text matches.
- **G2:** Source URL hints retain only an indexed path suffix, otherwise the filename. Query terms
  use filename parts; URL repository/branch prefixes and directory components of local paths do not
  enter the plain-word frequency pass. Qualified symbols retain all their name parts.
- **M1:** Document `named file` and `named symbol` in the format table, and update the ordering in the
  concepts/locate guide. README/SKILL default wording is left to the coordinator as agreed.
- **M3:** Consume a maximal reference with a simple character-class scan, then classify it. The
  punctuation flood `a-.` repeated to the request limit is a behavior fixture; measured scan timings
  will be recorded separately, without a timing-sensitive unit assertion. Plain-word first positions
  use a single dictionary pass rather than repeated list scans.
- **M4:** Unquoted read-only and built-in remain ordinary prose rather than receiving code-name
  priority; quoting or an explicit path still expresses a code reference.

L1 released cpu-heavy after both frozen-candidate evaluations (event 1789). This correction's tests
and measurements ran only after L2 acquired it (event 1792), without overlapping L1. The frozen
candidate remains bb6da5e; the correction is not re-evaluated on the separate 45 cases.

### Regression checks and scanner diagnostic

The 24-test module run on an isolated bb6da5e with the new tests fails five behavioral assertions:
incidental-file priority, arbitrary declarations with no usable words, URL/path noise, and the two
compound-name priority checks. The punctuation fixture preserves the same empty query on both
implementations; performance is measured separately. All 24 tests pass on the corrected branch.
Exact qualified symbols and entry of file hints among the first three remain covered, without
requiring file hints to displace the protected best text matches.

The adversarial scanner probe uses `a-.` repeated to 999, 4,998 and 19,998 characters. Each sample
performs two reference scans, with three repeats; both implementations find zero code references.
The values below are local diagnostic medians, **not end-to-end locate latency or a speed guarantee**.

| Input characters | Frozen bb6da5e, two scans (s) | Correction, two scans (s) |
|---:|---:|---:|
| 999 | 0.004273 | 0.000055 |
| 4998 | 0.102965 | 0.000148 |
| 19998 | 1.618549 | 0.000558 |

### Development before/after — frozen bb6da5e versus correction

The same original 23 issue texts, parent indexes and scorer are used, at both explicit budgets.
All 46 corrected counters are exact and within budget. The file@3 and callable@5 hit flags are
unchanged **for every development case**, not just in the totals. Therefore the generic Go
reproduction is fixed, but the two existing Go file misses are **not recovered** by this delta.
No case-specific adjustment or tuning on the separate evaluation is made.

| Implementation | Budget | File@3 | Callable@5 | Five candidates | Median characters | Range |
|---|---:|---:|---:|---:|---:|---|
| bb6da5e | 4000 | 14/23 | 9/19 | 9/23 | 3624 | 3253–3996 |
| Correction | 4000 | 14/23 | 9/19 | 9/23 | 3643 | 3253–4000 |
| bb6da5e | 5000 | 14/23 | 9/19 | 23/23 | 4045 | 3418–4825 |
| Correction | 5000 | 14/23 | 9/19 | 23/23 | 4045 | 3578–4825 |

At 4,000 the candidate distribution changes from 1×3 / 13×4 / 9×5 to 2×3 / 12×4 / 9×5; the extra
shorter answer does not alter target-hit flags. At the default 5,000 all answers still contain five.
Per-repository file/callable totals remain Django 3/8 and 2/4, typescript-eslint 6/8 and 3/8,
Prometheus 5/7 and 4/7. The scanner improvement is separate from localization accuracy.

Search is unchanged; fresh runs again preserve every lexical and hybrid hit list and its order
(8 retrieval queries / 15 relevant definitions, 11 paraphrases / 22). Guide examples are regenerated,
references aligned and selftest is 25/25 TRUST. The full exact-candidate suite is recorded in the
new delivery proof rather than asserted from these targeted checks.

### Post-review receipt identity

| Receipt | SHA-256 |
|---|---|
| `l2-review-after-4000-01a1d4ec.json` | `9af6afd3f63de309b5d69c7b511ffa4fca8d6bacbf78e2f3335530ba179b19c2` |
| `l2-review-after-5000-01a1d4ec.json` | `cf26dfde30e116e418e13020d14c95ef9579b07580dbe1ae325fcc43a495bfe8` |
| `l2-review-search-retrieval-01a1d4ec.json` | `e8a7c0b359e7bbc131645a1dc5356771836e53ce1d884a432eda5b6b0033b396` |
| `l2-review-search-paraphrase-01a1d4ec.json` | `5f5358e47754313b3a2e44db9f6967f225a9446e537a7d72420edb9141403bfd` |
| `l2-review-scanner-01a1d4ec.json` | `a74009012603b08bd68955f198d2bb89150f0d3c9b77dc8fcf96a333c59496a2` |

## Third-read R3 correction — uniform syntax with a known language limit

Review event 1828 rejected the two-compound exclusion list. Both authorized options were considered
using **only the same 23 development cases**. The original frozen evaluation remains bb6da5e; none
of these corrections is run again on the separate 45 cases, whose results/cases remain unopened.

### General evidence rule tried and rejected

A bare kebab word was given code-name priority only if quoted, bracketed/parenthesized or equal to
an indexed production filename stem. Explicit paths, source filenames and qualified identifiers
retained their existing treatment. This had no word-specific exceptions and passed 26 targeted
module tests, including unseen compounds and active-index refresh after deleting a named file.
Its source is preserved in `l2-general-rule-proposed-01a1d4ec.py`, bound by the experiment receipts.

The measurements below show one fewer callable hit (9/19 -> 8/19) and one default-budget answer with
four rather than five candidates. This experiment was rejected in favor of the owner's explicitly
authorized alternative: remove the exclusion list and document the language ambiguity, keeping the
smaller syntactic rule and the existing development behavior. This selection uses no separate
cohort information and adds no case-specific remedy.

### Chosen implementation and tests

The production delta from d696510 removes only the PROSE_COMPOUNDS constant and its skip branch
(three deleted lines). Bare kebab words are handled uniformly. **M4 remains a known limitation:**
English prose compounds can receive code-like priority and precede a qualified name. The guide and
concepts now state this instead of claiming that ordinary compounds are distinguished generally.
The previous test that promised special treatment of two words is replaced by a public-behavior
check: bare and quoted references receive the same query treatment across six compounds, including
ones outside the original probe. On d696510 it fails two assertions; the final 24-test module passes.
The source-filename test keeps its filename assertion without the now-unsupported prose exception.

The guide also clarifies the named-file limit: the line-number fallback is absent, but filename
terms match every declaration's indexed path. Remaining slots may therefore be path-only matches,
without a corresponding name/body match. This correction changes the explanation, not the ranking.

### Measurements at both budgets

The before column is d696510 (its source hash verified against its registered receipts), not the
initial pre-L2 engine. All rows use the same 23 original issue texts, parents and scorer.

| Variant | Budget | File@3 | Callable@5 | Five candidates | Median characters |
|---|---:|---:|---:|---:|---:|
| d696510 | 4000 | 14/23 | 9/19 | 9/23 | 3643 |
| General evidence rule (rejected) | 4000 | 14/23 | 8/19 | 8/23 | 3643 |
| Uniform syntax (chosen) | 4000 | 14/23 | 9/19 | 9/23 | 3643 |
| d696510 | 5000 | 14/23 | 9/19 | 23/23 | 4045 |
| General evidence rule (rejected) | 5000 | 14/23 | 8/19 | 22/23 | 4070 |
| Uniform syntax (chosen) | 5000 | 14/23 | 9/19 | 23/23 | 4045 |

The chosen implementation preserves every case record of d696510 at both budgets, including query
terms, ranking, target-hit flags, sizes and reading counters. Thus it neither recovers nor hides the
two existing Go file misses (Go remains 5/7 file@3, 4/7 callable@5). At 5,000 all 23 answers still
contain five candidates; all 46 counters are exact and within their budget.

Existing search benchmarks are rerun with both lexical and hybrid lists/order unchanged; search.py
is byte-identical. Guide examples are regenerated, references align and selftest is 25/25 TRUST.
The exact-candidate full-suite outcome is recorded in the final delivery proof.

### Third-read receipt identity

| Receipt | SHA-256 |
|---|---|
| `l2-general-kebab-4000-01a1d4ec.json` | `e1b9e54aa07be57138dfce35d27025218998fd2d553655c53115ba72e1a75dc0` |
| `l2-general-kebab-5000-01a1d4ec.json` | `6448acd4199a6d411d82a99bd1819e6d35200d16ef8a193a9c9f4b0422057e1c` |
| `l2-general-rule-proposed-01a1d4ec.py` | `182748b500027f877ca7c9e42b8df13a82e0fbf88eacef496d7edeca1283b099` |
| `l2-uniform-kebab-4000-01a1d4ec.json` | `492f04dabc50c012f63ef0e4d90bb1050b4e0cb7595171a6978b6595bd30bb8d` |
| `l2-uniform-kebab-5000-01a1d4ec.json` | `d36b826f7fb87fa634cc397296ff6797bd7c9644a90790261bf745270e91725e` |
| `l2-uniform-search-retrieval-01a1d4ec.json` | `55053e5577c9b6bc0407fb4c17a1942dfb800de659111b2e4ffd75e5d041970e` |
| `l2-uniform-search-paraphrase-01a1d4ec.json` | `6fe95b2358ab40afcba0d8afc38f3d9d0855ce1261354392f68af211c8f67439` |

## 2026-11-17 — restore the evaluated candidate order

Decision `r4-locate-order` restores `traced + hinted + best_text + eligible`, keeping the
original order of symbol and file hints. The post-freeze G1 change had moved file hints after the
best text matches; the coordinator's re-measurement found that this lost first-place file hits
relative to the blind evaluation. The original G1 fixture now states the trade-off: a file named
only as setup context can precede the actual text match. All other post-freeze fixes remain.
This is an order restore, with no new tuning or inspection of per-case bench results by the
builder. The coordinator performs the acceptance replay separately.
