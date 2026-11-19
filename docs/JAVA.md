# Java adapter: first supported analysis subset

Development contract, base `23822a2`. Target: the Java SE 17 subset described here,
using the pinned tree-sitter grammar. Native probes use OpenJDK 21.0.2 with
`javac --release 17`. Indexing never invokes a compiler, Maven/Gradle, annotation
processors, build hooks or project code. No JDK or dependency download is required.

## Extraction and ownership

- Index `.java` declarations: classes, interfaces, enums, records, annotations,
  methods, constructors and fields. Preserve complete byte spans for source and FTS.
  Overloads remain separate declarations even when qualified names match.
- Record method/constructor invocation sites, including arguments and constructor
  bodies. Field and static/instance initializers use the owning class; methods and
  constructors own their bodies. Lambdas remain lexical containment, not proof of
  execution. Local/nested/anonymous classes have their own owners and abstain.
- Explicit method references are separate from invocations. Constructor references
  remain unresolved constructor references; `C::new` is never a static method `C()`.
  Plain field/variable names are not interpreted as method-value references.

## Resolution boundary

- Resolve unique, directly declared static methods. Lexical unqualified/`this`
  calls may also resolve private/final methods or methods of a final class.
  Arbitrary instance receivers, interface/virtual/inherited dispatch, constructor
  targets and receiver chains abstain. Initializers resolve static targets only.
- Count the complete same-name method group before filtering modifiers, visibility,
  body or arity. Any overload group abstains; no argument-type/boxing/varargs/generic
  overload inference. Record component/accessor names and implicit Object/Enum names
  abstain, avoiding false singleton groups from missing synthesized declarations.
- Callers or targets with explicit superclass/interfaces abstain. Nested/local/
  anonymous contexts abstain. Type parameters, fields, local variables, loops,
  catches, lambda parameters and instanceof bindings block type-like qualifiers.
  Whole-method shadowing may omit valid calls. Unsupported pattern scopes abstain.
  Java variable names do not shadow the namespace of unqualified method invocations.
- Type lookup: own compilation unit, explicit single-type imports, same package,
  or explicit fully qualified names. Cross-file targets must be unique indexed
  top-level types in the same conventional source root, derived from the directory
  suffix matching the declared package. Nonconforming layouts and distinct roots
  do not gain a global fallback. No Maven/Gradle/source-set/classpath discovery.
- Check both type and method visibility. Cross-package public types/methods may be
  followed; private requires the same owner class; package/protected requires the
  same package in this subset. Same file does not grant access to private members
  of another top-level type. Explicit static imports require a unique accessible
  static declaration; wildcard/unknown/ambiguous imports abstain.
- A `module-info.java` presence among ancestors of selected Java sources disables
  cross-file resolution. Ignored, invalid, unreadable, oversized or symlinked
  descriptors are boundaries too; no symlink is followed. Bounded presence/content
  markers participate in config freshness and trigger re-resolution even with
  unchanged Java sources. JPMS requires/exports are not interpreted.
- Parser-error files and sources containing backslash-u escapes abstain: Java
  Unicode preprocessing is not implemented. SDK/external classes, generated code,
  reflection and dependency injection remain unresolved. Binding confidence is
  distinct from runtime execution, and compiler diagnostics remain separate.

Calls and references have exclusive Java dispatch. Existing symbols/observations,
transactions, rollback, source hashes, ignore/symlink rules, fingerprint and readonly
CLI/MCP APIs are reused. No DB migration or dependency is added. Search and impact
work through existing interfaces; JUnit/TestNG discovery is not inferred from names.

## Verification

Require positive/negative namespace, overload, accessibility, qualifier shadowing,
private/final/static/virtual, implicit record accessor and constructor-reference
cases. Cover initializer/nested ownership, full spans, cache isolation, rollback,
JPMS add/remove/ignored/symlink/oversize, target changes and readonly CLI/MCP parity.
Preserve the 622-test baseline and 78 prior fixture projects. Measure Java coverage
separately because the baseline has no Java adapter; never accept an empty index as
an equivalence result.

Recorded native observations work without a JDK. Explicit native reruns compile
only inspected synthetic cases with `--release 17 -proc:none -implicit:none`, empty
source/class paths and temporary output; inherited Java options/classpath are removed.
No project build system or processor is invoked. Version, archive checksum, sources
and outputs are bound in the oracle. The bounded StringJoiner SDK source case is
inspected separately, without compilation/execution or a whole-JDK claim.

```sh
.venv/bin/python -I tools/oracle_matrix.py --language java --rerun --java-home /path/to/jdk --output /tmp/java-oracle.json
```

References checked 11 November 2026: [JLS 17 names](https://docs.oracle.com/javase/specs/jls/se17/html/jls-6.html),
[packages/imports/modules](https://docs.oracle.com/javase/specs/jls/se17/html/jls-7.html),
[methods and records](https://docs.oracle.com/javase/specs/jls/se17/html/jls-8.html),
[invocation and references](https://docs.oracle.com/javase/specs/jls/se17/html/jls-15.html).
