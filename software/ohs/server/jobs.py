"""Background jobs (processing a scan, generating the simulator demo): a small thread pool.

A job function receives a :class:`JobContext` and returns its result (a JSON-able dict).
It reports progress with ``ctx.progress(fraction, message)`` and calls ``ctx.check()`` (or reads
``ctx.cancelled``) at convenient points: cancellation is **cooperative**. ``ctx.check()`` raises
:class:`JobCancelled` once :meth:`JobRunner.cancel` was called, and the job then ends in state
``"cancelled"``. Any other exception ends it in ``"failed"`` with ``error`` = the message (an
:class:`~ohs.server.protocol.ApiError` contributes its hint).

States: ``queued -> running -> done | failed | cancelled`` (a queued job that is cancelled never
runs). ``on_done(job)`` runs exactly once per job, whatever state it ends in: in the worker, or, for a
job cancelled while queued, in the thread that cancelled it. ``progress`` is a fraction from 0 to 1. Every change is passed to ``on_update(job_dict)``
(the WebSocket ``job`` message); progress-only updates are throttled to 5 per second per job.
Finished jobs are kept in memory (the newest 200).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from ohs.server.protocol import ApiError, jsonable, now_iso

__all__ = ["Job", "JobContext", "JobCancelled", "JobRunner", "ACTIVE_STATES"]

log = logging.getLogger(__name__)

ACTIVE_STATES = ("queued", "running")
KEEP_FINISHED = 200
UPDATE_PERIOD_S = 0.2


class JobCancelled(Exception):
    """Raised inside a job function by :meth:`JobContext.check` after a cancel request."""


@dataclass
class Job:
    id: str
    kind: str
    state: str = "queued"
    progress: float = 0.0
    message: str = "Queued"
    result: dict | None = None
    error: str | None = None
    created: str = field(default_factory=now_iso)
    meta: dict = field(default_factory=dict)  # extra fields shown to the UI (e.g. project_id, scan_id)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    future: Future | None = field(default=None, repr=False)
    finished: threading.Event = field(default_factory=threading.Event, repr=False)
    t_last_update: float = 0.0
    on_done: Callable[["Job"], None] | None = field(default=None, repr=False)  # set by submit

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def to_dict(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "state": self.state, "progress": round(float(self.progress), 4),
             "message": self.message, "result": jsonable(self.result), "error": self.error, "created": self.created}
        d.update(jsonable(self.meta))
        return d


class JobContext:
    """What a running job sees: progress reporting and the cancel flag."""

    def __init__(self, runner: "JobRunner", job: Job) -> None:
        self._runner = runner
        self.job = job

    @property
    def cancelled(self) -> bool:
        return self.job.cancel_event.is_set()

    def check(self) -> None:
        if self.job.cancel_event.is_set():
            raise JobCancelled()

    def progress(self, fraction: float | None = None, message: str | None = None, force: bool = False) -> None:
        self._runner._progress(self.job, fraction, message, force)


class JobRunner:
    """Thread pool running :class:`Job` functions; see the module docstring."""

    def __init__(self, on_update: Callable[[dict], None] | None = None, max_workers: int = 2) -> None:
        self._on_update = on_update
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="ohs-job")
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._closed = False

    # ------------------------------------------------------------------ queries
    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ApiError(404, f"no job {job_id!r}", "list the jobs with GET /api/jobs")
        return job

    def list(self) -> list[Job]:
        """All known jobs, newest first."""
        with self._lock:
            jobs = list(self._jobs.values())
        return jobs[::-1]

    def active(self, kind: str | None = None) -> list[Job]:
        return [j for j in self.list() if j.active and (kind is None or j.kind == kind)]

    # ------------------------------------------------------------------ control
    def submit(self, kind: str, fn: Callable[[JobContext], dict | None], *, message: str = "Queued",
               meta: dict | None = None, on_done: Callable[[Job], None] | None = None) -> Job:
        """Queue ``fn(ctx)``. ``on_done(job)`` runs in the worker after the job finished (any state)."""
        if self._closed:
            raise ApiError(503, "the app is shutting down", "start OpenHandScan Studio again")
        job = Job(id=f"j-{uuid.uuid4().hex[:10]}", kind=kind, message=message, meta=dict(meta or {}),
                  on_done=on_done)
        with self._lock:
            self._jobs[job.id] = job
            self._trim()
        job.future = self._pool.submit(self._run, job, fn, on_done)
        self._emit(job)
        log.info("job %s (%s) queued", job.id, kind)
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if not job.active:
            return job
        job.cancel_event.set()
        was_queued = False
        with self._lock:
            if job.state == "queued":
                job.state, job.message = "cancelled", "Cancelled before it started"
                was_queued = True
            else:
                job.message = "Cancelling..."
        if was_queued:  # _run will skip it, so settle it here (once), outside the lock
            self._call_done(job, job.on_done)
            job.finished.set()
        self._emit(job)
        log.info("job %s: cancel requested", job.id)
        return job

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        job = self.get(job_id)
        job.finished.wait(timeout)
        return job

    def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel everything and wait up to ``timeout`` s for running jobs to stop."""
        self._closed = True
        for job in self.list():
            if job.active:
                self.cancel(job.id)
        deadline = time.monotonic() + timeout
        for job in self.list():
            job.finished.wait(max(0.0, deadline - time.monotonic()))
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ internals
    def _trim(self) -> None:
        done = [j for j in self._jobs.values() if not j.active]
        for j in done[: max(0, len(done) - KEEP_FINISHED)]:
            self._jobs.pop(j.id, None)

    def _emit(self, job: Job) -> None:
        job.t_last_update = time.monotonic()
        if self._on_update is not None:
            try:
                self._on_update(job.to_dict())
            except Exception:
                log.exception("job update callback failed")

    def _progress(self, job: Job, fraction: float | None, message: str | None, force: bool) -> None:
        if fraction is not None:
            job.progress = float(min(1.0, max(0.0, fraction)))
        if message is not None and not job.cancel_event.is_set():
            job.message = str(message)
        if force or time.monotonic() - job.t_last_update >= UPDATE_PERIOD_S:
            self._emit(job)

    def _run(self, job: Job, fn: Callable[[JobContext], dict | None], on_done: Callable[[Job], None] | None) -> None:
        with self._lock:
            if job.state != "queued":  # cancelled while queued
                return
            job.state, job.message = "running", "Starting..."
        self._emit(job)
        t0 = time.perf_counter()
        ctx = JobContext(self, job)
        try:
            ctx.check()
            result = fn(ctx)
            ctx.check()
            job.result = jsonable(result) if result is not None else {}
            job.state, job.progress = "done", 1.0
            job.message = (job.result or {}).get("message", "Done") if isinstance(job.result, dict) else "Done"
            log.info("job %s (%s) done in %.1f s", job.id, job.kind, time.perf_counter() - t0)
        except (JobCancelled, InterruptedError):
            job.state, job.message = "cancelled", "Cancelled"
            log.info("job %s (%s) cancelled", job.id, job.kind)
        except ApiError as e:
            job.state, job.error, job.message = "failed", str(e), "Failed"
            log.warning("job %s (%s) failed: %s", job.id, job.kind, e)
        except Exception as e:  # report every failure to the UI
            text = str(e) or type(e).__name__
            job.state, job.message = "failed", "Failed"
            job.error = f"{text} (try again; if it keeps failing, run `ohs -v app` and look at the log)"
            log.exception("job %s (%s) failed", job.id, job.kind)
        finally:
            self._call_done(job, on_done)
            job.finished.set()
            self._emit(job)

    @staticmethod
    def _call_done(job: Job, on_done: Callable[[Job], None] | None) -> None:
        if on_done is not None:
            try:
                on_done(job)
            except Exception:
                log.exception("job %s: completion handler failed", job.id)
