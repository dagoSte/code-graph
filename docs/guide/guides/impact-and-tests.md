# Impact and tests

Before you change a function, ask what reaches it. code-graph answers from the call graph, with the line
of every call, and lists the tests that reach the function; it never runs them.

## impact: what breaks if this changes

<!-- cg: impact cancel_order -->
```text
$ cg impact cancel_order
IMPACT of cancel_order: 5 symbols in 4 files (depth ≤3, conf ≥medium)
-- level 1 (4)
  shop/admin.py:5-6 function cancel_unpaid  [high] calls @shop/admin.py:6
  shop/api.py:18-20 function handle_cancel  [high] calls @shop/api.py:19
  tests/test_orders.py:13-17 method Orders.test_cancel_refunds_and_restocks  [high] calls @tests/test_orders.py:16
  tests/test_orders.py:19-23 method Orders.test_cancel_twice_refunds_once  [high] calls @tests/test_orders.py:22
-- level 2 (1)
  tests/test_api.py:8-12 method Api.test_cancel_endpoint  [high] calls @tests/test_api.py:12
affected tests: tests/test_api.py, tests/test_orders.py
```
<!-- /cg -->

- **Levels.** Level 1 are direct callers, level 2 their callers, and so on up to `--depth` (default 3).
- **Callsite.** `@shop/api.py:19` is the exact line of the call: open it to see the arguments passed.
- **Confidence.** `[high]` is how the call was resolved (see [Concepts](../concepts.md#the-graph-and-its-confidence)).
  The default `--min-conf medium` hides `low` edges; add `--min-conf low` when a change is delicate and
  read those callsites yourself.
- **Tests touched.** The test files among the callers.

`impact shop/orders.py --file` covers every definition in a file. `--json` returns the chains and the
callsite snippets with their hashes.

## verify: the tests to run

`verify` proposes the tests that reach a function, through which calls, and never runs them. Without
`--json`, it lists direct and transitive tests and a command to run them:

<!-- cg: verify cancel_order -->
```text
$ cg verify cancel_order
3 tests reach cancel_order (2 direct, 1 transitive) · runner unittest
run all: python3 -m unittest tests.test_orders.Orders.test_cancel_refunds_and_restocks tests.test_orders.Orders.test_cancel_twice_refunds_once tests.test_api.Api.test_cancel_endpoint (cwd: .)
  direct     tests/test_orders.py:13 Orders.test_cancel_refunds_and_restocks
  direct     tests/test_orders.py:19 Orders.test_cancel_twice_refunds_once
  transitive tests/test_api.py:8 Api.test_cancel_endpoint via shop/api.py:19 handle_cancel
Proposal only; no tests executed.
```
<!-- /cg -->

The text shows up to 20 tests. Larger selections also have a command for the direct tests only.
Very long commands are marked as previews: use `--json` for the complete argv, not the shortened text.
Unknown calls and a need for broader verification are reported when present. A selected test itself
has the `target` label; direct callers are one edge away, and transitive callers are further away.

`--json` keeps the structured answer used by MCP. It finds three tests for `cancel_order`; here is the
one that reaches it through the API:

<!-- cg: verify cancel_order --json | keep=tests.2 -->
```json
{
  "chain": [
    {
      "callee": 7,
      "caller": 29,
      "confidence": "high",
      "file": "tests/test_api.py",
      "line": 12,
      "relation": "call",
      "snippet_ref": "e3abb06f860f458004be6a6c"
    },
    {
      "callee": 22,
      "caller": 7,
      "confidence": "high",
      "file": "shop/api.py",
      "line": 19,
      "relation": "call",
      "snippet_ref": "70c81adad971ffa7ef92f634"
    }
  ],
  "command": [
    "python3",
    "-m",
    "unittest",
    "tests.test_api.Api.test_cancel_endpoint"
  ],
  "command_evidence": [
    "tests/test_api.py"
  ],
  "command_reason": null,
  "command_runner": "unittest",
  "command_scope": "test",
  "cwd": ".",
  "distance": 2,
  "priority": "transitive",
  "reason": "Static calls to the symbol or its callers.",
  "relation": "call",
  "role": "test_case",
  "symbol": {
    "end_line": 12,
    "file": "tests/test_api.py",
    "id": 29,
    "kind": "method",
    "language": "python",
    "line": 8,
    "name": "Api.test_cancel_endpoint",
    "signature": "def test_cancel_endpoint(self):"
  }
}
```
<!-- /cg -->

Each test has the `chain` of calls from the test to your function (`distance` 1 is a direct call), the
`priority` (`direct` or `transitive`) and a `snippet_ref` to the source line of each call in `snippets`.
Each `command` is an argv array to run from `cwd` (`"."`, the project root), using your project's
environment. Python proposals prefer `.venv/bin/python`, then `venv/bin/python` when executable
(the selected path is recorded in `command_evidence`), otherwise `python3`. Detection only inspects
the executable's metadata; it does not start Python. The runner is proposed only from static evidence: pytest configuration, an explicit
`unittest.TestCase` class with an importable module, a root Go module, Jest/Vitest declared in
`package.json`, or PHPUnit: a root `phpunit.xml` or `phpunit.xml.dist` with `vendor/bin/phpunit`
resolving inside the project. A PHPUnit command runs the test file with a `--filter` anchored on the
full `Class::method` name, so data sets of that method run and other methods do not. JS/TS proposals select test **files**, not individual callback titles. No runner is
installed and no test runs during `verify`; unknown or ambiguous runners keep `command: null` with
an English `command_reason`. Scripts with project-specific options (such as `jest --config ...`) also
stay `null`: their options are not silently dropped. Other languages may have no command yet.

The plan also has one aggregate `command` for all selected tests when they share a known runner:

<!-- cg: verify cancel_order --json | keep=command pretty -->
```json
[
  "python3",
  "-m",
  "unittest",
  "tests.test_orders.Orders.test_cancel_refunds_and_restocks",
  "tests.test_orders.Orders.test_cancel_twice_refunds_once",
  "tests.test_api.Api.test_cancel_endpoint"
]
```
<!-- /cg -->

Run that argv yourself from the project root; the example uses unittest and runs the selected tests
without executing unrelated cases. A file command may run other tests in that file; the combined Go
name filter applies to all selected packages. PHPUnit takes one test path per run, so its aggregate
exists only when the selected tests share one file; otherwise run each proposed command. If any selected test has an uncertain command, or the
selection needs different runners, the aggregate remains `null`. In a bounded `review`, the aggregate
still names all selected tests, including tests whose evidence was omitted from the response.

`cg change` continues to require your explicit `--test-argv` approval; these proposals do not approve
or launch anything.

`broader_verification_required: true` means the static walk reached its limits (depth, node budget or
unresolved calls on the way): run the wider suite as well.

## callers, callees, path

<!-- cg: callers refund -->
```text
$ cg callers refund
shop/payments.py:12-17 function refund
  ← shop/orders.py:17-25 function cancel_order  [high] @shop/orders.py:22
```
<!-- /cg -->

<!-- cg: callees cancel_order -->
```text
$ cg callees cancel_order
shop/orders.py:17-25 function cancel_order
  → shop/inventory.py:16-18 function release_order  [high] @shop/orders.py:21
  → shop/payments.py:12-17 function refund  [high] @shop/orders.py:22
  → shop/notifications.py:10-12 function notify_cancelled  [high] @shop/orders.py:24
```
<!-- /cg -->

`callees` also lists calls the graph could not resolve to project code (standard library, external
packages, dynamic calls), so you can see what it does not know.

<!-- cg: path handle_cancel refund -->
```text
$ cg path handle_cancel refund
shop/api.py:18-20 function handle_cancel
  → shop/orders.py:17-25 function cancel_order  [high] @shop/api.py:19
  → shop/payments.py:12-17 function refund  [high] @shop/orders.py:22
```
<!-- /cg -->

`path` is the shortest chain of calls from one function to another.

## context: everything an agent needs about one function

<!-- cg: context cancel_order --max-chars 1500 -->
```text
$ cg context cancel_order --max-chars 1500
## TARGET cancel_order shop/orders.py:17
def cancel_order(order):
revision 91f1bd53507d2b76ec21ed92fdaecf7dbd47a7c6cb8881cd91ad8a9e7cacd5ec
profile change · conf ≥ medium
## CALLERS (4/4)
cancel_unpaid shop/admin.py:5 [high] @shop/admin.py:6 :: def cancel_unpaid(orders):
handle_cancel shop/api.py:18 [high] @shop/api.py:19 :: def handle_cancel(request):
Orders.test_cancel_refunds_and_restocks tests/test_orders.py:13 [high] @tests/test_orders.py:16 :: def test_cancel_refunds_and_restocks(self):
Orders.test_cancel_twice_refunds_once tests/test_orders.py:19 [high] @tests/test_orders.py:22 :: def test_cancel_twice_refunds_once(self):
## CALLEES (1/3)
release_order shop/inventory.py:16 [high] @shop/orders.py:21 :: def release_order(order):
## LIMITATIONS
static analysis; missing edges do not prove there are no callers.
## SOURCE
17: def cancel_order(order):
18:     """Cancel an order: give the stock back and refund what the customer paid."""
19:     if order.status == 'cancelled':
20:         return 0
21:     inventory.release_order(order)
22:     refunded = payments.refund(order, order.paid) if order.paid else 0
23:     order.status = 'cancelled'
24:     notifications.notify_cancelled(order, refunded)
25:     return refunded
## OMISSIONS
source: complete 9/9; callers: complete 4/4; callees: partial 1/3
Expand: context --max-chars <N>; callers; callees.
[max_chars=1500; estimate characters/4: 362; measured tokens: unavailable]
```
<!-- /cg -->

`context` packs the source of a function with its callers, callees and tests within a budget
(`--budget` in tokens of about four characters, or `--max-chars`). `--profile` changes what is kept
first: `understand`, `change` (default), `debug` or `review`.
