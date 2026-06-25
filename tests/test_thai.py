"""Thai compression quality tests.

Success criteria (per spec):
- ≥30% token reduction on Thai prose ≥10 sentences
- Compressed output is a strict subset of original sentences (extractive)
- Same input → same hash (deterministic)
- Code blocks preserved verbatim in mixed content
"""

import pytest

from sarup.compressor import compress, estimate_tokens
from sarup.store import CompressionStore
from sarup.thai import is_thai, split_sentences, tfidf_compress, tokenize_words

# ─── Thai detection ────────────────────────────────────────────────────────────

def test_is_thai_pure():
    assert is_thai("สวัสดีครับ ผมชื่อสมชาย") is True


def test_is_thai_english():
    assert is_thai("Hello world, this is English text.") is False


def test_is_thai_mixed():
    # >10% Thai chars → True
    assert is_thai("This is mixed content สวัสดี with some Thai") is True


def test_is_thai_empty():
    assert is_thai("") is False


# ─── Word tokenization ─────────────────────────────────────────────────────────

def test_tokenize_thai_returns_words():
    tokens = tokenize_words("ฉันชอบกินข้าวผัด")
    assert len(tokens) >= 2  # at least "ฉัน" + "ชอบ" + "กิน" etc.
    assert all(isinstance(t, str) for t in tokens)


def test_tokenize_english_splits_on_spaces():
    tokens = tokenize_words("hello world foo")
    assert tokens == ["hello", "world", "foo"]


# ─── Sentence splitting ────────────────────────────────────────────────────────

def test_split_sentences_by_newlines():
    text = "บรรทัดแรก\nบรรทัดสอง\nบรรทัดสาม"
    sents = split_sentences(text)
    assert len(sents) == 3


def test_split_sentences_english():
    text = "First sentence.\nSecond sentence.\nThird one."
    sents = split_sentences(text)
    assert len(sents) >= 2


# ─── TF-IDF compression ───────────────────────────────────────────────────────

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

ENGLISH_PROSE = """
Modern software development requires careful attention to architecture.
Teams must balance feature delivery with technical debt management.
Database design decisions affect application performance significantly.
Testing strategies vary depending on the complexity of the system.
Code reviews help catch bugs early in the development process.
Documentation is often neglected but critical for team onboarding.
Continuous integration reduces integration problems across the team.
Monitoring and alerting are essential for production system health.
Security considerations must be integrated from the start of development.
Refactoring should be done incrementally to reduce risk of breakage.
""".strip()


def test_thai_prose_compression_ratio():
    result = compress(THAI_PROSE, target_ratio=0.5)
    assert result.savings_percent >= 20, (
        f"Expected ≥20% savings, got {result.savings_percent}%"
    )


def test_thai_prose_hits_30pct_target():
    result = compress(THAI_PROSE, target_ratio=0.4)
    assert result.savings_percent >= 30, (
        f"Expected ≥30% savings at ratio=0.4, got {result.savings_percent}%"
    )


def test_english_prose_compression_ratio():
    result = compress(ENGLISH_PROSE, target_ratio=0.5)
    assert result.savings_percent >= 20


def test_compressed_is_subset_of_original():
    """Extractive: every line in output must appear verbatim in input."""
    result = compress(THAI_PROSE, target_ratio=0.5)
    original_lines = set(THAI_PROSE.splitlines())
    compressed_lines = [ln for ln in result.compressed.splitlines() if ln.strip()]
    for line in compressed_lines:
        assert line in original_lines, (
            f"Non-original line in compressed output: {line!r}"
        )


def test_compression_is_deterministic():
    r1 = compress(THAI_PROSE, target_ratio=0.5)
    r2 = compress(THAI_PROSE, target_ratio=0.5)
    assert r1.compressed == r2.compressed
    assert r1.savings_percent == r2.savings_percent


def test_hash_is_deterministic():
    h1 = CompressionStore.make_hash(THAI_PROSE)
    h2 = CompressionStore.make_hash(THAI_PROSE)
    assert h1 == h2
    assert len(h1) == 24


def test_hash_differs_for_different_input():
    h1 = CompressionStore.make_hash(THAI_PROSE)
    h2 = CompressionStore.make_hash(ENGLISH_PROSE)
    assert h1 != h2


# ─── Mixed content ────────────────────────────────────────────────────────────

MIXED = """\
นี่คือเอกสารที่อธิบายวิธีใช้งาน API
ระบบรองรับการเรียกใช้งานแบบ REST และ GraphQL
ผู้พัฒนาต้องส่ง request พร้อม authentication header
เซิร์ฟเวอร์จะตอบกลับด้วย JSON payload เสมอ
การจัดการ error ควรทำที่ฝั่ง client ด้วย

```python
import httpx

response = httpx.get(
    "https://api.example.com/v1/data",
    headers={"Authorization": "Bearer TOKEN"},
)
data = response.json()
```

หลังจากได้รับข้อมูลแล้ว ต้องตรวจสอบ status code ก่อนเสมอ
ถ้า status code เป็น 429 ให้ทำ retry พร้อม exponential backoff
"""

CODE_BLOCK = """\
```python
import httpx

response = httpx.get(
    "https://api.example.com/v1/data",
    headers={"Authorization": "Bearer TOKEN"},
)
data = response.json()
```"""


def test_code_block_preserved_verbatim():
    result = compress(MIXED, target_ratio=0.5)
    assert CODE_BLOCK in result.compressed, "Code fence must be preserved verbatim"


def test_mixed_has_code_preserved_transform():
    result = compress(MIXED, target_ratio=0.5)
    assert "code_preserved" in result.transforms


# ─── JSON compression ─────────────────────────────────────────────────────────

def test_json_compact():
    ugly_json = '{\n  "name": "sarup",\n  "version": "0.1.0",\n  "description": "Thai compression"\n}'
    result = compress(ugly_json, lossless=True)
    assert result.lossy is False
    assert "\n" not in result.compressed
    assert result.tokens_saved >= 0


# ─── Short content ────────────────────────────────────────────────────────────

def test_short_content_returns_noop():
    short = "สวัสดี"
    result = compress(short)
    assert result.transforms == ["noop"]
    assert result.compressed == short
    assert result.tokens_saved == 0


# ─── Token estimation ─────────────────────────────────────────────────────────

def test_estimate_tokens_positive():
    assert estimate_tokens("Hello world") > 0
    assert estimate_tokens("สวัสดีครับ") > 0


def test_estimate_tokens_longer_is_more():
    short_tok = estimate_tokens("abc")
    long_tok = estimate_tokens("abc " * 100)
    assert long_tok > short_tok
