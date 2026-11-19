import runpy
from pathlib import Path


def store_class():
    return runpy.run_path(str(Path(__file__).with_name('store.py')))['Store']


def open_store(root):
    with store_class()(root) as store:
        return store
