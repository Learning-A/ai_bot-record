@echo off
title QQ AI Bot Launcher
echo === QQ AI Bot (NapCat + AstrBot + DeepSeek) ===
echo.

rem --- 0. ffmpeg must be in PATH for voice silk conversion ---

set "PATH=C:/Users/<你>\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin;%PATH%"



rem --- 1. AstrBot (WebUI :6185, OneBot WS server :6199) ---
netstat -ano | findstr /C:":6185 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [1/3] starting AstrBot...
    pushd "%~dp0astrbot"
    start "astrbot" /min cmd /c "C:/Users/<你>\.local\bin\astrbot.exe run >> astrbot_run.log 2>&1"
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
echo   1. NapCat window: scan QR code with phone QQ of account <机器人QQ昵称>
echo   2. NapCat WebUI http://127.0.0.1:6099 - add Reverse WebSocket:
echo      ws://127.0.0.1:6199/ws
echo   3. Done - DeepSeek key is already configured
echo.
echo Daily use: private chat <机器人QQ昵称> directly; send /help for commands.
echo Stop with the stop bat in this folder.
echo.
if not defined ZCODE_SESSION pause
