@echo off
chcp 65001 >nul
title Maid Me
cd /d "%~dp0"
if not exist ".env" (
    echo Falta configurar: abrindo o configurador.
    call Configurar.bat
    exit /b
)
".venv\Scripts\python.exe" -m assistente %*
if errorlevel 1 pause
