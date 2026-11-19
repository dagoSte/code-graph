# Review a change

After an edit, `review` compares the project with a Git base: which definitions the diff touches, which
static findings are new, persistent or resolved, and which tests reach the changed code. It runs no
tests and does not judge whether the change is right; it tells you where to look.

## Run it

In a Git checkout, after editing and re-indexing:

```bash
cg index
cg review --base HEAD          # working tree against the last commit
cg review --base main          # against another branch or commit
cg review --base HEAD --staged # only what is staged
```

Working-tree `review` refuses an outdated index before mapping the diff: changed,
added or deleted source files, or a changed engine, require another `cg index`.
The check does not update the database. `--staged` instead builds a fresh temporary
index for the staged snapshot, so its diff uses staged line positions.

## Read the answer

Without `--json`, the answer is a short summary of the baseline, changed definitions and findings,
followed by the proposed tests and command. In this example, `cancel_order` was edited to refund the
order total instead of the amount actually paid:

<!-- cg: review --base HEAD | edit=partial-refund -->
```text
$ cg review --base HEAD
Baseline: available · 75ab02f4b5afb815fc74a612a3024d9ac1f8b930
Changed definitions listed: 1
  shop/orders.py:17-25 cancel_order · def cancel_order(order):
Findings: new 0 · persistent 0 · resolved 0 · uncompared 0

3 tests listed for the changed definitions (2 direct, 1 transitive) · runner unittest
run all: python3 -m unittest tests.test_orders.Orders.test_cancel_refunds_and_restocks tests.test_orders.Orders.test_cancel_twice_refunds_once tests.test_api.Api.test_cancel_endpoint (cwd: .)
  direct     tests/test_orders.py:13 Orders.test_cancel_refunds_and_restocks
  direct     tests/test_orders.py:19 Orders.test_cancel_twice_refunds_once
  transitive tests/test_api.py:8 Api.test_cancel_endpoint via shop/api.py:19 handle_cancel
Unknown/unresolved call records: 1.
Broader verification required: run the wider suite too.
Proposal only; no tests executed.
Static candidates, not confirmed bugs; read the diff. --json keeps the full structured answer within its budget.
```
<!-- /cg -->

The text shows at most eight definitions, five new (or uncompared) findings and twenty tests, with
explicit `… N more` notices. It distinguishes an unavailable baseline from comparable new findings;
when the response budget omitted evidence, it says so. Long commands are labelled as previews:
request `--json` for complete argv. A direct-only command in a bounded review covers the listed direct tests;
the aggregate still covers the full selection. The summary does not run tests or classify a candidate as a bug.

`--json` keeps the existing `codegraph-review/1` fields and evidence, also used by MCP.
For example, the changed definitions are:

<!-- cg: review --base HEAD --json | edit=partial-refund keep=symbols -->
```json
[
  {
    "change_kind": "overlapping_diff_unknown_semantics",
    "end_line": 25,
    "file": "shop/orders.py",
    "id": 38,
    "identity": {
      "file": "shop/orders.py",
      "kind": "function",
      "language": "python",
      "scope_name": "cancel_order"
    },
    "kind": "function",
    "language": "python",
    "line": 17,
    "name": "cancel_order",
    "signature": "def cancel_order(order):"
  }
]
```
<!-- /cg -->

Each changed definition comes with its current signature. `change_kind` is always
`overlapping_diff_unknown_semantics`: the diff overlaps the definition, and code-graph does not decide
whether its behaviour or its signature changed. Read the diff there, and run
[`impact`](impact-and-tests.md) on it when the signature changed.

The findings, compared with the same checks run on the base:

<!-- cg: review --base HEAD --json | edit=partial-refund keep=summary -->
```json
{
  "new": 0,
  "persistent": 0,
  "resolved": 0,
  "uncompared": 0
}
```
<!-- /cg -->

- **new**: findings that appear only after the change; these are the ones to look at.
- **persistent**: findings that were already there, so an old problem is not blamed on your change.
- **resolved**: findings the change removed.
- **uncompared**: findings that could not be matched with the base (for example after a rename).

For an agent, `review --base HEAD --summary --json` returns a compact answer
(`codegraph-review-summary/1`): base, revision, counts, the new findings, the tests with their
commands, the aggregate command and the limits, built from the complete result. When the budget
is short, tests leave first and the counts stay whole. MCP `cg_review` takes `summary: true`.

`verification` holds the same test plan as [`verify`](impact-and-tests.md#verify-the-tests-to-run) for
every changed definition.

## Baselines

To compare with a state that is not a commit (for example before a long refactor), save the findings
first and compare later. Baselines live in `.code-graph/` as `.json` files:

```bash
cg review --base HEAD --save-baseline .code-graph/before.json
# … edit …
cg index && cg review --base HEAD --baseline .code-graph/before.json
```

## check: static candidates for defects

<!-- cg: check -->
```text
$ cg check
-- 0 findings, 0 omitted. These are static CANDIDATES, not bug probabilities. Diagnostics: 2 groups; use --include-diagnostics.
```
<!-- /cg -->

`check` lists candidates, not bugs: calls with the wrong number of arguments, methods that do not exist
on the resolved class, cycles of high-confidence calls between files, PHP name clashes and receivers whose contract is
unknown. `--include-diagnostics` adds parser and analysis limits. `review` runs the same checks on the
base and on the change, which is why its `new` list is short.

## dead: code nothing calls

<!-- cg: dead -->
```text
$ cg dead
VERIFIED_DEAD shop/admin.py:5-6 function cancel_unpaid  (text occurrences 1, definitions 1)
-- 1 shown out of 1 without callers in the graph. AMBIGUOUS = name found as text elsewhere (strings, routes, DI): read before removing. Always test before removing.
```
<!-- /cg -->

Verdicts go from `VERIFIED_DEAD` and `LIKELY_DEAD` to `AMBIGUOUS` (the name appears as text elsewhere:
strings, routes, dependency injection) and `UNVERIFIED`. None of them authorises a deletion without
reading the code and running the tests.
