#!/usr/bin/env bash
# One-command Sarup setup (Linux / WSL / macOS). Idempotent — safe to re-run.
#
#   ./scripts/setup.sh            # venv + install + register MCP (user scope)
#   ./scripts/setup.sh --all      # also: auto-compress hook + pull Ollama models
#   ./scripts/setup.sh --with-hook --pull
#
# Sarup is an MCP server: register it once and every Claude Code project can call
# sarup_compress / sarup_retrieve / sarup_stats. It never sits in the API path, so
# it can never break Claude Code — if the server is down the tools are simply
# unavailable and Claude keeps working normally.
set -euo pipefail

WITH_HOOK=0; PULL=0
for a in "$@"; do
    case "$a" in
        --all) WITH_HOOK=1; PULL=1 ;;
        --with-hook) WITH_HOOK=1 ;;
        --pull) PULL=1 ;;
    esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO/.venv"
PY="$VENV/bin/python"
DB="$HOME/.sarup-cache.db"

# 1. venv
if [ ! -x "$PY" ]; then
    echo "[1/4] Creating .venv..."
    python3 -m venv "$VENV"
else
    echo "[1/4] .venv exists."
fi

# 2. install
echo "[2/4] Installing sarup + deps..."
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -e "$REPO"

# 3. register MCP at user scope
echo "[3/4] Registering 'sarup' MCP (user scope)..."
CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
if [ -x "$CLAUDE" ]; then
    "$CLAUDE" mcp remove sarup -s user >/dev/null 2>&1 || true
    "$CLAUDE" mcp add sarup --scope user --env "SARUP_DB_PATH=$DB" -- "$PY" -m sarup.server
else
    echo "  'claude' not found — run later:"
    echo "    claude mcp add sarup --scope user --env SARUP_DB_PATH=$DB -- $PY -m sarup.server"
fi

# 4. optional extras
echo "[4/4] Optional extras..."
if [ "$PULL" = "1" ] && command -v ollama >/dev/null 2>&1; then
    for m in nomic-embed-text gemma3:12b; do ollama pull "$m"; done
fi
if [ "$WITH_HOOK" = "1" ]; then "$PY" "$REPO/scripts/install.py" --with-hook; fi

echo ""
echo "Done. Sarup is registered for all projects (restart Claude Code to load)."
echo "Use it in any Claude Code session:"
echo "  sarup_compress(content, mode='auto')   # compress (lossy view + lossless store)"
echo "  sarup_retrieve(hash='...')             # recover the original byte-for-byte"
echo "  sarup_stats()                          # session savings"
