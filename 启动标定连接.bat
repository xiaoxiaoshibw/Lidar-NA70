@echo off
setlocal
title NA70 capture connection
echo Connecting to the Jetson capture service...
echo Start the NA70 WebUI separately if it is not already running.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start_capture_tunnel.ps1"
if errorlevel 1 (
  echo Connection failed. See the message above.
  pause
)
