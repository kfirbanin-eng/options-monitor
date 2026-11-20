@echo off
rem Replays the real 2026-11-19 SPY 734P day - works any time, no keys, no market hours.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8765"
%PY% monitor.py trades\replay_spy_734p_2026-11-19.json --feed replay --data data\spy_2026-11-19.json
pause
