"""ohs.inspect.reference: a scan compared with a reference model of the part (FEATURE_PLAN I2).

Made-up meshes with a known truth first: a block with a boss and a blind hole (closed, wound
outward, built here so that nothing of the module under test makes it) and a bent sheet (an open
surface). The "scan" is the same shape tessellated finer, with noise, at a random pose. Then one
simulated scan of the bracket against the scene's own surface, from the cached recording that
``tests/test_align.py`` uses.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.inspect.reference import (ReferenceModel, ScanSurface, align_by_datums, align_by_points,  # noqa: E402
                                   align_by_shape, compare_with_reference, deviation_from_reference, given_alignment,
                                   read_reference, reference_from_arrays, tolerance_summary)
from ohs.measure.fit import fit_plane  # noqa: E402
from ohs.measure.frame import part_frame  # noqa: E402
from ohs.mesh.topology import face_areas  # noqa: E402
from ohs.track.export import save_points_ply, write_stl  # noqa: E402

HOLE = ((15.0, 20.0), 4.0)
BOSS = ((42.0, 20.0), 6.0)
SEG = 64
L, D, H = 60.0, 40.0, 20.0


# --------------------------------------------------------------------------- made-up meshes
class _Builder:
    """Points and faces added piece by piece; each face is wound towards the normal it should have."""

    def __init__(self):
        self.V: list[np.ndarray] = []
        self.n = 0
        self.F: list[np.ndarray] = []
        self.want: list[np.ndarray] = []
        self.label: list[np.ndarray] = []

    def points(self, P) -> np.ndarray:
        P = np.asarray(P, float).reshape(-1, 3)
        self.V.append(P)
        self.n += len(P)
        return np.arange(self.n - len(P), self.n)

    def all(self) -> np.ndarray:
        return np.vstack(self.V)

    def faces(self, F, want, label: str) -> None:
        F = np.asarray(F, np.int64).reshape(-1, 3)
        w = want(F) if callable(want) else np.broadcast_to(np.asarray(want, float), F.shape)
        self.F.append(F)
        self.want.append(np.asarray(w, float))
        self.label.append(np.full(len(F), label, dtype=object))

    @staticmethod
    def strip(a: np.ndarray, b: np.ndarray, closed: bool) -> np.ndarray:
        a1, b1, a2, b2 = (a, b, np.roll(a, -1), np.roll(b, -1)) if closed else (a[:-1], b[:-1], a[1:], b[1:])
        return np.vstack([np.stack([a1, a2, b2], 1), np.stack([a1, b2, b1], 1)])

    def mesh(self):
        V, F, want = self.all(), np.vstack(self.F), np.vstack(self.want)
        n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
        flip = np.einsum("ij,ij->i", n, want) < 0
        F[flip] = F[flip][:, ::-1]
        return V, F, np.concatenate(self.label)


def _loop(step: float) -> np.ndarray:
    out = []
    for (x0, y0), (x1, y1) in (((0, 0), (L, 0)), ((L, 0), (L, D)), ((L, D), (0, D)), ((0, D), (0, 0))):
        n = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / step))
        t = np.arange(n) / n
        out.append(np.stack([x0 + t * (x1 - x0), y0 + t * (y1 - y0)], 1))
    return np.vstack(out)


def _wall(b: _Builder, top: np.ndarray, xy: np.ndarray, z0: float, z1: float, step: float, want, label: str) -> np.ndarray:
    rows = [top]
    nz = max(1, math.ceil(abs(z1 - z0) / step))
    for k in range(1, nz + 1):
        rows.append(b.points(np.column_stack([xy, np.full(len(xy), z0 + (z1 - z0) * k / nz)])))
    for a, c in zip(rows[:-1], rows[1:]):
        b.faces(b.strip(a, c, True), want, label)
    return rows[-1]


def _disc(b: _Builder, c, r: float, z: float, rim: np.ndarray, step: float, label: str) -> None:
    ang = np.arange(SEG) * 2 * math.pi / SEG
    m = max(1, math.ceil(r / step))
    rings = [rim] + [b.points(np.stack([c[0] + r * k / m * np.cos(ang), c[1] + r * k / m * np.sin(ang), np.full(SEG, z)], 1))
                     for k in range(m - 1, 0, -1)]
    for a, d in zip(rings[:-1], rings[1:]):
        b.faces(b.strip(a, d, True), [0, 0, 1], label)
    centre = b.points([[c[0], c[1], z]])[0]
    b.faces(np.stack([np.full(SEG, centre), rings[-1], np.roll(rings[-1], -1)], 1), [0, 0, 1], label)


def _part(step: float = 2.0, boss_top: float = 28.0, hole_bottom: float = 10.0, stretch: float = 1.0,
          bottom: bool = True):
    """A block 60 x 40 x 20 mm, a blind hole (d 8, 10 deep) and a boss (d 12, 8 high) on top, in
    the part's own axes (a corner at the origin): ``(V, F, label per face)``, closed and wound
    outward (open underneath without ``bottom``). ``stretch`` makes it longer along x."""
    b = _Builder()
    loop = _loop(step)
    ang = np.arange(SEG) * 2 * math.pi / SEG
    circ = {k: np.stack([c[0] + r * np.cos(ang), c[1] + r * np.sin(ang)], 1) for k, (c, r) in (("hole", HOLE), ("boss", BOSS))}
    gx, gy = np.meshgrid(np.arange(step, L - 0.5 * step, step), np.arange(step, D - 0.5 * step, step))
    grid = np.stack([gx.ravel(), gy.ravel()], 1)
    keep = np.ones(len(grid), bool)
    for c, r in (HOLE, BOSS):
        keep &= np.hypot(grid[:, 0] - c[0], grid[:, 1] - c[1]) > r + 0.5 * step
    top_loop = b.points(np.column_stack([loop, np.full(len(loop), H)]))
    top_grid = b.points(np.column_stack([grid[keep], np.full(int(keep.sum()), H)]))
    rim = {k: b.points(np.column_stack([v, np.full(SEG, H)])) for k, v in circ.items()}
    P2 = np.vstack([loop, grid[keep], circ["hole"], circ["boss"]])
    idx = np.concatenate([top_loop, top_grid, rim["hole"], rim["boss"]])
    from scipy.spatial import Delaunay

    tri = Delaunay(P2).simplices
    cen = P2[tri].mean(axis=1)
    ok = np.ones(len(tri), bool)
    for c, r in (HOLE, BOSS):
        ok &= np.hypot(cen[:, 0] - c[0], cen[:, 1] - c[1]) > r
    b.faces(idx[tri[ok]], [0, 0, 1], "top")

    def side_out(F):
        q = b.all()[F].mean(axis=1)
        n = np.zeros_like(q)
        xs = np.minimum(q[:, 0], L - q[:, 0]) < np.minimum(q[:, 1], D - q[:, 1])
        n[xs, 0] = np.where(q[xs, 0] < L / 2, -1.0, 1.0)
        n[~xs, 1] = np.where(q[~xs, 1] < D / 2, -1.0, 1.0)
        return n

    def radial(c, sign):
        def f(F):
            q = b.all()[F].mean(axis=1)
            return sign * np.column_stack([q[:, 0] - c[0], q[:, 1] - c[1], np.zeros(len(q))])
        return f

    bot_loop = _wall(b, top_loop, loop, H, 0.0, step, side_out, "side")
    if bottom:
        gb = b.points(np.column_stack([grid, np.zeros(len(grid))]))
        tri = Delaunay(np.vstack([loop, grid])).simplices
        b.faces(np.concatenate([bot_loop, gb])[tri], [0, 0, -1], "bottom")
    ring = _wall(b, rim["hole"], circ["hole"], H, hole_bottom, step, radial(HOLE[0], -1.0), "hole_wall")
    _disc(b, HOLE[0], HOLE[1], hole_bottom, ring, step, "hole_bottom")
    ring = _wall(b, rim["boss"], circ["boss"], H, boss_top, step, radial(BOSS[0], 1.0), "boss_wall")
    _disc(b, BOSS[0], BOSS[1], boss_top, ring, step, "boss_cap")
    V, F, lab = b.mesh()
    c = V[F].mean(axis=1)
    side = lab == "side"
    for name, m in (("x0", np.abs(c[:, 0]) < 1e-6), ("x1", np.abs(c[:, 0] - L) < 1e-6), ("y0", np.abs(c[:, 1]) < 1e-6),
                    ("y1", np.abs(c[:, 1] - D) < 1e-6)):
        lab[side & m] = name
    return V * np.array([stretch, 1.0, 1.0]), F, lab


def _sheet(step: float = 2.0, lift_from: float | None = None, lift_mm: float = 0.0):
    """An open bent sheet (a surface, no thickness): 50 mm flat along x, a 90 degree bend of radius
    10 mm, a 30 mm flange up; 40 mm wide along y. Wound with its normal up on the flat part.
    Points further than ``lift_from`` along the sheet move ``lift_mm`` along that normal."""
    r, flat, flange = 10.0, 50.0, 30.0
    s = np.linspace(0.0, flat + 0.5 * math.pi * r + flange, int(round((flat + 0.5 * math.pi * r + flange) / step)) + 1)
    y = np.linspace(0.0, 40.0, int(round(40.0 / step)) + 1)

    def curve(s):
        a = np.clip((s - flat) / r, 0.0, 0.5 * math.pi)
        x = np.where(s < flat, s, flat + r * np.sin(a))
        z = np.where(s < flat, 0.0, r - r * np.cos(a))
        up = s > flat + 0.5 * math.pi * r
        x = np.where(up, flat + r, x)
        z = np.where(up, r + (s - flat - 0.5 * math.pi * r), z)
        nx, nz = -np.sin(a), np.cos(a)  # the normal on the inside of the bend (up on the flat part)
        return x, z, nx, nz

    x, z, nx, nz = curve(s)
    if lift_from is not None:
        m = s >= lift_from
        x, z = x + lift_mm * nx * m, z + lift_mm * nz * m
    S, Y = np.meshgrid(np.arange(len(s)), np.arange(len(y)), indexing="ij")
    V = np.column_stack([x[S.ravel()], y[Y.ravel()], z[S.ravel()]])
    i = (S * len(y) + Y)[:-1, :-1].ravel()
    F = np.vstack([np.stack([i, i + len(y), i + len(y) + 1], 1), np.stack([i, i + len(y) + 1, i + 1], 1)])
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    want = np.column_stack([nx, np.zeros_like(nx), nz])[S.ravel()][F[:, 0]]
    flip = np.einsum("ij,ij->i", n, want) < 0
    F[flip] = F[flip][:, ::-1]
    return V, F


def _pose(seed: int, deg: float = 70.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    axis = rng.normal(size=3)
    return G.make_se3(G.so3_exp(math.radians(deg) * axis / np.linalg.norm(axis)), rng.uniform(-80.0, 80.0, 3))


def _moved(V: np.ndarray, T: np.ndarray) -> np.ndarray:
    return V @ T[:3, :3].T + T[:3, 3]


def _scan_of(V, F, T_part_to_scan, seed=0, noise=0.02) -> ScanSurface:
    """``(V, F)`` as a scan: noisy, in the scan's own world (``T_part_to_scan``), sigma = the noise."""
    rng = np.random.default_rng(seed)
    Vs = _moved(V + rng.normal(0.0, noise, V.shape), T_part_to_scan)
    return ScanSurface(Vs, F, sigma=np.full(len(V), noise))


def _pose_off_mm(T, T_true, P) -> float:
    """RMS distance between the points ``P`` moved by ``T`` and by ``T_true``."""
    return float(np.sqrt(np.mean(np.sum((_moved(P, T) - _moved(P, T_true)) ** 2, axis=1))))


def _faces_mean(dev, F, mask) -> float:
    """The mean deviation over the vertices of the faces ``mask`` that were compared."""
    v = np.unique(F[mask])
    d = dev.offset[v]
    return float(np.nanmean(d))


@pytest.fixture(scope="module")
def reference(tmp_path_factory) -> ReferenceModel:
    """The block, coarse, written to an STL and read back as the reference."""
    V, F, _ = _part(step=4.0)
    path = tmp_path_factory.mktemp("ref") / "block.stl"
    write_stl(path, V, F)
    return read_reference(path)


# --------------------------------------------------------------------------- alignment and deviation
@pytest.mark.parametrize("move", [0.0, 1.0, -1.0])
def test_a_noisy_copy_aligns_from_its_shape_and_a_moved_face_shows(reference, move):
    """A noisy copy at a random pose aligns from the shape and reads zero within the noise; with the
    boss's top face moved 1 mm out (or in), that face reads about 1 mm, the rest stays, and it is
    found as one region of the right size, side and place."""
    V, F, lab = _part(step=1.0, boss_top=28.0 + move)
    T_true = _pose(1)
    scan = _scan_of(V, F, G.invert_se3(T_true))  # the scan's world: the part moved by inv(T_true)
    al = align_by_shape(scan, reference)
    assert al.accepted and al.method == "shape", al.verdict
    assert al.pair is not None and al.pair["start"] == "features" and al.pair["accepted"]
    assert _pose_off_mm(al.T, T_true, scan.V) < 0.05
    res = compare_with_reference(scan, reference, al, tolerance_mm=0.3)
    dev, tol = res.deviation, res.tolerance
    cap = lab == "boss_cap"
    rest_v = np.setdiff1d(np.unique(F[~cap]), np.unique(F[cap | (lab == "boss_wall")]))
    assert abs(float(np.nanmedian(dev.offset[rest_v]))) < 0.01
    assert float(np.nanpercentile(np.abs(dev.offset[rest_v]), 95)) < 0.06
    assert dev.off_reference_share < 0.01 and dev.not_measured_share == 0.0
    if move == 0.0:
        assert tol.share_within > 0.999 and tol.regions == []
        assert abs(tol.stats["median_mm"]) < 0.01 and 0.01 < tol.stats["robust_sigma_mm"] < 0.04
        assert tol.sentence.startswith("100 % within ± 0.3 mm")
        return
    inner = np.unique(F[cap])
    inner = inner[np.hypot(V[inner, 0] - BOSS[0][0], V[inner, 1] - BOSS[0][1]) < BOSS[1] - 1.0]
    assert abs(float(np.nanmean(dev.offset[inner])) - move) < 0.05
    big = [r for r in tol.regions if r.area_mm2 > 20.0]
    assert len(big) == 1, [r.to_dict() for r in tol.regions]
    r = big[0]
    cap_area = math.pi * BOSS[1] ** 2
    # a taller boss also has a 1 mm band of its wall standing above the reference's boss (reading 0 to 1 mm)
    band = 2.0 * math.pi * BOSS[1] * move if move > 0 else 0.0
    area = cap_area + band
    assert r.side == ("above" if move > 0 else "below")
    assert abs(r.area_mm2 - area) < 0.1 * area
    # the mean is a little under 1 mm: the cap's rim (and the band) meet the reference's boss at its edge
    assert 0.75 < r.mean_mm / move < 1.02
    assert abs(r.worst_mm - move) < 0.08
    centre = np.array([BOSS[0][0], BOSS[0][1], 28.0 + move])  # regions are in the reference's frame
    assert np.linalg.norm(np.asarray(r.center) - centre) < 1.0
    assert r.exceeds_uncertainty is True and r.share_beyond_uncertainty > 0.9
    share_side = tol.share_above if move > 0 else tol.share_below
    assert abs(share_side - area / dev.compared_area_mm2) < 0.005
    assert tol.share_within + tol.share_above + tol.share_below == pytest.approx(1.0)
    d = res.to_dict()
    assert d["alignment"]["method"] == "shape" and d["tolerance"]["regions"][0]["side"] == r.side
    # a boss 1 mm short: near its rim the top is nearer the reference's wall, and is measured along its normal
    assert d["deviation"]["n_along_normal"] == int(dev.along_normal.sum()) and (move > 0 or dev.along_normal.sum() >= 5)
    assert "faces" not in d["tolerance"]["regions"][0]


def test_datums_show_what_a_best_fit_hides(reference):
    """A block 0.5 mm too long: a best fit spreads it over both ends (about a quarter each, the
    rest on the features), an alignment on datums at one end puts all of it on the other end.
    Both say how they were aligned, and the best fit says how far it is from the datums."""
    V, F, lab = _part(step=1.0, stretch=60.5 / 60.0)
    T_true = _pose(2)
    scan = _scan_of(V, F, G.invert_se3(T_true), seed=2)
    # the datums, fitted on the scan as the user would: bottom face, the end face x = 0, the side y = 0
    fits = [fit_plane(scan.V[np.unique(F[lab == name])]) for name in ("bottom", "x0", "y0")]
    frame = part_frame(*fits)
    on_datums = align_by_datums(frame)
    assert on_datums.method == "datums" and on_datums.accepted and on_datums.pair is None
    assert "datum" in on_datums.verdict
    best = align_by_shape(scan, reference, datums=frame)
    assert best.accepted and best.method == "shape"
    devs = {}
    for al in (on_datums, best):
        dev = deviation_from_reference(scan, reference, al)
        devs[al.method] = {k: _faces_mean(dev, F, lab == k) for k in ("x0", "x1", "y0", "bottom", "top")}
    dd, bf = devs["datums"], devs["shape"]
    assert abs(dd["x0"]) < 0.03 and abs(dd["x1"] - 0.5) < 0.05  # all of it at the far end
    assert abs(dd["bottom"]) < 0.03 and abs(dd["y0"]) < 0.03
    assert 0.12 < bf["x0"] < 0.38 and 0.12 < bf["x1"] < 0.38  # spread over both ends
    assert abs(bf["x0"] - bf["x1"]) < 0.2
    vs = best.vs_datums
    assert vs is not None and 0.1 < vs["shift_mm"] < 0.45 and vs["angle_deg"] < 0.2 and vs["max_mm"] >= vs["rms_mm"]
    d = best.to_dict()
    assert d["method"] == "shape" and d["vs_datums"]["shift_mm"] == pytest.approx(vs["shift_mm"])
    assert on_datums.to_dict()["method"] == "datums" and on_datums.to_dict()["frame"]["axes"]["primary"] == "Z"


def test_point_pairs_with_and_without_refinement(reference):
    V, F, lab = _part(step=1.0)
    T_true = _pose(3)
    scan = _scan_of(V, F, G.invert_se3(T_true), seed=3)
    on_ref = np.array([[0.0, 0.0, 20.0], [60.0, 0.0, 20.0], [60.0, 40.0, 0.0], [42.0, 20.0, 28.0]])
    rng = np.random.default_rng(4)
    on_scan = _moved(on_ref, G.invert_se3(T_true)) + rng.normal(0.0, 1.0, on_ref.shape)  # picked a millimetre off
    rough = align_by_points(scan, reference, on_scan, on_ref, refine=False)
    assert rough.method == "points" and rough.pair is None and rough.points_rms_mm > 0.2
    assert 0.2 < _pose_off_mm(rough.T, T_true, scan.V) < 5.0
    assert "4 picked pairs" in rough.verdict
    fine = align_by_points(scan, reference, on_scan, on_ref, refine=True)
    assert fine.method == "points+shape" and fine.accepted and fine.pair["start"] == "given", fine.verdict
    assert _pose_off_mm(fine.T, T_true, scan.V) < 0.05
    assert fine.points_rms_mm == pytest.approx(rough.points_rms_mm)
    with pytest.raises(ValueError, match="three or more"):
        align_by_points(scan, reference, on_scan[:2], on_ref[:2])


# --------------------------------------------------------------------------- reading the reference
def test_units_are_asked_about_not_guessed(tmp_path, reference):
    V, F, _ = _part(step=4.0)
    scan = _scan_of(*_part(step=2.0)[:2], np.eye(4))
    for factor, unit in ((25.4, "inch"), (1000.0, "m")):
        path = tmp_path / f"block_{unit}.stl"
        write_stl(path, V / factor, F)
        ref = read_reference(path, scan=scan)
        u = ref.units
        assert u.status == "pending" and u.factor == pytest.approx(factor) and u.unit == unit, u.to_dict()
        assert f"{factor:g}" in u.message and not ref.ready
        with pytest.raises(ValueError, match="units"):
            align_by_shape(scan, ref)
        applied = ref.apply_units()
        assert applied.ready and applied.units.status == "applied" and applied.scale == pytest.approx(factor)
        assert np.allclose(applied.V, ref.V * factor) and ref.scale == 1.0  # the original is left alone
        kept = ref.keep_units()
        assert kept.ready and kept.units.status == "kept" and np.allclose(kept.V, ref.V)
        assert ref.to_dict()["units"]["status"] == "pending"
    same = read_reference(tmp_path / "block_m.stl", scan=None)
    assert same.units is None and same.ready  # nothing to compare the size with: nothing asked
    ok = reference_from_arrays(V, F, scan=scan)
    assert ok.units.status == "same" and ok.ready


def test_empty_broken_and_odd_files_are_refused_in_words(tmp_path):
    def refused(path, words):
        with pytest.raises(ValueError, match=words):
            read_reference(path)

    refused(tmp_path / "missing.stl", "not found")
    (tmp_path / "empty.stl").write_bytes(b"")
    refused(tmp_path / "empty.stl", "no faces")
    save_points_ply(tmp_path / "points.ply", np.random.default_rng(0).normal(size=(50, 3)))
    refused(tmp_path / "points.ply", "no faces.*point cloud")
    (tmp_path / "junk.ply").write_bytes(b"ply\nformat binary_little_endian 1.0\nelement vertex 99\nproperty float x\nend_header\n\x00")
    refused(tmp_path / "junk.ply", "could not be read")
    (tmp_path / "part.step").write_text("ISO-10303-21;")
    refused(tmp_path / "part.step", "STL, OBJ or PLY")
    V, F, _ = _part(step=4.0)
    with pytest.raises(ValueError, match="no area"):
        reference_from_arrays(np.zeros((3, 3)), np.array([[0, 1, 2]]))
    bad = V.copy()
    bad[5] = np.nan
    with pytest.raises(ValueError, match="not a number"):
        reference_from_arrays(bad, F)
    with pytest.raises(ValueError, match="too small"):
        reference_from_arrays(V * 1e-6, F)
    with pytest.raises(ValueError, match="too large"):
        reference_from_arrays(V * 1e6, F)


def test_an_inside_out_or_mixed_reference_reads_the_same(reference):
    """A reference wound inside out, or with a third of its faces turned, is put right (and says
    so); an open surface takes its outside from the scan. The deviations are the same."""
    V, F, _ = _part(step=4.0)
    scan = _scan_of(*_part(step=1.0, boss_top=29.0)[:2], np.eye(4))
    al = given_alignment(np.eye(4), "test")
    assert al.method == "given" and al.accepted
    right = deviation_from_reference(scan, reference_from_arrays(V, F), al)
    assert reference.orientation["turned"] == 0 and reference.orientation["closed"]
    inside_out = reference_from_arrays(V, F[:, ::-1])
    assert inside_out.orientation["turned"] == len(F) and "inside out" in inside_out.orientation["note"]
    rng = np.random.default_rng(5)
    some = rng.random(len(F)) < 0.3
    Fm = F.copy()
    Fm[some] = Fm[some][:, ::-1]
    mixed = reference_from_arrays(V, Fm)
    assert mixed.orientation["turned"] == int(some.sum()) and "mixed" in mixed.orientation["note"]
    for ref in (inside_out, mixed):
        dev = deviation_from_reference(scan, ref, al)
        assert np.allclose(dev.offset, right.offset, atol=1e-5, equal_nan=True)
    # an open surface: its outside is the side the scan saw it from
    Vs, Fs = _sheet(step=3.0)
    sheet_scan = _scan_of(*_sheet(step=1.0, lift_from=70.0, lift_mm=0.6), np.eye(4))
    a = deviation_from_reference(sheet_scan, reference_from_arrays(Vs, Fs), al)
    flipped = reference_from_arrays(Vs, Fs[:, ::-1])
    assert not flipped.orientation["closed"] and "open" in flipped.orientation["note"]
    b = deviation_from_reference(sheet_scan, flipped, al)
    assert np.allclose(a.offset, b.offset, atol=1e-5, equal_nan=True)
    assert any("open" in n for n in b.notes)
    assert float(np.nanpercentile(a.offset, 99)) == pytest.approx(0.6, abs=0.08)  # lifted along the side the scan saw


def test_a_table_is_not_on_the_reference_and_the_unseen_bottom_is_reported(reference):
    """A scan of the block standing on a table (its bottom unseen): the table is "not on the
    reference" and counts in no share; the bottom face is reference surface the scan did not cover."""
    V, F, lab = _part(step=1.0, bottom=False)
    g = np.arange(-60.0, 120.0 + 1e-9, 2.0)
    gx, gy = np.meshgrid(g, np.arange(-60.0, 100.0 + 1e-9, 2.0))
    P = np.column_stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)])
    from scipy.spatial import Delaunay

    tri = Delaunay(P[:, :2]).simplices
    c = P[tri].mean(axis=1)
    out = ~((c[:, 0] > 0) & (c[:, 0] < L) & (c[:, 1] > 0) & (c[:, 1] < D))  # the table under the block is hidden
    tri = tri[out]
    n = np.cross(P[tri[:, 1]] - P[tri[:, 0]], P[tri[:, 2]] - P[tri[:, 0]])
    tri[n[:, 2] < 0] = tri[n[:, 2] < 0][:, ::-1]
    Vt, Ft = np.vstack([V, P]), np.vstack([F, tri + len(V)])
    T_true = _pose(6, deg=40.0)
    scan = _scan_of(Vt, Ft, G.invert_se3(T_true), seed=6)
    al = align_by_shape(scan, reference)
    assert al.accepted and _pose_off_mm(al.T, T_true, scan.V[: len(V)]) < 0.08, al.verdict
    res = compare_with_reference(scan, reference, al, tolerance_mm=0.1)
    dev, tol = res.deviation, res.tolerance
    table_area = float(face_areas(Vt, Ft)[len(F):].sum())
    part_area = float(face_areas(Vt, Ft)[: len(F)].sum())
    assert abs(dev.off_reference_area_mm2 - table_area) < 0.02 * table_area
    assert abs(dev.compared_area_mm2 - part_area) < 0.02 * part_area
    assert np.all(np.isnan(dev.offset[len(V):][np.abs(P[:, 0] - 30.0) > 35.0]))  # table vertices: no value
    assert tol.share_within > 0.98  # the table does not count against the part
    bottom_share = L * D / reference.area_mm2
    assert abs(dev.uncovered_share - bottom_share) < 0.03, (dev.uncovered_share, bottom_share)
    assert dev.uncovered_area_mm2 == pytest.approx(dev.uncovered_share * reference.area_mm2)
    d = dev.to_dict()
    assert "not on the reference" in d["rule"] and d["near_mm"] == dev.near_mm


def test_shares_are_by_area_not_by_vertex_count():
    """The sheet with its flange lifted 0.6 mm: refining the scan's mesh in the lifted band
    changes the vertex count there many times over, and the shares not at all."""
    Vr, Fr = _sheet(step=3.0)
    ref = reference_from_arrays(Vr, Fr)
    V, F = _sheet(step=1.0, lift_from=80.0, lift_mm=0.6)
    al = given_alignment(np.eye(4), "test")
    coarse = tolerance_summary(deviation_from_reference(ScanSurface(V, F), ref, al), 0.3)
    V2, F2 = V, F
    for _ in range(2):  # split each face of the band into four, twice
        band = V2[F2].mean(axis=1)[:, 2] > 25.0
        V2, F2 = _split(V2, F2, band)
    assert len(V2) > 2 * len(V)
    fine_dev = deviation_from_reference(ScanSurface(V2, F2), ref, al)
    fine = tolerance_summary(fine_dev, 0.3)
    assert coarse.share_above > 0.05
    assert fine.share_above == pytest.approx(coarse.share_above, abs=0.005)
    assert fine.share_within == pytest.approx(coarse.share_within, abs=0.005)
    by_count = float(np.mean(fine_dev.offset[np.isfinite(fine_dev.offset)] > 0.3))
    assert by_count > fine.share_above + 0.1  # what counting vertices would have said


def _split(V, F, which):
    """Each face of ``which`` split into four at its edges' midpoints (cracks at the border are fine here)."""
    Fs = F[which]
    a, b, c = Fs[:, 0], Fs[:, 1], Fs[:, 2]
    n = len(V)
    mids = np.vstack([(V[a] + V[b]) / 2, (V[b] + V[c]) / 2, (V[c] + V[a]) / 2])
    k = len(Fs)
    ab, bc, ca = n + np.arange(k), n + k + np.arange(k), n + 2 * k + np.arange(k)
    new = np.vstack([np.stack([a, ab, ca], 1), np.stack([ab, b, bc], 1), np.stack([ca, bc, c], 1), np.stack([ab, bc, ca], 1)])
    return np.vstack([V, mids]), np.vstack([F[~which], new])


def test_records_are_plain():
    import json

    Vr, Fr = _sheet(step=3.0)
    ref = reference_from_arrays(Vr, Fr, name="sheet")
    res = compare_with_reference(ScanSurface(*_sheet(step=2.0, lift_from=80.0, lift_mm=0.6)), ref,
                                 given_alignment(np.eye(4), "test"), tolerance_mm=0.3)
    text = json.dumps(res.to_dict())
    assert "NaN" not in text and '"method": "given"' in text
    assert json.loads(json.dumps(ref.to_dict()))["name"] == "sheet"
    assert res.to_dict()["tolerance"]["sentence"] == res.tolerance.sentence
    assert "within ± 0.3 mm" in res.summary


# --------------------------------------------------------------------------- a simulated scan
N_FRAMES = 60


def _bracket_reference() -> tuple[np.ndarray, np.ndarray]:
    """The bracket scene's part as triangles in the simulator's world: each box and cylinder closed
    (they overlap where the part is joined: their buried faces are reference surface nobody can
    scan). The table is left out."""
    from ohs.sim.scene import Box, Cylinder, make_scene

    Vs, Fs, n = [], [], 0
    for p in make_scene("bracket", 0).primitives:
        if isinstance(p, Box):
            s = np.array([[i, j, k] for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)], float) * p.half
            q = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            f = np.array([t for a, b, c, d in q for t in ((a, b, c), (a, c, d))])
        elif isinstance(p, Cylinder):
            m = 128
            ang = np.arange(m) * 2 * math.pi / m
            ring = np.column_stack([p.radius * np.cos(ang), p.radius * np.sin(ang)])
            s = np.vstack([np.column_stack([ring, np.full(m, -p.half_height)]), np.column_stack([ring, np.full(m, p.half_height)]),
                           [[0, 0, -p.half_height], [0, 0, p.half_height]]])
            i = np.arange(m)
            j = (i + 1) % m
            f = np.vstack([np.stack([i, j, j + m], 1), np.stack([i, j + m, i + m], 1),
                           np.stack([np.full(m, 2 * m), j, i], 1), np.stack([np.full(m, 2 * m + 1), i + m, j + m], 1)])
        else:
            continue
        Vs.append(s @ p.R.T + p.t)
        Fs.append(f + n)
        n += len(s)
    return np.vstack(Vs), np.vstack(Fs)


@pytest.fixture(scope="module")
def bracket_scan(tmp_path_factory) -> dict:
    return _bracket_scan(tmp_path_factory.mktemp("bracket"))


def _bracket_scan(tmp) -> dict:
    """The bracket scan of ``tests/test_align.py`` (its cached recording, 640 x 400, 60 frames from
    20 degrees), fused at its true poses: its world is the first camera's, ``world`` puts it into
    the simulator's."""
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams, process_recording
    from ohs.io.recording import RecordingReader
    from ohs.sim.nominal import nominal_calibration
    from ohs.sim.scene import make_scene
    from ohs.sim.trajectory import HandheldTrajectory
    from recordings import scan_recording

    sc = make_scene("bracket", 0)
    cal = nominal_calibration((640, 400), realistic=True)
    traj = HandheldTrajectory(target=sc.target, t0=100.0 + float(cal.imu.time_offset), duration=N_FRAMES / 30.0, seed=0,
                              baseline=cal.stereo.baseline, azimuth0_deg=20.0, **dict(sc.meta.get("trajectory", {})))
    rec = scan_recording(tmp / "rec_bracket_20_640", scene="bracket", n_frames=N_FRAMES, image_size=(640, 400),
                         realistic=True, depth_every=0, seed=0, trajectory=traj)
    gt = RecordingReader(rec).ground_truth_poses()
    first = min(gt)
    T0inv = G.invert_se3(gt[first])
    out = tmp / "out"
    process_recording(rec, out, params=ProcessParams(pipeline=PipelineParams(), export=("ply",)),
                      poses={i: T0inv @ T for i, T in gt.items()})
    return {"mesh": out / "mesh.ply", "world": gt[first], "scene": sc}


@pytest.mark.slow
def test_a_simulated_scan_against_its_scenes_own_surface(bracket_scan, tmp_path):
    """At the true pose the deviation is the scan's own error, as an independent ray cast to the
    simulator's surface measures it (VALIDATION.md, "Aligning and merging two scans": a median of
    0.1-0.25 mm on the bracket's part); from the shape alone the pose is found to about a
    millimetre (as there, at 640 x 400) and the shares change little. The table is not on the
    reference."""
    Vr, Fr = _bracket_reference()
    path = tmp_path / "bracket.stl"
    write_stl(path, Vr, Fr)
    scan = ScanSurface.from_file(bracket_scan["mesh"])
    ref = read_reference(path, scan=scan)
    assert ref.ready and ref.units.status == "same", ref.units.to_dict()
    T_true = bracket_scan["world"]
    truth = compare_with_reference(scan, ref, given_alignment(T_true, "the simulator's"), tolerance_mm=0.3)
    dev = truth.deviation
    ok = np.isfinite(dev.offset)
    assert dev.off_reference_share > 0.5  # most of a table scan is table
    # the error along each compared vertex's normal to the scene's surface, by a ray cast
    sc = bracket_scan["scene"]
    P = _moved(scan.V[ok], T_true)
    N = scan.normals()[ok] @ T_true[:3, :3].T
    o = P + 3.0 * N
    hit = sc.cast(o, -N, surface=False)
    part = hit.obj != sc.object_id("table")
    true_err = 3.0 - hit.t[part]
    med_true = float(np.median(np.abs(true_err[np.isfinite(true_err)])))
    med_dev = float(np.median(np.abs(dev.offset[ok][part])))
    print(f"bracket at the true pose: {truth.summary}; median |deviation| {med_dev:.3f} mm, by ray cast {med_true:.3f} mm")
    assert 0.05 < med_dev < 0.4 and abs(med_dev - med_true) < 0.05
    al = align_by_shape(scan, ref)
    assert al.accepted, al.verdict
    part_v = scan.V[ok]
    off = _pose_off_mm(al.T, T_true, part_v)
    res = compare_with_reference(scan, ref, al, tolerance_mm=0.3)
    print(f"from the shape: {al.verdict} {off:.2f} mm from the true pose; {res.summary}")
    assert off < 1.5
    assert abs(res.tolerance.share_within - truth.tolerance.share_within) < 0.15
