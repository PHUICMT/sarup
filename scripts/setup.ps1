<#
One-command Sarup setup (Windows). Idempotent - safe to re-run.

    .\scripts\setup.ps1            # venv + install + register MCP (user scope)
    .\scripts\setup.ps1 -All       # also: auto-compress hook, tray autostart, pull models
    .\scripts\setup.ps1 -WithHook -Autostart -Pull   # pick individually

After it finishes:
    sarup-tray                     # background proxy + tray (then "Route Claude Code")
    .\scripts\sarup-claude.ps1     # or: one-shot proxy+claude for a single session
#>
param([switch]$WithHook, [switch]$Autostart, [switch]$Pull, [switch]$All)
if ($All) { $WithHook = $true; $Autostart = $true; $Pull = $true }
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repo ".venv"
$py = Join-Path $venv "Scripts\python.exe"
$db = Join-Path $env:USERPROFILE ".sarup-cache.db"

# 1. venv (try 3.11, 3.12, then plain python)
if (-not (Test-Path $py)) {
    Write-Host "[1/4] Creating .venv..."
    $made = $false
    foreach ($v in @("-3.11", "-3.12")) {
        try { & py $v -m venv $venv; $made = $true; break } catch {}
    }
    if (-not $made) { & python -m venv $venv }
}
else { Write-Host "[1/4] .venv exists." }

# 2. install (the [tray] extra pulls proxy + tray + everything)
Write-Host "[2/4] Installing sarup + deps..."
& $py -m pip install -q --upgrade pip
& $py -m pip install -q -e "$repo[tray]"

# 3. register MCP at user scope (all projects). Use claude if available.
Write-Host "[3/4] Registering 'sarup' MCP (user scope)..."
$claude = (Get-Command claude -ErrorAction SilentlyContinue)
if ($claude) {
    & claude mcp remove sarup -s user 2>$null | Out-Null
    & claude mcp add sarup --scope user --env "SARUP_DB_PATH=$db" -- $py -m sarup.server
} else {
    Write-Warning "  'claude' not on PATH - skipped. Run later:"
    Write-Host "    claude mcp add sarup --scope user --env SARUP_DB_PATH=$db -- $py -m sarup.server"
}

# 4. optional extras
Write-Host "[4/4] Optional extras..."
if ($Pull -and (Get-Command ollama -ErrorAction SilentlyContinue)) {
    foreach ($m in @("nomic-embed-text", "gemma3:12b")) { & ollama pull $m }
}
if ($WithHook) { & $py (Join-Path $repo "scripts\install.py") --with-hook }
if ($Autostart) { & (Join-Path $repo "scripts\install-autostart.ps1") }
if ($All) { & (Join-Path $repo "scripts\install-command.ps1") }   # start-tray anywhere

Write-Host ""
Write-Host "Done. Sarup is registered for all projects (restart Claude Code to load)."
Write-Host "Use it:"
Write-Host "  - manual:  sarup_compress(content, mode='auto')   (in any Claude Code session)"
Write-Host "  - auto:    sarup-tray   ->  tray menu  ->  'Route Claude Code'"
Write-Host "  - one-shot: .\scripts\sarup-claude.ps1"
