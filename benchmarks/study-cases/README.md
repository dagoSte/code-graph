# Three-repository study inputs

This is a preparation dataset, not a result showing that an LLM benefits from
code-graph. The [protocol](../LIVE_PROTOCOL_STUDY.md) fixes the future comparison
and analysis before model outcomes. No provider is started by qualification.

`cases.json` freezes 23 historical bugs: Django 8, typescript-eslint 8,
Prometheus 7. The Go pool exhausted the preregistered 1,500-PR bound. The earlier
pool decision permits at least 20 total cases; seven Go cases are explicitly
below the eight-case target. Maximum actual cohort size is now fixed at 23,
requiring at most 138 starts and 46 Claude reviews, without retries.

## Evidence and formats

- `selection-rule.initial.json` preserves the initial rule. `selection-rule.json`
  contains the approved repository/eligibility amendments and conditional pool
  extension, with prior hashes and decision identifiers.
- `selection-ledger.json` retains every disposition in the ordered pools: 1,500
  Django, 500 typescript-eslint, 1,500 Prometheus. It includes creation timestamps,
  original metadata hashes, every block query, repository gates and native
  qualification summaries. Candidates after the eighth qualified case remain
  explicitly untested. Unavailable metadata is not evidence of a non-bug:
  two Django tickets returned HTTP 429 and one TypeScript issue HTTP 404.
- `cases.json` contains linked issue text, historical parent/fix/tree identities,
  test selectors, relative patch paths and hashes. In the published copy, local
  account names are masked in two issue bodies. The recorded run used the exact
  upstream text. Offline rescoring recognizes the published case manifest by its
  exact SHA-256 and keeps the original run's input hash. A new run records the
  hash of the actual manifest it uses. Production-fix
  patches and old-side hunk/declaration targets are scoring material only.
  They never enter agent prompts or the fresh agent Git repository.
- `inventories/*.json.gz` contain `source_paths` and `tree_paths` with deterministic
  gzip headers. Their compressed bytes are hashed; no custom delta format is used.
  `source_paths` is the existing writable language-file inventory. `tree_paths`
  is the union of parent/fix tracked inputs for external scoring copies.
  Under decision `studio-grammatiche-y`, Go inventories also include **all `.y`
  files tracked in the parent**, identically in both arms. Each of the seven Go
  parents has `promql/parser/generated_parser.y`; no future-only path is added.
- `patches/` contains historical test-only patches, applied only in host scoring
  copies. Changed fixtures and other test inputs are included, not just test code.
- `qualification.json` retains native parent/fix counts and the exact qualification
  helper/input bindings. It identifies private raw evidence by hash. It does not
  claim that every unrelated later harness edit was exercised by those runs.
- `prepared-integrity.json` independently checks 254,579 tracked file inputs across
  the 46 selected parent/fix trees against their Git blobs: zero changed inputs.
- `mechanical-fixture-hashes.json` records complete production-file hashes from
  each historical fix's Git blobs, for host-only fake-agent controls. It also
  covers `auxiliary_fix_files`: the grammar changed by Prometheus #19403, included
  in that case's production patch. The primary `production_files`/`fix_targets`
  remain the Go files used by the frozen selection/localization rule. Their
  zero-context production patches require `git apply --unidiff-zero`; an exit 0
  alone is insufficient. `apply_historical_fixture()` checks the patch hash and
  exact resulting bytes before declaring a fixture applied.
- `environment.json` records local toolchain versions/binary hashes, installed
  Python packages/wheel hashes when recorded, and per-parent dependency-lock hashes.
  Runtime paths, caches, raw output and working copies stay private.
- `engine-freeze.json` records the accepted engine commit, its 34 script/catalog
  hashes, parser versions and requirements hash (`S0-FREEZE-580`). This is distinct
  from the earlier engine/helper maps bound to native qualification receipts.
- `licenses/` preserves the repositories' BSD-3-Clause, MIT and Apache-2.0 notices.
  Copied historical patches remain subject to their respective notices.

Native discovery corrected two instrument errors, with original attempts retained:
Django mixin implementations require concrete TestCase selectors (including
generated partial methods); Go `TestMain` is a suite entrypoint, not a test case.
Neither correction weakens the native red/green criterion. Existing changed
Vitest cases with an unchanged identity cannot be selected as new; cases with no
new native identities are an explicit unsupported disposition.

## Preparing a private host environment

Use a separate pilot environment. Do not install dependencies into code-graph's
virtualenv or point candidate workspace packages at another revision's `dist`.
Preparation may fetch dependencies once. All qualification and workflow tests
then deny IP egress using macOS Seatbelt; local Unix IPC remains available for Nx.
This prepared environment is macOS arm64; it is not a cross-platform test claim.

Prewarm private Git caches at `<prepared>/<repository>/cache`, including the
parent/fix blobs. Export each selected pair, preserving modes and confined
relative symlinks, into `<prepared>/<repository>/cases/<PR>/base` and `fix`.
`benchmarks/real_pilot.py:archive(..., allow_symlinks=True)` performs this export
with runtime Git fetching disabled. The agent's seed is a new Git repository
containing only the parent tree, verified against `parent_tree`; never hand an
agent the historical cache with future fix objects.

Install Django test dependencies in its own Python 3.13.14 environment; preload
each historical pnpm lock with PNPM 12.15.0 and install scripts disabled; preload
each historical Go module cache with Go 1.27.4. The locked source inputs must
remain byte-identical after preparation. Node 26.6.0 and npm 12.1.0 were used.
The native runner builds each TypeScript workspace locally and offline, then runs
the affected Vitest modules. Go builds/tests use local caches, `GOPROXY=off`,
`GOTOOLCHAIN=local`, `-mod=readonly`, and `-count=1`.

Create a private runtime JSON with absolute host paths for these keys:

```json
{
  "python": "/path/to/django-venv/bin/python",
  "node": "/path/to/node",
  "pnpm": "/path/to/the/native/pnpm/binary",
  "pnpm_store": "/path/to/pilot/pnpm-store",
  "node_tmp": "/tmp/short-pilot-ipc",
  "go": "/path/to/go/bin/go",
  "go_modcache": "/path/to/pilot/modcache",
  "go_cache": "/path/to/pilot/gocache",
  "native_evidence": "/path/to/private/native-evidence"
}
```

PNPM's native binary is required here: an install-script-disabled `.bin/pnpm`
placeholder may have no executable shebang. Keep the IPC path short for macOS.

## Qualification, without models

From the code-graph checkout, with dependencies already prepared:

```sh
.venv/bin/python -I benchmarks/study_pilot.py \
  --cases benchmarks/study-cases/cases.json \
  --prepared /path/to/prepared \
  --environment /path/to/private-runtime.json \
  --skill-root /path/to/accepted-code-graph \
  --output /path/to/qualification.json
```

The command executes native tests and starts zero model clients. It requires a
nonempty assertion failure on the parent, a green historical fix, equal hidden
test counts and no errors/skips/timeouts. Public tests are frozen separately.
Raw native receipts stay private; the output contains bounded counters and hashes.

The future `--localize` and `--live --work-dir <new-directory>` modes additionally
require `--engine-manifest <accepted-freeze.json>` containing exact `engine_files`
and `parser_versions` maps. Use the committed `engine-freeze.json`, accepted after
engine review. Neither mode is part of this preparation assignment. `--live` must
only be used under the
separate accepted study assignment and budget. A dry run with in-memory fake
agents and a host-applied known fix proves workflow mechanics, not LLM success.

The prototype used a 120-second static-analysis timeout. The study's host adapter
uses 600 seconds per analysis/test and a 1,860-second workflow-step wait, with the
original cancellation/drain behavior. Both arms have
the same limits. Client calls remain capped at 600 seconds. The accepted engine
freeze includes a separately reviewed product performance correction; locate
ranking was not tuned on these cases.

The original Django mechanical attempts applied the zero-context production
patch without that flag, appending decorators at EOF and producing invalid
Python. Those attempts are preserved as invalid fixtures, and are not evidence
that the correct historical fix completed the workflow. The 23 native
qualifications apply full-context **test-only** patches in separate copies:
an additional byte audit confirms all 23 reconstruct the historical test inputs
exactly. The four completed TypeScript/Go mechanical controls also match their
historical production fix files exactly, with their earlier engine maps preserved.
The corrected Django controls now complete in both arms on freeze `580cd6b`:
verify takes 42.621/43.746 seconds, with public/hidden tests, integration and cleanup
green. Both Go controls were replayed with #19403's `.y` grammar as well as its Go
files; all four resulting files match the historical fix. Native Go tests do not
verify parser regeneration. All these controls use fake agents and known fixes,
start zero providers and imply no LLM benefit. The study still requires its
independent bank review and a separate go before live calls.
