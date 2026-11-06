@echo off
setlocal enabledelayedexpansion
title DaFeiYu Live Toolchain
echo === DaFeiYu Live Toolchain ===
echo.

set "AVATARDIR=%~dp0..\avatar"

rem ---------- 1. core hub ----------
netstat -ano | findstr ":8643" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [1/5] core hub ............ already running [OK]
    goto :step2
)
echo [1/5] starting core hub...
start "danmaku-hub" /min python -u "%AVATARDIR%\danmaku_hub.py"
set /a tries=0
:waitport
"%SystemRoot%\System32\timeout.exe" /t 1 /nobreak >nul
netstat -ano | findstr ":8643" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto :hubok
set /a tries+=1
if %tries% lss 15 goto :waitport
echo [1/5] core hub ............ FAILED to listen on 8643 [XX]
echo        run manually to see the error:  python -u "%AVATARDIR%\danmaku_hub.py"
goto :step2
:hubok
echo [1/5] core hub ............ started [OK]

:step2
rem ---------- 2. danmaku adapter (HEALTH-AWARE: ask the hub, not just process) ----------
rem A running process does NOT mean the source is online (a stale adapter can sit
rem with expired creds forever). The hub's /api/status is the source of truth.
rem If offline: start adapter, poll up to 39s; still offline: kill all adapters
rem and restart once (picks up fresh Bili Live Hime credentials), poll 30s more.
powershell -NoProfile -Command "try{ (Invoke-WebRequest -Uri 'http://127.0.0.1:8642/api/status' -UseBasicParsing -TimeoutSec 3).Content -match '\"danmaku_online\": true' }catch{ exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo [2/5] danmaku adapter ... ONLINE [OK]
    goto :step3
)
echo [2/5] danmaku adapter offline or absent, starting...
start "danmaku-bilibili" /min python -u "%AVATARDIR%\danmaku_bilibili.py"
set /a tries=0
:dmwait
"%SystemRoot%\System32\timeout.exe" /t 3 /nobreak >nul
set /a tries+=1
powershell -NoProfile -Command "try{ (Invoke-WebRequest -Uri 'http://127.0.0.1:8642/api/status' -UseBasicParsing -TimeoutSec 3).Content -match '\"danmaku_online\": true' }catch{ exit 1 }" >nul 2>&1
if not errorlevel 1 goto :dmok
if %tries% lss 13 goto :dmwait
echo [2/5] still offline after 39s, killing stale adapter and retrying once...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'danmaku_bilibili'} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
"%SystemRoot%\System32\timeout.exe" /t 2 /nobreak >nul
start "danmaku-bilibili" /min python -u "%AVATARDIR%\danmaku_bilibili.py"
set /a tries=0
:dmwait2
"%SystemRoot%\System32\timeout.exe" /t 3 /nobreak >nul
set /a tries+=1
powershell -NoProfile -Command "try{ (Invoke-WebRequest -Uri 'http://127.0.0.1:8642/api/status' -UseBasicParsing -TimeoutSec 3).Content -match '\"danmaku_online\": true' }catch{ exit 1 }" >nul 2>&1
if not errorlevel 1 goto :dmok
if %tries% lss 10 goto :dmwait2
echo [2/5] danmaku adapter ... STILL OFFLINE [XX]
echo        checklist:
echo        1. Bili Live Hime installed AND logged in today?
echo        2. run manually to see the error:  python -u "%AVATARDIR%\danmaku_bilibili.py"
goto :step3
:dmok
echo [2/5] danmaku adapter ... ONLINE [OK]

:step3
rem ---------- 3. jukebox (danmaku song requests) ----------
powershell -NoProfile -Command "if (@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'jukebox_bilibili'}).Count -eq 0) { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [3/5] starting jukebox adapter ^(song requests^)...
    start "jukebox-bilibili" /min python -u "%AVATARDIR%\jukebox_bilibili.py"
    "%SystemRoot%\System32\timeout.exe" /t 3 /nobreak >nul
) else (
    echo [3/5] jukebox ............ already running [OK]
)

rem ---------- 4. console (wait for http 200, then open; fallback to default browser) ----------
echo [4/5] opening console...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 15;$i++){ try{ $r=Invoke-WebRequest -Uri 'http://127.0.0.1:8642/console.html' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){$ok=$true; break} }catch{ Start-Sleep 1 } }; if(-not $ok){ exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [4/5] console ............. NOT reachable on :8642 [XX]
    goto :step4
)
start "" msedge --app=http://127.0.0.1:8642/console.html --window-size=1150,760 2>nul
if errorlevel 1 start "" http://127.0.0.1:8642/console.html
echo [4/5] console ............. opened [OK]

rem   jukebox page (close this window to fall back to browser-tab mode)
start "" msedge --app=http://127.0.0.1:8642/jukebox.html --window-size=1280,780 2>nul
echo [4/5] jukebox page ....... opened

:step4
rem ---------- 5. streaming software (start + verify the process came up) ----------
tasklist /FI "IMAGENAME eq obs64.exe" 2>nul | findstr /I obs64 >nul
if not errorlevel 1 (
    echo [5/5] OBS ............... already running [OK]
    goto :done
)
echo [5/5] starting OBS...
start "" "C:\Program Files\obs-studio\bin\64bit\obs64.exe"
"%SystemRoot%\System32\timeout.exe" /t 5 /nobreak >nul
tasklist /FI "IMAGENAME eq obs64.exe" 2>nul | findstr /I obs64 >nul
if errorlevel 1 (
    echo [5/5] OBS ............... FAILED to start [XX]
    echo        check install path: C:\Program Files\obs-studio\bin\64bit\obs64.exe
) else (
    echo [5/5] OBS ............... started [OK]
)

:done
echo.
echo ============================================
echo  Final danmaku source state:
powershell -NoProfile -Command "try{ Write-Host ('  ' + (Invoke-WebRequest -Uri 'http://127.0.0.1:8642/api/status' -UseBasicParsing -TimeoutSec 3).Content) }catch{ Write-Host '  /api/status unreachable' }"
echo.
echo  Go live: streaming app "Start Live" + fresh key,
echo  then "Start Streaming" and MINIMIZE the app.
echo  Console should show TWO GREEN dots.
echo ============================================
"%SystemRoot%\System32\timeout.exe" /t 8
