@echo off
cd /d "%~dp0"
where py >nul 2>nul || (echo Python not found. Install Python 3 from python.org and tick Add to PATH. & pause & exit /b 1)
py -3 native_host\install_host.py
echo.
pause
