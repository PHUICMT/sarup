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
