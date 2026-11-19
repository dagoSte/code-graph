# Localization comparison on 45 additional historical bugs

At the same 4,000-character cap, any historical fix file appears in the first five candidates on **28/44 cases with L2**, versus **17/44 with main**.
At the preset 5,000-character cap, L2 returns a fix file on **30/44 cases**. Exact callable hits at rank 5 are **7/41 → 16/41 → 16/41** (main4000 → L2-4000 → L2-5000).

This compares main `aa1af2f` at its 4,000-character default with the independently
frozen L2 candidate `bb6da5e` at 4,000 and 5,000 characters. Every engine/budget
has one attempt per case. The same 45 original issue queries, parents and ground
truth are used throughout; none of the original 23 live-study bugs is included.
The selection rule names `2cfb459` as its baseline commit; the baseline actually run is
main `aa1af2f`, which round 3 resumed from (decision `riprendi-giro-3`), as the README states.
django-20942 fails in all three runs: its issue text is 20,201 characters, above the
20,000-character input limit of `locate`, so the query is refused rather than answered.

## Results at the same budget and with more answer space

B = main at 4,000 characters; C4 = L2 at 4,000; C5 = L2 at 5,000.
B versus C4 compares engines at the same cap. C4 versus C5 compares the two
preset caps on the same frozen engine. The C5 cap is **25% larger**; this is an allowed
answer-size increase, not evidence that a larger answer costs less to read.
These budgets are CLI/default limits. The bank retains native reading counters
and candidate spans, not full response text or a total JSON-length measurement.

| Endpoint | B: main 4000 | C4: candidate 4000 | C5: candidate 5000 |
|---|---:|---:|---:|
| Any historical fix file @1 | 10/44 | 23/44 | 23/44 |
| All historical fix files @1 | 8/44 | 18/44 | 18/44 |
| Any historical fix file @3 | 17/44 | 28/44 | 28/44 |
| All historical fix files @3 | 13/44 | 21/44 | 21/44 |
| Any historical fix file @5 | 17/44 | 28/44 | 30/44 |
| All historical fix files @5 | 13/44 | 22/44 | 23/44 |
| Any exact changed declaration @5 | 7/41 | 16/41 | 16/41 |
| Exact declarations matched @5 | 8/135 | 20/135 | 20/135 |

The callable endpoint requires an exact file/start/end-line declaration match,
not merely the same name. It has 41 mapped cases and 135 declarations. All 44
file-evaluable cases remain in the file denominators, including query failures.
The 41 mapped callable cases also retain failures with mapped ground truth.

### Paired changes

| Comparison | Endpoint | Improved | Regressed | Both hit | Neither hit | Evaluable |
|---|---|---:|---:|---:|---:|---:|
| B → C4 (same cap) | File any @5 | 12 | 1 | 16 | 15 | 44 |
| B → C4 (same cap) | Callable any @5 | 9 | 0 | 7 | 25 | 41 |
| C4 → C5 (larger cap) | File any @5 | 2 | 0 | 28 | 14 | 44 |
| C4 → C5 (larger cap) | Callable any @5 | 0 | 0 | 16 | 25 | 41 |
| B → C5 (combined) | File any @5 | 13 | 0 | 17 | 14 | 44 |
| B → C5 (combined) | Callable any @5 | 9 | 0 | 7 | 25 | 41 |

Improved means a miss becomes a hit; regressed means a hit becomes a miss.
The comparison receipt also retains paired file results at ranks 1 and 3.
These are descriptive results on a fixed cohort, not a population estimate.

### By repository

| Repository | Endpoint | B | C4 | C5 |
|---|---|---:|---:|---:|
| django | File any @5 | 8/15 | 7/15 | 8/15 |
| django | Callable any @5 | 2/13 | 3/13 | 3/13 |
| prometheus | File any @5 | 9/15 | 10/15 | 10/15 |
| prometheus | Callable any @5 | 5/15 | 7/15 | 7/15 |
| typescript-eslint | File any @5 | 0/14 | 11/14 | 12/14 |
| typescript-eslint | Callable any @5 | 0/13 | 6/13 | 6/13 |

## After the freeze: the released engine

The numbers above are the blind evaluation of the frozen L2 engine `bb6da5e`. Two later
changes to `locate` reached main before release 1.9.0, so the coordinator re-ran the same
45 cases on them with the same tool (`measure`, engine defaults, 5,000 characters). These
runs are **not blind**: they were made after the evaluation above had been read.

| Endpoint | C5: frozen `bb6da5e` | main `b455525` | Release 1.9.0 engine |
|---|---:|---:|---:|
| Any historical fix file @1 | 23/44 | 16/44 | 23/44 |
| Any historical fix file @3 | 28/44 | 26/44 | 28/44 |
| Any historical fix file @5 | 30/44 | 29/44 | 30/44 |
| All historical fix files @5 | 23/44 | 23/44 | 24/44 |
| Any exact changed declaration @5 | 16/41 | 16/41 | 18/41 |
| Failed queries | 1 | 1 | 0 |

- **main `b455525`.** A fix from the code review of L2, made after the freeze, ranked files
  named in the request after the two best text matches. On this bench it lost the fix file at
  rank 1 in 7 cases (typescript-eslint 13051, 13039, 12912, 12869; prometheus 19307, 18409,
  17424) and gained none. That main was published with the frozen engine's numbers; they did
  not describe it.
- **Release 1.9.0 engine.** Decision `r4-locate-order` restored the evaluated order (frames,
  named symbols and files, best text matches, fused ranking) and kept the other post-freeze
  fixes; requests up to 200,000 characters are now accepted. The restore was decided **after**
  seeing these results, so its numbers are not an independent test. File-any totals match the frozen
  engine, but 18 candidate lists differ: django-20942 (the 20,201-character request) is now
  answered and hit at ranks 1, 3 and 5; django-20510 gains an exact declaration; fix-file hits
  are lost by prometheus-18409 at rank 1, prometheus-18628 at rank 3 and typescript-eslint-12934
  at rank 5.

Receipts: [post-freeze/main-b455525-5000.json](post-freeze/main-b455525-5000.json)
(sha256 `7b4805555893efaf9207391d61805969ab7edb98f8cf7b1015aa6288cc9ca2c0`) and
[post-freeze/release-fb6b4e5-5000.json](post-freeze/release-fb6b4e5-5000.json)
(sha256 `02b4c2cdadbef05967ff7b12670e78e2e4feb03bcfb171d70e8d0d9147334a10`). The release
receipt identifies the measured pre-launch engine exactly. Later launch changes add the stale-review
guard and the functional-map command/API/UI; they are not a new run of this benchmark. The recorded
scores below remain measurements of that pinned engine, not a claim that every file hash matches the
current tree. A first main run lost 9 cases to missing objects in a partial Git cache
(the tool forbids fetching); the caches were completed and the run repeated in full.

## Execution, missingness and native reading counters

| Measure | B | C4 | C5 |
|---|---:|---:|---:|
| Attempts completed | 45 | 45 | 45 |
| Failed localizations | 1 | 1 | 1 |
| Unstarted cases | 0 | 0 | 0 |
| Median returned characters | 3,452.5 | 3,622.0 | 4,166.0 |
| Median returned characters: known / missing | 44 / 1 | 44 / 1 | 44 / 1 |
| Median referenced-file characters | 55,312.0 | 119,307.0 | 130,079.0 |
| Median referenced-file characters: known / missing | 44 / 1 | 44 / 1 | 44 / 1 |
| Answers reporting an upper-bound count | 0 | 0 | 0 |

All three receipts have no unstarted cases, no engine drift and no uncertain
subprocess cleanup. **No model, project source, native project test or compiler
was run.** An execution-complete receipt is not a statement that every query
succeeded or every case has scoreable ground truth.

Character counters are native engine counters, not tokenizer counts. Missing
counters stay missing, and `exact=false` stays an upper bound. Separate medians
are not an aggregate ratio. This benchmark does not measure how much an agent
reads, token use, correction success or total developer effort.

### Failures retained without retries

| Run | Case | Status | Exit | Code |
|---|---|---|---:|---|
| B | django-20942 | failed | 1 | locate_failed_or_invalid_receipt |
| C4 | django-20942 | failed | 1 | locate_failed_or_invalid_receipt |
| C5 | django-20942 | failed | 1 | locate_failed_or_invalid_receipt |

The bank deliberately does not retain raw stderr. The failure codes and exit
statuses identify the observation, not its underlying cause. Failed queries stay
in the evaluable denominators with zero candidates/hits; they are never replaced
or retried to improve the score.

## Every assigned case

Each triple is ordered **B / C4 / C5**. `unavailable` is missing file truth;
`unmapped` is missing callable truth. Neither is silently turned into a hit.
The full receipts retain ranks 1/3/5, all-target file hits and ordered spans.

| Case | Query B / C4 / C5 | File any @5 B / C4 / C5 | Declarations hit/target B · C4 · C5 |
|---|---|---|---|
| django-22158 | valid / valid / valid | yes / no / yes | unmapped · unmapped · unmapped |
| django-22154 | valid / valid / valid | yes / yes / yes | 0/2 · 0/2 · 0/2 |
| django-22051 | valid / valid / valid | yes / yes / yes | 0/2 · 1/2 · 1/2 |
| django-22030 | valid / valid / valid | no / no / no | 0/1 · 0/1 · 0/1 |
| django-21152 | valid / valid / valid | no / no / no | 0/2 · 0/2 · 0/2 |
| django-20942 | failed / failed / failed | no / no / no | 0/6 · 0/6 · 0/6 |
| django-20864 | valid / valid / valid | yes / yes / yes | 1/1 · 1/1 · 1/1 |
| django-20518 | valid / valid / valid | no / no / no | 0/2 · 0/2 · 0/2 |
| django-20510 | valid / valid / valid | yes / yes / yes | 0/1 · 0/1 · 0/1 |
| django-20420 | valid / valid / valid | no / no / no | 0/7 · 0/7 · 0/7 |
| django-20350 | valid / valid / valid | no / no / no | unmapped · unmapped · unmapped |
| django-19626 | valid / valid / valid | yes / yes / yes | 1/6 · 1/6 · 1/6 |
| django-19548 | valid / valid / valid | yes / yes / yes | 0/6 · 0/6 · 0/6 |
| django-19099 | valid / valid / valid | no / no / no | 0/2 · 0/2 · 0/2 |
| django-19012 | valid / valid / valid | yes / yes / yes | 0/2 · 0/2 · 0/2 |
| typescript-eslint-13051 | valid / valid / valid | no / yes / yes | unmapped · unmapped · unmapped |
| typescript-eslint-13039 | valid / valid / valid | no / yes / yes | 0/3 · 2/3 · 2/3 |
| typescript-eslint-13016 | valid / valid / valid | no / yes / yes | 0/2 · 1/2 · 1/2 |
| typescript-eslint-12989 | valid / valid / valid | no / no / no | 0/1 · 0/1 · 0/1 |
| typescript-eslint-12948 | valid / valid / valid | no / no / no | 0/1 · 0/1 · 0/1 |
| typescript-eslint-12943 | valid / valid / valid | no / yes / yes | 0/4 · 1/4 · 1/4 |
| typescript-eslint-12934 | valid / valid / valid | no / no / yes | 0/1 · 0/1 · 0/1 |
| typescript-eslint-12930 | valid / valid / valid | no / yes / yes | 0/2 · 0/2 · 0/2 |
| typescript-eslint-12912 | valid / valid / valid | no / yes / yes | 0/2 · 0/2 · 0/2 |
| typescript-eslint-12904 | valid / valid / valid | no / yes / yes | 0/1 · 0/1 · 0/1 |
| typescript-eslint-12892 | valid / valid / valid | no / yes / yes | 0/5 · 0/5 · 0/5 |
| typescript-eslint-12886 | valid / valid / valid | unavailable / unavailable / unavailable | unmapped · unmapped · unmapped |
| typescript-eslint-12880 | valid / valid / valid | no / yes / yes | 0/2 · 1/2 · 1/2 |
| typescript-eslint-12873 | valid / valid / valid | no / yes / yes | 0/2 · 2/2 · 2/2 |
| typescript-eslint-12869 | valid / valid / valid | no / yes / yes | 0/2 · 2/2 · 2/2 |
| prometheus-19516 | valid / valid / valid | yes / yes / yes | 0/1 · 0/1 · 0/1 |
| prometheus-19307 | valid / valid / valid | yes / yes / yes | 0/3 · 1/3 · 1/3 |
| prometheus-19272 | valid / valid / valid | yes / yes / yes | 1/11 · 1/11 · 1/11 |
| prometheus-19241 | valid / valid / valid | yes / yes / yes | 1/2 · 1/2 · 1/2 |
| prometheus-19037 | valid / valid / valid | no / no / no | 0/20 · 0/20 · 0/20 |
| prometheus-18628 | valid / valid / valid | yes / yes / yes | 0/1 · 0/1 · 0/1 |
| prometheus-18409 | valid / valid / valid | yes / yes / yes | 0/1 · 0/1 · 0/1 |
| prometheus-18180 | valid / valid / valid | yes / yes / yes | 1/1 · 1/1 · 1/1 |
| prometheus-18084 | valid / valid / valid | no / no / no | 0/4 · 0/4 · 0/4 |
| prometheus-17932 | valid / valid / valid | yes / yes / yes | 2/3 · 2/3 · 2/3 |
| prometheus-17817 | valid / valid / valid | no / no / no | 0/1 · 0/1 · 0/1 |
| prometheus-17683 | valid / valid / valid | no / no / no | 0/1 · 0/1 · 0/1 |
| prometheus-17470 | valid / valid / valid | yes / yes / yes | 1/5 · 1/5 · 1/5 |
| prometheus-17424 | valid / valid / valid | no / yes / yes | 0/2 · 1/2 · 1/2 |
| prometheus-17412 | valid / valid / valid | no / no / no | 0/11 · 0/11 · 0/11 |

## Fixed protocol and evidence

The metadata rule selected the first 15 eligible stored ledger rows per repository
before any benchmark response. It did not filter on native test qualification.
All 45 parents, original issue texts, production diffs and declaration spans were
frozen at commit `99809d6f2666ddd8ecdbac385abfe816977de3bf` before the baseline.
The coordinator froze L2 under `l2-freeze-bb6da5e` before any candidate query.
All 734 candidate files were checked against the frozen commit; engine/parser
hashes were checked between cases and at the end. No query, ranking, case, target
or parameter was revised in response to a result; both budgets were prescribed
before measurement.

TypeScript #12886 was retained under the prospective `l1-caso-12886` exception:
its first-parent diff changes only test configuration JSON, not the production
file listed by the frozen metadata. Its three queries still ran; its file hits
are null. Thus file truth is known for 44 cases and missing for one. No case or
parent was substituted, and empty truth is never an automatic all-target success.

Callable mapping covers 59 of 69 file-target entries; seven have no changed
parent callable hunk and three are absent in the parent. Those entries leave three cases
unmapped (django-22158, django-20350, typescript-eslint-13051); typescript-eslint-12886 has
no targets at all, so four cases are unmapped for callable scoring. The parent grammar and old-side hunk mapping are
part of the ground-truth limitation, not evidence of runtime correctness.

This cohort is not random and a historical fix need not be the only valid fix.
The endpoint can miss alternative correct locations. These results do not prove
general bug-fixing ability, model benefit, developer time savings or token savings.
Later engine changes are outside this frozen comparison.

- [Selection, preparation and reproduction commands](README.md)
- [Cases and frozen targets](cases.json), [selection ledger](selection-ledger.json)
- [Ground-truth exception](ground-truth-dispositions.json)
- [Main engine pin](baseline-engine.json), [candidate engine pin](candidate-engine.json)
- [Offline comparison and recount receipt](comparison.json)

- [baseline.json](baseline.json), SHA-256 `f4bbee79f9950d15e3dc8540c7612be8242ee24318ff7128314b7ad2b6015819`
- [candidate-4000.json](candidate-4000.json), SHA-256 `e48f2423b9e40e5abda2eb4e764b6404df237811bec4d65ee67ba5ecd133cdf5`
- [candidate-5000.json](candidate-5000.json), SHA-256 `d3d6aa520cad1e90794c08acce5da38002371e5ecb53a687aa69bb418aa159d0`
