"""Proxy tests — app loads, passthrough by default, phase-2 compresses old Thai turns."""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from sarup import proxy
from sarup.store import CompressionStore

BIG_THAI = "\n".join(
    "การออกแบบและพัฒนาระบบซอฟต์แวร์ขนาดใหญ่ต้องอาศัยการวางแผนสถาปัตยกรรมที่รอบคอบและการทดสอบที่ครอบคลุม %d" % i
    for i in range(30)
)  # ~3k chars, all Thai


def test_health_reports_passthrough():
    r = TestClient(proxy.app).get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_disabled_is_identity():
    raw = b'{"messages":[{"role":"user","content":"hi"}]}'
    assert proxy._maybe_compress_body(raw, "/v1/messages") == (raw, 0)


def test_compresses_old_thai_turn_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(proxy, "COMPRESS_ENABLED", True)
    monkeypatch.setattr(proxy, "_store", None)
    monkeypatch.setenv("SARUP_DB_PATH", str(tmp_path / "proxy.db"))

    body = {
        "model": "claude-opus-4-8",
        "system": BIG_THAI,  # system must NOT be touched
        "messages": [
            {"role": "user", "content": BIG_THAI},          # old turn → compress
            {"role": "assistant", "content": "เข้าใจแล้ว"},
            {"role": "user", "content": BIG_THAI},           # LAST turn → keep verbatim
        ],
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    out, saved = proxy._maybe_compress_body(raw, "/v1/messages")
    data = json.loads(out)

    assert saved > 0
    assert len(out) < len(raw)
    assert data["system"] == BIG_THAI                        # system untouched
    assert data["messages"][2]["content"] == BIG_THAI        # last turn untouched
    first = data["messages"][0]["content"]
    assert first != BIG_THAI and "sarup_retrieve" in first   # old turn compressed

    # Original recoverable from the shared store (what the MCP server reads).
    h = first.split("hash '")[1].split("'")[0]
    assert CompressionStore(db_path=str(tmp_path / "proxy.db")).retrieve(h) == BIG_THAI


def test_cache_control_block_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(proxy, "COMPRESS_ENABLED", True)
    monkeypatch.setattr(proxy, "_store", None)
    monkeypatch.setenv("SARUP_DB_PATH", str(tmp_path / "p.db"))
    body = {"messages": [
        {"role": "user", "content": [
            {"type": "text", "text": BIG_THAI, "cache_control": {"type": "ephemeral"}},
        ]},
        {"role": "user", "content": "last"},
    ]}
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    out, saved = proxy._maybe_compress_body(raw, "/v1/messages")
    assert saved == 0  # cache anchor left alone
    assert json.loads(out)["messages"][0]["content"][0]["text"] == BIG_THAI
