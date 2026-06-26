"""Tray app smoke tests — importable, icon renders, proxy savings counter works."""

import json

import pytest

pytest.importorskip("pystray")
pytest.importorskip("PIL")

from sarup import proxy, tray


def test_tray_imports_and_icon_renders():
    img = tray._make_icon_image()
    assert img.size == (64, 64)
    assert tray._base_url().startswith("http://localhost:")


def test_proxy_tracks_cumulative_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(proxy, "COMPRESS_ENABLED", True)
    monkeypatch.setattr(proxy, "PROXY_KEEP_RECENT", 1)
    monkeypatch.setattr(proxy, "_store", None)
    monkeypatch.setenv("SARUP_DB_PATH", str(tmp_path / "t.db"))

    big = "\n".join("ระบบประมวลผลข้อมูลขนาดใหญ่ต้องการการออกแบบที่คำนึงถึงความเร็วและความถูกต้อง %d" % i
                    for i in range(30))
    body = {"messages": [{"role": "user", "content": big}, {"role": "user", "content": "now"}]}
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")

    before = proxy.total_saved()
    _, saved = proxy._maybe_compress_body(raw, "/v1/messages")
    assert saved > 0
    assert proxy.total_saved() == before + saved
