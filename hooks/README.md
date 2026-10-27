# Sarup auto-compression hook

Compress large tool outputs **automatically** — no need for the model to call
`sarup_compress` by hand. A Claude Code `PostToolUse` hook intercepts big
`Read`/`Bash`/`Grep` results, replaces them with a compressed version
(`updatedToolOutput`), and caches the original so `sarup_retrieve(hash)` still
recovers it byte-for-byte.

> **Fixed in October 2026.** Earlier versions returned the compressed text as a plain
> string, which Claude Code drops for built-in tools: `updatedToolOutput` must keep the
> tool's own shape (`{stdout, stderr, …}` for Bash, `{type, file: {content, …}}` for Read).
> That is why the hook looked like a no-op on every surface. It now keeps the shape;
> verified on the CLI (2.1.289) through the plugin. Set `SARUP_HOOK_LOG` to a file path to
> see one JSON line per call on your own build.

The [plugin](../README.md#as-a-claude-code-plugin-easiest) installs and wires this hook
for you; the rest of this page is for installs from a clone.

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
            "command": "<SARUP_DIR>/.venv/Scripts/sarup-hook --db <SARUP_DIR>/.sarup-cache.db",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

Point the MCP server at the **same** file (`SARUP_DB_PATH`) so retrieval works across
processes. **For code-heavy projects, prefer per-project install (or skip the hook)** —
it skips source-file reads, but compressing large `Bash`/`Grep` output can drop detail
the model may want verbatim.

## Tuning (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `SARUP_HOOK_MIN_TOKENS` | `400` | Only compress outputs with at least this many tokens (token-based so it's fair across Thai/English/code) |
| `SARUP_HOOK_MODE` | `extractive` | `extractive` (offline, ~1 ms), or `auto` / `semantic` (Ollama, ~2 s per call; on Windows `auto` also costs ~2 s when Ollama is absent) |
| `SARUP_DB_PATH` | *(in-memory)* | Shared SQLite store, **required** for cross-process retrieval. `sarup-hook --db PATH` sets it too |
| `SARUP_HOOK_LOG` | *(off)* | File to append one JSON line per hook call: event, tool, size, whether it substituted |

## Safety

- **Source-code reads are skipped** (`.py`, `.ts`, `.json`, … ) — line-dropping
  would corrupt code. Only prose / logs / docs get compressed.
- Substitutes **only** when compression actually saved tokens.
- Any error in the hook is swallowed → your tool result is never broken.
