@echo off
title Willy - Voice Daemon
cd /d "%~dp0\.."
echo Starting Willy Console Voice Daemon...
python main.py --mode voice
pause
