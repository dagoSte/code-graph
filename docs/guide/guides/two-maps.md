# Two maps: technical structure and functional purpose

The dashboard keeps two separate views. **Technical map** shows symbols and their static calls in
files and directories. **Functional map** starts with names such as *Payment*, *Orders* or *Shipping*,
then takes you to the exact functions, files, impact and proposed tests. The header's **User guide** panel shows how to choose a view and opens the full manual from the local server. **Project guide** prepares tasks for the project you opened.

Functional names come from the project's declarations. A directory called `payments/`, or a class
called `PaymentService`, does not prove which business flow it belongs to. When there are no
declarations, the map shows technical candidates separately, ready to inspect and name.

## Give a flow a name

Add a `features` array to `.code-graph-manifest.json` in your project's root. Keep any existing fields
and features. Check the file and qualified symbol name against your source first:

```json
{
  "format": "codegraph-manifest/1",
  "features": [
    {
      "id": "payment",
      "title": "Payment",
      "summary": "Charge an order and record the receipt",
      "entrypoints": [{"file": "payments.py", "symbol": "charge"}],
      "members": [{"file": "payments.py", "symbol": "record_receipt"}]
    }
  ]
}
```

Names can be in your own language. An agent can propose declarations after reading the source; you
review them like other project documentation. Then run `cg --root /path/to/project index` or refresh
the dashboard. No new schema, database or model service is required.

Open **Functional map**, search for *Payment*, then click **Files, impact and tests**. This opens the
existing feature view and its **Understand**, **Prepare for AI** and **Review change** tabs. A planned
flow or a missing reference remains visible as a declaration, without claiming it is implemented.

## Use the same map from an agent

```bash
cg --root /path/to/project flows "Payment" --json
cg --root /path/to/project prepare declared:payment --json
cg --root /path/to/project verify payments.py:charge --plan --json
```

MCP clients use `cg_flows` with `query: "Payment"`, then `cg_prepare` with
`feature: "declared:payment"`. `flows` is an overview: it does not traverse every feature or compute
every test plan. Details are requested only for the feature you choose. This gives the agent a
smaller routing view; it does not establish a token or task-time saving for an LLM.

## What the arrows mean

An arrow is a direct, statically resolved call between references uniquely assigned to two declared
features, with confidence, file, line and source hash. It is **not** an execution sequence or proof of
business behavior. Open **Why they are connected** to see its witnesses. Code shared by several
features is listed separately; it does not create an exclusive ownership claim or a flow arrow.

The JSON format is `codegraph-functional-map/1`. The default character budget is 12,000, including
the final newline; its maximum is 16,000. It reports omitted nodes, links, witnesses and candidates.
Unknown totals after a scan cutoff are `null`. The overview returns at most 24 technical candidates,
500 links and three witnesses per link. Its map-builder queries read at most 10,000 SQL result rows, including lookahead
(3,000 edge rows and 500 candidate rows); these are result-row bounds, not SQLite VM/time limits.
Source freshness is checked for consulted files and for the engine. It does not certify unvisited
files or discover a newly added, unindexed source. Re-index after changes.

Nothing in this map runs tests or executes project code. The technical map remains available for
the detailed topology, and [impact and tests](impact-and-tests.md) explains the checks to run.
