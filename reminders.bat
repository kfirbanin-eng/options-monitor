@echo off
rem Mindset reminders for the whole session, spoken out loud on this PC (and sent to the phone if NTFY_TOPIC is set).
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% tools\reminders.py --speak
pause
