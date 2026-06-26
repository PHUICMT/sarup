<div align="center">

# สรุป · Sarup

**Thai-first context compression for Claude Code.**
An MCP server that actually shrinks Thai — 50–88% fewer tokens — and caches every original
so nothing is ever lost.

[![CI](https://github.com/PHUICMT/sarup/actions/workflows/ci.yml/badge.svg)](https://github.com/PHUICMT/sarup/actions/workflows/ci.yml)
&nbsp;![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
&nbsp;![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
&nbsp;![MCP](https://img.shields.io/badge/MCP-server-8A2BE2.svg)
&nbsp;![tests](https://img.shields.io/badge/tests-55%20passing-brightgreen.svg)

<!--
Quickstart demo GIF goes here. To record one:
  1) asciinema rec demo.cast      (or: terminalizer record demo)
  2) agg demo.cast docs/quickstart.gif   (asciinema -> gif)
  3) uncomment the line below and commit docs/quickstart.gif
-->
<!-- ![Sarup quickstart](docs/quickstart.gif) -->

</div>

> *สรุป* means "to summarize." Headroom routes Thai through `noop` (0% savings) because its
> whitespace tokenizer can't find Thai word boundaries. Sarup uses PyThaiNLP segmentation, so it
> compresses Thai as well as English — and caches every original so nothing is ever lost.

## Contents

- [Highlights](#highlights)
- [Why it&#39;s safe — the two-tier guarantee](#why-its-safe--the-two-tier-guarantee)
- [How it works](#how-it-works)
- [Tools](#tools)
- [Compression modes](#compression-modes)
- [Measured results](#measured-results)
- [Example](#example)
- [Install](#install)
- [Register with Claude Code](#register-with-claude-code)
- [Auto-compression hook](#auto-compression-hook)
- [Privacy &amp; data](#privacy--data)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Tech stack &amp; techniques](#tech-stack--techniques)
- [Testing](#testing)
- [Roadmap](#roadmap)
- [License](#license)

## Highlights

- 🇹🇭 **Real Thai compression** — PyThaiNLP `newmm` word segmentation, not whitespace.
- ♻️ **Lossless by guarantee** — every compress caches the original; `verified: true` proves a byte-for-byte round-trip.
- 🎚️ **Five modes** — from offline 1 ms TF-IDF to an 88%-savings cascade.
- 🧠 **Optional local LLM** — embeddings + rewrite via Ollama, with automatic offline fallback.
- 📏 **Honest metrics** — token counts from a real tokenizer (tiktoken), not byte guesses.
- 🔌 **Content-aware** — JSON compaction, log dedup, and verbatim code-fence preservation built in.
- 🛟 **Can't break Claude** — it's an MCP tool, not an API proxy; if the server is down the tools just go away and Claude keeps working.

## Why it's safe — the two-tier guarantee

| Tier                      | What                                 | Guarantee               |
| ------------------------- | ------------------------------------ | ----------------------- |
| **Compressed view** | the shrunk text the model works on   | lossy · small · cheap |
| **Retrieval store** | the original, keyed by a stable hash | lossless · recoverable |

Aggressive lossy compression is safe *because* the original is always one `sarup_retrieve(hash)`
away. This is how "maximum savings" and "100% accuracy" coexist — they live in different tiers.

## How it works

Two entry points feed one engine: a cheap **compressed view** the model reads, and a lossless
**retrieval store** that can restore the original byte-for-byte.

```mermaid
flowchart TD
    M["🧑 Manual<br/>sarup_compress()"]:::entry --> R
    A["⚙️ Automatic<br/>PostToolUse hook<br/>(Read · Bash · Grep)"]:::entry --> R

    R{"Sarup compress<br/>extractive · semantic · abstractive · pipeline"}:::engine
    R -- "compressed view<br/>50–88% fewer tokens" --> V["📄 Model context"]:::lossy
    R -. "cache original" .-> S[("🗄️ Retrieval store<br/>hash → original")]:::lossless

    V -. "need full detail?" .-> RET["🔑 sarup_retrieve(hash)"]:::lossless
    RET --> S
    S == "byte-for-byte ✓" ==> V

    classDef entry fill:#e0e7ff,stroke:#6366f1,color:#111
    classDef engine fill:#fde68a,stroke:#d97706,color:#111
    classDef lossy fill:#fef3c7,stroke:#f59e0b,color:#111
    classDef lossless fill:#bbf7d0,stroke:#16a34a,color:#111
```

- **Manual** — the model calls `sarup_compress` / `sarup_retrieve` itself.
- **Automatic** — the hook intercepts large tool outputs, caches the original to `SARUP_DB_PATH`,
  and substitutes the compressed view + a retrieval hash. Source code is skipped; small outputs
  pass through untouched.

## Tools

| Tool                                                                 | Purpose                                                                                |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `sarup_compress(content, target_ratio?, lossless?, query?, mode?)` | Compress; returns compressed text, hash, token metrics,`verified`, `token_method`. |
| `sarup_retrieve(hash)`                                             | Recover the original content byte-for-byte.                                            |
| `sarup_stats()`                                                    | Cumulative session savings.                                                            |

**`sarup_compress` arguments**

| Arg              | Type    | Default        | Meaning                                                     |
| ---------------- | ------- | -------------- | ----------------------------------------------------------- |
| `content`      | string  | —             | Text to compress (required).                                |
| `target_ratio` | number  | `0.5`        | Fraction of prose to keep (0.1–0.9).                       |
| `lossless`     | boolean | `false`      | Only apply lossless transforms (whitespace / JSON compact). |
| `query`        | string  | `""`         | Relevance hint — sentences matching it are kept.           |
| `mode`         | string  | `extractive` | See modes below.                                            |

## Compression modes

| Mode                         | How                                       | Needs Ollama |    Savings¹    | Speed¹ | Output          |
| ---------------------------- | ----------------------------------------- | :----------: | :-------------: | :------: | --------------- |
| `extractive` *(default)* | TF-IDF scoring + n-gram dedup             |      no      |      50.8%      |  ~1 ms  | verbatim subset |
| `semantic`                 | Embedding centrality + cosine dedup       |     yes     |      64.6%      | ~1–2 s | verbatim subset |
| `abstractive`              | Local-LLM rewrite                         |     yes     |      ~51%      | ~8–20 s | paraphrased     |
| `pipeline`                 | Cascade: semantic → abstractive          |     yes     | **88.1%** |   ~2 s   | paraphrased     |
| `auto`                     | semantic if Ollama is up, else extractive |   optional   |      64.6%      |  ~90 ms  | subset          |

¹ Measured on a 10-sentence Thai paragraph (522 tokens). Every mode stays 100% recoverable via the
store; Ollama modes **degrade gracefully** to extractive when the backend is down.

## Measured results

```text
$ .\.venv\Scripts\python.exe bench\benchmark.py

sample                      before   after   savings   verify
Thai prose                     522     257    50.8%       OK
Thai prose (aggressive)        522     217    58.4%       OK
English prose                  105      54    48.6%       OK
JSON (lossless)                 67      44    34.3%       OK
Logs                           563     300    46.7%       OK
TOTAL                         1779     872    51.0%    ALL OK   → 100% recoverable

Mode comparison (Thai prose, 522 tok):
  extractive 50.8% (1ms) · auto 64.6% (~90ms) · semantic 64.6% (2.1s)
  abstractive 51.1% (8s) · pipeline 88.1% (2.3s)        ← all verified recoverable
```

Token counts via tiktoken `cl100k_base` — a real tokenizer, not a byte heuristic.

## Example

A real `sarup_compress` call on a Thai paragraph (`mode="auto"`, Ollama up → semantic):

```jsonc
// → sarup_compress(content="…518-token Thai paragraph…", mode="auto")
{
  "compressed": "จุดเด่นที่สำคัญที่สุดคือมันไม่มีทางทำให้ Claude พัง…",
  "hash": "caa568140bec0ff734937cf5",
  "original_tokens": 518,
  "compressed_tokens": 154,
  "tokens_saved": 364,
  "savings_percent": 70.3,
  "transforms": ["semantic_extractive", "embeddings", "thai"],
  "lossy": true,
  "verified": true,                    // round-trip proven byte-for-byte
  "token_method": "tiktoken:cl100k_base"
}
```

The model keeps working on the 154-token view; the full 518-token original is one call away:

```jsonc
// → sarup_retrieve(hash="caa568140bec0ff734937cf5")
{ "content": "…the exact original text, restored byte-for-byte…" }
```

## Install

**One command** (creates the venv, installs everything, registers the MCP server
for all projects — idempotent):

```powershell
.\scripts\setup.ps1 -All      # Windows  (-All also adds the hook + pulls Ollama models)
./scripts/setup.sh --all      # Linux / WSL / macOS
```

Uninstall just as cleanly (only removes what Sarup added; `-Purge`/`--purge` also
deletes the venv + cache):

```powershell
.\scripts\uninstall.ps1       # Windows
./scripts/uninstall.sh        # Linux / WSL / macOS
```

<details><summary>Manual install</summary>

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

</details>

Optional local-LLM modes (`semantic` / `abstractive` / `pipeline`) need [Ollama](https://ollama.com):

```powershell
ollama pull nomic-embed-text     # embeddings → semantic mode
ollama pull gemma3:12b           # rewrite → abstractive / pipeline (Thai-validated)
```

## Register with Claude Code

**One-command setup (recommended).** Detects this machine's paths, probes Ollama
(picks the best mode + models), and merges into `.mcp.json` / `.claude/settings.json`
**without clobbering** anything already there (a `.bak` is written first):

```powershell
.\.venv\Scripts\python.exe scripts\install.py --with-hook --pull
```

- No Ollama? It configures offline `extractive` mode — still fully works.
- Ollama up? It auto-selects `nomic-embed-text` (semantic) + `gemma3:12b` (rewrite)
  and sets the hook to `auto`. `--pull` fetches any missing models.
- Idempotent — safe to re-run; `--global` writes to `~/.claude` instead.

**Manual** — or add it yourself to your MCP config (e.g. `.mcp.json` or `~/.claude.json`).
Replace `<SARUP_DIR>` with the absolute path where you cloned this repo (the installer
above fills these in for you):

```json
{
  "mcpServers": {
    "sarup": {
      "command": "<SARUP_DIR>/.venv/Scripts/python.exe",
      "args": ["-m", "sarup.server"],
      "env": { "SARUP_DB_PATH": "<SARUP_DIR>/.sarup-cache.db" }
    }
  }
}
```

> On Linux/macOS the interpreter is `<SARUP_DIR>/.venv/bin/python`.

Or run it directly over stdio:

```powershell
.\.venv\Scripts\python.exe -m sarup.server
```

## Auto-compression hook

Skip manual tool calls entirely: install the **PostToolUse hook** and large `Read`/`Bash`/`Grep`
outputs are compressed before they enter context, with the original cached for retrieval.
Source-code reads are skipped for safety. Full setup in **[hooks/README.md](hooks/README.md)**.

> **Requires Claude Code ≥ 2.1.186**, which is when `PostToolUse` began applying a
> hook's `updatedToolOutput` (earlier builds ran the hook but ignored the substitution).
> Replace `<SARUP_DIR>` with your clone path — or just run `install.py --with-hook`, which
> writes this for you.

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Read|Bash|Grep",
        "hooks": [{ "type": "command",
          "command": "<SARUP_DIR>/.venv/Scripts/python.exe <SARUP_DIR>/hooks/sarup_hook.py" }] }
    ]
  },
  "env": { "SARUP_DB_PATH": "<SARUP_DIR>/.sarup-cache.db" }
}
```

## Privacy & data

To guarantee recovery, Sarup caches the **original** content in the store. Two
things to know:

- With `SARUP_DB_PATH` set, originals are written to that **SQLite file in
  plaintext** (no encryption). Treat it like a cache of whatever you compressed.
- If you compress tool outputs that contain secrets (e.g. a `.env` dump or
  credentials in a log), those land in the cache too. The auto-hook skips
  source-code/config file reads, but `Bash` output is fair game — review what
  you point it at.

`*.db` is git-ignored, so the cache never gets committed. For zero on-disk
footprint, leave `SARUP_DB_PATH` unset (memory-only; the MCP server then loses
the cache on restart, and the hook will not substitute — see the hook docs).

## Configuration

| Var                         | Default                    | Meaning                                                                                  |
| --------------------------- | -------------------------- | ---------------------------------------------------------------------------------------- |
| `SARUP_DB_PATH`           | *(in-memory)*            | SQLite path for a persistent, cross-process store.**Required** for hook retrieval. |
| `OLLAMA_HOST`             | `http://localhost:11434` | Ollama endpoint.                                                                         |
| `SARUP_ABSTRACTIVE_MODEL` | `gemma3:12b`             | Model for abstractive / pipeline rewrite.                                                |
| `SARUP_EMBED_MODEL`       | `nomic-embed-text`       | Model for semantic embeddings.                                                           |
| `SARUP_HOOK_MODE`         | `auto`                   | Hook compression mode.                                                                   |
| `SARUP_HOOK_MIN_TOKENS`   | `400`                    | Hook only compresses outputs with at least this many tokens (token-based, fair across languages). |

## Project structure

```text
sarup/
├── src/sarup/
│   ├── server.py       # MCP stdio server — 3 tools
│   ├── compressor.py   # router + modes (extractive/semantic/abstractive/pipeline/auto)
│   ├── thai.py         # PyThaiNLP tokenization, sentence split, TF-IDF
│   ├── semantic.py     # embedding centrality + cosine dedup
│   ├── llm.py          # optional Ollama backend (generate + embed)
│   ├── tokens.py       # real token counting (tiktoken)
│   └── store.py        # CCR store: hash → original (memory + SQLite)
├── hooks/
│   ├── sarup_hook.py   # PostToolUse auto-compression hook
│   └── README.md       # hook install guide
├── bench/benchmark.py  # before/after measurement
├── tests/              # test_thai, test_mcp, test_hook, ...
├── README.md
└── STACK.md            # full stack + techniques
```

## Tech stack & techniques

Python 3.11 · MCP · PyThaiNLP `newmm` · tiktoken · Ollama (optional) · SQLite · hatchling · pytest.

The technique behind each mode — TF-IDF scoring, embedding centrality, cascade pipeline, content
routing, and graceful degradation — is documented in **[STACK.md](STACK.md)**.

## Testing

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

The suite covers Thai NLP, the MCP tool contracts, every mode (including Ollama-fallback paths), the
roundtrip-verify guarantee, and the auto-compression hook (incl. cross-process retrieval).

## Roadmap

- [ ] Make `auto` the default mode for `sarup_compress` (currently `extractive`).
- [ ] Optional Typhoon 2.1 abstractive (blocked on an Ollama template fix).
- [ ] Per-content adaptive `target_ratio`.
- [ ] Published PyPI package.

## License

MIT
