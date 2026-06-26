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


_proxy_thread: threading.Thread | None = None
_auto_unrouted = False  # True while the watchdog has cleared routing due to a dead proxy
_last_healthy = True    # cached health for the menu (avoid blocking the UI on a probe)


def _run_proxy() -> None:
    import uvicorn

    config = uvicorn.Config(proxy.app, host="127.0.0.1", port=proxy.PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # not the main thread
    server.run()


def _start_proxy_thread() -> None:
    global _proxy_thread
    _proxy_thread = threading.Thread(target=_run_proxy, daemon=True)
    _proxy_thread.start()


def _watchdog() -> None:
    """Keep the proxy alive and never let a dead proxy brick Claude Code.

    Every few seconds: if the proxy is unhealthy, revive the worker thread; if it
    still won't come back, clear routing so NEW Claude Code sessions fall back to
    talking to the API directly (a dead localhost:PORT would otherwise hang them).
    When the proxy recovers, routing the watchdog itself cleared is restored.
    Already-open sessions inherit env at launch, so they're unaffected either way.
    """
    import time

    global _auto_unrouted, _last_healthy
    fails = 0
    while True:
        time.sleep(8)
        if _proxy_healthy():
            fails, _last_healthy = 0, True
            if _auto_unrouted:  # proxy is back — restore the routing we cleared
                _set_routing(True)
                _auto_unrouted = False
            continue

        fails += 1
        _last_healthy = False
        if _proxy_thread is None or not _proxy_thread.is_alive():
            _start_proxy_thread()  # worker died — respawn it (re-binds the port)
        if fails >= 2 and _routing_on():  # still down after a grace cycle → fall back
            _set_routing(False)
            _auto_unrouted = True


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

    # Direct (foreground) tray: the detached child, autostart, or `--child`.
    if os.environ.get("SARUP_TRAY_CHILD") == "1" or "--child" in sys.argv:
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

    _start_proxy_thread()
    threading.Thread(target=_watchdog, daemon=True).start()

    def toggle_compress(icon, item):
        proxy.COMPRESS_ENABLED = not proxy.COMPRESS_ENABLED

    def quit_tray(icon, item):
        # Closing the tray must always return to a clean state: stop routing so a
        # new Claude Code session never points at a now-dead proxy.
        _set_routing(False)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: f"Sarup proxy :{proxy.PORT}" + ("" if _last_healthy else " - DOWN"),
                         None, enabled=False),
        pystray.MenuItem("Compression", toggle_compress, checked=lambda item: proxy.COMPRESS_ENABLED),
        pystray.MenuItem(lambda item: f"Saved: {proxy.total_saved():,} tok", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Route Claude Code", _toggle_routing, checked=lambda item: _routing_on()),
        pystray.MenuItem("Quit (stops proxy + unroutes)", quit_tray),
    )
    pystray.Icon("sarup", _make_icon_image(), "Sarup proxy", menu).run()


if __name__ == "__main__":
    main()
