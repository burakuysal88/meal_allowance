#!/bin/bash
cd "$(dirname "$0")"
echo "Installing required packages (first time only, may take a minute)..."
python3 -m pip install --quiet pandas matplotlib reportlab
echo ""
python3 generate_meal_report.py
