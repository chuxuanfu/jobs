#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  print -u2 "Run $ROOT/setup.sh before creating a transfer bundle."
  exit 1
fi

DESTINATION="${1:-$HOME/Desktop}"
STAMP="$(date +%Y%m%d-%H%M%S)"
STAGING="${TMPDIR:-/tmp}/jobs-transfer-$STAMP"
ARCHIVE="$DESTINATION/jobs-transfer-$STAMP.zip"
mkdir -p "$DESTINATION" "$STAGING"

env PYTHONPATH="$ROOT/src" "$PYTHON" -c 'from pathlib import Path; import sys; from job_monitor.backup import backup_project; print(backup_project(Path(sys.argv[1]), sys.argv[2])["destination"])' "$ROOT" "$STAGING"
ditto -c -k --norsrc --keepParent "$STAGING/local-job-monitor-backup" "$ARCHIVE"
rm -rf "$STAGING"
print "AirDrop this file: $ARCHIVE"
print "On the new Mac, unzip it and run setup.sh inside the folder."
