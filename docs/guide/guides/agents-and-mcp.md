# Agents and MCP

code-graph reaches an agent in two ways: as a **skill** the agent reads and runs from the shell, or as
an **MCP server** whose tools any MCP client can call. Both give the same answers.

## As a skill (Codex, Claude Code)

Link the installed folder into the agent's skills (see [Getting started](../getting-started.md#1-install)),
start a new session and ask in plain words:

```text
Use code-graph to prepare this change: cancelling an order must also refund partial payments.
Show me where to intervene, the possible impact and the tests to run.
```

The skill tells the agent to:

1. call `locate` with the request text first, and read only the places it returns;
2. run `impact` and `verify` once while planning, not after every edit;
3. read bounded line ranges instead of whole files, and cite `file:line`;
4. treat the graph as a guide and let the tests decide.

Prompts that work well:

```text
If I change OrderService.cancel, which parts may be affected? List the tests that reach it.
```

```text
Prepare the context to fix this error: [paste the stack trace]. Show me the references in the code.
```

```text
Trace the flow from CheckoutController.submit to OrderRepository.save and list the steps the graph could not resolve.
```

```text
Compare before and after this change: what changed, and which checks are still needed?
```

## As an MCP server

```bash
cg --root /path/to/project mcp
```

The server speaks MCP over stdio and serves the tools listed in the [MCP reference](../reference/mcp.md).
It is bound to the project given with `--root`: clients cannot choose another folder or command. No tool
edits your sources or runs the analysed code; `cg_index` only refreshes `.code-graph/`. Responses stop
at 40,000 characters with an explicit marker.

**Claude Code**, for one project:

```bash
claude mcp add code-graph -- "$HOME/code-graph/scripts/cg" --root /path/to/project mcp
```

**Codex**, in `~/.codex/config.toml`:

```toml
[mcp_servers.code-graph]
command = "/Users/you/code-graph/scripts/cg"
args = ["--root", "/path/to/project", "mcp"]
```

Other clients take the same command and arguments. Check your client's documentation for where its MCP
servers are configured.

### Which tool for which question

| Question | Tool |
|---|---|
| Where do I change for this request or stack trace? | `cg_locate` |
| Where is the code that does X? | `cg_search` |
| What is this symbol, and who calls it? | `cg_show`, `cg_callers`, `cg_callees` |
| What breaks if I change it, and which tests reach it? | `cg_impact`, `cg_verify_plan` |
| What did my change touch? | `cg_review`, `cg_check` |
| Show me these lines | `cg_source` (a window around a line), `cg_read` (numbered lines of a file) |
| Everything about a feature, within a budget | `cg_prepare` |

## Spending fewer tokens

The answers are bounded and say what they left out, so an agent can stop reading early. What saves the
most is the order of the calls:

- **Locate first.** One `cg_locate` call replaces a round of searching and opening files. Its
  `reading` field shows the characters the agent did not have to read.
- **Plan once.** `cg_impact` and `cg_verify_plan` belong to the plan; calling them again after each edit
  repeats the same answer.
- **Read lines, not files.** `cg_read` with `start_line` and `end_line`, or the source windows that
  `cg_locate` and `cg_search` already return.

The live pilots in [Benchmarks](../benchmarks.md) report agent tokens with and without code-graph,
including the cases where it used more.

## Change flow

For a change carried out by one model and reviewed by the other, with your approval at the plan and
before integration, see [Change with an agent](change-flow.md). It passes this MCP server to both models
automatically.
