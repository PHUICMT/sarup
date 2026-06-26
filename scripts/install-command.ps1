<#
Make `start-tray` / `stop-tray` callable from ANY directory in any new PowerShell
session, by adding small functions to your PowerShell profile(s).

    .\scripts\install-command.ps1            # install
    .\scripts\install-command.ps1 -Remove    # uninstall

Writes to both Windows PowerShell 5.1 and PowerShell 7 profiles. Open a NEW shell
afterwards (or run  . $PROFILE  ) for it to take effect.
#>
param([switch]$Remove)
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
$stop = Join-Path $repo "scripts\start-tray.ps1"
$docs = [Environment]::GetFolderPath("MyDocuments")
$profiles = @(
    (Join-Path $docs "PowerShell\profile.ps1"),          # pwsh 7
    (Join-Path $docs "WindowsPowerShell\profile.ps1")    # Windows PowerShell 5.1
)

$marker = "# >>> sarup >>>"
$endmark = "# <<< sarup <<<"
$block = @"
$marker
function start-tray { & "$py" -m sarup.tray @args }
function stop-tray  { & "$stop" -Stop }
$endmark
"@

foreach ($p in $profiles) {
    $dir = Split-Path -Parent $p
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $content = if (Test-Path $p) { Get-Content -Raw $p } else { "" }

    # Strip any existing sarup block first (idempotent).
    if ($content -match [regex]::Escape($marker)) {
        $content = [regex]::Replace($content, "(?s)" + [regex]::Escape($marker) + ".*?" + [regex]::Escape($endmark) + "\r?\n?", "")
    }

    if ($Remove) {
        Set-Content -Path $p -Value $content.TrimEnd() -NoNewline
        Write-Host "Removed sarup commands from: $p"
    } else {
        $new = ($content.TrimEnd() + "`r`n`r`n" + $block + "`r`n").TrimStart()
        Set-Content -Path $p -Value $new
        Write-Host "Installed start-tray / stop-tray in: $p"
    }
}

if (-not $Remove) {
    Write-Host ""
    Write-Host "Open a NEW PowerShell window, then type:  start-tray   (from anywhere)"
}
