import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load('store_test', 'store.py')


def test_helper():
    assert store.helper() == 1
