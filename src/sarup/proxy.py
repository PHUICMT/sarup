"""Sarup proxy — Headroom-style middleware for the Anthropic Messages API.

Point a client at this proxy via `ANTHROPIC_BASE_URL=http://localhost:8788` and
it forwards every request to the real Anthropic API, optionally compressing
large Thai content in the message history on the way through (reusing
`sarup.compressor`), and caching originals in the same store the MCP server uses.

PHASE 1 (default): pure **passthrough** — forward + stream verbatim, change
nothing. This is the risky part (faithful streaming/auth proxy); prove it never
breaks Claude Code before enabling compression.

PHASE 2 (opt-in, SARUP_PROXY_COMPRESS=1): compress old / tool-result Thai blocks
in `messages` before forwarding. The seam is `_maybe_compress_body()` below.

Run:
    sarup-proxy                       # listens on :8788, upstream api.anthropic.com
    ANTHROPIC_BASE_URL=http://localhost:8788 claude   # route Claude Code through it

Needs the [proxy] extra:  pip install -e ".[proxy]"
"""

from __future__ import annotations

import json
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

UPSTREAM = os.environ.get("SARUP_PROXY_UPSTREAM", "https://api.anthropic.com").rstrip("/")
COMPRESS_ENABLED = os.environ.get("SARUP_PROXY_COMPRESS", "0") == "1"
PORT = int(os.environ.get("SARUP_PROXY_PORT", "8788"))
# Hot-path defaults: offline extractive (~1ms), only big Thai blocks.
PROXY_MODE = os.environ.get("SARUP_PROXY_MODE", "extractive")
PROXY_MIN_CHARS = int(os.environ.get("SARUP_PROXY_MIN_CHARS", "2000"))
# Prompt-cache safety: never compress the last N messages (the "settle window").
# Older turns are compressed deterministically (extractive) → the compressed
# prefix is byte-stable across requests, so Anthropic prompt-cache can still hit;
# only the single message aging past the window churns once per turn. Larger N =
# more cache-friendly, fewer savings. Use mode=extractive to keep it deterministic.
PROXY_KEEP_RECENT = max(1, int(os.environ.get("SARUP_PROXY_KEEP_RECENT", "4")))

_store = None
_total_saved = 0  # cumulative tokens saved this run (for the tray UI / stats)


def total_saved() -> int:
    return _total_saved

# Headers we must not forward verbatim (hop-by-hop / recomputed by httpx).
_DROP_REQUEST_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
_DROP_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding", "connection"}

app = FastAPI(title="sarup-proxy")


def _get_store():
    global _store
    if _store is None:
        from .store import CompressionStore
        _store = CompressionStore(db_path=os.environ.get("SARUP_DB_PATH"))
    return _store


def _compress_text(text: str) -> tuple[str, int]:
    """Compress one Thai text block; cache the original. Returns (text, tokens_saved).

    No-op (returns the input) unless it's large, Thai, and actually compresses.
    The original is cached in the SAME store the MCP server reads, so the model
    can recover it via sarup_retrieve(hash) — that's why this is safe to be lossy.
    """
    from .thai import is_thai
    from .compressor import compress

    if not text or len(text) < PROXY_MIN_CHARS or not is_thai(text):
        return text, 0
    result = compress(text, mode=PROXY_MODE)
    if result.tokens_saved <= 0 or result.compressed == text:
        return text, 0
    h = _get_store().store(text, result.compressed, result.original_tokens, result.compressed_tokens)
    footer = (
        f"\n\n[sarup: compressed {result.savings_percent}% "
        f"({result.original_tokens}→{result.compressed_tokens} tok); original cached as "
        f"hash '{h}' — call sarup_retrieve(hash='{h}') to recover full content]"
    )
    return result.compressed + footer, result.tokens_saved


def _compress_message(m: dict) -> int:
    """Compress eligible text inside one message in place. Returns tokens saved."""
    saved = 0
    content = m.get("content")
    if isinstance(content, str):
        m["content"], s = _compress_text(content)
        return s
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("cache_control"):
                continue  # never touch explicit prompt-cache anchors
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                block["text"], s = _compress_text(block["text"])
                saved += s
            elif btype == "tool_result":
                tc = block.get("content")
                if isinstance(tc, str):
                    block["content"], s = _compress_text(tc)
                    saved += s
                elif isinstance(tc, list):
                    for tb in tc:
                        if isinstance(tb, dict) and tb.get("type") == "text" and isinstance(tb.get("text"), str):
                            tb["text"], s = _compress_text(tb["text"])
                            saved += s
    return saved


def _maybe_compress_body(raw: bytes, path: str) -> tuple[bytes, int]:
    """Compress large Thai blocks in older turns of a /v1/messages request.

    Skips the LAST message (current turn — kept verbatim), the system prompt, and
    any block with `cache_control` (prompt-cache anchors). Returns (body, saved).
    Any failure returns the body unchanged — a proxy must never break the request.
    """
    if not COMPRESS_ENABLED or not path.endswith("/v1/messages"):
        return raw, 0
    try:
        data = json.loads(raw)
        msgs = data.get("messages")
        if not isinstance(msgs, list) or len(msgs) <= PROXY_KEEP_RECENT:
            return raw, 0
        # Keep the last PROXY_KEEP_RECENT messages verbatim (cache settle window).
        targets = msgs[:-PROXY_KEEP_RECENT]
        saved = sum(_compress_message(m) for m in targets if isinstance(m, dict))
        if saved <= 0:
            return raw, 0
        global _total_saved
        _total_saved += saved
        return json.dumps(data, ensure_ascii=False).encode("utf-8"), saved
    except Exception:
        return raw, 0  # never break the request


def _filter_headers(headers, drop: set[str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in drop}


@app.get("/health")
async def health() -> JSONResponse:
    # "mode" reports what the proxy actually does to request bodies: the active
    # compression mode when enabled, else plain passthrough.
    mode = PROXY_MODE if COMPRESS_ENABLED else "passthrough"
    return JSONResponse(
        {
            "ok": True,
            "upstream": UPSTREAM,
            "compress": COMPRESS_ENABLED,
            "mode": mode,
            "min_chars": PROXY_MIN_CHARS,
            "keep_recent": PROXY_KEEP_RECENT,
            "tokens_saved": _total_saved,
        }
    )


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(full_path: str, request: Request) -> Response:
    raw = await request.body()
    raw, saved = _maybe_compress_body(raw, "/" + full_path)

    url = f"{UPSTREAM}/{full_path}"
    if request.url.query:
        url += f"?{request.url.query}"
    fwd_headers = _filter_headers(request.headers, _DROP_REQUEST_HEADERS)

    is_stream = b'"stream":true' in raw or b'"stream": true' in raw
    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))

    if is_stream:
        req = client.build_request(request.method, url, headers=fwd_headers, content=raw)
        upstream = await client.send(req, stream=True)

        async def body_iter():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        resp_headers = _filter_headers(upstream.headers, _DROP_RESPONSE_HEADERS)
        resp_headers["x-sarup-tokens-saved"] = str(saved)
        return StreamingResponse(
            body_iter(),
            status_code=upstream.status_code,
            headers=resp_headers,
            media_type=upstream.headers.get("content-type"),
        )

    try:
        upstream = await client.request(request.method, url, headers=fwd_headers, content=raw)
        resp_headers = _filter_headers(upstream.headers, _DROP_RESPONSE_HEADERS)
        resp_headers["x-sarup-tokens-saved"] = str(saved)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=resp_headers,
            media_type=upstream.headers.get("content-type"),
        )
    finally:
        await client.aclose()


def main() -> None:
    import uvicorn

    print(f"sarup-proxy -> {UPSTREAM}  (compress={COMPRESS_ENABLED}, port={PORT})")
    print(f"Point your client at:  ANTHROPIC_BASE_URL=http://localhost:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
