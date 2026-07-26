#!/bin/bash
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python was not found on this system."
    echo "Install it first, e.g.:"
    echo "  Debian/Ubuntu:   sudo apt install python3 python3-pip"
    echo "  Fedora:          sudo dnf install python3 python3-pip"
    echo "  Arch:            sudo pacman -S python python-pip"
    read -p "Press Enter to close..."
    exit 1
fi

echo "Installing required packages (first time only, may take a minute)..."
"$PY" -m pip install --quiet --user pandas matplotlib reportlab 2>/tmp/pip_err.$$
if [ $? -ne 0 ]; then
    if grep -qi "externally-managed-environment" /tmp/pip_err.$$ 2>/dev/null; then
        echo "This system restricts pip by default (PEP 668) - retrying with an override flag..."
        "$PY" -m pip install --quiet --user --break-system-packages pandas matplotlib reportlab
    fi
fi
rm -f /tmp/pip_err.$$

# Final check: can we actually import the packages now?
"$PY" -c "import pandas, matplotlib, reportlab" 2>/dev/null
if [ $? -ne 0 ]; then
    echo ""
    echo "Could not install the required packages automatically."
    echo "Ask a colleague with more Linux experience for a hand, or try"
    echo "manually in a terminal:"
    echo "  $PY -m pip install --user --break-system-packages pandas matplotlib reportlab"
    read -p "Press Enter to close..."
    exit 1
fi

echo ""
"$PY" generate_meal_report.py
