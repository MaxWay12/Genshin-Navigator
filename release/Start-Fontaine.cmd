@echo off
cd /d "%~dp0"
if not exist "config.json" copy /y "config.example.json" "config.json" >nul
if not exist "datasets\local\references\hoyolab_fontaine_full_n1\pyramid.json" GenshinNavigator.exe setup-region --config config.json --region fontaine
if errorlevel 1 (
  pause
  exit /b 1
)
GenshinNavigator.exe setup-status --config config.json --region fontaine >nul
if errorlevel 1 (
  echo Setup check failed. Run Configure-Minimap.cmd and try again.
  pause
  exit /b 1
)
GenshinNavigator.exe track --regions regions.json --region fontaine
if errorlevel 1 pause
