@echo off
title Build VRC Auto Fish Launcher
cd /d "%~dp0"

python -m PyInstaller --noconfirm booth_launcher.spec
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo [OK] Build finished: dist\VRC Auto Fish Launcher\
pause
