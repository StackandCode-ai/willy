@echo off
title Willy - Windows Auto-Start Status
cd /d "%~dp0\.."
echo Checking Willy Windows Auto-Start Status...
python main.py --autostart-status
echo.
pause
