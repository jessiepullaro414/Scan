"""Fan-out of live messages to the WebSocket clients, with latest-only slots per client.

Producers (the processing, capture, status and job threads) call :meth:`Hub.publish_json` /
:meth:`Hub.publish_binary` from any thread. Publishing never blocks on a client: it stores the
message in each client's slot for that message *key*, replacing whatever an older message of the
same key left there (counted as dropped), and wakes the client's sender coroutine on the asyncio
loop with ``call_soon_threadsafe``. Each client's sender (:meth:`Hub.serve`) takes everything
that is pending and writes it to its socket; when a client reads slowly, its ``send`` awaits
(uvicorn's transport flow control) and only *its* slots fill up, with newer messages replacing
older ones. Memory per client is therefore bounded by one message per key.

Keys: the message ``type`` (``status``, ``pose``, ``ghost``, ``device``, ``session``), ``job:<id>``
for jobs (so updates of different jobs never replace each other), ``preview:left`` /
``preview:depth`` and ``mesh`` for the binary messages. ``log`` messages are events, not state:
they go through a short FIFO per client (the oldest are dropped beyond 50).

Per-client options (the ``subscribe`` message): ``preview`` and ``mesh`` (default on) and
``max_preview_hz`` (default 15, at most 60): previews due earlier stay in their slot and the
sender wakes up again when they are due.

Send order within one wake-up: logs, state (device, session, jobs), status / pose / ghost,
previews, mesh last (it is the biggest).
"""
from __future__ import annotations

import asyncio
import collections
import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ohs.server.protocol import dumps

__all__ = ["Hub", "Client", "BINARY_KEYS"]

log = logging.getLogger(__name__)

BINARY_KEYS = ("preview:left", "preview:depth", "mesh")
_TEXT_ORDER = ("device", "session", "status", "pose", "ghost")
MAX_LOGS = 50
DEFAULT_PREVIEW_HZ = 15.0


@dataclass
class Client:
    """One connected WebSocket client (state guarded by the hub lock)."""

    id: int
    loop: asyncio.AbstractEventLoop
    wake: asyncio.Event
    preview: bool = True
    mesh: bool = True
    max_preview_hz: float = DEFAULT_PREVIEW_HZ
    slots: dict[str, Any] = field(default_factory=dict)  # key -> str (text) or bytes (binary)
    logs: collections.deque = field(default_factory=lambda: collections.deque(maxlen=MAX_LOGS))
    wake_pending: bool = False
    closed: bool = False
    last_preview: dict[str, float] = field(default_factory=dict)
    sent: int = 0
    dropped: int = 0
    connected_at: float = field(default_factory=time.time)


class Hub:
    """Thread-safe publisher; see the module docstring."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[int, Client] = {}
        self._ids = itertools.count(1)
        self.n_published = 0

    # ------------------------------------------------------------------ clients
    def register(self, loop: asyncio.AbstractEventLoop) -> Client:
        c = Client(next(self._ids), loop, asyncio.Event())
        with self._lock:
            self._clients[c.id] = c
        log.info("live client %d connected (%d clients)", c.id, len(self._clients))
        return c

    def unregister(self, client: Client) -> None:
        with self._lock:
            client.closed = True
            self._clients.pop(client.id, None)
            n = len(self._clients)
        log.info("live client %d left (%d clients; %d sent, %d replaced unsent)", client.id, n, client.sent, client.dropped)

    @property
    def n_clients(self) -> int:
        return len(self._clients)

    def wants(self, kind: str) -> bool:
        """True if any client wants ``kind``: "preview", "mesh" or anything else (any client)."""
        with self._lock:
            clients = list(self._clients.values())
        if kind == "preview":
            return any(c.preview for c in clients)
        if kind == "mesh":
            return any(c.mesh for c in clients)
        return bool(clients)

    def preview_hz(self) -> float:
        """Highest preview rate any client asked for (0 if nobody wants previews)."""
        with self._lock:
            rates = [c.max_preview_hz for c in self._clients.values() if c.preview]
        return max(rates, default=0.0)

    def subscribe(self, client: Client, msg: dict) -> None:
        """Apply a ``subscribe`` message (unknown or invalid fields are ignored)."""
        with self._lock:
            if isinstance(msg.get("preview"), bool):
                client.preview = msg["preview"]
            if isinstance(msg.get("mesh"), bool):
                client.mesh = msg["mesh"]
            hz = msg.get("max_preview_hz")
            if isinstance(hz, (int, float)) and not isinstance(hz, bool) and hz > 0:
                client.max_preview_hz = float(min(hz, 60.0))
            if not client.preview:
                client.slots.pop("preview:left", None)
                client.slots.pop("preview:depth", None)
            if not client.mesh:
                client.slots.pop("mesh", None)
        self._wake(client)

    def stats(self) -> list[dict]:
        with self._lock:
            return [{"id": c.id, "sent": c.sent, "dropped": c.dropped, "pending": len(c.slots) + len(c.logs),
                     "preview": c.preview, "mesh": c.mesh, "max_preview_hz": c.max_preview_hz}
                    for c in self._clients.values()]

    # ------------------------------------------------------------------ publishing (any thread)
    def publish_json(self, msg: dict, key: str | None = None) -> None:
        """Queue a text message for every client (latest-only per ``key``; logs are FIFO)."""
        kind = str(msg.get("type", ""))
        if key is None:
            key = f"job:{msg.get('id')}" if kind == "job" else kind
        text = dumps(msg)
        self._put(key, text, log_event=(kind == "log"))

    def publish_binary(self, key: str, data: bytes) -> None:
        """Queue a binary message (``preview:left``, ``preview:depth`` or ``mesh``)."""
        self._put(key, bytes(data), log_event=False)

    def offer(self, client: Client, key: str, payload: Any) -> None:
        """Seed one client's slot unless something newer is already there (used at connect)."""
        with self._lock:
            if client.closed or key in client.slots:
                return
            if key.startswith("preview") and not client.preview or key == "mesh" and not client.mesh:
                return
            client.slots[key] = dumps(payload) if isinstance(payload, dict) else bytes(payload)
        self._wake(client)

    def _put(self, key: str, payload: Any, log_event: bool) -> None:
        wake: list[Client] = []
        with self._lock:
            self.n_published += 1
            for c in self._clients.values():
                if key.startswith("preview:") and not c.preview or key == "mesh" and not c.mesh:
                    continue
                if log_event:
                    if len(c.logs) == c.logs.maxlen:
                        c.dropped += 1
                    c.logs.append(payload)
                else:
                    if key in c.slots:
                        c.dropped += 1
                    c.slots[key] = payload
                if not c.wake_pending:
                    c.wake_pending = True
                    wake.append(c)
        for c in wake:
            self._wake(c, force=True)

    def _wake(self, client: Client, force: bool = False) -> None:
        if not force:
            with self._lock:
                if client.wake_pending:
                    return
                client.wake_pending = True
        try:
            client.loop.call_soon_threadsafe(client.wake.set)
        except RuntimeError:  # the loop is closed (shutdown)
            pass

    # ------------------------------------------------------------------ sending (asyncio)
    def _take(self, client: Client, now: float) -> tuple[list[Any], float | None]:
        """Pending messages in send order, and the delay until a held-back preview is due."""
        out: list[Any] = []
        delay: float | None = None
        with self._lock:
            client.wake_pending = False
            out.extend(client.logs)
            client.logs.clear()
            slots = client.slots
            for key in _TEXT_ORDER[:2]:
                if key in slots:
                    out.append(slots.pop(key))
            for key in [k for k in slots if k.startswith("job:")]:
                out.append(slots.pop(key))
            for key in _TEXT_ORDER[2:]:
                if key in slots:
                    out.append(slots.pop(key))
            for key in [k for k in slots if k not in BINARY_KEYS]:  # anything else that is text
                out.append(slots.pop(key))
            period = 1.0 / max(client.max_preview_hz, 1e-3)
            for key in BINARY_KEYS[:2]:
                if key not in slots:
                    continue
                due = client.last_preview.get(key, 0.0) + period
                if now >= due - 1e-3:
                    out.append(slots.pop(key))
                    client.last_preview[key] = now
                else:
                    delay = due - now if delay is None else min(delay, due - now)
            if "mesh" in slots:
                out.append(slots.pop("mesh"))
            client.sent += len(out)
        return out, delay

    async def serve(self, client: Client, send_text: Callable[[str], Awaitable[None]],
                    send_bytes: Callable[[bytes], Awaitable[None]]) -> None:
        """Sender loop of one client; returns when the client is unregistered, raises on send errors."""
        loop = asyncio.get_running_loop()
        timer: asyncio.TimerHandle | None = None
        try:
            while not client.closed:
                await client.wake.wait()
                client.wake.clear()
                batch, delay = self._take(client, time.monotonic())
                for item in batch:
                    if isinstance(item, str):
                        await send_text(item)
                    else:
                        await send_bytes(item)
                if delay is not None:
                    if timer is not None:
                        timer.cancel()
                    timer = loop.call_later(delay, client.wake.set)
        finally:
            if timer is not None:
                timer.cancel()
