
@echo off
setlocal
cd /d "%~dp0"

set "HOST=%~dp0downloadx_host.exe"
set "PYTHON_SCRIPT=%~dp0downloadx_host.py"

if exist "%HOST%" (
    "%HOST%"
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%PYTHON_SCRIPT%"
    exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if not errorlevel 1 (
    python "%PYTHON_SCRIPT%"
    exit /b %ERRORLEVEL%
)

echo ERROR: DownloadX host executable or Python was not found.
pause
exit /b 1
