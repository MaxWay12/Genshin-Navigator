@echo off
cd /d "%~dp0"
if not exist "config.json" copy /y "config.example.json" "config.json" >nul
GenshinNavigator.exe track --regions regions.json --region fontaine
if errorlevel 1 pause
