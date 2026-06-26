#!/usr/bin/env bash
# Install the sarup-setup skill into the global Claude Code skills dir so you can
# run /sarup-setup from any project. Idempotent.
#
#   ./scripts/install-skill.sh            # install to ~/.claude/skills
#   ./scripts/install-skill.sh --remove   # uninstall
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/skills/sarup-setup"
DST="$HOME/.claude/skills/sarup-setup"

if [ "${1:-}" = "--remove" ]; then
    rm -rf "$DST" && echo "Removed $DST" || echo "Nothing to remove."
    exit 0
fi

mkdir -p "$DST"
cp -f "$SRC/SKILL.md" "$DST/SKILL.md"
echo "Installed /sarup-setup skill -> $DST"
echo "Reload Claude Code, then type /sarup-setup in any project."
