@echo off
echo.
echo  ==============================
echo   EVE ONLINE MARKET DASHBOARD
echo  ==============================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11+ from python.org
    pause
    exit /b 1
)

REM Install dependencies if needed
if not exist ".deps_installed" (
    echo Installing Python dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
    echo. > .deps_installed
)

echo Starting dashboard at http://localhost:8501
echo Press Ctrl+C to stop.
echo.

python -m streamlit run app.py --server.port 8501 --server.headless false --browser.gatherUsageStats false
pause
