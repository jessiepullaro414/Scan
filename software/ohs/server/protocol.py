"""Message builders and binary framing for the ``/api/ws/live`` WebSocket (APP_API.md).

Text messages are JSON objects with a ``type``. Binary messages are framed as::

    uint32 little-endian  header length L (bytes)
    L bytes               UTF-8 JSON header, padded with spaces so that 4 + L is a multiple of 4
    payload               sections back to back, each padded with zero bytes to a multiple of 4

so every payload section starts on a 4-byte boundary *of the whole message*, and the UI can map
``Float32Array`` / ``Uint32Array`` views straight onto the received ``ArrayBuffer``. A mesh
payload is ``positions`` Float32[V*3], ``normals`` Float32[V*3], ``colors`` Uint8[V*4] (RGBA) and
``indices`` Uint32[T*3]; all of these sizes are multiples of 4 already, so no padding occurs
between them. A preview payload is one JPEG.

Also here: :class:`ApiError` (the ``{detail, hint}`` error of every REST call) and
:func:`jsonable` (numpy -> JSON).
"""
from __future__ import annotations

import json
import logging
import math
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

log = logging.getLogger(__name__)

__all__ = [
    "PROTOCOL_VERSION",
    "ApiError",
    "sentence",
    "plain_reason",
    "SETTINGS_FILE_HINT",
    "jsonable",
    "dumps",
    "now_iso",
    "pack_binary",
    "unpack_binary",
    "mesh_message",
    "split_mesh_payload",
    "preview_message",
    "pose_message",
    "ghost_message",
    "log_message",
    "QUALITIES",
    "QUALITY_NAMES",
    "normalize_quality",
    "quality_or_none",
]

PROTOCOL_VERSION = "0.3.0"  # M3 phase 2: the processing studio (APP_API.md)

#: the levels of the final mesh: Standard (the default, 30 processed frames per second of scanning),
#: Best (every frame) and the quick preview
QUALITIES = ("standard", "best", "preview")
QUALITY_NAMES = {"standard": "standard quality", "best": "best quality", "preview": "preview quality"}
_OLD_QUALITIES = {"full": "standard"}  # stored state, saved recipes and old clients


def normalize_quality(quality: Any) -> str:
    """``"preview"``, ``"standard"`` or ``"best"``; the old name ``"full"`` is ``"standard"``.
    ``ValueError`` for anything else."""
    q = str(quality).strip().lower() if quality is not None else ""
    q = _OLD_QUALITIES.get(q, q)
    if q not in QUALITIES:
        raise ValueError(f"unknown quality {quality!r}: use standard, best or preview")
    return q


def quality_or_none(quality: Any) -> str | None:
    """:func:`normalize_quality`, or ``None`` for a missing or unknown value (reading stored state)."""
    try:
        return normalize_quality(quality) if quality else None
    except ValueError:
        return None


def sentence(text: Any) -> str:
    """``text`` as a sentence: a capital first letter and a full stop at the end (unless it ends
    with ``.``, ``!``, ``?`` or ``:``, a closing quote after one of them, or is empty). Every error
    text the user reads passes through here (:class:`ApiError`, a failed job's ``error``), so the
    detail and the hint are each a full sentence."""
    s = " ".join(str(text or "").split())
    if not s:
        return ""
    if s[0].isalpha() and s[0].islower():
        s = s[0].upper() + s[1:]
    end = s.rstrip("\"'”’)")
    if not end.endswith((".", "!", "?", ":", "…")):
        s += "."
    return s


#: what is never shown for a caught exception: its message reads like a program's, not a person's
_NOT_PLAIN = re.compile(
    r"Error\b|Exception\b|Traceback|\bErrno\b|errno|__|\bNone\b|\bdtype\b|ndarray|numpy|open3d|cv2|"
    r"[\\/<>{}\[\]`|=]|\w\(|(?<!\w)'[^'\s]*'(?!\w)|\bline \d+|\bshape \(|0x[0-9a-f]|[a-z0-9]_[a-z0-9]")


def plain_reason(e: BaseException | str | None, fallback: str) -> str:
    """The message of ``e`` when it reads as a plain sentence written for the user (for example a
    ``ValueError`` from a check of a typed value: "a name must be 1 to 80 characters"), else
    ``fallback``. The guard test (``tests/test_server_error_words.py``) refuses ``str(e)`` and
    ``{e}`` in what the user reads; this is the one way a caught exception's words get there,
    filtered. What is refused here goes to the log."""
    s = " ".join(str(e or "").split())
    if s and len(s) <= 240 and not _NOT_PLAIN.search(s):
        return s
    if s:
        log.info("not shown to the user (%s): %s", type(e).__name__ if isinstance(e, BaseException) else "text", s)
    return fallback


#: what to do when the settings file cannot be written (it is the one file the Settings page names)
SETTINGS_FILE_HINT = ("Check that no other program has the settings file open and that it is not read-only; if you "
                      "edited it by hand, look for a typing mistake in it.")

class ApiError(Exception):
    """An error with an HTTP status, a message saying what happened and a hint saying what to do.
    Both are made full sentences (:func:`sentence`)."""

    def __init__(self, status: int, detail: str, hint: str | None = None, *, extra: dict | None = None) -> None:
        detail = sentence(detail)
        super().__init__(detail)
        self.status = int(status)
        self.detail = detail
        self.hint = sentence(hint) or None
        self.extra = dict(extra or {})  # fields of the answer beside detail and hint (a suggested name, UX_PLAN R21)

    def body(self) -> dict:
        out: dict[str, Any] = {"detail": self.detail}
        if self.hint:
            out["hint"] = self.hint
        out.update({k: v for k, v in self.extra.items() if k not in ("detail", "hint")})
        return out

    def __str__(self) -> str:
        """The detail and the hint as two sentences (a failed job's ``error``)."""
        return self.detail if not self.hint else f"{self.detail} {self.hint}"


def jsonable(x: Any) -> Any:
    """Convert numpy scalars/arrays, paths, tuples and non-finite floats into plain JSON values."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, Path):
        return str(x)
    return x


def dumps(msg: dict) -> str:
    """Compact JSON text of a message (numpy-safe)."""
    return json.dumps(jsonable(msg), separators=(",", ":"), ensure_ascii=False)


def now_iso() -> str:
    """Current time as ISO 8601 with the UTC offset, to the second."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- binary framing
def _as_bytes(section: Any) -> bytes | memoryview:
    if isinstance(section, np.ndarray):
        a = np.ascontiguousarray(section)
        if a.dtype.byteorder == ">" or (a.dtype.byteorder == "=" and struct.pack("=I", 1) != struct.pack("<I", 1)):
            a = a.astype(a.dtype.newbyteorder("<"))
        return memoryview(a.reshape(-1).view(np.uint8))  # works for empty arrays too
    if isinstance(section, (bytes, bytearray, memoryview)):
        return section
    raise TypeError(f"cannot pack {type(section).__name__}")


def pack_binary(header: dict, sections: Sequence[Any] = ()) -> bytes:
    """Frame a binary message: ``uint32 LE header length``, padded JSON header, 4-byte aligned sections."""
    hdr = dumps(header).encode("utf-8")
    hdr += b" " * ((-(4 + len(hdr))) % 4)
    parts: list[bytes | memoryview] = [struct.pack("<I", len(hdr)), hdr]
    for s in sections:
        b = _as_bytes(s)
        parts.append(b)
        pad = (-len(b)) % 4
        if pad:
            parts.append(b"\0" * pad)
    return b"".join(parts)


def unpack_binary(data: bytes | bytearray | memoryview) -> tuple[dict, memoryview, int]:
    """``(header, payload, payload_offset)`` of a framed message (``payload_offset`` from the start)."""
    mv = memoryview(data)
    if len(mv) < 4:
        raise ValueError("binary message shorter than its length prefix")
    (n,) = struct.unpack_from("<I", mv, 0)
    if 4 + n > len(mv):
        raise ValueError(f"header length {n} exceeds the message ({len(mv)} bytes)")
    header = json.loads(bytes(mv[4 : 4 + n]).decode("utf-8"))
    return header, mv[4 + n :], 4 + n


def mesh_message(seq: int, V: np.ndarray, N: np.ndarray, rgba: np.ndarray, F: np.ndarray, **extra: Any) -> bytes:
    """Binary mesh snapshot (world frame, mm); ``extra`` keys are added to the header."""
    V = np.ascontiguousarray(V, dtype="<f4").reshape(-1, 3)
    N = np.ascontiguousarray(N, dtype="<f4").reshape(-1, 3)
    C = np.ascontiguousarray(rgba, dtype=np.uint8).reshape(-1, 4)
    F = np.ascontiguousarray(F, dtype="<u4").reshape(-1, 3)
    if not (len(V) == len(N) == len(C)):
        raise ValueError("positions, normals and colors must have one row per vertex")
    if len(F) and int(F.max()) >= len(V):
        raise ValueError("triangle index out of range")
    header = {"type": "mesh", "seq": int(seq), "vertices": int(len(V)), "triangles": int(len(F)),
              "units": "mm", "frame": "world", **extra}
    return pack_binary(header, [V, N, C, F])


def split_mesh_payload(header: dict, payload: memoryview) -> dict[str, np.ndarray]:
    """Decode a mesh payload into ``positions``, ``normals``, ``colors``, ``indices`` (for tests/tools)."""
    V, T = int(header["vertices"]), int(header["triangles"])
    sizes = [("positions", "<f4", V * 3), ("normals", "<f4", V * 3), ("colors", "u1", V * 4), ("indices", "<u4", T * 3)]
    out: dict[str, np.ndarray] = {}
    off = 0
    for name, dt, count in sizes:
        nbytes = count * np.dtype(dt).itemsize
        if off + nbytes > len(payload):
            raise ValueError(f"mesh payload too short for section {name}")
        out[name] = np.frombuffer(payload, dtype=dt, count=count, offset=off)
        off += nbytes + ((-nbytes) % 4)
    if off != len(payload):
        raise ValueError(f"mesh payload has {len(payload) - off} unexpected trailing bytes")
    out["positions"] = out["positions"].reshape(V, 3)
    out["normals"] = out["normals"].reshape(V, 3)
    out["colors"] = out["colors"].reshape(V, 4)
    out["indices"] = out["indices"].reshape(T, 3)
    return out


def preview_message(camera: str, jpeg: bytes, w: int, h: int, seq: int, **extra: Any) -> bytes:
    """Binary preview image (``camera`` "left" or "depth")."""
    header = {"type": "preview", "camera": camera, "format": "jpeg", "w": int(w), "h": int(h), "seq": int(seq), **extra}
    return pack_binary(header, [jpeg])


# --------------------------------------------------------------------------- text messages
def _pose16(T: np.ndarray) -> list[float]:
    return [float(v) for v in np.asarray(T, dtype=np.float64).reshape(16)]


def pose_message(T_wc: np.ndarray, ok: bool, **extra: Any) -> dict:
    return {"type": "pose", "T_wc": _pose16(T_wc), "ok": bool(ok), **extra}


def ghost_message(T_wc: np.ndarray) -> dict:
    return {"type": "ghost", "T_wc": _pose16(T_wc)}


def log_message(level: str, message: str) -> dict:
    lv = {"warning": "warn", "critical": "error"}.get(level, level)
    if lv not in ("info", "warn", "error"):
        lv = "info"
    return {"type": "log", "level": lv, "message": str(message), "t": now_iso()}
