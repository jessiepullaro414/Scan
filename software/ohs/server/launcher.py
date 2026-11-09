"""``ohs app``: run OpenHandScan Studio (uvicorn in a background thread + a pywebview window).

:func:`run_app`:

1. checks that the port is free (a second instance would fail confusingly otherwise);
2. starts uvicorn with the app from :func:`ohs.server.app.create_app` in thread ``ohs-uvicorn``
   (no per-message deflate: JPEG and mesh payloads do not compress, and compressing them would
   block the event loop);
3. opens a pywebview window "OpenHandScan Studio" (1440x900, resizable) on the app's URL and
   blocks until it is closed. With ``window=False`` (``--no-window``) it blocks until Ctrl-C
   instead; ``open_browser`` (``--open-browser``) opens the URL in the default browser. If
   pywebview is missing or the window cannot open, the browser is used instead;
4. on window close, Ctrl-C, or any error, asks uvicorn to exit, which runs the app's lifespan
   shutdown (a running scan is stopped and saved, emitters off, devices closed, jobs cancelled),
   and finally calls :meth:`Studio.shutdown` itself as a safety net (it is idempotent). An
   ``atexit`` hook does the same if the interpreter exits some other way.

Ctrl-C while the window is open: a SIGINT handler (and, on Windows, a console control handler,
because the GUI loop runs in native code) closes the window, which then shuts down as above.
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import signal
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

__all__ = ["run_app", "port_free", "WINDOW_TITLE", "WINDOW_SIZE"]

log = logging.getLogger(__name__)

WINDOW_TITLE = "OpenHandScan Studio"
WINDOW_SIZE = (1440, 900)


def port_free(host: str, port: int) -> bool:
    """True if ``host:port`` can be bound right now."""
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(fam, socket.SOCK_STREAM) as s:
        if sys.platform.startswith("win"):
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        try:
            s.bind((host, int(port)))
        except OSError:
            return False
    return True


def _browser_host(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


class _Server:
    """uvicorn in a thread, started and stopped from the caller's thread."""

    def __init__(self, app: Any, host: str, port: int, log_level: str = "warning") -> None:
        import uvicorn

        self.config = uvicorn.Config(app, host=host, port=int(port), log_level=log_level, access_log=False,
                                     lifespan="on", ws_per_message_deflate=False, timeout_graceful_shutdown=3)
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self._run, name="ohs-uvicorn", daemon=True)
        self.error: BaseException | None = None

    def _run(self) -> None:
        try:
            self.server.run()
        except BaseException as e:  # reported by start()
            self.error = e
            log.exception("the web server stopped with an error")

    def start(self, timeout: float = 60.0) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive():
                raise RuntimeError(f"the web server did not start: {self.error or 'see the log above'}")
            if time.monotonic() > deadline:
                raise RuntimeError(f"the web server did not start within {timeout:.0f} s")
            time.sleep(0.05)

    def stop(self, timeout: float = 20.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)
        if self.thread.is_alive():
            log.warning("the web server did not stop in %.0f s; forcing it", timeout)
            self.server.force_exit = True
            self.thread.join(5.0)


def _install_console_handler(on_ctrl: Callable[[], None]) -> Any:
    """Windows: run ``on_ctrl`` on Ctrl-C / Ctrl-Break / console close even while native code runs."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes

        handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def handler(ctrl_type: int) -> int:
            if ctrl_type in (0, 1, 2):  # CTRL_C, CTRL_BREAK, CTRL_CLOSE
                threading.Thread(target=on_ctrl, name="ohs-ctrl-c", daemon=True).start()
                return 1
            return 0

        cb = handler_type(handler)
        if ctypes.windll.kernel32.SetConsoleCtrlHandler(cb, True):
            return cb  # keep a reference: the callback must outlive the registration
    except Exception as e:  # best effort
        log.debug("no console control handler: %s", e)
    return None


def _remove_console_handler(cb: Any) -> None:
    if cb is None:
        return
    with contextlib.suppress(Exception):
        import ctypes

        ctypes.windll.kernel32.SetConsoleCtrlHandler(cb, False)


def _run_window(url: str, echo: Callable[[str], None], webview_module: Any = None) -> bool:
    """Show the app window until it is closed. False if no window could be shown."""
    try:
        webview = webview_module or __import__("webview")
    except ImportError:
        echo("pywebview is not installed (pip install -e .[app]): opening the app in your browser instead")
        return False
    # pywebview blocks downloads by default; the app's STL/PLY/report buttons are plain downloads
    settings = getattr(webview, "settings", None)
    if isinstance(settings, dict):
        settings["ALLOW_DOWNLOADS"] = True
    try:
        window = webview.create_window(WINDOW_TITLE, url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], resizable=True,
                                       min_size=(1024, 640), background_color="#14171d")
    except Exception as e:
        echo(f"the app window could not be created ({e}): opening the app in your browser instead")
        return False

    def close_window() -> None:
        with contextlib.suppress(Exception):
            window.destroy()

    old = None
    with contextlib.suppress(ValueError, OSError):  # not in the main thread
        old = signal.signal(signal.SIGINT, lambda *_: close_window())
    cb = _install_console_handler(close_window)
    storage = Path("~/.ohs/webview").expanduser()
    try:
        with contextlib.suppress(OSError):
            storage.mkdir(parents=True, exist_ok=True)
        webview.start(private_mode=False, storage_path=str(storage))
        return True
    except Exception as e:  # no GUI backend / WebView2 runtime
        echo(f"the app window could not open ({e}): opening the app in your browser instead")
        return False
    finally:
        _remove_console_handler(cb)
        if old is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signal.SIGINT, old)


def _wait_for_ctrl_c(server: _Server, echo: Callable[[str], None]) -> None:
    echo("Press Ctrl-C to stop.")
    while server.thread.is_alive():
        server.thread.join(0.5)


def run_app(cfg: Any, *, host: str | None = None, port: int | None = None, window: bool = True,
            open_browser: bool = False, echo: Callable[[str], None] = print, projects_dir: Any = None,
            static_dir: Any = None, demo_dir: Any = None, webview_module: Any = None, log_level: str = "warning") -> int:
    """Run the app until its window closes (or Ctrl-C with ``window=False``); returns the exit code."""
    from ohs.server.app import create_app

    host = str(host or cfg.app.host)
    port = int(port or cfg.app.port)
    if not port_free(host, port):
        raise RuntimeError(f"port {port} on {host} is already in use: is OpenHandScan Studio already running? "
                           f"Open http://{_browser_host(host)}:{port}/ or start with --port <another port>")
    app = create_app(cfg, projects_dir=projects_dir, static_dir=static_dir, demo_dir=demo_dir)
    studio = app.state.studio
    server = _Server(app, host, port, log_level=log_level)
    atexit.register(studio.shutdown)
    url = f"http://{_browser_host(host)}:{port}/"
    interrupted = False
    try:
        server.start()
        echo(f"OpenHandScan Studio is running at {url}")
        if host in ("0.0.0.0", "::"):
            echo("warning: the app is reachable from your network (no password): use it on a network you trust only")
        if not (studio.static_dir / "index.html").is_file():
            echo("note: the UI is not built yet (npm run build in software/ui); the page explains how")
        if open_browser:
            webbrowser.open(url)
        shown = False
        if window:
            shown = _run_window(url, echo, webview_module)
        if not shown:
            if window and not open_browser:  # the window could not open: use the browser instead
                webbrowser.open(url)
            _wait_for_ctrl_c(server, echo)
    except KeyboardInterrupt:
        interrupted = True
        echo("Stopping...")
    finally:
        server.stop()
        studio.shutdown()
        with contextlib.suppress(Exception):
            atexit.unregister(studio.shutdown)
    echo("OpenHandScan Studio stopped.")
    return 130 if interrupted else 0
