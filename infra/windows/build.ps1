param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()

if (-not (Test-Path $Python)) {
    throw "Python not found at $Python. Create the venv first."
}

$env:SHOREKEEP_VERSION = $Version

if (-not $SkipInstall) {
    & $Python -m pip install -q -e "$Root\services\backend" -e "$Root\services\desktop[build]"
}

# Code signing is opt-in. Provide one of:
#   SHOREKEEP_SIGN_CERT_PATH   path to a .pfx/.p12 file (with SHOREKEEP_SIGN_CERT_PASSWORD)
#   SHOREKEEP_SIGN_THUMBPRINT  SHA1 thumbprint of a certificate in the local store
#   SHOREKEEP_SIGN_SUBJECT     subject name of a certificate in the local store
$TimestampUrl = if ($env:SHOREKEEP_TIMESTAMP_URL) { $env:SHOREKEEP_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }

function Get-SignTool {
    if ($env:SIGNTOOL -and (Test-Path $env:SIGNTOOL)) { return $env:SIGNTOOL }
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($base in @("${env:ProgramFiles(x86)}\Windows Kits\10\bin", "$env:ProgramFiles\Windows Kits\10\bin")) {
        if (Test-Path $base) {
            $found = Get-ChildItem -Path $base -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | Select-Object -First 1
            if ($found) { return $found.FullName }
        }
    }
    return $null
}

$SignTool = Get-SignTool
$HasCert = $env:SHOREKEEP_SIGN_CERT_PATH -or $env:SHOREKEEP_SIGN_THUMBPRINT -or $env:SHOREKEEP_SIGN_SUBJECT

function Invoke-Sign([string]$File) {
    if (-not $SignTool -or -not $HasCert) { return }
    $signArgs = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256")
    if ($env:SHOREKEEP_SIGN_CERT_PATH) {
        $signArgs += @("/f", $env:SHOREKEEP_SIGN_CERT_PATH)
        if ($env:SHOREKEEP_SIGN_CERT_PASSWORD) { $signArgs += @("/p", $env:SHOREKEEP_SIGN_CERT_PASSWORD) }
    } elseif ($env:SHOREKEEP_SIGN_THUMBPRINT) {
        $signArgs += @("/sha1", $env:SHOREKEEP_SIGN_THUMBPRINT)
    } else {
        $signArgs += @("/n", $env:SHOREKEEP_SIGN_SUBJECT)
    }
    Write-Host "Signing: $File"
    & $SignTool @signArgs $File
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $File" }
}

# Build the app, sign it, then package it so the installers contain a signed binary.
& $Python (Join-Path $Root "infra\brand_assets.py")
if ($LASTEXITCODE -ne 0) { throw "Brand asset generation failed." }

& $Python -m PyInstaller --noconfirm --clean --distpath "$Root\dist" --workpath "$Root\build" (Join-Path $PSScriptRoot "Shorekeep.spec")
if ($LASTEXITCODE -ne 0) { throw "App build failed; see the PyInstaller output above." }

$AppExe = Join-Path $Root "dist\Shorekeep\Shorekeep.exe"
if (-not (Test-Path $AppExe)) {
    throw "App build failed; $AppExe was not produced."
}
Invoke-Sign $AppExe

# Self-extracting setup built with PyInstaller, so no external tool is required.
& $Python -m PyInstaller --noconfirm --clean --distpath "$Root\dist\installer" --workpath "$Root\build\setup" (Join-Path $PSScriptRoot "setup.spec")
if ($LASTEXITCODE -ne 0) { throw "Setup build failed; see the PyInstaller output above." }

$Setup = Join-Path $Root "dist\installer\Shorekeep-Setup-$Version.exe"
if (-not (Test-Path $Setup)) {
    throw "Setup build failed; $Setup was not produced."
}
Invoke-Sign $Setup

$Iscc = $env:INNO_SETUP_ISCC
if (-not $Iscc) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { $Iscc = $command.Source }
}
if ($Iscc) {
    & $Iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot "Shorekeep.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }
    Invoke-Sign $Setup
    Write-Host "Inno installer: $Setup"
} else {
    Write-Host "Inno Setup not found; skipping the Inno installer (the self-extracting setup above is ready)."
}

if (-not $SignTool) { Write-Host "signtool not found; artifacts are unsigned." }
elseif (-not $HasCert) { Write-Host "Signing skipped: no certificate configured." }

$sums = Join-Path $Root "dist\installer\SHA256SUMS.txt"
Get-ChildItem -Path (Join-Path $Root "dist\installer") -Filter "Shorekeep-Setup-$Version.exe" |
    ForEach-Object { "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name } |
    Set-Content -Path $sums -Encoding ascii

Write-Host "App:    $AppExe"
Write-Host "Setup:  $Setup"
Write-Host "Sums:   $sums"
