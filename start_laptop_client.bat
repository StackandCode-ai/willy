@echo off
title Willy - Laptop Worker Client
cd /d "%~dp0"
echo ===================================================
echo   WILLY LAPTOP CLIENT (Zero-Overhead Worker Node)
echo   Connecting to central Willy Server...
echo ===================================================
python -m willy.server.agent_bridge
pause
