# Swift adapter: first supported analysis subset

Development contract, base `ab662f6`. Target: the bounded Swift language mode 5
subset below, tested with local Apple Swift 6.4.2 and its macOS SDK. This is not
certification of the Swift 5.0 compiler or the full Swift language. Indexing needs
no Swift/Xcode installation and never executes a package manifest, compiler or project.

## Source evidence

- Index `.swift` declarations: functions, nominal types, protocols, properties,
  initializers/deinitializers and subscripts, with complete source spans. Extensions
  have distinct source owners, preserving their origin rather than merging into a type.
- Record callsites separately from function-value references, including labels-only
  selections such as `leaf(_:)`. Indirect/returned function calls remain unresolved;
  a reference is not an immediate invocation. Keep argument references inside chains.
- Properties/default arguments and executable top-level statements retain their
  actual source positions. Initializers use the file-module/type owner; closures
  remain lexical containment, not proof of execution. Member contexts abstain.
- The pinned parser represents some postfix calls over infix expressions with a
  binary-expression head. Normalize only its unparenthesized right operand for call
  lookup; grouped expressions and unknown receiver chains do not become bare names.
  SwiftPM `.build` output is excluded with existing build/cache directories.

## Binding boundary

- Resolve unique same-file private/fileprivate global functions with a body.
  Internal/public globals and cross-file/module binding remain unresolved.
  Private/fileprivate does not close overload lookup: count all indexed top-level
  declarations with the same base name, including non-private and excluded files,
  before eligibility filtering. Adding/removing a blocker re-resolves calls AND refs.
  Do not infer a Swift module from directories or evaluate Package.swift/Xcode targets.
- Resolve directly declared `static` functions on same-file private/fileprivate
  nominal types. Instance/class-func/protocol/inheritance/generic/extension dispatch
  and constructor targets abstain. Matching extensions, any overload, an enum case or a property
  sharing the member name block the target. Explicit private members are not promoted
  to file-private. All member callers, including initializer/property bodies, abstain.
- Implicit SDK overloads matter even without imports. A pinned inert set of 450
  top-level public/SPI names from SDK symbol graphs (Swift, _Concurrency,
  _StringProcessing), extracted in language mode 5, prevents false singleton globals
  and type qualifiers, including `abs`, `Task` and
  `withUnsafeCurrentTask`. It supplies no SDK targets or type inference. Provenance,
  compiler/SDK version and graph hashes are retained in the author evidence; these
  guards do not establish completeness for different SDKs, modes or platforms.
- Locals, parameters, patterns, type aliases and generic parameters block callable
  or type-like interpretations. Whole-scope shadowing can omit valid bindings.
  Closure-bearing, generic, local-function/type and complex-pattern scopes abstain.
  Any indexed type alias blocks nominal static resolution: an extension through an
  alias may introduce overloads on the original type. No alias/extension type checker
  is inferred; bare private functions retain their other binding guards.
  Unknown receivers never fall through to a same-name private global.
- Files with explicit imports/directives, attributes, macros, `dynamic` or parser
  errors are searchable but do not resolve. Parser errors/attributes/macros/dynamic
  in any indexed Swift file make the project namespace unknown and disable Swift
  resolution: names may be missing or synthesized. Ordinary import-only files still
  contribute their declared global names as overload blockers. Ignored/build outputs
  are outside the analyzed source set; this remains bounded static evidence.
- Overloads are grouped by base name, including argument labels, defaults and
  generics. No arity/type/label overload selection or SDK/compiler diagnostics.

Calls/references have exclusive Swift dispatch. Existing observations, IDs, atomic
indexing/rollback, source hashes, engine fingerprint, FTS, source windows, impact and
readonly CLI/MCP interfaces are reused. The guard set is embedded in the fingerprinted
adapter: no SDK lookup at index time. No database migration or dependency is added.
XCTest discovery is not inferred from names.

## Verification

Require positive and negative file visibility, SDK/cross-file overload blockers,
nominal/static/extension boundaries, shadowing, references versus invocation,
top-level/default/property ownership, complete spans and cache/rollback/readonly
checks. Preserve the 649-test baseline and 79 previous fixture projects. New Swift
coverage is measured separately from the baseline with no Swift adapter.

Native probes compile only inspected synthetic sources in language mode 5, using
explicit compiler/SDK, isolated cache/output and sanitized driver/include/link/loader
environment. No SwiftPM/Xcode build, user macro/plugin or SDK source execution.
Recorded cases require no native toolchain. The SDK Observation interface is a
separate bounded source case: newer syntax is partial for this parser, reported as
such, with zero resolved targets; it is not a real application execution test.

```sh
.venv/bin/python -I tools/oracle_matrix.py --language swift --rerun --swiftc /path/to/swiftc --swift-sdk /path/to/sdk --output /tmp/swift-oracle.json
```

References checked 11 November 2026: official TSPL sources for
[access control](https://raw.githubusercontent.com/swiftlang/swift-book/main/TSPL.docc/LanguageGuide/AccessControl.md),
[methods](https://raw.githubusercontent.com/swiftlang/swift-book/main/TSPL.docc/LanguageGuide/Methods.md),
[extensions](https://raw.githubusercontent.com/swiftlang/swift-book/main/TSPL.docc/LanguageGuide/Extensions.md).
Local compiler counterexamples, not a newer book's wording, establish the tested bindings.
