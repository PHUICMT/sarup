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

$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "python not found at $py (run setup.ps1 first)" }
# The launcher self-detaches (prints starting/started, spawns the tray via pythonw,
# then returns) — so this just runs it and the console is freed.
& $py -m sarup.tray
