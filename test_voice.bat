@echo off
rem Plays the 15-minute reminder once, so you can hear the voice.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% -c "import config, notifier; notifier.Notifier().speak(config.REMINDER_TEXT)"
echo Playing... (takes about 15 seconds)
timeout /t 15 >nul
