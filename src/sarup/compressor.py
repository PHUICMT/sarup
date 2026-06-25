"""Compression pipeline and content router for Sarup."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .thai import is_thai, tfidf_compress, PYTHAINLP_AVAILABLE
from .tokens import count_tokens, token_method  # noqa: F401  (re-exported)
from .llm import generate as _llm_generate, ABSTRACTIVE_MODEL
from .semantic import semantic_compress

# Compression modes for the prose path.
MODE_EXTRACTIVE = "extractive"   # TF-IDF, deterministic, offline (default)
MODE_SEMANTIC = "semantic"       # embedding centrality + cosine dedup (ollama)
MODE_ABSTRACTIVE = "abstractive"  # local LLM rewrite (ollama)
MODE_PIPELINE = "pipeline"       # cascade semantic -> abstractive for max savings
MODE_AUTO = "auto"               # semantic if ollama up, else extractive
VALID_MODES = {
    MODE_EXTRACTIVE, MODE_SEMANTIC, MODE_ABSTRACTIVE, MODE_PIPELINE, MODE_AUTO
}


# ─── Token estimation ─────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Token count via the real tokenizer (tiktoken) when available.

    Delegates to :func:`sarup.tokens.count_tokens`. Both original and
    compressed text are measured the same way, so the reported savings ratio
    is accurate rather than a byte-length guess.
    """
    return count_tokens(text)


# ─── Result type ──────────────────────────────────────────────────────────────

@dataclass
class CompressionResult:
    compressed: str
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    savings_percent: float
    transforms: list[str] = field(default_factory=list)
    lossy: bool = True


# ─── Content detection ────────────────────────────────────────────────────────

_CODE_FENCE_RE = re.compile(r"(```[^\n]*\n.*?```)", re.DOTALL)
_JSON_START_RE = re.compile(r"^\s*[\[{]")
_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}|INFO |ERROR |WARN |DEBUG |\[[\d:T.Z+-]+\])",
    re.MULTILINE,
)


def _looks_like_json(text: str) -> bool:
    return bool(_JSON_START_RE.match(text)) and len(text) > 40


def _looks_like_logs(text: str) -> bool:
    matches = _LOG_LINE_RE.findall(text)
    lines = text.count("\n") + 1
    return lines > 5 and len(matches) / max(lines, 1) > 0.4


def _has_code_fences(text: str) -> bool:
    return bool(_CODE_FENCE_RE.search(text))


# ─── Individual compressors ───────────────────────────────────────────────────

def _compress_json(text: str) -> tuple[str, list[str]]:
    """Compact JSON — removes all insignificant whitespace. Lossless."""
    try:
        data = json.loads(text)
        out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return out, ["json_compact"]
    except (json.JSONDecodeError, ValueError):
        return text, []


def _compress_logs(text: str, target_ratio: float) -> tuple[str, list[str]]:
    """
    Log compression: deduplicate repeated lines, keep first+last occurrence
    of each unique prefix, apply TF-IDF on remainder.
    """
    lines = text.splitlines()
    if len(lines) < 6:
        return text, []

    seen: dict[str, int] = {}
    deduped: list[str] = []
    for ln in lines:
        # Key on first 60 chars (ignores trailing variable data like timestamps)
        key = ln[:60]
        if key not in seen:
            seen[key] = 0
        seen[key] += 1
        if seen[key] <= 2:  # keep first two occurrences
            deduped.append(ln)
        elif seen[key] == 3:
            deduped.append(f"  ... ({seen[key]-2} similar lines omitted) ...")

    k = max(4, round(len(deduped) * target_ratio))
    result = deduped[:k]
    if len(deduped) > k:
        result.append(f"... [{len(deduped) - k} more lines]")
    return "\n".join(result), ["log_dedup", "log_truncate"]


def _compress_mixed(
    text: str, target_ratio: float, query: str = "", mode: str = MODE_EXTRACTIVE
) -> tuple[str, list[str]]:
    """
    Split text on code fences. Compress prose segments, preserve code verbatim.
    """
    transforms: set[str] = set()
    parts: list[str] = []
    last = 0

    for m in _CODE_FENCE_RE.finditer(text):
        prose = text[last : m.start()]
        if prose.strip():
            compressed_prose, t = _compress_prose(prose, target_ratio, query=query, mode=mode)
            parts.append(compressed_prose)
            transforms.update(t)
        parts.append(m.group(0))
        transforms.add("code_preserved")
        last = m.end()

    remainder = text[last:]
    if remainder.strip():
        compressed_prose, t = _compress_prose(remainder, target_ratio, query=query, mode=mode)
        parts.append(compressed_prose)
        transforms.update(t)

    return "\n".join(p for p in parts if p), sorted(transforms)


_ABSTRACTIVE_PROMPT_TH = (
    "ย่อข้อความต่อไปนี้ให้สั้นที่สุดเท่าที่ทำได้ โดยคงใจความสำคัญทั้งหมดไว้ครบถ้วน "
    "ตัดคำฟุ่มเฟือย คำซ้ำ คำสุภาพ และคำเชื่อมที่ไม่จำเป็นออก "
    "ห้ามเพิ่มข้อมูลใหม่ ห้ามตีความ ตอบกลับเฉพาะข้อความที่ย่อแล้วเท่านั้น "
    "ห้ามมีคำนำหรือคำอธิบายใดๆ\n\nข้อความ:\n{text}\n\nข้อความที่ย่อแล้ว:"
)
_ABSTRACTIVE_PROMPT_EN = (
    "Compress the following text as much as possible while preserving every "
    "key fact. Remove filler, repetition, and hedging. Do not add or infer "
    "anything. Reply with ONLY the compressed text, no preamble.\n\n"
    "Text:\n{text}\n\nCompressed:"
)


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def _compress_abstractive(text: str, target_ratio: float) -> tuple[str, list[str]] | None:
    """Local-LLM rewrite via Ollama. Returns None if unavailable or unhelpful."""
    prompt = (_ABSTRACTIVE_PROMPT_TH if is_thai(text) else _ABSTRACTIVE_PROMPT_EN).format(text=text)
    out = _llm_generate(prompt)
    if not out:
        return None
    out = _THINK_RE.sub("", out).strip()  # drop reasoning models' <think> blocks
    if not out:
        return None
    # Accept only if it truly compressed by tokens (not chars — Thai differs)
    # and isn't pathologically short.
    if count_tokens(out) >= count_tokens(text) or len(out) < len(text) * 0.05:
        return None
    return out, ["abstractive_llm", f"model:{ABSTRACTIVE_MODEL}"]


def _semantic_try(text: str, target_ratio: float, query: str) -> tuple[str, list[str]] | None:
    """Semantic extractive (embeddings). None if unavailable/unhelpful."""
    sem = semantic_compress(text, target_ratio=target_ratio, query=query)
    if sem is None or sem == text:
        return None
    transforms = ["semantic_extractive", "embeddings"]
    if is_thai(text):
        transforms.append("thai")
    return sem, transforms


def _extractive(text: str, target_ratio: float, query: str) -> tuple[str, list[str]]:
    """TF-IDF extractive — deterministic, offline. Always available."""
    result = tfidf_compress(text, target_ratio=target_ratio, query=query)
    if result == text:
        return text, ["noop"]
    transforms = []
    if is_thai(text):
        transforms.append("thai_extractive")
        if PYTHAINLP_AVAILABLE:
            transforms.append("pythainlp_tokenizer")
    else:
        transforms.append("en_extractive")
    transforms.append("tfidf_scoring")
    return result, transforms


def _compress_pipeline(text: str, target_ratio: float, query: str) -> tuple[str, list[str]]:
    """Cascade stages for maximum reduction; each stage feeds the next.

    1. Sentence selection (semantic if Ollama up, else TF-IDF) keeps the most
       representative content.
    2. Abstractive rewrite (if Ollama up) further tightens the survivors.

    Every stage is recoverable end-to-end because the *original* is cached in the
    store before any compression — the pipeline only shrinks the working view.
    """
    transforms: list[str] = []

    # Stage 1 — selection.
    stage1 = _semantic_try(text, target_ratio, query) or _extractive(text, target_ratio, query)
    current, t1 = stage1
    if t1 != ["noop"]:
        transforms.extend(t1)

    # Stage 2 — abstractive rewrite of what survived.
    ab = _compress_abstractive(current, target_ratio)
    if ab is not None:
        current, t2 = ab
        transforms.extend(t2)

    if not transforms:
        return text, ["noop"]
    return current, ["pipeline", *transforms]


def _compress_prose(
    text: str,
    target_ratio: float,
    query: str = "",
    mode: str = MODE_EXTRACTIVE,
) -> tuple[str, list[str]]:
    """Compress Thai/English prose using the selected mode, with safe fallback.

    Every Ollama-backed mode degrades to TF-IDF extractive when the backend is
    unavailable, so compression always works offline.
    """
    if mode == MODE_PIPELINE:
        return _compress_pipeline(text, target_ratio, query)

    # auto / semantic → prefer embeddings, fall through to extractive.
    if mode in (MODE_AUTO, MODE_SEMANTIC):
        sem = _semantic_try(text, target_ratio, query)
        if sem is not None:
            return sem
        # Ollama down → extractive below.

    # Explicit abstractive (LLM rewrite); falls through if backend is down.
    if mode == MODE_ABSTRACTIVE:
        ab = _compress_abstractive(text, target_ratio)
        if ab is not None:
            return ab

    return _extractive(text, target_ratio, query)


# ─── Whitespace normalization (lossless) ─────────────────────────────────────

def _normalize_whitespace(text: str) -> tuple[str, list[str]]:
    out = re.sub(r"\n{3,}", "\n\n", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = out.strip()
    return out, ["whitespace_normalize"]


# ─── Main entry point ─────────────────────────────────────────────────────────

def compress(
    content: str,
    target_ratio: float = 0.5,
    lossless: bool = False,
    query: str = "",
    mode: str = MODE_EXTRACTIVE,
) -> CompressionResult:
    """
    Route content to the best compressor and return a CompressionResult.

    Routing priority:
      1. Lossless-only mode → whitespace / JSON compact only
      2. JSON → json_compact (lossless, often 10-30% savings)
      3. Logs → dedup + head truncation
      4. Mixed prose+code → compress prose, preserve code fences verbatim
      5. Pure Thai / English prose → selected mode (extractive/semantic/abstractive)

    Args:
        content:      Text to compress.
        target_ratio: Fraction of prose sentences to keep (0.3–0.7).
        lossless:     If True, only apply whitespace/JSON normalization.
        query:        Optional context query for relevance-aware scoring.
        mode:         Prose compression strategy (see VALID_MODES). Modes other
                      than 'extractive' use the optional Ollama backend and fall
                      back to extractive when it is unavailable.
    """
    if mode not in VALID_MODES:
        mode = MODE_EXTRACTIVE
    original_tokens = estimate_tokens(content)
    stripped = content.strip()

    # ── Lossless path ──────────────────────────────────────────────────────
    if lossless:
        if _looks_like_json(stripped):
            out, transforms = _compress_json(stripped)
        else:
            out, transforms = _normalize_whitespace(content)
        ct = estimate_tokens(out)
        saved = max(0, original_tokens - ct)
        pct = round(saved / original_tokens * 100, 1) if original_tokens else 0.0
        return CompressionResult(
            compressed=out,
            original_tokens=original_tokens,
            compressed_tokens=ct,
            tokens_saved=saved,
            savings_percent=pct,
            transforms=transforms,
            lossy=False,
        )

    # ── JSON ───────────────────────────────────────────────────────────────
    if _looks_like_json(stripped):
        out, transforms = _compress_json(stripped)
        if out != stripped:
            ct = estimate_tokens(out)
            saved = max(0, original_tokens - ct)
            pct = round(saved / original_tokens * 100, 1) if original_tokens else 0.0
            if pct >= 3:  # only return if actually saved something
                return CompressionResult(
                    compressed=out,
                    original_tokens=original_tokens,
                    compressed_tokens=ct,
                    tokens_saved=saved,
                    savings_percent=pct,
                    transforms=transforms,
                    lossy=False,
                )

    # ── Logs ───────────────────────────────────────────────────────────────
    if _looks_like_logs(content):
        out, transforms = _compress_logs(content, target_ratio)
        ct = estimate_tokens(out)
        saved = max(0, original_tokens - ct)
        pct = round(saved / original_tokens * 100, 1) if original_tokens else 0.0
        if pct >= 10:
            return CompressionResult(
                compressed=out,
                original_tokens=original_tokens,
                compressed_tokens=ct,
                tokens_saved=saved,
                savings_percent=pct,
                transforms=transforms,
                lossy=True,
            )

    # ── Mixed prose + code fences ─────────────────────────────────────────
    if _has_code_fences(content):
        out, transforms = _compress_mixed(content, target_ratio, query=query, mode=mode)
    else:
        # ── Pure prose (Thai or English) ─────────────────────────────────
        out, transforms = _compress_prose(content, target_ratio, query=query, mode=mode)

    ct = estimate_tokens(out)
    saved = max(0, original_tokens - ct)
    pct = round(saved / original_tokens * 100, 1) if original_tokens else 0.0

    # If compression provided no benefit, return original unchanged
    if pct < 5 or out == content:
        return CompressionResult(
            compressed=content,
            original_tokens=original_tokens,
            compressed_tokens=original_tokens,
            tokens_saved=0,
            savings_percent=0.0,
            transforms=["noop"],
            lossy=False,
        )

    return CompressionResult(
        compressed=out,
        original_tokens=original_tokens,
        compressed_tokens=ct,
        tokens_saved=saved,
        savings_percent=pct,
        transforms=transforms,
        lossy=True,
    )
