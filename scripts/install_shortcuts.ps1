<#
.SYNOPSIS
    Create Start Menu / Desktop (and optional Startup) shortcuts that launch
    VoxPilot in desktop mode with no terminal window.

.DESCRIPTION
    Builds Windows .lnk shortcuts that run the project's venv pythonw.exe with
    "-m voxpilot --windowed". pythonw.exe has no console, so VoxPilot starts
    straight into its on-screen overlay + system-tray icon. Hold F9 to talk;
    quit from the tray menu or with Ctrl+Alt+Q.

.PARAMETER Startup
    Also add a shortcut to the current user's Startup folder so VoxPilot launches
    automatically at login.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_shortcuts.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_shortcuts.ps1 -Startup
#>
param(
    [switch]$Startup
)

$ErrorActionPreference = "Stop"

# Resolve project root (parent of this scripts/ folder) and the venv pythonw.exe.
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Error "pythonw.exe not found at $pythonw. Create the venv first (python -m venv .venv)."
}

$shell = New-Object -ComObject WScript.Shell

function New-VoxPilotShortcut([string]$Path) {
    $lnk = $shell.CreateShortcut($Path)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = "-m voxpilot --windowed"
    $lnk.WorkingDirectory = $root
    $lnk.Description = "VoxPilot - voice-controlled screen agent"
    $lnk.IconLocation = "shell32.dll,138"  # microphone-ish icon
    $lnk.Save()
    Write-Host "Created: $Path"
}

# Start Menu (Programs) and Desktop shortcuts.
$programs = [Environment]::GetFolderPath("Programs")
New-VoxPilotShortcut (Join-Path $programs "VoxPilot.lnk")

$desktop = [Environment]::GetFolderPath("Desktop")
New-VoxPilotShortcut (Join-Path $desktop "VoxPilot.lnk")

# Optional: launch at login.
if ($Startup) {
    $startupDir = [Environment]::GetFolderPath("Startup")
    New-VoxPilotShortcut (Join-Path $startupDir "VoxPilot.lnk")
    Write-Host "VoxPilot will now launch automatically at login."
}

Write-Host ""
Write-Host "Done. Launch VoxPilot from the Start Menu or Desktop (no terminal)."
Write-Host "Hold F9 to talk; quit from the tray icon or press Ctrl+Alt+Q."
