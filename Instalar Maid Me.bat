@echo off
chcp 65001 >nul
title Maid Me - Instalação
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\instalar.ps1"
if errorlevel 1 (
    echo.
    echo A instalação não terminou. Veja a mensagem acima.
)
pause
