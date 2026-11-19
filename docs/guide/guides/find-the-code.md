# Find the code

Three commands, from the vaguest question to the most precise:

| You have | Use | It returns |
|---|---|---|
| A request, an issue or a stack trace, in prose | `locate "…"` | The likely places to change, with source, callers and tests |
| A few words that may appear in the code | `search "…"` | Ranked definitions with the reason each one is there |
| A name, or part of one | `find "cancel_*"` | Matching definitions |

## locate: from a request to the places to change

Paste the request as it was written:

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

How to read it:

- **`[text #N]`**: the place matched the words of the request; `#1` is the best text match.
  `(expanded)` means it matched only through a word root or a synonym.
- **`[graph neighbour of a text match, high]`**: it did not match the words, but it calls or is called
  by one that did.
- **`[stack frame …]`**: it comes from a frame of a stack trace in the text.
- **`[named file]` / `[named symbol]`**: the request explicitly names an indexed file or a qualified
  symbol. This is a location hint, not proof that the bug is there.
- **callers / tests**: how many there are and the first one, with `file:line`. Test files are never
  candidates themselves.

`--json` returns the same answer with up to two source lines per place (100 characters per line),
each file's SHA-256, explicit truncation flags and the
reading counter. Its format is in [Formats](../reference/formats.md#locate).

Names such as `guard-missing-value`, `@rules/guard-missing-value`, `src/cache-store.ts` and
`OrderService.cancel` carry more information than general prose. A named file can enter the
candidates even when its function is called only `create` and is outside the global text-search
pool. Kebab words are treated uniformly as code-like: English prose compounds can receive the same
priority and precede a qualified name. This is a known ambiguity; there are no compound-specific
exceptions. Remove incidental compounds when they crowd the request's useful terms.
Bare basenames shared by several production files are left to normal text ranking; give a
path to disambiguate them. URL hosts, repository/branch prefixes and query/hash payloads are
discarded. Source links keep their indexed path suffix as file evidence, or just the filename when
no indexed path matches; only filename parts enter the query. Directory names in other paths do not
become query words. Only indexed files are eligible: a path in the request is never opened directly.

Frames lead, followed by explicit hints, the two best text matches and the hybrid ranking.
Files named in the request rank before text matches, restoring the order measured in the blind
evaluation. There is no fallback that takes
declarations solely by line number, but FTS indexes the file path for every declaration: filename
words can match all of them even when their names and bodies are unrelated. These path-only matches
can fill remaining slots and do not establish relevance of the declaration's content. Up to two
declarations per file are considered before further repeats; when fewer matching files exist,
remaining declarations can fill the answer. These heuristics suggest places to inspect, and can
still favour a file mentioned as context over the actual cause.

### Stack traces

When the text contains a stack trace, its frames come first: the deepest frame leads, unless another
file appears in more frames, because the raising frame is often a shared helper. With this report saved as
`request.txt`:

```text
Refunds fail in production:
Traceback (most recent call last):
  File "/srv/app/shop/api.py", line 19, in handle_cancel
  File "/srv/app/shop/orders.py", line 22, in cancel_order
  File "/srv/app/shop/payments.py", line 14, in refund
ValueError: refund larger than the captured amount
```

<!-- cg: locate "Refunds fail in production:\nTraceback (most recent call last):\n  File \"/srv/app/shop/api.py\", line 19, in handle_cancel\n  File \"/srv/app/shop/orders.py\", line 22, in cancel_order\n  File \"/srv/app/shop/payments.py\", line 14, in refund\nValueError: refund larger than the captured amount" | lines=13 -->
```text
$ cg locate "$(cat request.txt)"
5 candidate places · ready · searched: handle* cancel* order* value* refund* fail* product* larg* captur* amount*
reading: 1,161 characters here instead of 2,940 in 6 files (60% less to read)

1. shop/payments.py:12 refund  [stack frame shop/payments.py:14 in refund]
   def refund(order, amount):
   callers 1: shop/orders.py:22 cancel_order
   tests 2: tests/test_orders.py:13 Orders.test_cancel_refunds_and_restocks

2. shop/orders.py:17 cancel_order  [stack frame shop/orders.py:22 in cancel_order]
   def cancel_order(order):
   callers 2: shop/admin.py:6 cancel_unpaid
   tests 3: tests/test_orders.py:13 Orders.test_cancel_refunds_and_restocks

…
```
<!-- /cg -->

Frames are matched to the index by path suffix (`/srv/app/shop/api.py` → `shop/api.py`) and to functions
by name before line number, because traces often come from a deployed version whose lines differ.
Python, JavaScript/TypeScript (`at fn (file:line:col)`) and Go (`file.go:line`) frames are recognised.

### Long requests

Requests contain 1 to 200,000 characters. For inputs above 20,000, text search and filename hints
use only the first 20,000 characters. For every input length, the text query has a 512-character budget;
terms that do not fit are omitted and counted in `query.omitted_terms`.
Stack frames are scanned throughout the full request, including the middle and end of long logs.

JSON declares this with `query.input_chars`, `query.analysed_chars` and `query.trimmed: true`, plus
`query.omitted_terms` when needed. Text output adds a short limitation line. The declaration remains
even if the answer budget is too small for a candidate. Inputs of at most 20,000 characters whose
terms already fit the query budget keep their previous answers and have no new metadata. Previously
rejected oversized queries now return an answer with `query.omitted_terms`. This does not change
the answer budget below.

### Budget

The whole answer stays within `--max-chars` (default 5,000; 512 to 16,000). Places that do not fit are
dropped from the end and counted in `omitted`; if not even one fits, the status is
`budget_insufficient`. Raise the budget or lower `--limit` (default 5, at most 10).

The default ceiling was raised from 4,000 to 5,000 characters (+25%) alongside shorter previews.
On the 23 published development bugs, this retained five candidates in every answer, with a median
of 4,045 characters (previously 3,630 characters for three or four candidates). This is a development
measurement, not a guarantee for other requests or a measurement of agent token savings. Long
paths/signatures and smaller explicit budgets can still reduce the candidate count. See the
[development log](../../../benchmarks/locate-dev-log.md) for the full comparison and rejected variants.

## search: words to definitions

`search` is the engine under `locate`, with every detail of the ranking. It prints JSON:

<!-- cg: search "release stock" --limit 3 --max-chars 3000 | keep=items.0 -->
```json
{
  "graph": {
    "chain": [
      {
        "confidence": "high",
        "direction": "in",
        "file": "tests/test_orders.py",
        "line": 16,
        "relation": "call",
        "source": 33,
        "target": 22
      }
    ],
    "confidence": "high",
    "distance": 1,
    "rank": 5,
    "supporting_seeds": [
      11,
      22
    ]
  },
  "lexical": {
    "bm25": -0.7359,
    "match": "exact",
    "rank": 8
  },
  "rrf_score": 0.030090497737556562,
  "source": {
    "source_sha256": "485fd79c1a96ce6c316d2bd9e94dc535cbf2f30628137b59a4368ea72fc6efe9",
    "start_line": 13,
    "text": "    def test_cancel_refunds_and_restocks(self):\n        order = place_order('o1', 'c1', [Line('book', 2, 1500)])\n        pay_order(order)\n        self.assertEqual(cancel_order(order), 3000)\n        self.assertEqual(inventory.STOCK['book'], 5)",
    "truncated": false
  },
  "symbol": {
    "end_line": 17,
    "file": "tests/test_orders.py",
    "id": 33,
    "kind": "method",
    "language": "python",
    "line": 13,
    "name": "Orders.test_cancel_refunds_and_restocks",
    "signature": "def test_cancel_refunds_and_restocks(self):"
  }
}
```
<!-- /cg -->

Each item has `lexical` (its BM25 rank and score, `exact` or `expanded`), `graph` (the call chain from a
seed, with file, line and confidence) or both, and the `rrf_score` that ordered them. The top-level
`query.expansions` lists every root and synonym that was added, and `omissions` says how many ranked
results were cut by `--limit`.

Tips:

- **Use code words.** `refund order` finds more than *give the money back*. When your agent searches for
  you, it reformulates the request into code terms first.
- **Lexical mode** (`--mode lexical`) shows what the words alone find; compare it with hybrid when a
  result surprises you.
- **Depth.** `--depth 1` keeps only direct callers and callees of the seeds; the default is 2, at most 3.

## find and show: names you already know

<!-- cg: find "cancel*" -->
```text
$ cg find 'cancel*'
shop/orders.py:17-25 function cancel_order
shop/admin.py:5-6 function cancel_unpaid
```
<!-- /cg -->

<!-- cg: show cancel_order -->
```text
$ cg show cancel_order
shop/orders.py:17-25 function cancel_order
  signature: def cancel_order(order):
  params 1..1
  called by 4 · calls 3 · dynamic calls in body 0
```
<!-- /cg -->

`find` accepts `*` as a wildcard and `--kind function|method|class`. Ambiguous names are reported with
every match; qualify them (`Order.total`) or pass `file:name` (`shop/orders.py:cancel_order`).

## Reading saved

The **reading** line of `locate` compares the characters of the answer with the characters of the files
its places, callers and tests live in. On the example project the files are a few hundred characters and
the saving is small. It grows with the size of the files, because the answer stays within its budget
while the files do not. It is reading avoided for one question, not a measure of an agent's tokens: see
[Concepts](../concepts.md#reading-saved).
