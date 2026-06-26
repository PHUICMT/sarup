---
name: sarup-setup
description: Install, register, or verify the Sarup Thai-compression MCP server for Claude Code. Use when the user wants to set up Sarup, add the sarup MCP server, (re)connect it, install the auto-compression hook, or check that Sarup is working.
---

# Sarup setup

Set up **Sarup** — a Thai-first context-compression MCP server — for Claude Code.
Sarup is an MCP server (tools `sarup_compress` / `sarup_retrieve` / `sarup_stats`),
**not** an API proxy, so it can never break Claude Code: if it's down the tools just
disappear and Claude keeps working.

Work through these steps. Stop and report if a step fails.

## 1. Locate the Sarup repo

Find the clone directory (it contains `pyproject.toml` with `name = "sarup"` and a
`scripts/` folder). In order:

1. If `$SARUP_HOME` is set, use it.
2. If the current working directory is the Sarup repo, use it.
3. Otherwise ask the user for the path. If they don't have it cloned, offer:
   `git clone https://github.com/PHUICMT/sarup.git`

## 2. Run setup (creates venv, installs, registers the MCP at user scope)

From the repo root:

- **Windows (PowerShell):** `./scripts/setup.ps1 -All`
- **Linux / WSL / macOS:** `./scripts/setup.sh --all`

`-All` / `--all` also pulls the optional Ollama models (`nomic-embed-text`,
`gemma3:12b`) for the higher-ratio `semantic` / `pipeline` modes. Drop the flag for
a minimal MCP-only install (offline `extractive` mode still works with no Ollama).

If the scripts can't run, do it manually with the repo's venv python:

```
<venv-python> -m pip install -e <repo>
claude mcp add sarup --scope user --env "SARUP_DB_PATH=<home>/.sarup-cache.db" -- <venv-python> -m sarup.server
```

(Windows venv python: `<repo>/.venv/Scripts/python.exe`; Unix: `<repo>/.venv/bin/python`.)

## 3. Verify

Run `claude mcp list` and confirm `sarup` shows **Connected**. Tell the user to
**reload Claude Code** (the VS Code window or a new CLI session) so the tools load,
then they can call `sarup_compress(content, mode="auto")` in any project.

## 4. (Optional) auto-compression hook

Only offer this if the user asks for automatic compression. The `PostToolUse` hook
compresses large tool outputs (≥ `SARUP_HOOK_MIN_TOKENS`, default 400) and needs
Claude Code ≥ 2.1.186.

⚠️ **Do not install it globally on a machine used for coding** — it compresses large
`Bash`/`Grep` output, which can drop detail the model needs verbatim (recoverable via
`sarup_retrieve`, but inconvenient mid-task). Prefer per-project install in
documentation/Thai-heavy projects. To install: `python scripts/install.py --with-hook`
(add `--global` for `~/.claude`). See `hooks/README.md`.

## Notes

- Safe by design: no `ANTHROPIC_BASE_URL` proxy, no persistent env routing.
- Ollama is optional; modes degrade to offline `extractive` when it's down.
- Uninstall cleanly: `./scripts/uninstall.ps1` (or `./scripts/uninstall.sh`).
