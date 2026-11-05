@echo off
setlocal
title NA70 + IMU 60 second capture
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\capture_local_60s.ps1"
echo.
echo Press any key to close this window.
pause >nul
