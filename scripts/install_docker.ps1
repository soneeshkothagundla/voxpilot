<#
.SYNOPSIS
    Install Docker Desktop (WSL2 backend) so VoxPilot can run in a container.
    MUST be run as Administrator. The assistant launches this elevated for you;
    you just approve the UAC prompt.
#>
$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Installing Docker Desktop for VoxPilot" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

# Enable the Windows features WSL2/Docker need (idempotent; safe to re-run).
Write-Host "[1/2] Enabling WSL + Virtual Machine Platform..."
try {
    dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart | Out-Null
    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart | Out-Null
    Write-Host "      ok"
} catch {
    Write-Host "      (continuing; Docker Desktop will enable these too)" -ForegroundColor Yellow
}

Write-Host "[2/2] Installing Docker Desktop via winget (downloads ~600 MB)..."
winget install -e --id Docker.DockerDesktop `
    --accept-package-agreements --accept-source-agreements

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host " Docker Desktop installed." -ForegroundColor Green
Write-Host " NEXT (these two steps are yours):" -ForegroundColor Green
Write-Host "   1. RESTART your PC (required to finish WSL2/virtualization)."
Write-Host "   2. After restart, launch Docker Desktop once and wait until it"
Write-Host "      says 'Engine running'."
Write-Host " Then tell the assistant: 'Docker is ready' and it will build + run"
Write-Host " VoxPilot for you."
Write-Host "==========================================================" -ForegroundColor Green
Read-Host "Press Enter to close this window"
