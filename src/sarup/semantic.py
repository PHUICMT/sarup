"""Embedding-based extractive compression (semantic mode).

Scores sentences by *centrality* — how representative each sentence is of the
whole document (mean cosine similarity to the rest) — and suppresses
near-duplicates by cosine similarity. This catches paraphrased repetition that
the TF-IDF / n-gram path misses, and ranks by meaning rather than surface
word overlap.

Like everything in the LLM layer this is optional: if embeddings can't be
produced it returns None and the caller falls back to TF-IDF. Output is always
a verbatim subset of the input sentences — no paraphrasing — so it stays
lossless-at-selection and fully recoverable via the store.
"""

from __future__ import annotations

import math
from typing import Optional

from .llm import embed
from .thai import split_sentences

# Centrality + dedup are O(N²) in sentence count. Above this, fall back to the
# linear TF-IDF path instead of pinning CPU/RAM on a pathological input.
MAX_SENTENCES = 400


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def semantic_compress(
    text: str,
    target_ratio: float = 0.5,
    query: str = "",
    near_dup_threshold: float = 0.92,
    model: Optional[str] = None,
) -> Optional[str]:
    """Extractive compression using sentence embeddings.

    Returns the compressed subset, or None if embeddings are unavailable
    (caller should fall back to TF-IDF).
    """
    sentences = split_sentences(text)
    if len(sentences) < 4 or len(sentences) > MAX_SENTENCES:
        return None  # too short to help, or too large for the O(N²) passes

    inputs = list(sentences)
    if query:
        inputs.append(query)

    vectors = embed(inputs, model=model)
    if not vectors:
        return None

    sent_vecs = vectors[: len(sentences)]
    query_vec = vectors[len(sentences)] if query else None

    N = len(sentences)

    # Centrality: mean similarity of each sentence to all others.
    scores: list[float] = []
    for i in range(N):
        sims = [_cosine(sent_vecs[i], sent_vecs[j]) for j in range(N) if j != i]
        centrality = sum(sims) / len(sims) if sims else 0.0
        if query_vec is not None:
            centrality += 1.5 * _cosine(sent_vecs[i], query_vec)  # relevance boost
        scores.append(centrality)

    # Position bias — first/last often carry framing.
    scores[0] = max(scores[0], max(scores) * 0.85)
    scores[-1] = max(scores[-1], max(scores) * 0.75)

    k = max(2, round(N * target_ratio))
    ranked = sorted(range(N), key=lambda i: scores[i], reverse=True)

    kept: list[int] = []
    for idx in ranked:
        if len(kept) >= k:
            break
        # Cosine near-duplicate suppression against already-kept sentences.
        if any(_cosine(sent_vecs[idx], sent_vecs[j]) > near_dup_threshold for j in kept):
            continue
        kept.append(idx)

    kept.sort()
    return "\n".join(sentences[i] for i in kept)
