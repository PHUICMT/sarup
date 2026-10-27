"""Sarup benchmark — measure compression before/after on a sample corpus.

Run:
    .\.venv\Scripts\python.exe bench\benchmark.py
    .\.venv\Scripts\python.exe bench\benchmark.py --claude   # + Claude tokenizer column

`--claude` also counts every sample with Claude's own tokenizer (Anthropic
count_tokens API; it sends the sample text and needs the `llm` extra + credentials).

For each sample it reports: original tokens → compressed tokens, savings %,
roundtrip-verified (original recoverable byte-for-byte), and latency.
This is the "before/after" measurement: the proof that Sarup saves tokens
*and* loses nothing (every entry must show verified=OK).
"""

from __future__ import annotations

import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")  # box-drawing chars on Windows
except Exception:
    pass

from sarup.compressor import compress
from sarup.llm import abstractive_model, ollama_available
from sarup.store import CompressionStore
from sarup.tokens import CLAUDE_COUNT_MODEL, count_claude_tokens, token_method

# ─── Sample corpus (representative of real Claude Code context) ─────────────────

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

JSON_BLOB = """{
  "name": "sarup",
  "version": "0.1.0",
  "description": "Thai-first context compression",
  "settings": {
    "ratio": 0.5,
    "lossless": false,
    "engines": ["newmm", "tfidf"]
  }
}"""

LOGS = "\n".join(
    [f"2026-06-25 10:00:{i:02d} INFO  request handled in {i}ms" for i in range(20)]
    + [f"2026-06-25 10:01:{i:02d} ERROR connection reset by peer" for i in range(8)]
)

SAMPLES = [
    ("Thai prose", THAI_PROSE, {"target_ratio": 0.5}),
    ("Thai prose (aggressive)", THAI_PROSE, {"target_ratio": 0.4}),
    ("English prose", ENGLISH_PROSE, {"target_ratio": 0.5}),
    ("JSON", JSON_BLOB, {"lossless": True}),
    ("Logs", LOGS, {"target_ratio": 0.5}),
]


def _bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "·" * (width - filled)


def main() -> None:
    use_claude = "--claude" in sys.argv[1:]
    store = CompressionStore()
    print(f"\nSarup benchmark: token_method = {token_method()}"
          f"{f' + claude:{CLAUDE_COUNT_MODEL}' if use_claude else ''}\n")
    header = f"{'sample':<26}{'before':>8}{'after':>8}{'saved':>8}  {'savings':<28}{'verify':>8}{'ms':>7}"
    if use_claude:
        header += f"{'claude':>9}{'→':>2}{'after':>7}{'saved':>8}"
    print(header)
    print("─" * len(header))

    tot_before = tot_after = 0
    cl_before = cl_after = 0
    all_verified = True

    for label, text, opts in SAMPLES:
        t0 = time.perf_counter()
        r = compress(text, **opts)
        elapsed = (time.perf_counter() - t0) * 1000

        h = store.store(text, r.compressed, r.original_tokens, r.compressed_tokens)
        verified = store.verify(h, text)
        all_verified = all_verified and verified

        tot_before += r.original_tokens
        tot_after += r.compressed_tokens

        row = (
            f"{label:<26}{r.original_tokens:>8}{r.compressed_tokens:>8}"
            f"{r.tokens_saved:>8}  {_bar(r.savings_percent)} {r.savings_percent:>5.1f}%"
            f"{'OK' if verified else 'FAIL':>8}{elapsed:>7.1f}"
        )
        if use_claude:
            cb, ca = count_claude_tokens(text), count_claude_tokens(r.compressed)
            cl_before += cb
            cl_after += ca
            row += f"{cb:>9}{'→':>2}{ca:>7}{(cb - ca) / cb * 100:>7.1f}%"
        print(row)

    print("─" * len(header))
    tot_saved = tot_before - tot_after
    tot_pct = tot_saved / tot_before * 100 if tot_before else 0.0
    total = (
        f"{'TOTAL':<26}{tot_before:>8}{tot_after:>8}{tot_saved:>8}  "
        f"{_bar(tot_pct)} {tot_pct:>5.1f}%"
        f"{'ALL OK' if all_verified else 'FAILED':>8}"
    )
    if use_claude and cl_before:
        total += f"{'':>7}{cl_before:>9}{'→':>2}{cl_after:>7}{(cl_before - cl_after) / cl_before * 100:>7.1f}%"
    print(total)
    print(
        f"\n→ {tot_pct:.1f}% fewer tokens, "
        f"{'100% recoverable (every sample verified)' if all_verified else 'VERIFY FAILED — data loss!'}\n"
    )

    _mode_comparison()


def _mode_comparison() -> None:
    """Same Thai prose through every mode, side by side."""
    store = CompressionStore()
    have_ollama = ollama_available()
    print(f"Mode comparison on Thai prose  (Ollama {'UP' if have_ollama else 'DOWN'}"
          f"{', model=' + abstractive_model() if have_ollama else ''})\n")
    header = f"{'mode':<14}{'before':>8}{'after':>8}{'savings':>9}{'verify':>8}{'ms':>9}"
    print(header)
    print("─" * len(header))

    for mode in ("extractive", "semantic", "abstractive", "pipeline", "auto"):
        t0 = time.perf_counter()
        r = compress(THAI_PROSE, target_ratio=0.5, mode=mode)
        elapsed = (time.perf_counter() - t0) * 1000
        h = store.store(THAI_PROSE, r.compressed, r.original_tokens, r.compressed_tokens)
        verified = store.verify(h, THAI_PROSE)
        print(
            f"{mode:<14}{r.original_tokens:>8}{r.compressed_tokens:>8}"
            f"{r.savings_percent:>8.1f}%{'OK' if verified else 'FAIL':>8}{elapsed:>9.1f}"
        )
    print()


if __name__ == "__main__":
    main()
