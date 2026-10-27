"""Tests for the PostToolUse auto-compression hook (sarup.hook)."""

import json

import pytest

from sarup import hook as sarup_hook
from sarup.store import CompressionStore

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
    updated = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
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


def test_plugin_data_dir_is_the_default_store(monkeypatch, tmp_path):
    # As a plugin, server and hook share CLAUDE_PLUGIN_DATA with no SARUP_DB_PATH.
    monkeypatch.delenv("SARUP_DB_PATH", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(sarup_hook, "MIN_TOKENS", 100)
    out = sarup_hook.build_hook_output(_bash(BIG_THAI))
    assert out is not None
    h = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"].split("hash '")[1].split("'")[0]
    store = CompressionStore(db_path=str(tmp_path / "data" / "sarup-cache.db"))
    assert store.retrieve(h) == BIG_THAI


def test_updated_output_keeps_each_tools_shape():
    # The host drops an updatedToolOutput that doesn't match the tool's schema.
    bash = {"stdout": "long", "stderr": "warn", "interrupted": False, "isImage": False}
    assert sarup_hook._replace_output(bash, "short") == {
        "stdout": "short", "stderr": "", "interrupted": False, "isImage": False}
    read = {"type": "text", "file": {"filePath": "a.md", "content": "long", "numLines": 9}}
    assert sarup_hook._replace_output(read, "short") == {
        "type": "text", "file": {"filePath": "a.md", "content": "short", "numLines": 9}}
    assert sarup_hook._replace_output({"mode": "content", "content": "long"}, "short") == {
        "mode": "content", "content": "short"}
    assert sarup_hook._replace_output("long", "short") == "short"


def test_main_reads_utf8_stdin_whatever_the_locale(monkeypatch, tmp_path, capsys):
    # Windows hands the hook a cp1252 stdin; the payload bytes are UTF-8.
    import io
    import sys

    payload = json.dumps(_bash(BIG_THAI), ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload), encoding="cp1252"))
    monkeypatch.setattr(sys, "argv", ["sarup-hook", "--db", str(tmp_path / "c.db")])
    monkeypatch.delenv("SARUP_DB_PATH", raising=False)
    monkeypatch.setattr(sarup_hook, "MIN_TOKENS", 100)
    with pytest.raises(SystemExit):
        sarup_hook.main()
    out = json.loads(capsys.readouterr().out)
    h = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"].split("hash '")[1].split("'")[0]
    assert CompressionStore(db_path=str(tmp_path / "c.db")).retrieve(h) == BIG_THAI


def test_no_db_path_skips_substitution(monkeypatch):
    """Without a shared store, the original is unrecoverable → never substitute."""
    monkeypatch.delenv("SARUP_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setattr(sarup_hook, "MIN_TOKENS", 100)
    assert sarup_hook.build_hook_output(_bash(BIG_THAI)) is None
