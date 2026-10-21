#!/bin/bash
# Install DC Digs on a Mac: a Python environment for the app, and a login item that
# keeps it running in the background at http://localhost:8765. Run it again any time
# to repair or update; your inbox and settings are kept.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="local.dc-digs.web"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="$(grep -E '^DIG_PORT=' "$ROOT/.env" 2>/dev/null | cut -d= -f2 || true)"
PORT="${PORT:-8765}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# macOS keeps background apps out of these folders, so the app couldn't start there.
case "$ROOT" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
    say "Please move the DC Digs folder first."
    echo "It's in $ROOT, and macOS doesn't let background apps read Documents, Desktop or Downloads."
    echo "Move the whole folder to your home folder ($HOME), then run this installer again."
    exit 1 ;;
esac

say "1/4  Looking for Python 3.10 or newer…"
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  say "Python 3.10 or newer is needed."
  echo "Download it from https://www.python.org/downloads/macos/ , install it, then run this installer again."
  open "https://www.python.org/downloads/macos/" 2>/dev/null || true
  exit 1
fi
echo "Using $PY ($("$PY" --version))"

say "2/4  Setting up the app (this takes a minute or two the first time)…"
"$PY" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip
"$ROOT/.venv/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt"
mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"

say "3/4  Starting DC Digs in the background (and at every login)…"
"$ROOT/.venv/bin/python" - "$ROOT" "$LABEL" "$AGENT" <<'PY'
import plistlib, sys
root, label, agent = sys.argv[1:4]
plistlib.dump({
    "Label": label,
    "ProgramArguments": [f"{root}/.venv/bin/python", "-m", "dig.web"],
    "WorkingDirectory": root,
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "StandardOutPath": f"{root}/logs/web.log",
    "StandardErrorPath": f"{root}/logs/web.log",
}, open(agent, "wb"))
PY
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENT"

say "4/4  Waiting for it to come up…"
for _ in $(seq 1 30); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
    echo "DC Digs is running at http://localhost:$PORT"
    open "http://localhost:$PORT/"
    exit 0
  fi
  sleep 1
done
say "DC Digs didn't answer yet."
echo "Have a look at $ROOT/logs/web.log, or open http://localhost:$PORT in a minute."
exit 1
