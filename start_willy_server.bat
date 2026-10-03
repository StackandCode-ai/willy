@echo off
title Willy - Central Cloud Server Relay
cd /d "%~dp0"
echo ===================================================
echo   WILLY CENTRAL CLOUD SERVER (Brain & Relay Hub)
echo   Listening on port 8000 (Call, Webhooks, Mobile)
echo ===================================================
uvicorn willy.server.relay:app --host 0.0.0.0 --port 8000
pause
