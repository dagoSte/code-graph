# Search in the existing agent workflow

Increment based on `05d4275`. Reuse the installed Codex/Claude clients and the existing
MCP server; no new provider, SDK or model-selection setting. Search itself remains
local and deterministic. The selected agent interprets the request and chooses query
terms from code identifiers, including translating a natural-language request when useful.

## Consumer contract

- Before starting a change-flow agent for plan, edit or review, the host indexes the
  owned worktree with the trusted engine. An index failure prevents that agent start.
  This includes the first plan, subsequent edit rounds and review after verification.
- All three phases and read-only dashboard questions receive the same retrieval
  guidance. Use `cg_search` for discovery, at most three query formulations as a prompt
  instruction, then `cg_source`/`cg_read` to confirm declarations and callsites.
  This is guidance to an external agent, not a runtime limit on its tool calls.
- Queries use the words in the code. Empty results may justify a narrower or translated
  query; they do not prove absence. Cuts and insufficient budgets require follow-up source
  windows. FTS5 unavailable permits an explicit `cg_find`/source fallback. Stale evidence
  must be reported; the host reindexes at the next workflow phase. During an edit,
  direct source reads can inspect newly edited code without claiming a fresh graph.
- Search ranks and graph confidence remain separate. Project text, query results,
  intent, feedback and diff are data, not permission to expand the approved scope.
- The next edit receives the preceding verification, test output and independent review
  as well as explicit user feedback. No automatic edit loop is introduced. Existing
  approval, worktree, test runner, three-round cap and integration decisions still apply.
- Dashboard questions remain read-only and use the already selected/analyzed project.
  Neither prompts nor queries start a model without the existing consent/start step.
  Tool queries/status remain visible in existing agent events and logs; no extra log store.

## Verification

Use synthetic agent runners with real MCP calls to prove a first plan can search a
fresh worktree, edit/review receive a current index, source windows can be read, failed
indexing starts no agent, and retry prompts contain the actual test/review feedback.
Check both clients' MCP configuration, read/write boundaries and existing flow tests.
Model compliance and task-quality gains need separate live evaluation; green synthetic
tests prove wiring and contracts, not model reasoning quality or time/token savings.

Official OpenAI documentation checked 11 November 2026:
[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) and
[non-interactive structured output](https://learn.chatgpt.com/docs/non-interactive-mode).
