@echo off
title Restart Jukebox Adapter
setlocal enabledelayedexpansion

set "AVATARDIR=%~dp0..\直播形象"
if not exist "%AVATARDIR%\jukebox_bilibili.py" set "AVATARDIR=%~dp0..\avatar"
if not exist "%AVATARDIR%\jukebox_bilibili.py" (
    echo [XX] 找不到 jukebox_bilibili.py
    pause & exit /b 1
)
set "LOGDIR=%~dp0.."

echo [1/3] 停止旧点歌适配器...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'jukebox_bilibili'} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
"%SystemRoot%\System32\timeout.exe" /t 2 /nobreak >nul

echo [2/3] 启动新点歌适配器（日志: %LOGDIR%\_jukebox.log）...
powershell -NoProfile -Command "Start-Process python -ArgumentList '-u','\"%AVATARDIR%\jukebox_bilibili.py\"' -WorkingDirectory '%AVATARDIR%' -RedirectStandardOutput '%LOGDIR%\_jukebox.log' -RedirectStandardError '%LOGDIR%\_jukebox.err' -WindowStyle Hidden"
"%SystemRoot%\System32\timeout.exe" /t 5 /nobreak >nul

echo [3/3] 验证...
powershell -NoProfile -Command "if (@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'jukebox_bilibili'}).Count -eq 0) { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [XX] 点歌适配器启动失败，看日志: %LOGDIR%\_jukebox.err
) else (
    echo [OK] 点歌适配器已重启（弹幕发「点歌 歌名」即可测试；点歌台页面无需重启）
)
echo.
echo 提示：点歌台页面不用动，它会自动重连。若页面显示掉线，F5 刷新一次。
pause
