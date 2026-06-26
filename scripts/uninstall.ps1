<#
Clean uninstall of Sarup's footprint (Windows). Idempotent.

    .\scripts\uninstall.ps1           # remove MCP registration + hook
    .\scripts\uninstall.ps1 -Purge    # also delete .venv and the cache db

Removes only what Sarup added. The Git repo itself is left in place. The Sarup
section in your global CLAUDE.md is left too (it no-ops when the server is gone);
delete it by hand if you want it fully gone.
#>
param([switch]$Purge)
$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
$db = Join-Path $env:USERPROFILE ".sarup-cache.db"

# 1. Legacy cleanup: older Sarup versions shipped an ANTHROPIC_BASE_URL proxy.
#    Clear it if it still points at a local proxy, so nothing routes to a now-gone
#    service. (Harmless no-op on a clean MCP-only install.)
if ($env:ANTHROPIC_BASE_URL -like "http://localhost:*" -or $env:ANTHROPIC_BASE_URL -like "http://127.0.0.1:*") {
    [Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", $null, "User")
    Remove-Item Env:\ANTHROPIC_BASE_URL -ErrorAction SilentlyContinue
    Write-Host "- cleared legacy ANTHROPIC_BASE_URL"
}

# 2. Unregister MCP (user + project scopes)
if (Get-Command claude -ErrorAction SilentlyContinue) {
    & claude mcp remove sarup -s user 2>$null | Out-Null
    & claude mcp remove sarup -s project 2>$null | Out-Null
    Write-Host "- removed 'sarup' MCP (user + project)"
}

# 3. Remove hook + SARUP_* env from .claude/settings.json + project .mcp.json
if (Test-Path $py) { & $py (Join-Path $repo "scripts\install.py") --uninstall }

# 4. Remove the global /sarup-setup skill
& (Join-Path $repo "scripts\install-skill.ps1") -Remove

# 5. Purge venv + cache
if ($Purge) {
    if (Test-Path (Join-Path $repo ".venv")) { Remove-Item (Join-Path $repo ".venv") -Recurse -Force; Write-Host "- removed .venv" }
    if (Test-Path $db) { Remove-Item $db -Force; Write-Host "- removed cache db" }
}

Write-Host ""
Write-Host "Sarup uninstalled. Restart Claude Code. (Global CLAUDE.md Sarup note left; harmless.)"
