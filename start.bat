@echo off
rem Live monitoring of trades\live.json with Yahoo prices and the model option price.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
if not exist trades\live.json (
  echo trades\live.json not found. Create it first - see "Live day" in README.md.
  pause
  exit /b 1
)
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8765"
%PY% monitor.py trades\live.json --feed yahoo %*
pause
