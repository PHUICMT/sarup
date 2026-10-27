"""Sarup PostToolUse hook: automatic, transparent context compression.

Run as the `sarup-hook` command (see hooks/README.md). When a tool
like Read/Bash/Grep returns a large, prose-or-log-like output, the hook replaces
it with a compressed version via `updatedToolOutput`, and caches the original in
the shared store so `sarup_retrieve(hash)` can recover it byte-for-byte.

Safety:
- Source-code reads are skipped (line-dropping would corrupt code).
- Only substitutes when compression actually saved tokens.
- The original is always cached first, so substitution is never lossy end-to-end.

Runs offline with the deterministic `extractive` mode by default (fast: adds
~1ms). Set SARUP_HOOK_MODE=semantic for higher ratio (needs Ollama, slower).
"""

from __future__ import annotations

import json
import os
import sys

# On Windows the hook's stdout is a cp1252 pipe; writing Thai directly would
# raise UnicodeEncodeError and crash the hook (silently dropping compression).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Extensions whose content must never be line-dropped.
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".scala", ".sh",
    ".ps1", ".sql", ".html", ".css", ".scss", ".vue", ".lua", ".r", ".m",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".csv", ".tsv",
}

# Token-based threshold (fair across languages): in cl100k_base, Thai is ~1
# token/char but English is ~0.2, so a *char* threshold would make this Thai-first
# tool skip token-heavy Thai blocks while over-compressing English. Default 400 tok.
MIN_TOKENS = int(os.environ.get("SARUP_HOOK_MIN_TOKENS", "400"))
# Extractive by default: ~1 ms, offline, and it kept at least as many facts as
# semantic in the QA eval. 'auto' costs ~2 s per tool call on Windows when
# Ollama is absent (each hook is a fresh process, so nothing is remembered).
HOOK_MODE = os.environ.get("SARUP_HOOK_MODE", "extractive")


def _is_code_read(tool_name: str, tool_input: dict) -> bool:
    """Skip Read of source files: lossy line-dropping would corrupt code."""
    if tool_name != "Read":
        return False
    path = (tool_input or {}).get("file_path", "")
    _, ext = os.path.splitext(path.lower())
    return ext in _CODE_EXTS


def _extract_output(tool_response) -> str:
    """Pull the human-readable text out of a PostToolUse `tool_response`.

    The host sends `tool_response`, whose shape depends on the tool:
      Bash → {"stdout": str, "stderr": str, ...}
      Read → {"file": {"content": str, ...}} (or a plain str)
      Grep → str, or {"content"/"output": str}
    """
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, dict):
        if "stdout" in tool_response:  # Bash
            out = tool_response.get("stdout") or ""
            err = tool_response.get("stderr") or ""
            return out + (("\n" + err) if err else "")
        f = tool_response.get("file")  # Read
        if isinstance(f, dict) and isinstance(f.get("content"), str):
            return f["content"]
        for k in ("content", "output", "result", "text"):  # generic
            if isinstance(tool_response.get(k), str):
                return tool_response[k]
    return ""


def _replace_output(tool_response, text: str):
    """`tool_response` with its text swapped for `text`, in the tool's own shape.

    The host ignores an updatedToolOutput that doesn't match the tool's output
    schema, so a bare string for Read or Bash changes nothing: keep the
    structure and replace only the field _extract_output read from.
    """
    if not isinstance(tool_response, dict):
        return text
    if "stdout" in tool_response:  # Bash: stderr was folded into the text
        return {**tool_response, "stdout": text, "stderr": ""}
    f = tool_response.get("file")
    if isinstance(f, dict) and isinstance(f.get("content"), str):
        return {**tool_response, "file": {**f, "content": text}}
    for k in ("content", "output", "result", "text"):
        if isinstance(tool_response.get(k), str):
            return {**tool_response, k: text}
    return text


def build_hook_output(payload: dict) -> dict | None:
    """Core logic. Returns the hook JSON dict, or None to leave output unchanged.

    Importable and pure (no stdin/stdout) so it can be unit-tested.
    """
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    tool_response = payload.get("tool_response", "")
    output = _extract_output(tool_response)
    # Windows console capture can leave lone surrogates (surrogateescape) in the
    # text; drop them so hashing/tokenizing downstream can't raise UnicodeError.
    output = output.encode("utf-8", "surrogatepass").decode("utf-8", "replace")

    # Cheap pre-gate: skip clearly-small outputs WITHOUT loading the tokenizer
    # (the hook is a fresh process per call). Thai is ~1 token/char, so anything
    # shorter than MIN_TOKENS chars can't reach MIN_TOKENS tokens; the real
    # token-based decision happens below.
    if len(output) < MIN_TOKENS:
        return None
    if _is_code_read(tool_name, tool_input):
        return None

    # Without a shared on-disk store, the MCP server (a different process) could
    # never recover the original, so the retrieval hash we'd advertise would be
    # a broken promise. Refuse to substitute rather than lose data silently.
    # Imported lazily so an import error never breaks the user's tool result.
    from sarup.store import CompressionStore, default_db_path

    db_path = default_db_path()
    if not db_path:
        return None

    from sarup.compressor import compress
    from sarup.tokens import count_tokens

    # Real gate: only worth it past MIN_TOKENS; below that the ~30-token footer
    # and retrieval indirection cost more than compression saves.
    if count_tokens(output) < MIN_TOKENS:
        return None

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
        f"[…] marks omitted text; a fact you need may be there, not in the lines nearby. "
        f"Call sarup_retrieve(hash='{h}') for full content if needed.]"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": _replace_output(tool_response, result.compressed + footer),
        }
    }


def _log(payload: dict, out: dict | None, error: Exception | None) -> None:
    """One JSON line per call to SARUP_HOOK_LOG, if set: did the hook fire, and what did it do."""
    path = os.environ.get("SARUP_HOOK_LOG")
    if not path:
        return
    resp = payload.get("tool_response")
    entry = {
        "event": payload.get("hook_event_name"),
        "tool": payload.get("tool_name"),
        "response_keys": sorted(resp) if isinstance(resp, dict) else type(resp).__name__,
        "chars": len(_extract_output(resp)) if resp is not None else 0,
        "substituted": out is not None,
        "error": repr(error) if error else None,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> None:
    # `--db PATH` lets a hook config name the shared store in its args, for
    # hosts that can't set env vars on a hook (the plugin's hooks.json).
    args = sys.argv[1:]
    if "--db" in args[:-1]:
        os.environ["SARUP_DB_PATH"] = args[args.index("--db") + 1]

    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # malformed input → leave the tool result untouched

    try:
        out = build_hook_output(payload)
    except Exception as e:
        _log(payload, None, e)
        sys.exit(0)  # any failure → never break the user's tool result
    _log(payload, out, None)

    if out is not None:
        try:
            # ensure_ascii=True keeps stdout pure ASCII (Thai → \uXXXX), so it
            # never trips a non-UTF-8 console; the host decodes it back.
            sys.stdout.write(json.dumps(out))
        except Exception:
            pass  # writing failed → leave tool output untouched, never crash
    sys.exit(0)


if __name__ == "__main__":
    main()
