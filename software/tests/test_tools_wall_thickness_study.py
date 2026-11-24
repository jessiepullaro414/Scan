"""``tools/wall_thickness_study.py`` (ENGINE_PLAN E-D3 item 2, the study's experiment 2): a wall's thickness read
from raw frame points, on synthetic points of two known faces and a synthetic mesh (no rendering, no scan)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from tools import wall_thickness_study as ws  # noqa: E402

L, H = 40.0, 25.0  # the wall's length (x) and height (z), centred at the origin; faces at y = +-t/2


def _frames(t: float, *, n_frames: int = 8, per: int = 600, sd: float = 0.4, seed: int = 0, sides=("+", "-"),
            frame_shift: float = 0.0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Frames that each see one face of the wall (alternating), points with noise across it; ``frame_shift``: each
    frame's points moved by a random amount of that size across the wall (a frame's own pose scatter)."""
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n_frames):
        side = sides[k % len(sides)]
        sgn = 1.0 if side == "+" else -1.0
        P = np.column_stack([rng.uniform(-L / 2, L / 2, per), np.full(per, sgn * t / 2), rng.uniform(-H / 2, H / 2, per)])
        P[:, 1] += rng.normal(0.0, sd, per) + rng.normal(0.0, frame_shift)
        N = np.tile([0.0, sgn, 0.0], (per, 1)) + rng.normal(0.0, 0.05, (per, 3))
        out.append((P, N / np.linalg.norm(N, axis=1, keepdims=True)))
    return out


@pytest.mark.parametrize("t", [0.5, 1.0, 2.0])
def test_two_known_faces_read_their_distance_and_twice_the_pm_covers_it(t):
    A, B = ws.select_faces(_frames(t), np.zeros(3), np.array([0.0, 1.0, 0.0]), half_mm=1.5)
    r = ws.read_faces(A, B, boot=100)
    assert not r["refused"]
    assert abs(r["mm"] - t) < 0.03
    assert 0.0 < r["pm_mm"] < 0.05 and abs(r["mm"] - t) <= 2.0 * r["pm_mm"] + 0.01
    assert r["frames"] == [4, 4] and r["tilt_deg"] < 1.0 and not r["tilted"]


def test_the_pm_grows_with_each_frames_own_scatter():
    still = ws.read_faces(*ws.select_faces(_frames(1.0), np.zeros(3), np.array([0.0, 1.0, 0.0]), half_mm=1.5), boot=100)
    shaky = ws.read_faces(*ws.select_faces(_frames(1.0, frame_shift=0.2), np.zeros(3), np.array([0.0, 1.0, 0.0]),
                                           half_mm=1.5), boot=100)
    assert shaky["pm_mm"] > 2.0 * still["pm_mm"]


def test_one_side_not_seen_is_refused():
    A, B = ws.select_faces(_frames(1.0, sides=("+",)), np.zeros(3), np.array([0.0, 1.0, 0.0]), half_mm=1.5)
    r = ws.read_faces(A, B)
    assert r["refused"] and "face B" in r["why"] and "one side not seen" in r["why"]


def test_a_wall_thinner_than_the_scatter_is_not_thinned_and_says_it_cannot_tell():
    """The faces are told apart by their normals, not by a cut at the middle: a 0.05 mm wall under 0.4 mm of
    noise is not read thinner than its truth beyond twice its +- (six noise draws); a sheet of no thickness says
    it cannot tell how thin."""
    axis = np.array([0.0, 1.0, 0.0])
    for seed in range(6):
        r = ws.read_faces(*ws.select_faces(_frames(0.05, n_frames=6, per=400, seed=seed), np.zeros(3), axis,
                                           half_mm=1.5), boot=100)
        assert not r["refused"]
        assert r["mm"] - 0.05 >= -2.0 * r["pm_mm"]
    flat = ws.read_faces(*ws.select_faces(_frames(0.0, n_frames=6, per=400, seed=3), np.zeros(3), axis, half_mm=1.5),
                         boot=100)
    assert flat["cannot_tell_how_thin"]


def _grid(origin, a, b, na: int, nb: int):
    """A face from ``origin`` spanned by ``a`` (na cells) and ``b`` (nb cells); outward normal a x b."""
    i, j = np.meshgrid(np.arange(na + 1), np.arange(nb + 1), indexing="ij")
    V = origin + i.reshape(-1, 1) / na * a + j.reshape(-1, 1) / nb * b
    idx = (i * (nb + 1) + j)
    F = []
    for p in range(na):
        for q in range(nb):
            v00, v10, v11, v01 = idx[p, q], idx[p + 1, q], idx[p + 1, q + 1], idx[p, q + 1]
            F += [[v00, v10, v11], [v00, v11, v01]]
    return V, np.array(F)


def _box_mesh(t_read: float):
    """A closed box mesh 40 x t_read x 25 (a wall as a 1 mm mesh reads it), faces at about 1 mm."""
    hx, hy, hz = L / 2, t_read / 2, H / 2
    ex, ey, ez = np.array([2 * hx, 0, 0]), np.array([0, 2 * hy, 0]), np.array([0, 0, 2 * hz])
    faces = [(np.array([-hx, hy, -hz]), ez, ex), (np.array([-hx, -hy, -hz]), ex, ez),  # +y, -y
             (np.array([-hx, -hy, hz]), ex, ey), (np.array([-hx, -hy, -hz]), ey, ex),  # +z, -z
             (np.array([hx, -hy, -hz]), ey, ez), (np.array([-hx, -hy, -hz]), ez, ey)]  # +x, -x
    Vs, Fs, off = [], [], 0
    for o, a, b in faces:
        V, F = _grid(o, a, b, max(1, int(round(np.linalg.norm(a)))), max(1, int(round(np.linalg.norm(b)))))
        Vs.append(V)
        Fs.append(F + off)
        off += len(V)
    return np.concatenate(Vs), np.concatenate(Fs)


def test_the_wall_at_a_click_comes_from_the_mesh_and_the_faces_from_the_frames():
    V, F = _box_mesh(1.8)  # the mesh reads 1.8 mm; the frames' faces are 0.5 mm apart
    w = ws.mesh_wall(V, F, np.array([0.0, 0.9, 0.0]))
    assert not w["refused"] and abs(w["mesh_mm"] - 1.8) < 0.05 and abs(abs(w["axis"][1]) - 1.0) < 1e-6
    r = ws.read_wall(_frames(0.5), V, F, np.array([0.0, 0.9, 0.0]), boot=50)
    assert not r["refused"] and abs(r["mm"] - 0.5) < 0.05 and abs(r["mesh_at_click_mm"] - 1.8) < 0.05


def test_a_click_away_from_any_thin_wall_is_refused():
    V, F = _box_mesh(1.8)
    r = ws.read_wall(_frames(0.5), V, F, np.array([0.0, 30.0, 0.0]))
    assert r["refused"] and "no wall seen from both sides" in r["why"]
    V2, F2 = _box_mesh(6.0)  # 6 mm: no thin wall in the mesh at all
    r2 = ws.read_wall(_frames(6.0), V2, F2, np.array([0.0, 3.0, 0.0]))
    assert r2["refused"] and "no wall seen from both sides" in r2["why"]


def test_frame_points_back_project_a_plane_with_normals_facing_the_camera():
    K = np.array([[700.0, 0, 64.0], [0, 700.0, 40.0], [0, 0, 1.0]])
    D = np.full((80, 128), 250.0)
    T = np.eye(4)
    T[:3, 3] = [1.0, 2.0, 3.0]
    P, N = ws.frame_points(D, K, T, step=4)
    assert len(P) > 0 and np.allclose(P[:, 2], 253.0) and np.allclose(N, [0.0, 0.0, -1.0])


def test_rigid_fit_finds_a_known_motion():
    rng = np.random.default_rng(1)
    A = rng.normal(size=(20, 3)) * 100
    c, s = np.cos(0.3), np.sin(0.3)
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    T = ws.rigid_fit(A, A @ R.T + [5.0, -2.0, 1.0])
    assert np.allclose(T[:3, :3], R) and np.allclose(T[:3, 3], [5.0, -2.0, 1.0])


def test_experiment_1s_scenes_add_a_ball_or_tags_and_keep_the_walls():
    from tools.preset_study import fins_scene

    base = fins_scene(0)
    ball = ws.fins_ball_scene(0)
    assert len(ball.primitives) == len(base.primitives) + 1 and ball.primitives[-1].name == "tie_ball_front"
    assert np.allclose(ball.primitives[-1].center, [95.0, 0.0, 20.0]) and ball.meta["tie"]["kind"] == "ball"
    rev = ws.variant_scene("reversed", 0)
    assert rev.meta["trajectory"]["sweep_deg_s"] == -base.meta["trajectory"]["sweep_deg_s"]
    two = ws.variant_scene("balls2", 0)
    assert len(two.primitives) == len(base.primitives) + 2
    tags = ws.fins_tags_scene(0)
    assert len(tags.primitives) == len(base.primitives) and sorted(tags.markers) == list(range(len(ws.TAG_AT)))
    for k, (x, y) in enumerate(ws.TAG_AT):
        X = tags.markers[k]
        assert X.shape == (4, 3) and np.allclose(X[:, 2], 0.0) and np.allclose(X[:, :2].mean(axis=0), [x, y], atol=1e-6)


def test_the_tools_own_wall_test_reads_a_wall_the_finding_does_not_flag():
    """A 3 mm wall reads about 3.5 mm in a 1 mm mesh, over the thin-parts finding's 2.7 limit: the finding refuses it,
    the tool's own ray (4 mm) reads it; a 6 mm block is refused by both."""
    V, F = _box_mesh(3.5)
    click = np.array([0.0, 1.75, 0.0])
    assert ws.read_wall(_frames(3.0), V, F, click, wall_test="finder")["refused"]
    r = ws.read_wall(_frames(3.0), V, F, click, boot=50)
    assert not r["refused"] and abs(r["mm"] - 3.0) < 0.05 and abs(r["mesh_at_click_mm"] - 3.5) < 0.05
    V6, F6 = _box_mesh(6.0)
    r6 = ws.read_wall(_frames(6.0), V6, F6, np.array([0.0, 3.0, 0.0]))
    assert r6["refused"] and "no wall seen from both sides" in r6["why"]


def test_a_click_between_the_findings_flagged_vertices_is_read():
    """The finding flags every n-th vertex only; the tool's own test starts at the vertex nearest the click, so a
    click a few mm from any flagged vertex is still read."""
    V, F = _box_mesh(1.8)
    walls = ({"found": True}, np.array([0]))  # the finding flagged one corner vertex only
    click = np.array([0.0, 0.9, 0.0])
    assert ws.read_wall(_frames(0.5), V, F, click, walls=walls, wall_test="finder")["refused"]
    r = ws.read_wall(_frames(0.5), V, F, click, walls=walls, boot=50)
    assert not r["refused"] and abs(r["mm"] - 0.5) < 0.05


def test_a_plate_lying_on_a_table_is_no_wall():
    """Under a plate's top the next surface (the table) faces the same way: not a wall seen from both sides."""
    Vt, Ft = _grid(np.array([-30.0, -30.0, 0.0]), np.array([60.0, 0, 0]), np.array([0, 60.0, 0]), 60, 60)  # up
    Vp, Fp = _grid(np.array([-20.0, -20.0, 0.5]), np.array([40.0, 0, 0]), np.array([0, 40.0, 0]), 40, 40)
    V, F = np.concatenate([Vt, Vp]), np.concatenate([Ft, Fp + len(Vt)])
    r = ws.read_wall(_frames(0.5), V, F, np.array([0.0, 0.0, 0.5]))
    assert r["refused"] and "no wall seen from both sides" in r["why"]


def test_faces_scattering_too_much_are_refused_not_read():
    """A face whose points scatter more than MAX_FACE_SD_MM (the scorecard's faces: 0.29 to 0.60 mm) is refused: the
    reading cannot tell, rather than read a thin wall thinner."""
    V, F = _box_mesh(1.8)
    r = ws.read_wall(_frames(0.5, sd=1.2, per=2000), V, F, np.array([0.0, 0.9, 0.0]), boot=20)
    assert r["refused"] and "scatter" in r["why"] and "cannot tell" in r["why"]


def test_the_walls_axis_comes_from_the_raw_faces_when_the_mesh_is_tilted():
    """The mesh's wall leans 3 degrees against the frames' faces: the axis is taken again from the faces' own planes."""
    V, F = _box_mesh(1.8)
    a = np.radians(3.0)
    R = np.array([[1.0, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    r = ws.read_wall(_frames(0.5), V @ R.T, F, R @ np.array([0.0, 0.9, 0.0]), boot=50)
    assert not r["refused"] and abs(r["mm"] - 0.5) < 0.03 and r["tilt_deg"] < 1.0


def test_the_sheet_with_tags_keeps_the_sheet_and_puts_the_tags_on_its_table():
    from tools.scorecard import sheet_scene

    base, sc = sheet_scene(0), ws.variant_scene("sheet-tags", 0)
    assert len(sc.primitives) == len(base.primitives) and sorted(sc.markers) == list(range(len(ws.TAG_AT)))
    for k, (x, y) in enumerate(ws.TAG_AT):
        assert np.allclose(sc.markers[k][:, 2], 0.0) and np.allclose(sc.markers[k][:, :2].mean(axis=0), [x, y], atol=1e-6)
