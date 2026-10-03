@echo off
title Willy - Remote Gateway & WebSocket Bridge
cd /d "%~dp0\.."
echo Starting Willy Remote Gateway for Siri and Cloud Relay...
python -m willy.server.runner
pause
