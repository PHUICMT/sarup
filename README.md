# Sarup (สรุป)

**Thai-first context compression for Claude Code.** An MCP server that shrinks
the text you feed to an LLM by ~50–65% while keeping the original **100%
recoverable, byte-for-byte**.

Inspired by [Headroom](https://github.com/headroomlabs-ai/headroom), but built
to actually compress Thai — Headroom's whitespace tokenizer routes Thai to
`noop` (0% savings) because Thai has no word boundaries. Sarup uses PyThaiNLP
word segmentation, so it compresses Thai as well as it compresses English.

## Why it's safe ("100% accuracy")

Sarup is **two-tier**:

| Tier | What | Guarantee |
|------|------|-----------|
| **Compressed view** | the shrunk text the model works on | lossy, small, cheap |
| **Retrieval store** | the original, keyed by hash | lossless, recoverable |

Every `sarup_compress` returns a `hash` and a `verified: true` flag proving the
original round-trips byte-for-byte. If the model ever needs full detail it calls
`sarup_retrieve(hash)`. **You can never permanently lose information** — that's
how aggressive compression and 100% accuracy coexist.

## How it works (flow)

**Manual** — the model calls the tools explicitly:

```
large content ──► sarup_compress ──► { compressed, hash, verified:true }
                                         │
                  model works on the compressed view (cheap)
                                         │
                  need full detail? ──► sarup_retrieve(hash) ──► original (byte-for-byte)
```

**Automatic** — install the PostToolUse hook and the model does nothing special:

```
Read / Bash / Grep returns a large output
        │
        ▼
  PostToolUse hook intercepts
        ├─ caches the ORIGINAL into SARUP_DB_PATH   (lossless)
        └─ substitutes a COMPRESSED view + hash into context   (cheap)
        │
        ▼
  model sees the compressed output automatically
        │
  need full detail? ──► sarup_retrieve(hash) ──► original (byte-for-byte)
```

Source-code reads are skipped by the hook; small outputs pass through unchanged.
Either way, **nothing is ever permanently lost** — the original is one hash away.

## Tools

| Tool | Purpose |
|------|---------|
| `sarup_compress(content, target_ratio?, lossless?, query?, mode?)` | Compress; returns compressed text, hash, token metrics, `verified`, `token_method`. |
| `sarup_retrieve(hash)` | Recover the original content byte-for-byte. |
| `sarup_stats()` | Cumulative session savings. |

### Compression modes (the `mode` arg)

| Mode | How | Needs Ollama | Notes |
|------|-----|:---:|-------|
| `extractive` *(default)* | TF-IDF sentence scoring + n-gram dedup | no | Deterministic, offline, ~1ms, verbatim subset |
| `semantic` | Embedding centrality + cosine dedup | yes | **Best ratio** (~65%), ~1s, verbatim subset |
| `abstractive` | Local-LLM rewrite | yes | Highest potential ratio, slow (~10–20s), paraphrased |
| `auto` | abstractive if Ollama is up, else extractive | optional | — |

All modes stay 100% recoverable via the store. Ollama modes **degrade
gracefully** to extractive when the backend is down.

## Measured results

`.\.venv\Scripts\python.exe bench\benchmark.py`

```
sample                      before   after   savings   verify
Thai prose                     522     257    50.8%       OK
Thai prose (aggressive)        522     217    58.4%       OK
English prose                  105      54    48.6%       OK
JSON (lossless)                 67      44    34.3%       OK
Logs                           563     300    46.7%       OK
TOTAL                         1779     872    51.0%    ALL OK   → 100% recoverable

Mode comparison (Thai prose):  extractive 50.8% / semantic 64.6% / abstractive 27.4%
```

(Token counts via tiktoken `cl100k_base` — a real tokenizer, not a byte guess.)

## Install

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Optional local-LLM modes (`semantic`/`abstractive`) need [Ollama](https://ollama.com):

```powershell
ollama pull nomic-embed-text     # embeddings for semantic mode
ollama pull gemma3:12b           # abstractive (Thai-validated base)
```

## Run

```powershell
.\.venv\Scripts\python.exe -m sarup.server   # stdio (Claude Code)
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

## Auto-compression (no manual tool calls)

Instead of asking the model to call `sarup_compress`, install the **PostToolUse
hook** — it transparently compresses large `Read`/`Bash`/`Grep` outputs before
they enter context, caches the original, and leaves a `sarup_retrieve` hash.
Source-code reads are skipped for safety. See [hooks/README.md](hooks/README.md).

## Configuration (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `SARUP_DB_PATH` | *(in-memory)* | SQLite path for a persistent store |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint |
| `SARUP_ABSTRACTIVE_MODEL` | `gemma3:12b` | Model for abstractive mode |
| `SARUP_EMBED_MODEL` | `nomic-embed-text` | Model for semantic mode |

See [STACK.md](STACK.md) for the full stack and the techniques behind each mode.

## License

MIT
