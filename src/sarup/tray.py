"""Sarup system-tray app — run the proxy quietly in the background.

Starts `sarup.proxy` (compression on by default) in a worker thread and shows a
tray icon with: compression toggle, cumulative tokens saved, Claude Code routing
toggle, and quit. Designed to auto-start on login (see scripts/install-autostart.ps1).

The proxy uses the offline `extractive` mode by default, so it does NOT depend on
Ollama — Ollama may start later (or never); compression keeps working.

Run:
    sarup-tray                 # prints starting/started, spawns detached, returns —
                               # close the console and the tray keeps running.

Needs the [tray] extra:  pip install -e ".[tray]"
"""

from __future__ import annotations

import os
import subprocess
import threading

from . import proxy


def _run_proxy() -> None:
    import uvicorn

    config = uvicorn.Config(proxy.app, host="127.0.0.1", port=proxy.PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # not the main thread
    server.run()


def _make_icon_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (64, 64), (22, 163, 74))  # green = lossless/safe
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill=(255, 255, 255))
    d.text((22, 20), "S", fill=(22, 163, 74))
    return img


# ── Claude Code routing (persistent user env var) ────────────────────────────

def _base_url() -> str:
    return f"http://localhost:{proxy.PORT}"


def _routing_on() -> bool:
    return os.environ.get("ANTHROPIC_BASE_URL", "") == _base_url()


def _set_routing(on: bool) -> None:
    if on:
        subprocess.run(["setx", "ANTHROPIC_BASE_URL", _base_url()], capture_output=True)
        os.environ["ANTHROPIC_BASE_URL"] = _base_url()
    else:
        # Only clear OUR value, never someone else's custom base URL.
        if os.environ.get("ANTHROPIC_BASE_URL", "") == _base_url():
            subprocess.run(["reg", "delete", "HKCU\\Environment", "/v", "ANTHROPIC_BASE_URL", "/f"],
                           capture_output=True)
            os.environ.pop("ANTHROPIC_BASE_URL", None)
    # New terminals pick this up; existing ones are unaffected.


def _toggle_routing(icon, item) -> None:
    _set_routing(not _routing_on())


def _proxy_healthy(timeout: float = 1.0) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://localhost:{proxy.PORT}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> None:
    """Launcher: spawn the tray detached so the console returns and can be closed.

    Re-execs this module with SARUP_TRAY_CHILD=1 via pythonw (no console window,
    survives the parent console closing). The child runs `_run_tray()`.
    """
    import sys
    import time

    if os.environ.get("SARUP_TRAY_CHILD") == "1":
        _run_tray()
        return

    if _proxy_healthy():
        print(f"Sarup tray: already running (proxy on :{proxy.PORT}).")
        return

    print("Sarup tray: starting...")
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    launcher = pyw if os.path.exists(pyw) else exe
    env = {**os.environ, "SARUP_TRAY_CHILD": "1"}

    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True, env=env)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([launcher, "-m", "sarup.tray"], **kwargs)

    for _ in range(20):  # wait up to ~5s for the proxy to answer
        if _proxy_healthy():
            print(f"Sarup tray: started (proxy on :{proxy.PORT}). You can close this console.")
            return
        time.sleep(0.25)
    print("Sarup tray: launched (proxy not confirmed yet — check the tray icon).")


def _run_tray() -> None:
    import pystray

    # The tray exists to auto-compress, so default compression ON (unless the
    # user explicitly set SARUP_PROXY_COMPRESS=0).
    proxy.COMPRESS_ENABLED = os.environ.get("SARUP_PROXY_COMPRESS", "1") != "0"

    threading.Thread(target=_run_proxy, daemon=True).start()

    def toggle_compress(icon, item):
        proxy.COMPRESS_ENABLED = not proxy.COMPRESS_ENABLED

    def quit_tray(icon, item):
        # Closing the tray must always return to a clean state: stop routing so a
        # new Claude Code session never points at a now-dead proxy.
        _set_routing(False)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: f"Sarup proxy :{proxy.PORT}", None, enabled=False),
        pystray.MenuItem("Compression", toggle_compress, checked=lambda item: proxy.COMPRESS_ENABLED),
        pystray.MenuItem(lambda item: f"Saved: {proxy.total_saved():,} tok", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Route Claude Code", _toggle_routing, checked=lambda item: _routing_on()),
        pystray.MenuItem("Quit (stops proxy + unroutes)", quit_tray),
    )
    pystray.Icon("sarup", _make_icon_image(), "Sarup proxy", menu).run()


if __name__ == "__main__":
    main()
