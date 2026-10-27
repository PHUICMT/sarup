r"""Sarup QA eval: does Claude still answer correctly from the compressed view?

The benchmark proves tokens go down; this proves the facts survive. For every
document in bench/qa_thai.json and every mode, Sarup compresses the document
(no query, as the hook would), then Claude answers each question from that view
alone. Each answer is graded deterministically:

    correct: matches an accepted answer (numbers compared as numbers)
    abstain: "ไม่มีข้อมูล", the fact was dropped and Claude said so; in real
               use it would call sarup_retrieve, so this costs a round-trip, not
               accuracy
    wrong: a confident wrong answer: the failure that actually hurts

Token savings are measured with Claude's own tokenizer: the input-token usage
each answer call reports, minus a per-document baseline call with an empty
document. Everything except the document is identical between modes, so the
difference is the document alone.

Backends:
    cli (default): the local `claude -p`, on your Claude Code login; no API key
    api: the Anthropic SDK; needs the `llm` extra + ANTHROPIC_API_KEY

Run:
    .\.venv\Scripts\python.exe bench\eval_qa.py --dry-run              # compress only
    .\.venv\Scripts\python.exe bench\eval_qa.py --limit 2              # smoke test
    .\.venv\Scripts\python.exe bench\eval_qa.py --out eval.json        # full eval
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sarup.compressor import compress
from sarup.llm import ollama_available
from sarup.tokens import count_tokens

DATASET = Path(__file__).with_name("qa_thai.json")
ANSWER_MODEL = "claude-opus-5-5"
DEFAULT_MODES = "original,extractive:0.5,extractive:0.3,semantic:0.5,extractive:0.5+q"
ABSTAIN = "ไม่มีข้อมูล"

SYSTEM = (
    "ตอบคำถามโดยใช้ข้อมูลจากเอกสารที่ให้มาเท่านั้น ห้ามเดาหรือใช้ความรู้ภายนอก\n"
    "ตอบให้สั้นที่สุด เป็นคำหรือวลีเดียว ตัวเลขให้ใช้เลขอารบิก\n"
    f"ถ้าเอกสารไม่มีข้อมูลพอจะตอบ ให้ตอบว่า {ABSTAIN} เท่านั้น"
)

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _normalize(s: str) -> str:
    return re.sub(r"[\s,]", "", s.translate(_THAI_DIGITS)).lower()


def grade(answer: str, accepted: list[str] | None) -> str:
    if ABSTAIN in answer:
        return "correct" if accepted is None else "abstain"
    if accepted is None:
        return "wrong"
    norm = _normalize(answer)
    numbers = set(_NUMBER_RE.findall(norm))
    for acc in accepted:
        acc_norm = _normalize(acc)
        if _NUMBER_RE.fullmatch(acc_norm):
            if acc_norm in numbers:
                return "correct"
        elif acc_norm in norm:
            return "correct"
    return "wrong"


# What Claude sees under the hook: the compressed text plus a footer. The hash
# and retrieve call are left out: the eval has no tool, so "ไม่มีข้อมูล"
# stands in for calling sarup_retrieve.
SARUP_NOTE = (
    "\n\n[sarup: compressed. […] marks omitted text; a fact you need may be there, "
    "not in the lines nearby. The full original is retrievable.]"
)


def parse_modes(spec: str) -> list[tuple[str, str, float | None, bool]]:
    """`mode[:ratio][+q]` → (label, mode, ratio, use question as query)."""
    modes = []
    for item in spec.split(","):
        label = item.strip()
        spec_part, plus_q = (label[:-2], True) if label.endswith("+q") else (label, False)
        name, _, ratio = spec_part.partition(":")
        modes.append((label, name, float(ratio) if ratio else (None if name == "original" else 0.5), plus_q))
    return modes


def compressed_view(text: str, mode: str, ratio: float | None, query: str = "") -> tuple[str, list[str]]:
    if mode == "original":
        return text, []
    r = compress(text, target_ratio=ratio, mode=mode, query=query)
    if r.compressed == text:
        return text, r.transforms
    return r.compressed + SARUP_NOTE, r.transforms


def prompt_for(doc: str, question: str) -> str:
    return f"<document>\n{doc}\n</document>\n\nคำถาม: {question}"


# ─── Backends: each returns (answer text, model that answered, input tokens) ────
# served_by == "error" marks a failed call; the answer then holds the reason.


def _usage_total(usage: dict) -> int:
    return sum(usage.get(k) or 0 for k in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


class CliBackend:
    name = "claude-cli"

    def __init__(self) -> None:
        exe = shutil.which("claude")
        if not exe:
            sys.exit("`claude` CLI not found on PATH; install Claude Code or use --backend api")
        self.cmd = [
            exe, "-p", "--output-format", "json",
            "--model", ANSWER_MODEL, "--effort", "low",
            "--system-prompt", SYSTEM,
            "--tools", "", "--strict-mcp-config",
            "--disable-slash-commands", "--no-session-persistence",
        ]
        self.cost_usd = 0.0

    def ask(self, doc: str, question: str) -> tuple[str, str, int]:
        try:
            proc = subprocess.run(
                self.cmd, input=prompt_for(doc, question), capture_output=True,
                text=True, encoding="utf-8", timeout=300,
            )
            out = json.loads(proc.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            return f"<cli error: {type(e).__name__}>", "error", 0
        if out.get("is_error"):
            return f"<cli error: {out.get('result') or out.get('subtype')}>", "error", 0
        # Claude Code also runs a small helper model per session; only the
        # answering model's row counts. Its absence means a fallback answered.
        per_model = out.get("modelUsage") or {}
        self.cost_usd += sum(m.get("costUSD") or 0 for m in per_model.values())
        mine = per_model.get(ANSWER_MODEL)
        if mine is None:
            return (out.get("result") or "").strip(), ",".join(per_model) or "unknown", 0
        tokens = sum(mine.get(k) or 0 for k in
                     ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))
        return (out.get("result") or "").strip(), ANSWER_MODEL, tokens


class ApiBackend:
    name = "anthropic-api"

    def __init__(self) -> None:
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()

    def ask(self, doc: str, question: str) -> tuple[str, str, int]:
        try:
            resp = self.client.beta.messages.create(
                model=ANSWER_MODEL,
                max_tokens=4096,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt_for(doc, question)}],
            )
        except self.anthropic.APIStatusError as e:
            return f"<api error {e.status_code}>", "error", 0
        tokens = _usage_total(resp.usage.to_dict())
        if resp.stop_reason == "refusal":
            return "<refusal>", resp.model, tokens
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text, resp.model, tokens


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--modes", default=DEFAULT_MODES,
                    help=f"comma list of mode[:ratio] (default: {DEFAULT_MODES})")
    ap.add_argument("--backend", choices=("cli", "api"), default="cli")
    ap.add_argument("--dry-run", action="store_true",
                    help="compress and report cl100k savings only; no model calls")
    ap.add_argument("--limit", type=int, help="only the first N documents (smoke test)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", help="write per-question results as JSON")
    args = ap.parse_args()

    docs = json.loads(DATASET.read_text(encoding="utf-8"))["docs"][: args.limit]
    modes = parse_modes(args.modes)
    if not ollama_available():
        print("Ollama is down; semantic/pipeline fall back to extractive (see transforms).\n")

    # One view per (mode, doc, question); without +q every question of a doc
    # shares the same view, as it would under the hook.
    views: dict[tuple[str, str, int], tuple[str, list[str]]] = {}
    for label, mode, ratio, plus_q in modes:
        for d in docs:
            shared = None if plus_q else compressed_view(d["text"], mode, ratio)
            for qi, q in enumerate(d["questions"]):
                views[(label, d["id"], qi)] = shared or compressed_view(d["text"], mode, ratio, q["q"])

    labels = [m[0] for m in modes]
    n_questions = sum(len(d["questions"]) for d in docs)
    n_calls = n_questions * len(labels) + len(docs)

    if args.dry_run:
        print(f"{'mode':<18}{'cl100k saved':>14}   transforms")
        before = sum(count_tokens(d["text"]) for d in docs)
        for label in labels:
            after = sum(count_tokens(views[(label, d['id'], 0)][0]) for d in docs)
            transforms = sorted({t for d in docs for t in views[(label, d['id'], 0)][1]})
            print(f"{label:<18}{(before - after) / before * 100:>13.1f}%   {', '.join(transforms) or '-'}")
        print(f"\nFull run = {n_calls} calls to {ANSWER_MODEL} "
              f"({n_questions} questions × {len(labels)} modes + {len(docs)} baselines).")
        return

    backend = CliBackend() if args.backend == "cli" else ApiBackend()
    jobs = [("baseline", d, d["questions"][0], "") for d in docs] + [
        (label, d, q, views[(label, d["id"], qi)][0])
        for label in labels
        for d in docs
        for qi, q in enumerate(d["questions"])
    ]
    print(f"Asking {n_calls} questions via {backend.name} ({ANSWER_MODEL})…\n")
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        answers = list(pool.map(lambda j: backend.ask(j[3], j[2]["q"]), jobs))
    elapsed = time.perf_counter() - t0

    rows, baseline = [], {}
    for (label, d, q, _), (answer, served_by, tokens) in zip(jobs, answers):
        if label == "baseline":
            baseline[d["id"]] = tokens if served_by != "error" else None
            continue
        rows.append({
            "mode": label, "doc": d["id"], "question": q["q"], "accepted": q["answers"],
            "answer": answer, "served_by": served_by, "input_tokens": tokens,
            "grade": "error" if served_by == "error" else grade(answer, q["answers"]),
        })

    def doc_tokens(label: str, doc_id: str) -> int | None:
        first = next(r for r in rows if r["mode"] == label and r["doc"] == doc_id)
        if baseline.get(doc_id) is None or first["grade"] == "error":
            return None
        return first["input_tokens"] - baseline[doc_id]

    errors = [r for r in rows if r["grade"] == "error"]
    if errors:
        print(f"{len(errors)} failed calls, excluded below. First: {errors[0]['answer']}\n")

    measurable = [d["id"] for d in docs if all(doc_tokens(lb, d["id"]) is not None for lb in labels)]
    before = sum(doc_tokens(labels[0], i) or 0 for i in measurable)

    header = f"{'mode':<18}{'claude tok':>11}{'saved':>8}{'correct':>9}{'abstain':>9}{'wrong':>7}"
    print(header)
    print("─" * len(header))
    for label in labels:
        after = sum(doc_tokens(label, i) or 0 for i in measurable)
        mine = [r for r in rows if r["mode"] == label and r["grade"] != "error"]
        counts = {g: sum(r["grade"] == g for r in mine) for g in ("correct", "abstain", "wrong")}
        pct = (lambda n: f"{n / len(mine) * 100:>8.0f}%") if mine else (lambda n: f"{'-':>9}")
        saved = f"{(before - after) / before * 100:>7.1f}%" if before else f"{'-':>8}"
        print(f"{label:<18}{after:>11}{saved}{pct(counts['correct'])}{pct(counts['abstain'])}{counts['wrong']:>7}")

    print(f"\n{len(jobs)} calls in {elapsed:.0f}s; tokens over {len(measurable)}/{len(docs)} docs "
          f"(savings % assume '{labels[0]}' is the uncompressed baseline).")
    if getattr(backend, "cost_usd", 0):
        print(f"API-equivalent cost reported by the CLI: ${backend.cost_usd:.2f} "
              f"(on a subscription this is usage quota, not a bill).")
    for r in rows:
        if r["grade"] == "wrong" or r["served_by"] not in (ANSWER_MODEL, "error"):
            note = "" if r["served_by"] == ANSWER_MODEL else f"  [served by {r['served_by']}]"
            print(f"  {r['grade']:<7} {r['mode']:<16} {r['doc']:<13} {r['question']}  →  {r['answer']!r}{note}")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nPer-question results → {args.out}")


if __name__ == "__main__":
    main()
