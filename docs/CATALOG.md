# The API catalog (format `codegraph-catalog/1`)

The catalog holds, as data, what languages and frameworks say about functions: which decorators keep a
function registered, which are pure wrappers, which APIs receive a callback and in which argument, which calls
are dynamic, and which start files name entry points. Code-graph reads it **locally**: no network and no model
calls during analysis. The same files always give the same catalog and the same fingerprint.

- Base catalog: `scripts/catalog/python.json`, `js.json`, `php.json` and `files.json`. The allowed values are in
  `scripts/catalog/schema.json`, which `scripts/catalog.py` reads, so the validator and the description cannot drift.
- Project extension: `.code-graph-catalog.json` at the project root, next to `.code-graph-manifest.json`.
  It is committed with the project; `.code-graph/` is ignored by Git, so the extension does not live there.
- Loader and validator: `scripts/catalog.py` (`load`, `lookup`, `role`, `start_files`, `fingerprint`), plus
  `python scripts/catalog.py validate [--root R]` and `python scripts/catalog.py check --root R [--json]`.

## Entries

```json
{"id": "python.flask.flask.route.registrar",
 "language": "python",
 "role": "registrar",
 "origin": {"via": "instance", "module": "flask", "name": "Flask.route"},
 "observation": "registration.site",
 "source": "https://flask.palletsprojects.com/en/stable/api/#flask.Flask.route",
 "checked_with": "flask 3.0"}
```

| Field | Rule |
|---|---|
| `id` | Written by hand, stable, lowercase (`[a-z0-9._-]`, ending with a letter or digit). Never derived from the origin: adding a re-export path must not change an id that a project disables. Project ids start with `project.`. |
| `language` | `python`, `js` or `php`; it must match the base file the entry is in. |
| `role` | One per entry; see below. The same origin may appear in several entries with different roles (`atexit.register` is both a registrar and a callback API). |
| `origin` | Where the API comes from. Required. See below. |
| `source` | Where the knowledge comes from: an `https://` link in the base catalog, free text in a project file. It is never fetched. |
| `checked_with` | What the entry was checked against (`python 3.12: hasattr`, `php 8.3.23: function_exists`, or an honest `not run here: ...`). It is informative and is **never used for matching**. |
| `note` | Optional text. |

### Origin: `via` decides how the engine must prove it

| `via` | Fields | Matches when | Allowed in |
|---|---|---|---|
| `import` | `module`, `name` | the name in the code is bound by an import of `module` (aliases followed, shadowing respected) | base, project |
| `instance` | `module`, `name` = `Class.member` | the receiver's type is bound by import to `module.Class` | base, project |
| `builtin` | `name` | a language built-in used by its bare name, with no import, local binding or project symbol of that name | base only |
| `global` | `provider`, `name` | a function a framework defines globally (WordPress `add_action`); same guard as `builtin` | base only |

There is **no fallback by name**. If the engine cannot prove the origin, the entry matches nothing (lesson L0007).
Names are exact dotted identifiers. There are no globs, regular expressions or prefixes: two import paths mean
two entries (`fastmcp.FastMCP` and `mcp.server.fastmcp.FastMCP`). PHP module and function names are compared
case-insensitively.

### Roles in format 1

| `role` | Fields | Meaning for the engine (wired by lanes B) |
|---|---|---|
| `pure_decorator` | none | wraps or describes the function; it does not keep it alive |
| `registrar` | optional `observation` (`registration.site`, `registration.mcp`) | the decorated function is referenced (dead does not propose it); with `observation`, it is also an entry point |
| `callback` | at least one of `args` (positions from 0), `kwargs` (parameter names), `rest_from` (this position and all later ones); optional `label` | a function passed there is a reference |
| `dynamic` | none | calls are flagged dynamic |

Each language file also declares `unrecognized_decorator` (`registrar` or `none`): what an imported decorator
that no entry describes does. Python keeps `registrar`, as in 1.6.4: an unknown framework decorator keeps its
function alive. Project-defined decorators, such as a local identity decorator (R1), are a rule of the engine,
not an entry.

`files.json` lists start files (`name` or `prefix`, without origin) that `dead` scans for entry points.

**Reserved for a later format**, refused by the validator in format 1: the roles `dynamic_loader` and
`return_type`, and the field `returns`. They come in with the migration rule below when a lane reads them
(A4 for loaders; C for return types and for factories such as `click.group()` and `express()`).

**Confidence is not an entry field.** The engine sets it from the role. References are at most medium; no
entry produces a call edge. Data, and project data in particular, can never raise it.

## Project extension

```json
{"format": "codegraph-catalog/1",
 "entries": [{"id": "project.web.page", "language": "python", "role": "registrar",
              "origin": {"via": "instance", "module": "myco.web", "name": "Router.page"},
              "source": "team wiki: routing", "checked_with": "myco.web 2.1"}],
 "files": [{"id": "project.taskfile", "name": "Taskfile"}],
 "disable": ["python.functools.lru_cache.pure"]}
```

- Same entry schema, with fewer rights: `import` and `instance` origins only (no bare names), and ids prefixed
  `project.`.
- It may add entries and start files, and disable base entries by id. It cannot disable `dynamic` entries:
  hiding `eval` would hide dynamic code. Disabling an unknown id is an error.
- Nothing is overridden silently. A project entry with the same origin and role as an enabled base entry is an
  error; to replace one, disable it and add the new one.
- The file is read without following links: it must be a regular file of at most 256 KiB, strict UTF-8 JSON,
  with no duplicate keys and no `NaN` or `Infinity`. Any error rejects **the whole file**. The base catalog is
  used, `cg index` prints `avviso catalogo: ...`, and the warning is stored in the index meta
  (`catalog_warnings`). A project file is never partly applied.

## Index and fingerprints

- `cg.catalog_for(root)` loads the base files once per process and reads the project file again on each call.
  It is never called per file.
- `analysis_inputs(root)` hashes the base catalog files along with the engine code, and includes the effective
  catalog fingerprint (base plus project, sorted by id). A change in any catalog file re-parses every file at the
  next `cg index`.
- The fingerprint is also stored in the meta (`catalog_fingerprint`) and in `content_revision`. The project
  file is one of the configuration files, so queries refuse a stale index until it is indexed again.

## Format versioning: the migration rule

Every catalog file declares `"format": "codegraph-catalog/1"`, and the engine reads only the formats it knows,
refusing a newer one by naming the file. Every key must be understood: an unknown field, role, `via` or
observation is an error, never ignored. Ignoring a key can create wrong edges; refusing it at worst loses some.
A new optional field may enter format 1 only if leaving it out reproduces the earlier behaviour exactly.
Anything else, or any new value of a closed set that older engines would misread, is format 2.

## `catalog check`

`python scripts/catalog.py check --root R` counts what the index uses that the catalog does not describe, to
choose new entries on data (lanes C):
- **Decorators** (Python): their origins are read from the module's imports, with aliases and module-level
  instances (`app = Flask(...)`) followed. Re-exports are not followed, and project-defined decorators are
  counted as `project:`.
- **Callees with a callback argument**: calls on the same line as a resolved callback reference. They are
  matched **by name only**, because graph.db does not store callee origins. A receiver that is not a dotted
  name (`db.query(X).filter`) is counted as `<expr>`, so call chains add up instead of splitting.

It is a counting aid, not a resolver. Its limits are printed with every result.

## Decision record (A2, council decide, 1.6.5)

Eight independent seats (Architect, Pragmatist, Security, Maintainer, Data & Migrations, Boring-Tech,
Adversary, Performance) and two anonymous peer reviewers. Compliance and Operations were not convened: there is
no regulated data and no production service. The council's harness selftest passed 17/17 (TRUST).

**Chosen:**
- an origin structure with a closed `via` set;
- one role per entry;
- explicit ids;
- confidence set by the engine;
- versions as text only;
- closed keys and an exact format string;
- the project file at the root, with fewer rights, rejected whole on any error;
- the catalog fingerprint in the analysis inputs.

**Rejected, and why:**
- **A single qualified string**, even `module:qualname`. It cannot say whether the name is reached through an
  import or a typed receiver, which is exactly where L0007 lives. It also cannot express PHP's case rules, and
  every consumer would have to parse it. Seven of eight seats chose the structure.
- **Ids derived from the origin.** Adding a module path would change the id and break a project's `disable`.
- **Project entries overriding base entries in place.** A silent override is how the extension could hide a
  registration; disable-then-add makes it explicit.
- **A confidence field in entries.** Project data could raise confidence; the engine owns it.
- **Semver ranges.** There is no lockfile and the analysis is offline, so a range would look like logic without
  being logic. It would also make the same sources give different graphs.
- **`dynamic_loader` and `return_type` in format 1.** No lane of phase B reads them; they come with format 1
  additions or format 2 under the migration rule.
- **`.code-graph/catalog.json`.** That folder is ignored by Git (`connect()` writes `*`), so team and CI would
  index with different catalogs.
- **Name prefixes such as `mcp.*`.** They are not exact; two paths make two entries.
- **Prefix-free exceptions lists, the 1.6.4 shape.** "Not in PURE_DECORATORS means registrar" turned every
  other name into a registrar (L0007).

**Facts checked for the dump:**
- The stdlib origins were checked with `hasattr` on Python 3.9, 3.12 and 3.13: `typing.override` exists from
  3.12 and `warnings.deprecated` from 3.13.
- The PHP built-ins were checked with `function_exists` on PHP 8.3.23. `eval` is a language construct, and
  `add_action`/`add_filter` are WordPress functions that plain PHP lacks.
- The JS built-ins were checked on Node 25.2.1.
- Flask and FastAPI entries record attribute checks against installed packages in `checked_with`;
  the retained `Flask.websocket` entry explicitly records that the attribute was absent.
  FastMCP and `typing_extensions` entries still state that they were not run against those packages.
