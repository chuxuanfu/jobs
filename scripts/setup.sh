#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "This one-click setup supports macOS only."
  exit 1
fi

find_python() {
  local candidate
  for candidate in "${PYTHON_BIN:-}" /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 "$(command -v python3.12 2>/dev/null || true)" "$(command -v python3 2>/dev/null || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' 2>/dev/null; then
      print -r -- "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON="$(find_python || true)"
if [[ -z "$PYTHON" ]]; then
  UV="$(command -v uv 2>/dev/null || true)"
  if [[ -z "$UV" ]]; then
    print "Installing the self-contained Python runtime manager..."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
      if [[ -x "$candidate" ]]; then
        UV="$candidate"
        break
      fi
    done
  fi
  if [[ -z "$UV" || ! -x "$UV" ]]; then
    print -u2 "Could not install the Python runtime automatically."
    exit 1
  fi
  "$UV" python install 3.12
  "$UV" venv --python 3.12 --clear "$ROOT/.venv"
else
  "$PYTHON" -m venv --clear "$ROOT/.venv"
fi

VENV_PYTHON="$ROOT/.venv/bin/python"
mkdir -p "$ROOT/data/databases" "$ROOT/source" "$ROOT/original" "$ROOT/results" "$ROOT/logs"

BACKUP_DIRECTORY="${BACKUP_DIRECTORY:-$HOME/JobsMonitorBackup}"
mkdir -p "$BACKUP_DIRECTORY"
"$VENV_PYTHON" -c 'from pathlib import Path; import json,sys; path=Path(sys.argv[1]); value=str(Path(sys.argv[2]).expanduser().resolve()); path.write_text("backup_directory = "+json.dumps(value)+"\n", encoding="utf-8")' "$ROOT/config/settings.local.toml" "$BACKUP_DIRECTORY"

env PYTHONPATH="$ROOT/src" "$VENV_PYTHON" -c '
from job_monitor.config import project_paths
from job_monitor.storage import CompanyDatabase
paths = project_paths()
for company in ("apple", "openai", "meta", "google", "broadcom", "nvidia"):
    CompanyDatabase(paths.databases / f"{company}_jobs.sqlite", paths.root / "migrations").migrate()
'

if [[ "${SETUP_SKIP_LAUNCHD:-0}" != "1" ]]; then
  "$ROOT/scripts/install-launchd.sh"
fi

print "Setup complete."
print "Project: $ROOT"
print "Daily schedule: 09:00 Mac local time"
print "Backup: $BACKUP_DIRECTORY"
print "The first launchd run starts immediately after installation."
