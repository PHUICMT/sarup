"""Sarup PostToolUse hook — automatic, transparent context compression.

Wire this as a Claude Code PostToolUse hook (see hooks/README.md). When a tool
like Read/Bash/Grep returns a large, prose-or-log-like output, the hook replaces
it with a compressed version via `updatedToolOutput`, and caches the original in
the shared store so `sarup_retrieve(hash)` can recover it byte-for-byte.

Safety:
- Source-code reads are skipped (line-dropping would corrupt code).
- Only substitutes when compression actually saved tokens.
- The original is always cached first — substitution is never lossy end-to-end.

Runs offline with the deterministic `extractive` mode by default (fast — adds
~1ms). Set SARUP_HOOK_MODE=semantic for higher ratio (needs Ollama, slower).
"""

from __future__ import annotations

import json
import os
import sys

# Extensions whose content must never be line-dropped.
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".scala", ".sh",
    ".ps1", ".sql", ".html", ".css", ".scss", ".vue", ".lua", ".r", ".m",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".csv", ".tsv",
}

MIN_CHARS = int(os.environ.get("SARUP_HOOK_MIN_CHARS", "4000"))
# 'auto' = semantic when Ollama is up (best ratio), else extractive offline.
HOOK_MODE = os.environ.get("SARUP_HOOK_MODE", "auto")


def _is_code_read(tool_name: str, tool_input: dict) -> bool:
    """Skip Read of source files — lossy line-dropping would corrupt code."""
    if tool_name != "Read":
        return False
    path = (tool_input or {}).get("file_path", "")
    _, ext = os.path.splitext(path.lower())
    return ext in _CODE_EXTS


def build_hook_output(payload: dict) -> dict | None:
    """Core logic. Returns the hook JSON dict, or None to leave output unchanged.

    Importable and pure (no stdin/stdout) so it can be unit-tested.
    """
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    output = payload.get("tool_output", "") or ""

    if len(output) < MIN_CHARS:
        return None
    if _is_code_read(tool_name, tool_input):
        return None

    # Without a shared on-disk store, the MCP server (a different process) could
    # never recover the original — so the retrieval hash we'd advertise would be
    # a broken promise. Refuse to substitute rather than lose data silently.
    db_path = os.environ.get("SARUP_DB_PATH")
    if not db_path:
        return None

    # Imported lazily so an import error never breaks the user's tool result.
    from sarup.compressor import compress
    from sarup.store import CompressionStore

    result = compress(output, mode=HOOK_MODE)
    if result.tokens_saved <= 0 or result.compressed == output:
        return None

    # Cache original in the SHARED store so sarup_retrieve can recover it.
    store = CompressionStore(db_path=db_path)
    h = store.store(output, result.compressed, result.original_tokens, result.compressed_tokens)

    footer = (
        f"\n\n[sarup: auto-compressed {result.savings_percent}% "
        f"({result.original_tokens}→{result.compressed_tokens} tok). "
        f"Original cached as hash '{h}'. "
        f"Call sarup_retrieve(hash='{h}') for full content if needed.]"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": result.compressed + footer,
        }
    }


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # malformed input → leave the tool result untouched

    try:
        out = build_hook_output(payload)
    except Exception:
        sys.exit(0)  # any failure → never break the user's tool result

    if out is not None:
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
