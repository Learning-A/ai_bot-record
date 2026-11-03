@echo off
title Restart Core Hub
setlocal enabledelayedexpansion

rem 定位形象目录（兼容 直播形象/ 与 avatar/ 两种布局）
set "AVATARDIR=%~dp0..\直播形象"
if not exist "%AVATARDIR%\danmaku_hub.py" set "AVATARDIR=%~dp0..\avatar"
if not exist "%AVATARDIR%\danmaku_hub.py" (
    echo [XX] 找不到 danmaku_hub.py
    pause & exit /b 1
)

echo [1/3] 停止旧核心...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'danmaku_hub'} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
"%SystemRoot%\System32\timeout.exe" /t 2 /nobreak >nul

echo [2/3] 启动新核心...
start "danmaku-hub" /min python -u "%AVATARDIR%\danmaku_hub.py"

set /a tries=0
:waitport
"%SystemRoot%\System32\timeout.exe" /t 1 /nobreak >nul
netstat -ano | findstr ":8643" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto :ok
set /a tries+=1
if %tries% lss 15 goto :waitport
echo [3/3] [XX] 核心启动失败，手动运行看报错: python -u "%AVATARDIR%\danmaku_hub.py"
pause & exit /b 1
:ok
echo [3/3] [OK] 核心已重启（端口 8643 就绪）
echo.
echo 提示：核心重启后，适配器会在几秒内自动重连；监控台刷新页面即可。
pause
