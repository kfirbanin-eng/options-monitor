@echo off
rem One-time setup: installs the Python packages the monitor needs.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python is not installed. Get it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" on the first installer screen.
  pause
  exit /b 1
)
%PY% -m pip install -r requirements.txt plyer
echo.
echo Done. Double-click demo.bat to check everything works.
pause
