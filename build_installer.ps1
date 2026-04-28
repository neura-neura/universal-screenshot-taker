$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "No se encontro venv\\Scripts\\python.exe"
}

& $python .\tools\generate_icon.py | Out-Host
& .\build_exe.ps1 | Out-Host

$iscc = Get-Command ISCC -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
if (-not $iscc) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $iscc = $candidate
            break
        }
    }
}

if (-not $iscc) {
    Write-Host "Inno Setup no esta instalado. Intentando instalarlo con winget..." -ForegroundColor Yellow
    winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements | Out-Host

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $iscc = $candidate
            break
        }
    }
}

if (-not $iscc) {
    throw "No se pudo encontrar ISCC.exe. Instala Inno Setup y vuelve a ejecutar este script."
}

& $iscc .\installer\UniversalScreenshotTaker.iss | Out-Host

Write-Host ""
Write-Host "Instalador listo en installer-output\\UniversalScreenshotTaker-Setup.exe"
