$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "[2026 D-Sak] Hiworks Automation"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$InstallMarker = Join-Path $Root ".venv\.hiworks_setup_complete"
$SettingsPath = Join-Path $Root "config\settings.yml"
$SettingsExamplePath = Join-Path $Root "config\settings.example.yml"
$ClientSecretPath = Join-Path $Root "client_secret.json"
$Url = "http://127.0.0.1:8765/"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Pause-ForUser {
    param([string]$Message)
    Write-Host ""
    Read-Host $Message | Out-Null
}

function Invoke-SystemPython {
    param([string[]]$Arguments)

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & py -3.11 @Arguments
        if ($LASTEXITCODE -eq 0) {
            return
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Step "Python 3.11 is required"
        Write-Host "Python is not installed on this computer."
        Write-Host "The Python download page will open now."
        Write-Host "Install Python 3.11 or newer, and check 'Add python.exe to PATH' during setup."
        Start-Process "https://www.python.org/downloads/windows/"
        Pause-ForUser "After installing Python, close and run Hiworks_Start.bat again. Press Enter to exit"
        exit 1
    }

    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed."
    }
}

Write-Host "[2026 D-Sak] Hiworks Title / Approval Automation" -ForegroundColor Green
Write-Host "Keep this PowerShell window open while using the controller."

$ExistingServer = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($ExistingServer) {
    Write-Step "Controller is already running"
    Write-Host "Opening the controller in your browser: $Url"
    Start-Process $Url
    return
}

if (-not (Test-Path $VenvPython)) {
    Write-Step "First-run setup"
    Write-Host "Creating the Python virtual environment. This happens only once."
    Invoke-SystemPython @("-m", "venv", ".venv")
}

$NeedsInstall = -not (Test-Path $InstallMarker)
if ((Test-Path $InstallMarker) -and (Test-Path "requirements.txt")) {
    $NeedsInstall = (Get-Item "requirements.txt").LastWriteTime -gt (Get-Item $InstallMarker).LastWriteTime
}

if ($NeedsInstall) {
    Write-Step "Installing Python packages"
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }

    & $VenvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "requirements install failed." }

    Write-Step "Installing Playwright Chromium"
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium install failed." }

    New-Item -ItemType File -Path $InstallMarker -Force | Out-Null
}

if (-not (Test-Path $SettingsPath)) {
    Write-Step "Creating config/settings.yml"
    Copy-Item $SettingsExamplePath $SettingsPath
    Write-Host "Created config\settings.yml from the example file."
    Write-Host "Set the Google Sheet URL and Hiworks options, then save the file."
    Start-Process notepad.exe $SettingsPath
    Pause-ForUser "After saving config/settings.yml, press Enter"
}

if (-not (Test-Path $ClientSecretPath)) {
    Write-Step "Google client_secret.json is required"
    Write-Host "Place client_secret.json in this folder:"
    Write-Host $Root -ForegroundColor Yellow
    Start-Process explorer.exe $Root
    Pause-ForUser "After adding client_secret.json, press Enter"
}

Write-Step "Starting controller"
Write-Host "The browser will open soon: $Url"
Write-Host "To stop the controller, press Ctrl+C in this window or close the window."
& $VenvPython -m hiworks_sync control-panel --settings config\settings.yml --host 127.0.0.1 --port 8765
