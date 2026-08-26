@echo off
cd /d "%~dp0"
if not exist "config.sumeru.json" copy /y "config.sumeru.example.json" "config.sumeru.json" >nul
GenshinNavigator.exe track --regions regions.json --region sumeru_desert
if errorlevel 1 pause
