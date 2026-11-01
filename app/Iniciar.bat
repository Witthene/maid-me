@echo off
chcp 65001 >nul
title Maid Me
cd /d "%~dp0"
if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -m assistente
    exit /b
)
".venv\Scripts\python.exe" -m assistente %*
if errorlevel 1 pause
