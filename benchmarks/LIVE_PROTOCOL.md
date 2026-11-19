# Paired live-agent pilot

Protocol fixed before live calls, 12 November 2026. This is a small reproducible
pilot, not a statistical estimate of time/token savings or model superiority.

## Question and controlled comparison

Does access to code-graph retrieval help the same change workflow complete two
small corrections compared with source-only reads? Four disposable repositories
start from identical per-task bytes. The author is the installed Codex client and
the reviewer the installed Claude client. No user's repository or private source
is sent to either service. The actual client versions/settings and usage are
recorded, including unavailable model identifiers rather than inventing them.

Both arms use the existing change-flow implementation, isolated worktree, schema,
test runner, scope checks, independent reviewer and integration into their own
temporary `main`. Both receive the same file list, intent and approved test argv.
The source-only arm exposes only `cg_read` through the existing MCP server and
replaces retrieval-specific prompt guidance with source-read guidance. The assisted
arm exposes the existing graph tools and retrieval guidance. No source-only tool
can query the index. Host-side indexing and static verification are retained in
both arms to hold workflow gates constant: this compares agent access to evidence,
not an entirely code-graph-free client installation. Report that distinction.
Host-enriched plan graphs, checked bindings and static review results are removed
from both arms' model prompts; test/perimeter feedback remains. This prevents
the source-only arm from receiving graph evidence through host-side enrichment.

## Frozen tasks and order

1. **Refund window:** include day30 in a nonnegative 30-day refund window while
   retaining the allowed payment states. Public tests cover existing behavior;
   independent holdouts cover day30, negative ages, day31 and invalid states.
   Run source-only before assisted.
2. **Falsy configuration:** preserve stored `0`, `False`, empty string and `None`;
   use the default only for an absent key. Public tests cover truthy and missing
   values; independent holdouts check value identity/types and all falsy cases.
   Run assisted before source-only.

Only the task's declared source and test files may change. Host holdouts live
outside the agent worktree and are never exposed. The harness owns and approves
only these generated fixtures: no approval/integration is performed on the real
code-graph project as part of the experiment. Test argv is the current Python
interpreter followed by `-m unittest discover -s tests -v`; no shell or project hook.

## Limits and stop rules

- At most four complete paths, one plan/edit/review each: **12 live starts**.
- One edit round per path, no prompt tuning, automatic repair or reruns of failures.
- Each agent call has a180-second deadline. Each Claude review uses the existing
  `--max-budget-usd 1` control: at most4USD in configured review caps. Codex uses
  the signed-in client's quota and has no corresponding dollar-cap flag here.
  Stop on quota/auth failure and retain an incomplete outcome; do not buy credits,
  configure another provider or extend these limits automatically.
- When a workflow wait expires, close the start gate, cancel and drain its worker
  before restoring the MCP configuration. Stop the remaining pilot if cleanup
  cannot be confirmed within30 seconds; retain that incomplete outcome.
- Publish neither raw client logs nor session identifiers/absolute user paths.
  Keep the raw native logs in the ignored proof archive; publish sanitized receipts,
  frozen task sources, scoring and reproducible commands.

## Outcomes fixed before observation

For every path report plan/edit/verify/review/integration state, actual changed
files, public tests, untouched host holdouts and whether the final diff fixes the
task. Keep rejected plans, failed calls, skipped subsequent stages and regressions.
Run the original public tests from an external frozen directory before, after and
after integration. Success also requires retention of original test assertions and
public parameter names/defaults, plus an added regression test. A passing mutable
suite or a positive model verdict cannot replace these checks.
The new tests must pass on the candidate and detect the original bug with at least
one assertion failure on the original sources, without errors or skipped tests.
Report elapsed task/stage time and **native client input/output/cache token counts**;
missing usage stays null, never characters/4. Preserve raw per-call units and cache
fields and do not equate them to billed dollars. Count tool calls and whether the
assisted path actually used graph evidence. Record started/ok/error tool outcomes;
a successful search without a returned graph witness is not evidence of graph use.
Unreadable result payloads remain unknown. Two task pairs cannot establish a
general improvement; no post-hoc task exclusion or synthetic-result substitution.

CLI JSONL and required MCP initialization are checked against the installed client
and the official [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
and [MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) documentation.
