# Maid Me installer. Run through "Instalar Maid Me.bat" in the folder above.
# Everything stays inside this folder (.venv) plus two shortcuts; nothing runs at Windows startup.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Find-Python {
    foreach ($v in '3.14', '3.13', '3.12', '3.11') {
        try { $exe = & py "-$v" -c 'import sys; print(sys.executable)' 2>$null; if ($LASTEXITCODE -eq 0 -and $exe) { return $exe } } catch {}
    }
    $local = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($local) { return $local.FullName }
    return $null
}

function Step($n, $text) { Write-Host ''; Write-Host "[$n/4] $text" -ForegroundColor Magenta }

Clear-Host
Write-Host '  Maid Me - instalacao' -ForegroundColor Magenta
Write-Host '  Assistente de voz para Windows. Codigo aberto, licenca MIT.'
Write-Host '  Tudo fica dentro desta pasta. Para desinstalar, apague a pasta e os atalhos.'

Step 1 'Verificando o Python'
$python = Find-Python
if (-not $python) {
    Write-Host 'Python nao encontrado. Instalando a versao oficial pelo winget (Microsoft)...'
    winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
    $python = Find-Python
    if (-not $python) { throw 'Nao foi possivel instalar o Python. Instale pelo site python.org e rode o instalador de novo.' }
}
Write-Host "Usando $python"

Step 2 'Criando o ambiente isolado do programa'
if (-not (Test-Path '.venv\Scripts\python.exe')) { & $python -m venv .venv }
$venv = (Resolve-Path '.venv\Scripts\python.exe').Path
& $venv -m pip install --upgrade pip --quiet --disable-pip-version-check

Step 3 'Baixando as bibliotecas do PyPI (pode levar alguns minutos)'
& $venv -m pip install -r requirements.txt --disable-pip-version-check
if ($LASTEXITCODE) { throw 'Falha ao instalar as bibliotecas. Verifique a internet e tente de novo.' }
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host 'Placa NVIDIA encontrada: instalando a aceleracao por GPU.'
    & $venv -m pip install -r requirements-gpu.txt --disable-pip-version-check
}

Step 4 'Criando os atalhos'
$shell = New-Object -ComObject WScript.Shell
$icon = Join-Path $PSScriptRoot 'assets\maidme.ico'
$menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Maid Me'
New-Item -ItemType Directory -Force $menu | Out-Null
Remove-Item (Join-Path $menu 'Configurar Maid Me.lnk') -ErrorAction SilentlyContinue  # old versions had a separate configurator
foreach ($s in @(
        @{ Path = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Maid Me.lnk'; Target = 'Iniciar.bat'; Desc = 'Iniciar a assistente' },
        @{ Path = Join-Path $menu 'Maid Me.lnk'; Target = 'Iniciar.bat'; Desc = 'Iniciar a assistente' })) {
    $lnk = $shell.CreateShortcut($s.Path)
    $lnk.TargetPath = Join-Path $PSScriptRoot $s.Target
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.IconLocation = $icon
    $lnk.Description = $s.Desc
    $lnk.Save()
}

Write-Host ''
Write-Host 'Instalacao concluida. Abrindo o Maid Me...' -ForegroundColor Green
Start-Process (Join-Path $PSScriptRoot 'Iniciar.bat')
