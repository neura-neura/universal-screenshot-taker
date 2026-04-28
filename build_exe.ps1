$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "No se encontro venv\Scripts\python.exe"
}

& $python .\tools\generate_icon.py | Out-Host

& $python -m pip install --disable-pip-version-check pyinstaller | Out-Host

& $python -m PyInstaller --noconfirm --clean .\universal_screenshot_taker.spec | Out-Host

Write-Host ""
Write-Host "Build listo en dist\UniversalScreenshotTaker.exe"
