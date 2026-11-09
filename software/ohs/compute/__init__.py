"""GPU compute for the engine: one OpenCL device, found automatically (DESKTOP_APP_PLAN.md, M3).

The engine runs everywhere on the CPU. When the machine has a GPU (NVIDIA, AMD or Intel, discrete
or integrated), the heavy dense steps (stereo matching, the tracking model) run on it through
OpenCL (PyOpenCL), so one kernel source serves every vendor. Every GPU step has a CPU twin, and
the tests check that the two agree.

Choosing the device (:func:`get_device`), once per process:

* the preference is, in order: the ``OHS_COMPUTE`` environment variable, then
  :func:`configure` (the CLI and the app pass the config key ``compute.device``), then ``"auto"``;
* ``"cpu"`` turns the GPU off; ``"auto"`` takes the best GPU: a discrete GPU (more memory first),
  then an integrated one; any other value picks the first GPU whose name contains it (case
  ignored), e.g. ``"RTX"`` or ``"gfx1035"``;
* OpenCL *CPU* devices are never used (the CPU path is numpy / OpenCV), nor Microsoft's
  "Basic Render Driver" (software). Microsoft's OpenCL-on-D3D12 compatibility layer
  ("OpenCLOn12") lists the same GPUs a second time; it is used only when no native platform has
  them, so on a laptop with an RTX the "NVIDIA CUDA" platform's device is chosen;
* a device must build and run a probe kernel before it is used.

Without PyOpenCL, without a GPU, or when the probe fails, :func:`get_device` returns ``None`` and
:func:`status` says why in plain words. Nothing raises: callers fall back to the CPU.

:class:`ComputeDevice` holds the context, builds programs once per source (cached), and gives
each user its own command queue (:meth:`ComputeDevice.new_queue`). OpenCL kernel objects keep
their arguments, so a kernel object must not be shared between threads: create kernels per user
(``cl.Kernel(program, name)``) and keep them, rather than ``program.name`` attribute lookups, which
create a new kernel every time.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ComputeDevice", "configure", "get_device", "status", "describe", "reset", "ENV_VAR"]

log = logging.getLogger(__name__)

ENV_VAR = "OHS_COMPUTE"
_PROBE_SRC = "__kernel void ohs_probe(__global int *a) { a[get_global_id(0)] += 1; }"

_lock = threading.Lock()
_preference: str | None = None
_resolved = False
_device: "ComputeDevice | None" = None
_status = "not checked yet"


@dataclass
class ComputeDevice:
    """One OpenCL GPU with its context (see the module docstring)."""

    cl: Any  # the pyopencl module
    device: Any
    ctx: Any
    name: str
    vendor: str
    platform: str
    kind: str  # "discrete" or "integrated"
    mem_mb: int
    max_alloc_mb: int
    version: str
    _programs: dict = field(default_factory=dict, repr=False)
    _plock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def new_queue(self, profiling: bool = False) -> Any:
        """A new in-order command queue on this device (one per user / thread)."""
        props = self.cl.command_queue_properties.PROFILING_ENABLE if profiling else 0
        return self.cl.CommandQueue(self.ctx, properties=props)

    def program(self, source: str, options: tuple[str, ...] | list[str] = ()) -> Any:
        """``source`` built for this device, once per (source, options); raises on a build error."""
        key = hashlib.sha1((source + "\0" + " ".join(options)).encode("utf-8")).hexdigest()
        with self._plock:
            prg = self._programs.get(key)
            if prg is None:
                prg = self.cl.Program(self.ctx, source).build(options=list(options))
                self._programs[key] = prg
            return prg

    def describe(self) -> dict:
        return {"name": self.name, "vendor": self.vendor, "platform": self.platform, "kind": self.kind,
                "memory_mb": self.mem_mb, "max_alloc_mb": self.max_alloc_mb, "opencl": self.version}


def configure(preference: str | None) -> None:
    """Set the device preference (config ``compute.device``); takes effect at the next :func:`get_device`."""
    global _preference, _resolved, _device, _status
    pref = (str(preference).strip() or "auto") if preference is not None else None
    with _lock:
        if pref == _preference:
            return
        _preference = pref
        _resolved, _device, _status = False, None, "not checked yet"


def reset() -> None:
    """Forget the chosen device (tests)."""
    global _resolved, _device, _status
    with _lock:
        _resolved, _device, _status = False, None, "not checked yet"


def _wanted() -> str:
    env = os.environ.get(ENV_VAR)
    if env is not None and env.strip():
        return env.strip()
    return _preference or "auto"


def _candidates(cl: Any) -> list[tuple[tuple, Any, Any]]:
    out = []
    for plat in cl.get_platforms():
        try:
            devices = plat.get_devices(device_type=cl.device_type.GPU)
        except Exception:  # a platform without GPUs raises DEVICE_NOT_FOUND
            continue
        compat = "opencl" in plat.name.lower() and "12" in plat.name  # "OpenCLOn12" (D3D12 mapping layer)
        for d in devices:
            if "basic render" in d.name.lower():  # Microsoft's software renderer: a CPU pretending to be a GPU
                continue
            unified = bool(getattr(d, "host_unified_memory", False))
            score = (1 if compat else 0, 1 if unified else 0, -int(d.global_mem_size))
            out.append((score, plat, d))
    out.sort(key=lambda t: t[0])
    return out


def _probe(cl: Any, ctx: Any) -> None:
    import numpy as np

    q = cl.CommandQueue(ctx)
    a = np.zeros(64, np.int32)
    buf = cl.Buffer(ctx, cl.mem_flags.READ_WRITE | cl.mem_flags.COPY_HOST_PTR, hostbuf=a)
    prg = cl.Program(ctx, _PROBE_SRC).build()
    cl.Kernel(prg, "ohs_probe")(q, (64,), None, buf)
    cl.enqueue_copy(q, a, buf)
    q.finish()
    if not (a == 1).all():
        raise RuntimeError("the probe kernel returned wrong values")


def _resolve() -> tuple["ComputeDevice | None", str]:
    wanted = _wanted()
    if wanted.lower() == "cpu":
        return None, "the GPU is turned off (compute.device = \"cpu\"): everything runs on the CPU"
    try:
        import pyopencl as cl
    except ImportError:
        return None, "PyOpenCL is not installed (pip install pyopencl): everything runs on the CPU"
    try:
        cands = _candidates(cl)
    except Exception as e:  # no OpenCL driver at all (no ICD): pyopencl raises on get_platforms
        return None, f"no OpenCL driver was found ({e}): everything runs on the CPU"
    if wanted.lower() != "auto":
        cands = [c for c in cands if wanted.lower() in c[2].name.lower()]
        if not cands:
            return None, f"no GPU matches compute.device = {wanted!r}: everything runs on the CPU"
    if not cands:
        return None, "no OpenCL GPU was found: everything runs on the CPU"
    problems = []
    for _, plat, d in cands:
        try:
            ctx = cl.Context([d])
            _probe(cl, ctx)
        except Exception as e:
            problems.append(f"{d.name.strip()}: {e}")
            continue
        unified = bool(getattr(d, "host_unified_memory", False))
        dev = ComputeDevice(cl=cl, device=d, ctx=ctx, name=d.name.strip(), vendor=d.vendor.strip(),
                            platform=plat.name.strip(), kind="integrated" if unified else "discrete",
                            mem_mb=int(d.global_mem_size >> 20), max_alloc_mb=int(d.max_mem_alloc_size >> 20),
                            version=d.version.strip())
        return dev, f"GPU: {dev.name} ({dev.kind}, {dev.mem_mb} MB, {dev.platform})"
    return None, "no GPU passed the OpenCL check (" + "; ".join(problems) + "): everything runs on the CPU"


def get_device() -> "ComputeDevice | None":
    """The GPU to use, or ``None`` for the CPU (see the module docstring). Thread-safe, cached."""
    global _resolved, _device, _status
    with _lock:
        if not _resolved:
            _device, _status = _resolve()
            _resolved = True
            log.info("compute: %s", _status)
        return _device


def status() -> str:
    """Plain-language description of the compute choice ("GPU: …" or why it is the CPU)."""
    get_device()
    return _status


def describe() -> dict:
    """``{"backend": "gpu" | "cpu", "status": str, "device": {...} | None, "preference": str}``."""
    dev = get_device()
    return {"backend": "gpu" if dev is not None else "cpu", "status": _status,
            "device": dev.describe() if dev is not None else None, "preference": _wanted()}
