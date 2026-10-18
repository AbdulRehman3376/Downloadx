@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo          DownloadX Release Builder
echo ============================================

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Project virtual environment not found.
    exit /b 1
)
call ".venv\Scripts\activate.bat"
python -m PyInstaller --clean --noconfirm DownloadX.spec
if errorlevel 1 exit /b 1

python -m PyInstaller --clean --noconfirm downloadx_host.spec
if errorlevel 1 exit /b 1

if not exist "dist\DownloadX\DownloadX.exe" (
    echo ERROR: Desktop executable was not created.
    exit /b 1
)
if not exist "dist\downloadx_host.exe" (
    echo ERROR: Native host executable was not created.
    exit /b 1
)

where ISCC.exe >nul 2>nul
if errorlevel 1 (
    echo.
    echo Desktop app and host built successfully.
    echo Install Inno Setup 6, then run:
    echo ISCC.exe DownloadX-Setup.iss
    exit /b 0
)

ISCC.exe DownloadX-Setup.iss
if errorlevel 1 exit /b 1

echo.
echo SUCCESS: Check build_output\DownloadX-Setup.exe
