"""Proxy smoke tests — app loads, health reports passthrough, compression seam is inert."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from sarup import proxy


def test_health_reports_passthrough():
    client = TestClient(proxy.app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["mode"] == "passthrough"
    assert "upstream" in body


def test_compression_seam_is_identity_by_default():
    # Phase 1: the seam must change nothing (passthrough), even for /v1/messages.
    raw = b'{"messages":[{"role":"user","content":"\xe0\xb8\xaa\xe0\xb8\xa7\xe0\xb8\xb1\xe0\xb8\x94\xe0\xb8\x94\xe0\xb8\xb5"}]}'
    assert proxy._maybe_compress_body(raw, "/v1/messages") == raw
    assert proxy._maybe_compress_body(raw, "/v1/other") == raw
