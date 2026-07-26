@echo off
echo Installing required packages (first time only, may take a minute)...
python -m pip install --quiet pandas matplotlib reportlab
if errorlevel 1 (
    py -m pip install --quiet pandas matplotlib reportlab
)
echo.
python "%~dp0generate_meal_report.py"
if errorlevel 1 (
    py "%~dp0generate_meal_report.py"
)
pause
