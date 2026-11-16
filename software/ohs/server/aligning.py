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
MAX_SCANS = 8  # scans aligned or merged at once (every pair is aligned: 28 pairs at 8)
TAG_FRAMES = 120  # frames of a scan looked at for tags (an even pick of those with a pose; about 2 s at 640x400)


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

    def _tags(self, it: dict) -> Any:
        """The tags the scan saw, in its mesh's world (:mod:`ohs.align.tags`): a marker-mode scan's own
        map, or those found in its recording at the poses of its mesh; ``None`` when there are none
        or nothing to find them in (a merged scan). Tags only help: any failure here is logged and
        the scan is aligned from its shape."""
        from ohs.align import load_tag_map, tag_map
        from ohs.track.markers import MarkerParams

        res = it["mesh"].parent
        try:
            if (res / "marker_map.json").is_file():
                m = load_tag_map(res / "marker_map.json")
            elif (not it["entry"].get("merged_from") and (it["recording"] / "frames.csv").is_file()
                  and it["trajectory"].is_file()):
                m = tag_map(it["recording"], it["trajectory"], params=MarkerParams(tag_mm=float(self.st.cfg.markers.tag_mm)),
                            max_frames=TAG_FRAMES)
            else:
                return None
        except Exception as e:  # noqa: BLE001
            log.warning("no tags for scan %s: %s", it["id"], e)
            return None
        return m if len(m) else None

    def _pair(self, pid: str, target: str, source: str, need_recording: bool = False) -> tuple[dict, dict]:
        if target == source:
            raise ApiError(409, "a scan cannot be aligned to itself", "pick two different scans")
        return self._scan(pid, target, "first", need_recording), self._scan(pid, source, "second", need_recording)

    # ------------------------------------------------------------------ align
    def align(self, pid: str, target: str, source: str, same_setup: bool, pairs: list[dict] | None,
              use_tags: bool = True) -> Any:
        """``POST /api/projects/{pid}/align``. Without picked points and with ``use_tags``, tags both
        scans saw give the start (:func:`ohs.align.align_with_tags`)."""
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
                from ohs.align import align_pair, align_with_tags, scan_cloud_from_mesh

                ctx.progress(0.05, f"Reading \"{a['name']}\"", force=True)
                dst = scan_cloud_from_mesh(a["mesh"])
                ctx.check()
                ctx.progress(0.2, f"Reading \"{b['name']}\"", force=True)
                src = scan_cloud_from_mesh(b["mesh"])
                ctx.check()
                tags_row = None
                if init is None and use_tags:
                    ctx.progress(0.3, "Looking for tags in both scans", force=True)
                    ta = self._tags(a)
                    ctx.check()
                    tb = self._tags(b) if ta else None
                    ctx.check()
                    ctx.progress(0.5, "Aligning the two scans", force=True)
                    res, tags_row = align_with_tags(src, dst, tb, ta, same_setup=bool(same_setup))
                else:
                    ctx.progress(0.45, "Aligning the two scans", force=True)
                    res = align_pair(src, dst, init=init, same_setup=bool(same_setup))
                out = res.as_dict()
                out.update(project_id=pid, target=target, source=source, tags=tags_row,
                           pairs_rms_mm=None if pairs_rms is None else round(float(pairs_rms), 3),
                           support={"target": dst.support is not None, "source": src.support is not None},
                           message=res.verdict)
                return jsonable(out)

            return self.st.jobs.submit("align", run, message="Queued: align two scans",
                                       meta={"project_id": pid, "target": target, "source": source})

    # ------------------------------------------------------------------ several scans
    def align_all(self, pid: str, scans: list[str], same_setup: bool, picked: list[dict] | None,
                  use_tags: bool = True) -> Any:
        """``POST /api/projects/{pid}/align_all``: every scan placed in the first one's world (with
        ``use_tags``, pairs without picked points start from the tags they share)."""
        from ohs.align import pose_from_point_pairs

        if len(scans) < 2:
            raise ApiError(422, "at least two scans are needed", "pick two or more scans")
        if len(set(scans)) != len(scans):
            raise ApiError(409, "a scan is in the list twice", "pick each scan once")
        if len(scans) > MAX_SCANS:
            raise ApiError(422, f"{len(scans)} scans are too many at once (at most {MAX_SCANS})",
                           "merge some of them first, then align the merged scan with the others")
        with self.st._lock:
            items = [self._scan(pid, sid, "first" if k == 0 else f"scan {k + 1}:") for k, sid in enumerate(scans)]
            index = {sid: k for k, sid in enumerate(scans)}
            starts: dict[tuple[int, int], tuple[Any, Any]] = {}
            for e in picked or []:
                if e.get("a") not in index or e.get("b") not in index or e["a"] == e["b"]:
                    raise ApiError(422, "picked points name a scan that is not in the list", "pick points on two of the listed scans")
                pairs = e.get("pairs") or []
                if len(pairs) < MIN_PAIRS:
                    raise ApiError(422, f"{len(pairs)} pair(s) of points are too few", "pick at least three matching points on each scan")
                try:
                    src = [q["source"] for q in pairs]  # on scan b
                    dst = [q["target"] for q in pairs]  # on scan a
                    pose_from_point_pairs(src, dst)
                except (ValueError, KeyError, TypeError) as err:
                    raise ApiError(422, f"the picked points do not give a pose: {err}",
                                   "pick three or more matching points that span a triangle") from err
                starts[(index[e["a"]], index[e["b"]])] = (src, dst)

            def run(ctx: Any) -> dict:
                from ohs.align import align_many, scan_cloud_from_mesh

                clouds, tags = [], []
                for k, it in enumerate(items):
                    ctx.progress(0.3 * k / len(items), f"Reading \"{it['name']}\"", force=True)
                    clouds.append(scan_cloud_from_mesh(it["mesh"]))
                    ctx.check()
                    if use_tags:
                        ctx.progress(0.3 * (k + 0.5) / len(items), f"Looking for tags in \"{it['name']}\"", force=True)
                        tags.append(self._tags(it))
                        ctx.check()

                def progress(f: float, msg: str) -> None:
                    ctx.check()
                    ctx.progress(0.3 + 0.7 * f, msg)

                res = align_many(clouds, same_setup=bool(same_setup), picked=starts, tags=tags if use_tags else None,
                                 progress=progress)
                d = res.as_dict()
                verdict = res.verdict
                for k, it in enumerate(items):  # the engine counts scans; the user knows them by name
                    verdict = verdict.replace(f"scan {k + 1} ", f"\"{it['name']}\" ").replace(f"scan {k + 1}.", f"\"{it['name']}\".")
                return jsonable({
                    "project_id": pid, "scans": scans, "accepted": d["accepted"],
                    "T": {sid: d["T"][k] for k, sid in enumerate(scans)},
                    "placed": [scans[k] for k in d["placed"]], "unplaced": [scans[k] for k in d["unplaced"]],
                    "pairs": [{**{key: v for key, v in r.items() if key not in ("i", "j")}, "a": scans[r["i"]], "b": scans[r["j"]]}
                              for r in d["pairs"]],
                    "loops": d["loops"], "graph": d["graph"], "worst_link_mm": d["worst_link_mm"],
                    "support": {sid: clouds[k].support is not None for k, sid in enumerate(scans)},
                    "tags": {sid: (sorted(tags[k].corners) if use_tags and tags[k] else []) for k, sid in enumerate(scans)},
                    "same_setup": bool(same_setup), "ms": d["ms"], "verdict": verdict, "message": verdict})

            return self.st.jobs.submit("align_all", run, message="Queued: align several scans",
                                       meta={"project_id": pid, "scans": list(scans)})

    # ------------------------------------------------------------------ merge
    @staticmethod
    def _rigid(T: Any) -> np.ndarray:
        try:
            T = np.asarray(T, dtype=np.float64).reshape(4, 4)
        except (ValueError, TypeError) as e:
            raise ApiError(422, "the pose must be a 4x4 matrix", "send the pose the alignment returned") from e
        R = T[:3, :3]
        if not (np.all(np.isfinite(T)) and np.allclose(R @ R.T, np.eye(3), atol=1e-6) and np.linalg.det(R) > 0
                and np.allclose(T[3], [0.0, 0.0, 0.0, 1.0], atol=1e-9)):
            raise ApiError(422, "the pose is not a rigid motion", "send the pose the alignment returned")
        return T

    def merge(self, pid: str, target: str, source: str, T: Any, same_setup: bool, name: str | None) -> Any:
        """``POST /api/projects/{pid}/merge`` with two scans: the source moved by ``T`` onto the target."""
        if target == source:
            raise ApiError(409, "a scan cannot be aligned to itself", "pick two different scans")
        return self.merge_many(pid, [(target, np.eye(4)), (source, T)], same_setup, name)

    def merge_many(self, pid: str, items_in: list[tuple[str, Any]], same_setup: bool, name: str | None) -> Any:
        """``POST /api/projects/{pid}/merge``: every scan at its pose (in the first scan's world)."""
        if len(items_in) < 2:
            raise ApiError(422, "at least two scans are needed", "pick two or more scans")
        if len(items_in) > MAX_SCANS:
            raise ApiError(422, f"{len(items_in)} scans are too many at once (at most {MAX_SCANS})",
                           "merge some of them first, then merge the result with the others")
        sids = [sid for sid, _ in items_in]
        if len(set(sids)) != len(sids):
            raise ApiError(409, "a scan is in the list twice", "pick each scan once")
        poses = [self._rigid(T) for _, T in items_in]
        with self.st._lock:
            items = [self._scan(pid, sid, "first" if k == 0 else ("second" if len(sids) == 2 else f"scan {k + 1}:"),
                                need_recording=True) for k, sid in enumerate(sids)]
            title = (name or "").strip() or " + ".join(it["name"] for it in items)
            work = self.st.projects.project_dir(pid) / ".merge"
            noun = "two scans" if len(items) == 2 else f"{len(items)} scans"

            def run(ctx: Any) -> dict:
                from ohs.align import ScanInput, merge_scans, scan_cloud_from_mesh
                from ohs.io.recording import RecordingReader
                from ohs.server.projects import report_summary
                from ohs.server.protocol import quality_or_none
                from ohs.server.studio import process_params

                supports: list[Any] = [None] * len(items)
                if not same_setup:  # the part was moved: each scan's table is elsewhere, and stays out
                    ctx.progress(0.02, "Finding what the part lay on", force=True)
                    supports = [scan_cloud_from_mesh(it["mesh"]).support for it in items]
                    ctx.check()
                first = items[0]
                reader = RecordingReader(first["recording"])
                width = int((reader.meta.get("image_size") or list(reader.frame(0).image_size))[0])
                quality = quality_or_none(first["entry"].get("quality")) or "standard"
                params = dataclasses.replace(process_params(self.st.cfg, "markerless", quality, width), poisson=False)
                out = work / ctx.job.id
                shutil.rmtree(out, ignore_errors=True)

                def progress(k: int, n: int) -> None:
                    ctx.check()
                    ctx.progress(0.05 + 0.85 * (k + 1) / max(n, 1), f"Merging: frame {k + 1} of {n}")

                try:
                    rep = merge_scans([ScanInput(it["recording"], it["trajectory"], T, it["name"], support=sup)
                                       for it, T, sup in zip(items, poses, supports)], out, params, progress)
                    if rep["mesh"]["empty"]:
                        raise ApiError(409, "the merged model is empty", "check that the scans have a mesh, and align them again")
                    ctx.progress(0.95, "Adding the merged scan to the project", force=True)
                    scan, _ = self.st.projects.add_scan(pid, title, "markerless")
                    sid = scan["id"]
                    dst = self.st.projects.scan_dir(pid, sid) / "result"
                    shutil.move(str(out), str(dst))
                    scan = self.st.projects.update_scan(
                        pid, sid, status="processed", n_frames=int(rep["frames"]["fused"]), quality=quality,
                        duration_s=float(sum(float(it["entry"].get("duration_s", 0.0) or 0.0) for it in items)),
                        report=report_summary({**rep, "quality": quality}),
                        merged_from=[{"id": it["id"], "name": it["name"]} for it in items])
                finally:
                    shutil.rmtree(out, ignore_errors=True)
                    if work.is_dir() and not any(work.iterdir()):
                        work.rmdir()
                return {"project_id": pid, "scan_id": sid, "scan": scan, "merged_from": sids,
                        "message": f"Merged into \"{title}\""}

            def done(job: Any) -> None:
                if job.state == "done":
                    self.st.notify("info", f"Merged scan ready: {title}")
                elif job.state == "failed":
                    self.st.notify("error", f"Merging failed: {job.error}. The scans it was made from are unchanged.")

            meta = {"project_id": pid, "scans": sids}
            if len(sids) == 2:
                meta.update(target=sids[0], source=sids[1])
            return self.st.jobs.submit("merge", run, message=f"Queued: merge {noun}", meta=meta, on_done=done)
