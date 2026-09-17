@echo off
setlocal
title NA70 WebUI
rem Resolve paths relative to this file, including paths containing spaces.
set "WEBUI_PYTHON=python"
if exist "%~dp0web\.venv\Scripts\python.exe" set "WEBUI_PYTHON=%~dp0web\.venv\Scripts\python.exe"

echo Starting NA70 WebUI...
echo The first launch installs dependencies and builds the frontend.
echo Keep this window open. Press Ctrl+C to stop the server.
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0web\start.ps1" -Python "%WEBUI_PYTHON%" %*
set "WEBUI_EXIT=%ERRORLEVEL%"
if "%WEBUI_EXIT%"=="0" exit /b 0
echo.
echo WebUI startup failed. See the error above.
echo Python 3.12 and Node.js 22.12+ are required.
pause
exit /b %WEBUI_EXIT%
