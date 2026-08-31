@echo off
cd /d "%~dp0"
if not exist "config.json" copy /y "config.example.json" "config.json" >nul
echo Select the complete minimap circle, then press Enter. Press C to cancel.
GenshinNavigator.exe configure-roi --config config.json
if errorlevel 1 pause
