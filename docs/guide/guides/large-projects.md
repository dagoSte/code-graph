# Large projects

code-graph is built for codebases too large to read: the full Go SDK (2.2 million lines, 7,035 files)
indexes in 52 seconds on a laptop, and staying current costs seconds.

## Keep the index fresh

```bash
cg index          # incremental: re-parses only files whose content hash changed
cg index --full   # rebuild everything
```

After the first index, a one-file change on the Go SDK is re-indexed in 9 s and an unchanged project is
re-checked in 3 s ([measurements](../benchmarks.md#indexing)). `locate` and `start` run the incremental
index themselves; other commands refuse stale source instead of answering from old lines.

## Choose what is indexed

Skipped automatically: vendored dependencies (`vendor/`, `node_modules/`, `.venv/`), build folders,
Git-ignored files, symlinks, files over 1 MB and minified files. For other generated or vendored code,
add globs to `<project>/.cgignore`, one per line, then re-index:

```text
libs/*
public/vendor/*
*.pb.go
```

## See what was indexed

<!-- cg: stats -->
```text
$ cg stats
engine 1.9.0 · index schema 1.4.0 · indexed 2026-11-16 14:06:27
files 11 · lines 165 · symbols 23 · calls 41 · edges 26
definitions 23 · nodes 34 · resolved callsites 26 · distinct links 25
  edges high: 25
  edges low: 1
resolved calls to project code: 26/41 (63%) — the rest is stdlib/vendor/external or ambiguous · dynamic: 0
```
<!-- /cg -->

The share of resolved calls is the part of the project's calls that reach project code; the rest go to
the standard library, external packages or cannot be resolved statically. **Partial files** are files the parser could not fully read: what it could read is kept, the rest is
missing, and `start` reports how many there are.

## Map an unfamiliar codebase

<!-- cg: map --limit 5 -->
```text
$ cg map --limit 5
MAP · scope all · conf ≥medium
shop                                     8 files · 127 lines · 17 definitions
tests                                    3 files · 38 lines · 6 definitions
MODULE DEPENDENCIES
tests → shop (12 distinct links)
HOTSPOTS · distinct callers, modules, symbol lines
4 ← shop/orders.py:17-25 function cancel_order · 2 modules · 9 lines
3 ← shop/models.py:6-9 class Line · 1 modules · 4 lines
3 ← shop/orders.py:12-14 function pay_order · 2 modules · 3 lines
3 ← shop/orders.py:6-9 function place_order · 2 modules · 4 lines
1 ← shop/api.py:13-15 function handle_pay · 1 modules · 3 lines
```
<!-- /cg -->

`map` groups files into modules by directory (`--depth` levels), shows the dependencies between them and
the most-called functions. `--scope production` leaves tests out.

## Tips

- **Budgets.** Answers keep their size whatever the project size: `--max-chars` on `locate`, `search`,
  `context`, `prepare` and `review`, `--limit` elsewhere.
- **Confidence.** On large dynamic codebases, start with the default `medium`, and widen to `low` only to
  look for callers you suspect are missing.
- **Small projects.** Under about 30 source files, `start` says so: reading the files directly is usually
  as fast.
