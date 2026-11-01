# Makes MaidMe.zip to send to friends, without the local install or anyone's keys and settings.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$personal = '.venv', '.env', 'persona.md', 'personalidade.txt', 'sobre-voce.txt', 'vts_token.txt', 'conversas.json', 'app_settings.json', 'assistente.log', '__pycache__'
$stage = Join-Path $env:TEMP 'Maid Me'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null
Get-ChildItem $root -File | Where-Object { $_.Name -ne 'empacotar.ps1' -and $_.Extension -ne '.zip' } |
    Copy-Item -Destination $stage
New-Item -ItemType Directory "$stage\app" | Out-Null
Get-ChildItem "$root\app" -Force | Where-Object { $personal -notcontains $_.Name } |
    Copy-Item -Destination "$stage\app" -Recurse
Get-ChildItem $stage -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
$zip = Join-Path $root 'MaidMe-1.1.0.zip'
Compress-Archive -Path $stage -DestinationPath $zip -Force
Remove-Item $stage -Recurse -Force
Write-Host "Pacote criado: $zip"
