@echo off
REM OPTIONAL: virtual environment for development
cd /d "%~dp0"
py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
echo Virtual environment ready.
pause
