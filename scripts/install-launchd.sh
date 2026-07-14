#!/bin/zsh
set -eu

ROOT="${0:A:h:h}"
LABEL="com.local.jobs-monitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  print -u2 "Run $ROOT/setup.sh first."
  exit 1
fi

"$PYTHON" -c 'import plistlib,sys; root,label,output=sys.argv[1:]; data={"Label":label,"ProgramArguments":[str(__import__("pathlib").Path(root)/"scripts"/"scheduled-run")],"WorkingDirectory":root,"StartCalendarInterval":{"Hour":9,"Minute":0},"RunAtLoad":True,"StandardOutPath":str(__import__("pathlib").Path(root)/"logs"/"launchd.stdout.log"),"StandardErrorPath":str(__import__("pathlib").Path(root)/"logs"/"launchd.stderr.log")}; plistlib.dump(data,open(output,"wb"))' "$ROOT" "$LABEL" "$PLIST"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
print "Installed daily 09:00 launchd job: $PLIST"
print "launchd uses the Mac system timezone and starts the first run now."
