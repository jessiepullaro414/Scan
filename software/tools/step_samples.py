"""Write a fixed set of sample STEP files of fitted shapes, to open in a CAD program (FEATURE_PLAN C1, D3).

    python tools/step_samples.py <folder>

writes into ``<folder>``:

* ``box_with_hole_and_boss.step``: an 80 x 60 x 20 mm box scanned at a slant: its top face, a
  side face, a Ø 10 mm hole in the top and a Ø 14 mm boss standing on it, in the part's frame
  built from (top, side, hole). The hole's centre is the CAD origin, and the part's X, Y and Z
  axes are drawn there;
* ``full_cylinder.step``: a Ø 20 mm cylinder, 50 mm long, the whole round (in the scan's frame);
* ``part_cylinder.step``: 90° of a Ø 30 mm cylinder, 40 mm long;
* ``plate_outline.step``: a 200 x 120 mm plate with its scanned outline (40,000 stepped points,
  simplified as the export does), two real holes in it, specks left out, and the two holes'
  circles;
* ``ragged_table.step``: a 400 x 300 mm table top whose outline is as hard as a real scan's: a
  ragged outer loop of 28,000 points with three narrow slots and a lobe touching it at one point,
  four islands beside it, 300 pits of 2-6 mm and two real holes (Ø 40 and Ø 25 mm), and the two
  holes' circles.

The shapes are made up here with exact numbers (no scan, no fit), so what a CAD program shows
can be checked against them: :func:`sample_set` gives each file's features, outlines and frame,
which ``tests/test_cad_step.py`` uses for its checks.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ohs.cad.shapes import cad_shapes  # noqa: E402
from ohs.cad.step import write_step  # noqa: E402
from ohs.measure.frame import part_frame  # noqa: E402


def _rotation(axis, angle_deg: float) -> np.ndarray:
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    t = math.radians(angle_deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * K @ K


class _Pose:
    """Where the scanner happened to see the part: the part's own numbers into the scan's frame."""

    def __init__(self, axis, angle_deg, shift):
        self.R = _rotation(axis, angle_deg)
        self.t = np.asarray(shift, float)

    def p(self, x) -> list:
        return [float(v) for v in self.R @ np.asarray(x, float) + self.t]

    def d(self, x) -> list:
        return [float(v) for v in self.R @ np.asarray(x, float)]


def _square_to(n) -> np.ndarray:
    n = np.asarray(n, float)
    e = np.zeros(3)
    e[int(np.argmin(np.abs(n)))] = 1.0
    u = e - (e @ n) * n
    return u / np.linalg.norm(u)


def _plane(pose: _Pose, fid, name, point, normal, rect_axes, half) -> dict:
    """A kept plane feature as features.json holds it, with its extent's small part."""
    n = np.asarray(pose.d(normal))
    u = _square_to(n)
    v = np.cross(n, u)
    return {"id": fid, "name": name, "kind": "plane",
            "shape": {"kind": "plane", "point": pose.p(point), "normal": [float(x) for x in n], "rms_mm": 0.02,
                      "flatness_mm": 0.1, "n": 5000, "normal_sigma_deg": 0.01, "offset_sigma_mm": 0.005},
            "extent": {"kind": "plane", "origin": pose.p(point), "u": [float(x) for x in u],
                       "v": [float(x) for x in v],
                       "rectangle": {"center": pose.p(point), "axes": [pose.d(a) for a in rect_axes],
                                     "half_sizes_mm": [float(h) for h in half]},
                       "area_mm2": 4.0 * half[0] * half[1], "n_outer": 1, "n_holes": 0, "n_points": 4}}


def _cylinder(pose: _Pose, fid, name, point, axis, radius, along, start_deg, arc_deg) -> dict:
    a = np.asarray(pose.d(axis))
    return {"id": fid, "name": name, "kind": "cylinder",
            "shape": {"kind": "cylinder", "point": pose.p(point), "axis": [float(x) for x in a],
                      "radius_mm": float(radius), "rms_mm": 0.02, "n": 3000, "radius_sigma_mm": 0.01,
                      "axis_sigma_deg": 0.05, "point_sigma_mm": 0.01,
                      "diameter_mm": 2.0 * radius, "diameter_sigma_mm": 0.02},
            "extent": {"kind": "cylinder", "along_mm": [float(x) for x in along], "length_mm": float(along[1] - along[0]),
                       "zero": [float(x) for x in _square_to(a)], "start_deg": float(start_deg),
                       "arc_deg": float(arc_deg)}}


def _hole(pose: _Pose, fid, name, center, normal, radius) -> dict:
    return {"id": fid, "name": name, "kind": "hole",
            "shape": {"kind": "circle", "center": pose.p(center), "normal": pose.d(normal), "radius_mm": float(radius),
                      "rms_mm": 0.02, "n": 200, "radius_sigma_mm": 0.01, "center_sigma_mm": 0.01,
                      "diameter_mm": 2.0 * radius, "diameter_sigma_mm": 0.02},
            "extent": {"kind": "hole", "n_edge": 0, "edge": []}}


def _box() -> dict:
    pose = _Pose([0.3, -0.5, 0.8], 37.0, [112.4, -38.1, 251.9])
    top = _plane(pose, "f-top", "Top face", [40, 30, 20], [0, 0, 1], [[1, 0, 0], [0, 1, 0]], [40, 30])
    side = _plane(pose, "f-side", "Side face", [0, 30, 10], [1, 0, 0], [[0, 1, 0], [0, 0, 1]], [30, 10])
    hole = _hole(pose, "f-hole", "Hole Ø10", [25, 30, 20], [0, 0, 1], 5.0)
    boss = _cylinder(pose, "f-boss", "Boss Ø14", [58, 30, 27.5], [0, 0, 1], 7.0, [-7.5, 7.5], 0.0, 360.0)
    frame = part_frame(top["shape"], side["shape"], hole["shape"], ids=("f-top", "f-side", "f-hole"))
    return {"title": "Box with hole and boss", "features": [top, side, hole, boss], "outlines": {},
            "frame": frame.to_dict(), "boundary": "rectangle"}


def _full_cylinder() -> dict:
    pose = _Pose([1, 2, 0.5], 23.0, [-40.2, 15.7, 310.3])
    cyl = _cylinder(pose, "f-cyl", "Tube Ø20", [0, 0, 0], [0, 0, 1], 10.0, [-12.5, 37.5], 0.0, 360.0)
    return {"title": "Full cylinder", "features": [cyl], "outlines": {}, "frame": None, "boundary": "rectangle"}


def _part_cylinder() -> dict:
    pose = _Pose([-0.4, 1, 0.2], 61.0, [80.0, -12.5, 290.0])
    cyl = _cylinder(pose, "f-arc", "Bore Ø30, 90°", [5, -3, 2], [0, 0, 1], 15.0, [0.0, 40.0], 30.0, 90.0)
    return {"title": "Part cylinder", "features": [cyl], "outlines": {}, "frame": None, "boundary": "rectangle"}


def _stepped_loop(rng, corners_or_circle, n: int, step_mm: float, smooth: int = 1) -> np.ndarray:
    """A loop as a scan's outline has it: many points, stepped by about ``step_mm``, counter-clockwise.

    The steps move each point towards or away from the shape's centre, so the loop never crosses
    itself (as a mesh's border does not); ``smooth`` > 1 averages that many neighbours' steps, for
    a ragged edge rather than a fuzzy one."""
    kind, spec = corners_or_circle
    t = np.arange(n) / n
    if kind == "circle":
        (cx, cy), r = spec
        ang = 2 * np.pi * t
        xy = np.c_[cx + r * np.cos(ang), cy + r * np.sin(ang)]
        centre = np.array([cx, cy], float)
    else:
        P = np.asarray(spec, float)
        seg = np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1)
        s = t * seg.sum()
        k = np.searchsorted(np.cumsum(seg), s, side="right")
        f = (s - (np.cumsum(seg) - seg)[k]) / seg[k]
        xy = P[k] + f[:, None] * (np.roll(P, -1, axis=0)[k] - P[k])
        centre = P.mean(axis=0)
    step = rng.uniform(-step_mm, step_mm, n)
    if smooth > 1:
        step = np.convolve(np.r_[step[-smooth:], step, step[:smooth]], np.ones(smooth) / smooth, "same")[smooth:-smooth]
        step *= step_mm / max(np.abs(step).max(), 1e-12)
    ray = xy - centre
    return xy + step[:, None] * ray / np.linalg.norm(ray, axis=1)[:, None]


def _plate() -> dict:
    rng = np.random.default_rng(20261117)
    pose = _Pose([0.2, 1, -0.3], 48.0, [30.0, 60.0, 280.0])
    plate = _plane(pose, "f-plate", "Plate", [100, 60, 0], [0, 0, 1], [[1, 0, 0], [0, 1, 0]], [100, 60])
    # the outline in the plate's own 2D basis (origin, u, v), counter-clockwise seen from the normal
    ext = plate["extent"]
    o, u, v = (np.asarray(ext[k]) for k in ("origin", "u", "v"))
    R, t = pose.R, pose.t

    def to2(xy_part):  # the part's x, y on the plate -> the outline basis
        P = (np.c_[xy_part, np.zeros(len(xy_part))] @ R.T) + t - o
        return np.c_[P @ u, P @ v]

    corners = [[0, 0], [200, 0], [200, 120], [0, 120]]
    outer = to2(_stepped_loop(rng, ("poly", corners), 40000, 0.2))
    holes = [to2(_stepped_loop(rng, ("circle", ((50, 60), 10.0)), 3000, 0.15))[::-1],
             to2(_stepped_loop(rng, ("circle", ((150, 40), 6.0)), 2000, 0.15))[::-1]]
    holes += [to2(_stepped_loop(rng, ("circle", (c, 0.5)), 12, 0.05))[::-1] for c in ((120, 90), (30, 20), (170, 100))]
    for i, h in enumerate(holes):  # the loops must turn as region_extent gives them
        x, y = h[:, 0], h[:, 1]
        assert np.sum(x * np.roll(y, -1) - y * np.roll(x, -1)) < 0, i
    outline = {"kind": "plane", "origin": ext["origin"], "u": ext["u"], "v": ext["v"],
               "outer": [outer.tolist()], "holes": [h.tolist() for h in holes], "mesh_version": 1}
    ext.update(n_holes=len(holes), n_points=len(outer) + sum(len(h) for h in holes))
    hole_a = _hole(pose, "f-ha", "Hole A Ø20", [50, 60, 0], [0, 0, 1], 10.0)
    hole_b = _hole(pose, "f-hb", "Hole B Ø12", [150, 40, 0], [0, 0, 1], 6.0)
    return {"title": "Plate with outline", "features": [plate, hole_a, hole_b], "outlines": {"f-plate": outline},
            "frame": None, "boundary": "outline"}


def _ccw(xy) -> bool:
    x, y = xy[:, 0], xy[:, 1]
    return float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1))) > 0


#: the ragged table's two real holes: centre (mm) and diameter
TABLE_HOLES = (((100.0, 150.0), 40.0), ((300.0, 100.0), 25.0))


def _table_outline(rng) -> tuple[list, list]:
    """A 400 x 300 mm table top's outline as hard as a real scan's (in the table's own x, y): a
    ragged outer loop of 28,000 points with three narrow slots and a lobe that touches it at one
    point, four islands beside it, two real holes and 300 pits of 2-6 mm."""
    outer = _stepped_loop(rng, ("poly", [[0, 0], [400, 0], [400, 300], [0, 300]]), 28000, 1.5, smooth=8)
    # three slots 0.6 mm wide and 15 mm deep into the bottom edge (y = 0), its points nearby made straight
    for x0 in (60.0, 180.0, 330.0):
        near = (np.abs(outer[:, 1]) < 5) & (outer[:, 0] > x0 - 3) & (outer[:, 0] < x0 + 3.6)
        outer[near, 1] = 0.0
        at = int(np.flatnonzero(near & (outer[:, 0] < x0))[-1]) + 1
        after = int(np.flatnonzero(near & (outer[:, 0] > x0 + 0.6))[0])
        slot = np.array([[x0, 0.0], [x0, 15.0], [x0 + 0.6, 15.0], [x0 + 0.6, 0.0]])
        outer = np.vstack([outer[:at], slot, outer[after:]])
    # a lobe outside the top right corner, touching the loop at one point (the region pinches there)
    k = int(np.argmin(np.linalg.norm(outer - [400, 300], axis=1)))
    v = outer[k]
    lobe = v + np.array([[10.0, 3.0], [12.0, 12.0], [3.0, 10.0]])
    outer = np.vstack([outer[: k + 1], lobe, outer[k:]])
    assert _ccw(outer)
    islands = [_stepped_loop(rng, ("circle", (c, r)), 400, 0.3) for c, r in
               (((-30, 100), 8.0), ((430, 150), 10.0), ((200, -30), 6.0), ((200, 330), 9.0))]
    holes = [_stepped_loop(rng, ("circle", (c, d / 2)), 2000, 0.3, smooth=4)[::-1] for c, d in TABLE_HOLES]
    # pits on a grid, kept clear of the edges, the slots and the real holes
    pits = []
    for gx in np.arange(20.0, 381.0, 18.0):
        for gy in np.arange(25.0, 281.0, 14.0):
            c = np.array([gx, gy]) + rng.uniform(-3, 3, 2)
            if any(np.linalg.norm(c - h) < d / 2 + 8 for h, d in TABLE_HOLES):
                continue
            pits.append(_stepped_loop(rng, ("circle", (c, rng.uniform(1.0, 3.0))), 24, 0.15)[::-1])
    return [outer] + islands, holes + pits[:300]


def _ragged_table() -> dict:
    rng = np.random.default_rng(1113)
    pose = _Pose([0.5, -0.2, 1.0], 27.0, [-120.0, 40.0, 300.0])
    table = _plane(pose, "f-table", "Table top", [200, 150, 0], [0, 0, 1], [[1, 0, 0], [0, 1, 0]], [200, 150])
    ext = table["extent"]
    o, u, v = (np.asarray(ext[k]) for k in ("origin", "u", "v"))

    def to2(xy):  # the table's x, y -> the outline's basis
        P = (np.c_[xy, np.zeros(len(xy))] @ pose.R.T) + pose.t - o
        return np.c_[P @ u, P @ v]

    outers, holes = _table_outline(rng)
    outline = {"kind": "plane", "origin": ext["origin"], "u": ext["u"], "v": ext["v"],
               "outer": [to2(x).tolist() for x in outers], "holes": [to2(h).tolist() for h in holes],
               "mesh_version": 1}
    ext.update(n_outer=len(outers), n_holes=len(holes), n_points=sum(len(x) for x in outers + holes))
    feats = [table] + [_hole(pose, f"f-t{i}", f"Table hole {i + 1}", [c[0], c[1], 0], [0, 0, 1], d / 2)
                       for i, (c, d) in enumerate(TABLE_HOLES)]
    return {"title": "Ragged table", "features": feats, "outlines": {"f-table": outline}, "frame": None,
            "boundary": "outline"}


def sample_set() -> dict:
    """File name -> ``{"title", "features", "outlines", "frame", "boundary"}``, made fresh each call."""
    return {"box_with_hole_and_boss.step": _box(), "full_cylinder.step": _full_cylinder(),
            "part_cylinder.step": _part_cylinder(), "plate_outline.step": _plate(),
            "ragged_table.step": _ragged_table()}


def write_samples(folder) -> list[Path]:
    """Write every sample into ``folder`` (made if needed); returns the paths."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name, s in sample_set().items():
        shapes = cad_shapes(s["features"], outlines=s["outlines"], frame=s["frame"], boundary=s["boundary"])
        out.append(write_step(folder / name, shapes, title=s["title"]))
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    for p in write_samples(argv[0]):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
