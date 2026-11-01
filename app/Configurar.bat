@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo O Maid Me ainda não foi instalado. Rode o "Instalar Maid Me.bat" primeiro.
    pause
    exit /b
)
start "" ".venv\Scripts\pythonw.exe" configurar.py
