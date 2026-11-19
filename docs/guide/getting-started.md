# Getting started

Ten minutes from install to a first answer you can check in the source.

## 1. Install

You need Git, Bash and Python 3.10 or newer with `venv`. Nothing is installed in the system Python.

**With an agent (recommended).** Paste this into Claude Code or Codex:

```text
Install code-graph by following https://github.com/dagoSte/code-graph/blob/main/SETUP.md: show me the plan first, wait for my yes, then install it and check that it works.
```

**By hand.**

```bash
git clone https://github.com/dagoSte/code-graph.git "$HOME/code-graph"
cd "$HOME/code-graph"
bash scripts/setup.sh                       # CG_PYTHON=python3.12 bash scripts/setup.sh to pick an interpreter
```

`setup.sh` creates `.venv` inside the folder, installs the pinned dependencies as binary wheels and
runs the self-test. The parsers ship inside a wheel, so indexing works offline.

To let your agents use it as a skill, link the whole folder (not `SKILL.md` alone):

```bash
mkdir -p "$HOME/.claude/skills" "$HOME/.codex/skills"
ln -s "$HOME/code-graph" "$HOME/.claude/skills/code-graph"   # Claude Code
ln -s "$HOME/code-graph" "$HOME/.codex/skills/code-graph"    # Codex
```

The examples below call the launcher as `cg`. Either add an alias or use the full path:

```bash
alias cg="$HOME/code-graph/scripts/cg"
```

## 2. Check the installation

```bash
cg selftest
```

It runs 25 parser and call-resolution checks on built-in fixtures and must end with `25/25 → TRUST`.
If it does not, see [Troubleshooting](troubleshooting.md).

## 3. Start on a project

`start` indexes the project, tells you whether it is large enough for the graph to pay its way, and
prints three questions with commands that work as printed. Run it in your own project, or copy the
example project of this guide and follow along:

```bash
mkdir -p ~/code && cp -R "$HOME/code-graph/docs/guide/example/shop" ~/code/shop
cd ~/code/shop && git init -q && git add -A && git commit -qm shop   # review needs a Git base
```

Every `cg` command runs on the current folder; `--root PATH`, written before the command, picks another.
On the example project:

<!-- cg: start --lang en -->
```text
$ cg start --lang en
code-graph · shop

11 source files · 23 definitions · 26 resolved calls · languages: python 11

Small project (11 source files). Reading the files directly is usually just as fast here: in our live pilot on projects of about ten files, agents fixed every task with and without code-graph, and the graph tools only added time. code-graph is meant for larger or unfamiliar code.

Three things to try now

1. What breaks if I change it, and which tests should I run?
   cancel_order is called from 4 places; 3 tests reach it through those calls.
   $ cg --root ~/code/shop impact shop/orders.py:cancel_order --depth 3
   $ cg --root ~/code/shop verify shop/orders.py:cancel_order --plan

2. Where is the code for a request?
   Describe it with words that may appear in the code. Hybrid search ranks matching declarations and adds the functions they call or are called by.
   $ cg --root ~/code/shop search 'cancel order'

3. Did my change touch anything I did not intend?
   After editing, compare signatures, calls and findings with the last commit.
   $ cg --root ~/code/shop review --base HEAD

With Codex or Claude Code
   Paste this into your agent; it runs the commands above and reads the source it points to:
   «Use code-graph to prepare this change: [describe it]. Show me where to intervene, the possible impact and the tests to run.»

To explore visually:
   $ cg explore
```
<!-- /cg -->

The index lives in `<project>/.code-graph/graph.db`. It excludes itself from Git; do not publish it,
because it contains excerpts of your source.

## 4. Find the code for a request

Describe the request as you would to a colleague. `locate` picks the words that look like code, searches
the index and returns the likely places to change with their callers and tests:

<!-- cg: locate "Cancelling an order does not refund the customer" -->
```text
$ cg locate 'Cancelling an order does not refund the customer'
5 candidate places · ready · searched: cancel* order* refund* custom*
reading: 981 characters here instead of 2,848 in 6 files (66% less to read)

1. shop/orders.py:17 cancel_order  [text #2]
   def cancel_order(order):
   callers 2: shop/admin.py:6 cancel_unpaid
   tests 3: tests/test_orders.py:13 Orders.test_cancel_refunds_and_restocks

2. shop/notifications.py:10 notify_cancelled  [text #1]
   def notify_cancelled(order, refunded):
   callers 1: shop/orders.py:24 cancel_order
   tests 2: tests/test_orders.py:13 Orders.test_cancel_refunds_and_restocks

3. shop/api.py:18 handle_cancel  [text #7]
   def handle_cancel(request):
   callers 0: —
   tests 1: tests/test_api.py:8 Api.test_cancel_endpoint

4. shop/admin.py:5 cancel_unpaid  [text #12]
   def cancel_unpaid(orders):
   callers 0: —
   tests 0: —

5. shop/notifications.py:6 send_email  [text #9]
   def send_email(customer_id, subject, body):
   callers 1: shop/notifications.py:11 notify_cancelled
   tests 0: —
```
<!-- /cg -->

Each place says **why** it was chosen: a text match (`text #2` is the second best), a graph neighbour of
one, or a frame of a stack trace you pasted. The **reading** line compares this answer with the files
you would otherwise open to find the same places. On a project this small the files are short, so the
saving is modest; on large files it grows (see [Find the code](guides/find-the-code.md)).

## 5. Ask what a change would break

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

Every caller comes with the line where the call happens and how the call was resolved (`high` here: a
direct import). The tests at the end are the ones that reach `cancel_order` through those calls; they
are listed, never run.

## 6. Use it from your agent

With the skill linked, ask in plain words:

```text
Use code-graph to prepare this change: cancelling an order must also refund partial payments.
Show me where to intervene, the possible impact and the tests to run.
```

The agent runs `locate`, `impact` and `verify`, then reads only the lines they point to. To use the
tools from any MCP client, see [Agents and MCP](guides/agents-and-mcp.md).

## Next

- [Concepts](concepts.md): how the index, the graph and hybrid search work, and their limits.
- [Find the code](guides/find-the-code.md), [Impact and tests](guides/impact-and-tests.md),
  [Review a change](guides/review-a-change.md).
- [Dashboard](guides/dashboard.md): `cg explore` opens the same answers in the browser.
