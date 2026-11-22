"""``ohs.mesh.thin_parts``: thin walls, thin plates lying flat and small round holes in a finished mesh, and the
hint to make it again with "Thin sheet metal and small parts" (ENGINE_PLAN X5, step 1).

Every limit in mm is tried on both sides of it on a thin wall and a thin plate (the thin-parts rule): a wall
reads thin up to 2.7 mesh detail (2.6 mm yes, 2.8 mm no at 1 mm), a plate lying flat up to 2.7 too (0.5 and
2 mm yes, 4 mm no). A thick block and a smooth panel give nothing; a mesh made with the thin preset or at
0.5 mm is not looked at. The finding only reads: the arrays it is given come back unchanged.
"""
from __future__ import annotations

import math
import re

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs.mesh.thin_parts import (PLATE_MAX_VOXELS, ROUND_MAX_VOXELS, THIN_PRESET_ID, WALL_READS_VOXELS,  # noqa: E402
                                 find_thin_parts, hint_words)
from test_mesh_support import box_on_table, flat_table, raise_disc  # noqa: E402


# --------------------------------------------------------------------------- meshes
def _grid(o, u, w, nu, nv, step=0.5):
    """A flat grid from ``o`` along ``u`` (length nu) and ``w`` (length nv), its normal ``u x w``."""
    o, u, w = (np.asarray(x, dtype=np.float64) for x in (o, u, w))
    a = np.arange(0.0, nu + 1e-9, step)
    b = np.arange(0.0, nv + 1e-9, step)
    A, B = np.meshgrid(a, b, indexing="ij")
    V = o + A.reshape(-1, 1) * u + B.reshape(-1, 1) * w
    n, m = len(a), len(b)
    i, j = np.meshgrid(np.arange(n - 1), np.arange(m - 1), indexing="ij")
    v00 = (i * m + j).ravel()
    F = np.concatenate([np.stack([v00, v00 + m, v00 + m + 1], 1), np.stack([v00, v00 + m + 1, v00 + 1], 1)])
    return V, F


def _join(parts):
    Vs, Fs, k = [], [], 0
    for V, F in parts:
        Vs.append(V)
        Fs.append(F + k)
        k += len(V)
    return np.concatenate(Vs), np.concatenate(Fs)


def box(sx, sy, sz, step=0.5):
    """A closed box [0, sx] x [0, sy] x [0, sz], its normals outward."""
    X, Y, Z = np.eye(3)
    return _join([
        _grid((0, 0, 0), Y, X, sy, sx, step), _grid((0, 0, sz), X, Y, sx, sy, step),  # bottom (-z), top (+z)
        _grid((0, 0, 0), X, Z, sx, sz, step), _grid((0, sy, 0), Z, X, sz, sx, step),  # -y, +y
        _grid((0, 0, 0), Z, Y, sz, sy, step), _grid((sx, 0, 0), Y, Z, sy, sz, step),  # -x, +x
    ])


def bore(d=10.0, depth=8.0, n=72, step=0.5):
    """A blind bore of diameter ``d`` in a flat face (z = 0, its normal up): the face round it (a square ring), the
    bore's wall (normals towards the axis) and its floor."""
    r = d / 2
    ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
    zs = np.arange(0.0, depth + 1e-9, step)
    V = np.array([[r * math.cos(a), r * math.sin(a), -z] for z in zs for a in ang])
    F = []
    for k in range(len(zs) - 1):
        for i in range(n):
            a, b = k * n + i, k * n + (i + 1) % n
            c, e = a + n, b + n
            F += [[a, e, c], [a, b, e]]  # wound so that the normal points to the axis
    wall = (V, np.array(F))
    floor_v = np.vstack([[0, 0, -depth], V[-n:]])
    floor_f = np.array([[0, 1 + (i + 1) % n, 1 + i] for i in range(n)])
    floor_f = floor_f[:, [0, 2, 1]]  # normal up (+z)
    ring = [_grid((-20, -20, 0), (1, 0, 0), (0, 1, 0), 40, 20 - r - 1, step),
            _grid((-20, r + 1, 0), (1, 0, 0), (0, 1, 0), 40, 20 - r - 1, step)]
    return _join([wall, (floor_v, floor_f), *ring])


def _normals_up(V, F):
    from ohs.mesh.topology import face_normals
    n, _ = face_normals(V, F)
    return n


# --------------------------------------------------------------------------- thin walls
@pytest.mark.parametrize("t", [0.5, 1.0, 2.0, 2.6])
def test_a_thin_wall_is_found_with_its_thickness(t):
    V, F = box(40.0, t, 30.0)
    f = find_thin_parts(V, F, voxel_mm=1.0, opposite_sides=True)
    w = f["walls"]
    assert f["suggest"] and w["found"]
    assert w["reads_mm"] == pytest.approx(t, abs=0.05)
    assert w["area_mm2"] == pytest.approx(2 * 40 * 30, rel=0.1)  # both faces
    assert w["regions"] and w["regions"][0]["area_mm2"] >= 100
    lo, hi = w["estimate_mm"]  # measured: walls read 0.1 to 1.3 mm too thick at 1 mm
    assert lo == pytest.approx(max(t - 1.3, 0), abs=0.06) and hi == pytest.approx(t - 0.1, abs=0.06)
    assert f["bricks"]["n"] > 0 and f["bricks"]["size_mm"] == 8.0


def test_the_wall_limit_is_2_7_mesh_detail():
    assert WALL_READS_VOXELS == 2.7
    thick = find_thin_parts(*box(40.0, 2.8, 30.0), voxel_mm=1.0)
    assert not thick["walls"]["found"] and not thick["suggest"]
    # at mesh detail 2 mm the same 2.8 mm wall is thin (5.4 mm), and the estimate is not given (not measured there)
    coarse = find_thin_parts(*box(40.0, 2.8, 30.0, step=1.0), voxel_mm=2.0, opposite_sides=True)
    assert coarse["walls"]["found"] and coarse["walls"]["estimate_mm"] is None


def test_a_small_patch_of_thin_readings_is_not_a_wall():
    """Under 100 square mesh detail of surface (the parts without a thin wall had at most 50 mm² in all)."""
    f = find_thin_parts(*box(6.0, 1.0, 6.0), voxel_mm=1.0)  # 2 x 36 mm²
    assert not f["walls"]["found"] and f["walls"]["all_readings_mm2"] > 0


def test_a_thick_block_gives_nothing():
    V, F = box(40.0, 30.0, 20.0)
    f = find_thin_parts(V, F, voxel_mm=1.0)
    assert f["found"] is False and f["suggest"] is False and f["why_not"] == "nothing_found"
    assert not f["walls"]["found"] and not f["holes"]["found"] and not f["plates"]["found"]
    assert hint_words(f) is None


def test_a_smooth_panel_gives_nothing():
    """A gently curved sheet seen from one side (a car panel, radius 1,200 mm): no wall, no hole, no plate."""
    V, F = _grid((-100, -60, 0), (1, 0, 0), (0, 1, 0), 200, 120, 1.0)
    V = V.copy()
    V[:, 2] = -(V[:, 0] ** 2) / (2 * 1200.0)
    f = find_thin_parts(V, F, voxel_mm=1.0)
    assert f["suggest"] is False and f["why_not"] == "nothing_found"


# --------------------------------------------------------------------------- thin plates lying flat
@pytest.mark.parametrize("t", [0.5, 0.8])
def test_a_thin_plate_lying_flat_is_found_but_not_suggested(t):
    """Lying flat it reads right at every mesh detail: recorded, no hint (no measured gain)."""
    V, F = flat_table(half=80.0)
    V = raise_disc(V, [0, 0, 1], 0.0, np.zeros(3), 30.0, t)
    f = find_thin_parts(V, F, voxel_mm=1.0)
    assert f["found"] and f["plates"]["found"]
    assert f["suggest"] is False and f["why_not"] == "no_measured_gain" and hint_words(f) is None
    p = f["plates"]["plates"][0]
    assert p["height_mm"] == pytest.approx(t, abs=0.1)
    assert p["size_mm"] == pytest.approx(60.0 * math.sqrt(2), rel=0.25)
    assert not f["walls"]["found"]  # lying flat it is no wall seen from both sides


@pytest.mark.parametrize("H,thin", [(2.0, True), (4.0, False)])
def test_the_plate_limit_is_2_7_mesh_detail(H, thin):
    assert PLATE_MAX_VOXELS == 2.7
    V, F, _ = box_on_table(hx=30.0, hy=20.0, H=H, rf=0.5, half=80.0)
    f = find_thin_parts(V, F, voxel_mm=1.0)
    assert f["plates"]["found"] is thin
    if thin:
        assert f["plates"]["plates"][0]["height_mm"] == pytest.approx(H, abs=0.15)


def test_a_bare_table_is_not_a_plate():
    V, F = flat_table(half=80.0)
    f = find_thin_parts(V, F, voxel_mm=1.0)
    assert not f["plates"]["found"] and not f["suggest"]


# --------------------------------------------------------------------------- small round holes
def test_a_small_bore_alone_is_found_but_not_suggested():
    V, F = bore(10.0)
    f = find_thin_parts(V, F, voxel_mm=1.0)
    h = f["holes"]
    assert h["found"] and h["count"] == 1 and h["smallest_mm"] == pytest.approx(10.0, abs=1.0)
    assert f["found"] and f["suggest"] is False and f["why_not"] == "no_measured_gain" and hint_words(f) is None
    assert f["bricks"]["n"] > 0  # recorded for step 2 all the same


def test_walls_and_a_bore_give_the_hint_with_both():
    """The walls are the reason; the bore is said after them, with its honest line."""
    wall = box(40.0, 1.0, 30.0)
    Vb, Fb = bore(10.0)
    V, F = _join([wall, (Vb + np.array([0.0, 100.0, 0.0]), Fb)])
    f = find_thin_parts(V, F, voxel_mm=1.0, opposite_sides=True)
    assert f["walls"]["found"] and f["holes"]["found"] and f["suggest"] and f["why_not"] is None
    w = hint_words(f)
    assert w["title"] == "Thin walls found"
    assert w["found"][0].startswith("Thin walls:") and "Ø 10 mm" in w["found"][1]
    assert w["gain"][0].startswith("Walls seen from both sides") and "not measured to bring small holes nearer" in w["gain"][1]


def test_a_large_hole_and_a_slot_are_not_small_round_holes():
    assert ROUND_MAX_VOXELS == 16.0
    assert not find_thin_parts(*bore(25.0), voxel_mm=1.0)["holes"]["found"]  # Ø 25: left out
    # a slot (two faces 8 mm apart facing each other, a seam's gap): no circle round one axis
    a = _grid((0, 0, 0), (0, 0, 1), (1, 0, 0), 10, 60)  # at y = 0, facing +y
    b = _grid((0, 8, 0), (1, 0, 0), (0, 0, 1), 60, 10)  # at y = 8, facing -y
    V, F = _join([b, a])
    n = _normals_up(V, F)
    assert n[: len(b[1])][:, 1].mean() < -0.9 and n[len(b[1]):][:, 1].mean() > 0.9  # the faces face each other
    assert not find_thin_parts(V, F, voxel_mm=1.0)["holes"]["found"]


# --------------------------------------------------------------------------- made fine already, reads only
@pytest.mark.parametrize("kw", [{"preset_id": THIN_PRESET_ID, "voxel_mm": 1.0}, {"voxel_mm": 0.5}, {"voxel_mm": 0.4}])
def test_nothing_is_suggested_for_a_mesh_made_fine(kw):
    V, F = box(40.0, 1.0, 30.0)
    f = find_thin_parts(V, F, **kw)
    assert f["suggest"] is False and f["why_not"] == "made_fine" and f["walls"] is None
    assert hint_words(f) is None


def test_the_finding_only_reads():
    V, F = box(40.0, 1.0, 30.0)
    S = np.full(len(V), 0.1)
    V0, F0, S0 = V.copy(), F.copy(), S.copy()
    find_thin_parts(V, F, voxel_mm=1.0, sigma=S)
    assert np.array_equal(V, V0) and np.array_equal(F, F0) and np.array_equal(S, S0)


def test_made_up_vertices_are_not_measured():
    """The fill of Fill holes (confidence 50 mm) is no wall."""
    V, F = box(40.0, 1.0, 30.0)
    f = find_thin_parts(V, F, voxel_mm=1.0, sigma=np.full(len(V), 50.0))
    assert not f["walls"]["found"]


def test_bad_mesh_detail_is_refused():
    with pytest.raises(ValueError):
        find_thin_parts(*box(4.0, 1.0, 4.0), voxel_mm=0.0)


# --------------------------------------------------------------------------- the words
def test_the_hint_says_what_was_found_what_it_changes_and_what_it_costs():
    V, F = box(40.0, 1.0, 30.0)
    f = find_thin_parts(V, F, voxel_mm=1.0, opposite_sides=True)
    w = hint_words(f, seconds=190.0, triangles=1_234_567, graphics_card=True)
    assert w["title"] == "Thin walls found" and w["action"] == "Make the mesh again with this preset"
    assert w["preset_id"] == THIN_PRESET_ID and "0.5 mm instead of 1 mm" in w["intro"]
    text = " ".join([w["intro"], *w["found"], *w["gain"], *w["cost"], w["measured"]])
    assert re.search(r"\b2,\d{3} mm² of the surface", text)  # thousands separated
    assert "probably up to 0.9 mm in truth" in text
    assert "0.23 to 0.67 mm nearer" in text and "1.2 to 2.4 times" in text
    assert "3.2 min" in text and "3.8 min to 7.6 min" in text  # this mesh's time and the preset's
    assert "4.9 million instead of 1.23 million" in text and "7 to 11.9 GB of memory" in text and "simulated" in text
    for word in ("GPU", "CPU", "voxel", "sigma", "uncertain"):
        assert word not in text
    nocard = hint_words(f, graphics_card=False)["measured"]
    assert "without a graphics card" in nocard


def test_the_engines_words_pass_the_guards():
    from pathlib import Path

    import test_server_error_words as g
    assert "mesh/thin_parts.py" in g.ENGINE_MODULES
    src = (Path(g.ENGINE) / "mesh" / "thin_parts.py").read_text(encoding="utf-8")
    assert not g.engine_retired_names(src, "mesh/thin_parts.py")
