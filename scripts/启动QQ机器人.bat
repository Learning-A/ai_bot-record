@echo off
title QQ AI Bot Launcher
echo === QQ AI Bot (NapCat + AstrBot + DeepSeek) ===
echo.

rem --- 0. ffmpeg must be in PATH for voice silk conversion ---
set "PATH=C:\Users\<Windows用户名>\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin;%PATH%"

rem --- 0b. clear watchdog stop-flag + start watchdog if not running (pid-file based) ---
if exist "%~dp0astrbot\WATCHDOG_STOP.flag" del "%~dp0astrbot\WATCHDOG_STOP.flag" >nul 2>&1
powershell -NoProfile -Command "$running=$false; $pf='%~dp0astrbot\astrbot_watchdog.pid'; if (Test-Path $pf) { $wp=[int](Get-Content $pf); $proc=Get-CimInstance Win32_Process -Filter \"ProcessId=$wp\" -ErrorAction SilentlyContinue; if ($proc -and $proc.CommandLine -match 'watchdog') { $running=$true } }; if (-not $running) { Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0astrbot_watchdog.ps1'; 'watchdog started' } else { 'watchdog already running' }"

rem --- 1. AstrBot (WebUI :6185, OneBot WS server :6199) ---
netstat -ano | findstr /C:":6185 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [1/3] starting AstrBot...
    pushd "%~dp0astrbot"
    start "astrbot" /min cmd /c "C:\Users\<Windows用户名>\.local\bin\astrbot.exe run >> astrbot_run.log 2>&1"
    popd
    ping -n 7 127.0.0.1 >nul
) else (
    echo [1/3] AstrBot already running
)

rem --- 2. NapCat (elevated window: QR login + WebUI :6099) ---
netstat -ano | findstr /C:":6099 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [2/3] starting NapCat...  ^(UAC prompt expected, click YES^)
    powershell -NoProfile -Command "Start-Process -FilePath '%~dp0NapCat.Shell\launcher.bat' -Verb RunAs"
) else (
    echo [2/3] NapCat already running
)

rem --- 3. open AstrBot WebUI ---
echo [3/3] opening AstrBot WebUI...
start "" "http://localhost:6185"

echo.
echo NEXT STEPS (first run only - see the .md manual in this folder):
echo   1. NapCat window: scan QR code with phone QQ of account m_meng
echo   2. NapCat WebUI http://127.0.0.1:6099 - add Reverse WebSocket:
echo      ws://127.0.0.1:6199/ws
echo   3. Done - DeepSeek key is already configured
echo.
echo Daily use: private chat m_meng directly; send /help for commands.
echo Stop with the stop bat in this folder.
echo.
if not defined ZCODE_SESSION pause
