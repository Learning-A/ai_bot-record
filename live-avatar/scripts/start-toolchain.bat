@echo off
title DaFeiYu Live Avatar
echo === DaFeiYu Live Avatar Toolchain ===
echo.

rem --- 1. core hub (skip if port 8643 listening) ---
netstat -ano | findstr ":8643" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [1/4] starting core hub...
    start "danmaku-hub" /min python -u "%~dp0..\avatar\danmaku_hub.py"
    "%SystemRoot%\System32\timeout.exe" /t 3 /nobreak >nul
) else (
    echo [1/4] core hub already running
)

rem --- 2. danmaku adapter (bilibili; edit for other platforms) ---
tasklist /FI "WINDOWTITLE eq danmaku-bilibili" 2>nul | findstr /I python >nul
if errorlevel 1 (
    echo [2/4] starting danmaku adapter (bilibili)...
    start "danmaku-bilibili" /min python -u "%~dp0..\avatar\danmaku_bilibili.py"
    "%SystemRoot%\System32\timeout.exe" /t 3 /nobreak >nul
) else (
    echo [2/4] danmaku adapter already running
)

rem --- 3. console ---
echo [3/4] opening console...
start "" msedge --app=http://127.0.0.1:8642/console.html --window-size=1150,760

rem --- 4. streaming software (EDIT ME: OBS by default) ---
tasklist /FI "IMAGENAME eq obs64.exe" 2>nul | findstr /I obs64 >nul
if errorlevel 1 (
    echo [4/4] starting OBS...
    start "" "C:\Program Files\obs-studio\bin\64bit\obs64.exe"
) else (
    echo [4/4] OBS already running
)

echo.
echo READY - see README.md / docs for details. MINIMIZE OBS when streaming.
pause
