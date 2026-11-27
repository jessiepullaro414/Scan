"""Tags as a start for aligning scans (research plan 4.7.2).

AprilTags stuck on the part, or on what it stands on, are seen by both scans. Their corners, in
each scan's world, give a rough pose of one scan in the other's, as points picked by hand would
(four per tag); :func:`ohs.align.pairwise.align_pair` then refines it from the shape (``init``).
Where the shape alone cannot say how the part is turned (a sphere, a cylinder, a row of equal
holes) or the scans share too little of it, the tags can.

* :func:`tag_map`: the tags a scan saw, in its world, found in its recording at its frames' poses:
  every frame with a pose, or an even pick of ``max_frames`` of them, detected in both rectified
  images and triangulated (:class:`ohs.track.markers.MarkerTracker`), replaced by the square of
  the printed size that fits them best, then moved into the world by the frame's pose. A tag's
  corners are the median of its observations, without those more than ``outlier_mm`` from it; a
  tag seen fewer than ``min_views`` times is left out.
* :func:`load_tag_map`: a marker-mode scan's own map (``marker_map.json``), in its world too.
* :func:`pose_from_tags`: the pose from the tags two maps share. A tag that does not fit the others
  (moved between the scans, or found wrongly) is left out, one at a time, until the rest agree
  within ``max_rms_mm``.

Only tags that stayed where they were relative to the part help: tags on the table give the right
start only when the part was not moved on the table between the scans (same setup); for a part
that was moved, only tags on the part itself. A start from the wrong tags is a wrong start, which
the alignment then refuses (the shapes do not fit) or, in :func:`ohs.align.align_many`, replaces
with the guesses from the shape.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ohs.align.pairwise import pose_from_point_pairs
from ohs.geometry import kabsch

__all__ = ["TagMap", "TagPose", "tag_map", "load_tag_map", "pose_from_tags", "align_with_tags", "tag_pose_sigma_mm"]

log = logging.getLogger(__name__)


@dataclass
class TagMap:
    """Tags in one scan's world: id -> corners (4,3) mm (ArUco order), with how often each was
    seen and how far its observations lie from the corners (median, mm)."""

    corners: dict[int, np.ndarray] = field(default_factory=dict)
    views: dict[int, int] = field(default_factory=dict)
    spread_mm: dict[int, float] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.corners)

    def as_dict(self) -> dict:
        return {"tags": {str(i): {"corners": np.asarray(X).tolist(), "views": int(self.views.get(i, 0)),
                                  "spread_mm": round(float(self.spread_mm.get(i, 0.0)), 3)}
                         for i, X in sorted(self.corners.items())}}


@dataclass
class TagPose:
    """What :func:`pose_from_tags` found: ``T`` takes the source scan's world into the target's."""

    T: np.ndarray
    rms_mm: float  # of the corners of the tags used
    ids: list[int]  # the tags used
    left_out: list[int]  # shared tags that did not fit the others


def tag_map(recording: str | Path, trajectory: Any, calib: Any = None, params: Any = None, *,
            max_frames: int = 120, min_views: int = 2, outlier_mm: float = 2.0,
            progress: Callable[[int, int], None] | None = None) -> TagMap:
    """The tags the scan saw, in its world (module docstring). ``trajectory``: the scan's
    ``trajectory.csv`` or rows ``(index, t, ok, T_wc)``; ``calib``: the calibration the scan was
    processed with (default: the recording's own); ``params``: :class:`ohs.track.markers.MarkerParams`
    (its ``tag_mm`` is the printed size of the tags). ``progress(k, n)`` after each frame looked at."""
    from ohs.io.recording import RecordingReader  # noqa: PLC0415
    from ohs.track.markers import MarkerParams, MarkerTracker, tag_corners_local  # noqa: PLC0415
    from ohs.track.posegraph import read_trajectory  # noqa: PLC0415
    from ohs.vision.rectify import Rectifier  # noqa: PLC0415

    reader = RecordingReader(recording)
    cal = calib if calib is not None else reader.calibration()
    if cal is None:
        raise ValueError(f"{recording}: the recording has no calibration")
    rows = read_trajectory(trajectory) if isinstance(trajectory, (str, Path)) else list(trajectory)
    poses = {int(r[0]): np.asarray(r[3], dtype=np.float64).reshape(4, 4) for r in rows if r[2]}
    poses = {i: T for i, T in poses.items() if np.all(np.isfinite(T))}
    positions = [pos for pos, idx in enumerate(reader.indices) if int(idx) in poses]
    if len(positions) > max_frames > 0:
        positions = [positions[int(round(k))] for k in np.linspace(0, len(positions) - 1, int(max_frames))]
    rect = Rectifier(cal.stereo, 1.0)
    mp = params if params is not None else MarkerParams()
    mt = MarkerTracker(rect.K, rect.baseline, mp)
    square = tag_corners_local(float(mp.tag_mm))
    obs: dict[int, list[np.ndarray]] = {}
    frames = reader.iter_ahead(positions, 4)
    try:
        for k, frame in enumerate(frames):
            left, right = rect.rectify(frame.left, frame.right)
            found = mt.triangulate(mt.detect(left), mt.detect(right))  # camera-frame corners
            T = poses[int(frame.index)]
            for i, X in found.items():
                R, t = kabsch(square, X)  # the printed square that fits the four corners best
                Y = square @ R.T + t
                obs.setdefault(int(i), []).append(Y @ T[:3, :3].T + T[:3, 3])
            if progress is not None:
                progress(k, len(positions))
    finally:
        frames.close()
    out = TagMap()
    for i, Xs in sorted(obs.items()):
        A = np.stack(Xs)  # (m, 4, 3)
        med = np.median(A, axis=0)
        d = np.linalg.norm(A - med, axis=2).max(axis=1)  # each observation's worst corner
        keep = d <= float(outlier_mm)
        if keep.sum() < int(min_views):
            log.info("tag %d: %d of %d views agree, fewer than %d: left out", i, int(keep.sum()), len(Xs), min_views)
            continue
        X = np.median(A[keep], axis=0)
        out.corners[i] = X
        out.views[i] = int(keep.sum())
        out.spread_mm[i] = float(np.median(np.linalg.norm(A[keep] - X, axis=2)))
    log.info("tag map of %s: %d tags from %d frames (%s)", Path(recording).name, len(out), len(positions),
             ", ".join(f"{i}: {out.views[i]} views" for i in out.corners) or "none")
    return out


def load_tag_map(path: str | Path) -> TagMap:
    """A marker-mode scan's ``marker_map.json`` (its world is the scan's world, as the mesh's)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = TagMap()
    counts = d.get("n_observations") or {}
    for key, X in (d.get("corners") or {}).items():
        A = np.asarray(X, dtype=np.float64).reshape(4, 3)
        if np.all(np.isfinite(A)):
            out.corners[int(key)] = A
            out.views[int(key)] = int(counts.get(str(key), counts.get(key, 0)) or 0)
            out.spread_mm[int(key)] = 0.0
    return out


def align_with_tags(src: Any, dst: Any, tags_src: TagMap | None, tags_dst: TagMap | None, params: Any = None, *,
                    same_setup: bool = False) -> tuple[Any, dict | None]:
    """:func:`ohs.align.pairwise.align_pair` of the scan ``src`` to ``dst``, from the tags both saw
    when they share some and agree, else (or when that start is not accepted) from the shape's own
    guesses. Returns ``(the alignment, what the tags did)``: ``None`` when the two share no tag,
    else ``{"shared", "used", "left_out", "rms_mm", "start"}`` (``start``: ``"accepted"``, or why
    the tags gave no accepted start)."""
    from ohs.align.pairwise import AlignParams, align_pair  # noqa: PLC0415

    p = params if params is not None else AlignParams()
    row = None
    if tags_src and tags_dst:
        shared = sorted(set(tags_src.corners) & set(tags_dst.corners))
        tp = pose_from_tags(tags_src, tags_dst)
        if tp is not None:
            res = align_pair(src, dst, params, init=tp.T, same_setup=same_setup, init_from="tags")
            if p.hold_tags and res.accepted and res.overlap < p.caution_overlap:
                # the tag pose held (ohs.align.pairwise, module docstring 8.), from the tags that agree closely when
                # enough of them do; else from the same tags as without the hold (its sentence then says why). Not held,
                # the answer is the one without the hold (``off``); from the same tags its refinement is reused
                off = res
                th = pose_from_tags(tags_src, tags_dst, max_rms_mm=float(p.hold_max_rms_mm))
                same = th is None or len(th.ids) < int(p.hold_min_tags) or th.ids == tp.ids
                if not same:
                    tp = th
                hold = {"ids": list(tp.ids), "rms_mm": float(tp.rms_mm), "sigma_mm": tag_pose_sigma_mm(tp),
                        "dst_corners": np.concatenate([np.asarray(tags_dst.corners[i], dtype=np.float64) for i in tp.ids]),
                        "off": off, "refined": off if same else None}
                res = align_pair(src, dst, params, init=tp.T, same_setup=same_setup, init_from="tags", tag_hold=hold)
            row = {"shared": shared, "used": tp.ids, "left_out": tp.left_out, "rms_mm": round(tp.rms_mm, 3),
                   "start": "accepted" if res.accepted else f"refused: {res.reason}"}
            if res.tag_hold is not None:
                row["start"] = ("held" if res.tag_hold["held_by_tags"] else "overruled" if res.tag_hold["overruled"]
                                else row["start"])
            if res.accepted:
                return res, row
        elif shared:
            row = {"shared": shared, "used": [], "left_out": [], "rms_mm": None,
                   "start": "the shared tags do not agree with each other"}
    return align_pair(src, dst, params, same_setup=same_setup), row


def tag_pose_sigma_mm(tp: TagPose) -> float:
    """The tag pose's own 1-sigma displacement (mm; ENGINE_PLAN X19): the corners' RMS residual spread over the
    pose's six unknowns, ``rms * sqrt(6 / (4 n - 6))`` for ``n`` tags (four corners each), and for one tag its RMS. It
    leaves out what the tags and the stereo pair share (the scanner's calibration, a scale error moves both alike), which
    no residual shows."""
    n = 4 * len(tp.ids)
    return float(tp.rms_mm) if n <= 6 else float(tp.rms_mm) * float(np.sqrt(6.0 / (n - 6)))


def pose_from_tags(src: TagMap, dst: TagMap, max_rms_mm: float = 2.0) -> TagPose | None:
    """The pose of the source scan in the target scan's world from the tags both saw (module
    docstring), or ``None``: no tag in common, or no set of them that agrees within ``max_rms_mm``
    (RMS of the corners; a start is refined from the shape, so it need not be closer than a few
    millimetres, while a tag that was moved is usually moved by more)."""
    ids = sorted(set(src.corners) & set(dst.corners))
    left_out: list[int] = []
    while ids:
        A = np.concatenate([src.corners[i] for i in ids])
        B = np.concatenate([dst.corners[i] for i in ids])
        try:
            T, rms = pose_from_point_pairs(A, B)
        except ValueError:  # one tag's corners in a line: not a tag
            return None
        if rms <= max_rms_mm:
            return TagPose(T=T, rms_mm=float(rms), ids=list(ids), left_out=left_out)
        if len(ids) == 1:
            break
        r = np.linalg.norm(A @ T[:3, :3].T + T[:3, 3] - B, axis=1).reshape(len(ids), 4).mean(axis=1)
        worst = ids[int(np.argmax(r))]
        log.info("tag %d does not fit the other shared tags (%.2f mm): left out", worst, float(r.max()))
        left_out.append(worst)
        ids = [i for i in ids if i != worst]
    return None
