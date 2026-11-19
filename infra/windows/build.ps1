param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Python not found at $Python. Create the venv first."
}

if (-not $SkipInstall) {
    & $Python -m pip install -q -e "$Root\services\backend" -e "$Root\services\desktop[build]"
}

& $Python (Join-Path $Root "infra\brand_assets.py")
& $Python -m PyInstaller --noconfirm --clean --distpath "$Root\dist" --workpath "$Root\build" (Join-Path $PSScriptRoot "Shorekeep.spec")

# Self-extracting setup built with PyInstaller, so no external tool is required.
& $Python -m PyInstaller --noconfirm --clean --distpath "$Root\dist\installer" --workpath "$Root\build\setup" (Join-Path $PSScriptRoot "setup.spec")

$Setup = Join-Path $Root "dist\installer\Shorekeep-Setup-0.2.0.exe"
if (-not (Test-Path $Setup)) {
    throw "Setup build failed; $Setup was not produced."
}
Write-Host "App:    $Root\dist\Shorekeep\Shorekeep.exe"
Write-Host "Setup:  $Setup"

$Iscc = $env:INNO_SETUP_ISCC
if (-not $Iscc) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { $Iscc = $command.Source }
}
if ($Iscc) {
    & $Iscc (Join-Path $PSScriptRoot "Shorekeep.iss")
    Write-Host "Inno installer: $Root\dist\installer\Shorekeep-Setup-0.2.0.exe"
} else {
    Write-Host "Inno Setup not found; skipping the Inno installer (the self-extracting setup above is ready)."
}
