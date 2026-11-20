"""Hints while scanning (FEATURE_PLAN N3, an experiment; ``ohs/server/scan_hints.py``): the weak cells,
the one target, its hint, the filter, the thin plate, and that the session's results do not move.

The feature is an option of :class:`ohs.server.session.ScanSession` (``guide=True``), off by
default; measured in ``VALIDATION.md``, "Hints while scanning".
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from ohs.server.scan_hints import (FEW_FRAMES, LIT, MIN_CELLS, GuideHintFilter, ScanGuide, Target, ViewMoments,
                                   hint_for, key_centres)
from ohs.server.meshing import cell_keys
from ohs.sim.trajectory import look_at


def _plate(x0=-40.0, x1=40.0, y0=-30.0, y1=30.0, z=0.0, step=1.0):
    """A flat grid of vertices facing up (normals +z) and its faces."""
    xs = np.arange(x0, x1 + 1e-9, step)
    ys = np.arange(y0, y1 + 1e-9, step)
    X, Y = np.meshgrid(xs, ys)
    V = np.stack([X.ravel(), Y.ravel(), np.full(X.size, z)], axis=1)
    N = np.tile([0.0, 0.0, 1.0], (len(V), 1))
    nx = len(xs)
    i = np.arange(len(ys) - 1)[:, None] * nx + np.arange(nx - 1)[None]
    i = i.ravel()
    F = np.concatenate([np.stack([i, i + 1, i + nx], 1), np.stack([i + 1, i + nx + 1, i + nx], 1)])
    return V, N, F


def _cam(direction_deg_from_normal: float, az_deg: float = 0.0, dist: float = 250.0, target=(0.0, 0.0, 0.0)):
    a, b = math.radians(direction_deg_from_normal), math.radians(az_deg)
    t = np.asarray(target, float)
    eye = t + dist * np.array([math.sin(a) * math.cos(b), math.sin(a) * math.sin(b), math.cos(a)])
    return look_at(eye, t, up=(0.0, 1.0, 0.0) if direction_deg_from_normal < 1 else (0.0, 0.0, 1.0))


def _frames(V, cams, cell=8.0):
    keys = np.unique(cell_keys(V, cell))
    return [(T, keys) for T in cams]


def test_moments_give_the_cosine_of_the_looks():
    keys = np.unique(cell_keys(np.array([[0.5, 0.5, 0.5]]), 8.0))
    c = key_centres(keys, 8.0)[0]
    m = ViewMoments(8.0)
    m.add_frames([(_cam(0.0, target=c), keys)] * 3)
    assert m.n.tolist() == [3.0]
    assert m.rms_cos(np.array([0]), np.array([[0.0, 0.0, 1.0]]))[0] == pytest.approx(1.0, abs=1e-6)
    m.add_frames([(_cam(70.0, target=c), keys)])
    want = math.sqrt((3 * 1.0 + math.cos(math.radians(70.0)) ** 2) / 4)
    assert m.rms_cos(np.array([0]), np.array([[0.0, 0.0, 1.0]]))[0] == pytest.approx(want, abs=1e-6)
    assert m.dmin[0] == pytest.approx(250.0, abs=1e-6)


def test_a_surface_seen_only_steeply_is_the_target_and_square_looks_clear_it():
    V, N, _ = _plate()
    g = ScanGuide()
    steep = [_cam(72.0, az) for az in (-10.0, 0.0, 10.0)] * 4  # 12 frames, all 72 degrees off the normal
    t = g.update(V, N, _frames(V, steep), steep[-1])
    assert t is not None and t.reason == "steep"
    assert g.lit(V).mean() > 0.9  # the whole plate is lit
    assert t.normal @ np.array([0.0, 0.0, 1.0]) > 0.99
    square = [_cam(10.0, az) for az in range(0, 360, 30)]
    assert g.update(V, N, _frames(V, square), square[-1]) is None  # rms cos now above the steep limit
    assert not g.lit(V).any()


def test_a_surface_seen_by_few_frames_is_seen_too_little():
    V, N, _ = _plate()
    g = ScanGuide()
    few = [_cam(5.0)] * (FEW_FRAMES - 1)
    t = g.update(V, N, _frames(V, few), few[-1])
    assert t is not None and t.reason == "few"
    assert g.update(V, N, _frames(V, [_cam(5.0)]), _cam(5.0)) is None  # one more frame: enough


def test_the_target_is_the_patch_near_the_view_and_does_not_jump():
    Va, Na, _ = _plate(-40, -10, -10, 10)
    Vb, Nb, _ = _plate(60, 100, -10, 10)  # a larger patch, 100 mm away
    V, N = np.concatenate([Va, Vb]), np.concatenate([Na, Nb])
    g = ScanGuide()
    look_a = _cam(5.0, target=(-25.0, 0.0, 0.0))
    t = g.update(V, N, _frames(V, [look_a]), look_a)
    assert t is not None and t.centre[0] < 0  # near where the scanner points, though the other is larger
    look_mid = _cam(5.0, target=(30.0, 0.0, 0.0))  # now nearer b, but a still scores more than half
    t2 = g.update(V, N, [], look_mid)
    assert t2.centre[0] < 0  # hysteresis: kept
    look_b = _cam(5.0, target=(80.0, 0.0, 0.0))
    assert g.update(V, N, [], look_b).centre[0] > 50  # far from a: it changes


def test_one_cell_is_not_a_patch():
    V = np.array([[1.0, 1.0, 0.0], [2.0, 1.0, 0.0], [1.0, 2.0, 0.0]])
    N = np.tile([0.0, 0.0, 1.0], (3, 1))
    assert MIN_CELLS >= 2
    assert ScanGuide().update(V, N, _frames(V, [_cam(5.0)]), _cam(5.0)) is None


def _target(normal=(0.0, 0.0, 1.0), centre=(0.0, 0.0, 0.0), reason="steep"):
    return Target(np.zeros(1, np.int64), np.asarray(centre, float), np.asarray(normal, float), reason, 1.0)


@pytest.mark.parametrize("az", [0.0, 90.0, 180.0, 270.0])
def test_round_hints_point_the_camera_towards_the_normal(az):
    # the camera 60 degrees off the normal on one side: the hint is to come round towards the top
    T = _cam(60.0, az)
    h = hint_for(_target(), T)
    # moving the camera the way the hint says brings it closer to the normal
    R, c = T[:3, :3], T[:3, 3]
    step = {"round_left": -R[:, 0], "round_right": R[:, 0], "higher": -R[:, 1], "lower": R[:, 1]}[h["code"]]
    c2 = c + 20.0 * step
    before = (c / np.linalg.norm(c))[2]
    after = (c2 / np.linalg.norm(c2))[2]
    assert after > before
    assert h["text"].startswith("Lit area seen only at a slant: ")


def test_round_hints_left_and_right():
    t = _target(normal=(1.0, 0.0, 0.0))  # a wall facing +x, the camera looking at it from -y
    T = look_at([0.0, -250.0, 0.0], [0.0, 0.0, 0.0], up=(0.0, 0.0, 1.0))
    h = hint_for(t, T)
    # +x is to the camera's left when it looks along +y with z up
    assert h["code"] == ("round_left" if T[:3, 0] @ np.array([1.0, 0, 0]) < 0 else "round_right")


def test_turn_closer_and_hold():
    T = look_at([0.0, 0.0, 250.0], [0.0, 0.0, 0.0], up=(0.0, 1.0, 0.0))
    left = T[:3, 3] + 250.0 * T[:3, 2] - 150.0 * T[:3, 0]
    assert hint_for(_target(centre=left), T)["code"] == "turn_left"
    far = look_at([0.0, 0.0, 420.0], [0.0, 0.0, 0.0], up=(0.0, 1.0, 0.0))
    assert hint_for(_target(), far)["code"] == "closer"
    h = hint_for(_target(reason="few"), T)
    assert h["code"] == "hold" and h["text"] == "Lit area seen too little: hold the scanner on it"
    for code in ("turn_left", "closer", "hold", "round_left", "higher"):
        from ohs.server.scan_hints import HINTS

        assert "—" not in HINTS[code] and HINTS[code] == HINTS[code].lower()


def test_the_filter_needs_four_of_six_and_yields_to_the_tracking_hint():
    f = GuideHintFilter(window=6, need=4)
    ok = {"state": "ok", "reason": None}
    weak = {"state": "weak", "reason": "Flat surface: the scanner may slide"}
    a = {"code": "round_left", "reason": "steep", "text": "a"}
    b = {"code": "higher", "reason": "steep", "text": "b"}
    shown = [f.apply(x, ok) for x in [a, b, a, a, a, b, a, None, None, None, None]]
    codes = [s["code"] if s else None for s in shown]
    assert codes == [None, None, None, None, "round_left", "round_left", "round_left", "round_left", None, None, None]
    f2 = GuideHintFilter()
    for _ in range(5):
        f2.apply(a, ok)
    assert f2.apply(a, ok)["code"] == "round_left"
    assert f2.apply(a, weak) is None  # the slide hint shows instead: one hint at a time
    assert f2.apply(a, ok)["code"] == "round_left"


def test_a_thin_plate_on_its_table_is_lit_and_never_left_out():
    # a 0.5 mm plate lying on a table: its cells hold the table round it as well (8 mm cells); seen
    # only steeply, the plate's surface is lit with them, nothing of it is removed or called the table
    Vt, Nt, _ = _plate(-120, 120, -120, 120, z=0.0, step=2.0)
    Vp, Np, _ = _plate(-30, 30, -20, 20, z=0.5, step=1.0)
    V, N = np.concatenate([Vt, Vp]), np.concatenate([Nt, Np])
    g = ScanGuide()
    square = [_cam(5.0, az, target=(100.0, 100.0, 0.0)) for az in range(0, 360, 45)] * 2  # the table off there is fine
    steep = [_cam(72.0, az) for az in (-10.0, 0.0, 10.0)] * 4
    keys_t = np.unique(cell_keys(Vt[(np.abs(Vt[:, 0] - 100) < 40) & (np.abs(Vt[:, 1] - 100) < 40)], 8.0))
    plate_keys = np.unique(cell_keys(Vp, 8.0))
    t = g.update(V, N, [(T, keys_t) for T in square] + [(T, plate_keys) for T in steep], steep[-1])
    assert t is not None
    lit = g.lit(V)
    assert lit[len(Vt):].all()  # every vertex of the plate is lit
    assert len(V) == len(Vt) + len(Vp)  # nothing is removed (the guide only reads)


# --------------------------------------------------------------------------- the session
class _Pipe:
    K = np.array([[300.0, 0.0, 160.0], [0.0, 300.0, 100.0], [0.0, 0.0, 1.0]])
    gpu_fallbacks: dict = {}

    def __init__(self, V, F, N):
        self._m = (V, F, N)

    def mesh(self):
        return self._m


class _Sync:
    def __init__(self, fn):
        self.fn = fn

    def submit(self, item):
        self.fn(item)

    def close(self, timeout=None):
        pass


class _Hub:
    def __init__(self):
        self.binary: list = []
        self.json: list = []

    def publish_json(self, msg, key=None):
        self.json.append(msg)

    def publish_binary(self, key, data):
        self.binary.append((key, data))

    def wants(self, kind):
        return True

    def preview_hz(self):
        return 0.0


def _session(tmp_path, guide):
    from ohs.app.config import Config
    from ohs.server.session import ScanSession
    from ohs.sim.nominal import nominal_calibration

    hub = _Hub()
    s = ScanSession(calib=nominal_calibration((640, 400)), mode="markerless", rec_dir=tmp_path / f"rec{guide}",
                    project_id="p", scan_id="s", cfg=Config(), hub=hub, use_imu=False, use_lasers=False, guide=guide)
    s._mesh_worker = _Sync(s._encode_mesh)
    return s, hub


def _drive(s, pipe, n=12):
    from ohs.types import TrackResult

    depth = np.full((200, 320), 250.0, np.float32)
    for i in range(n):
        T = _cam(72.0, 5.0 * i)
        res = SimpleNamespace(track=TrackResult(T, True, {"fitness": 0.8, "rmse_mm": 0.3}), depth=depth, t=100.0 + 0.1 * i,
                              laser_points={}, timings_ms={"total": 10.0}, left_r=None, lines=None)
        s._after(pipe, res, 0.0)
        if i % 4 == 3:
            s._maybe_mesh(pipe, force=True)


def _mesh(data):
    from ohs.server.protocol import unpack_binary

    h, mv, _ = unpack_binary(data)
    n, t = h["vertices"], h["triangles"]
    b = bytes(mv)
    V = np.frombuffer(b, "<f4", 3 * n, 0).reshape(-1, 3)
    N = np.frombuffer(b, "<f4", 3 * n, 12 * n).reshape(-1, 3)
    C = np.frombuffer(b, np.uint8, 4 * n, 24 * n).reshape(-1, 4)
    F = np.frombuffer(b, "<u4", 3 * t, 28 * n).reshape(-1, 3)
    return h, (V, N, C, F)


def test_the_session_with_and_without_the_guide(tmp_path):
    V, N, F = _plate()
    off, hub_off = _session(tmp_path, False)
    on, hub_on = _session(tmp_path, True)
    _drive(off, _Pipe(V, F, N))
    _drive(on, _Pipe(V, F, N))
    assert "guide" not in off.live_stats()  # off: nothing new in the live stats
    g = on.live_stats()["guide"]
    assert g is not None and g["text"].startswith("Lit area ")
    # the same poses, coverage and kept frames either way: the guide only reads
    assert [m for m in hub_off.json] == [m for m in hub_on.json]
    assert off._coverage.snapshot()[0].tolist() == on._coverage.snapshot()[0].tolist()
    assert [e.t for e in off._ring] == [e.t for e in on._ring]
    h_off, (Vo, No, Co, Fo) = _mesh(hub_off.binary[-1][1])
    h_on, (Vn, Nn, Cn, Fn) = _mesh(hub_on.binary[-1][1])
    assert np.array_equal(Vo, Vn) and np.array_equal(Fo, Fn) and np.array_equal(No, Nn)
    lit = (Cn == LIT).all(axis=1)
    assert lit.any() and not (Co == LIT).all(axis=1).any()
    assert np.array_equal(Co[~lit], Cn[~lit])  # only the lit patch changes colour
