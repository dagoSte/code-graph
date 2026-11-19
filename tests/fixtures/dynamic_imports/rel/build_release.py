import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(alias, name):
    spec = importlib.util.spec_from_file_location(alias, HERE / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build():
    policy = load("policy", "build-policy.py")
    return policy.recipe({"x": 1})


def _load(name):
    spec = importlib.util.spec_from_file_location("_r_" + name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check():
    return _load("fs").inspect_roots(["a"])


def direct():
    spec = importlib.util.spec_from_file_location("p2", HERE / "build-policy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.recipe({})
