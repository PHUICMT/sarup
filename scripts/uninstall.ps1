<#
Clean uninstall of Sarup's footprint (Windows). Idempotent.

    .\scripts\uninstall.ps1           # remove MCP reg, hook, autostart, routing
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

# 1. Stop routing (only if it points at a local proxy = ours)
if ($env:ANTHROPIC_BASE_URL -like "http://localhost:*" -or $env:ANTHROPIC_BASE_URL -like "http://127.0.0.1:*") {
    reg delete "HKCU\Environment" /v ANTHROPIC_BASE_URL /f 2>$null | Out-Null
    Remove-Item Env:\ANTHROPIC_BASE_URL -ErrorAction SilentlyContinue
    Write-Host "- unrouted (cleared ANTHROPIC_BASE_URL)"
}

# 2. Unregister MCP (user + project scopes)
if (Get-Command claude -ErrorAction SilentlyContinue) {
    & claude mcp remove sarup -s user 2>$null | Out-Null
    & claude mcp remove sarup -s project 2>$null | Out-Null
    Write-Host "- removed 'sarup' MCP (user + project)"
}

# 3. Remove hook + SARUP_* env from .claude/settings.json + project .mcp.json
if (Test-Path $py) { & $py (Join-Path $repo "scripts\install.py") --uninstall }

# 4. Remove autostart shortcut
& (Join-Path $repo "scripts\install-autostart.ps1") -Remove

# 5. Purge venv + cache
if ($Purge) {
    if (Test-Path (Join-Path $repo ".venv")) { Remove-Item (Join-Path $repo ".venv") -Recurse -Force; Write-Host "- removed .venv" }
    if (Test-Path $db) { Remove-Item $db -Force; Write-Host "- removed cache db" }
}

Write-Host ""
Write-Host "Sarup uninstalled. Restart Claude Code. (Global CLAUDE.md Sarup note left; harmless.)"
