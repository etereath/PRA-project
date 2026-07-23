@echo off
setlocal

cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8765"
set "SKIP_ENV_CHECK="

if not "%~1"=="" set "HOST=%~1"
if not "%~2"=="" set "PORT=%~2"
if /I "%~1"=="--skip-env-check" (
    set "HOST=127.0.0.1"
    set "PORT=8765"
    set "SKIP_ENV_CHECK=-SkipEnvCheck"
)

where powershell >nul 2>nul
if errorlevel 1 (
    echo [ERROR] PowerShell is not available in PATH.
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not available in PATH.
    exit /b 1
)

echo Starting PRA web console at http://%HOST%:%PORT% ...
start "" "http://%HOST%:%PORT%/dashboard"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1" -HostName "%HOST%" -Port %PORT% %SKIP_ENV_CHECK%
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Web console exited with code %EXIT_CODE%.
)

exit /b %EXIT_CODE%
