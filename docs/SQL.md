# SQL adapter: SQLite resource evidence

First supported source subset, interpreted as SQLite. This does not claim generic
SQL/PostgreSQL/MySQL support or validate a migration against a database. Indexing
reads `.sql` text with the bundled parser and never prepares/executes it, opens a
user database, loads extensions or runs a migration framework. SQLite used for the
code-graph cache is separate from databases described by indexed SQL.

## Source and resource facts

- Table/view declarations and complete query statement spans become searchable
  symbols with original signatures/source. Queries have source-coordinate names;
  aliases, columns and scalar/table-valued functions do not become table declarations.
  Table-valued functions remain only in the searchable statement source; they do
  not emit a dedicated resource observation in this increment.
- Recognize the bundled grammar's `CREATE TABLE`, `CREATE VIEW`, `SELECT`, `INSERT`,
  `UPDATE`, `DELETE`, CTEs and simple transaction wrappers. `sql.declare`, `sql.read`
  and `sql.write` observations retain the owner, line and normalized schema/name.
  Reads mean explicit FROM/JOIN relations; writes mean explicit DML targets. These
  are syntax observations, not all runtime reads, view expansion or data lineage.
- CTE names conservatively suppress unqualified reads within the entire statement,
  including nested scopes. Explicit schema-qualified names bypass CTE shadowing.
  This can omit a valid outer read; it cannot prove a complete access inventory.
- Normalize identifier ASCII case and decode accepted double-quoted identifiers.
  Original spelling remains in the source. Do not Unicode-casefold identifiers.
  Bracket/backtick/legacy quoting and typeless columns can be valid SQLite but
  unsupported by the pinned grammar; native boundary tests expose this limitation.
- A parser error, an unsupported statement kind, a foreign-key or virtual-table
  construct suppresses access observations for the whole file. Best-effort symbols
  remain searchable and `sql.file.supported=false` records the boundary. `true`
  means only the syntactic subset was recognized, never complete SQLite semantics.
  Triggers, ALTER/DROP, PRAGMA, ATTACH/DETACH, schema mutation/order and external
  catalogs are outside this increment. No SQL extraction from ORM/string literals.

## Resource candidates and consumers

`architecture --json` adds a `sql` section when indexed SQL exists; it contains
the dialect, source-verified items, total/truncation and candidate links. Existing
non-SQL output is preserved. The local `/api/architecture` endpoint exposes the same section and symbol detail
retains observations. The architecture panel renders a separate SQL section with the
dialect, returned file/declaration/read/write evidence and candidate relationships.
Each proof keeps its source location, SHA-256 and an expandable source window. Counts
describe returned records only (up to 500, including file status); truncation and
unsupported file syntax remain explicit. Recognized syntax never means a complete
access inventory. Candidates cover only returned accesses in the same script and
remain separate from call edges; the panel never executes SQL or opens a database.
MCP `cg_search`/`cg_source` retrieve SQL source evidence through existing tools.

Links match a unique declaration and access name within the same script. All
schema/temp/duplicate declarations with that base name count before eligibility;
only non-temp main/unqualified declarations are eligible. Explicit other-schema
accesses remain observations. There is no cross-file/database or migration-order
inference. A link remains a candidate even if the declaration follows the query:
it is a lexical relation with explicit assumptions, not a resolved runtime target.
Resources have no call-confidence score, call/reference edges or inheritance;
`callers`, `callees`, `impact` and call-path search do not traverse resource links.

The SQL section reads at most the requested 1..500 observation rows, ordered by
file/line/ID, and at most two declaration records for each candidate lookup. Links
cover only returned access rows (`links_scope=returned_accesses`). Source excerpts
reuse ProjectData hash/boundary checks and include SHA-256; changed source or engine
requires reindexing. Queries use a readonly connection and never migrate the cache.
Existing atomic indexing, deletion/ignore handling, fingerprint, FTS and source
windows are reused; no schema migration, dependency or new command family is added.

## Verification and semantic references

Preserve the 678-test Swift baseline and all 80 previous fixture projects. New SQL
coverage is separate from the baseline without an SQL adapter, with resource
observations counted separately from zero SQL call edges. Tests cover owner/spans,
aliases/CTEs, quoted/schema names, duplicates/temp, writes/subqueries, unsupported
syntax, rollback, freshness, isolated imports, bounded output and CLI/MCP reads.

Native tests use only inspected synthetic statements in a fresh in-memory SQLite
database and its authorizer. They compare actual table reads and schema objects;
they also show valid SQLite syntax that the grammar cannot parse. Project SQL is
never executed. Existing call/reference oracle protocols are unchanged because
SQL observations belong to a different domain. SQLite runtime/version and native
results are retained in the author receipt; this is not cross-dialect certification.

Official references checked 11 November 2026:
[SQLite SQL syntax](https://www.sqlite.org/lang.html),
[object-name resolution](https://www.sqlite.org/lang_naming.html),
[views](https://www.sqlite.org/lang_createview.html),
[WITH/CTEs](https://www.sqlite.org/lang_with.html),
[identifier quoting](https://www.sqlite.org/lang_keywords.html) and
[authorizer callbacks](https://www.sqlite.org/c3ref/set_authorizer.html).
