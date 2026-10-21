#!/bin/bash
# Stop DC Digs and remove it from your login items. The folder with your inbox and
# settings stays; delete it yourself if you want everything gone.
set -u
LABEL="local.dc-digs.web"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Stopped DC Digs."
rm -f "$AGENT"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
echo "DC Digs won't start at login any more."
echo "Your inbox and settings are still in $ROOT (data/ and criteria.yaml)."
