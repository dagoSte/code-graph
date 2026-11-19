# Security policy

## Supported versions

Security fixes target the latest release, currently **1.7.0**, and `main`.
Older versions do not have a separate maintenance branch. Check the
[release history](CHANGELOG.md) and whether the issue still exists in the latest
version before reporting it, when you can do so safely.

## Reporting a vulnerability

Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/dagoSte/code-graph/security/advisories/new),
or open this repository's **Security** tab and choose **Report a vulnerability**.
Do not put exploit details, real source code or credentials in a public issue,
discussion or pull request. If private reporting is unavailable, ask the
maintainer for a private contact channel without including the sensitive details.

Include:

- The code-graph version and commit, installation method, operating system,
  Python version and relevant Codex/Claude client version.
- What an attacker must control: source files, Git configuration, client settings,
  a web page, a local process, or the dashboard token.
- The smallest reproduction in a disposable repository, expected behavior and
  actual behavior. Use synthetic files and values rather than real secrets.
- Whether the issue affects static analysis, an agent step, approved tests, Git
  integration, or a combination of those operations.

There is no guaranteed response time. Use the private report for follow-up and
coordinate any public disclosure there. Problems confined to a coding client or
model service should also be reported to that provider.

## Trust boundaries

**Static analysis.** Indexing parses source; it does not import the project's
modules or run its declared commands. MCP tools do not modify source files or
run project tests. `cg_index` writes the local cache; `cg_review` with a base may
write its own temporary snapshot and index. Source/path checks reject symlinks
where required and constrain tool roots. They are not isolation against an
untrusted process changing the filesystem concurrently.

**Local dashboard.** The server binds only to `127.0.0.1`. Its API checks a session
token and Host, and state-changing requests check Origin when supplied. Requests
have size limits, bundled assets are served locally, and agent events are
rendered as text. Keep the token private. Do not expose the server through a
proxy, tunnel or public interface. Loopback and a token are not a boundary
against a compromised local account or malicious browser extension.

**Agent consent and tools.** No dashboard agent starts without session consent;
the dashboard's runner gate checks consent under the same lock as revocation
immediately before starting the client. On the CLI,
`cg change plan`, `edit` and `review` ask for confirmation before starting the client
and sending context to its provider: without a terminal they require `--yes` and
otherwise refuse (exit 2). Pass `--yes` only on the user's explicit request for that
step with that agent. Codex uses `read-only` or `workspace-write` with
its shell tool disabled and approval policy `never`. Claude uses `plan` or `acceptEdits`
with an explicit tool set and no Bash. Both receive an explicit code-graph MCP
configuration. Project rules/hooks/settings are disabled through client flags;
Codex also ignores its user configuration, so its usual provider/profile defaults
do not apply. Claude's empty `--setting-sources` disables user settings as well
as project settings. These controls depend on the installed client honoring those
options; the recorded client checks used Codex 0.167.0 and Claude 2.1.319.

**Working copy and approvals.** Planning, editing and review run in a linked Git
worktree rooted at `.code-graph/worktrees/<id>`; questions use the project root
in read mode. Editing requires an approved plan. Its test argv may be empty only
with an explicit `no_tests_reason` to skip tests. The engine compares changed
paths with the approved scope and checks test results before integration.
That comparison only covers changes inside the worktree and happens after
editing and test execution; it cannot detect or prevent writes elsewhere.
`cg_read` enforces its own root boundary. Confinement of the client's file tools
depends on the installed client honoring its settings. A Git worktree shares
repository metadata with the original checkout and is not a virtual machine or
a security sandbox.

**Tests.** Within the built-in change flow, only the engine starts the approved
test argv, without a shell, in the change worktree. Tests run as your user with
the inherited environment and a timeout; they are not sandboxed. During `verify`,
tests run before the scope check and agent review; they can execute the agent's
changed code, including files later found outside the approved scope. A test command
can execute arbitrary project code, reach other files, use credentials or access
the network. Review the command and trust the repository before approving it.
Cancellation targets the workflow's own process groups, not unrelated processes.

**Git integration.** The engine never runs `git push`. Local merge requires a
clean destination, the original branch and a HEAD that contains the base commit;
conflicts are aborted. An override reason can bypass the review verdict and
verification blockers, never those Git checks. Git commands through the change
flow's `_git` helper disable hooks, filesystem monitor hooks and commit signing.
This is not a guarantee for every Git command in code-graph: static review uses
a separate helper that disables fsmonitor but does not disable hooks or signing;
its `git diff` can invoke configured clean filters. The flow's helper also does
**not** disable configured merge drivers or clean/smudge filters selected through Git configuration and
`.gitattributes`, including tools such as Git LFS. These can run project code and
access the network during Git operations. Inspect that configuration first.
Keeping the result on a branch postpones the merge; it does not make a later
manual merge safe or undo filters used earlier in the workflow.

**Untrusted content and limits.** Source, prompts and tool responses can contain
misleading instructions. Treat project content as data and verify proposed
commands and changes. Static confidence, `TRUST` on fixtures, a passing suite or
an agent's accepting review does not prove the absence of vulnerabilities. No
general reduction in errors, time or model tokens has been established.

## Data and known limits

The cache, change archives, diffs and logs can contain confidential source and
test output. They are local files, not an encrypted store; an output limit is not
secret redaction. See [PRIVACY.md](PRIVACY.md) for provider access and removal.

In the recorded 1.6.0 native smoke run, Codex planned and edited, and Claude
reviewed with the verdict `changes`. Test-created `__pycache__` files entered the
edit commit through `git add -A`; verification reported files outside the scope
and a worktree changed after the commit. The final record remains `reviewed`,
with verification blockers and no integration recorded. It does not establish
that a merge was attempted and failed. Do not bypass the checks or broadly remove
untracked files: inspect generated files and preserve user changes. Version 1.6.1
fixed the handling of test-generated files; see the [release history](CHANGELOG.md).

The bundled regression fixtures use synthetic projects and fake clients. A small
number of controlled native-client checks is evidence for those cases, not a
guarantee for every client version, operating system or repository configuration.
