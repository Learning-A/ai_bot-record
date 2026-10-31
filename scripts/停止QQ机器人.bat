@echo off
title QQ AI Bot Stopper
echo === Stopping QQ AI Bot ===

rem --- 0. set watchdog stop-flag FIRST so it won't resurrect the bot ---
type nul > "%~dp0astrbot\WATCHDOG_STOP.flag" 2>nul

rem --- 1. stop NapCat headless QQ (found via its WebUI port :6099) ---
rem     NOTE: only kills the process listening on 6099 - your normal QQ
rem     desktop client (opened manually) is NOT touched.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":6099 " ^| findstr "LISTENING"') do (
    echo killing NapCat QQ process tree PID %%p
    taskkill /PID %%p /T /F >nul 2>&1
)
taskkill /IM NapCatWinBootMain.exe /F >nul 2>&1

rem --- 2. stop AstrBot (WebUI :6185 / OneBot WS :6199) ---
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":6185 " ^| findstr "LISTENING"') do (
    echo killing AstrBot PID %%p
    taskkill /PID %%p /T /F >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":6199 " ^| findstr "LISTENING"') do (
    taskkill /PID %%p /T /F >nul 2>&1
)
taskkill /IM astrbot.exe /F >nul 2>&1

echo.
echo Done. Bot fully stopped.
if not defined ZCODE_SESSION pause
