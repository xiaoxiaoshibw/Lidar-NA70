@echo off
setlocal
title NA70 IMU Qt Recorder
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start_na70_qt.ps1" %*
set "QT_CAPTURE_EXIT=%ERRORLEVEL%"
if "%QT_CAPTURE_EXIT%"=="0" exit /b 0
echo Qt recorder failed. See the error above.
pause
exit /b %QT_CAPTURE_EXIT%
