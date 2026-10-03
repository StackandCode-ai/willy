@echo off
title Willy - Enable Windows Auto-Start
cd /d "%~dp0\.."
echo Enabling Willy Auto-Start on Windows boot...
python main.py --enable-autostart ui
echo.
pause
