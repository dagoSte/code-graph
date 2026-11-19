# Formats

The JSON answers of the main commands. Each one names its format and version in `format`: when the
shape of search answers changed, it became `codegraph-search/2`. Keys are in English. The examples are
real answers on the [example project](../example/shop/README.md).

Fields shared by most answers:

| Field | Meaning |
|---|---|
| `format` | Name and version of the format, e.g. `codegraph-locate/1` |
| `engine_version` | code-graph version that produced the answer |
| `revision`, `content_revision` | Identity of the index and of the indexed content the answer comes from |
| `limitations` | What the method cannot see for this question |
| `source_sha256` | SHA-256 of the file a source window comes from, to check it is unchanged |

## locate

`cg locate "…" --json` and MCP `cg_locate`: format `codegraph-locate/1`.

<!-- cg: locate "Cancelling an order does not refund the customer" --json --limit 1 | pretty -->
```json
{
  "engine_version": "1.9.0",
  "format": "codegraph-locate/1",
  "items": [
    {
      "callers": {
        "count": 2,
        "shown": [
          "shop/admin.py:6 cancel_unpaid"
        ]
      },
      "end_line": 25,
      "file": "shop/orders.py",
      "files": [
        "shop/admin.py",
        "shop/orders.py",
        "tests/test_orders.py"
      ],
      "kind": "function",
      "line": 17,
      "name": "cancel_order",
      "signature": "def cancel_order(order):",
      "source": {
        "lines_cut": 0,
        "source_sha256": "77f47042ebe142ab34da8909edb5406db4ac786e86141e4f8d2c437c2923a478",
        "start_line": 17,
        "text": "def cancel_order(order):\n    \"\"\"Cancel an order: give the stock back and refund what the customer paid.\"\"\"",
        "truncated": true
      },
      "tests": {
        "count": 3,
        "shown": [
          "tests/test_orders.py:13 Orders.test_cancel_refunds_and_restocks"
        ],
        "truncated": false
      },
      "why": "text #2"
    }
  ],
  "limitations": [
    "Static candidates: verify in source and tests.",
    "Listed tests were not executed."
  ],
  "query": {
    "confidence": "medium",
    "frames": 0,
    "limit": 1,
    "searched": [
      "cancel*",
      "order*",
      "refund*",
      "custom*"
    ],
    "terms": [
      "cancelling",
      "order",
      "refund",
      "customer"
    ]
  },
  "reading": {
    "file_chars": 1651,
    "files": 3,
    "meaning": "file characters for shown candidates/callers/tests versus this answer; not agent tokens",
    "returned_chars": 1252,
    "saved_ratio": 0.242
  },
  "revision": "1965878c6bda6241aade5c67744bf82c51ddfcd6549da444ee3e593dedbf3757",
  "status": "ready"
}
```
<!-- /cg -->

| Field | Meaning |
|---|---|
| `status` | `ready`, `empty` (no usable words or no match) or `budget_insufficient` (not even one place fits) |
| `query.terms` | Words taken from the request; `query.searched` the roots actually searched; `query.frames` stack frames used |
| `query.input_chars`, `query.analysed_chars`, `query.trimmed` | Present only above 20,000 input characters: lexical analysis uses the first 20,000, while stack frames are scanned throughout the request (maximum 200,000) |
| `query.omitted_terms` | Present at any input length when terms exceed the 512-character search query budget; those terms were omitted |
| `items[].why` | `text #N`, `text #N (expanded)`, `graph neighbour of a text match, <confidence>`, `stack frame <file:line in function>`, `named symbol` (qualified name) or `named file` (file hint before text matches; may match only the indexed path) |
| `items[].source` | First lines of the definition from `start_line`; `truncated` with `lines_cut` when lines or width were cut |
| `items[].callers`, `items[].tests` | Count and the first one, as `file:line name`; the count can exceed the shown preview |
| `items[].files` | Files this place, its callers and tests live in; they make `reading.file_chars` |
| `reading` | `returned_chars` (this answer), `file_chars` (those files), `saved_ratio`; `exact: false` when `returned_chars` is an upper bound by a few characters |
| `omitted` | Places dropped to stay within `--max-chars` |

## search

`cg search` and MCP `cg_search`: format `codegraph-search/2`. One result:

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

| Field | Meaning |
|---|---|
| `lexical` | `rank` and `bm25` score of the text match, `match` = `exact` or `expanded`; `null` if found only by the graph |
| `graph` | `chain` of call edges from a seed (file, line, direction, confidence), `distance`, `rank`, `supporting_seeds`; `null` if found only by text |
| `rrf_score` | Reciprocal Rank Fusion score that ordered the results |
| `source` | Window from `start_line`; when `truncated`, `window.continuation.argv` is the `cg` command that reads on |
| `query.expansions` | Roots and synonyms added to the words, with the word each came from |
| `retrieval` | Constant parameters: BM25 body prefix, RRF `k`, graph limits, source window size |
| `omissions` | `ranked`, `shown`, `omitted` and why |
| `budget` | `max_chars` and `actual_chars` of the answer |

## impact

`cg impact SYMBOL --json` and MCP `cg_impact`: format `codegraph-impact/1`. One caller:

<!-- cg: impact cancel_order --json | keep=items.0 -->
```json
{
  "chain": [
    {
      "callee": 22,
      "caller": 3,
      "confidence": "high",
      "file": "shop/admin.py",
      "line": 6,
      "relation": "call",
      "snippet_ref": "f48b682337927fb0de6edddf"
    }
  ],
  "distance": 1,
  "relation": "call",
  "role": "production",
  "symbol": {
    "end_line": 6,
    "file": "shop/admin.py",
    "id": 3,
    "kind": "function",
    "language": "python",
    "line": 5,
    "name": "cancel_unpaid",
    "signature": "def cancel_unpaid(orders):"
  }
}
```
<!-- /cg -->

| Field | Meaning |
|---|---|
| `items[]` | Callers up to `depth`, each with `distance`, `role` (`production` or `test_case`) and the `chain` of calls down to the symbol |
| `chain[].snippet_ref` | Key into `snippets`, the hashed source line of that call |
| `tests` | Test cases reached through the callers |
| `pages` | `shown`, `omitted`, `total` and `next_cursor` for `items` and `tests` |
| `measurement` | Size of this answer against repeating every chain with its snippet (`saved_chars` can be negative) |
| `truncated` | `true` when the node limit cut the walk; callers beyond `--depth` are simply not followed and do not set it |

## verify

Without `--json`, the CLI prints a short text summary; MCP keeps the structured answer described here.

`cg verify SYMBOL` and MCP `cg_verify_plan`: no `format` field yet; `mode` is always `proposal_only` and
`execution_performed` always `false`.

| Field | Meaning |
|---|---|
| `tests[]` | Test cases, or JS/TS test files, that reach the symbol: `chain`, `distance`, `priority` (`direct` or `transitive`), `symbol` |
| `tests[].command` | Proposed argv (an array, never a shell string), or `null` when the runner or selector is uncertain |
| `tests[].cwd`, `cwd` | `"."`: run from the project root, using its environment and installed dependencies |
| `tests[].command_reason`, `command_reason` | Short English reason when no command can be proposed |
| `tests[].command_runner`, `tests[].command_evidence` | Statically detected runner and the project files that identify it; includes a project Python executable when one is selected (`.venv/bin/python`, then `venv/bin/python`, otherwise `python3`) |
| `tests[].command_scope` | `test` for a named case, `file` for a JS/TS suite; a file command can run additional cases |
| `command` | One argv for all selected tests when they share a known runner; `null` for mixed or uncertain runners. In a bounded review this still covers the full selection, including omitted evidence |
| `snippets` | Source lines of the calls in the chains, by `snippet_ref` |
| `unknown_calls` | Calls on the way that could not be resolved |
| `broader_verification_required` | `true` when unresolved calls, limits or deletion-only changes mean the wider suite should run |

## review

Without `--json`, the CLI prints definitions, finding counts and the test plan as text. `--json` and MCP keep the existing JSON fields.

`cg review --base REV` and MCP `cg_review`: format `codegraph-review/1`. With `--summary --json` (MCP `summary: true`) the
answer is `codegraph-review-summary/1`: `base`, `revision`, `counts`, `new`, `tests` (name, file, line,
distance, command), the aggregate `command` with `cwd`, `limitations` and `output` (what the budget omitted). See
[Review a change](../guides/review-a-change.md) for `symbols`, `comparison`, `summary` and `verification`.
