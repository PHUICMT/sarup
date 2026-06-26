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

# Headers we must not forward verbatim (hop-by-hop / recomputed by httpx).
_DROP_REQUEST_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
_DROP_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding", "connection"}

app = FastAPI(title="sarup-proxy")


def _maybe_compress_body(raw: bytes, path: str) -> bytes:
    """Compression seam. PHASE 1: identity passthrough.

    PHASE 2 will, when COMPRESS_ENABLED and this is /v1/messages, parse the JSON,
    walk `messages`, compress large Thai text blocks via sarup.compressor.compress
    (caching originals in the store), and re-serialize. Kept verbatim for now so
    the proxy provably changes nothing until compression is explicitly turned on.
    """
    if not COMPRESS_ENABLED or not path.endswith("/v1/messages"):
        return raw
    # --- PHASE 2 placeholder (intentionally inert until implemented & tested) ---
    # from .compressor import compress
    # from .store import CompressionStore
    # data = json.loads(raw); ... compress old Thai blocks ...; return json.dumps(data).encode()
    return raw


def _filter_headers(headers, drop: set[str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in drop}


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {"ok": True, "upstream": UPSTREAM, "compress": COMPRESS_ENABLED, "mode": "passthrough"}
    )


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(full_path: str, request: Request) -> Response:
    raw = await request.body()
    raw = _maybe_compress_body(raw, "/" + full_path)

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

        return StreamingResponse(
            body_iter(),
            status_code=upstream.status_code,
            headers=_filter_headers(upstream.headers, _DROP_RESPONSE_HEADERS),
            media_type=upstream.headers.get("content-type"),
        )

    try:
        upstream = await client.request(request.method, url, headers=fwd_headers, content=raw)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=_filter_headers(upstream.headers, _DROP_RESPONSE_HEADERS),
            media_type=upstream.headers.get("content-type"),
        )
    finally:
        await client.aclose()


def main() -> None:
    import uvicorn

    print(f"sarup-proxy → {UPSTREAM}  (compress={COMPRESS_ENABLED}, port={PORT})")
    print(f"Point your client at:  ANTHROPIC_BASE_URL=http://localhost:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
