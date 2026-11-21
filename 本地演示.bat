@echo off
chcp 65001 >nul
rem ============================================================
rem  Lite3 camera web viewer - start on THIS PC (no robot)
rem
rem  This shows the synthetic demo picture only. There is no
rem  RealSense camera on a normal PC, so it always falls back
rem  to the demo source. To see the robot camera, run the other
rem  file: the robot deploy script.
rem
rem  Optional flags, type them after the file name:
rem    --source demo   synthetic picture
rem    --port 8090     another port
rem ============================================================
title Lite3 Camera Web - Local Demo
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found. Install Python 3.8+ and add it to PATH.
  pause
  exit /b 1
)

echo.
echo  [note] LOCAL demo picture only - not the robot camera.
echo         For the robot camera, run the deploy script.
echo.
python run.py %*
if errorlevel 1 pause
