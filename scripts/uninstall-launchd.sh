#!/bin/zsh
set -eu

LABEL="com.local.jobs-monitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
if [[ -f "$PLIST" ]]; then
  rm "$PLIST"
fi
print "Uninstalled $LABEL"
