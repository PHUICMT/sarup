"""Sarup installer — wire Sarup into Claude Code on *this* machine, safely.

What it does:
  1. Detects the repo path, the venv Python, the cache DB path (machine-specific).
  2. Probes Ollama: is it running? which models? → picks the best mode + models.
  3. MERGES config into .mcp.json and .claude/settings.json without clobbering
     anything already there (existing servers / hooks / env are preserved; a
     timestamped .bak is written before any change).
  4. Idempotent — running twice changes nothing.

Usage:
    python scripts/install.py              # project-level config (default)
    python scripts/install.py --with-hook  # also install the auto-compression hook
    python scripts/install.py --pull       # pull recommended Ollama models if missing
    python scripts/install.py --global     # write to ~/.claude instead of the project

Pure stdlib. Never deletes keys it didn't add.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

# Preference order when choosing a generation model for abstractive/pipeline.
_GEN_PREFS = ["gemma3:12b", "gemma3", "gemma4:12b", "gemma4", "qwen3:8b", "qwen3"]
_EMBED_PREFS = ["nomic-embed-text", "bge-m3", "mxbai-embed-large"]


# ── detection ────────────────────────────────────────────────────────────────

def venv_python() -> str:
    """Path to this repo's venv Python, or the current interpreter as fallback."""
    win = REPO / ".venv" / "Scripts" / "python.exe"
    nix = REPO / ".venv" / "bin" / "python"
    if win.exists():
        return str(win)
    if nix.exists():
        return str(nix)
    return sys.executable


def ollama_models() -> list[str] | None:
    """List installed Ollama model names, or None if Ollama is not reachable."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return None


def _pick(models: list[str], prefs: list[str]) -> str | None:
    for p in prefs:
        for m in models:
            if m == p or m.startswith(p + ":") or m.split(":")[0] == p:
                return m
    return None


def recommend(models: list[str] | None) -> dict:
    """Decide hook mode + models from what's actually installed."""
    if models is None:
        return {"ollama": False, "hook_mode": "extractive", "embed": None, "gen": None}
    embed = _pick(models, _EMBED_PREFS)
    gen = _pick(models, _GEN_PREFS)
    # semantic needs embeddings; without them, stay on offline extractive.
    hook_mode = "auto" if embed else "extractive"
    return {"ollama": True, "hook_mode": hook_mode, "embed": embed, "gen": gen}


# ── safe JSON merge ──────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            print(f"  ! {path.name} exists but isn't valid JSON — leaving it untouched.")
            raise
    return {}

def _backup(path: Path) -> None:
    if path.exists():
        bak = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
        shutil.copy2(path, bak)
        print(f"  - backed up {path.name} -> {bak.name}")

def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_mcp(path: Path, py: str, db: str) -> bool:
    data = _load(path)
    servers = data.setdefault("mcpServers", {})
    desired = {"command": py, "args": ["-m", "sarup.server"], "env": {"SARUP_DB_PATH": db}}
    if servers.get("sarup") == desired:
        print("  = mcpServers.sarup already up to date")
        return False
    _backup(path)
    servers["sarup"] = desired  # replaces only the 'sarup' key
    _write(path, data)
    print(f"  + registered MCP server 'sarup' in {path.name}")
    return True


def merge_hook(path: Path, py: str, hook: str, env: dict) -> bool:
    data = _load(path)
    hooks = data.setdefault("hooks", {})
    post = hooks.setdefault("PostToolUse", [])
    cmd = f'"{py}" "{hook}"'
    already = any(
        "sarup_hook.py" in h.get("command", "")
        for grp in post for h in grp.get("hooks", [])
    )
    changed = False
    if not already:
        _backup(path)
        post.append({
            "matcher": "Read|Bash|Grep",
            "hooks": [{"type": "command", "command": cmd, "timeout": 15}],
        })
        changed = True
        print("  + added PostToolUse sarup hook")
    else:
        print("  = PostToolUse sarup hook already present")
    # Merge env keys without clobbering unrelated ones.
    cur_env = data.setdefault("env", {})
    for k, v in env.items():
        if cur_env.get(k) != v:
            if not changed:
                _backup(path)
            cur_env[k] = v
            changed = True
            print(f"  + set env {k}")
    if changed:
        _write(path, data)
    return changed


# ── uninstall (remove only what Sarup added) ──────────────────────────────────

def unmerge_mcp(path: Path) -> bool:
    if not path.exists():
        return False
    data = _load(path)
    servers = data.get("mcpServers", {})
    if "sarup" not in servers:
        print("  = mcpServers.sarup not present")
        return False
    _backup(path)
    del servers["sarup"]
    if not servers:
        data.pop("mcpServers", None)
    _write(path, data)
    print(f"  - removed MCP server 'sarup' from {path.name}")
    return True


def unmerge_hook(path: Path) -> bool:
    if not path.exists():
        return False
    data = _load(path)
    changed = False
    post = data.get("hooks", {}).get("PostToolUse", [])
    kept = [
        grp for grp in post
        if not any("sarup_hook.py" in h.get("command", "") for h in grp.get("hooks", []))
    ]
    if len(kept) != len(post):
        _backup(path)
        changed = True
        if kept:
            data["hooks"]["PostToolUse"] = kept
        else:
            data["hooks"].pop("PostToolUse", None)
            if not data["hooks"]:
                data.pop("hooks", None)
        print("  - removed PostToolUse sarup hook")
    # Drop SARUP_* env keys we own.
    env = data.get("env", {})
    sarup_keys = [k for k in env if k.startswith("SARUP_")]
    if sarup_keys:
        if not changed:
            _backup(path)
        for k in sarup_keys:
            del env[k]
            print(f"  - removed env {k}")
        if not env:
            data.pop("env", None)
        changed = True
    if changed:
        _write(path, data)
    elif not (len(kept) != len(post)):
        print("  = no sarup hook/env to remove")
    return changed


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Install Sarup into Claude Code (safe merge).")
    ap.add_argument("--with-hook", action="store_true", help="also install the auto-compression hook")
    ap.add_argument("--pull", action="store_true", help="pull recommended Ollama models if missing")
    ap.add_argument("--global", dest="is_global", action="store_true", help="write to ~/.claude")
    ap.add_argument("--uninstall", action="store_true", help="remove only what Sarup added")
    args = ap.parse_args()

    base = Path.home() / ".claude" if args.is_global else REPO
    mcp_path = (Path.home() / ".claude.json") if args.is_global else (REPO / ".mcp.json")
    settings_path = base / "settings.json" if args.is_global else (REPO / ".claude" / "settings.json")

    if args.uninstall:
        print("Sarup uninstaller (removes only Sarup's entries):")
        unmerge_mcp(mcp_path)
        unmerge_hook(settings_path)
        print("\nDone. Restart Claude Code. Your other servers/hooks were left intact.")
        return

    py = venv_python()
    db = str(REPO / ".sarup-cache.db")
    hook = str(REPO / "hooks" / "sarup_hook.py")
    models = ollama_models()
    rec = recommend(models)

    print("Sarup installer")
    print(f"  repo:   {REPO}")
    print(f"  python: {py}")
    print(f"  db:     {db}")
    if rec["ollama"]:
        print(f"  ollama: UP - embed={rec['embed'] or 'MISSING'}, gen={rec['gen'] or 'MISSING'}")
        print(f"          -> hook mode: {rec['hook_mode']}")
    else:
        print("  ollama: not running → offline 'extractive' mode (still works fully)")

    if args.pull and rec["ollama"]:
        for want, have in (("nomic-embed-text", rec["embed"]), ("gemma3:12b", rec["gen"])):
            if not have:
                print(f"  - pulling {want} ...")
                subprocess.run(["ollama", "pull", want], check=False)

    print("\nWiring config (merge, non-destructive):")
    merge_mcp(mcp_path, py, db)

    if args.with_hook:
        env = {"SARUP_DB_PATH": db, "SARUP_HOOK_MODE": rec["hook_mode"]}
        if rec["gen"]:
            env["SARUP_ABSTRACTIVE_MODEL"] = rec["gen"]
        merge_hook(settings_path, py, hook, env)
    else:
        print("  (skipped hook — pass --with-hook to enable auto-compression)")

    print("\nDone. Restart Claude Code to load the 'sarup' MCP server.")
    if not rec["ollama"]:
        print("Tip: install Ollama + `ollama pull nomic-embed-text` to unlock semantic mode (~65%).")


if __name__ == "__main__":
    main()
