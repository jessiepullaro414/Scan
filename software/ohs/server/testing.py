"""Helpers to run the app on a real uvicorn server in a background thread (tests, smoke tests).

FastAPI's ``TestClient`` needs ``httpx``, which is not a dependency, so the tests talk to a real
server over TCP: :class:`ServerThread` starts uvicorn on a free port and stops it again (running
the app's lifespan, so the shutdown path is exercised too). :func:`free_port` finds a port and
:func:`recv_until` reads WebSocket messages until a predicate holds.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable

from ohs.server.protocol import unpack_binary

__all__ = ["ServerThread", "free_port", "recv_until", "decode"]


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


class ServerThread:
    """``with ServerThread(app) as srv: requests.get(srv.url + "/api/health")``."""

    def __init__(self, app: Any, host: str = "127.0.0.1", port: int | None = None, log_level: str = "warning") -> None:
        import uvicorn

        self.app = app
        self.host = host
        self.port = int(port or free_port(host))
        self.config = uvicorn.Config(app, host=host, port=self.port, log_level=log_level, access_log=False, lifespan="on",
                                     ws_per_message_deflate=False, timeout_graceful_shutdown=3)
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, name="ohs-test-uvicorn", daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/api/ws/live"

    def start(self, timeout: float = 30.0) -> "ServerThread":
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the test server did not start")
            time.sleep(0.02)
        return self

    def stop(self, timeout: float = 30.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)
        if self.thread.is_alive():
            self.server.force_exit = True
            self.thread.join(5.0)

    def __enter__(self) -> "ServerThread":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def decode(message: str | bytes) -> tuple[dict, Any]:
    """``(header or JSON message, binary payload or None)``."""
    import json

    if isinstance(message, (bytes, bytearray, memoryview)):
        header, payload, _ = unpack_binary(message)
        return header, payload
    return json.loads(message), None


def recv_until(ws: Any, predicate: Callable[[dict, Any, Any], bool], timeout: float = 20.0) -> list[tuple[dict, Any, Any]]:
    """Read ``(message, payload, raw)`` from a ``websockets.sync`` connection until ``predicate`` is true.

    Returns everything read (the last item satisfied the predicate); raises ``TimeoutError``.
    """
    out: list[tuple[dict, Any, Any]] = []
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            types = sorted({m.get("type", "?") for m, _, _ in out})
            raise TimeoutError(f"no matching message within {timeout:.0f} s (got {len(out)} messages: {types})")
        try:
            raw = ws.recv(timeout=left)
        except TimeoutError:
            continue
        msg, payload = decode(raw)
        out.append((msg, payload, raw))
        if predicate(msg, payload, raw):
            return out
