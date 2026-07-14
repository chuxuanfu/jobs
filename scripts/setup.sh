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

BACKUP_DIRECTORY="${BACKUP_DIRECTORY:-}"
if [[ -t 0 && -z "$BACKUP_DIRECTORY" ]]; then
  print -n "Backup directory (blank disables backup; editable later in config/settings.toml): "
  read -r BACKUP_DIRECTORY
fi
if [[ -n "$BACKUP_DIRECTORY" ]]; then
  "$PYTHON_BIN" -c 'from pathlib import Path; import json,sys; path=Path(sys.argv[1]); value=str(Path(sys.argv[2]).expanduser()); text=path.read_text(); import re; text=re.sub(r"^backup_directory\s*=.*$", "backup_directory = "+json.dumps(value), text, flags=re.M); path.write_text(text)' "$ROOT/config/settings.toml" "$BACKUP_DIRECTORY"
fi

if [[ -t 0 ]]; then
  print -n "Install daily 09:00 launchd schedule now? [Y/n]: "
  read -r INSTALL_SCHEDULE
  if [[ "${INSTALL_SCHEDULE:-Y}" != [nN]* ]]; then
    "$ROOT/scripts/install-launchd.sh"
  fi
fi

print "Installed. Run: $ROOT/scripts/jobs-monitor run --company openai --dry-run"
print "Backup path: $ROOT/config/settings.toml"
