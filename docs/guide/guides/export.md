# Export

`cg export` writes one HTML file with an interactive picture of the call graph, to open offline or
attach to a ticket.

```bash
cg export                                                   # whole project → .code-graph/explore.html
cg export --symbol cancel_order --depth 1 --output .code-graph/cancel.html
```

On the example project:

<!-- cg: export --symbol cancel_order --depth 1 --output .code-graph/cancel.html -->
```text
$ cg export --symbol cancel_order --depth 1 --output .code-graph/cancel.html
Browsable HTML: ~/code/shop/.code-graph/cancel.html (8 nodes, 7 links). Open it in the browser.
```
<!-- /cg -->

- **Self-contained.** Scripts and styles are inline, behind a Content-Security-Policy that forbids
  network connections: the file loads nothing from the internet.
- **Bounded.** `--max-nodes` (default 5,000) and `--max-edges` (default 50,000) stop very large
  graphs; use `--symbol` with `--depth` to export a neighbourhood instead.
- **Inside the project.** The output path must be an `.html` file inside the project; an existing file
  is replaced only with `--force`.

The file contains names and paths from your code. Share it as you would share the code.
