@echo off
title Willy - Voice-Driven Windows Assistant
color 0b
cd /d "%~dp0"

:menu
cls
echo ======================================================================
echo           WILLY: Autonomous Voice-Driven Windows Controller           
echo ======================================================================
echo.
echo   [1] Start Desktop Voice UI (Floating Widget with Waveform)
echo   [2] Start Voice Assistant (Headless Console Daemon)
echo   [3] Start Remote Bridge (Siri, Phone & Cloud Relay)
echo   [4] Start Interactive Text Mode (Testing without Mic)
echo   [5] Enable Auto-Start on Windows Boot (Starts Willy Automatically)
echo   [6] Disable Auto-Start on Windows Boot
echo   [7] Check Auto-Start Status
echo   [8] Run System Verification Tests
echo   [9] Exit
echo.
echo ======================================================================
set /p choice="Select an option (1-9): "

if "%choice%"=="1" goto opt_ui
if "%choice%"=="2" goto opt_voice
if "%choice%"=="3" goto opt_remote
if "%choice%"=="4" goto opt_text
if "%choice%"=="5" goto opt_autostart_enable
if "%choice%"=="6" goto opt_autostart_disable
if "%choice%"=="7" goto opt_autostart_status
if "%choice%"=="8" goto opt_tests
if "%choice%"=="9" exit /b
goto menu

:opt_ui
call scripts\run_ui.bat
goto menu

:opt_voice
call scripts\run_voice.bat
goto menu

:opt_remote
call scripts\run_remote.bat
goto menu

:opt_text
call scripts\run_text.bat
goto menu

:opt_autostart_enable
call scripts\enable_autostart.bat
goto menu

:opt_autostart_disable
call scripts\disable_autostart.bat
goto menu

:opt_autostart_status
call scripts\autostart_status.bat
goto menu

:opt_tests
python tests\test_willy.py
pause
goto menu
