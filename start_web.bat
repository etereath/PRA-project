@echo off
setlocal

cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8765"
set "CHECK_ONLY="

if /I "%~1"=="--check" set "CHECK_ONLY=1"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not available in PATH.
    exit /b 1
)

echo [1/3] Installing project dependencies...
python -m pip install -e .
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    exit /b 1
)

echo [2/3] Generating sample workbooks...
python scripts\create_sample_workbooks.py
if errorlevel 1 (
    echo [ERROR] Failed to generate sample workbooks.
    exit /b 1
)

if defined CHECK_ONLY (
    echo [3/3] Check complete. Environment looks good.
    exit /b 0
)

echo [3/3] Starting web console at http://%HOST%:%PORT% ...
start "" "http://%HOST%:%PORT%/"
python -m app.cli serve-web --host %HOST% --port %PORT%

endlocal
