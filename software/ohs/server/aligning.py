"""Aligning two scans of a project and merging them into a new scan (APP_API.md, "M4 first
slice"; the engine: :mod:`ohs.align`).

* ``POST /api/projects/{pid}/align``: a job (kind ``align``) that aligns the *source* scan to the
  *target* scan from their meshes (``result/mesh.ply``), from the shape alone or from three or
  more matching points the user picked on the two meshes. Its result holds the pose, whether it
  was accepted, and a sentence for the user. Nothing is written.
* ``POST /api/projects/{pid}/merge``: a job (kind ``merge``) that fuses the raw frames of both
  scans into one model at a given pose and adds it to the project as a new scan, processed, with
  its mesh in ``result/``. The merged scan has no recording of its own: it can be viewed, edited
  and exported, not tracked again.

Both scans must have a mesh (``result/mesh.ply``); for the merge they also need their recordings
and the camera path of the mesh (``result/trajectory.csv``). A merged scan can therefore be
aligned to, but not merged again. A scan's mesh is in the scan's own world; the pose ``T`` takes
points of the source's mesh into the target's.
"""
from __future__ import annotations

import dataclasses
import logging
import shutil
from typing import Any

import numpy as np

from ohs.server.protocol import ApiError, jsonable

__all__ = ["AlignManager"]

log = logging.getLogger(__name__)

MIN_PAIRS = 3


class AlignManager:
    def __init__(self, studio: Any) -> None:
        self.st = studio

    # ------------------------------------------------------------------ checks
    def _scan(self, pid: str, sid: str, what: str, need_recording: bool = False) -> dict:
        """The scan's entry and files, or the 409 that says what is missing."""
        entry = self.st.projects.scan_entry(pid, sid)
        name = entry.get("name", sid)
        if entry.get("status") in ("recording", "processing"):
            raise ApiError(409, f"the {what} scan \"{name}\" is busy", "wait until it has finished, then try again")
        res = self.st.projects.scan_dir(pid, sid) / "result"
        if not (res / "mesh.ply").is_file():
            raise ApiError(409, f"the {what} scan \"{name}\" has no mesh yet", "make its mesh on the project page first")
        rec = self.st.projects.recording_dir(pid, sid)
        if need_recording:  # a merge fuses the frames again: it needs them and their poses
            if entry.get("merged_from") or not (rec / "frames.csv").is_file():
                raise ApiError(409, f"the {what} scan \"{name}\" has no recording (a merged scan, or its recording was removed)",
                               "merge scans that still have their recordings")
            if not (res / "trajectory.csv").is_file():
                raise ApiError(409, f"the {what} scan \"{name}\" has no camera path with its mesh",
                               "make its mesh again on the project page")
        return {"id": sid, "name": name, "entry": entry, "mesh": res / "mesh.ply", "trajectory": res / "trajectory.csv",
                "recording": rec}

    def _pair(self, pid: str, target: str, source: str, need_recording: bool = False) -> tuple[dict, dict]:
        if target == source:
            raise ApiError(409, "a scan cannot be aligned to itself", "pick two different scans")
        return self._scan(pid, target, "first", need_recording), self._scan(pid, source, "second", need_recording)

    # ------------------------------------------------------------------ align
    def align(self, pid: str, target: str, source: str, same_setup: bool, pairs: list[dict] | None) -> Any:
        """``POST /api/projects/{pid}/align``."""
        from ohs.align import pose_from_point_pairs

        with self.st._lock:
            a, b = self._pair(pid, target, source)
            init, pairs_rms = None, None
            if pairs:
                if len(pairs) < MIN_PAIRS:
                    raise ApiError(422, f"{len(pairs)} pair(s) of points are too few", "pick at least three matching points on each scan")
                try:
                    init, pairs_rms = pose_from_point_pairs([p["source"] for p in pairs], [p["target"] for p in pairs])
                except (ValueError, KeyError, TypeError) as e:
                    raise ApiError(422, f"the picked points do not give a pose: {e}",
                                   "pick three or more matching points that span a triangle") from e

            def run(ctx: Any) -> dict:
                from ohs.align import align_pair, scan_cloud_from_mesh

                ctx.progress(0.05, f"Reading \"{a['name']}\"", force=True)
                dst = scan_cloud_from_mesh(a["mesh"])
                ctx.check()
                ctx.progress(0.25, f"Reading \"{b['name']}\"", force=True)
                src = scan_cloud_from_mesh(b["mesh"])
                ctx.check()
                ctx.progress(0.45, "Aligning the two scans", force=True)
                res = align_pair(src, dst, init=init, same_setup=bool(same_setup))
                out = res.as_dict()
                out.update(project_id=pid, target=target, source=source,
                           pairs_rms_mm=None if pairs_rms is None else round(float(pairs_rms), 3),
                           support={"target": dst.support is not None, "source": src.support is not None},
                           message=res.verdict)
                return jsonable(out)

            return self.st.jobs.submit("align", run, message="Queued: align two scans",
                                       meta={"project_id": pid, "target": target, "source": source})

    # ------------------------------------------------------------------ merge
    def merge(self, pid: str, target: str, source: str, T: Any, same_setup: bool, name: str | None) -> Any:
        """``POST /api/projects/{pid}/merge``."""
        try:
            T = np.asarray(T, dtype=np.float64).reshape(4, 4)
        except (ValueError, TypeError) as e:
            raise ApiError(422, "the pose must be a 4x4 matrix", "send the pose the alignment returned") from e
        R = T[:3, :3]
        if not (np.all(np.isfinite(T)) and np.allclose(R @ R.T, np.eye(3), atol=1e-6) and np.linalg.det(R) > 0
                and np.allclose(T[3], [0.0, 0.0, 0.0, 1.0], atol=1e-9)):
            raise ApiError(422, "the pose is not a rigid motion", "send the pose the alignment returned")
        with self.st._lock:
            a, b = self._pair(pid, target, source, need_recording=True)
            title = (name or "").strip() or f"{a['name']} + {b['name']}"
            work = self.st.projects.project_dir(pid) / ".merge"

            def run(ctx: Any) -> dict:
                from ohs.align import ScanInput, merge_scans, scan_cloud_from_mesh
                from ohs.io.recording import RecordingReader
                from ohs.server.projects import report_summary
                from ohs.server.protocol import quality_or_none
                from ohs.server.studio import process_params

                supports = [None, None]
                if not same_setup:  # the part was moved: each scan's table is elsewhere, and stays out
                    ctx.progress(0.02, "Finding what the part lay on", force=True)
                    supports = [scan_cloud_from_mesh(s["mesh"]).support for s in (a, b)]
                    ctx.check()
                reader = RecordingReader(a["recording"])
                width = int((reader.meta.get("image_size") or list(reader.frame(0).image_size))[0])
                quality = quality_or_none(a["entry"].get("quality")) or "standard"
                params = dataclasses.replace(process_params(self.st.cfg, "markerless", quality, width), poisson=False)
                out = work / ctx.job.id
                shutil.rmtree(out, ignore_errors=True)

                def progress(k: int, n: int) -> None:
                    ctx.check()
                    ctx.progress(0.05 + 0.85 * (k + 1) / max(n, 1), f"Merging: frame {k + 1} of {n}")

                try:
                    rep = merge_scans([ScanInput(a["recording"], a["trajectory"], np.eye(4), a["name"], support=supports[0]),
                                       ScanInput(b["recording"], b["trajectory"], T, b["name"], support=supports[1])],
                                      out, params, progress)
                    if rep["mesh"]["empty"]:
                        raise ApiError(409, "the merged model is empty", "check that both scans have a mesh, and align them again")
                    ctx.progress(0.95, "Adding the merged scan to the project", force=True)
                    scan, _ = self.st.projects.add_scan(pid, title, "markerless")
                    sid = scan["id"]
                    dst = self.st.projects.scan_dir(pid, sid) / "result"
                    shutil.move(str(out), str(dst))
                    scan = self.st.projects.update_scan(
                        pid, sid, status="processed", n_frames=int(rep["frames"]["fused"]), quality=quality,
                        duration_s=float(sum(float(x["entry"].get("duration_s", 0.0) or 0.0) for x in (a, b))),
                        report=report_summary({**rep, "quality": quality}),
                        merged_from=[{"id": a["id"], "name": a["name"]}, {"id": b["id"], "name": b["name"]}])
                finally:
                    shutil.rmtree(out, ignore_errors=True)
                    if work.is_dir() and not any(work.iterdir()):
                        work.rmdir()
                return {"project_id": pid, "scan_id": sid, "scan": scan, "merged_from": [target, source],
                        "message": f"Merged into \"{title}\""}

            def done(job: Any) -> None:
                if job.state == "done":
                    self.st.notify("info", f"Merged scan ready: {title}")
                elif job.state == "failed":
                    self.st.notify("error", f"Merging failed: {job.error}. The two scans are unchanged.")

            return self.st.jobs.submit("merge", run, message="Queued: merge two scans",
                                       meta={"project_id": pid, "target": target, "source": source}, on_done=done)
