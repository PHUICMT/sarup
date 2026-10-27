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
import time
import urllib.error
import urllib.request

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

# Abstractive model: SARUP_ABSTRACTIVE_MODEL if set, else the first of these
# that is installed. Gemma 4 with thinking off scored 91% on the Thai QA eval
# (bench/eval_qa.py, Oct 2026) in both sizes; 12b is first because it is the
# smaller download (7.6 GB vs e4b's 9.6 GB) and rewrites faster (~8 s vs ~12 s).
# Gemma 3 12B is SCB10X's validated Thai base. Typhoon2.1 would be ideal but
# its GGUF chat template crashes llama-server in recent Ollama.
# NOTE: abstractive is the slowest mode (~8-12s/doc), so not for the hook.
GEN_PREFS = ["gemma4:12b", "gemma4:e4b", "gemma4", "gemma3:12b", "gemma3", "qwen3:8b", "qwen3"]
ABSTRACTIVE_MODEL = os.environ.get("SARUP_ABSTRACTIVE_MODEL") or GEN_PREFS[0]
_resolved_model: str | None = None
EMBED_MODEL = os.environ.get("SARUP_EMBED_MODEL", "nomic-embed-text")

# Short connect timeout for the availability probe; longer for generation.
_PROBE_TIMEOUT = float(os.environ.get("SARUP_OLLAMA_PROBE_TIMEOUT", "1.0"))
_GEN_TIMEOUT = float(os.environ.get("SARUP_OLLAMA_GEN_TIMEOUT", "60.0"))
_EMBED_TIMEOUT = float(os.environ.get("SARUP_OLLAMA_EMBED_TIMEOUT", "30.0"))

# On Windows a refused connection takes ~2 s to report, and every Ollama-backed
# mode tries embed/list/generate in turn. After one refusal, treat Ollama as
# absent for a while so the rest fall back to extractive at once.
_DOWN_SECONDS = 60.0
_down_until = 0.0


def _urlopen(req: urllib.request.Request, timeout: float):
    global _down_until
    if time.monotonic() < _down_until:
        raise ConnectionRefusedError("Ollama was unreachable moments ago")
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, ConnectionError) as e:
        if isinstance(getattr(e, "reason", e), ConnectionError):
            _down_until = time.monotonic() + _DOWN_SECONDS
        raise


def _post(path: str, body: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_available(timeout: float = _PROBE_TIMEOUT) -> bool:
    """True if an Ollama server answers on OLLAMA_HOST."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with _urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def list_models(timeout: float = _PROBE_TIMEOUT) -> list[str]:
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with _urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def pick_model(installed: list[str], prefs: list[str]) -> str | None:
    """First preference that matches an installed model name (tag optional)."""
    for p in prefs:
        for m in installed:
            if m == p or m.startswith(p + ":") or m.split(":")[0] == p:
                return m
    return None


def abstractive_model() -> str:
    """The rewrite model: the env override, else the best installed, else the default."""
    global _resolved_model
    if os.environ.get("SARUP_ABSTRACTIVE_MODEL"):
        return ABSTRACTIVE_MODEL
    if _resolved_model is None:
        installed = list_models()
        if not installed:
            return ABSTRACTIVE_MODEL  # Ollama down: don't cache, retry next call
        _resolved_model = pick_model(installed, GEN_PREFS) or ABSTRACTIVE_MODEL
    return _resolved_model


def backend_status() -> dict:
    """What the Ollama-backed modes can use right now."""
    installed = list_models()
    override = os.environ.get("SARUP_ABSTRACTIVE_MODEL")
    rewrite = (override if override in installed else None) if override else pick_model(installed, GEN_PREFS)
    return {
        "ollama": bool(installed) or ollama_available(),
        "embed_model": pick_model(installed, [EMBED_MODEL]),
        "rewrite_model": rewrite,
    }


_NEEDS = {
    "semantic": ("embed",),
    "abstractive": ("rewrite",),
    "pipeline": ("embed", "rewrite"),
}


def setup_hint(mode: str) -> str | None:
    """How to get `mode` working, or None if nothing it needs is missing.

    'auto' is left out on purpose: falling back to extractive is what it promises.
    """
    needs = _NEEDS.get(mode)
    if not needs:
        return None
    status = backend_status()
    pulls = []
    if "embed" in needs and not status["embed_model"]:
        pulls.append(f"ollama pull {EMBED_MODEL}")
    if "rewrite" in needs and not status["rewrite_model"]:
        pulls.append(f"ollama pull {os.environ.get('SARUP_ABSTRACTIVE_MODEL') or GEN_PREFS[0]}")
    if not pulls:
        return None
    if not status["ollama"]:
        return (f"Mode '{mode}' needs Ollama, which isn't running here, so extractive ran "
                f"instead. Install it from https://ollama.com, start it, then run: "
                + "; ".join(pulls))
    return f"Mode '{mode}' fell back to extractive: Ollama is missing a model. Run: " + "; ".join(pulls)


def generate(prompt: str, model: str | None = None, timeout: float = _GEN_TIMEOUT) -> str | None:
    """Run a one-shot generation. Returns None on any failure (caller falls back)."""
    model = model or abstractive_model()
    try:
        out = _post(
            "/api/generate",
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                # gemma4/qwen3 think by default: 3-4x slower for no gain on a
                # rewrite. Models without thinking ignore the flag.
                "think": False,
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
