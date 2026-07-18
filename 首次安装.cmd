@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 3d66 标签系统 - 首次安装

where py >nul 2>nul
if errorlevel 1 (
  echo 未检测到 Python。请先安装 Python 3.11 或 3.12，并勾选 Add Python to PATH。
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo 未检测到 Node.js。请先安装 Node.js LTS。
  pause
  exit /b 1
)

echo [1/4] 创建 Python 运行环境...
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto :failed

echo [2/4] 安装后端依赖...
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto :failed

echo [3/4] 安装前端依赖...
pushd frontend
call npm install
if errorlevel 1 goto :failed_frontend

echo [4/4] 构建网站...
call npm run build
if errorlevel 1 goto :failed_frontend
popd

echo.
echo 安装完成。以后双击“启动3d66标签系统.cmd”即可。
pause
exit /b 0

:failed_frontend
popd
:failed
echo.
echo 安装未完成，请保留本窗口中的错误信息。
pause
exit /b 1

