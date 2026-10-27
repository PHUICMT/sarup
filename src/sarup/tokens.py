"""Accurate token counting.

Sarup needs *real* token counts (not byte heuristics) so that reported
savings are verifiable, not estimated. We use tiktoken's `cl100k_base`
encoder as a deterministic, offline proxy for an LLM tokenizer. It is not
byte-for-byte identical to Claude's tokenizer, but because the *same*
encoder is applied to both original and compressed text, the savings ratio
is accurate and consistent.

If tiktoken is unavailable we fall back to a byte heuristic and clearly
report which method was used (see `token_method`).
"""

from __future__ import annotations

import os

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
    TIKTOKEN_AVAILABLE = True
except Exception:  # pragma: no cover - import/runtime guard
    _ENC = None
    TIKTOKEN_AVAILABLE = False


def count_tokens(text: str) -> int:
    """Count tokens precisely with tiktoken, or estimate from bytes."""
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text, disallowed_special=()))
    # Fallback: ~3.5 UTF-8 bytes/token (mixed Thai+English middle ground)
    return max(1, round(len(text.encode("utf-8")) / 3.5))


def token_method() -> str:
    """Name of the active counting method — surfaced in results for honesty."""
    return "tiktoken:cl100k_base" if TIKTOKEN_AVAILABLE else "byte_heuristic:3.5"


# ─── Claude's own tokenizer (opt-in, network) ───────────────────────────────────
#
# cl100k_base is OpenAI's tokenizer and undercounts Claude tokens, most of all on
# Thai. For published numbers we ask Anthropic's count_tokens endpoint instead.
# It sends the text to the API, so the server and hook never call it; only
# bench/eval scripts do, explicitly. Needs the `llm` extra and credentials.

CLAUDE_COUNT_MODEL = os.environ.get("SARUP_CLAUDE_COUNT_MODEL", "claude-opus-5-5")

_claude_client = None
_claude_overhead: dict[str, int] = {}


def _client():
    global _claude_client
    if _claude_client is None:
        import anthropic

        _claude_client = anthropic.Anthropic()
    return _claude_client


def _raw_claude_count(text: str, model: str) -> int:
    return _client().messages.count_tokens(
        model=model,
        messages=[{"role": "user", "content": text}],
    ).input_tokens


def count_claude_tokens(text: str, model: str | None = None) -> int:
    """Count tokens with Claude's tokenizer, minus the message-wrapper overhead.

    Raises if the SDK or credentials are missing; callers decide the fallback.
    """
    if not text:
        return 0
    model = model or CLAUDE_COUNT_MODEL
    if model not in _claude_overhead:
        _claude_overhead[model] = _raw_claude_count("a", model) - 1
    return max(1, _raw_claude_count(text, model) - _claude_overhead[model])
