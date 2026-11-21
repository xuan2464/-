@echo off
chcp 65001 >nul
rem ============================================================
rem  Lite3 camera web viewer - deploy to the robot and open it
rem
rem  Double-click this file. It connects to the robot, uploads
rem  this folder, picks a video source, starts the service and
rem  opens your browser. Everything else is printed in Chinese
rem  by the PowerShell script below.
rem
rem  You will be asked for the robot password 2-3 times.
rem  The password is one single quote character:  '
rem  (press Shift to switch to English input first)
rem
rem  Optional flags, type them after the file name:
rem    -Source ros     use the ROS topic, keep the realsense service
rem    -Source demo    synthetic picture, do not touch the camera
rem    -Check          only check: reachable? service already up?
rem    -Stop           stop the service on the robot
rem    -Port 8090      serve on another port
rem ============================================================
title Lite3 Camera Web - Robot
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\connect.ps1" %*

echo.
pause
