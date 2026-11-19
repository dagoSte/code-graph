# Privacy notice

Updated 6 November 2026.

code-graph runs on your computer. Its static engine and bundled dashboard do not
send source files or usage telemetry to the maintainer. The optional agent change
flow is different: when you authorize Codex or Claude, those clients communicate
with their providers using your account.

## What stays on your computer

| Location | What it can contain |
|---|---|
| `<project>/.code-graph/graph.db` | File names, symbols, signatures, source fragments, hashes, call relationships and index metadata |
| SQLite side files such as `.code-graph/graph.db-journal` | Temporary database contents during transactions; interrupted writes can leave them behind |
| `<project>/.code-graph/<name>.json` | Static review baselines saved explicitly with CLI `review --save-baseline`; findings and index identifiers |
| `<project>/.code-graph/explore.html` | The default HTML graph export, including symbol names, relative paths, signatures and code positions |
| `<project>/.code-graph/changes/<id>/` | Your intent, plan, approvals, affected paths, test commands and results, diff, review, events, timestamps and client run logs |
| `<project>/.code-graph/worktrees/<id>/` | A separate Git working tree, including source files and any uncommitted work from the change |
| The project's Git repository | Change branches and commits; integrating or discarding a worktree does not erase Git history |
| Exported HTML, context packets and handoff files | The source excerpts, paths and analysis evidence included in each export |

Agent run logs can include raw client output, tool events, responses, session
identifiers, command arguments and error text. They are not a secret-redaction
service. The engine does not dump environment variables into its run metadata,
but tests inherit the process environment. Their retained stdout/stderr tails
can contain anything the tests print, including secrets. Those tails are stored
in the change archive and exposed through the authenticated local API; the page
shows test status, counts and names rather than all raw output.

Read-only questions and prepared comparison state are held in the running
dashboard's memory. The client may keep its own session history independently.
The browser stores the dashboard token in session storage and language/theme
preferences in local storage. Exports downloaded from the browser are separate
files under your control.

Analysis may also create temporary data: `cg_review` with a Git base can copy a
snapshot and build an index in its own temporary directory. Agent execution uses
temporary client configuration/schema files. Normal cleanup removes these
temporary directories; an abrupt termination can leave remnants. None of these
locations is an encrypted vault, and copies and backups have their own access
permissions. Do not publish `.code-graph` or confidential exports.

## The dashboard and model providers

The dashboard binds to `127.0.0.1` and serves its bundled assets locally. Static
navigation and analysis do not call a model or upload a project. Selecting a
project is not consent to start an agent. Access to the local API requires the
session token; this does not protect against a process or user who can read your
files or client account on the same machine.

In the dashboard, starting a question or agent step requires session consent.
On the command line, `cg change plan`, `edit` and `review` ask for confirmation
before they start Codex or Claude and send project context to its provider: type
`y` in a terminal or pass `--yes`; without either they refuse and send nothing.
Let an agent pass `--yes` only on your explicit request for that step with that agent.

The client can send the prompt, intent, plan, selected source, MCP tool results,
diffs, test results and review context to its provider. Prompts and tool results
can include absolute local paths, which may reveal your operating-system user
name and folder names. The files and excerpts the agent requests can expose more
than the initially selected symbol. `cg_read` enforces its own project-root
boundary; confinement of the client's other file tools depends on the client
honoring its permissions and settings. Use the flow only for material you are
permitted to share with that provider.

Provider processing, retention, account history and billing follow your client,
account settings and provider terms. code-graph does not set a provider retention
policy or delete data already sent. Read-only mode requests source-editing
restrictions from the client; it does not mean the interaction stays offline.
Revoking dashboard consent prevents subsequent starts in that session, including
delayed starts of already accepted workflow steps. It does not retract earlier
requests or itself cancel an already running agent; use the cancellation controls
as well.

Installation and updates contact GitHub and the configured Python package index.
Approved tests and configured Git drivers or filters can also use the network
or other local resources. They are project code, not offline analysis. See
[SECURITY.md](SECURITY.md) for these boundaries.

## Retention and removal

Project state remains until you remove it; there is no automatic archive expiry.
Before cleanup, stop active agents/tests and the dashboard, and inspect what
you want to keep. In particular, do not delete `.code-graph` as if it contained
only an index while changes still have worktrees or unintegrated edits.

- For a change you no longer need, `change discard <id>` removes its own worktree
  and branch but deliberately retains its archive. Save any wanted edits first.
- Both integration modes remove the worktree and keep the change branch. In
  branch mode it remains for a later merge; merge mode keeps it after merging too.
  Preserve or delete that branch deliberately; deleting the archive does not
  remove it or any merged commits.
- Once all active worktrees have been dealt with and needed evidence saved, remove
  the intended project's `.code-graph` directory to remove its local index and
  archives. If you want to retain workflow records, remove only the index database
  while nothing is using it; the next `index` recreates it. Do not delete the
  project's Git directory as a data-cleanup step.
- Remove exported reports, downloaded handoffs, browser site data and separate
  backups under your own retention policy. Uninstalling the skill does not remove
  those copies or project data.
- Use the clients' and providers' own controls for their histories and remote
  copies. Filesystem deletion is not a guarantee of secure erasure.

Do not put real source, credentials or personal data in a public support issue.
Use a synthetic reproduction. For sensitive disclosures, follow
[SECURITY.md](SECURITY.md).
