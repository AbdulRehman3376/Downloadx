@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo       DownloadX - Build Executables
echo ========================================
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python launcher ^(py^) was not found.
    echo Install Python 3 and try again.
    pause
    exit /b 1
)

echo [1/4] Installing build dependencies...
py -3 -m pip install -r requirements-dev.txt
if errorlevel 1 goto :error

echo.
echo [2/4] Building DownloadX native host...
py -3 -m PyInstaller --noconfirm --clean --onefile --name downloadx_host --distpath "%~dp0native_host" --workpath "%~dp0build\host" --specpath "%~dp0build" "%~dp0native_host\downloadx_host.py"
if errorlevel 1 goto :error

echo.
echo [3/4] Building DownloadX desktop application...
py -3 -m PyInstaller --noconfirm --clean --windowed --name DownloadX --distpath "%~dp0dist" --workpath "%~dp0build\app" --specpath "%~dp0build" "%~dp0app.py"
if errorlevel 1 goto :error

echo.
echo [4/4] Build completed.
echo.
echo Desktop app:
echo %~dp0dist\DownloadX\DownloadX.exe
echo.
echo Native host:
echo %~dp0native_host\downloadx_host.exe
echo.
echo IMPORTANT:
echo Check your native host manifest points to the EXE above.
echo Reinstall or update the native host manifest if its path changed.
echo.
pause
exit /b 0

:error
echo.
echo ERROR: Build failed. Read the error messages above.
pause
exit /b 1
