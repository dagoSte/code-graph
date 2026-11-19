# Troubleshooting

**`selftest` does not end with `TRUST`.**
The parsers or the environment do not match the pinned versions. Re-run `bash scripts/setup.sh`; to use
another interpreter, `CG_PYTHON=python3.12 bash scripts/setup.sh`. Do not use the graph to decide changes
until the self-test passes.

**`setup.sh` finds no wheel for my Python.**
Dependencies are installed as binary wheels only. Choose a supported interpreter with `CG_PYTHON`
(Python 3.12 is the verified one).

**A command says the source or the index changed.**
A file changed after the last index, so code-graph refuses to quote old lines. Run `cg index` and ask
again. `locate` and `start` re-index by themselves.

**A caller I expected is missing.**
Try `--min-conf low` to include name matches. If it is still missing, the call is probably decided at run
time (an interface with several implementations, reflection, injection, a route registered by string):
the language contracts in [Languages](languages.md) list where the analysis abstains. `callees` on the
calling function shows the calls it could not resolve.

**Search finds nothing for my question.**
Use words that appear in the code (`refund order` rather than *give the money back*), or the name of a
function you know with `find`. `--mode lexical` shows what the words alone match. An empty answer does
not prove the code is absent.

**`locate` returns `budget_insufficient`.**
Not even one candidate fits in `--max-chars`. Raise it (up to 16,000) or lower `--limit`.

**The index is large or slow to build.**
Check `cg stats` and exclude generated or vendored code with `.cgignore`. After the first index, only
changed files are parsed again.

**The dashboard does not open.**
`cg explore --no-open` prints the local address with its session token; open it in a browser on the same
machine. `--port` picks a fixed port.

**The agent does not see the skill.**
Link the whole folder, not `SKILL.md` alone, and start a new session of the agent.
