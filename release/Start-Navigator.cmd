@echo off
cd /d "%~dp0"
GenshinNavigator.exe launcher
if errorlevel 1 pause
