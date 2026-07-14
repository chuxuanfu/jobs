#!/bin/zsh
set -eu

ROOT="${0:A:h:h}"
PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
PYTHONPATH="$ROOT/src" PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/jobs-monitor-pycache" \
  "$PYTHON" -m unittest discover -s "$ROOT/tests" -v
