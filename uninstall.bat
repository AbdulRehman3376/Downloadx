@echo off
cd /d "%~dp0"
py -3 native_host\install_host.py --uninstall
pause
