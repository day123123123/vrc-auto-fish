@echo off
title VRChat Auto Fish
cd /d "%~dp0"

:: ============================================
::  Check Python
:: ============================================
python --version >nul 2>&1
if errorlevel 1 (
    echo ============================================
    echo   [ERROR] Python not found.
    echo   Please install Python 3.10+ and check "Add to PATH"
    echo   Download: https://www.python.org/downloads/
    echo ============================================
    pause
    exit /b 1
)

:: ============================================
::  Check if dependencies are installed
:: ============================================
python -c "import cv2, keyboard, torch, ultralytics" 2>nul
if errorlevel 1 (
    echo ============================================
    echo   First run detected, installing dependencies...
    echo   (This only runs once, please wait)
    echo ============================================
    echo.
    call :install_deps
    if errorlevel 1 (
        echo [ERROR] Install failed. Check your network and try again.
        pause
        exit /b 1
    )
    echo.
    echo ============================================
    echo   Dependencies installed, launching app...
    echo ============================================
    echo.
)

:: ============================================
::  Launch
:: ============================================
echo [INFO] Starting VRC Auto Fish...
echo        First launch can take 10-30 seconds while torch / YOLO load, please wait.
echo.
python main.py
if errorlevel 1 pause
exit /b 0

:: ============================================
::  Install subroutine
:: ============================================
:install_deps
echo.
echo [1/2] Installing PyTorch...
call :install_torch

echo.
echo [2/2] Installing other dependencies...
pip install -r requirements.txt
if errorlevel 1 exit /b 1
exit /b 0

:install_torch
setlocal EnableExtensions EnableDelayedExpansion
set "GPU_NAME="
set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu"
set "TORCH_LABEL=CPU"
set "LEGACY_NVIDIA_GPU=0"

for /f "usebackq delims=" %%G in (`nvidia-smi --query-gpu=name --format=csv,noheader 2^>nul`) do (
    if not defined GPU_NAME set "GPU_NAME=%%G"
)

if not defined GPU_NAME (
    echo   No NVIDIA GPU detected, installing CPU version of PyTorch
) else (
    echo   NVIDIA GPU detected: !GPU_NAME!
    call :is_legacy_nvidia_gpu "!GPU_NAME!" LEGACY_NVIDIA_GPU
    if "!LEGACY_NVIDIA_GPU!"=="1" (
        echo   Legacy NVIDIA GPU detected, installing CUDA 11.8 version of PyTorch
        echo   Some older NVIDIA GPUs cannot use the default CUDA 12.8 build.
        set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cu118"
        set "TORCH_LABEL=CUDA 11.8"
    ) else (
        echo   Installing CUDA 12.8 version of PyTorch (GPU acceleration)
        set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128"
        set "TORCH_LABEL=CUDA 12.8"
    )
)

pip install torch torchvision --index-url !TORCH_INDEX_URL!
set "INSTALL_ERR=!ERRORLEVEL!"

if "!INSTALL_ERR!"=="0" if "!LEGACY_NVIDIA_GPU!"=="1" (
    echo.
    echo   [WARNING] 您的设备型号过于老旧，使用时可能会导致异常。
    echo   [WARNING] Detected GPU: !GPU_NAME!
    echo   [WARNING] Installed PyTorch build: !TORCH_LABEL!
)

endlocal & exit /b %INSTALL_ERR%

:is_legacy_nvidia_gpu
set "%~2=0"
echo %~1 | findstr /i /c:"GTX 6" /c:"GTX 7" /c:"GTX 8" /c:"GTX 9" /c:"GTX 10" /c:"GT 7" /c:"GT 8" /c:"GT 9" /c:"GT 10" /c:"Quadro K" /c:"Quadro M" /c:"Quadro P" /c:"Tesla K" /c:"Tesla M" /c:"Tesla P" /c:"Tesla V" /c:"TITAN X" /c:"TITAN XP" /c:"TITAN V" >nul && set "%~2=1"
exit /b 0
