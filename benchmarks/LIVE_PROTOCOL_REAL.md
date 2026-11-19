# Paired pilot on a real repository — Phase A

This protocol and all cases must be accepted by `coord-read` before a live run.
Phase B needs a separate coordinator announcement to the owner. No model has
been started to prepare or qualify these cases. This extends the historical
fixture pilot; it does not alter `LIVE_PROTOCOL_1.8.md` or its results.

## Question and selection

Does graph access help locate, change and review real code, and does it reduce
native input/output usage in the subsequent edit and review calls? Success,
time and usage are reported separately. A smaller prompt or a returned graph
witness alone is not a benefit on the complete task.

The repository is [SymPy](https://github.com/sympy/sympy). At reference
`ddcde486d659487aad2df8edb029a078aa473cc5`, it has 880 non-test Python
package files (1,570 including tests). The complete composite license contains
BSD three-clause notices and MIT; the retained `real-cases/LICENSE.sympy` applies
to the copied historical test patches. GitHub's license API reports
`NOASSERTION`; classification uses the actual license text, not that label.

`real-cases/selection-rule.json` was recorded in Squadra output 377 before
fetching PR metadata, SHA-256
`b3c601b6f1f6a27fa53e7cca5339c03d24e4de34207a8468f038cc60114017c2`.
Its pool is the 100 most recently **created** PRs merged before 2026-11-01 UTC,
ordered by `created_at` then number descending. The exact GitHub query and
ordered pool are in `real-cases/selection-ledger.json`. The search response was
not marked incomplete. Public metadata can change; the recorded pool is the
selection input, not a future rerun of the query.

Eligibility: one to three production Python files changed, tests added or
modified, a Fixes/Closes/Resolves link to a same-repository issue (lowest issue
number if several), and offline red-on-first-parent / green-on-historical-fix
controls. HTML template comments, including the example `Fixes #1234`, are not
active issue references. Take the first four eligible PRs in order. If fewer
than four qualify, stop: no silent pool expansion or repository replacement.

Before the fourth selection, PRs 30909, 30906, 30895, 30890, 30888, 30883 and
30868 lack an active closing issue reference; 30892 has no changed test;
30881 has no production change. These are selection exclusions, not agent
outcomes. Later PRs are not substitutes for a failed live case.

## Frozen cases and qualification

`real-cases/cases.json` contains exact issue title/body, body hash, parent and fix
commits, parent Git tree, test-only patch hash, test IDs and the common Python
file inventory. The original issue is the intent, including any location hints
already present in it. The agent receives neither the PR implementation diff,
the hidden test patch nor this host-side case manifest.

| PR / issue | Historical first parent | Historical fix | Hidden test function |
| --- | --- | --- | --- |
| [#30885](https://github.com/sympy/sympy/pull/30885) / [#30884](https://github.com/sympy/sympy/issues/30884) | `36e94607e83b672e423e6b17ab978261831f4613` | `6134f599e14a0a7cd857d801fdc8636e72bd7253` | `sympy/logic/tests/test_boolalg.py::test_simplify_univariate_endpoints` |
| [#30882](https://github.com/sympy/sympy/pull/30882) / [#9154](https://github.com/sympy/sympy/issues/9154) | `8d955e7ef24b0322e4ae5e168c2b64a881c15f14` | `ccf7f5732b145de0da03db190d1f957aaefb8f9a` | `sympy/core/tests/test_exprtools.py::test_monotonic_sign` |
| [#30872](https://github.com/sympy/sympy/pull/30872) / [#26652](https://github.com/sympy/sympy/issues/26652) | `5b861e4c4a06d56bac4b3f1398985416ae6ec6c7` | `36e94607e83b672e423e6b17ab978261831f4613` | `sympy/printing/tests/test_latex.py::test_Array` |
| [#30866](https://github.com/sympy/sympy/pull/30866) / [#26503](https://github.com/sympy/sympy/issues/26503) | `9c0cb1bcfa48af38d165441eae15b421ab8c07df` | `a11605ca930cc3138cdd6c90bc8179e9e166dd8b` | `sympy/solvers/tests/test_solvers.py::test_issue_26503` |

For each case, every new/changed top-level test function in the historical
patch is hidden. Existing modified functions retain all their assertions, even
coverage beyond the issue's shortest reproduction. The public smoke set is the
first five unchanged test functions in the changed test modules, sorted by
qualified name and excluding hidden IDs. These module paths give both arms a
location hint: this is not blind localization across the entire repository.

Initial qualification and the executable dry run each observed one assertion
failure on the parent, one hidden pass on the historical fix, and five public
passes on each, with zero collection/setup errors, skips or timeouts. The
qualification process has a 120-second limit per test subprocess. A setup error,
skip, timeout or missing test receipt does not qualify as a reproduced bug.

Environment: Python 3.13.14, pytest 8.3.5, mpmath 1.3.0 and hypothesis 6.131.9
in a separate temporary venv. There are no optional compiled algebra backends,
service dependencies or editable project install/build hooks. The host test
entry point disables pytest plugin auto-discovery and user PYTEST_* overrides,
and blocks socket connection/address resolution in the test process. It does
not execute an arbitrary project test command proposed by an agent.

## Equal inputs and workflow

Each arm starts from an export of the exact parent tree, verified by Git tree
hash. The disposable repository has a fresh seed commit and **no historical
future fix objects**. All source blobs must be warmed in the preparation cache
before execution; archive transport is disabled and the benchmark downloads
nothing at runtime. Git hooks are disabled. The original input tree hash is
reported; the disposable seed commit is not presented as the historical commit.

Both arms get the same original issue, 1,570-file Python inventory and approved
public-test argv. Only existing Python package files may change; both `touch`
and `tests` plan paths and actual changes must stay in that inventory. There is
one Codex plan, one Codex edit, host verification and one Claude review. No edit
retry or feedback round. Agent shell/project commands are forbidden; observed
command events invalidate the case and stop further starts.

Both arms use the pilot-only `real_pilot_mcp.py` wrapper and expose the same
`text_search(pattern, path_glob=null, max_results=50)`: a literal, case-sensitive
search of tracked working-tree files, with path/line/snippet results. No index
or graph is used by this tool. It skips binary files, files over 1 MB and
symlinks (including ancestor links); reports omitted counts and truncation;
limits scans to two seconds, results to 50 and escaped response size. There is
no regex execution. This preserves basic text search for the Codex baseline
while keeping project shell commands disabled. Product MCP code is unchanged.

`source_only` exposes `text_search` and `cg_read`;
`graph_assisted` exposes `text_search`, the normal MCP tools and retrieval guidance.
Claude's built-in Read/Grep/Glob permissions are the same in both arms. Host
indexing, plan enrichment, test execution and verification still run in both
arms, with host `checked`/`static`/`graph` payloads removed from agent prompts.
This compares text-search-plus-read against text-search-plus-read-plus-graph
within the code-graph workflow,
not a code-graph-free host comparison.

Arms alternate by case index: source/graph, graph/source, source/graph,
graph/source. They are sequential, with a new client run for each phase; this
is not an estimate of a persistent conversation. Existing provider caches may
still affect native cached-token counts. No cache reset or equivalent provider
latency is claimed.

Success requires the workflow verification and independent review to accept,
zero out-of-scope changes, original public smoke tests and hidden historical
tests green in separate host copies, existing changed test statements/decorators
preserved, and existing top-level function signatures in changed source files
preserved. After merge into the disposable host, hidden tests run again.
These finite checks are not the full SymPy test suite or complete API equivalence.
No new agent-written regression test is required for the score: the historical
tests are the independent regression gate fixed before observation.

The hidden patch is applied only in an external scoring copy. Public tests
also use frozen original test modules in a host copy, so deleting/weakening an
agent-side test cannot make that independent smoke check pass. Tests and
historical fixes are not imported into the agent checkout. All actual agent
changes are retained privately for later scoring audit.

## Limits, measurements and disclosure

- At most 24 client starts, including failed starts; 600 seconds per client call.
- Claude receives `--max-budget-usd 0.60` for each review. At most 5 USD reported
  total; missing reported cost consumes the full per-review allowance. A next
  case starts only if its allowance fits. This is a CLI/reported-cost control,
  not a separate estimate of billing. Codex uses the authorized account quota.
- Quota/authentication/startup failure, unknown cleanup, forbidden commands or a
  budget boundary stops all later paths. Every stopped path remains in JSON.
  Startup failures and timeouts are not silently retried.
- `setup_seconds` covers parent export and local seed preparation, separate
  from `seconds` for the workflow and scoring. `stages[].seconds` includes host
  work for that stage; `calls[].seconds` is the native client end duration.
- Calls identify `phase` (plan/edit/review), client, mode, outcome, resolved
  model if reported, native usage fields, native tool outcomes and Claude's
  reported cost. Cached/write tokens retain their native field names; no
  different tokenizers are added into a common price. The compatibility
  `usage` field is normalized; use `native_usage` for per-provider comparisons.
- `explicit_source_files_read` lists successful cg_read/cg_source/Claude Read
  paths matched to the original tracked inventory, including non-Python files.
  `tool_returned_file_mentions` separately lists matching paths in structured
  tool results: a mention can be metadata, not a source read. Neither field
  counts implicit provider file access or unstructured grep output as a read.
- Graph search witness flags mean evidence was returned. They do not prove the
  agent used it or that it caused a correct edit.

Raw streams, prompts, sessions, absolute user paths and test output stay private.
Public rows contain only projected metrics, relative tracked paths, stage/error
codes and sanitized test counts. Test failures/holdout timeouts still leave rows
and call receipts. A harness failure with unavailable call receipts is explicitly
marked incomplete and stops further starts.

Publish all eight paths, including failures. Compare success first; show phase
usage and time for matched cases, cache counts separately, and explain missing
measurements. Four selected public historical bugs from one project do not prove
general usefulness or savings; model familiarity with public fixes is possible.
The selection rule is never retuned after live outcomes.

## Reproduction before live

Prepare an external Python 3.13 venv with the pinned versions above. Download
SymPy Git objects beforehand into a temporary cache; no setup hook from SymPy is
needed. With absolute local CACHE and PILOT_PY paths:

```bash
.venv/bin/python -I tests/test_real_pilot.py
.venv/bin/python -I benchmarks/real_pilot.py --repo-cache "$CACHE" \
  --test-python "$PILOT_PY" --output /tmp/codegraph-real-dry.json
```

The dry run qualifies all four parent/fix pairs and reports `live_starts: 0`.
Only after the frozen Phase A is accepted and the owner notified may the same
command be given `--live --work-dir` pointing at a new disposable directory.
Raw work and the projected output must be preserved separately. Result prose
and any claim of utility need a separate read after the run.
