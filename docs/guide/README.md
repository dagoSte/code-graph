# code-graph guide

code-graph indexes a codebase on your machine and answers the questions a change needs: **where is the
code for this request, what calls it, which tests reach it, and what did my change touch**. It answers
from a local SQLite file without calling a model, so your agent reads a few hundred lines it can verify
instead of opening files one by one.

## Start here

| If you want to… | Read |
|---|---|
| Install it and ask a first question | [Getting started](getting-started.md) |
| Understand how the answers are built and how far to trust them | [Concepts](concepts.md) |
| Look up a command or an MCP tool | [CLI reference](reference/cli.md) · [MCP reference](reference/mcp.md) |

## Guides by task

| Task | Guide | Main commands |
|---|---|---|
| Find where to change for a request, an issue or a stack trace | [Find the code](guides/find-the-code.md) | `locate`, `search`, `find` |
| Know what breaks and which tests to run before you change | [Impact and tests](guides/impact-and-tests.md) | `impact`, `verify`, `callers` |
| Check what a change actually touched | [Review a change](guides/review-a-change.md) | `review`, `check` |
| Give the same answers to Codex, Claude Code or any MCP client | [Agents and MCP](guides/agents-and-mcp.md) | skill, `mcp` |
| Browse a project visually | [Dashboard](guides/dashboard.md) | `explore` |
| Start from Payment or another functional purpose, then open the code and tests | [Two maps](guides/two-maps.md) | `flows`, `prepare`, `verify` |
| Hand a change to one model and have the other review it | [Change with an agent](guides/change-flow.md) | `change` |
| Share an offline picture of the call graph | [Export](guides/export.md) | `export` |
| Work on large repositories and keep the index fresh | [Large projects](guides/large-projects.md) | `index`, `.cgignore`, `stats` |

## Reference

- [CLI reference](reference/cli.md): every command and option, generated from the code.
- [MCP reference](reference/mcp.md): every tool and parameter, generated from the code.
- [Formats](reference/formats.md): the JSON answers of `locate`, `search`, `impact` and `verify`.
- [Languages](languages.md): what is resolved in each language, and where the analysis abstains.
- [Benchmarks](benchmarks.md): measured indexing speed, retrieval and agent pilots, with their limits.
- [FAQ](faq.md) and [Troubleshooting](troubleshooting.md).

## When it helps, and when it does not

code-graph pays its way when the code you need is not in a file you already have open: a shared helper
with many callers, a request worded differently from the code, a project too large to read. On a few
small files, reading them is as cheap and the graph adds nothing; the [benchmarks](benchmarks.md) show
this too.

It is a static analysis. A call that is only decided at run time (dynamic dispatch, reflection,
dependency injection, routes registered by string) can be missing from the graph, and every answer says
how a call was resolved. Treat the answers as a fast, verifiable guide to the code, and let the tests
decide.

## The example project

Every example in this guide runs on [`example/shop`](example/shop/README.md), a small order service with tests,
and shows real output: `tools/docs_examples.py` re-runs the commands and a test fails when a page
no longer matches what the code prints.
