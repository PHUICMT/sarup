<#
Start the Sarup tray in the background (no console window, terminal returns).

    .\scripts\start-tray.ps1          # start
    .\scripts\start-tray.ps1 -Stop    # stop any running tray

Uses pythonw.exe so there's no blocking console. The tray icon appears in the
notification area; right-click it for the menu (compression / route / quit).
#>
param([switch]$Stop)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"

if ($Stop) {
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*sarup.tray*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "Stopped tray (PID $($_.ProcessId))." }
    return
}

if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found at $pythonw (run setup.ps1 first)" }
Start-Process -FilePath $pythonw -ArgumentList "-m", "sarup.tray"
Write-Host "sarup-tray started in background - look for the tray icon (right-click for menu)."
