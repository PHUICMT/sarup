<#
Register the Sarup tray app to auto-start on Windows login (no console window).
Drops a shortcut in the user's Startup folder that runs `pythonw -m sarup.tray`.

    .\scripts\install-autostart.ps1            # install
    .\scripts\install-autostart.ps1 -Remove    # uninstall

The proxy runs offline (extractive) - it does NOT need Ollama, which may start
later. Routing Claude Code through it is a separate, explicit toggle in the tray
menu ("Route Claude Code"), so installing autostart alone changes nothing until
you opt in.
#>
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "Sarup Proxy.lnk"

if ($Remove) {
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "Removed: $lnk" }
    else { Write-Host "Not installed." }
    return
}

if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found at $pythonw (create the .venv first)" }

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $pythonw
$sc.Arguments = "-m sarup.tray"
$sc.WorkingDirectory = $repo
$sc.WindowStyle = 7  # minimized
$sc.Description = "Sarup compression proxy (tray)"
$sc.Save()

Write-Host "Installed autostart shortcut: $lnk"
Write-Host "Starts on next login. To run now:  Start-Process '$pythonw' '-m sarup.tray'"
