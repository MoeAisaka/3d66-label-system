@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0backend"
if not exist "..\.venv\Scripts\python.exe" (
  echo Python environment is missing. Please run setup first.
  pause
  exit /b 1
)
if not exist "..\frontend\dist\index.html" (
  echo Frontend build is missing. Please run setup first.
  pause
  exit /b 1
)
title 3d66 Label System
"..\.venv\Scripts\python.exe" -X utf8 -m app.launcher
if errorlevel 1 echo The service exited with an error. Please keep this window and send a screenshot.
pause
endlocal
