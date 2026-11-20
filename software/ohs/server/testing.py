"""Helpers to run the app on a real uvicorn server in a background thread (tests, smoke tests).

FastAPI's ``TestClient`` needs ``httpx``, which is not a dependency, so the tests talk to a real
server over TCP: :class:`ServerThread` starts uvicorn on a free port and stops it again (running
the app's lifespan, so the shutdown path is exercised too). :func:`free_port` finds a port,
:func:`recv_until` reads WebSocket messages until a predicate holds and :class:`LiveReader`
keeps a live socket read while a test does other things.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable

from ohs.server.protocol import unpack_binary

__all__ = ["ServerThread", "LiveReader", "free_port", "recv_until", "decode"]


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


class LiveReader:
    """A ``websockets.sync`` connection that is read all the time, by a background thread.

    A ``websockets.sync`` client stops reading its socket once 16 messages wait unread (its
    ``max_queue``), so it no longer sees the server's pong either, and its own keepalive closes
    the connection (1011, "keepalive ping timeout") about 40 s after a test stops reading. A test
    that keeps the live socket open across long phases (views, a solve) uses this instead: the
    thread keeps the socket read and the messages wait here (the newest ``keep`` of them; the
    number dropped is in ``dropped``) for :meth:`recv` and :func:`recv_until`. The client's
    keepalive stays on, so a server that really stops answering still fails the test. Every
    ``ping_every`` s it also pings the server and keeps the round trips (``rtts``, ``max_rtt``):
    how long the server's event loop took to answer.
    """

    def __init__(self, url: str, *, keep: int = 4096, ping_every: float = 1.0, **connect_kw: Any) -> None:
        import collections

        self.url = url
        self.connect_kw = {"max_size": None, "open_timeout": 5, **connect_kw}
        self.ping_every = float(ping_every)
        self._items: collections.deque = collections.deque(maxlen=int(keep))
        self._cond = threading.Condition()
        self._closed: BaseException | None = None
        self._stop = threading.Event()
        self.dropped = 0
        self.rtts: list[float] = []
        self.ws: Any = None

    def __enter__(self) -> "LiveReader":
        from websockets.sync.client import connect

        self._cm = connect(self.url, **self.connect_kw)
        self.ws = self._cm.__enter__()  # websockets 17 wants connect() used as a context manager
        self._threads = [threading.Thread(target=self._read, name="ohs-test-ws-read", daemon=True)]
        if self.ping_every > 0:
            self._threads.append(threading.Thread(target=self._ping, name="ohs-test-ws-ping", daemon=True))
        for th in self._threads:
            th.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._cm.__exit__(None, None, None)  # closes the connection; the reader thread then ends
        for th in self._threads:
            th.join(5.0)

    @property
    def max_rtt(self) -> float:
        return max(self.rtts, default=0.0)

    def _read(self) -> None:
        while True:
            try:
                raw = self.ws.recv()
            except BaseException as e:  # closed (by us or the server), or the keepalive gave up
                with self._cond:
                    self._closed = e
                    self._cond.notify_all()
                return
            with self._cond:
                if len(self._items) == self._items.maxlen:
                    self.dropped += 1
                self._items.append(raw)
                self._cond.notify_all()

    def _ping(self) -> None:
        while not self._stop.wait(self.ping_every):
            t = time.perf_counter()
            try:
                pong = self.ws.ping()
            except BaseException:
                return
            if not pong.wait(60.0):
                self.rtts.append(60.0)
                return
            self.rtts.append(time.perf_counter() - t)

    def send(self, message: Any) -> None:
        self.ws.send(message)

    def recv(self, timeout: float | None = None) -> Any:
        """The oldest unread message (``TimeoutError`` after ``timeout``; the close reason once closed)."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._items or self._closed is not None, timeout):
                raise TimeoutError("no message")
            if self._items:
                return self._items.popleft()
            raise self._closed  # type: ignore[misc]


def recv_until(ws: Any, predicate: Callable[[dict, Any, Any], bool], timeout: float = 20.0) -> list[tuple[dict, Any, Any]]:
    """Read ``(message, payload, raw)`` from a ``websockets.sync`` connection (or a :class:`LiveReader`)
    until ``predicate`` is true.

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
