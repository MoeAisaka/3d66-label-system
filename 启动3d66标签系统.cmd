@echo off
chcp 65001 >nul
cd /d "%~dp0backend"
if not exist "..\.venv\Scripts\python.exe" (
  echo 尚未完成安装，请先双击“首次安装.cmd”。
  pause
  exit /b 1
)
if not exist "..\frontend\dist\index.html" (
  echo 前端尚未构建，请先双击“首次安装.cmd”。
  pause
  exit /b 1
)
title 3d66 标签系统
"..\.venv\Scripts\python.exe" -X utf8 -m app.launcher
pause

