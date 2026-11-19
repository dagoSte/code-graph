# Dart local package imports

Increment after `978752e`, extending the [Dart subset](DART.md). Target runtime
observations remain Dart 3.6.2; the bundled grammar and indexing require no SDK.
This reads existing configuration, never Pub/Flutter, hooks or dependency downloads.

## Configuration and context

Support one project-root `.dart_tool/package_config.json` in format version 2.
The configuration belongs to the program, not independently to each imported
library. Relevant nested JSON configurations make import resolution opaque across
the project; no entrypoint is inferred from filenames. Explicit `--packages`
overrides are outside the inferred context. Index an application root separately
when a workspace has several configurations.

The tested VM ignores legacy `.packages` files. They are neither interpreted nor
used as override boundaries. The older specification also describes a historical
transition to JSON; that transition is not attributed to the tested runtime.

Validate all entries before filtering for local targets, including unused external
roots: duplicate names/roots and forbidden package-directory overlaps make the
mapping opaque. The supported file-only URI subset normalizes rootUri relative to
the JSON file and packageUri relative to the root directory, appending its directory
slash; omitted packageUri uses that root. Decode valid UTF-8 percent escapes once
and normalize dot segments afterwards. Validate original URI strings before URL
normalization: raw ASCII spaces/controls, query/fragment delimiters (even empty),
authority, encoded separator/NUL forms and packageUri escaping its root are rejected.
Spaces in paths must be percent-encoded; encoded leading spaces and UTF-8 remain
supported without trimming. Package names require an allowed URI path character
other than `.`; an unused invalid entry also makes the whole mapping opaque.
External package directories must be disjoint from the analyzed workspace; they are inspected only
as URI strings and never opened. Targets require a local root and an indexed file;
URI locations match the canonical project root lexically, without following aliases.

JSON must be UTF-8, configVersion the integer 2, and packages a list of at most 500
entries. Validate languageVersion's major.minor spelling when present; its shape
does not certify language-feature support or select a compiler mode. Unknown
metadata supplies no new binding rules. Configurations are bounded to 200 KB.
Malformed, oversized, symlinked and nonregular boundaries remain explicit states
and cannot inherit another mapping. `.dart_tool` generated sources are excluded.

## URI identity and binding

Follow explicit `package:name/path.dart` only to indexed project sources. Preserve
prefix/show/hide/privacy, local precedence and getter/value blockers. Unknown,
excluded, external or missing imports cannot be silently dropped from ambiguity.
Parts, parser errors, conditional/deferred imports, exports and member contexts
retain their existing exclusions. Escaped/interpolated/query/fragment import URIs,
including direct package URI dot segments, remain unresolved.

A package URI and file URI can denote distinct Dart libraries at the same physical
path. Import candidates retain that distinction before uniqueness checks for both
calls and references. Nodes still identify source declarations, not runtime library
instances; two separately qualified prefixes can point to the same source node.

Relative imports keep the file model when no package configuration exists. Under a
valid mapping, a source inside a package URI directory can also be loaded as a
package library. Dart's relative resolution clamps at that package URI root. Check
both possible contexts and resolve only when they agree on the physical target;
otherwise abstain. Mixed explicit package/file identities remain conservatively
distinct even when one context might unify them. Invalid or nested contexts block
cross-library imports while same-library declarations retain their lexical rules.
This can omit valid bindings; it must not choose a different library's function.

## Index and readers

Discover root and selected-source ancestor boundaries even when Git ignores them.
Keep unreadable directory/leaf symlink and nonregular presence without following
external targets or opening FIFOs. Reuse the existing bounded configuration/status
hashes. The resolver consumes only stored metadata and indexed paths, never the
filesystem or SDK. No database schema, dependency or general config framework.

Configuration-only changes re-resolve calls and references without reparsing source.
Use one snapshot for the package mapping and its metadata hashes; a change during
index rolls back the transaction. Active package context includes the project root
in the content revision. Moving a cached project requires reindexing even when raw
configuration bytes match, since absolute roots can change their local meaning.

ProjectData consumers (guided API, search/context and source tools) and legacy graph
CLI opens reject changed Dart configuration/root metadata. Raw `cg_read` reads current
source without deriving a graph result. Existing source hash/readonly guards,
transactional FTS, import isolation and engine fingerprints remain in force.

## Evidence

Preserve the 703-test baseline, 81 previous fixture projects and existing native
oracles on identical frozen inputs. New package coverage is measured separately.
Regressions cover URI ambiguity, two relative contexts, ignored/status boundaries,
config-only A/B changes with an independent edge, calls plus tear-offs, rollback,
snapshot races, root moves and reader freshness. Native programs are inspected
synthetics using the checksum-verified Dart 3.6.2 VM with `--disable-dart-dev` and
analytics disabled. Recorded cases require no runtime during ordinary tests.

Semantic references checked 11 November 2026:
[package configuration v2](https://raw.githubusercontent.com/dart-lang/language/main/accepted/2.8/language-versioning/package-config-file-v2.md),
[language versioning](https://raw.githubusercontent.com/dart-lang/language/main/accepted/2.8/language-versioning/feature-specification.md).
The SDK accepts one unused overlap that the stated specification rejects; this
subset retains the stricter boundary rather than treating exit 0 as full validation.
