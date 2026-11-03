@echo off
title Restart All Components
echo === 重启全部组件（核心 + B站适配器）===
echo.
call "%~dp0重启核心.bat" nopause
call "%~dp0重启适配器.bat" nopause
echo.
echo ===== 全部重启完成，运行 检查.bat 确认健康 =====
pause
