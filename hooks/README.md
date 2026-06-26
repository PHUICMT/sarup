# Sarup auto-compression hook

Compress large tool outputs **automatically** — no need for the model to call
`sarup_compress` by hand. A Claude Code `PostToolUse` hook intercepts big
`Read`/`Bash`/`Grep` results, replaces them with a compressed version
(`updatedToolOutput`), and caches the original so `sarup_retrieve(hash)` still
recovers it byte-for-byte.

> **Experimental — verify on your build.** The hook is invoked and emits a valid
> `updatedToolOutput`, but *applying* it is surface-dependent: as of testing the **VS Code
> extension (2.1.193) does not apply it** (verified live — the model still receives the full
> output), so it's a no-op there. The Claude Code
> [hooks docs](https://code.claude.com/docs/en/hooks) describe `updatedToolOutput` as
> replacing the result, so it may work on other/CLI builds. The manual `sarup_compress`
> tool works everywhere.

## How it stays 100% accurate

The hook caches the original into the **same store** the MCP server reads from
(via `SARUP_DB_PATH`) *before* substituting. The compressed text carries the
retrieval hash in a footer, so full detail is always one `sarup_retrieve` away.

## Install

Easiest: run `python scripts/install.py --with-hook`, which fills in the real paths.
Scope it:

- `--project "<dir>"` — install **only** into that project's `.claude/settings.json`
  (omit the value for the current dir). Recommended — keeps the hook out of code-heavy
  projects. The MCP server stays user-scoped.
- `--global` — into `~/.claude` (every project; avoid on coding machines).
- (no flag) — into this repo's own `.claude` (dev/testing).

Or add it by hand to `.claude/settings.json`
(project) or `~/.claude/settings.json` (global), replacing `<SARUP_DIR>` with your
clone path (on Linux/macOS the interpreter is `<SARUP_DIR>/.venv/bin/python`):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Read|Bash|Grep",
        "hooks": [
          {
            "type": "command",
            "command": "<SARUP_DIR>/.venv/Scripts/python.exe <SARUP_DIR>/hooks/sarup_hook.py",
            "timeout": 15
          }
        ]
      }
    ]
  },
  "env": {
    "SARUP_DB_PATH": "<SARUP_DIR>/.sarup-cache.db"
  }
}
```

Point the MCP server at the **same** `SARUP_DB_PATH` so retrieval works across
processes. **For code-heavy projects, prefer per-project install (or skip the hook)** —
it skips source-file reads, but compressing large `Bash`/`Grep` output can drop detail
the model may want verbatim.

## Tuning (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `SARUP_HOOK_MIN_TOKENS` | `400` | Only compress outputs with at least this many tokens (token-based so it's fair across Thai/English/code) |
| `SARUP_HOOK_MODE` | `auto` | `auto` (semantic if Ollama is up, else extractive), or force `extractive` (offline, ~1ms) / `semantic` (Ollama, higher ratio, slower) |
| `SARUP_DB_PATH` | *(in-memory)* | Shared SQLite store — **required** for cross-process retrieval |

## Safety

- **Source-code reads are skipped** (`.py`, `.ts`, `.json`, … ) — line-dropping
  would corrupt code. Only prose / logs / docs get compressed.
- Substitutes **only** when compression actually saved tokens.
- Any error in the hook is swallowed → your tool result is never broken.
