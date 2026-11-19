"""Compare lexical and hybrid retrieval on declared, unexecuted synthetic sources."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import sqlite3
import statistics
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', type=Path, default=REPO / 'benchmarks/search-retrieval.json')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('cg_search_benchmark', REPO / 'scripts/cg.py')
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    ProjectData = runpy.run_path(str(REPO / 'scripts/project_data.py'))['ProjectData']
    search = engine.search_module()['search']
    raw = args.cases.read_bytes()
    cases = json.loads(raw)
    records = []
    with tempfile.TemporaryDirectory(prefix='cg-retrieval-') as temporary:
        for case in cases['projects']:
            root = Path(temporary) / case['id']
            root.mkdir()
            for name, source in case['sources'].items():
                path = engine.local_path(root, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            with contextlib.redirect_stdout(io.StringIO()):
                engine.cmd_index(argparse.Namespace(root=str(root), full=True))
            for query in case['queries']:
                expected = set(query['relevant'])
                record = dict(project=case['id'], query=query['text'], relevant=sorted(expected), modes={})
                for mode in ('lexical', 'hybrid'):
                    start = time.perf_counter()
                    with ProjectData(engine, root) as project:
                        result = search(project, query['text'], mode=mode, limit=5, max_chars=16000)
                    elapsed = (time.perf_counter() - start) * 1000
                    hits = [item['symbol']['file'] + ':' + item['symbol']['name'] for item in result['items']]
                    ranks = [i for i, hit in enumerate(hits, 1) if hit in expected]
                    record['modes'][mode] = dict(hits=hits, missing=sorted(expected-set(hits)),
                        recall_at_5=len(expected.intersection(hits))/len(expected) if expected else None,
                        reciprocal_rank=1/min(ranks) if ranks else 0,
                        unexpected=len(set(hits)-expected), milliseconds=elapsed,
                        payload_chars=len(json.dumps(result, ensure_ascii=False)))
                records.append(record)
    summary = {}
    for mode in ('lexical', 'hybrid'):
        values = [r['modes'][mode] for r in records if r['relevant']]
        summary[mode] = dict(recall_at_5=statistics.mean(v['recall_at_5'] for v in values),
                             mean_reciprocal_rank=statistics.mean(v['reciprocal_rank'] for v in values),
                             median_milliseconds=statistics.median(v['milliseconds'] for v in values))
    report = dict(format='codegraph-search-benchmark/1', cases_sha256=hashlib.sha256(raw).hexdigest(),
                  engine_hashes=engine.analysis_inputs()[0], sqlite_version=sqlite3.sqlite_version,
                  scope=cases['description'], measured_tokens=None, summary=summary, queries=records)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
