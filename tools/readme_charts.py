"""Render the README charts from the committed benchmark results.

Usage: python3 -I tools/readme_charts.py [--check]
Reads benchmarks/search-retrieval-results.json (benchmarks/search_retrieval.py),
benchmarks/scaling-1.7.0.json and benchmarks/scaling-go-1.8.json (benchmarks/scale.py), and writes
docs/images/retrieval-{light,dark}.svg and docs/images/scale-{light,dark}.svg.
With --check, exits 1 if the files on disk differ from a fresh rendering.
"""
import json
import sys
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / 'benchmarks/search-retrieval-results.json'
SCALING = REPO / 'benchmarks/scaling-1.7.0.json'
GO_FOLLOWUP = REPO / 'benchmarks/scaling-go-1.8.json'
OUTPUTS = {mode: REPO / f'docs/images/retrieval-{mode}.svg' for mode in ('light', 'dark')}
SCALE_OUTPUTS = {mode: REPO / f'docs/images/scale-{mode}.svg' for mode in ('light', 'dark')}
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
LANGUAGE = {'.py': 'Python', '.ts': 'TypeScript', '.php': 'PHP', '.js': 'JavaScript'}
THEMES = {
    'light': dict(surface='#fcfcfb', text='#0b0b0b', muted='#52514e', grid='#d9d8d4', track='#ecebe7',
                  lexical='#2a78d6', graph='#eb6834'),
    'dark': dict(surface='#1a1a19', text='#f0efec', muted='#c3c2b7', grid='#3a3a37', track='#2a2a28',
                 lexical='#3987e5', graph='#d95926'),
}


def rows(report):
    """One row per query with declared relevant symbols: counts found by each mode."""
    result = []
    for query in report['queries']:
        relevant = set(query['relevant'])
        if not relevant:
            continue
        lexical = relevant & set(query['modes']['lexical']['hits'])
        hybrid = relevant & set(query['modes']['hybrid']['hits'])
        languages = sorted({LANGUAGE.get(Path(item.split(':')[0]).suffix, '?') for item in relevant})
        result.append(dict(query=query['query'], language=' + '.join(languages), relevant=len(relevant),
                           lexical=len(lexical), hybrid=len(hybrid), kept=len(hybrid & lexical),
                           added=len(hybrid - lexical)))
    return result


def render(report, mode):
    t = THEMES[mode]
    data = rows(report)
    unit, left, top, row_h, bar_h = 64, 230, 92, 44, 18
    width = left + unit * max(r["relevant"] for r in data) + 250
    height = top + row_h * len(data) + 58
    found = sum(r['lexical'] for r in data), sum(r['hybrid'] for r in data), sum(r['relevant'] for r in data)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'role="img" aria-labelledby="t d" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif">',
           '<title id="t">Relevant symbols in the top five results, per query</title>',
           f'<desc id="d">BM25 text matching alone finds {found[0]} of {found[2]} relevant symbols; '
           f'adding call-graph expansion with RRF finds {found[1]} of {found[2]}. Synthetic benchmark, '
           f'{len(data)} queries.</desc>',
           f'<rect width="{width}" height="{height}" rx="12" fill="{t["surface"]}"/>',
           f'<text x="24" y="34" font-size="16" font-weight="600" fill="{t["text"]}">Relevant symbols in the top 5 results</text>',
           f'<text x="24" y="56" font-size="12.5" fill="{t["muted"]}">Synthetic retrieval benchmark · {len(data)} queries with declared answers · '
           f'BM25 alone {found[0]}/{found[2]} · hybrid {found[1]}/{found[2]}</text>']
    legend_y = 76
    for i, (key, label) in enumerate((('lexical', 'Also found by BM25 alone'), ('graph', 'Found only with call-graph expansion'))):
        x = 24 + i * 210
        out.append(f'<rect x="{x}" y="{legend_y - 9}" width="10" height="10" rx="2" fill="{t[key]}"/>')
        out.append(f'<text x="{x + 16}" y="{legend_y}" font-size="12" fill="{t["muted"]}">{escape(label)}</text>')
    for i, r in enumerate(data):
        y = top + i * row_h
        out.append(f'<text x="24" y="{y + 13}" font-size="13" font-weight="600" fill="{t["text"]}">“{escape(r["query"])}”</text>')
        out.append(f'<text x="24" y="{y + 29}" font-size="11.5" fill="{t["muted"]}">{escape(r["language"])}</text>')
        out.append(f'<rect x="{left}" y="{y + 4}" width="{unit * r["relevant"]}" height="{bar_h}" rx="4" fill="{t["track"]}"/>')
        x = left
        for key, count in (('lexical', r['kept']), ('graph', r['added'])):
            for _ in range(count):
                out.append(f'<rect x="{x}" y="{y + 4}" width="{unit - 2}" height="{bar_h}" rx="4" fill="{t[key]}"/>')
                x += unit
        label = f'{r["hybrid"]}/{r["relevant"]}  (BM25 alone {r["lexical"]}/{r["relevant"]})'
        out.append(f'<text x="{left + unit * r["relevant"] + 12}" y="{y + 18}" font-size="12" fill="{t["text"]}">{escape(label)}</text>')
    axis_y = top + row_h * len(data) + 6
    out.append(f'<line x1="{left}" y1="{top - 2}" x2="{left}" y2="{axis_y - 8}" stroke="{t["grid"]}" stroke-width="1"/>')
    out.append(f'<text x="24" y="{axis_y + 16}" font-size="11" fill="{t["muted"]}">Blocks = relevant symbols in the hybrid top 5; grey = not in the hybrid top 5. Cases are synthetic and small:</text>')
    out.append(f'<text x="24" y="{axis_y + 32}" font-size="11" fill="{t["muted"]}">they show the mechanism, not a general accuracy figure. A query with no answer returns nothing in both modes.</text>')
    out.append('</svg>')
    return '\n'.join(out) + '\n'


def scale_rows(scaling, followup):
    """One row per completed workload, smallest first; the Go SDK uses the post-fix follow-up run."""
    names = {'generated-10000': 'Generated, 8 languages', 'generated-100000': 'Generated, 8 languages',
             'generated-1000000': 'Generated, 8 languages', 'dart-sdk': 'Dart SDK 3.6 (real)', 'go-stdlib': 'Go SDK (real)'}
    result = []
    for workload in scaling['workloads']:
        phases = (workload.get('measurement') or {}).get('phases')
        if workload['label'] == 'go-stdlib':
            assert followup['input_workload'] == 'scaling-1.7.0.json#go-stdlib'
            phases, note = followup['measurement']['phases'], 'was > 600 s before the fix'
        else:
            note = ''
        if not phases:
            continue
        result.append(dict(name=names[workload['label']], lines=workload['input']['source_lines'],
                           files=phases['full']['counts']['files'], full=phases['full']['seconds'],
                           change=phases['one_file_change']['seconds'], note=note))
    return sorted(result, key=lambda row: row['lines'])


def render_scale(scaling, followup, mode):
    t = THEMES[mode]
    data = scale_rows(scaling, followup)
    left, top, row_h, bar_h, plot = 250, 96, 58, 14, 330
    width, height = left + plot + 150, top + row_h * len(data) + 40
    peak = max(row['full'] for row in data)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'role="img" aria-labelledby="st sd" font-family="{escape(FONT)}">',
           '<title id="st">Indexing time: full index and re-index after a one-file change</title>',
           '<desc id="sd">' + escape('; '.join(f"{r['name']}, {r['lines']:,} lines: full {r['full']:.1f} s, "
                                            f"one-file change {r['change']:.2f} s" for r in data)) + '</desc>',
           f'<rect width="{width}" height="{height}" rx="12" fill="{t["surface"]}"/>',
           f'<text x="24" y="34" font-size="16" font-weight="600" fill="{t["text"]}">Indexing time (seconds)</text>',
           f'<text x="24" y="56" font-size="12.5" fill="{t["muted"]}">macOS ARM64, Python 3.13 · no source executed · re-indexing parses only the changed file</text>']
    for i, (key, label) in enumerate((('lexical', 'Full index'), ('graph', 'Re-index after a one-file change'))):
        x = 24 + i * 130
        out.append(f'<rect x="{x}" y="67" width="10" height="10" rx="2" fill="{t[key]}"/>')
        out.append(f'<text x="{x + 16}" y="76" font-size="12" fill="{t["muted"]}">{label}</text>')
    for i, r in enumerate(data):
        y = top + i * row_h
        out.append(f'<text x="24" y="{y + 12}" font-size="13" font-weight="600" fill="{t["text"]}">{r["lines"]:,} lines</text>')
        out.append(f'<text x="24" y="{y + 29}" font-size="11.5" fill="{t["muted"]}">{escape(r["name"])} · {r["files"]:,} files</text>')
        if r['note']:
            out.append(f'<text x="24" y="{y + 45}" font-size="11.5" fill="{t["muted"]}">{escape(r["note"])}</text>')
        for j, (key, value, text) in enumerate((('lexical', r['full'], f'{r["full"]:.1f} s'),
                                               ('graph', r['change'], f'{r["change"]:.2f} s'))):
            by = y + j * (bar_h + 3)
            w = max(3, plot * value / peak)
            out.append(f'<rect x="{left}" y="{by}" width="{w:.1f}" height="{bar_h}" rx="3" fill="{t[key]}"/>')
            out.append(f'<text x="{left + w + 8:.1f}" y="{by + 11}" font-size="11.5" fill="{t["text"]}">{text}</text>')
    axis = top + row_h * len(data) - 10
    out.append(f'<line x1="{left}" y1="{top - 4}" x2="{left}" y2="{axis}" stroke="{t["grid"]}" stroke-width="1"/>')
    out.append(f'<text x="24" y="{axis + 26}" font-size="11" fill="{t["muted"]}">Single observations on a running desktop. Peak memory, query times and limits: benchmarks/SCALING.md.</text>')
    out.append('</svg>')
    return '\n'.join(out) + '\n'


def main(argv):
    report = json.loads(RESULTS.read_text())
    scaling, followup = json.loads(SCALING.read_text()), json.loads(GO_FOLLOWUP.read_text())
    stale = []
    renders = [(path, render(report, mode)) for mode, path in OUTPUTS.items()]
    renders += [(path, render_scale(scaling, followup, mode)) for mode, path in SCALE_OUTPUTS.items()]
    for path, svg in renders:
        if '--check' in argv:
            if not path.exists() or path.read_text() != svg:
                stale.append(str(path.relative_to(REPO)))
        else:
            path.write_text(svg)
    if stale:
        print('stale charts: ' + ', '.join(stale) + '; run python3 -I tools/readme_charts.py')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
