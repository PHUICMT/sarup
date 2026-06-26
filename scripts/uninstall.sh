#!/usr/bin/env bash
# Clean uninstall of Sarup's footprint (Linux / WSL / macOS). Idempotent.
#
#   ./scripts/uninstall.sh            # remove MCP registration + hook
#   ./scripts/uninstall.sh --purge    # also delete .venv and the cache db
#
# Removes only what Sarup added. The repo is left in place. The Sarup section in
# your global CLAUDE.md is left too (it no-ops when the server is gone).
set -uo pipefail

PURGE=0
for a in "$@"; do [ "$a" = "--purge" ] && PURGE=1; done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
DB="$HOME/.sarup-cache.db"

# 1. Legacy cleanup: older Sarup versions shipped an ANTHROPIC_BASE_URL proxy.
case "${ANTHROPIC_BASE_URL:-}" in
    http://localhost:*|http://127.0.0.1:*) unset ANTHROPIC_BASE_URL; echo "- cleared legacy ANTHROPIC_BASE_URL for this shell; remove it from your profile if set there";;
esac

# 2. Unregister MCP (user + project)
CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
if [ -x "$CLAUDE" ]; then
    "$CLAUDE" mcp remove sarup -s user >/dev/null 2>&1 || true
    "$CLAUDE" mcp remove sarup -s project >/dev/null 2>&1 || true
    echo "- removed 'sarup' MCP (user + project)"
fi

# 3. Remove hook + SARUP_* env from config
[ -x "$PY" ] && "$PY" "$REPO/scripts/install.py" --uninstall || true

# 4. Purge venv + cache
if [ "$PURGE" = "1" ]; then
    [ -d "$REPO/.venv" ] && rm -rf "$REPO/.venv" && echo "- removed .venv"
    [ -f "$DB" ] && rm -f "$DB" && echo "- removed cache db"
fi

echo ""
echo "Sarup uninstalled. Restart Claude Code. (Global CLAUDE.md Sarup note left; harmless.)"
