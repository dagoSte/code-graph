# Client fixtures for the 1.6 agent adapter

`client.py` is a synthetic executable. It never contacts a provider. Tests copy it
with the current Python interpreter in its shebang, then pass test scenarios on
stdin. No production code depends on the fixture.

## Observations and their limits

On 2026-11-06 the local commands reported **codex-cli 0.167.0** and
**Claude Code 2.1.319**. Only `--help`, `--version` and the coordinator-authorized
`codex features list` were run against real clients.
No real model run, authentication check, MCP connection from a real model client,
permission enforcement probe or paid request was performed. The JSONL below is
synthetic, based on the documented event shapes, rather than a captured model run.
Fake clients verify our argv, stdin, parsing and process lifecycle; they cannot
prove that a native client's permissions match its documentation.

`codex features list` reports `shell_tool` as stable and the older
`apply_patch_freeform` feature as removed. Neither this list nor CLI help exposes
the exact model tool inventory after disabling the shell. No matching source
distribution is installed locally. Therefore availability of `apply_patch` in a
real `workspace-write` run with `shell_tool=false` remains **unverified**; it must
be checked at integration, within an explicitly authorized model budget. The
adapter does not re-enable shell execution to mask this limitation.

The local `codex exec --help` exposes `--json`, `--output-schema <FILE>`,
`--sandbox read-only|workspace-write`, `-C`, `-c`, `--ignore-user-config`,
`--ignore-rules` and stdin through `-`. The local `claude --help` exposes `-p`,
`--output-format stream-json`, `--verbose`, `--json-schema`, `--max-budget-usd`,
`--permission-mode`, `--tools`, `--allowedTools`, `--restricted`,
`--setting-sources`, `--mcp-config` and `--strict-mcp-config`.

Sources consulted:

- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
  documents item, turn and thread events, usage and schema output.
- [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
  documents command-tool and hook feature switches.
- [Claude programmatic mode](https://code.claude.com/docs/en/headless) documents
  stream JSON, result metadata and `structured_output`.
- [Claude SDK message reference](https://code.claude.com/docs/en/agent-sdk/typescript)
  defines assistant content blocks, tool results, sessions and usage fields.
- [MCP stdio transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
  specifies newline-delimited JSON-RPC with protocol-only stdout.

## Client configuration

Both clients receive the prompt solely on stdin, inherit authentication from the
environment, and run with `shell=False` in their own process group. The environment
is never serialized by this adapter. A timeout or cancellation sends TERM followed
by KILL to that group, including children that retain pipes. `start()` returns an
`AgentRun` without waiting for the model; `wait(timeout)` returns the current status
even if the wait expires. Exactly one `end` event marks completion.

Codex receives `approval_policy="never"`, an explicit read-only or workspace-write
sandbox, `--ignore-user-config` and `--ignore-rules`. Hooks, shell execution,
shell snapshots, multi-agent spawning and apps are disabled by explicit config
overrides. Graph access uses the explicit `mcp_servers.code-graph` entry pointing
to this installation's `scripts/cg --root <root> mcp`. The schema is a private
temporary file. A dollar budget is rejected for Codex rather than silently ignored.

Claude receives `--tools Read,Grep,Glob` in read mode and adds `Edit,Write` in write
mode. `--allowedTools` names that same set plus `mcp__code-graph__*`; Bash is absent.
Its permission mode is `plan` or `acceptEdits`, respectively. `--restricted` confines
file tools; empty `--setting-sources` prevents loading user/project settings, and
`--strict-mcp-config` uses only the temporary MCP configuration. This preserves
ordinary authentication instead of using `--bare`, whose documented authentication
behavior differs. Managed client policy remains authoritative.

Before write mode starts a process, Git must identify `cwd` as the root of a linked
worktree (`git-dir != git-common-dir`) and `root` must equal that directory. Read
mode can inspect a normal project directory. UI consent and approved plan ownership
belong to the calling workbench/flow, not this low-level module.

## Synthetic stream shapes

Codex sends a session followed by item events and a terminal turn:

```jsonl
{"type":"thread.started","thread_id":"fake-codex-session"}
{"type":"item.started","item":{"id":"t","type":"mcp_tool_call","tool":"cg_find","arguments":{"pattern":"work"}}}
{"type":"item.completed","item":{"id":"a","type":"agent_message","text":"{\"answer\":\"ok\",\"count\":2}"}}
{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":20}}
```

The adapter also handles reasoning, command executions, file changes, completed MCP
calls, `turn.failed` and `error`. The last completed agent message supplies the final
text or JSON. An exit code of zero without a terminal turn is a failure.

Claude sends `system/init`, `assistant` content arrays, `user` tool-result arrays
and a terminal `result`. The result's `structured_output` supplies schema output;
text is never used as a substitute when that field is absent.

```jsonl
{"type":"system","subtype":"init","session_id":"fake-claude-session"}
{"type":"assistant","message":{"content":[{"type":"text","text":"Working."},{"type":"tool_use","id":"t","name":"Read","input":{"file_path":"src.py"}}]}}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t","content":"source"}]}}
{"type":"result","subtype":"success","is_error":false,"session_id":"fake-claude-session","result":"Done","structured_output":{"answer":"ok","count":2},"usage":{"input_tokens":10,"cache_read_input_tokens":40,"cache_creation_input_tokens":50,"output_tokens":20}}
```

Usage is reported only from valid native terminal counters, never estimated from
characters. Codex input already includes cached input. Claude input is normalized
as new input + cache-read + cache-creation; cached input reports cache-read only.
Missing cache counts remain `null`, and missing/invalid input or output counts leave
`usage` absent. No summing of assistant snapshots that might duplicate usage occurs.

Unknown events expose only their type in normalized data. Event text and tool input
previews are bounded. Raw output preserves the original prefix up to 5 MiB; stderr
up to 256 KiB. `run.json` lists truncated logs. JSONL lines over 1 MiB, malformed
JSON, and more than 10,000 normalized events fail the run explicitly. Log files are
created exclusively with mode 0600; a reused log directory cannot overwrite an
earlier run. Logs are client output and may contain source or echoed prompt text;
they are local artifacts, not a redaction boundary.

## Structured result validation

The adapter validates results locally before publishing `result` or `done`.
No new dependency is required. Supported schema keywords are `type`, `properties`,
`required`, boolean `additionalProperties`, `items` and `enum`: the six keywords
used by the workflow plan and review schemas. Root type must be
`object`; recursive schema depth is capped at 40. Unknown keywords, including
`$ref`, are refused before launching a client. This is the subset needed by the
frozen plan and review contracts, not a general JSON Schema implementation.

## MCP behavior

Eleven tools reuse the existing `cg.py` command functions with a fixed root and
captured stdout/stderr. Commands run with file descriptors 1 and 2 redirected to
a pipe, drained continuously while retaining only a bounded prefix. Python output,
child-process writes and buffered native stdio are captured; native buffers are
flushed before descriptor restoration. Dispatch is serial and a lock prevents
overlapping descriptor redirections. JSON is preferred where the CLI supports it. Tool failures,
including `SystemExit`, use `isError`; framing failures use JSON-RPC errors. The
server supports initialization, notifications, ping, tool listing and tool calls
over stdio. It negotiates 2024-11-05, 2025-03-26 or 2025-06-18 (fallback).

The historical protocol also introduced `cg_read(path,
start_line=1, end_line=null)`. It reads files even without an index, with numbered
lines and `next_line` on truncation. It opens each component with directory file
descriptors and refuses symlinks, absolute paths, parent traversal and non-regular
files. A single line larger than the response budget is marked as partially
omitted; `next_line` then points to the following line. No file is executed.

`cg_review` deliberately excludes `save_baseline` and `force`; it can use private temporary analysis snapshots.
`cg_index` only writes graph cache files. Arguments cannot override the root or the
dispatch function. Inputs are bounded to 1,000,000 characters, IDs to 1,000 and
complete responses to 40,000 serialized characters with an explicit truncation
marker. Source, code and project commands are never executed by these tools.

Tests cover both clients, write confinement, stdin, metadata, schema failures,
unknown/malformed events, missing terminal events, usage, timeout, cancellation of
the owned group while an unrelated process survives, concurrent stdout/stderr,
large prompts, log caps, all eleven tools on synthetic source, protocol recovery,
path escape attempts, and response truncation including JSON escaping.

`e2e_client.py` also handles read-only questions without an output schema. The
integration test uses the real dashboard, session ConsentGate, agents.start and
MCP cg_read for both clients; it checks refusal before consent and after revocation,
event polling, result/usage and unchanged project sources. This is a fake-model
integration proof, not a native-client permission or provider test.

`mcp_noisy_command.py` exercises direct fd writes, a native child executable and
buffered libc output, including command failure and output larger than the pipe
and response limits. Its stdout must still contain only JSON-RPC responses.
