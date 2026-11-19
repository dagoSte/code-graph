# Set up code-graph — instructions for a coding agent

Follow this guide when a user asks you to install code-graph, for example:

> Install code-graph by following https://github.com/dagoSte/code-graph/blob/main/SETUP.md: show me the plan first, wait for my yes, then install it and check that it works.

This installs the whole repository and its private Python environment, then links
it into Codex, Claude Code, or both. It does not install either coding client or
start a model. People can follow the same steps manually.

## Rules for the setup

1. Read-only checks come first. Show the exact commands, effects and undo steps;
   wait for the user's explicit yes before making the planned changes.
2. Stop at the first failed command. Report its exit status and relevant output.
   Explain what has already changed before proposing a remedy or undo. Do not
   silently switch interpreters, destinations or dependency versions.
3. Do not use `sudo`, install system packages, change shell profiles, or alter
   client configuration. Missing prerequisites need a separate plan.
4. Respect the client's sandbox and approval controls. Request the normal client
   approval if a command needs network access or access outside its workspace.
5. Never overwrite an existing checkout, skill directory or symlink, including
   a broken symlink. Report an existing installation instead of installing twice.
6. Report each command's exit status. Keep credentials out of the report.

## Step 1 — Check what exists

These commands only inspect the machine:

```sh
git --version
bash --version
python3.12 --version
python3.12 -I -c 'import sys, venv; print(sys.executable); print(sys.version)'
```

The engine requires Python 3.10 or later. Python 3.12 on macOS arm64 is the
verified setup. Use `python3.12` when available. If it is missing, stop and report
that fact; an alternative interpreter must be named in the plan and needs binary
wheels for all the pinned dependencies. Do not install Python yourself.

Check the default skill locations:

```sh
for cg_target in "$HOME/.codex/skills/code-graph" "$HOME/.claude/skills/code-graph"; do
  if [ -e "$cg_target" ] || [ -L "$cg_target" ]; then
    ls -ld "$cg_target"
    if [ -L "$cg_target" ]; then readlink "$cg_target"; fi
  else
    printf 'absent: %s\n' "$cg_target"
  fi
done
```

If either client uses a custom configuration directory, establish its actual
skill location before proposing links; do not create a second installation in
the default directory by assumption. An occupied path may belong to something
else. Inspect its `SKILL.md` and link target before calling it code-graph.

Ask which clients the user wants and choose a new clone directory with an
existing, writable parent. In the examples below, replace `/path/to/code-graph`
with that agreed absolute path. Do not run the placeholder literally.

```sh
if [ -e "/path/to/code-graph" ] || [ -L "/path/to/code-graph" ]; then
  ls -ld "/path/to/code-graph"
else
  printf '%s\n' 'clone destination is free'
fi
```

For an existing trusted checkout, inspect its origin, commit and working tree:

```sh
git -C "/path/to/code-graph" remote get-url origin
git -C "/path/to/code-graph" rev-parse HEAD
git -C "/path/to/code-graph" status --short --ignored --untracked-files=all
```

If code-graph is already installed, report where it is and follow the update or
verification section as appropriate. Do not clone over it or replace its links.

## Step 2 — Show the plan and wait

Present this table in the user's language, with real paths and only the client
rows they requested. Include the verification commands in the plan. A yes covers
the listed operations, not unrelated installation or cleanup.

| Command | What changes | How to undo it |
|---|---|---|
| `git clone https://github.com/dagoSte/code-graph.git "/path/to/code-graph"` | Downloads the repository and Git history into a new directory | After removing the links and checking for user changes, remove that newly created directory |
| `CG_PYTHON=python3.12 bash "/path/to/code-graph/scripts/setup.sh"` | Creates `.venv` in the clone; downloads and installs the versions in `requirements.txt`; makes `scripts/cg` executable; runs the selftest | Remove the clone's `.venv` to remove this Python environment; removing the new clone also undoes the launcher mode change |
| `mkdir -p "$HOME/.codex/skills"` | Creates missing parent directories for the Codex skill | Remove only directories created by this plan, and only if still empty |
| `ln -s "/path/to/code-graph" "$HOME/.codex/skills/code-graph"` | Registers the whole checkout as a Codex skill | Verify the link still points to this checkout, then `unlink "$HOME/.codex/skills/code-graph"` |
| `mkdir -p "$HOME/.claude/skills"` | Creates missing parent directories for the Claude Code skill | Remove only directories created by this plan, and only if still empty |
| `ln -s "/path/to/code-graph" "$HOME/.claude/skills/code-graph"` | Registers the whole checkout as a Claude Code skill | Verify the link still points to this checkout, then `unlink "$HOME/.claude/skills/code-graph"` |
| `"/path/to/code-graph/scripts/cg" selftest` | Runs synthetic checks using temporary test data; no model or user project is involved | No persistent project data to undo |

The clone contacts GitHub. Setup uses pip and its configured package index,
normally PyPI, and may populate pip's download cache. Dependencies go into the
clone's `.venv`, not the system Python. The installer accepts binary wheels only;
it does not compile missing packages from source.

**Wait for the explicit yes before Step 3.**

## Step 3 — Install the approved plan

Run the approved commands one at a time. Immediately before each clone or link,
repeat the destination check using both `-e` and `-L`. Stop if it is occupied.
Use `ln -s`, never a force or replacement option. Link the whole checkout, not
just `SKILL.md`: scripts, assets and `.venv` must remain available together.

`setup.sh` already runs the selftest. On failure, show the failing stage and leave
the partial installation in place until the user chooses the next action.

## Step 4 — Verify and report

Run the standalone verification command from the approved plan:

```sh
"/path/to/code-graph/scripts/cg" selftest
```

Require exit status 0, **25/25 checks** and **TRUST**. On `DISTRUST` or any failure,
do not report a successful installation. These are fixture checks, not proof of
complete analysis of an arbitrary project.

Inspect each link you created with `ls -ld` and `readlink`, and record the clone's
commit with `git -C "/path/to/code-graph" rev-parse HEAD`. Tell the user the paths,
commit, selected Python, verification result and how to undo the installation.
If the client does not see the skill yet, start a new session. No shell profile
change is needed; the launcher can always be called by its full path.

Offer a first request, without executing it as part of setup:

> Use code-graph to map this project. Show the relevant files and the limits of the analysis before suggesting changes.

The static engine works offline after setup. The optional agent change flow
needs separately installed and authenticated clients and their provider access.
The dashboard requires session consent before starting agents. On the command line
`cg change plan`, `edit` and `review` ask for confirmation before they start Codex or
Claude and send project context to its provider: without a terminal they need `--yes`
and otherwise refuse with exit 2. Pass `--yes` only when the user explicitly asks for
that step with that agent.
These runs consume the user's client quota; installation does not authorize them.
See [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md).

## Updating

Stop code-graph and any active change workers first. Inspect the checkout and
read the release notes. Show an update plan and wait for yes, as for installation.
Record the current commit, Python executable/version and the backup location
before changing anything. For an exact rollback, the update plan must include a
complete backup of the existing `.venv`, preserving its files, permissions and
symlinks, while no process uses it. Restore it to the original path with the same
Python installation and platform; virtual environments are not portable backups.
For a clean checkout on `main` tracking the intended origin, the usual commands
are:

```sh
git -C "/path/to/code-graph" pull --ff-only
CG_PYTHON=python3.12 bash "/path/to/code-graph/scripts/setup.sh"
"/path/to/code-graph/scripts/cg" selftest
```

The pull changes source files and history; setup reuses the existing `.venv`
without `--clear`, installs the pinned packages and runs its checks. Existing
links continue to use that checkout. If the tree is dirty, the branch differs,
or fast-forward fails, stop without resetting,
stashing or discarding user work. An exact rollback needs the recorded source
and environment backup; checking out old source alone may leave newer Python
packages behind. Propose and approve the recovery commands separately.

## Uninstalling

Stop the dashboard and active agents/tests. Inspect each skill link and remove
it with the corresponding `unlink` command above only if it is still the link
created by this installation. Leave occupied directories and changed links alone.
Inspect the clone with `git -C "/path/to/code-graph" status --short --ignored
--untracked-files=all` so ignored files, including `.venv`, are visible too.
After reviewing those files and saving anything needed, the user can approve
deletion of that exact checkout and its `.venv`. Never delete the parent skills
directory.

Uninstalling the skill does not remove project indices, change archives, Git
branches, client histories or provider records. Follow [data removal](PRIVACY.md#retention-and-removal)
before deleting project state: `.code-graph` can contain unintegrated worktrees,
not just a disposable index.
