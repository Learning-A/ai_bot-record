@echo off
title Voice Services (GPT-SoVITS GPU tunnel + local CPU fallback)
rem --- 1) local CPU api :9880 (fallback, ~30-60s/sentence) ---
netstat -ano | findstr /C:":9880 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [1/2] starting local GSV api (CPU)...
    start "gsv-local" /min cmd /c "F:\apps\gpt-sovits-work\GPT-SoVITS\.venv\Scripts\python.exe F:\apps\gpt-sovits-work\GPT-SoVITS\api_v2.py -a 127.0.0.1 -p 9880 > F:\apps\gpt-sovits-work\api_runtime.log 2>&1"
) else (
    echo [1/2] local api already running
)
rem --- 2) SSH tunnel :9881 -> GPU server :9880 (primary, ~2-4s/sentence) ---
netstat -ano | findstr /C:":9881 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [2/2] starting SSH tunnel to GPU server...
    start "gsv-tunnel" /min cmd /c "F:\apps\gpt-sovits-work\tunnel.bat"
    echo note: GPU server api must be running (F:\apps\gpt-sovits-work\ssh_gpu.sh)
) else (
    echo [2/2] tunnel already running
)
rem --- 3) SSH tunnel :9883 -> GPU server :9882 (paopao voice) ---
netstat -ano | findstr /C:":9883 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [3/3] starting SSH tunnel for paopao voice...
    start "gsv-tunnel-paopao" /min cmd /c "F:pps\gpt-sovits-work	unnel_paopao.bat"
) else (
    echo [3/3] paopao tunnel already running
)
rem --- 4) SSH tunnel :9885 -> GPU server :9884 (IndexTTS-2.5 emotion engine) ---
netstat -ano | findstr /C:":9885 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [4/4] starting SSH tunnel for IndexTTS...
    start "itts-tunnel" /min cmd /c "F:pps\gpt-sovits-work	unnel_itts.bat"
) else (
    echo [4/4] IndexTTS tunnel already running
)
rem --- 5) tunnel watchdog (auto-reconnect) ---
tasklist | findstr /i "powershell" >nul 2>&1
start "tunnel-watchdog" /min powershell -NoProfile -ExecutionPolicy Bypass -File F:pps\gpt-sovits-work	unnel_watchdog.ps1
echo done. bot will prefer 9881 (GPU), fallback 9880 (local CPU)
