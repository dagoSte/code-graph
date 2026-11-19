#!/usr/bin/env bash
set -euo pipefail
CG_SKILL_DIR="$(cd -- "$(dirname -- "$0")/.." && pwd)"
CG_PYTHON_BIN="${CG_PYTHON:-python3}"
if [[ -L "$CG_SKILL_DIR/.venv" ]]; then
  printf '%s\n' ".venv environment symlink refused." >&2
  exit 1
fi
"$CG_PYTHON_BIN" -I -m venv "$CG_SKILL_DIR/.venv"
"$CG_SKILL_DIR/.venv/bin/python" -I -m pip install --only-binary=:all: -r "$CG_SKILL_DIR/requirements.txt"
chmod +x "$CG_SKILL_DIR/scripts/cg"
"$CG_SKILL_DIR/scripts/cg" selftest
