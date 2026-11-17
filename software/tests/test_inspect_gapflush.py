"""ohs.inspect.gapflush: gap and flush along a seam between two panels (FEATURE_PLAN I4).

Made-up meshes with a known truth, built here so that nothing of the module under test makes them:
two plates side by side, each a top surface that rolls over a rounded edge (a hemmed edge: a
radius about the sheet's thickness) and on down, open underneath as a scan sees it from above.
Known gap (between the edges' outermost points) and flush (the second plate's surface above the
first's), constant or tapering along the seam, with noise at the scan's level, with one side's
edge missing or filled, and with a stretch where the "scan" bridges the gap. Then the simulator's
seam scene (``ohs.sim.scene.panel_seam``): its own surface, at its nominal parameters, reads its
stated gap and flush. No simulated recording: the numbers from simulated scans are in the study
(``tools/gapflush_study.py``).
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from ohs import geometry as G
from ohs.inspect.gapflush import DEFAULT_RULE, EDGE_RULES, GapFlush, measure_gap_flush

R_EDGE = 1.0  # mm, the hem's outer radius
DROP_INSET = R_EDGE - math.sqrt(R_EDGE**2 - (R_EDGE - 0.5) ** 2)  # where a round edge has dropped 0.5 mm: this far inside its outermost point


# --------------------------------------------------------------------------- made-up plates
def _profile(gap: float, flush: float, side: int, width: float, step: float, arc_to_deg: float) -> np.ndarray:
    """One plate's cross-section (y, z) at one place along the seam, from its inner end to the edge
    and round it: the top at ``z = flush`` up to the radius, then the round edge (radius R_EDGE,
    its outermost point ``gap / 2`` from the seam's middle), on down to ``arc_to_deg``. ``side``
    -1 is the first plate (y < 0), +1 the second."""
    yc = side * (gap / 2 + R_EDGE)
    zc = flush - R_EDGE
    d = np.arange(width, 0.0, -step)
    top = np.column_stack([yc + side * d, np.full(len(d), flush)])  # inner end first
    th = np.radians(np.arange(90.0, arc_to_deg - 1e-9, -7.5))  # 7.5° steps: 0° (the outermost point) is one of them
    arc = np.column_stack([yc - side * R_EDGE * np.cos(th), zc + R_EDGE * np.sin(th)])
    return np.vstack([top, arc])


def _grid(rows: list[np.ndarray], xs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vertices and faces of a sheet through one profile per x (all of one length)."""
    k = len(rows[0])
    V = np.vstack([np.column_stack([np.full(k, x), r]) for x, r in zip(xs, rows)])
    F = []
    for j in range(len(xs) - 1):
        a, b = j * k, (j + 1) * k
        for i in range(k - 1):
            F.append((a + i, b + i, a + i + 1))
            F.append((a + i + 1, b + i, b + i + 1))
    F = np.asarray(F, dtype=np.int64)
    # wound so that the top faces up (+z): a scan's faces face where it was seen from
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    if np.median(n[:, 2]) < 0:
        F = F[:, ::-1].copy()
    return V, F


def _plates(gap=(4.0, 4.0), flush=(1.0, 1.0), length=120.0, width=25.0, step=0.5, dx=1.0, arc_to_deg=-60.0,
            x0=None, x1=None):
    """Two plates along x from ``x0`` to ``x1`` (default: ``±length / 2``), the gap and flush
    changing linearly from their first value at ``-length / 2`` to the second at ``+length / 2``.
    Returns ``(V, F, plate per face)``, plate 0 the first (y < 0) and 1 the second."""
    x0 = -length / 2 if x0 is None else x0
    x1 = length / 2 if x1 is None else x1
    xs = np.arange(x0, x1 + 1e-9, dx)

    def lin(v, x):
        return v[0] + (v[1] - v[0]) * (x + length / 2) / length

    Vs, Fs, plate, n = [], [], [], 0
    for side, p in ((-1, 0), (1, 1)):
        rows = [_profile(lin(gap, x), lin(flush, x) if side > 0 else 0.0, side, width, step, arc_to_deg) for x in xs]
        V, F = _grid(rows, xs)
        Vs.append(V)
        Fs.append(F + n)
        plate.append(np.full(len(F), p))
        n += len(V)
    return np.vstack(Vs), np.vstack(Fs), np.concatenate(plate)


def _truth(gap, flush, length, x):
    t = (np.asarray(x) + length / 2) / length
    return gap[0] + (gap[1] - gap[0]) * t, flush[0] + (flush[1] - flush[0]) * t


def _pose(seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return G.make_se3(G.so3_exp(rng.normal(size=3) * 0.4), rng.uniform(-50, 50, 3))


def _moved(X, T):
    return np.asarray(X) @ T[:3, :3].T + T[:3, 3]


def _clicks(gap: float, flush: float, x: float = 0.0, away: float = 6.0):
    """A point on each plate's top, ``away`` mm from its edge's start."""
    a = np.array([x, -(gap / 2 + R_EDGE + away), 0.0])
    b = np.array([x, gap / 2 + R_EDGE + away, flush])
    return a, b


def _station_x(res: GapFlush, T: np.ndarray) -> np.ndarray:
    """Each station's place along the seam (the plates' x) from its seam point."""
    P = np.array([s.point for s in res.stations])
    return _moved(P, G.invert_se3(T))[:, 0]


# --------------------------------------------------------------------------- tests
def test_two_plates_read_their_gap_and_flush():
    """Gap 4 mm, flush 1 mm, at a random pose: the outermost points of the edges ("end") read the
    gap itself, the line where the edge has dropped 0.5 mm reads it plus the round's inset on each
    side; the flush is read square to the first plate. Every station along the 120 mm seam is
    there, both edges measured, every reading trusted."""
    T = _pose()
    V, F, _ = _plates()
    a, b = _clicks(4.0, 1.0)
    res = measure_gap_flush(_moved(V, T), F, _moved(a, T), _moved(b, T), step_mm=3.0)
    assert DEFAULT_RULE in EDGE_RULES and "end" in EDGE_RULES
    assert res.rule == DEFAULT_RULE
    n = len(res.stations)
    assert 36 <= n <= 41  # 120 mm at 3 mm
    pos = np.array([s.position_mm for s in res.stations])
    assert np.all(np.diff(pos) > 0) and pos.min() < -50 and pos.max() > 50
    for s in res.stations:
        assert s.side_a == "measured" and s.side_b == "measured" and not s.bridged, s.to_dict()
        assert s.gap_trusted and s.flush_trusted, s.notes
        assert s.gap_by_rule["end"] == pytest.approx(4.0, abs=0.03)
        assert s.gap_by_rule["drop_0.5"] == pytest.approx(4.0 + 2 * DROP_INSET, abs=0.03)
        assert s.flush_mm == pytest.approx(1.0, abs=0.01)
    assert res.flush.mean == pytest.approx(1.0, abs=0.01)
    assert res.gap_reading.mean == pytest.approx(4.0 + 2 * DROP_INSET, abs=0.03)
    # the same mesh read by the "end" rule
    res_end = measure_gap_flush(_moved(V, T), F, _moved(a, T), _moved(b, T), step_mm=3.0, rule="end")
    assert res_end.gap_reading.mean == pytest.approx(4.0, abs=0.03)
    assert res_end.gap_reading.lowest == pytest.approx(4.0, abs=0.03)
    assert res_end.gap_reading.highest == pytest.approx(4.0, abs=0.03)


def test_the_flush_sign_follows_the_first_click():
    """The flush is the second clicked panel's surface above (+) or below (-) the first's."""
    V, F, _ = _plates()
    a, b = _clicks(4.0, 1.0)
    ab = measure_gap_flush(V, F, a, b)
    ba = measure_gap_flush(V, F, b, a)
    assert ab.flush.mean == pytest.approx(1.0, abs=0.01)
    assert ba.flush.mean == pytest.approx(-1.0, abs=0.01)
    assert ba.gap_reading.mean == pytest.approx(ab.gap_reading.mean, abs=0.02)


def test_a_tapering_seam_reads_each_place():
    """Gap 2 -> 6 mm and flush 0 -> 1.5 mm over 150 mm: each station reads the value where it
    is; lowest, mean and highest follow."""
    T = _pose(5)
    gap, flush, L = (2.0, 6.0), (0.0, 1.5), 150.0
    V, F, _ = _plates(gap=gap, flush=flush, length=L)
    a, b = _clicks(4.0, 0.75)
    res = measure_gap_flush(_moved(V, T), F, _moved(a, T), _moved(b, T), step_mm=4.0, rule="end")
    x = _station_x(res, T)
    g_true, f_true = _truth(gap, flush, L, x)
    g = np.array([s.gap_mm for s in res.stations])
    f = np.array([s.flush_mm for s in res.stations])
    assert len(res.stations) >= 34
    assert np.max(np.abs(g - g_true)) < 0.05
    assert np.max(np.abs(f - f_true)) < 0.02
    assert res.gap_reading.lowest == pytest.approx(g_true.min(), abs=0.05)
    assert res.gap_reading.highest == pytest.approx(g_true.max(), abs=0.05)
    assert res.flush.mean == pytest.approx(f_true.mean(), abs=0.02)
    assert res.gap_reading.lowest_at_mm == pytest.approx(res.stations[int(np.argmin(g))].position_mm)


def test_noise_at_the_scans_level_and_its_stated_uncertainty():
    """Each vertex moved along z by 0.08 mm (1σ) and given that sigma: the flush stays within a
    few hundredths on average, and its stated ± covers the station's real error as a ± should
    (most within 2σ, nearly all within 3σ); the gap by the 0.5 mm drop too."""
    rng = np.random.default_rng(1)
    gap, flush = (4.0, 4.0), (1.0, 1.0)
    V, F, _ = _plates(gap=gap, flush=flush, length=150.0)
    V = V + np.column_stack([np.zeros((len(V), 2)), rng.normal(0.0, 0.08, len(V))])
    a, b = _clicks(4.0, 1.0)
    res = measure_gap_flush(V, F, a, b, sigma=np.full(len(V), 0.08), step_mm=3.0)
    st = [s for s in res.stations if s.flush_trusted]
    assert len(st) >= 0.9 * len(res.stations)
    ef = np.array([s.flush_mm - 1.0 for s in st])
    sf = np.array([s.flush_sigma_mm for s in st])
    assert abs(ef.mean()) < 0.03
    assert 0.005 < np.median(sf) < 0.15
    assert np.mean(np.abs(ef) <= 2 * sf) >= 0.8 and np.mean(np.abs(ef) <= 3 * sf) >= 0.95
    sg = [s for s in res.stations if s.gap_trusted]
    assert len(sg) >= 0.8 * len(res.stations)
    eg = np.array([s.gap_mm - (4.0 + 2 * DROP_INSET) for s in sg])
    gs = np.array([s.gap_sigma_mm for s in sg])
    assert abs(eg.mean()) < 0.1
    assert np.mean(np.abs(eg) <= 3 * gs) >= 0.9


def test_an_edge_that_is_missing_or_filled_is_marked_not_dropped():
    """Where the second plate's last 4 mm before its edge are missing, the gap reads too wide and
    is marked as not trustworthy; where they are filled (not measured), that side says so. The
    stations stay in the answer and the flush is still read from the surfaces."""
    gap, flush = (4.0, 4.0), (1.0, 1.0)
    V, F, plate = _plates(gap=gap, flush=flush, length=150.0)
    C = V[F].mean(axis=1)
    near_edge = (plate == 1) & (C[:, 1] < gap[0] / 2 + R_EDGE + 4.0)
    a, b = _clicks(4.0, 1.0)
    full = measure_gap_flush(V, F, a, b, step_mm=3.0)
    gone = near_edge & (np.abs(C[:, 0]) < 10.0)
    res = measure_gap_flush(V, F[~gone], a + [30.0, 0, 0], b + [30.0, 0, 0], step_mm=3.0)
    assert abs(len(res.stations) - len(full.stations)) <= 1
    hit = [s for s in res.stations if abs(s.point[0]) < 8.0]
    assert hit and all(not s.gap_trusted for s in hit)
    assert all(s.notes for s in hit)
    assert all(s.flush_mm == pytest.approx(1.0, abs=0.02) for s in hit if s.flush_mm is not None)
    away = [s for s in res.stations if abs(s.point[0]) > 25.0]
    assert all(s.gap_trusted for s in away)
    # filled instead of missing
    filled = near_edge & (C[:, 0] > 20.0) & (C[:, 0] < 40.0)
    res2 = measure_gap_flush(V, F, a, b, filled=filled, step_mm=3.0)
    inside = [s for s in res2.stations if 23.0 < s.point[0] < 37.0]
    assert inside and all(s.side_b == "filled" and not s.gap_trusted for s in inside)
    assert all(s.side_a == "measured" for s in inside)
    assert len(res2.stations) == len(full.stations)
    assert res2.gap_reading.n_trusted < res2.gap_reading.n_stations


def test_a_gap_the_scan_has_bridged():
    """For x < 0 the plates are apart; for x > 0 the "scan" runs on across the gap as one sheet with
    a shallow dip (stereo matching fills a narrow gap over a dark background). There the stations
    say the gap is bridged and its reading is not trusted; the flush is still read; the seam is
    followed across."""
    Vp, Fp, _ = _plates(gap=(4.0, 4.0), flush=(0.0, 0.0), length=120.0, x1=0.0)
    ys = np.arange(-30.0, 30.0 + 1e-9, 0.5)
    xs = np.arange(0.0, 60.0 + 1e-9, 1.0)
    dip = -0.3 * np.clip(1.0 - (ys / 2.5) ** 2, 0.0, None)
    Vs, Fs = _grid([np.column_stack([ys, dip]) for _ in xs], xs)
    V = np.vstack([Vp, Vs])
    F = np.vstack([Fp, Fs + len(Vp)])
    a, b = _clicks(4.0, 0.0, x=-30.0)
    res = measure_gap_flush(V, F, a, b, step_mm=3.0)
    pos_x = np.array([s.point[0] for s in res.stations])
    assert pos_x.max() > 50.0 and pos_x.min() < -50.0
    open_ = [s for s in res.stations if s.point[0] < -5.0]
    shut = [s for s in res.stations if s.point[0] > 5.0]
    assert all(s.gap_trusted and not s.bridged for s in open_)
    assert shut and all(s.bridged and not s.gap_trusted for s in shut)
    assert all(s.flush_trusted and s.flush_mm == pytest.approx(0.0, abs=0.03) for s in shut)


def test_the_records_are_plain_json():
    V, F, _ = _plates(length=60.0)
    a, b = _clicks(4.0, 1.0)
    res = measure_gap_flush(V, F, a, b, sigma=np.full(len(V), 0.1))
    d = res.to_dict()
    back = json.loads(json.dumps(d))
    assert back == d
    for key in ("rule", "step_mm", "stations", "gap", "flush", "gap_offered", "gap_note", "units"):
        assert key in d
    s = d["stations"][0]
    for key in ("position_mm", "point", "gap_mm", "gap_sigma_mm", "flush_mm", "flush_sigma_mm", "side_a", "side_b",
                "bridged", "gap_trusted", "flush_trusted", "notes", "gap_by_rule", "edge_a", "edge_b"):
        assert key in s
    for k in ("lowest", "mean", "highest", "n_trusted", "n_stations"):
        assert k in d["flush"]


def test_clicks_that_do_not_make_a_seam_are_refused():
    V, F, _ = _plates(length=60.0)
    a, b = _clicks(4.0, 1.0)
    with pytest.raises(ValueError, match="mesh"):
        measure_gap_flush(V, F, a + [0, 0, 40.0], b)  # far off the surface
    with pytest.raises(ValueError):
        measure_gap_flush(V, F, a, a)  # one point twice
    with pytest.raises(ValueError, match="rule"):
        measure_gap_flush(V, F, a, b, rule="nope")


# --------------------------------------------------------------------------- the simulator's scene
def test_the_seam_scene_reads_its_stated_gap_and_flush():
    """The simulator's seam scene (two hemmed panels, slightly curved, over a dark floor): its own
    surface, at the nominal parameters, reads the gap and flush the scene states at each place
    (the outermost points of the edges, the flush square to the first panel). Its gap is open
    down to the floor."""
    pytest.importorskip("open3d")
    from ohs.sim.scene import SCENE_PRESETS, make_scene, panel_seam, panel_seam_truth

    assert "panel_seam" in SCENE_PRESETS
    sc = panel_seam(gap_mm=(2.0, 6.0), flush_mm=(0.0, 2.0))
    assert make_scene("panel_seam").name == "panel_seam"
    V, F = sc.meta["mesh"]
    a, b = sc.meta["clicks"]
    res = measure_gap_flush(V, F, a, b, step_mm=6.0, rule="end")
    assert len(res.stations) >= 35  # 240 mm
    X = np.array([s.point for s in res.stations])
    truth = panel_seam_truth(sc, X)
    ok = truth["on_seam"]
    assert ok.sum() >= 35
    g = np.array([s.gap_mm for s in res.stations])[ok]
    f = np.array([s.flush_mm for s in res.stations])[ok]
    assert np.max(np.abs(g - truth["gap_mm"][ok])) < 0.02
    assert np.max(np.abs(f - truth["flush_mm"][ok])) < 0.02
    assert truth["gap_mm"][ok].min() < 2.2 and truth["gap_mm"][ok].max() > 5.8
    # a ray straight down through the middle of the seam reaches the floor
    mid = sc.meta["seam_points"]
    hit = sc.cast(mid + [0.0, 0.0, 200.0], np.tile([0.0, 0.0, -1.0], (len(mid), 1)), surface=False)
    assert all(sc.primitives[i].name == "floor" for i in hit.obj)
