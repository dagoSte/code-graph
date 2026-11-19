# Three-repository historical-bug study

Preparation only: S0 starts no model or provider. The live phase requires a
separate assignment after this protocol, case set, harness and final engine are
accepted. This document fixes the analysis before any live outcome is observed.

## Question and design

Does access to code-graph help an author/reviewer workflow correct historical
bugs, compared with the same workflow using literal text search and file reading?
Does it reduce native input tokens in the later edit and review calls?

Target eight bugs in each of three public, permissively licensed repositories:
Django (Python), typescript-eslint (TypeScript), and Prometheus (Go). The exact
prospective rule and prior candidate dispositions are in
[`study-cases/selection-rule.json`](study-cases/selection-rule.json). The initial
rule is preserved alongside it. Repository and eligibility amendments preceded
the initial PR metadata; the conditional pool-extension rule preceded all native
qualification results and extension metadata. NetworkX failed the 300-production-file threshold. The coordinator
excluded microsoft/TypeScript before PR inspection and authorized the replacement
candidates. No repository or bug is silently substituted.

Preparation reached **23 qualified bugs: eight Django, eight typescript-eslint,
seven Prometheus**. Prometheus exhausted the preregistered 1,500-PR bound with
seven qualified cases; it does not meet the eight-case target. The earlier
`s0-pool-esteso` decision permits this cohort because it exceeds the minimum 20.
The frozen cohort uses at most 138 starts and 46 reviews (27.60 USD in configured
review caps), within the absolute ceilings below. Every disposition is in
[`study-cases/selection-ledger.json`](study-cases/selection-ledger.json).

The initial pool is 500 merged PRs per repository before 1 November 2026,
ordered by creation time descending, then PR number descending. Retain every
candidate disposition, including unavailable issues, unsupported test selection,
environment failures, skips, timeouts, and parent/fix results. Take the first eight
that meet the rule. Decision `s0-pool-esteso`, registered before any parent/fix
qualification result, authorizes preceding blocks of 500 up to 1,500 per
repository if eight cases are not reached. Criteria stay unchanged; preserve
the initial pool and all extension queries. At that bound retain the number
reached; at least 20 bugs total across all three repositories are required.
A failed repository gate or fewer than 20 cases is a declared stop, not permission
to relax a criterion. Linked issues must be
classified as bugs in their primary tracker. Django uses its official Trac
tickets; the other repositories use their GitHub issues.

Each bug is one pair: source-only versus graph-assisted. Both receive exactly the
original issue title/body, the historical parent code, the same file inventory,
and the same approved public-test command. Neither sees future Git history,
production fix patches, held-out test patches, or the historical PR explanation.
Both arms get `text_search` and `cg_read`; the assisted arm additionally gets the
actual code-graph MCP tools. Neither model gets a shell, network, or a test runner.
Host code performs compilation and approved tests in disposable copies.
The complete identical file inventory is provided at plan time. Edit and review
receive the same approved-plan/diff context in each arm and retain literal search
over every tracked file; they do not repeat the full inventory. On these cases an
inventory can exceed 200,000 characters, so repeating it would dominate later-call
input. This symmetric rule is fixed during preparation, before any model outcome.

Decision `studio-grammatiche-y`, approved before live calls, extends the writable
inventory in **both Go arms** to every `.y` grammar file tracked in that case's
historical parent, alongside its existing Go files. Derive this list from the
parent tree, never from future fix files or the union used for scoring copies.
This allows edits to a generator's grammar and generated Go together. It changes
neither case selection nor the Go production-file/callable targets of the
localization measure. Historical `.y` changes are host-only auxiliary fixture
material; no fix path or patch is disclosed to an agent beyond the complete
parent inventory. Native tests exercise the generated Go; they do not certify
that regenerating the parser reproduces it. Code-graph does not index `.y`;
both arms can read these files and search their literal text.

Select cases without any model result. Freeze their input trees, issue text,
test-only historical patches, public and hidden test IDs, environment manifests,
selection ledger and hashes before the live phase. A case's hidden tests must
fail by assertions on its first parent and pass on the historical fix, with
identical nonzero executed counts and no errors, skips or timeouts. Up to five
unchanged public cases, ordered by qualified name, must pass on both trees.

## Frozen environment and engine

All caches and dependencies live outside the product virtualenv. Fixed local
toolchains: Python 3.13.14, Node 26.6.0, npm 12.1.0 and Go 1.27.4 darwin/arm64.
Record any repository-specified package-manager version and every dependency
lock/hash before qualification. Preload dependencies during preparation only;
compilation/tests in qualification and the live phase must run without network
or separately operated services. Environment errors are not red regressions.

The engine is supplied with mandatory `--skill-root`; the study does not embed a
fork or copy of it. Coordinator message `S0-FREEZE-580` freezes commit
`580cd6b3cb1ac03674fc0fc021d92603619740ac`, after the engine improvements were
accepted. [`study-cases/engine-freeze.json`](study-cases/engine-freeze.json)
records its 34 script/catalog hashes and parser versions. Loading a study case
cannot fetch an engine, dependency, parser, repository or model. Source-only is an evidence-access
ablation inside the same host workflow: host indexing/verification still exists
in both arms. This does not compare code-graph with a general agent shell.

`--live` and `--localize` additionally require an accepted `--engine-manifest`
whose `engine_files` (scripts and catalogs) and `parser_versions` maps match
`--skill-root` exactly. Engine drift stops further paths and invalidates complete
study analysis. S0 prepares and tests this bank; it does not run the model or
localization matrix before the engine and protocol are frozen.

## Workflow and limits

For each arm use fresh author plan and edit calls (Codex), host verification,
then one independent review call (Claude). One edit round, no automatic retry,
repair loop or provider substitution. Interleave repositories in the fixed order
above, up to eight rounds; alternate which arm runs first by global pair index. Keep
the other arm even when the first fix fails. Record clients and resolved model
IDs when observed; do not invent missing model IDs or tokenizer counts.

Decision `s0-tetti-studio` reconciles the initial 60-start draft: 24 pairs require
**at most 144 starts** (48 plans, 48 edits, 48 reviews); a cohort of N bugs
uses at most 6N starts, with 20 <= N <= 24. Each call has a 600-second limit;
each Claude review a 0.60 USD CLI cap, and the study a **30 USD reported total**.
Forty-eight configured review caps sum to 28.80 USD. Codex uses the owner's quota.
Reserve a full review cap before starting a new arm. Missing cost consumes that
reservation conservatively; keep native cost null. Stop on total-budget boundary,
quota/auth/access errors, a model command attempt, uncertain cancellation/drain,
or a start ceiling. Keep all unstarted paths explicitly in the receipt. No billing
invoice or absolute guarantee of provider-side charges is inferred from CLI caps.

Success requires the approved scope, untouched external public tests, historical
hidden tests, test-retention guard, host verification and independent review all
to pass, followed by integration and another hidden-test pass. Review acceptance
alone is insufficient. Never integrate changes outside the allowed inventory;
check both planned touch/tests and actual changes. Preserve cancellation and
timeout receipts, with every started call, even on host-scoring failures.

## Test integrity

Freeze original public tests outside the mutable worktree. Apply the historical
test-only patch only in external host copies. Test-preservation guards must allow
legitimate assertions added inside existing compound statements, while rejecting
removed/changed original statements, headers, clauses and duplicates. Exercise
this contract for Python, TypeScript and Go with positive and negative probes.
Unmodified original tests need no reformatting. Unsupported or partial parse
states in changed tests fail closed and are recorded.

Native selectors come from concrete Django TestCases (including inherited and
generated implementations), Go test functions (excluding the suite entrypoint
`TestMain` and non-test helpers), and native Vitest case identities. Vitest runs
the complete affected modules: selected public/hidden counts and complete native
totals are both retained. Public selectors use file/name order, never hash order.
Existing changed Vitest cases with an unchanged identity are not identified as
new cases; an empty hidden selection is an explicit unsupported disposition.
Any native runtime error blocks qualification, including one outside selected
Vitest IDs. Discovery/instrument failures and corrected attempts stay in the
ledger; they are not model retries or silently replaced bug outcomes.

Structural retention is finite: it does not prove reachability or semantic
equivalence after arbitrary additions. Frozen public and hidden tests remain
independent checks. Do not change the scoring rule after seeing model results;
if a scoring defect is found, retain the original classification and publish a
separately identified offline rescore and its reason, without a new model run.

## Analysis fixed before live outcomes

The primary unit is a historical bug, paired across arms. Report all N assigned
pairs (the frozen cohort size, 20 to 24), their four paired success categories, and the difference in successful
bugs divided by N. The primary comparison is a two-sided exact McNemar/binomial
test on discordant pairs, with alpha 0.05; if there are no discordant pairs,
report p=1. Report a paired percentile-bootstrap 95% interval for the success-rate
difference, resampling pairs within each repository (its frozen case count per stratum), 10,000
replicates with fixed seed 20261113. Repository-specific counts are exploratory;
do not select the most favorable repository as the headline or claim causality
from a handful of disagreements.

If a stop rule leaves paths unexecuted or cleanup uncertain, label the study
incomplete and retain missingness explicitly; show observed results but do not
present the predeclared complete-study test or interval as confirmatory. Report
all failed plans, edits, reviews, hidden tests and integrations, not only successes.

Secondary measurements, separate by provider and phase:

- Native input/output, cache-read and cache-write/creation counters for plan,
  edit and review; missing remains null. Cached input already included in a
  client's input total is never added again. No character-based token estimate.
- Paired per-phase differences and ratios, their medians, and totals for each
  arm. Separate aggregate ratios from the median of per-pair ratios; do not infer
  persistent-chat savings from independent calls or pool different tokenizers
  into a dollar estimate.
- Host setup/index time, each workflow stage, scoring/integration time, native
  call durations and complete workflow time, with explicit timing boundaries.
- Successful and failed tool calls, returned graph witnesses, distinct relative
  source paths read per call and per workflow, and returned file mentions.
  Mentions do not prove a file was read, nor that evidence caused the model's fix.
- CLI-reported Claude cost, configured caps, actual starts and stop dispositions.

Decision `studio-localizzazione` adds one deterministic, model-free endpoint
before the protocol freeze. On every qualified historical parent, call the
frozen engine's `cg locate "<original title>\n\n<original issue body>" --json`
once with its default limits, without query tuning. Ground truth is the historical
production-file diff, prepared before viewing locate results. Report any/all file
hits and distinct target-file counts in the first 1/3/5 candidates. For callables,
report any hit and matched/total parent function/method declarations among the
first five, matched by file and exact declaration span. Declarations are mapped
from old-side diff hunks with the pinned parser; insertions must fall inside a
parent declaration. Absent or partial parent declarations are explicitly unmapped,
with their denominator disclosed. A query rejection, timeout or invalid response
is retained as a failed localization and no file hit, not removed from the cohort.
Keep `reading.returned_chars` and `reading.file_chars` in their native character
units. These are output/file sizes, not token savings or measured agent behavior.
Preserve `reading.exact=false` when returned characters are an upper bound;
do not relabel that counter as an exact size or token count.
The engine remains frozen for the complete matrix; do not use these cases to
change ranking, limits, terms or locate implementation.

Use no retrospective case weighting, endpoint changes, retries, discarded pairs
or selection of better outcomes. Models may know public historical fixes; issue
text and public-test modules may already reveal location. Fixed within-pair
order, caches, provider latency and the uncontrolled host limit generalization.

## Receipts and publication

Keep raw streams, prompts, session IDs, private paths, detailed test output and
working copies private. Public receipts contain hashes, relative source paths,
native counters, bounded test summaries and outcomes. Validate the public
projection before publication. Never publish the SQLite index, which may contain
source excerpts. Native telemetry and exact qualification evidence are retained;
no productivity, quality, token-saving or speed claim follows from a dry run.

The study inherited the earlier SymPy bank's internal 120-second static-analysis
timeout. Its host-only adapter raises each engine analysis command to 600 seconds,
preserving cancellation and process-group
cleanup. Native test commands also retain their 600-second limit. A workflow step
can contain tests and two analyses, so the bank waits at most 1,860 seconds for a
step before draining it; the model call itself remains capped at 600 seconds.
This applies identically to both arms, changes no engine code, and is fixed before
live outcomes. The original timed-out mechanical attempt stays in the evidence.
Its Django production patch was later found to have been misapplied at EOF and
is classified as an invalid fixture, not a control on the correct fix. Mechanical
controls now require the correct zero-context patch option and exact hashes from
the historical fix's Git blobs. The accepted freeze includes a separately reviewed
product performance correction; locate ranking was not tuned on this cohort.
On that freeze, both corrected Django mechanical controls complete successfully:
the verify stages take 42.621 and 43.746 seconds, within the unchanged 600-second
analysis limit. Both Go controls were also replayed after the `.y` adaptation,
integrating the grammar and generated Go with exact historical bytes. These are
host-applied known fixes with fake agents, not model successes or a general speed
benchmark. Earlier TypeScript controls and their distinct engine provenance are
retained. Native test-only patch qualifications were independently byte-audited
and remain valid. No live call is authorized by this preparation result.
