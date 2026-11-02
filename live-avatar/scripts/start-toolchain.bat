@echo off
setlocal enabledelayedexpansion
title DaFeiYu Live Toolchain
echo === DaFeiYu Live Toolchain ===
echo.

set "AVATARDIR=%~dp0..\avatar"

rem ---------- 1. core hub ----------
netstat -ano | findstr ":8643" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [1/4] core hub ............ already running [OK]
    goto :step2
)
echo [1/4] starting core hub...
start "danmaku-hub" /min python -u "%AVATARDIR%\danmaku_hub.py"
set /a tries=0
:waitport
"%SystemRoot%\System32\timeout.exe" /t 1 /nobreak >nul
netstat -ano | findstr ":8643" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto :hubok
set /a tries+=1
if %tries% lss 15 goto :waitport
echo [1/4] core hub ............ FAILED to listen on 8643 [XX]
echo        run manually to see the error:  python -u "%AVATARDIR%\danmaku_hub.py"
goto :step2
:hubok
echo [1/4] core hub ............ started [OK]

:step2
rem ---------- 2. danmaku adapter (detect by command line, not window title) ----------
powershell -NoProfile -Command "if (@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'danmaku_bilibili'}).Count -eq 0) { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [2/4] starting danmaku adapter ^(bilibili^)...
    start "danmaku-bilibili" /min python -u "%AVATARDIR%\danmaku_bilibili.py"
    "%SystemRoot%\System32\timeout.exe" /t 4 /nobreak >nul
    powershell -NoProfile -Command "if (@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'danmaku_bilibili'}).Count -eq 0) { exit 1 }" >nul 2>&1
    if errorlevel 1 (
        echo [2/4] danmaku adapter ... FAILED to start [XX]
        echo        run manually to see the error:  python -u "%AVATARDIR%\danmaku_bilibili.py"
    ) else (
        echo [2/4] danmaku adapter ... started [OK]
    )
) else (
    echo [2/4] danmaku adapter ... already running [OK]
)

:step3
rem ---------- 3. console (wait for http 200, then open; fallback to default browser) ----------
echo [3/4] opening console...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 15;$i++){ try{ $r=Invoke-WebRequest -Uri 'http://127.0.0.1:8642/console.html' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){$ok=$true; break} }catch{ Start-Sleep 1 } }; if(-not $ok){ exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [3/4] console ............. NOT reachable on :8642 [XX]
    goto :step4
)
start "" msedge --app=http://127.0.0.1:8642/console.html --window-size=1150,760 2>nul
if errorlevel 1 start "" http://127.0.0.1:8642/console.html
echo [3/4] console ............. opened [OK]

:step4
rem ---------- 4. streaming software (EDIT ME: OBS by default) ----------
tasklist /FI "IMAGENAME eq obs64.exe" 2>nul | findstr /I obs64 >nul
if errorlevel 1 (
    echo [4/4] starting OBS...
    start "" "C:\Program Files\obs-studio\bin\64bit\obs64.exe"
) else (
    echo [4/4] OBS ............... already running [OK]
)

echo.
echo ============================================
echo  Go live: streaming app "Start Live" + fresh key,
echo  then "Start Streaming" and MINIMIZE the app.
echo  Console should show TWO GREEN dots.
echo ============================================
"%SystemRoot%\System32\timeout.exe" /t 8
