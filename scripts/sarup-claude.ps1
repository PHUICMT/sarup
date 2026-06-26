<#
Launch the Sarup proxy + Claude Code routed through it (Windows).

    .\scripts\sarup-claude.ps1                 # compression on, default port/db
    .\scripts\sarup-claude.ps1 -- <claude args>

The proxy is stopped automatically when Claude Code exits. SARUP_DB_PATH defaults
to the same cache the MCP server uses, so sarup_retrieve recovers originals.
#>
param(
    [string]$DbPath = "$env:USERPROFILE\.sarup-cache.db",
    [int]$Port = 8788,
    [Parameter(ValueFromRemainingArguments = $true)] $ClaudeArgs
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"

$env:SARUP_PROXY_COMPRESS = "1"
$env:SARUP_DB_PATH = $DbPath
$env:SARUP_PROXY_PORT = "$Port"

Write-Host "Starting sarup-proxy on :$Port (db=$DbPath, compress=on)..."
$proxy = Start-Process -FilePath $py -ArgumentList "-m", "sarup.proxy" -PassThru -WindowStyle Hidden

try {
    $ok = $false
    for ($i = 0; $i -lt 20; $i++) {
        try { if ((Invoke-RestMethod "http://localhost:$Port/health" -TimeoutSec 2).ok) { $ok = $true; break } } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ok) { throw "proxy did not become healthy on :$Port" }

    $env:ANTHROPIC_BASE_URL = "http://localhost:$Port"
    Write-Host "Launching Claude Code via proxy (Ctrl-C to exit)..."
    claude @ClaudeArgs
}
finally {
    Write-Host "Stopping sarup-proxy..."
    Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
}
