$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "[2026 D-Sak] Create Employee ZIP"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$DistDir = Join-Path $Root "dist"
$PackageDir = Join-Path $DistDir "digitalsprout_howorks_employee"
$ZipPath = Join-Path $DistDir "digitalsprout_howorks_employee.zip"

Write-Host "[2026 D-Sak] Creating employee ZIP package" -ForegroundColor Green

if (-not (Test-Path "client_secret.json")) {
    throw "client_secret.json is missing. Put it in the project folder first."
}

if (-not (Test-Path "config\settings.yml")) {
    throw "config\settings.yml is missing. Run Hiworks_Start.bat once and save the settings first."
}

if (Test-Path $PackageDir) {
    Remove-Item $PackageDir -Recurse -Force
}
if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}
New-Item -ItemType Directory -Path $PackageDir | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PackageDir "config") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PackageDir "scripts") | Out-Null

Copy-Item "Hiworks_Start.bat" $PackageDir
Copy-Item "README.md" $PackageDir
Copy-Item "requirements.txt" $PackageDir
Copy-Item "client_secret.json" $PackageDir
Copy-Item "config\settings.yml" (Join-Path $PackageDir "config\settings.yml")
Copy-Item "config\settings.example.yml" (Join-Path $PackageDir "config\settings.example.yml")
Copy-Item "scripts\start_hiworks.ps1" (Join-Path $PackageDir "scripts\start_hiworks.ps1")
Copy-Item "hiworks_sync" (Join-Path $PackageDir "hiworks_sync") -Recurse

Get-ChildItem -Path $PackageDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $PackageDir -Recurse -File -Include "*.pyc" | Remove-Item -Force

Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $ZipPath -Force

Write-Host ""
Write-Host "Employee ZIP created:" -ForegroundColor Cyan
Write-Host $ZipPath -ForegroundColor Yellow
Write-Host ""
Write-Host "Share this ZIP with employees. They only need to unzip it and double-click Hiworks_Start.bat."
