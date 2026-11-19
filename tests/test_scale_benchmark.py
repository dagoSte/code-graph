"""Reproducible scale inputs and invariants; no compiler, model or project run."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scale_benchmark_test_subject', REPO / 'benchmarks/scale.py')
scale = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scale)


class ScaleInputs(unittest.TestCase):
    def test_eight_language_counts_noop_and_single_file_increment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = scale.generate(root, 2000)
            result = scale.worker(REPO, root)
            counts = result['phases']['full']['counts']
            for key, value in expected['expected'].items():
                self.assertEqual(counts[key], value, key)
            self.assertEqual(len(counts['languages']), 8)
            self.assertEqual(counts['partial_files'], 0)
            self.assertTrue(result['noop_content_revision_stable'])
            self.assertTrue(result['noop_parsed_nothing'])
            self.assertTrue(result['incremental_parsed_one'])
            self.assertFalse(result['source_execution'])

    def test_real_source_snapshot_is_stable_and_source_is_never_imported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original, copied = root / 'original', root / 'copied'
            original.mkdir()
            marker = root / 'source-executed'
            (original / 'app.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("bad")\ndef leaf(): return 1\n')
            engine = scale.load(REPO)
            first, hashes = scale.freeze_source(engine, original, copied)
            second, other = scale.freeze_source(engine, original, root / 'second')
            self.assertEqual(first, second)
            self.assertEqual(hashes, other)
            self.assertEqual(first['files'], 1)
            scale.worker(REPO, copied)
            self.assertFalse(marker.exists())
            self.assertEqual((original / 'app.py').read_bytes(), (root / 'second/app.py').read_bytes())
