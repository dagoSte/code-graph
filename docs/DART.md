# Dart adapter: first supported analysis subset

Development contract, base `ab99c2d`. Target: Dart 3.6 syntax within the subset
below, using tree-sitter-language-pack 0.13.0. A parser accepting syntax does not
establish full Dart semantics. Indexing needs no Dart/Flutter SDK, pub resolution,
network access or project execution.

## Declarations and source evidence

- Index `.dart` files with top-level functions and variables, named classes, enums,
  mixins, extensions and their declared methods/accessors/constructors. The existing
  class/method kinds represent ownership; they do not prove instance dispatch.
- Associate sibling signature/body nodes to retain the complete declaration span
  for FTS5, source windows and evidence. Keep callsites separate from function-value
  references (tear-offs). Initializers use their file-module or class owner.
- Arrow bodies, block bodies, generic call syntax, nested callbacks, explicit
  constructor syntax and cascades are recorded. Indirect calls and constructors
  remain unresolved. Anonymous extension owners use a source-line name.

## Resolution boundary

- A standalone Dart file is a library, regardless of a `library` name. Resolve
  unique top-level functions in that file, including recursion. Same names in an
  unrelated file or another language never supply fallback targets.
- Follow plain relative import URIs to indexed files within the project, with the
  [package-context guards](DART_PACKAGES.md) when a root package configuration exists.
  Respect prefixes, `show`/`hide`, underscore privacy and local declaration
  precedence. Ambiguous names, missing/excluded libraries and opaque imports
  abstain. Multiple imports with one prefix require a unique visible declaration.
- Resolve local `package:` imports through the bounded [root JSON configuration
  and URI identity subset](DART_PACKAGES.md), without Pub or SDK discovery.
- Resolve explicitly qualified static methods on a unique class declared in the
  caller's library. Instance, interface, inherited, extension and mixin dispatch,
  constructors and imported-class method chains remain unresolved.
- All resolution from class/mixin/extension member contexts abstains: field and
  inherited-member shadowing are not inferred. Whole-function shadowing considers
  parameters, type parameters, local functions/variables, loops, catches and nested
  callbacks. Pattern-bearing scopes abstain entirely. This can omit valid calls,
  but must not turn local values into package functions or import prefixes.
- Part/part-of libraries and syntax with parser errors are searchable, but neither
  callers nor targets participate in resolution. Conditional/deferred imports,
  `dart:` SDK resolution and re-export traversal remain outside the supported subset.
  URI escapes, interpolation, query/fragment and encoded paths are not followed.
  No pubspec resolution, config generation or implicit compiler/dependency discovery.
- Calls and references use exclusive Dart resolution. High confidence describes
  the static binding, not execution. SDK/compiler diagnostics remain separate.

The existing transaction, source hashes, ignore/symlink boundaries, engine
fingerprint and readonly CLI/MCP query interfaces apply unchanged. Changing an
import changes the source hash; target changes/deletions trigger global resolution.
No DB migration or dependency is introduced. Search and impact use existing APIs;
`package:test` callback discovery is not implemented or inferred from names.

## Verification

Require positive and negative library/privacy/prefix/static/shadowing cases,
initializer and callback references, complete source spans, partial syntax,
incremental/full equivalence, rollback and CLI/MCP readonly parity. Preserve the
600-test baseline and the existing fixture/oracle results. New Dart projects are
measured on the candidate separately: the baseline has no Dart adapter.

Native observations use trusted synthetic programs and a checksum-verified temporary
Dart 3.6.2 SDK. Invoke the VM with `--disable-dart-dev`: no pub/get, Dart CLI analytics,
build hooks or project configuration. Record exact sources, runtime, archive checksum
and outputs. Normal tests read recorded observations without looking for Dart.
Inspect a bounded real SDK source case separately; source extraction is not a
native whole-project test.

## SDK 3.6.2 partial-file diagnosis

The SDK workload recorded in [the 1.7.0 scaling results](../benchmarks/SCALING.md)
contains 425 Dart files: 396 parse without errors and 29 have parser errors.
A follow-up on 13 November 2026 reproduced the exact 29 paths with
tree-sitter-language-pack **0.13.0** and tree-sitter **0.25.2**, directly on the
original bytes, before the Dart adapter runs. The source hashes match both the
recorded index and the SDK copy. The eight disjoint causes below account for all
29 files; no adapter bug causing these partial statuses was found.

| Parser limitation | Files | Decision |
|---|---:|---|
| Unnamed `library;` directive | 7 | Keep partial and abstain |
| Labels on blocks or loops | 7 | Keep partial and abstain |
| `abstract` fields, including nullable function types | 7 | Keep partial and abstain |
| `external` fields in classes, extensions or library scope | 3 | Keep partial and abstain |
| Named arguments spelled `get:` or `set:` | 2 | Keep partial and abstain |
| Empty record literal `()` | 1 | Keep partial and abstain |
| Typed local variable named `set` | 1 | Keep partial and abstain |
| Empty `;` body of a `for` statement | 1 | Keep partial and abstain |
| **Total** | **29** | **Parser change deferred** |

Paths below are relative to the SDK's `lib/` directory:

- Unnamed library:
  `_internal/js_dev_runtime/private/ddc_only.dart`,
  `_internal/js_runtime/lib/dart2js_only.dart`,
  `_internal/js_runtime/lib/synced/embedded_names.dart`,
  `_internal/js_shared/lib/synced/async_status_codes.dart`,
  `_internal/js_shared/lib/synced/recipe_syntax.dart`,
  `js_interop/js_interop.dart`, `js_interop_unsafe/js_interop_unsafe.dart`.
- Labels:
  `_internal/vm/lib/convert_patch.dart`,
  `_internal/wasm/lib/compact_hash.dart`,
  `_internal/wasm/lib/convert_patch.dart`,
  `_internal/wasm_js_compatibility/lib/convert_patch.dart`,
  `convert/json.dart`, `convert/utf.dart`, `io/stdio.dart`.
- Abstract fields:
  `_internal/vm/lib/ffi_patch.dart`,
  `_internal/vm_shared/lib/compact_hash.dart`,
  `async/stream_controller.dart`, `io/io_resource_info.dart`,
  `io/security_context.dart`, `io/socket.dart`, `isolate/isolate.dart`.
- External fields:
  `_internal/vm/lib/ffi_native_finalizer_patch.dart`,
  `_internal/wasm/lib/weak_patch.dart`, `ffi/ffi.dart`.
- Named `get`/`set` arguments:
  `_internal/js_dev_runtime/private/ddc_runtime/records.dart`,
  `_internal/js_dev_runtime/private/ddc_runtime/rtti.dart`.
- Empty record: `_internal/js_runtime/lib/records.dart`.
- Typed `set` local: `collection/splay_tree.dart`.
- Empty `for` body: `_http/http_date.dart`.

These are parser coverage limits for forms present in the 3.6.2 SDK, rather than
evidence that those source files are invalid Dart or require a newer Dart version.
For example, [unnamed libraries were introduced in Dart 2.19](https://dart.dev/resources/language/evolution#dart-2-19),
and the accepted specifications cover [abstract/external fields in 2.12](https://github.com/dart-lang/language/blob/main/accepted/2.12/abstract-external-fields/feature-specification.md)
and [empty records in 3.0](https://github.com/dart-lang/language/blob/main/accepted/3.0/records/feature-specification.md).

Each cause was isolated with a minimal parser input and an in-memory diagnostic
control on the affected full files. The controls deliberately alter syntax and
sometimes semantics; they are never applied to indexed sources. Reducing these
29 partials requires a grammar/parser change, outside this increment. No parser,
dependency, adapter or historical benchmark receipt changes here.

The regression test checks the product boundary: an AST with errors is reported
as partial, and neither its callers nor its targets produce resolved calls or
references. It derives the expected partial set from the parser rather than
requiring these grammar bugs to persist. Complete controls and transitions to and
from the affected forms are checked through incremental and full indexing.
Existing `part`/`part of` exclusions still apply independently. No SDK source was
executed; a parser without errors alone does not prove extraction completeness,
name-resolution correctness or runtime behavior.

References checked 11 November 2026: [specification status](https://dart.dev/resources/language/spec),
[libraries and imports](https://dart.dev/language/libraries),
[classes](https://dart.dev/language/classes),
[extension methods](https://dart.dev/language/extension-methods).
The Dart 3 specification remains in progress; this contract states implementation
coverage rather than claiming whole-language conformance.

## Candidate grammar rejected after source verification

On 13 November 2026, newer grammar distributions were investigated. Production
continues to use **tree-sitter-language-pack 0.13.0** and **tree-sitter 0.25.2**;
the 29 partial SDK files listed above remain the measured production baseline.
Neither requirements nor the parser loader changed.

The latest pack, [1.25.0](https://pypi.org/project/tree-sitter-language-pack/1.25.0/),
was excluded before installation: it downloads individual parsers on first use
and requires Python 3.11+, unlike the bundled/offline and Python 3.10+ product
requirements. A previously populated download cache is not a bundled grammar.

The individual [`tree-sitter-dart` 0.1.0](https://pypi.org/project/tree-sitter-dart/0.1.0/)
wheel is an MIT-licensed Python packaging fork maintained by
[Efren Bautista](https://github.com/efrenbl/tree-sitter-dart), derived from
UserNobody14's grammar; it is not the Dart SDK or a Google-maintained compiler.
It includes the compiled parser and has Python 3.8+ ABI wheels for macOS arm64
and Linux. It was tested with tree-sitter 0.25.2 in a separate temporary environment.
The `tree-sitter-dart-orchard` 0.7.1 release has only a source distribution on PyPI,
so it was not compiled or adopted as a binary-wheel dependency.

### Provenance and independent build

The 0.1.0 source archive SHA-256 is
`5d1566e150f5ad8cd79ae225ff89db3eaf4a5e4cbbc667839578aee0512c3fdd`;
the macOS arm64 wheel SHA-256 is
`715948740c5a2398564b20e0303ce855024ebce1a6c8de79e8468f71c4fe9127`.
`grammar.js`, generated `src/parser.c`, `src/scanner.c` and the parser header
match [UserNobody14 commit `a9bdfa3db2fbc9b9f12c93450d04a671f33a5102`](https://github.com/UserNobody14/tree-sitter-dart/commit/a9bdfa3db2fbc9b9f12c93450d04a671f33a5102)
byte for byte. The fork's `grammar.json` and node-type definitions match that
commit too; those two files are absent from the published source archive.
The fork changes Python packaging and CI, not these grammar sources.

An independent local build compiled the upstream C parser and scanner at that
commit with Apple clang 21 and a minimal Python PyCapsule bridge. Against the
published wheel, the following comparisons found **zero differences**:

- All **425 SDK Dart files**, plus static Dart fixtures and Dart oracle inputs:
  **451 inputs**, 446 distinct sources, **1,485,170 nodes**.
- Generated inputs observed during all **51 Dart and Dart-package test executions**:
  350 parses, 87 distinct sources, **3,692 nodes**.

The comparison includes every named and anonymous child, extra/error/missing flags,
node kind, field, ordering, byte span and row/byte-column position. These checks
establish the measured wheel/source correspondence on macOS arm64, not a proof
for all Dart programs or an independently rebuilt Linux wheel.

### Fewer syntax errors did not preserve call extraction

The candidate accepts all 425 SDK sources without syntax errors: **29 partial
files become zero**, and the twelve problematic minimal inputs covering the eight
causes above become complete. Of the 396 previously complete files, 388 keep the
same named-tree representation and eight change. Applying the unchanged Dart
extractor to those eight gives the following differences:

| SDK `lib/` path | Candidate minus production extraction |
|---|---|
| `_internal/js_dev_runtime/patch/async_patch.dart` | 1 call added; 2 former function-value references removed |
| `_internal/js_dev_runtime/patch/core_patch.dart` | 1 dynamic constructor callsite lost (`Map<K,V>.from`, line 376) |
| `_internal/js_runtime/lib/async_patch.dart` | 1 call added; 2 former function-value references removed |
| `_internal/js_runtime/lib/core_patch.dart` | Calls unchanged; 2 references added |
| `_internal/js_runtime/lib/js_array.dart` | Calls unchanged; 4 references added |
| `_internal/js_shared/lib/rti.dart` | 1 generic call lost (`TYPE_REF<()>`, line 1085); 1 reference added |
| `_internal/vm/lib/array.dart` | Calls unchanged; 2 references added |
| `_internal/wasm/lib/type.dart` | Calls unchanged; 4 references added |

Symbols and observations in those eight files are unchanged. The two added
`StreamController<T>` calls reflect corrected disambiguation, but the losses and
new generic-expression references prevent treating the complete delta as an
improvement. The following reduced source demonstrates the generic-call regression:

```dart
int leaf<T>() => 1;
int entry() => leaf<()>();
```

Both parsers report no syntax errors. The production grammar records a call
`entry -> leaf` with `high` confidence; 0.1.0 parses comparisons/record literals,
loses the call and records `leaf` as a function-value reference instead. This was
reproduced through the actual index with only the parser substituted in memory;
no Dart source was executed. The unchanged Dart test suite alone did not catch it.

**Decision: do not adopt 0.1.0.** A future candidate must pass both source/build
verification and extraction/resolution comparisons, including this counterexample;
reducing the partial-file count alone is insufficient. No adapter workaround,
source normalization or dependency change is introduced by this investigation.
