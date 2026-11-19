"""Benchmark ablation: expose only the existing confined source reader over MCP."""
import argparse
import importlib.util
from pathlib import Path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-root', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    skill, root = args.skill_root.resolve(strict=True), args.root.resolve(strict=True)
    engine = load(skill / 'scripts/cg.py', 'cg_source_only_engine')
    server = load(skill / 'scripts/mcp_server.py', 'cg_source_only_server')
    server.SPECS = {'cg_read': server.SPECS['cg_read']}
    server.serve(engine, str(root))


if __name__ == '__main__':
    main()
