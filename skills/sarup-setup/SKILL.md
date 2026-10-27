---
name: sarup-setup
description: Install, verify or extend the Sarup Thai-compression MCP server. Use when the user wants to set up Sarup, check that it works, turn on the optional Ollama modes (semantic, abstractive, pipeline), fix a Sarup result that carries a "setup" field, or install the auto-compression hook.
---

# Sarup setup

Sarup is an MCP server (tools `sarup_compress`, `sarup_retrieve`, `sarup_stats`) plus an
optional hook that compresses large Read/Bash/Grep output. It is not an API proxy: if it
is down, its tools disappear and nothing else breaks.

Work through the steps in order and stop to report if one fails.

## 1. Find out what is installed

- Sarup tools named `mcp__plugin_sarup_sarup__*` are available: it is installed as a
  **plugin**. Go to step 3.
- Tools named `sarup_*` without the plugin prefix: it was installed **from a clone**. Go to
  step 3; for clone maintenance see step 5.
- No Sarup tools: go to step 2.

## 2. Install (plugin, recommended)

The plugin runs Sarup through `uvx`, so the machine needs **uv**. Check with `uv --version`.
If it is missing, give the user the command for their OS and wait for them to run it:

- Windows: `winget install astral-sh.uv` (or `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`)
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

Then tell the user to type these two commands themselves (they are interactive
commands, not shell commands):

```text
/plugin marketplace add PHUICMT/sarup
/plugin install sarup@sarup
```

After the install they reload (`/reload-plugins` or a new window). The first start takes
a few seconds while uvx builds the environment.

## 3. Verify

Call `sarup_stats`. Check:

- `store` is a file path. "memory only" means the hook can't hand originals to the server;
  for a clone install, set `SARUP_DB_PATH` on both (the plugin does this itself).
- `backend` shows what the optional modes can use (next step).

Then call `sarup_compress` on a few paragraphs of Thai text and confirm `verified: true`.

## 4. Optional: Ollama for the higher-ratio modes

Without Ollama everything still works: the hook and the default `extractive` mode never
use it. Only `semantic`, `abstractive` and `pipeline` need it, and they fall back to
extractive with a `setup` field naming what is missing.

Read `backend` from `sarup_stats` and offer only what is missing:

| `backend` shows | What it enables | User runs |
|---|---|---|
| `"ollama": false` | anything below | install from https://ollama.com (Windows: `winget install Ollama.Ollama`, macOS: `brew install ollama`), then start it |
| `"embed_model": null` | `semantic`, stage 1 of `pipeline` | `ollama pull nomic-embed-text` (274 MB) |
| `"rewrite_model": null` | `abstractive`, stage 2 of `pipeline` | `ollama pull gemma4:12b` (7.6 GB, needs a GPU with about 8 GB or patience on CPU) |

Explain the trade-off before the large download: on the Thai QA eval, `abstractive` kept
91% of facts at 42% fewer tokens versus 69% at 30% for `extractive`, but it takes several
seconds per call. Call `sarup_stats` again afterwards to confirm.

## 5. Clone installs only

For a clone (development, or no plugin support), from the repo root:

- Windows: `./scripts/setup.ps1 -All`
- Linux / WSL / macOS: `./scripts/setup.sh --all`

The hook for a single project:
`<venv-python> <repo>/scripts/install.py --with-hook --project "<project-dir>"`
(remove with `--uninstall --project "<project-dir>"`). The plugin installs the hook for
every project; it skips source-file reads, but in code-heavy projects large Bash/Grep
output may lose detail the model wants verbatim (recoverable with `sarup_retrieve`).

## Troubleshooting

- Hook seems to do nothing: set `SARUP_HOOK_LOG` to a file path before starting; each call
  appends one JSON line saying whether it substituted.
- Plugin MCP server fails to start: usually uv is missing or the first `uvx` build timed out;
  run `uv --version`, then reload.
