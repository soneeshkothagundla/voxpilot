<#
.SYNOPSIS
    One-click setup for VoxPilot on Windows. No prior terminal experience needed.

.DESCRIPTION
    Sets everything up in your user account (no Administrator rights required):
      1. Finds Python 3.11+ (offers to install it via winget if missing).
      2. Creates a private virtual environment (.venv) in this folder.
      3. Installs VoxPilot and its dependencies (including the "Hey Jarvis" wake word).
      4. Asks for your AI provider + key and writes a local .env (never committed).
      5. Writes a starter config.yaml.
      6. Creates Start Menu + Desktop shortcuts that launch hands-free mode.

    Re-running is safe: existing .env / config.yaml are kept, not overwritten.

.EXAMPLE
    Right-click this file -> "Run with PowerShell".
    Or, in PowerShell:  powershell -ExecutionPolicy Bypass -File install.ps1
#>
param(
    [switch]$Startup  # also launch VoxPilot automatically at login
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Say([string]$m, [string]$c = "Gray") { Write-Host $m -ForegroundColor $c }
function Step([string]$m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  VoxPilot setup - voice control for your Windows PC" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# --- 1. Find a suitable Python ------------------------------------------------
function Find-Python {
    $candidates = @(
        @("py", "-3.12"), @("py", "-3.11"), @("python"), @("python3")
    )
    foreach ($cand in $candidates) {
        $exe = $cand[0]
        $pre = @()
        if ($cand.Count -gt 1) { $pre = $cand[1..($cand.Count - 1)] }
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $v = & $exe @pre -c "import sys;print(sys.version_info.major,sys.version_info.minor)"
        } catch { continue }
        if ($v -match '^\s*(\d+)\s+(\d+)\s*$') {
            if ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 11) {
                return [pscustomobject]@{ Exe = $exe; Pre = $pre; Ver = "$($Matches[1]).$($Matches[2])" }
            }
        }
    }
    return $null
}

Step "Looking for Python 3.11 or newer..."
$py = Find-Python
if (-not $py) {
    Say "Python 3.11+ was not found." Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Installing Python 3.12 via winget (you may see a permission prompt)..."
        try {
            winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        } catch {
            Say "winget install failed: $($_.Exception.Message)" Yellow
        }
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path", "User")
        $py = Find-Python
    }
}
if (-not $py) {
    Say ""
    Say "Could not find or install Python automatically." Red
    Say "Please install Python 3.11+ from https://www.python.org/downloads/" Red
    Say "(check 'Add python.exe to PATH' during install), then re-run this script." Red
    Read-Host "Press Enter to close"
    exit 1
}
Say "Found Python $($py.Ver)." Green

# --- 2. Virtual environment ---------------------------------------------------
$venv = Join-Path $root ".venv"
$vpy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    Step "Creating a private virtual environment (.venv)..."
    $pre = @($py.Pre)
    & $py.Exe @pre -m venv $venv
}
if (-not (Test-Path $vpy)) {
    Say "Failed to create the virtual environment." Red; Read-Host "Press Enter"; exit 1
}
Say "Virtual environment ready." Green

# --- 3. Install VoxPilot + dependencies --------------------------------------
Step "Installing VoxPilot and dependencies (this can take a few minutes)..."
$ans = Read-Host "Enable hands-free wake word 'Hey Jarvis'? (adds onnxruntime/openwakeword) [Y/n]"
$jarvisEnabled = -not ($ans -match '^(n|no)$')
if ($jarvisEnabled) { $target = @("-e", ".[jarvis]") } else { $target = @("-e", ".") }

& $vpy -m pip install --upgrade pip
Push-Location $root
try {
    & $vpy -m pip install @target
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) {
    Say ""
    Say "pip install failed (exit code $code)." Red
    Say "Scroll up to see the error, fix it, and re-run install.ps1." Red
    Read-Host "Press Enter to close"
    exit 1
}
Say "Dependencies installed." Green

# --- 4. Provider + key -> .env ------------------------------------------------
$provider = "bedrock"
$envPath = Join-Path $root ".env"
if (Test-Path $envPath) {
    Step "Keeping your existing .env (delete it to reconfigure your key)."
} else {
    Step "Connect your AI account"
    Say "VoxPilot uses Claude. Pick how you'll connect:"
    Say "  [1] Anthropic API key  (easiest - get one at https://console.anthropic.com/)"
    Say "  [2] AWS Bedrock token  (for AWS users; region us-east-1)"
    $choice = Read-Host "Enter 1 or 2 (default 1)"
    if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "1" }

    if ($choice -eq "2") {
        $key = Read-Host "Paste your AWS Bedrock bearer token"
        $region = Read-Host "AWS region (default us-east-1)"
        if ([string]::IsNullOrWhiteSpace($region)) { $region = "us-east-1" }
        Set-Content -Path $envPath -Encoding ascii -Value @(
            "AWS_BEARER_TOKEN_BEDROCK=$key",
            "AWS_REGION=$region"
        )
        $provider = "bedrock"
    } else {
        $key = Read-Host "Paste your Anthropic API key (starts with sk-ant-)"
        Set-Content -Path $envPath -Encoding ascii -Value @("ANTHROPIC_API_KEY=$key")
        $provider = "anthropic"
    }
    Say ".env written (this file is private and never committed)." Green
}

# --- 5. Starter config.yaml ---------------------------------------------------
$cfgPath = Join-Path $root "config.yaml"
if (Test-Path $cfgPath) {
    Step "Keeping your existing config.yaml."
} else {
    Step "Writing a starter config.yaml..."
    if ($provider -eq "anthropic") {
        Set-Content -Path $cfgPath -Encoding ascii -Value @(
            "agent:",
            "  provider: anthropic",
            "  model: claude-sonnet-4-6",
            "  opus_model: claude-opus-4-8",
            "safety:",
            "  autonomy: supervised",
            "hotkey:",
            "  wake_word: hey_jarvis"
        )
    } else {
        Copy-Item (Join-Path $root "config.example.yaml") $cfgPath
    }
    Say "config.yaml written (edit it any time to tweak settings)." Green
}

# --- 6. Shortcuts -------------------------------------------------------------
Step "Creating Start Menu + Desktop shortcuts (hands-free Jarvis mode)..."
$shScript = Join-Path $root "scripts\install_shortcuts.ps1"
$shArgs = @("-ExecutionPolicy", "Bypass", "-File", $shScript)
if ($jarvisEnabled) { $shArgs += "-Jarvis" }
if ($Startup) { $shArgs += "-Startup" }
try {
    & powershell @shArgs
} catch {
    Say "Shortcut creation skipped: $($_.Exception.Message)" Yellow
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  VoxPilot is ready!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
if ($jarvisEnabled) {
    Say "Launch it from the Start Menu / Desktop ('VoxPilot'), then say:"
    Say '    "Hey Jarvis"  ->  give it a command.' Cyan
} else {
    Say "Launch it from the Start Menu / Desktop ('VoxPilot'), then hold F9 and speak."
}
Say ""
Say "Prefer the command line? From this folder run:"
Say "    .\.venv\Scripts\pythonw.exe -m voxpilot --windowed --jarvis --turbo" Cyan
Say ""
Say "Safety: triple-tap Esc to stop instantly; money / file-deletion /"
Say "credential actions always ask first. Supervise it while it works."
Read-Host "Press Enter to close"
