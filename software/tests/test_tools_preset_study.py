"""``tools/preset_study.py`` (FEATURE_PLAN P1): its settings, recipes, judge and marks, on small synthetic input
(no rendering, no scan)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from tools import preset_study as ps  # noqa: E402


def test_defaults_are_the_apps_make_mesh():
    from ohs.app.config import Config
    from ohs.server.studio import process_params

    app = process_params(Config(), "markerless", "standard", 1280)
    pp = ps.process_params_for("default", "-")
    assert pp.pipeline == app.pipeline
    assert (pp.max_frame_rate, pp.frame_step, pp.frame_motion_deg) == (app.max_frame_rate, app.frame_step, app.frame_motion_deg)
    assert ps.RECIPES["general"] == [ps.FLOATERS, {"op": "fill_holes", "params": {"max_hole_mm": 5.0}}]
    for s, v in ps.DEFAULTS.items():
        assert v in ps.SETTINGS[s]


def test_each_setting_changes_only_its_value():
    base = ps.process_params_for("default", "-").pipeline
    assert ps.process_params_for("voxel", 2.0).pipeline.fusion.voxel_mm == 2.0
    assert ps.process_params_for("gap_guard", 0.0).pipeline.gap_guard_px == 0.0
    assert ps.process_params_for("thin_walls", "off").pipeline.thin_walls == "off"
    d = ps.process_params_for("depth_range", "200-350").pipeline.depth
    assert (d.min_depth_mm, d.max_depth_mm) == (200.0, 350.0)
    prev = ps.process_params_for("quality", "preview")
    assert prev.pipeline.scale == 0.5 and prev.pipeline.fusion.voxel_mm == 2.0
    assert ps.process_params_for("quality", "best").max_frame_rate == 0.0
    g = ps.process_params_for("gap_guard", 4.0).pipeline
    assert (g.fusion, g.depth, g.thin_walls, g.scale) == (base.fusion, base.depth, base.thin_walls, base.scale)


def test_marks_against_the_spread():
    assert ps.marks(0.30, 0.10, 0.05) == "*"
    assert ps.marks(0.12, 0.10, 0.05) == ""
    assert ps.marks(0.10, 0.10, 0.05) == "="
    assert ps.marks(None, 0.10, 0.05) == "*"
    assert ps.marks(None, None, 0.05) == ""
    runs = {"plate-s0": {"default=-": {"judge": {"cover1": 0.9}}}, "plate-s1": {"default=-": {"judge": {"cover1": 0.95}}}}
    assert abs(ps.spread(runs, "plate")["cover 1mm"]["range"] - 0.05) < 1e-12
    assert ps.in_spread("door-w2") and ps.in_spread("bracket-s1") and not ps.in_spread("bracket-60fps")


def _grid(n: int, step: float = 1.0, z: float = 0.0, hole: tuple[int, int] | None = None):
    xs = np.arange(n) * step
    X, Y = np.meshgrid(xs, xs)
    V = np.stack([X.ravel(), Y.ravel(), np.full(X.size, z)], axis=1)
    F = []
    for i in range(n - 1):
        for j in range(n - 1):
            if hole is not None and (i, j) == hole:
                continue
            a = i * n + j
            F += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    return V, np.asarray(F, dtype=np.int64)


def test_general_recipe_fills_a_pin_hole_and_drops_a_floater():
    V, F = _grid(30, hole=(10, 10))  # a 1 mm square hole, 4 mm round
    Vf, Ff = _grid(2, z=40.0)  # a 1 mm loose bit far away
    V2 = np.vstack([V, Vf])
    F2 = np.vstack([F, Ff + len(V)])
    V3, F3, S3, filled, n = ps.apply_ops(V2, F2, np.full(len(V2), 0.1), ps.RECIPES["general"])
    assert n == len(V) and len(F3) == len(F) + 2 and filled is not None and int(filled.sum()) == 2
    _, F4, _, filled4, _ = ps.apply_ops(V2, F2, None, ps.RECIPES["fill-0"])
    assert len(F4) == len(F) and filled4 is None


def test_judge_reads_a_thin_walls_thickness():
    judge = ps.Judge({"scene": "fins", "seed": 0})
    wall = next(p for p in judge.sc.primitives if p.name == "wall_0")  # the 0.5 mm wall
    t = 2.0 * float(wall.half[1])
    rng = np.random.default_rng(0)
    pl = np.column_stack([rng.uniform(-18, 18, 4000), np.repeat([t / 2, -t / 2], 2000), rng.uniform(-11, 11, 4000)])
    P = pl @ wall.R.T + wall.t
    d, obj = judge.dist(P)
    assert d.max() < 1e-9 and obj.all()
    fits = ps.scene_fits(judge, P, np.zeros((0, 3), np.int64), None, None, P, np.zeros(len(P)))
    assert abs(fits["t0_5_read"] - t) < 1e-6 and fits["t0_5_cover05"] == 1.0
    assert fits["t1_read"] is None  # nothing of the 1 mm wall in this "mesh"
    ends = np.array([[wall.t[0] + 19.9, 0.0, wall.t[2]], [wall.t[0], 0.0, wall.t[2]]])
    assert judge.edges(ends).tolist() == [True, False]
