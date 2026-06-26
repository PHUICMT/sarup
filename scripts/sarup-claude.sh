#!/usr/bin/env bash
# Launch the Sarup proxy + Claude Code routed through it (Linux / WSL / macOS).
#
#   ./scripts/sarup-claude.sh [claude args...]
#
# The proxy is killed automatically on exit. SARUP_DB_PATH defaults to the same
# cache the MCP server uses, so sarup_retrieve recovers originals.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
PORT="${SARUP_PROXY_PORT:-8788}"

export SARUP_PROXY_COMPRESS="${SARUP_PROXY_COMPRESS:-1}"
export SARUP_DB_PATH="${SARUP_DB_PATH:-$HOME/.sarup-cache.db}"
export SARUP_PROXY_PORT="$PORT"

echo "Starting sarup-proxy on :$PORT (db=$SARUP_DB_PATH, compress=$SARUP_PROXY_COMPRESS)..."
"$PY" -m sarup.proxy &
PROXY_PID=$!
trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 20); do
    curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1 && break || sleep 0.5
done

export ANTHROPIC_BASE_URL="http://localhost:$PORT"
echo "Launching Claude Code via proxy (Ctrl-C to exit)..."
claude "$@"
