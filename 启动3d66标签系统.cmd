@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoLogo -NoProfile -File "%~dp0scripts\windows\start.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
