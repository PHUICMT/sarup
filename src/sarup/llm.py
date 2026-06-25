"""Optional local-LLM backend via Ollama.

Everything here is *opt-in* and degrades gracefully: if Ollama is not running
or a model is missing, callers fall back to the deterministic extractive
pipeline. Because every compression is backed by the retrieval store
(byte-for-byte recoverable), even lossy abstractive output keeps the
end-to-end accuracy guarantee intact.

Uses only the stdlib (urllib) — no extra dependency, works fully offline.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

# Default abstractive model. Gemma 3 12B is SCB10X's own validated base for
# Thai (best size-to-performance, doesn't "overthink" the way Qwen3 does) and
# runs on a 12GB GPU. Typhoon2.1 would be ideal but its current GGUF chat
# template crashes llama-server in recent Ollama. Alternatives: qwen3:8b (fast,
# disable thinking), gemma4. Override with SARUP_ABSTRACTIVE_MODEL.
# NOTE: abstractive is the slowest mode (~10-20s/doc); 'semantic' usually gives
# a higher ratio far faster — prefer it for everyday use.
ABSTRACTIVE_MODEL = os.environ.get("SARUP_ABSTRACTIVE_MODEL", "gemma3:12b")
EMBED_MODEL = os.environ.get("SARUP_EMBED_MODEL", "nomic-embed-text")

# Short connect timeout for the availability probe; longer for generation.
_PROBE_TIMEOUT = float(os.environ.get("SARUP_OLLAMA_PROBE_TIMEOUT", "1.0"))
_GEN_TIMEOUT = float(os.environ.get("SARUP_OLLAMA_GEN_TIMEOUT", "60.0"))
_EMBED_TIMEOUT = float(os.environ.get("SARUP_OLLAMA_EMBED_TIMEOUT", "30.0"))


def _post(path: str, body: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_available(timeout: float = _PROBE_TIMEOUT) -> bool:
    """True if an Ollama server answers on OLLAMA_HOST."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def list_models(timeout: float = _PROBE_TIMEOUT) -> list[str]:
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def generate(prompt: str, model: str | None = None, timeout: float = _GEN_TIMEOUT) -> str | None:
    """Run a one-shot generation. Returns None on any failure (caller falls back)."""
    model = model or ABSTRACTIVE_MODEL
    try:
        out = _post(
            "/api/generate",
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "top_p": 0.9},
            },
            timeout=timeout,
        )
        text = (out.get("response") or "").strip()
        return text or None
    except Exception:
        return None


def embed(texts: list[str], model: str | None = None, timeout: float = _EMBED_TIMEOUT) -> list[list[float]] | None:
    """Embed a batch of texts. Returns None on any failure (caller falls back)."""
    model = model or EMBED_MODEL
    if not texts:
        return []
    try:
        out = _post(
            "/api/embed",
            {"model": model, "input": texts},
            timeout=timeout,
        )
        embeddings = out.get("embeddings")
        if embeddings and len(embeddings) == len(texts):
            return embeddings
        return None
    except Exception:
        return None
