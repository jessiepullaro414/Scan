"""OpenHandScan Studio backend: the FastAPI app behind the desktop UI (``ohs app``).

The contract with the UI is ``software/docs/APP_API.md``. Modules:

* :mod:`ohs.server.app`: :func:`create_app` (REST routes, the ``/api/ws/live`` WebSocket, the
  built UI at ``/``, error handling, CORS);
* :mod:`ohs.server.studio`: :class:`~ohs.server.studio.Studio`, the backend core that wires the
  parts below together and enforces the rules (one scan at a time, emitters off, ...);
* :mod:`ohs.server.devices`: :class:`~ohs.server.devices.DeviceManager` (camera + Pico, replay,
  simulator sources; capture, Pico and idle-preview threads);
* :mod:`ohs.server.session`: :class:`~ohs.server.session.ScanSession` (recording, the live
  ``FramePipeline`` thread, previews, mesh snapshots, pause / rewind / stop);
* :mod:`ohs.server.hub`: :class:`~ohs.server.hub.Hub`, per-client latest-only message slots;
* :mod:`ohs.server.protocol`: message builders and the binary framing;
* :mod:`ohs.server.projects`: :class:`~ohs.server.projects.ProjectStore` (folders + project.json);
* :mod:`ohs.server.jobs`: :class:`~ohs.server.jobs.JobRunner` (thread pool, progress, cancel);
* :mod:`ohs.server.meshing`: mesh decimation, coverage-based quality colours;
* :mod:`ohs.server.imaging`: preview images (laser overlay, depth colour map, JPEG);
* :mod:`ohs.server.demo`: the simulator demo recording;
* :mod:`ohs.server.launcher`: ``ohs app`` (uvicorn thread + pywebview window);
* M2, setup and calibration: :mod:`ohs.server.setup` (setup state, print check, devices probe,
  self-test and printables jobs), :mod:`ohs.server.selftest`, :mod:`ohs.server.printables`,
  :mod:`ohs.server.calibration` (calibration sessions, solve, save), :mod:`ohs.server.calib_guide`
  (live detection, capture rules, coverage, hints) and :mod:`ohs.server.calib_results`
  (plain-language results, the simulator's truth comparison);
* M3 phase 2, the processing studio: :mod:`ohs.server.processing` (stages, mesh edits with undo /
  redo, recipes, hole and trajectory overlays), on top of the mesh operations in :mod:`ohs.mesh`.

FastAPI and uvicorn are optional dependencies (``pip install -e .[app]``); importing this package
does not import them.
"""
from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):  # noqa: ANN002, ANN003 - thin lazy wrapper, see ohs.server.app.create_app
    """Build the FastAPI app (see :func:`ohs.server.app.create_app`)."""
    from ohs.server.app import create_app as _create_app

    return _create_app(*args, **kwargs)
