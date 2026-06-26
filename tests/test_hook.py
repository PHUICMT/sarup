"""Tests for the PostToolUse auto-compression hook (hooks/sarup_hook.py)."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

from sarup.store import CompressionStore

# Load hooks/sarup_hook.py (not an installed package).
_HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "sarup_hook.py"
_spec = importlib.util.spec_from_file_location("sarup_hook", _HOOK_PATH)
sarup_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sarup_hook)

BIG_THAI = "\n".join(
    [
        "ระบบการจัดการข้อมูลในองค์กรปัจจุบันมีความซับซ้อนมากขึ้นเรื่อยๆ",
        "การนำเทคโนโลยีใหม่มาใช้ช่วยให้ทีมงานสามารถทำงานได้อย่างมีประสิทธิภาพ",
        "ฐานข้อมูลขนาดใหญ่ต้องการการดูแลรักษาอย่างสม่ำเสมอเพื่อให้ทำงานได้ดี",
        "นักพัฒนาซอฟต์แวร์ต้องเข้าใจทั้งโครงสร้างข้อมูลและอัลกอริทึมอย่างลึกซึ้ง",
        "การทดสอบซอฟต์แวร์เป็นส่วนสำคัญของกระบวนการพัฒนาที่ขาดไม่ได้",
        "โปรแกรมเมอร์ที่ดีต้องเขียนโค้ดที่อ่านเข้าใจง่ายและบำรุงรักษาได้ในระยะยาว",
        "ระบบ CI CD ช่วยให้การ deploy โค้ดเป็นไปอย่างราบรื่นและลดความผิดพลาด",
        "การทำ code review ช่วยป้องกันบั๊กและเพิ่มคุณภาพของโค้ดโดยรวม",
        "เอกสารประกอบโค้ดที่ดีช่วยให้สมาชิกทีมใหม่เข้าใจระบบได้รวดเร็วขึ้นมาก",
        "การจัดการ dependency อย่างเหมาะสมลดปัญหาความขัดแย้งของไลบรารีในโปรเจค",
    ]
)


def _bash(output: str) -> dict:
    """A PostToolUse payload shaped like Claude Code's real Bash event."""
    return {"tool_name": "Bash", "tool_input": {"command": "x"},
            "tool_response": {"stdout": output, "stderr": ""}}


def test_extract_output_handles_real_shapes():
    """tool_response is an object, not a string — extract per tool shape."""
    assert sarup_hook._extract_output({"stdout": "hi", "stderr": ""}) == "hi"
    assert sarup_hook._extract_output({"stdout": "a", "stderr": "b"}) == "a\nb"
    assert sarup_hook._extract_output({"file": {"content": "doc"}}) == "doc"
    assert sarup_hook._extract_output("plain") == "plain"
    assert sarup_hook._extract_output({}) == ""


def test_small_output_left_unchanged():
    assert sarup_hook.build_hook_output(_bash("short")) is None


def test_code_file_read_is_skipped(monkeypatch):
    monkeypatch.setattr(sarup_hook, "MIN_TOKENS", 10)
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": "d:\\proj\\server.py"},
        "tool_response": {"file": {"content": BIG_THAI}},
    }
    assert sarup_hook.build_hook_output(payload) is None


def test_large_prose_is_compressed_and_recoverable(monkeypatch, tmp_path):
    db = tmp_path / "cache.db"
    monkeypatch.setenv("SARUP_DB_PATH", str(db))
    monkeypatch.setattr(sarup_hook, "MIN_TOKENS", 100)

    out = sarup_hook.build_hook_output(_bash(BIG_THAI))

    assert out is not None
    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    assert len(updated) < len(BIG_THAI) + 400  # compressed (+ footer)
    assert "sarup_retrieve" in updated

    # Extract the hash from the footer and prove cross-process recovery.
    h = updated.split("hash '")[1].split("'")[0]
    other_process_store = CompressionStore(db_path=str(db))
    assert other_process_store.retrieve(h) == BIG_THAI  # byte-for-byte


def test_hook_output_is_ascii_safe(monkeypatch, tmp_path):
    """Regression: hook stdout must be pure ASCII so a cp1252 console (Windows)
    can't crash it on Thai output. main() writes json.dumps(out) (ensure_ascii)."""
    monkeypatch.setenv("SARUP_DB_PATH", str(tmp_path / "c.db"))
    monkeypatch.setattr(sarup_hook, "MIN_TOKENS", 100)
    out = sarup_hook.build_hook_output(_bash(BIG_THAI))
    assert out is not None
    json.dumps(out).encode("ascii")  # raises if any non-ASCII leaks to stdout


def test_surrogate_output_does_not_crash(monkeypatch, tmp_path):
    """Windows console capture can inject lone surrogates (\\udc81). The hook
    must sanitize them, not crash in make_hash/tokenizer."""
    monkeypatch.setenv("SARUP_DB_PATH", str(tmp_path / "c.db"))
    monkeypatch.setattr(sarup_hook, "MIN_TOKENS", 100)
    dirty = BIG_THAI + "\udc81\udc82" + BIG_THAI  # lone surrogates in the middle
    out = sarup_hook.build_hook_output(_bash(dirty))
    assert out is not None  # compressed without raising
    json.dumps(out).encode("ascii")


def test_no_db_path_skips_substitution(monkeypatch):
    """Without a shared store, the original is unrecoverable → never substitute."""
    monkeypatch.delenv("SARUP_DB_PATH", raising=False)
    monkeypatch.setattr(sarup_hook, "MIN_TOKENS", 100)
    assert sarup_hook.build_hook_output(_bash(BIG_THAI)) is None
