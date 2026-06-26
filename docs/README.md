# docs/

Assets referenced by the top-level `README.md`.

## Recording the quickstart GIF

The README has a (commented-out) slot for `docs/quickstart.gif`. To produce it:

**Option A — asciinema + agg (crisp, small):**

```bash
asciinema rec demo.cast          # record the session, Ctrl-D to stop
agg demo.cast docs/quickstart.gif # render cast -> gif
```

**Option B — terminalizer:**

```bash
terminalizer record demo
terminalizer render demo -o docs/quickstart.gif
```

A good ~15-second script to record:

```text
1.  .\scripts\setup.ps1            # venv + install + register MCP
2.  (open Claude Code)
3.  sarup_compress("…long Thai paragraph…", mode="auto")
4.  show the result: 518 -> 154 tokens (70.3%), verified: true
5.  sarup_retrieve(hash=...)        # original restored byte-for-byte
```

Then uncomment the `![Sarup quickstart](docs/quickstart.gif)` line in `README.md`
and commit the gif. Keep it under ~2 MB so the README stays light.
