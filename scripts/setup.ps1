<#
One-command Sarup setup (Windows). Idempotent - safe to re-run.

    .\scripts\setup.ps1            # venv + install + register MCP (user scope)
    .\scripts\setup.ps1 -All       # also: auto-compress hook + pull Ollama models
    .\scripts\setup.ps1 -WithHook -Pull   # pick individually

Sarup is an MCP server: register it once and every Claude Code project can call
sarup_compress / sarup_retrieve / sarup_stats. It never sits in the API path, so
it can never break Claude Code - if the server is down the tools are simply
unavailable and Claude keeps working normally.
#>
param([switch]$WithHook, [switch]$Pull, [switch]$All)
if ($All) { $WithHook = $true; $Pull = $true }
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

# 2. install
Write-Host "[2/4] Installing sarup + deps..."
& $py -m pip install -q --upgrade pip
& $py -m pip install -q -e "$repo"

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
if ($All) { & (Join-Path $repo "scripts\install-skill.ps1") }   # /sarup-setup anywhere

Write-Host ""
Write-Host "Done. Sarup is registered for all projects (restart Claude Code to load)."
Write-Host "Use it in any Claude Code session:"
Write-Host "  sarup_compress(content, mode='auto')   # compress (lossy view + lossless store)"
Write-Host "  sarup_retrieve(hash='...')             # recover the original byte-for-byte"
Write-Host "  sarup_stats()                          # session savings"
