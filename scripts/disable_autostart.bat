@echo off
title Willy - Disable Windows Auto-Start
cd /d "%~dp0\.."
echo Disabling Willy Auto-Start on Windows boot...
python main.py --disable-autostart
echo.
pause
