"""Thai NLP utilities: tokenization, sentence splitting, TF-IDF scoring."""

from __future__ import annotations

import math
import re

try:
    from pythainlp.tokenize import word_tokenize as _th_word_tokenize
    from pythainlp.tokenize import sent_tokenize as _th_sent_tokenize

    PYTHAINLP_AVAILABLE = True
except ImportError:  # pragma: no cover
    PYTHAINLP_AVAILABLE = False

# Thai Unicode range
_THAI_RE = re.compile(r"[฀-๿]")

# Sentence boundary patterns (works for both Thai and English)
_SENT_SPLIT_RE = re.compile(
    r"(?<=[.!?\nฯ])\s+"  # after . ! ? newline ฯ
    r"|[\n\r]{2,}"             # blank lines
)

# Thai clause connectors, used to sub-split very long sentences. Zero-width, so
# the connector stays at the head of its clause and the text stays verbatim.
_THAI_CONNECTORS = re.compile(
    r"(?<=\s)(?=(?:และ|หรือ|แต่|เพราะ|จึง|ดังนั้น|อย่างไรก็ตาม|นอกจากนี้|ทั้งนี้|โดย)\s)"
)

# Marks where extractive compression dropped sentences. Without it, a nearby
# surviving fact can pass for the answer (eval: "5 years → how many days?"
# answered "12" because the "15" sentence was gone). With it, the reader knows
# something is missing and retrieves the original instead.
GAP_MARKER = "[…]"

# Specific facts: numbers (Arabic or Thai digits) and Latin identifiers such as
# X-Api-Key or PostgreSQL. These are what questions ask about, so sentences
# carrying them outrank general statements of the same TF-IDF weight.
_FACT_RE = re.compile(r"[0-9๐-๙]+(?:[.,:/][0-9๐-๙]+)*|[A-Za-z][\w.\-]*")
_FACT_BOOST = 0.35
_FACT_BOOST_CAP = 3


def fact_boost(sentence: str) -> float:
    """Score multiplier for a sentence by how many specific facts it carries."""
    return 1.0 + _FACT_BOOST * min(len(_FACT_RE.findall(sentence)), _FACT_BOOST_CAP)


def join_kept(sentences: list[str], kept: list[int]) -> str:
    """Rejoin kept sentences in order, with GAP_MARKER wherever some were dropped."""
    out: list[str] = []
    prev = -1
    for i in sorted(kept):
        if i != prev + 1:
            out.append(GAP_MARKER)
        out.append(sentences[i])
        prev = i
    if prev != len(sentences) - 1:
        out.append(GAP_MARKER)
    return "\n".join(out)


# Stop-words to ignore in scoring (Thai + English)
_THAI_STOPWORDS: set[str] = {
    "ที่", "ใน", "และ", "ของ", "มี", "เป็น", "การ", "ได้", "จาก", "ไป",
    "มา", "ว่า", "กับ", "จะ", "ก็", "แล้ว", "ใช้", "เพื่อ", "โดย", "ให้",
    "นั้น", "นี้", "คือ", "เมื่อ", "หรือ", "แต่", "ซึ่ง", "อยู่", "ยัง",
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "be", "or", "and", "this",
}


def is_thai(text: str) -> bool:
    """True if Thai characters make up >10% of the text."""
    if not text:
        return False
    thai_count = len(_THAI_RE.findall(text))
    return thai_count / max(len(text), 1) > 0.10


def tokenize_words(text: str) -> list[str]:
    """
    Word-tokenize text. Uses PyThaiNLP for Thai content (newmm engine),
    falls back to whitespace split for pure English / no PyThaiNLP.
    """
    if PYTHAINLP_AVAILABLE and is_thai(text):
        tokens = _th_word_tokenize(text, engine="newmm", keep_whitespace=False)
        return [t for t in tokens if t.strip()]
    return [t for t in text.split() if t]


def split_sentences(text: str) -> list[str]:
    """
    Split text into sentence-level segments.
    Uses PyThaiNLP for Thai paragraphs, regex fallback otherwise.
    """
    # Primary: newline-based splits (works well for context/logs/docs)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 3:
        return lines

    # Secondary: PyThaiNLP sentence tokenizer for dense Thai prose
    if PYTHAINLP_AVAILABLE and is_thai(text):
        try:
            sents = _th_sent_tokenize(text)
            if sents and len(sents) >= 2:
                # Sub-split very long sentences at clause boundaries
                result: list[str] = []
                for s in sents:
                    if len(s) > 120:
                        parts = _THAI_CONNECTORS.split(s)
                        result.extend(p.strip() for p in parts if p.strip())
                    else:
                        if s.strip():
                            result.append(s.strip())
                return result
        except Exception:
            pass

    # Fallback: regex sentence splitter
    parts = _SENT_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _shingle_overlap(a: list[str], b: list[str], n: int = 2) -> float:
    """Jaccard overlap on n-grams between two token lists."""
    def shingles(tokens: list[str]) -> set[tuple[str, ...]]:
        if len(tokens) < n:
            return {tuple(tokens)}
        return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}

    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def tfidf_compress(
    text: str,
    target_ratio: float = 0.5,
    query: str = "",
    near_dup_threshold: float = 0.72,
) -> str:
    """
    Extractive compression via TF-IDF sentence scoring.

    Keeps top-scoring sentences (by TF-IDF + optional query boost) in original
    order. Near-duplicate sentences are suppressed. Pure selection — no
    paraphrasing.

    Args:
        text: Input text (Thai, English, or mixed).
        target_ratio: Fraction of sentences to keep (0.3–0.7).
        query: Optional context/query for relevance boost.
        near_dup_threshold: Jaccard threshold above which to drop duplicates.

    Returns:
        Compressed text: original sentences joined by newlines, with GAP_MARKER
        where sentences were dropped.
    """
    sentences = split_sentences(text)

    if len(sentences) < 4:
        return text  # Too short — compression would lose too much signal

    tokenized: list[list[str]] = [
        [t.lower() for t in tokenize_words(s) if t not in _THAI_STOPWORDS and len(t) > 1]
        for s in sentences
    ]

    N = len(sentences)

    # Document frequency
    df: dict[str, int] = {}
    for tokens in tokenized:
        for word in set(tokens):
            df[word] = df.get(word, 0) + 1

    # Query boost terms
    query_terms: set[str] = set()
    if query:
        query_terms = {
            t.lower()
            for t in tokenize_words(query)
            if t not in _THAI_STOPWORDS and len(t) > 1
        }

    # Score each sentence
    scores: list[float] = []
    for sentence, tokens in zip(sentences, tokenized):
        if not tokens:
            scores.append(0.0)
            continue

        # Term frequency (normalized)
        tf: dict[str, float] = {}
        for word in tokens:
            tf[word] = tf.get(word, 0.0) + 1.0
        max_tf = max(tf.values())

        score = 0.0
        for word, count in tf.items():
            idf = math.log((N + 1) / (df.get(word, 0) + 1)) + 1.0
            tfidf = (count / max_tf) * idf
            if word in query_terms:
                tfidf *= 2.5  # relevance boost
            score += tfidf

        # Normalize by sentence length to avoid bias toward long sentences
        scores.append(score / math.sqrt(len(tokens)) * fact_boost(sentence))

    # Position bias: first and last sentences are often important
    if N >= 4:
        scores[0] = max(scores[0], max(scores) * 0.85)
        scores[-1] = max(scores[-1], max(scores) * 0.75)

    # How many sentences to keep
    k = max(2, round(N * target_ratio))

    # Sort by score descending, apply near-dup suppression
    ranked = sorted(range(N), key=lambda i: scores[i], reverse=True)

    kept_indices: list[int] = []
    kept_tokens: list[list[str]] = []

    for idx in ranked:
        if len(kept_indices) >= k:
            break
        tokens = tokenized[idx]
        # Check near-duplicate against already-kept sentences
        is_dup = any(
            _shingle_overlap(tokens, kept, n=2) > near_dup_threshold
            for kept in kept_tokens
        )
        if not is_dup:
            kept_indices.append(idx)
            kept_tokens.append(tokens)

    return join_kept(sentences, kept_indices)
