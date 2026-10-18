@echo off
cd /d "%~dp0"
where py >nul 2>nul && (start "" pyw -3 app.py) || (start "" pythonw app.py)
