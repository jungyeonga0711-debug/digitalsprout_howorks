@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0scripts\start_hiworks.ps1"
