#!/bin/zsh
set -eu

ROOT="${0:A:h:h}"
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/bin/python3.12}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3.12 || command -v python3)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  print -u2 "Python 3.12 or newer is required."
  exit 1
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'
"$PYTHON_BIN" -m venv "$ROOT/.venv"

mkdir -p "$ROOT/data/databases" "$ROOT/source" "$ROOT/original" "$ROOT/results" "$ROOT/logs"
print "Installed. Run: $ROOT/scripts/jobs-monitor run --company openai --dry-run"
print "Backup path can be edited in: $ROOT/config/settings.toml"
