<#
Install the sarup-setup skill into the global Claude Code skills dir so you can
run /sarup-setup from any project. Idempotent.

    .\scripts\install-skill.ps1            # install to ~/.claude/skills
    .\scripts\install-skill.ps1 -Remove    # uninstall
#>
param([switch]$Remove)
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$src = Join-Path $repo "skills\sarup-setup"
$dstDir = Join-Path $env:USERPROFILE ".claude\skills\sarup-setup"

if ($Remove) {
    if (Test-Path $dstDir) { Remove-Item $dstDir -Recurse -Force; Write-Host "Removed $dstDir" }
    else { Write-Host "Nothing to remove." }
    return
}

New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
Copy-Item (Join-Path $src "SKILL.md") (Join-Path $dstDir "SKILL.md") -Force
Write-Host "Installed /sarup-setup skill -> $dstDir"
Write-Host "Reload Claude Code, then type /sarup-setup in any project."
