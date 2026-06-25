"""MCP tool contract tests — verify the three Sarup tools behave correctly."""

import json
import pytest

from sarup import llm
from sarup.compressor import compress
from sarup.server import SarupServer
from sarup.store import CompressionStore

THAI_PROSE = """
ระบบการจัดการข้อมูลในองค์กรปัจจุบันมีความซับซ้อนมากขึ้นเรื่อยๆ
การนำเทคโนโลยีใหม่มาใช้ช่วยให้ทีมงานสามารถทำงานได้อย่างมีประสิทธิภาพ
ฐานข้อมูลขนาดใหญ่ต้องการการดูแลรักษาอย่างสม่ำเสมอ
นักพัฒนาซอฟต์แวร์ต้องเข้าใจทั้งโครงสร้างข้อมูลและอัลกอริทึม
การทดสอบซอฟต์แวร์เป็นส่วนสำคัญของกระบวนการพัฒนา
โปรแกรมเมอร์ที่ดีต้องเขียนโค้ดที่อ่านเข้าใจง่ายและบำรุงรักษาได้
ระบบ CI/CD ช่วยให้การ deploy โค้ดเป็นไปอย่างราบรื่น
การทำ code review ช่วยป้องกันบั๊กและเพิ่มคุณภาพของโค้ด
เอกสารประกอบโค้ดที่ดีช่วยให้สมาชิกทีมใหม่เข้าใจระบบได้รวดเร็ว
การจัดการ dependency อย่างเหมาะสมลดปัญหาความขัดแย้งของไลบรารี
""".strip()


@pytest.fixture
def server():
    return SarupServer()


# ─── sarup_compress ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compress_returns_required_fields(server):
    result = await server._handle_compress({"content": THAI_PROSE})
    payload = json.loads(result[0].text)
    for field in ("compressed", "hash", "original_tokens", "compressed_tokens",
                  "tokens_saved", "savings_percent", "transforms", "lossy"):
        assert field in payload, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_compress_hash_is_24_chars(server):
    result = await server._handle_compress({"content": THAI_PROSE})
    payload = json.loads(result[0].text)
    assert len(payload["hash"]) == 24


@pytest.mark.asyncio
async def test_compress_tokens_saved_non_negative(server):
    result = await server._handle_compress({"content": THAI_PROSE})
    payload = json.loads(result[0].text)
    assert payload["tokens_saved"] >= 0
    assert payload["original_tokens"] >= payload["compressed_tokens"]


@pytest.mark.asyncio
async def test_compress_missing_content_returns_error(server):
    result = await server._handle_compress({})
    payload = json.loads(result[0].text)
    assert "error" in payload


@pytest.mark.asyncio
async def test_compress_empty_content_returns_error(server):
    result = await server._handle_compress({"content": ""})
    payload = json.loads(result[0].text)
    assert "error" in payload


@pytest.mark.asyncio
async def test_compress_lossless_flag(server):
    ugly = '{\n  "key": "value",\n  "num": 42\n}'
    result = await server._handle_compress({"content": ugly, "lossless": True})
    payload = json.loads(result[0].text)
    assert payload["lossy"] is False


@pytest.mark.asyncio
async def test_compress_target_ratio_clamped(server):
    result = await server._handle_compress({"content": THAI_PROSE, "target_ratio": 999})
    payload = json.loads(result[0].text)
    # Should not crash; target_ratio is clamped to 0.9
    assert "compressed" in payload


@pytest.mark.asyncio
async def test_compress_same_input_same_hash(server):
    r1 = await server._handle_compress({"content": THAI_PROSE})
    r2 = await server._handle_compress({"content": THAI_PROSE})
    h1 = json.loads(r1[0].text)["hash"]
    h2 = json.loads(r2[0].text)["hash"]
    assert h1 == h2


# ─── sarup_retrieve ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieve_returns_original(server):
    compress_result = await server._handle_compress({"content": THAI_PROSE})
    h = json.loads(compress_result[0].text)["hash"]

    retrieve_result = await server._handle_retrieve({"hash": h})
    assert retrieve_result[0].text == THAI_PROSE


@pytest.mark.asyncio
async def test_retrieve_unknown_hash_returns_error(server):
    result = await server._handle_retrieve({"hash": "000000000000000000000000"})
    payload = json.loads(result[0].text)
    assert "error" in payload


@pytest.mark.asyncio
async def test_retrieve_missing_hash_returns_error(server):
    result = await server._handle_retrieve({})
    payload = json.loads(result[0].text)
    assert "error" in payload


# ─── sarup_stats ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stats_initial_zeros(server):
    result = await server._handle_stats()
    payload = json.loads(result[0].text)
    assert payload["total_compressed"] == 0
    assert payload["total_tokens_saved"] == 0


@pytest.mark.asyncio
async def test_stats_increments_after_compress(server):
    await server._handle_compress({"content": THAI_PROSE})
    await server._handle_compress({"content": THAI_PROSE})

    result = await server._handle_stats()
    payload = json.loads(result[0].text)
    assert payload["total_compressed"] == 2


@pytest.mark.asyncio
async def test_stats_has_required_fields(server):
    result = await server._handle_stats()
    payload = json.loads(result[0].text)
    for field in ("total_compressed", "total_tokens_saved", "store_entries"):
        assert field in payload


# ─── Store directly ────────────────────────────────────────────────────────────

def test_store_roundtrip():
    store = CompressionStore()
    h = store.store("original content", "compressed", 10, 5)
    assert store.retrieve(h) == "original content"


def test_store_unknown_hash_returns_none():
    store = CompressionStore()
    assert store.retrieve("nonexistent_hash") is None


def test_store_exists():
    store = CompressionStore()
    h = store.store("x", "y", 1, 1)
    assert store.exists(h) is True
    assert store.exists("bad_hash") is False


def test_store_verify_roundtrip():
    store = CompressionStore()
    h = store.store(THAI_PROSE, "compressed subset", 100, 40)
    assert store.verify(h, THAI_PROSE) is True
    assert store.verify(h, "tampered original") is False


@pytest.mark.asyncio
async def test_compress_reports_verified_and_token_method(server):
    result = await server._handle_compress({"content": THAI_PROSE})
    payload = json.loads(result[0].text)
    assert payload["verified"] is True
    assert "token_method" in payload


# ─── Modes (Ollama optional — must work whether or not it is running) ────────────

def test_ollama_available_returns_bool_without_crashing():
    assert isinstance(llm.ollama_available(), bool)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["extractive", "semantic", "abstractive", "auto"])
async def test_all_modes_compress_and_stay_recoverable(server, mode):
    """Every mode must produce valid output AND keep the original 100% recoverable,
    regardless of whether the Ollama backend is present."""
    result = await server._handle_compress({"content": THAI_PROSE, "mode": mode})
    payload = json.loads(result[0].text)
    assert "compressed" in payload and payload["compressed"]
    assert payload["verified"] is True  # original recoverable byte-for-byte
    assert payload["compressed_tokens"] <= payload["original_tokens"]


@pytest.mark.asyncio
async def test_invalid_mode_falls_back_to_extractive(server):
    result = await server._handle_compress({"content": THAI_PROSE, "mode": "bogus"})
    payload = json.loads(result[0].text)
    assert payload["verified"] is True
    assert payload["compressed"]
