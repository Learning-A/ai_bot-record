@echo off
title Rebuild OBS Scene
setlocal enabledelayedexpansion

rem 定位 setup_obs.py（工具包布局优先，其次仓库布局）
set "SETUP=%~dp0..\大肥鱼直播工具包\setup_obs.py"
if not exist "%SETUP%" set "SETUP=%~dp0..\scripts\setup_obs.py"
if not exist "%SETUP%" (
    echo [XX] 找不到 setup_obs.py（OBS 场景配置脚本）
    echo 应位于: 大肥鱼直播工具包\setup_obs.py 或 scripts\setup_obs.py
    pause & exit /b 1
)
if not exist "%~dp0..\直播形象\index.html" if not exist "%~dp0..\avatar\index.html" (
    echo [XX] 找不到形象页 index.html
    pause & exit /b 1
)

echo 确认 OBS 正在运行后按任意键开始...
pause >nul
python "%SETUP%"
echo.
pause
