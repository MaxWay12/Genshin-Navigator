@echo off
cd /d "%~dp0"
if not exist "config.sumeru.json" copy /y "config.sumeru.example.json" "config.sumeru.json" >nul
if not exist "datasets\local\references\hoyolab_sumeru_desert_n1\surface_pyramid.json" GenshinNavigator.exe setup-region --config config.sumeru.json --region sumeru_desert
if errorlevel 1 pause & exit /b 1
GenshinNavigator.exe track --regions regions.json --region sumeru_desert
if errorlevel 1 pause
