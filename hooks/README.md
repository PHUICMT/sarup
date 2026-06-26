# Sarup auto-compression hook

Compress large tool outputs **automatically** — no need for the model to call
`sarup_compress` by hand. A Claude Code `PostToolUse` hook intercepts big
`Read`/`Bash`/`Grep` results, replaces them with a compressed version
(`updatedToolOutput`), and caches the original so `sarup_retrieve(hash)` still
recovers it byte-for-byte.

> **⚠️ Status (June 2026): experimental — pending Claude Code support.**
> The hook is invoked correctly and emits a valid `updatedToolOutput`, but
> Claude Code **2.1.167 and 2.1.193 do not apply it** — the model still receives
> the full tool result (verified live). The [hooks docs](https://code.claude.com/docs/en/hooks)
> say `updatedToolOutput` replaces the result, so this should start working on a
> build that honors it. **Until then, use the manual `sarup_compress` tool**
> (fully working). This hook is ready for the day Claude Code applies the field.

## How it stays 100% accurate

The hook caches the original into the **same store** the MCP server reads from
(via `SARUP_DB_PATH`) *before* substituting. The compressed text carries the
retrieval hash in a footer, so full detail is always one `sarup_retrieve` away.

## Install

Add to `.claude/settings.json` (project) or `~/.claude/settings.json` (global):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Read|Bash|Grep",
        "hooks": [
          {
            "type": "command",
            "command": "d:\\WORK\\Sarup\\.venv\\Scripts\\python.exe d:\\WORK\\Sarup\\hooks\\sarup_hook.py",
            "timeout": 15
          }
        ]
      }
    ]
  },
  "env": {
    "SARUP_DB_PATH": "d:\\WORK\\Sarup\\.sarup-cache.db"
  }
}
```

Point the MCP server at the **same** `SARUP_DB_PATH` so retrieval works across
processes.

## Tuning (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `SARUP_HOOK_MIN_CHARS` | `4000` | Only compress outputs larger than this |
| `SARUP_HOOK_MODE` | `extractive` | `extractive` (offline, ~1ms) or `semantic` (Ollama, higher ratio, slower) |
| `SARUP_DB_PATH` | *(in-memory)* | Shared SQLite store — **required** for cross-process retrieval |

## Safety

- **Source-code reads are skipped** (`.py`, `.ts`, `.json`, … ) — line-dropping
  would corrupt code. Only prose / logs / docs get compressed.
- Substitutes **only** when compression actually saved tokens.
- Any error in the hook is swallowed → your tool result is never broken.
