#!/bin/sh
# PB-1000 Tools Launcher - macOS / Linux
#
# Run from a terminal:  ./launcher.sh
# (Some Linux file managers won't run a .sh on double-click unless it's
# marked executable and "Run" is chosen instead of "Open"/"Display".)

cd "$(dirname "$0")" || exit 1

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Python 3 was not found on PATH."
    echo "  macOS:  brew install python-tk   (or install from python.org)"
    echo "  Linux:  sudo apt install python3-tk   (Debian/Ubuntu)"
    exit 1
fi

if ! "$PYTHON" -c "import tkinter" >/dev/null 2>&1; then
    echo "tkinter is not available for $PYTHON."
    echo "  macOS:  brew install python-tk"
    echo "  Linux:  sudo apt install python3-tk   (Debian/Ubuntu)"
    echo "          sudo dnf install python3-tkinter   (Fedora)"
    exit 1
fi

exec "$PYTHON" launcher.py
