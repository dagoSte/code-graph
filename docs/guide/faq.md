# FAQ

**Does it send my code anywhere?**
Indexing, search and every answer run on your machine. Nothing calls a model except the
[change flow](guides/change-flow.md), which starts the Codex or Claude client you installed, with your
account, only after you confirm. See [PRIVACY.md](../../PRIVACY.md).

**Does it run my code?**
No. Files are parsed, never imported or executed. Tests run only in the change flow, with the command you
approved.

**Does it use embeddings or an LLM to search?**
No. Search is BM25 full-text over the index, fused with the call graph, plus word roots and a small list of
programming synonyms. It matches words, not meaning: an agent that reformulates a request into code words
gets the best results. See [Concepts](concepts.md#hybrid-search).

**Will it save my agent tokens?**
Not in the studies so far. `locate` reports the size of its answer against the whole files its places,
callers and tests live in; that compares two sizes and does not measure what an agent would actually
have read. In a study on 23 historical bugs agents with code-graph fixed the same bugs and used more
input tokens while planning. See [Benchmarks](benchmarks.md#with-agents).

**Is it worth it on a small project?**
Usually not. Under about 30 source files, reading the files is as fast, and `start` says so.

**How much can I trust the callers it lists?**
Every edge carries a confidence and the line of the call. `high` is a direct resolution (named import,
`self`, inferred type); `low` is a name match. Calls decided at run time can be missing. Use it to find
where to look, then read the code and run the tests.

**Which files are indexed?**
Supported source files, except vendored dependencies, build folders, Git-ignored files, symlinks, files
over 1 MB and minified files. Add globs to `.cgignore` for anything else.

**Where is the index, and can I commit it?**
In `<project>/.code-graph/graph.db`. It excludes itself from Git; do not commit or publish it, because it
contains excerpts of your source. Deleting the folder is safe: the next `index` rebuilds it.

**Which agents does it work with?**
Codex and Claude Code as a skill; any MCP client through `cg mcp`. See [Agents and MCP](guides/agents-and-mcp.md).

**Under which license is it distributed?**
See [LICENSE.md](../../LICENSE.md).
