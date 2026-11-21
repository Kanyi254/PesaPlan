@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo PesaPlan Windows build
echo ========================================

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python Launcher was not found.
  echo Install Python 3.12 64-bit from python.org and enable the Python Launcher.
  pause
  exit /b 1
)

py -3.12 --version
if errorlevel 1 (
  echo ERROR: Python 3.12 is not installed.
  pause
  exit /b 1
)

if not exist .venv-win\Scripts\python.exe (
  py -3.12 -m venv .venv-win
  if errorlevel 1 goto :failed
)

call .venv-win\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :failed
python -m pip install -r requirements-windows.txt
if errorlevel 1 goto :failed

python make_icon.py
if errorlevel 1 goto :failed

pyinstaller --clean --noconfirm PesaPlan.spec
if errorlevel 1 goto :failed

echo.
echo BUILD COMPLETE.
echo Test: dist\PesaPlan\PesaPlan.exe
echo Next: compile PesaPlan.iss with Inno Setup to create the installer.
pause
exit /b 0

:failed
echo.
echo BUILD FAILED. Copy the full error message before closing this window.
pause
exit /b 1
