"""Simulated recordings and renders for the tests, made once and kept on disk between runs.

Rendering is most of the tests' fixture time (about 1 s per full-resolution stereo frame). The
three functions here put a cache in front of it:

* :func:`scan_recording`: :func:`ohs.sim.datasets.make_scan_recording`;
* :func:`cached_recording`: any function that writes a recording folder;
* :func:`cached_result`: any simulator function that returns data (board views, a calibration).

**The key** is a hash of the call's arguments and of the *renderer version*: the source of
everything a render is made from (``ohs/sim``, ``ohs/io``, ``ohs/types.py``, ``ohs/geometry.py``)
and the versions of numpy and OpenCV. Any change to the simulator makes a new key, so a render of
an older simulator is never used. A function that is not part of the simulator (a test's own
recording maker) adds its own source to the key. Arguments that are dataclasses or arrays (a
trajectory, render parameters, a board) are part of the key by their constructor fields; a call
the key cannot describe (a ``Scene`` object) is rendered every time.

**A hit** copies the cached folder to where the test asked for it (or unpickles the result), so a
test may change what it gets. A folder is byte for byte what a fresh render writes, except
``created`` in ``meta.json`` (the time of the first render).

**Where:** ``OHS_TEST_RECORDINGS`` (a folder), default ``<temp dir>/ohs-test-recordings``:
outside the repository. ``OHS_TEST_RECORDINGS=off`` (or ``0``) renders every time. The cache
only grows: delete the folder to start over. Entries of other simulator versions are removed
when a new one is written, once they are a week old.
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import pickle
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ohs.sim.datasets import make_scan_recording

__all__ = ["scan_recording", "cached_recording", "cached_result", "cache_dir", "renderer_version", "cache_stats"]

_SOFTWARE = Path(__file__).resolve().parents[1]
_SOURCES = ("ohs/sim", "ohs/io", "ohs/types.py", "ohs/geometry.py")
_OFF = ("0", "off", "no", "false")
_OLD_S = 7 * 24 * 3600.0
cache_stats = {"hits": 0, "renders": 0, "uncached": 0}
_version: str | None = None
_SCAN_ARGS = inspect.signature(make_scan_recording)


def cache_dir() -> Path | None:
    """The cache folder, or None when the cache is turned off."""
    v = os.environ.get("OHS_TEST_RECORDINGS", "").strip()
    if v.lower() in _OFF:
        return None
    return Path(v) if v else Path(tempfile.gettempdir()) / "ohs-test-recordings"


def renderer_version() -> str:
    """Hash of the simulator's source and of the libraries that shape its output."""
    global _version
    if _version is None:
        import cv2
        import numpy as np

        h = hashlib.sha256(f"numpy {np.__version__} opencv {cv2.__version__}".encode())
        for rel in _SOURCES:
            p = _SOFTWARE / rel
            for f in ([p] if p.is_file() else sorted(p.rglob("*.py"))):
                h.update(f.relative_to(_SOFTWARE).as_posix().encode())
                h.update(f.read_bytes().replace(b"\r\n", b"\n"))  # the same on any checkout
        _version = h.hexdigest()[:12]
    return _version


def _plain(x: Any) -> Any:
    """``x`` as JSON-able data, or raise TypeError when the key cannot describe it."""
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, (tuple, list)):
        return [_plain(v) for v in x]
    if isinstance(x, dict):
        return {"__dict__": [[_plain(k), _plain(v)] for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))]}
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        # what it was built from: the fields its constructor takes (the others are derived from them)
        return {"__type__": type(x).__name__, **{f.name: _plain(getattr(x, f.name)) for f in dataclasses.fields(x) if f.init}}
    if hasattr(x, "tolist") and hasattr(x, "dtype"):  # a numpy array or scalar
        return {"__array__": str(x.dtype), "values": x.tolist()}
    raise TypeError(type(x).__name__)


def _entry(maker: Callable | None, kwargs: dict) -> tuple[Path, str] | None:
    """``(the cache entry's path, its arguments as text)``, or None when this call is not cached."""
    root = cache_dir()
    if root is None:
        return None
    try:
        blob = json.dumps({k: _plain(v) for k, v in kwargs.items()}, sort_keys=True)
    except TypeError:
        return None
    name = hashlib.sha256(blob.encode()).hexdigest()[:20]
    if maker is not None:  # not make_scan_recording: say which function, and its source when it is not the simulator's
        who = f"{maker.__module__}.{maker.__qualname__}"
        src = "" if maker.__module__.startswith("ohs.sim") else inspect.getsource(maker).replace("\r\n", "\n")
        name = f"{maker.__name__}-" + hashlib.sha256(f"{who}\n{src}\n{blob}".encode()).hexdigest()[:20]
    return root / f"v{renderer_version()}" / name, blob


def _make(entry: Path, blob: str, fill: Callable[[Path], None]) -> None:
    """Fill a new cache entry in a temporary folder, then move it into place."""
    tmp = entry.with_name(f"{entry.name}.tmp-{uuid.uuid4().hex[:8]}")
    tmp.mkdir(parents=True, exist_ok=True)
    fill(tmp)
    (tmp / "args.json").write_text(blob, encoding="utf-8")
    (tmp / "ok").write_text("", encoding="utf-8")
    try:
        tmp.replace(entry)
    except OSError:  # another run wrote it meanwhile
        shutil.rmtree(tmp, ignore_errors=True)
    cache_stats["renders"] += 1
    _drop_old(entry.parent.parent, entry.parent.name)


def _recording(out: Path, maker: Callable | None, write: Callable[[Path], Any], kwargs: dict, overwrite: bool) -> Path:
    found = _entry(maker, kwargs)
    if found is None:
        cache_stats["uncached"] += 1
        write(out)
        return out
    entry, blob = found
    if (entry / "ok").is_file():
        cache_stats["hits"] += 1
    else:
        _make(entry, blob, lambda tmp: write(tmp / "rec"))
    if out.exists():
        if not overwrite:
            raise FileExistsError(f"{out} exists (overwrite=False)")
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(entry / "rec", out)
    return out


def scan_recording(out_dir: str | Path, *args: Any, **kwargs: Any) -> Path:
    """``make_scan_recording(out_dir, ...)`` with the same arguments, from the cache when it has
    this recording."""
    kwargs = dict(_SCAN_ARGS.bind(out_dir, *args, **kwargs).arguments)  # by name, however they were passed
    out_dir = kwargs.pop("out_dir")
    overwrite = bool(kwargs.pop("overwrite", False))
    if cache_dir() is None or _entry(None, kwargs) is None:
        cache_stats["uncached"] += 1
        return make_scan_recording(out_dir, overwrite=overwrite, **kwargs)
    return _recording(Path(out_dir), None, lambda p: make_scan_recording(p, **kwargs), kwargs, overwrite)


def cached_recording(out_dir: str | Path, maker: Callable[..., Any], **kwargs: Any) -> Path:
    """``maker(out_dir, **kwargs)`` for a function that writes a recording folder there, from the
    cache when it has this recording. Returns the folder (what ``maker`` returns is not kept)."""
    return _recording(Path(out_dir), maker, lambda p: maker(p, **kwargs), kwargs, False)


def cached_result(fn: Callable[..., Any], **kwargs: Any) -> Any:
    """``fn(**kwargs)`` for a simulator function that returns data (images, a calibration), from
    the cache when it has this call. The result is kept as a pickle, so every call gets its own
    copy."""
    found = _entry(fn, kwargs)
    if found is None:
        cache_stats["uncached"] += 1
        return fn(**kwargs)
    entry, blob = found
    if (entry / "ok").is_file():
        cache_stats["hits"] += 1
    else:
        def fill(tmp: Path) -> None:
            with open(tmp / "result.pkl", "wb") as fh:
                pickle.dump(fn(**kwargs), fh, protocol=pickle.HIGHEST_PROTOCOL)

        _make(entry, blob, fill)
    with open(entry / "result.pkl", "rb") as fh:
        return pickle.load(fh)


def _drop_old(root: Path, keep: str) -> None:
    """Remove the renders of other simulator versions that have not been written for a week."""
    now = time.time()
    for d in root.glob("v*"):
        if d.is_dir() and d.name != keep and now - d.stat().st_mtime > _OLD_S:
            shutil.rmtree(d, ignore_errors=True)
