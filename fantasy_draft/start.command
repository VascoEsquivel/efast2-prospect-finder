#!/usr/bin/env bash
# macOS: double-click this file. Linux: run ./start.command
# Sets everything up on the first run, then opens the draft assistant in your browser.
cd "$(dirname "$0")" || exit 1
for py in python3.13 python3.12 python3.11 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    exec "$py" start.py "$@"
  fi
done
echo "Python 3.11 or newer is required. Install it from https://www.python.org/downloads/ and try again."
read -r -p "Press Enter to close."
