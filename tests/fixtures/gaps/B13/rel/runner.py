import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location("_r_" + name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def in_if(flag):
    if flag:
        return _load("fs").inspect_roots(["a"])
    return None


def in_try():
    try:
        return _load("fs").inspect_roots(["b"])
    except OSError:
        return None


def in_for(items):
    for item in items:
        _load("fs").inspect_roots([item])


def in_with(lock):
    with lock:
        return _load("fs").inspect_roots(["c"])


def guessed(names):
    for name in names:
        _load(name).inspect_roots([])
