#!/bin/sh
# PrintTwin (macOS / Linux): start the bridge, the viewer opens in your browser.
# Needs Python 3.11 or newer - nothing else to install.
cd "$(dirname "$0")" || exit 1
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "PrintTwin needs Python 3.11 or newer (python.org or your package manager)."
    exit 1
fi
exec "$PY" -u run.py --open "$@"
