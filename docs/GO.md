# Go adapter: supported analysis subset

Initial development contract, base `0591fc7`. Target: the Go 1.23 language subset described
below, using the pinned tree-sitter grammar. Parser availability does not establish
whole-language support. Indexing never runs Go, downloads modules or invokes a compiler.

## Extraction and package identity

- Index `.go` files: package functions, named types, interfaces and declared methods.
  Map named concrete types to the existing `class` kind; interfaces retain `interface`.
  Preserve receiver-qualified method names, source byte spans and exported status
  (initial Unicode uppercase letter), including declarations split across files.
- Record actual callsites including `go` and `defer`. Count grouped parameters and
  variadic arguments. Do not turn a type conversion or function value into a constructor.
  Package initializers use the file's module owner for calls and references.
  Calls in function literals remain in their enclosing function or module with conservative
  shadowing; the enclosing caller is containment, not proof of immediate execution.
- Package identity includes source directory and declared package name. Same spelling
  in another package or another language does not provide a fallback target.
  Production files cannot resolve to declarations found only in `_test.go` files.
- Persist Go extraction provenance in namespaced `observations`; reuse the existing
  symbols, calls, import and reference tables. No destructive DB migration is needed.

## Resolution boundary

- Resolve unique package-level functions across files of the same package. Resolve
  local-module imports (explicit aliases or the target's declared package name) only
  within the caller's nearest `go.mod` boundary. Imported names must be exported.
- Resolve directly declared methods on unambiguous concrete receiver parameters or
  explicitly typed outer-body local variables. Direct outer-body `var` and `:=`
  declarations can also retain the type written in a `T{}` or `&T{}` initializer,
  including parentheses, comments, imported types and one-to-one initializer lists.
  Addressable value variables may call pointer receiver methods. Interface receivers,
  promoted embedded methods, method expressions,
  type aliases, unresolved/generic receiver types and inferred return types abstain.
- Local receiver hints remain conservative across the whole function. A name used
  before its declaration or anywhere in that declaration's initializers cannot take
  the new local type: those uses may refer to an outer binding. Omit that name's hint
  for the whole owner, including for explicit local types. Control-header, implicit
  switch/select case blocks and nested declarations supply blockers, not receiver hints.
  This also applies to explicit types and grouped `var` declarations. Package-level
  variables, copies of other variables and arbitrary initializer expressions supply
  no new receiver inference. Method values remain references, not invocations.
- Local bindings and type parameters shadow package names. Conservative whole-function
  shadowing can omit valid calls before/after a nested binding; package-initializer
  literals similarly use conservative file-module shadowing. Neither may invent edges.
  Function-valued variables and unresolved receiver chains remain dynamic/unresolved.
  Explicit generic calls may resolve a named function, never an indexed function value.
  Static function-value references are kept separately from invocations. Ambiguous
  conversion/call syntax can remain an unresolved callsite; it never becomes a constructor.
  Call-head names and receivers longer than the 160-character diagnostic fields stay
  unresolved; their clipped text cannot select another symbol.
- Build constraints and OS/architecture suffixes are not evaluated. Such files remain
  searchable but neither their calls nor declarations supply resolved targets.
  The same applies to dot/underscore filenames, cgo and `go:linkname` files.
  Partial syntax and ambiguous declarations also abstain. No same-name interface-to-
  implementation guesses or package-wide low-confidence fallback.
- `go.mod` and `go.work` are inert, bounded configuration inputs. Changes invalidate
  freshness and re-resolve imports even when `.go` contents are unchanged. Nested,
  invalid or symlinked module files form boundaries; they cannot inherit an outer module.
  Module/workspace files in ancestors of selected Go sources participate even if Git ignores them.
  Freshness fingerprints include bounded presence/status markers for unreadable,
  symlinked and oversized boundaries; these markers are not source-file contents.
  Workspace selection, cross-module dependencies, `replace`, vendoring and GOPATH
  resolution are outside this increment. Workspace mode abstains on imported targets.

## Consumers and verification

FTS5/search, source windows, impact and test-plan queries use the existing interfaces.
Conventional Test/Benchmark/Fuzz/Example functions in `_test.go` are discovery candidates,
not proof of execution. Go arity/type diagnostics remain the compiler's responsibility.
The existing Python/PHP checks are not extended with guessed Go diagnostics.

Require positive and negative cases for package isolation, aliases, export rules,
receiver/interface distinctions, shadowing, generics/conversions, build constraints,
test variants, module changes/boundaries and mixed languages. Cover hostile imports,
engine-copy isolation, cache invalidation, incremental/full equivalence and rollback.
Receiver-literal regressions also preserve explicit interface types, predeclaration
and initializer scopes, control headers, nested bindings, method values and changed
initializer types; native observations must keep these synthetic programs valid.
Keep existing language suites and recorded oracle green on identical frozen inputs.

Native probes use a checksum-verified temporary Go toolchain and trusted synthetic
fixtures, with module downloads disabled; record its exact version separately. Inspect
a bounded real standard-library package as an additional source case. Neither native
observations nor a successful parse prove complete Go semantics.

References checked 12 November 2026: [Go specification](https://go.dev/ref/spec),
[blocks](https://go.dev/ref/spec#Blocks) and [declaration scopes](https://go.dev/ref/spec#Declarations_and_scope),
[module layout](https://go.dev/doc/modules/layout),
[go.mod reference](https://go.dev/doc/modules/gomod-ref).

Recorded observations run without Go in the normal Python suite. To explicitly rerun
the bundled, trusted native programs with an installed compiler:

```sh
.venv/bin/python -I tools/oracle_matrix.py --language go --rerun --go /path/to/go --output /tmp/go-oracle.json
```

## SDK grammar investigation, 13 November 2026

The post-1.7.0 capture contains **7,034 indexed Go sources**, of which **65** have
`root_node.has_error`. The individual upstream package
[`tree-sitter-go` 0.25.0](https://pypi.org/project/tree-sitter-go/0.25.0/)
provides MIT-licensed, compiled macOS arm64 and Linux wheels for Python 3.10+,
and loads with tree-sitter 0.25.2 without runtime downloads. However, its trees
are identical to the pinned language pack on all 7,034 sources; the same 65
paths remain partial. It is therefore **not added as a dependency**.

The table assigns one primary observed cause per file. Some test files also
contain other deliberately invalid cases. These groups describe parser output,
not compiler acceptance or a claim that every partial file is valid Go. In
particular, malformed test inputs and annotated templates should not all become
successful parses. This investigation does not widen the Go 1.23 resolver subset.

| Primary cause | Files |
|---|---:|
| Negative test fixtures with expected-error annotations | 44 |
| Method declarations with their own type parameters | 6 |
| Expression arguments to `new`, such as `new(42)` | 7 |
| Implicit semicolon at end of file | 4 |
| Consecutive labels | 1 |
| Method expression on an anonymous struct type | 1 |
| Coverage template with `«` / `»` annotations | 1 |
| Intentionally non-Go text in parser test data | 1 |
| **Total** | **65** |

For the four EOF cases, `has_error` is true without a visible `ERROR` or missing
node. Appending a newline in a diagnostic memory buffer makes them parse without
errors. The actual indexed source bytes are never rewritten. Ten minimal Go
inputs, including one complete control, also produce identical trees with the
language pack and the individual wheel. No Go compiler or SDK source was run.

All paths below are relative to the SDK's `src/` directory:

Negative test fixtures (44):

- `cmd/compile/internal/syntax/testdata/issue20789.go`
- `cmd/compile/internal/syntax/testdata/issue23385.go`
- `cmd/compile/internal/syntax/testdata/issue23434.go`
- `cmd/compile/internal/syntax/testdata/issue31092.go`
- `cmd/compile/internal/syntax/testdata/issue43527.go`
- `cmd/compile/internal/syntax/testdata/issue43674.go`
- `cmd/compile/internal/syntax/testdata/issue46558.go`
- `cmd/compile/internal/syntax/testdata/issue47704.go`
- `cmd/compile/internal/syntax/testdata/issue48382.go`
- `cmd/compile/internal/syntax/testdata/issue49205.go`
- `cmd/compile/internal/syntax/testdata/issue49482.go`
- `cmd/compile/internal/syntax/testdata/issue52391.go`
- `cmd/compile/internal/syntax/testdata/issue56022.go`
- `cmd/compile/internal/syntax/testdata/issue60599.go`
- `cmd/compile/internal/syntax/testdata/issue65790.go`
- `cmd/compile/internal/syntax/testdata/issue68589.go`
- `cmd/compile/internal/syntax/testdata/issue70957.go`
- `cmd/compile/internal/syntax/testdata/sample.go`
- `cmd/compile/internal/syntax/testdata/smoketest.go`
- `cmd/compile/internal/syntax/testdata/tparams.go`
- `cmd/compile/internal/syntax/testdata/typeset.go`
- `cmd/compile/internal/types2/testdata/local/issue47996.go`
- `cmd/compile/internal/types2/testdata/local/issue68183.go`
- `internal/types/testdata/check/builtins0.go`
- `internal/types/testdata/check/constdecl.go`
- `internal/types/testdata/check/decls0.go`
- `internal/types/testdata/check/expr0.go`
- `internal/types/testdata/check/expr3.go`
- `internal/types/testdata/check/issues0.go`
- `internal/types/testdata/check/stmt0.go`
- `internal/types/testdata/check/typeinst0.go`
- `internal/types/testdata/check/vardecl.go`
- `internal/types/testdata/examples/functions.go`
- `internal/types/testdata/examples/types.go`
- `internal/types/testdata/fixedbugs/issue39634.go`
- `internal/types/testdata/fixedbugs/issue42987.go`
- `internal/types/testdata/fixedbugs/issue43190.go`
- `internal/types/testdata/fixedbugs/issue45635.go`
- `internal/types/testdata/fixedbugs/issue46403.go`
- `internal/types/testdata/fixedbugs/issue48827.go`
- `internal/types/testdata/fixedbugs/issue50427a.go`
- `internal/types/testdata/fixedbugs/issue50427b.go`
- `internal/types/testdata/fixedbugs/issue51658.go`
- `internal/types/testdata/fixedbugs/issue58612.go`

Method type parameters (6):

- `cmd/compile/internal/importer/testdata/genmeth.go`
- `go/internal/gcimporter/testdata/genmeth.go`
- `internal/types/testdata/check/typeparams.go`
- `internal/types/testdata/spec/methods.go`
- `math/rand/v2/rand.go`
- `runtime/traceback_test.go`

Expression arguments to `new` (7):

- `cmd/compile/internal/types2/trie_test.go`
- `encoding/json/v2/arshal_test.go`
- `go/types/trie_test.go`
- `internal/types/testdata/check/go1_25.go`
- `internal/types/testdata/fixedbugs/issue75986.go`
- `net/http/internal/http2/server.go`
- `runtime/secret/secret_test.go`

EOF semicolon (4):

- `internal/types/testdata/check/cycles5.go`
- `internal/types/testdata/check/decls4.go`
- `internal/types/testdata/fixedbugs/issue48083.go`
- `internal/types/testdata/fixedbugs/issue51607.go`

Consecutive labels (1):

- `internal/types/testdata/check/gotos.go`

Anonymous-struct method expression (1):

- `internal/types/testdata/fixedbugs/issue53358.go`

Coverage annotation template (1):

- `cmd/cover/testdata/ranges/ranges.go`

Non-Go parser input (1):

- `go/parser/testdata/issue42951/not_a_file.go/invalid.go`
