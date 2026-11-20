"""A second pass that joins the first (FEATURE_PLAN N2; APP_API.md "A second pass").

"Scan these areas" in the Check panel starts a scan that continues another one:

* ``POST /api/scan/start`` with ``continues: {scan_id, tolerance_mm, version, areas, poses, same_setup}``:
  :meth:`PassManager.link` checks the first scan (it must have a mesh) and the asked areas (indices
  into its Check answer at that tolerance and mesh version) and returns the link. The new scan's
  entry and its recording's ``meta.json`` carry it as ``continues``; what the asked areas were on the
  first mesh is kept next to the pass (``pass_areas.npz``: :func:`ohs.guide.passes.area_samples`), so
  an edit of the first scan later does not change what was asked for. The pass takes the first
  scan's preset unless another one is given. A scan that continues nothing is started as before, and
  nothing of this module runs for it.
* **After Stop** (:meth:`PassManager.after_stop`) a chain of jobs runs, each an ordinary job the user
  can follow and cancel: the pass is processed (``process_scan``, its preset's quality), aligned to the
  first scan (``align``, the Align page's engine and acceptance rule), and, only when the alignment is
  accepted, merged with it (``merge``) into a new scan (``merged_from``). The first scan is never
  changed. The chain's state is kept in the pass's entry (``continues.chain``) and moves on from the
  jobs' updates (:meth:`PassManager.on_job`). Not accepted: nothing is merged, the pass stays a scan
  of its own, and the chain says so with the aligner's words and points to the Align page.
  ``POST .../pass/run`` runs what is left (after a cancel, a failure or a refusal).
* A merged first scan is continued by merging again from the scans it was made from, each at its
  place in the merge (its report), with the pass at the aligned pose: the result is one merge of all.
* ``GET .../pass``: the link, the chain and its running job, and the asked areas' faces on the first
  mesh (while that mesh is the one they were asked on).
* ``POST .../pass/compare``: the Check answer of the first scan and of the merged scan at the same
  tolerance, side by side, and for each asked area the share of it that passes now
  (:func:`ohs.guide.passes.area_progress`), with :data:`MERGE_NOTE`.
"""
from __future__ import annotations

import base64
import logging
import math
import threading
from typing import Any

import numpy as np

from ohs.server.protocol import ApiError, jsonable

__all__ = ["PassManager", "MERGE_NOTE", "CHAIN_STEPS"]

log = logging.getLogger(__name__)

#: said with every before and after (VALIDATION.md, "Aligning and merging two scans")
MERGE_NOTE = ("A merge adds coverage: it fills in surface the first scan did not measure well enough. It does not yet "
              "make the surface the first scan already saw well more accurate: where both scans saw a place, the two "
              "are averaged.")
CHAIN_STEPS = ("process", "align", "merge")
STEP_DOING = {"process": "making the pass's mesh", "align": "aligning the pass to the first scan",
              "merge": "merging the two"}
ACTIVE = ("processing", "aligning", "merging")
SETUP_WORDS = {True: "The part was not moved since the first scan: the table helps to align the two, and the merge keeps it.",
               False: "The part was moved since the first scan: the alignment uses the part alone, and each scan's table "
                      "is left out of the merge."}
MAX_POSES = 12
MAX_AREAS = 50
AREAS_FILE = "pass_areas.npz"
POSE_VECTORS = ("position_mm", "camera_position_mm", "look_dir", "up", "target_mm")
POSE_NUMBERS = ("distance_mm", "tilt_deg", "azimuth_deg")


def _finite(x: Any, what: str) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ApiError(422, f"{what} is not a number", "send the pose as the Check panel gave it") from None
    if not math.isfinite(v):
        raise ApiError(422, f"{what} is not a finite number", "send the pose as the Check panel gave it")
    return v


def _pose(p: Any, k: int) -> dict:
    """One pose to scan from, as the Check panel gave it (its numbers and its sentence), checked."""
    if not isinstance(p, dict):
        raise ApiError(422, f"pose {k + 1} is not an object", "send the poses as the Check panel gave them")
    pose = p.get("pose") if isinstance(p.get("pose"), dict) else p
    out: dict[str, Any] = {}
    for key in POSE_VECTORS:
        if key in pose:
            v = pose[key]
            if not isinstance(v, (list, tuple)) or len(v) != 3:
                raise ApiError(422, f"pose {k + 1}: {key} must have three numbers", "send the pose as the Check panel gave it")
            out[key] = [_finite(x, f"pose {k + 1}: {key}") for x in v]
    for key in POSE_NUMBERS:
        if key in pose and pose[key] is not None:
            out[key] = _finite(pose[key], f"pose {k + 1}: {key}")
    if "T_wc" in pose:
        T = np.asarray(pose["T_wc"], dtype=object)
        if T.shape != (4, 4):
            raise ApiError(422, f"pose {k + 1}: T_wc must be 4x4", "send the pose as the Check panel gave it")
        out["T_wc"] = [[_finite(x, f"pose {k + 1}: T_wc") for x in row] for row in T.tolist()]
    if "target_mm" not in out or "look_dir" not in out:
        raise ApiError(422, f"pose {k + 1} has no target_mm or look_dir", "send the pose as the Check panel gave it")
    area = p.get("area")
    text = p.get("text")
    return {"area": int(area) if isinstance(area, (int, float)) and not isinstance(area, bool) else None,
            "kind": str(p.get("kind") or "area")[:16], "text": str(text)[:400] if text else None, "pose": out}


class PassManager:
    """See the module docstring. ``Studio.passes``."""

    def __init__(self, studio: Any) -> None:
        self.st = studio
        self._lock = threading.RLock()
        self._watch: dict[str, tuple[str, str, str]] = {}  # job id -> (project, pass scan, step)
        self._starting: dict[tuple[str, str], int] = {}  # passes whose next step is being submitted (and how deep)
        # Locking: self._lock guards the two above and each chain's read-modify-write of the scan entry. It is never
        # held while a job is submitted (the align and merge routes take Studio._lock, and Stop holds Studio._lock
        # when it calls after_stop).

    # ------------------------------------------------------------------ the first scan
    def _first(self, pid: str, sid: str) -> dict:
        """The first scan's entry, or the refusal in words: it must exist, be free and have a mesh."""
        entry = self.st.projects.scan_entry(pid, sid)  # 404
        name = entry.get("name", sid)
        if entry.get("status") in ("recording", "processing"):
            raise ApiError(409, f"the scan \"{name}\" is busy", "wait until it has finished, then scan its areas again")
        if not (self.st.projects.result_dir(pid, sid) / "mesh.ply").is_file():
            raise ApiError(409, f"the scan \"{name}\" has no mesh yet, so there is nothing to join a pass to",
                           "make its mesh first; then Check shows which areas to scan again")
        return entry

    def merge_problem(self, pid: str, sid: str, entry: dict | None = None) -> str | None:
        """Why a pass could be aligned to this scan but not merged with it, in words; None when it can."""
        from ohs.server.aligning import MAX_SCANS
        from ohs.server.processing import _read_json

        entry = entry if entry is not None else self.st.projects.scan_entry(pid, sid)
        name = entry.get("name", sid)
        merged = entry.get("merged_from") or []
        if not merged:
            rec = self.st.projects.recording_dir(pid, sid)
            if not (rec / "frames.csv").is_file():
                return f"the recording of \"{name}\" was removed to free space, and a merge fuses its frames again"
            if not (self.st.projects.result_dir(pid, sid) / "trajectory.csv").is_file():
                return f"\"{name}\" has no camera path with its mesh (make its mesh again)"
            return None
        if len(merged) + 1 > MAX_SCANS:
            return f"\"{name}\" was merged from {len(merged)} scans, and at most {MAX_SCANS} are merged at once"
        rep = _read_json(self.st.projects.result_dir(pid, sid) / "report.json") or {}
        inputs = rep.get("scans") or []
        if len(inputs) != len(merged):
            return f"the report of \"{name}\" does not say where each scan it was made from lies"
        for src, inp in zip(merged, inputs):
            sname = str(src.get("name") or src.get("id"))
            try:
                e = self.st.projects.scan_entry(pid, str(src.get("id")))
            except ApiError:
                return f"\"{sname}\", one of the scans \"{name}\" was made from, is no longer in the project"
            if e.get("merged_from") or not (self.st.projects.recording_dir(pid, e["id"]) / "frames.csv").is_file():
                return f"the recording of \"{sname}\", one of the scans \"{name}\" was made from, is not there any more"
            traj = self.st.projects.result_dir(pid, e["id"]) / "trajectory.csv"
            if not traj.is_file():
                return f"the camera path of \"{sname}\", one of the scans \"{name}\" was made from, is not there"
            if isinstance(inp.get("frames"), int):
                from ohs.track.posegraph import read_trajectory

                try:
                    n = sum(1 for r in read_trajectory(traj) if r[2])
                except (OSError, ValueError):
                    n = -1
                if n != int(inp["frames"]):
                    return f"the camera path of \"{sname}\" changed since \"{name}\" was merged (make the merge again first)"
        return None

    # ------------------------------------------------------------------ start
    def link(self, pid: str, body: Any) -> tuple[dict, dict]:
        """``continues`` of ``POST /api/scan/start``, checked: ``(the link, the asked areas' samples)``.
        Raises the refusal in words before anything is made."""
        from ohs.guide.passes import area_samples
        from ohs.mesh.check import weak_areas
        from ohs.server.projects import check_id

        if not isinstance(body, dict):
            raise ApiError(422, "continues must be an object", "send {scan_id, tolerance_mm, areas}")
        sid = check_id(str(body.get("scan_id") or ""), "scan")
        tol = body.get("tolerance_mm")
        if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not math.isfinite(float(tol)) or not 0 < float(tol) <= 100:
            raise ApiError(422, "the tolerance must be a number of mm above 0 (at most 100)",
                           "send the tolerance the Check panel used, for example 0.3")
        tol = float(tol)
        areas = body.get("areas") or []
        if not isinstance(areas, list) or not all(isinstance(a, int) and not isinstance(a, bool) for a in areas):
            raise ApiError(422, "areas must be a list of area numbers", "send the areas' places in the Check answer, from 0")
        if len(set(areas)) != len(areas):
            raise ApiError(422, "an area is asked for twice", "send each area once")
        if len(areas) > MAX_AREAS:
            raise ApiError(422, f"{len(areas)} areas are too many (at most {MAX_AREAS})", "pick the areas to scan again")
        poses_in = body.get("poses") or []
        if not isinstance(poses_in, list) or len(poses_in) > MAX_POSES:
            raise ApiError(422, f"poses must be a list of at most {MAX_POSES}", "send the places the Check panel suggested")
        poses = [_pose(p, k) for k, p in enumerate(poses_in)]
        same_setup = body.get("same_setup", True)
        if not isinstance(same_setup, bool):
            raise ApiError(422, "same_setup must be true or false", "true when the part was not moved since the first scan")
        entry = self._first(pid, sid)
        name = entry.get("name", sid)
        v, V, F, S, Fl = self.st.processing.mesh_arrays(pid, sid)
        version = body.get("version")
        if version is not None and (not isinstance(version, int) or int(version) != int(v)):
            raise ApiError(409, f"the mesh of \"{name}\" changed since its Check was shown",
                           "check it again, then press Scan these areas")
        samples = {"points": np.zeros((0, 3)), "normals": np.zeros((0, 3)), "weights": np.zeros(0),
                   "area": np.zeros(0, dtype=np.int32), "faces": np.zeros(0, dtype=np.int64)}
        listed: list[dict] = []
        if areas:
            try:
                r = weak_areas(V, F, S, Fl if Fl is not None and len(Fl) == len(F) else None, tolerance_mm=tol, why=False)
            except ValueError as e:
                raise ApiError(422, str(e), "send the tolerance the Check panel used, for example 0.3") from None
            n = len(r["areas"])
            bad = [a for a in areas if not 0 <= a < n]
            if bad:
                raise ApiError(422, f"area {bad[0] + 1} is not in the Check answer of \"{name}\" at ± {tol:g} mm "
                                    f"({n} areas)", "check the scan again, then press Scan these areas")
            picked = [r["areas"][a] for a in areas]
            samples = area_samples(V, F, [np.asarray(a["faces"]) for a in picked])
            samples["faces"] = np.concatenate([np.asarray(a["faces"], dtype=np.int64) for a in picked])
            listed = [{"index": int(i), "kind": a["kind"], "area_mm2": float(a["area_mm2"]),
                       "center": [float(x) for x in a["center"]], "n_faces": int(a["n_faces"])}
                      for i, a in zip(areas, picked)]
        problem = self.merge_problem(pid, sid, entry)
        link = {"scan_id": sid, "scan_name": name, "tolerance_mm": tol, "version": int(v), "areas": listed,
                "poses": poses, "same_setup": bool(same_setup), "setup_words": SETUP_WORDS[bool(same_setup)],
                "merge_problem": problem,
                "chain": {"state": "waiting", "step": None, "jobs": {}, "message": "Waiting for Stop: then the pass is "
                          "made into a mesh, aligned to the first scan and merged with it."}}
        return link, samples

    def preset_of(self, pid: str, sid: str) -> tuple[str | None, dict | None, str]:
        """The first scan's preset for the pass: ``(preset id or None for General, hand values, its name)``.
        A preset that is gone is General (and said so in the name)."""
        from ohs.app.presets import GENERAL_ID

        kept = self.st.projects.scan_entry(pid, sid).get("preset")
        if not isinstance(kept, dict) or not kept.get("id") or kept["id"] == GENERAL_ID:
            return None, (dict(kept.get("hand") or {}) if isinstance(kept, dict) else None) or None, "General"
        if self.st.presets.store.find(str(kept["id"])) is None:
            return None, None, f"General (the first scan's preset \"{kept.get('name') or kept['id']}\" is gone)"
        hand = {k: v for k, v in (kept.get("hand") or {}).items() if k != "mode"}
        return str(kept["id"]), hand or None, str(kept.get("name") or kept["id"])

    def save_areas(self, pid: str, sid: str, samples: dict) -> None:
        path = self.st.projects.scan_dir(pid, sid) / AREAS_FILE
        np.savez_compressed(path, **{k: np.asarray(v) for k, v in samples.items()})

    def _samples(self, pid: str, sid: str) -> dict | None:
        path = self.st.projects.scan_dir(pid, sid) / AREAS_FILE
        if not path.is_file():
            return None
        try:
            with np.load(path) as z:
                return {k: z[k] for k in z.files}
        except (OSError, ValueError) as e:
            log.warning("the asked areas of %s/%s cannot be read: %s", pid, sid, e)
            return None

    # ------------------------------------------------------------------ the chain
    def _link_of(self, pid: str, sid: str) -> tuple[dict, dict]:
        entry = self.st.projects.scan_entry(pid, sid)
        c = entry.get("continues")
        if not isinstance(c, dict) or not c.get("scan_id"):
            raise ApiError(409, f"\"{entry.get('name', sid)}\" is not a second pass of another scan",
                           "start one from the Check panel of the first scan: Scan these areas")
        return entry, c

    def _set_chain(self, pid: str, sid: str, **fields: Any) -> dict:
        with self._lock:
            entry = self.st.projects.scan_entry(pid, sid)
            c = dict(entry.get("continues") or {})
            chain = dict(c.get("chain") or {})
            jobs = dict(chain.get("jobs") or {})
            jobs.update(fields.pop("jobs", {}) or {})
            chain.update(fields, jobs=jobs)
            c["chain"] = chain
            self.st.projects.update_scan(pid, sid, continues=c)
            return chain

    def _settle_stale(self, pid: str, sid: str, c: dict) -> dict:
        """A chain that says a step runs while no job of it does (the app was closed meanwhile): stopped, in words."""
        chain = c.get("chain") or {}
        if chain.get("state") not in ACTIVE:
            return c
        step = chain.get("step") or "process"
        jid = (chain.get("jobs") or {}).get(step)
        with self._lock:
            watched = jid in self._watch or (pid, sid) in self._starting
        try:
            running = bool(jid) and self.st.jobs.get(jid).active
        except ApiError:
            running = False
        if watched or running:
            return c
        chain = self._set_chain(pid, sid, state="cancelled",
                                message=f"Stopped while {STEP_DOING.get(step, step)}: the app was closed before it ended. "
                                        "The pass is kept as its own scan; Continue runs the rest.")
        return {**c, "chain": chain}

    def _follow(self, job: Any, pid: str, sid: str, step: str) -> None:
        with self._lock:
            self._watch[job.id] = (pid, sid, step)
        self._set_chain(pid, sid, jobs={step: job.id})
        if not job.active:  # finished before it was watched
            self.on_job(job.to_dict())

    def after_stop(self, pid: str, sid: str) -> None:
        """Stop of a pass: the chain starts (called by ``Studio`` after the scan entry is written)."""
        try:
            entry, _ = self._link_of(pid, sid)
        except ApiError:
            return
        if not int(entry.get("n_frames") or 0):
            self._set_chain(pid, sid, state="failed", step="process",
                            message="The pass recorded no frames, so there is nothing to join. Scan the areas again.")
            return
        try:
            self._start_step(pid, sid, "process")
        except ApiError as e:
            self._set_chain(pid, sid, state="failed", step="process", message=f"The pass could not be processed: {e}.")

    def run(self, pid: str, sid: str) -> dict:
        """``POST .../pass/run``: run what is left of the chain (after a cancel, a failure or a refusal)."""
        with self._lock:
            entry, c = self._link_of(pid, sid)
            c = self._settle_stale(pid, sid, c)
            chain = c.get("chain") or {}
            if chain.get("state") in ACTIVE or (pid, sid) in self._starting:
                raise ApiError(409, "the pass's steps are running already", "follow them, or cancel the running one")
            if entry.get("status") == "recording":
                raise ApiError(409, "the pass is still recording", "stop the scan first")
            if chain.get("state") == "merged" and chain.get("merged_scan_id"):
                raise ApiError(409, "the pass is merged already", "open the merged scan, or scan the areas again")
            self._first(pid, c["scan_id"])
            step = "process" if not (self.st.projects.result_dir(pid, sid) / "mesh.ply").is_file() else "align"
            self._starting[(pid, sid)] = self._starting.get((pid, sid), 0) + 1  # a second run waits its turn
        try:
            self._start_step(pid, sid, step)  # outside the lock: it submits a job
        finally:
            with self._lock:
                n = self._starting.get((pid, sid), 1) - 1
                if n > 0:
                    self._starting[(pid, sid)] = n
                else:
                    self._starting.pop((pid, sid), None)
        return self.view(pid, sid)

    def _start_step(self, pid: str, sid: str, step: str, T: Any = None) -> None:
        """Submit ``step``'s job and follow it. The chain says the step first (so a job that ends at once moves it on
        from there), and the job's id once it is known. Never called with ``self._lock`` held."""
        _, c = self._link_of(pid, sid)
        first = c["scan_id"]
        name = c.get("scan_name") or first
        key = (pid, sid)
        with self._lock:
            self._starting[key] = self._starting.get(key, 0) + 1
        try:
            if step == "process":
                self._set_chain(pid, sid, state="processing", step="process", merged_scan_id=None, align=None,
                                message="Making the pass's mesh.")
                job = self.st.process_scan(pid, sid, None)  # the pass's preset's quality (the first scan's by default)
            elif step == "align":
                self._set_chain(pid, sid, state="aligning", step="align", merged_scan_id=None,
                                message=f"Aligning the pass to \"{name}\".")
                job = self.st.aligning.align(pid, first, sid, bool(c.get("same_setup", True)), None, True)
            else:
                self._set_chain(pid, sid, state="merging", step="merge",
                                message=f"Merging the pass with \"{name}\" into a new scan.")
                job = self._merge(pid, sid, c, T)
            self._follow(job, pid, sid, step)
        except ApiError as e:
            self._set_chain(pid, sid, state="failed", step=step,
                            message=f"Could not start {STEP_DOING[step]}: {e}. The pass is kept as its own scan.")
            raise
        finally:
            with self._lock:
                n = self._starting.get(key, 1) - 1
                if n > 0:
                    self._starting[key] = n
                else:
                    self._starting.pop(key, None)

    def _merge(self, pid: str, sid: str, c: dict, T: Any) -> Any:
        from ohs.server.processing import _read_json

        first = c["scan_id"]
        fe = self.st.projects.scan_entry(pid, first)
        pe = self.st.projects.scan_entry(pid, sid)
        name = f"{fe.get('name', first)} + {pe.get('name', sid)}"
        same = bool(c.get("same_setup", True))
        merged = fe.get("merged_from") or []
        if not merged:
            return self.st.aligning.merge(pid, first, sid, T, same, name)
        inputs = (_read_json(self.st.projects.result_dir(pid, first) / "report.json") or {}).get("scans") or []
        items = [(str(src["id"]), inp.get("T", np.eye(4).tolist())) for src, inp in zip(merged, inputs)]
        return self.st.aligning.merge_many(pid, items + [(sid, T)], same, name)

    def on_job(self, job: dict) -> None:
        """Every job update (``Studio._job_update``): a watched job that ended moves its chain on."""
        if job.get("state") not in ("done", "failed", "cancelled"):
            return
        with self._lock:
            w = self._watch.pop(str(job.get("id")), None)
        if w is None:
            return
        pid, sid, step = w
        try:
            self._finished(job, pid, sid, step)
        except Exception as e:  # never let a job update fail; the chain says what happened
            log.exception("the second pass %s/%s after %s", pid, sid, step)
            safe = str(e) or type(e).__name__
            try:
                self._set_chain(pid, sid, state="failed", step=step,
                                message=f"Stopped after {STEP_DOING[step]}: {safe}. The pass is kept as its own scan.")
            except Exception:  # noqa: BLE001
                log.exception("could not record the chain's state")

    def _finished(self, job: dict, pid: str, sid: str, step: str) -> None:
        state = job["state"]
        _, c = self._link_of(pid, sid)
        first_name = c.get("scan_name") or c["scan_id"]
        if state == "cancelled":
            self._set_chain(pid, sid, state="cancelled", step=step,
                            message=f"Cancelled while {STEP_DOING[step]}. The pass is kept as its own scan; "
                                    "Continue runs the rest.")
            return
        if state == "failed":
            self._set_chain(pid, sid, state="failed", step=step,
                            message=f"Failed while {STEP_DOING[step]}: {job.get('error') or 'unknown error'}. "
                                    "The pass is kept as its own scan.")
            self.st.notify("error", f"Second pass: failed while {STEP_DOING[step]}. The pass is kept as its own scan.")
            return
        res = job.get("result") or {}
        if step == "process":
            self._start_step(pid, sid, "align")
        elif step == "align":
            info = {k: res.get(k) for k in ("accepted", "verdict", "overlap", "agree", "rms_mm", "sure", "doubt", "start")}
            info["T"] = res.get("T")
            if not res.get("accepted"):
                msg = (f"{res.get('verdict') or 'Not aligned.'} Nothing was merged: the pass is kept as its own scan. "
                       f"Align it to \"{first_name}\" on the Align page, where matching points can be picked.")
                self._set_chain(pid, sid, state="not_aligned", step="align", align=info, message=msg)
                self.st.notify("warn", f"Second pass not merged: the alignment to \"{first_name}\" was not accepted. "
                                       "Use the Align page.")
                return
            problem = self.merge_problem(pid, c["scan_id"])
            if problem:
                self._set_chain(pid, sid, state="not_merged", step="align", align=info,
                                message=f"Aligned, but not merged: {problem}. The pass is kept as its own scan.")
                self.st.notify("warn", f"Second pass aligned but not merged: {problem}.")
                return
            self._set_chain(pid, sid, align=info)
            self._start_step(pid, sid, "merge", T=res.get("T"))
        else:
            mid = res.get("scan_id")
            self._set_chain(pid, sid, state="merged", step="merge", merged_scan_id=mid,
                            message=f"Merged with \"{first_name}\" into \"{(res.get('scan') or {}).get('name') or mid}\". "
                                    f"\"{first_name}\" itself is unchanged.")
            self.st.notify("info", f"Second pass merged with \"{first_name}\"")

    # ------------------------------------------------------------------ views
    def view(self, pid: str, sid: str) -> dict:
        """``GET .../pass``: the link, the chain, its running job, and the asked areas' faces on the first
        mesh while that mesh is still the version they were asked on."""
        entry, c = self._link_of(pid, sid)
        c = self._settle_stale(pid, sid, c)
        chain = dict(c.get("chain") or {})
        job = None
        jid = (chain.get("jobs") or {}).get(chain.get("step") or "")
        if jid:
            try:
                job = self.st.jobs.get(jid).to_dict()
            except ApiError:
                job = None
        first = c["scan_id"]
        out = {k: v for k, v in c.items() if k != "chain"}
        out.update(pass_id=sid, pass_name=entry.get("name", sid), chain=chain, job=job, steps=list(CHAIN_STEPS),
                   note=MERGE_NOTE)
        lit = None
        why_not = None
        try:
            fe = self.st.projects.scan_entry(pid, first)
            out["first"] = {"id": first, "name": fe.get("name", first),
                            "mesh_url": f"/api/projects/{pid}/scans/{first}/files/mesh.ply",
                            "has_mesh": (self.st.projects.result_dir(pid, first) / "mesh.ply").is_file()}
            if out["first"]["has_mesh"] and c.get("areas"):
                v = int(self.st.processing.mesh_arrays(pid, first)[0])
                s = self._samples(pid, sid)
                if v != int(c.get("version", -1)):
                    why_not = f"\"{fe.get('name', first)}\" was edited since these areas were asked for"
                elif s is None or "faces" not in s:
                    why_not = "what the areas were is not kept with this pass"
                else:
                    faces, lab = np.asarray(s["faces"]), np.asarray(s["area"])
                    lit = [base64.b64encode(np.asarray(faces[lab == k], dtype="<u4").tobytes()).decode("ascii")
                           for k in range(len(c["areas"]))]
        except ApiError as e:
            out["first"] = {"id": first, "name": c.get("scan_name") or first, "mesh_url": None, "has_mesh": False}
            why_not = e.detail
        out["areas_faces"] = lit
        out["areas_faces_why_not"] = why_not
        return jsonable(out)

    @staticmethod
    def _shares(r: dict, sid: str, name: str) -> dict:
        return {"id": sid, "name": name, "share_ok": r["share_ok"], "share_uncertain": r["share_uncertain"],
                "share_not_measured": r["share_not_measured"], "n_areas": int(r["n_areas"]), "area_mm2": float(r["area_mm2"]),
                "has_sigma": bool(r["has_sigma"])}

    def compare(self, pid: str, sid: str, tolerance_mm: float | None = None) -> dict:
        """``POST .../pass/compare``: before and after, at the link's tolerance unless another is given."""
        from ohs.guide.passes import MATCH_ANGLE_DEG, MATCH_MM, PASSES_SHARE, area_progress, common_surface
        from ohs.server.processing import _read_json
        from ohs.mesh.check import weak_areas

        _, c = self._link_of(pid, sid)
        chain = c.get("chain") or {}
        mid = chain.get("merged_scan_id")
        if chain.get("state") != "merged" or not mid:
            raise ApiError(409, "the pass is not merged with the first scan", chain.get("message") or
                           "wait for the pass's steps to finish")
        tol = float(c["tolerance_mm"] if tolerance_mm is None else tolerance_mm)
        if not math.isfinite(tol) or tol <= 0:
            raise ApiError(422, f"the tolerance must be above 0 mm, got {tolerance_mm}", "give the tolerance in mm, for example 0.3")
        first = c["scan_id"]
        try:
            me = self.st.projects.scan_entry(pid, mid)
        except ApiError:
            raise ApiError(409, "the merged scan is no longer in the project", "scan the areas again") from None
        fe = self.st.projects.scan_entry(pid, first)
        out: dict[str, Any] = {"tolerance_mm": tol, "note": MERGE_NOTE,
                               "rule": {"match_mm": MATCH_MM, "match_angle_deg": MATCH_ANGLE_DEG, "passes_share": PASSES_SHARE}}
        meshes = {}
        for key, s, e in (("first", first, fe), ("merged", mid, me)):
            _, V, F, S, Fl = self.st.processing.mesh_arrays(pid, s)
            fl = Fl if Fl is not None and len(Fl) == len(F) else None
            meshes[key] = (V, F, S, fl)
            r = weak_areas(V, F, S, fl, tolerance_mm=tol, why=False)
            out[key] = self._shares(r, s, e.get("name", s))
            if key == "merged":
                samples = self._samples(pid, sid)
                prog = area_progress(samples, V, F, S, fl, tol) if samples is not None and len(c.get("areas") or []) else []
        areas = []
        for a, p in zip(c.get("areas") or [], prog):
            areas.append({**a, "share_ok": p["share_ok"], "found_share": p["found_share"], "state": p["state"]})
        out["areas"] = areas
        # before and after on the surface both meshes hold; what of the first scan's is not in the merge, and why
        com = common_surface(meshes["first"], meshes["merged"], tol)
        out["common"] = {k: com[k] for k in ("found_share", "before_ok", "after_ok")}
        why = None
        tables = (_read_json(self.st.projects.result_dir(pid, mid) / "report.json") or {}).get("scans") or []
        if any(isinstance(t, dict) and t.get("table") for t in tables):
            why = "the part was moved, so each scan's table was left out of the merge"
        out["missing"] = {"share": com["missing_share"], "area_mm2": com["missing_mm2"], "why": why}
        out["whole_note"] = ("Each scan's whole mesh, as its own Check says it: the shares are of different surfaces "
                             "when the merged scan holds less or more than the first.")
        out["areas_note"] = (None if tol == float(c["tolerance_mm"]) else
                             f"The areas were asked for at ± {float(c['tolerance_mm']):g} mm; here they are judged at ± {tol:g} mm.")
        return jsonable(out)
