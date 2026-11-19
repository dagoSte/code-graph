# Languages

Each language has a written contract: what is resolved, and where the analysis deliberately abstains
rather than guess. No compiler, SDK or language server is needed.

| Language | What is resolved | Contract |
|---|---|---|
| Python | Imports and re-exports, typed receivers, callbacks and decorators, modules loaded with `importlib`/`runpy`, FastAPI/Flask routes | [LANGUAGES](../LANGUAGES.md) |
| JavaScript / TypeScript | ES and CommonJS modules, barrels, `tsconfig` paths, workspace packages, JSX components | [LANGUAGES](../LANGUAGES.md) |
| PHP | Namespaces, function and class imports, callbacks, WordPress hooks | [LANGUAGES](../LANGUAGES.md) |
| Go | Package/import calls and explicit concrete receivers; interfaces and build variants abstain | [GO](../GO.md) |
| Dart | Libraries, relative and local `package:` imports, static methods; instance dispatch abstains | [DART](../DART.md) |
| Java | Declared static/private/final subset; overloads and virtual dispatch abstain | [JAVA](../JAVA.md) |
| Swift | File-local private/static subset; overloads and imports abstain | [SWIFT](../SWIFT.md) |
| SQLite SQL | Tables, views and queries with read/write observations, separate from calls | [SQL](../SQL.md) |

**Abstaining** means no edge is recorded where the static evidence is not enough: a missing caller is
possible, a wrong one is avoided. Search, `find` and source reading work on every indexed file whatever
its language support.

The [API catalog](../CATALOG.md) describes known library calls, so that `catalog check` can report the
calls the index sees but the catalog does not know.
