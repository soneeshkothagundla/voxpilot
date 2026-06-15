<#
.SYNOPSIS
    Launch VoxPilot in hands-free desktop mode (overlay + tray, no terminal).

.DESCRIPTION
    Convenience launcher: starts VoxPilot with --windowed --jarvis --turbo using
    the local virtual environment. Run install.ps1 first if you haven't yet.

.EXAMPLE
    Right-click -> "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File run.ps1
#>
$root = $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Host "VoxPilot isn't installed yet. Run install.ps1 first." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}
Start-Process -FilePath $pythonw -ArgumentList "-m", "voxpilot", "--windowed", "--jarvis", "--turbo" -WorkingDirectory $root
Write-Host "VoxPilot is starting in the background - look for the tray icon. Say 'Hey Jarvis'." -ForegroundColor Green
