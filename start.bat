@echo off
title VRChat Auto Fish
cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please run install.bat first.
    pause
    exit /b 1
)

:: Check core dependencies
python -c "import cv2, keyboard, torch, ultralytics" 2>nul
if errorlevel 1 (
    echo [INFO] Dependencies not installed. Please run install.bat first.
    pause
    exit /b 1
)

echo [INFO] Starting VRC Auto Fish...
echo        First launch can take 10-30 seconds while torch / YOLO load, please wait.
echo.
python main.py
if errorlevel 1 pause
