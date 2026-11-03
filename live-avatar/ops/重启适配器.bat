@echo off
title Restart Danmaku Adapter
setlocal enabledelayedexpansion

set "AVATARDIR=%~dp0..\直播形象"
if not exist "%AVATARDIR%\danmaku_bilibili.py" set "AVATARDIR=%~dp0..\avatar"
if not exist "%AVATARDIR%\danmaku_bilibili.py" (
    echo [XX] 找不到 danmaku_bilibili.py
    pause & exit /b 1
)

echo [1/3] 停止旧适配器...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'danmaku_bilibili'} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
"%SystemRoot%\System32\timeout.exe" /t 2 /nobreak >nul

echo [2/3] 启动新适配器...
start "danmaku-bilibili" /min python -u "%AVATARDIR%\danmaku_bilibili.py"
"%SystemRoot%\System32\timeout.exe" /t 6 /nobreak >nul

echo [3/3] 验证...
powershell -NoProfile -Command "if (@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'danmaku_bilibili'}).Count -eq 0) { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [XX] 适配器启动失败，手动运行看报错: python -u "%AVATARDIR%\danmaku_bilibili.py"
) else (
    echo [OK] 适配器已重启（连B站约需2-5秒，跑 检查.bat 确认）
)
echo.
echo 提示：若反复连不上B站，先确认 Bili Live Hime 登录态正常（适配器读它的凭据）。
pause
