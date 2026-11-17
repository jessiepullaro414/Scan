"""``ohs.mesh.support``: finding the surface a part stands on and removing it ("Remove the table").

The scenes are swept surfaces with known truth: a flat grid is folded onto a profile that runs
across the part's top, down its wall, round a blend at its foot (the rounding a scan's surface
has where the part meets the table) and out over the table, so every face knows whether it lies
on the table, on the blend or on the part. Noise along the normals at the scan's level (0.1 mm,
as the simulated bracket's table: 0.11 mm RMS).

* a box on a table: the table goes, the box's walls stay right down to where the blend turns
  more than 45° from the table, its top stays; the numbers of the result;
* a ball on a table (the sphere's lower side turns away from the table: kept to the crevice);
* a low dome meeting the table at a shallow angle (30°): the hard case; what it costs is the
  margin: the dome is kept from ``margin_mm`` up;
* a box whose flat top is larger than what is seen of the table: the top stays, the table goes
  (the top is the largest plane, but nothing stands on it: the next plane is the table);
* no table (a box alone, a dome alone) and a part's own face with a block standing on it (the
  maintenance plan's F11): refused in words;
* a tilted table at a random pose; a table with a hole and a loose patch of table, and a loose
  bit of the part above the table (left for Remove floaters).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from ohs.mesh.support import MARGIN_MM, SupportParams, SupportRefused, find_support, remove_support
from ohs.mesh.topology import compact, face_areas, face_normals, vertex_normals

TABLE, PART, BLEND = 0, 1, 2
NOISE_MM = 0.1


# --------------------------------------------------------------------------- swept scenes
def _rounded_rect(Q: np.ndarray, hx: float, hy: float, rho: float):
    """Signed distance of 2-D points from a rounded rectangle (outside +), the closest point on its
    border and the outward normal there."""
    k = np.array([hx - rho, hy - rho])
    s = np.where(Q < 0, -1.0, 1.0)
    qa = np.abs(Q) - k
    out = np.maximum(qa, 0.0)
    corner = (qa[:, 0] > 0) & (qa[:, 1] > 0)
    m = np.zeros_like(Q)
    ln = np.linalg.norm(out, axis=1)
    m[corner] = out[corner] / ln[corner, None]
    xs = ~corner & (qa[:, 0] >= qa[:, 1])
    m[xs, 0] = 1.0
    m[~corner & ~xs, 1] = 1.0
    a = np.where(corner, ln, np.max(qa, axis=1)) - rho
    m = m * s
    return a, Q - a[:, None] * m, m


def _disk(Q: np.ndarray, r0: float = 0.0):
    r = np.linalg.norm(Q, axis=1)
    m = np.divide(Q, r[:, None], out=np.tile([1.0, 0.0], (len(Q), 1)), where=r[:, None] > 0)
    return r - r0, m * r0, m


def _box_profile(H: float, rf: float):
    """Box of height ``H`` with a blend of radius ``rf`` at its foot: ``a`` (arc length, 0 at the
    top edge) -> (outward offset, height, label)."""
    def prof(a):
        w, z, lab = np.empty_like(a), np.empty_like(a), np.empty(len(a), dtype=np.int8)
        top = a <= 0
        w[top], z[top], lab[top] = a[top], H, PART
        wall = (a > 0) & (a <= H - rf)
        w[wall], z[wall], lab[wall] = 0.0, H - a[wall], PART
        a1 = H - rf + 0.5 * math.pi * rf
        bl = (a > H - rf) & (a <= a1)
        th = (a[bl] - (H - rf)) / max(rf, 1e-9)
        w[bl], z[bl], lab[bl] = rf - rf * np.cos(th), rf - rf * np.sin(th), BLEND
        tb = a > a1
        w[tb], z[tb], lab[tb] = rf + a[tb] - a1, 0.0, TABLE
        return w, z, lab
    return prof


def _sphere_profile(R: float, zc: float):
    """A sphere of radius ``R`` centred ``zc`` above the table, from its top down to the table, then
    the table: ``a`` (arc length from the top) -> (radius from the axis, height, label)."""
    phi_r = math.acos(-zc / R)  # where it meets the table
    a_r = R * phi_r

    def prof(a):
        w, z, lab = np.empty_like(a), np.empty_like(a), np.empty(len(a), dtype=np.int8)
        cap = a <= a_r
        phi = a[cap] / R
        w[cap], z[cap], lab[cap] = R * np.sin(phi), zc + R * np.cos(phi), PART
        tb = ~cap
        w[tb], z[tb], lab[tb] = R * math.sin(phi_r) + a[tb] - a_r, 0.0, TABLE
        return w, z, lab
    return prof


def _swept(footprint, profile, half: float, step: float = 1.0, seed: int = 0, noise: float = NOISE_MM):
    """A grid over ``[-half, half]^2`` folded onto ``profile`` about ``footprint``: ``(V, F, face
    labels)``, faces wound so that the normals face up and out (to where it is seen from)."""
    xs = np.arange(-half, half + 0.5 * step, step)
    n = len(xs)
    X, Y = np.meshgrid(xs, xs, indexing="ij")
    Q = np.column_stack([X.ravel(), Y.ravel()])
    a, P, M = footprint(Q)
    w, z, lab = profile(a)
    V = np.column_stack([P + w[:, None] * M, z])
    i, j = np.meshgrid(np.arange(n - 1), np.arange(n - 1), indexing="ij")
    v00 = (i * n + j).ravel()
    v10, v01, v11 = v00 + n, v00 + 1, v00 + n + 1
    F = np.concatenate([np.stack([v00, v10, v11], 1), np.stack([v00, v11, v01], 1)])
    labs = lab[F]  # a face is the part's or the table's when two of its corners are, else the blend's
    flab = np.where((labs == PART).sum(axis=1) >= 2, PART, np.where((labs == TABLE).sum(axis=1) >= 2, TABLE, BLEND))
    if noise > 0:
        V = V + _noise(V, noise, seed)[:, None] * vertex_normals(V, F)
    return V, F, flab


def _noise(P: np.ndarray, sigma: float, seed: int, k: int = 24) -> np.ndarray:
    """A smooth random field of standard deviation ``sigma`` (waves of 3 to 8 mm): a fused scan's
    noise is correlated over a voxel or two, and the folded grid packs vertices closely at the
    corners, where noise drawn per vertex would turn tiny triangles every way."""
    rng = np.random.default_rng(seed)
    d = rng.normal(size=(k, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    w = 2.0 * math.pi / rng.uniform(3.0, 8.0, k)
    ph = rng.uniform(0.0, 2.0 * math.pi, k)
    return sigma * math.sqrt(2.0 / k) * np.cos(P @ (d * w[:, None]).T + ph).sum(axis=1)


def box_on_table(hx=40.0, hy=25.0, H=30.0, rf=1.0, half=110.0, seed=0, step=1.0):
    return _swept(lambda Q: _rounded_rect(Q, hx, hy, 3.0), _box_profile(H, rf), half, step, seed)


def ball_on_table(R=40.0, zc=36.0, half=120.0, seed=1):
    return _swept(_disk, _sphere_profile(R, zc), half, 1.0, seed)


def _moved(V, R, t):
    return V @ R.T + t


def _random_pose(seed: int):
    rng = np.random.default_rng(seed)
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    a, b, c, d = q
    R = np.array([[a * a + b * b - c * c - d * d, 2 * (b * c - a * d), 2 * (b * d + a * c)],
                  [2 * (b * c + a * d), a * a - b * b + c * c - d * d, 2 * (c * d - a * b)],
                  [2 * (b * d - a * c), 2 * (c * d + a * b), a * a - b * b - c * c + d * d]])
    return R, rng.uniform(-300, 300, 3)


def _area(V, F, mask):
    return float(face_areas(V, F)[mask].sum())


def _removed_mask(r, m):
    out = np.zeros(m, dtype=bool)
    out[r.faces] = True
    return out


# --------------------------------------------------------------------------- a box on a table
@pytest.fixture(scope="module")
def box_scene():
    V, F, lab = box_on_table()
    return V, F, lab, find_support(V, F)


def test_a_box_on_a_table_loses_its_table_and_keeps_its_walls(box_scene):
    V, F, lab, r = box_scene
    gone = _removed_mask(r, len(F))
    table, part, blend = lab == TABLE, lab == PART, lab == BLEND
    # the table goes (a stray face the noise turned is a loose crumb within the band: it goes too)
    assert _area(V, F, gone & table) >= 0.999 * _area(V, F, table)
    # the box's walls and top stay, right down to the blend
    assert not (gone & part).any()
    # the blend at the foot (a 1 mm rounding): what faces the table's way within 45° goes, the rest stays
    fn = face_normals(V, F)[0]
    up = fn[:, 2] >= math.cos(math.radians(45))
    assert _area(V, F, gone & blend & ~up) <= 0.1 * _area(V, F, blend & ~up)
    assert _area(V, F, gone & blend & up) >= 0.8 * _area(V, F, blend & up)
    # what is left: the part, from just above the table
    C = V[F].mean(axis=1)
    assert -0.4 < r.lowest_mm <= 0.6 and r.lowest_mm == pytest.approx(float(V[np.unique(F[~gone])][:, 2].min()), abs=0.05)
    assert C[~gone][:, 2].max() > 29.0


def test_the_result_says_what_it_found_and_did(box_scene):
    V, F, lab, r = box_scene
    assert abs(float(r.normal @ [0, 0, 1])) > math.cos(math.radians(0.2)) and r.normal[2] > 0  # towards the part
    assert abs(r.d) < 0.05  # the table is z = 0
    assert r.flatness_mm == pytest.approx(NOISE_MM, rel=0.4)
    assert 0.25 <= r.band_mm <= 0.6 and r.margin_mm == MARGIN_MM
    assert 0.5 < r.share < 0.95 and r.front_share > 0.05 and r.behind_share < 0.01
    gone = _removed_mask(r, len(F))
    assert np.array_equal(r.faces, np.flatnonzero(gone)) and r.n_faces == len(r.faces)
    assert np.array_equal(r.kept, np.flatnonzero(~gone))
    assert r.area_mm2 == pytest.approx(_area(V, F, gone), rel=1e-9)
    assert r.area_left_mm2 == pytest.approx(_area(V, F, ~gone), rel=1e-9)
    d = r.to_dict()
    assert d["n_faces"] == r.n_faces and d["normal"] == pytest.approx(list(r.normal)) and isinstance(d["rule"], str)
    assert d["warnings"] == [] and "faces" not in d  # the face list is the caller's to send
    assert "45" in r.rule and f"{MARGIN_MM:g} mm" in r.rule
    # the same mesh gives the same faces (the preview and the edit must agree)
    assert np.array_equal(find_support(V, F).faces, r.faces)


def test_remove_support_applies_it(box_scene):
    V, F, lab, r = box_scene
    V2, F2, r2 = remove_support(V, F)
    assert np.array_equal(r2.faces, r.faces) and len(F2) == len(F) - r.n_faces
    V3, F3, _ = compact(V, F[r.kept])
    assert np.array_equal(V2, V3) and np.array_equal(F2, F3)


def test_the_margin_trades_table_left_for_part_kept():
    """On a low dome that meets the table at 30° (its foot faces the table's way) the dome is kept
    from the margin up; with a larger margin more of it goes."""
    zc = -40.0 * math.cos(math.radians(30))  # the dome meets the table 30° from the table's plane
    V, F, lab = _swept(_disk, _sphere_profile(40.0, zc), 110.0, 1.0, 2)
    C = V[F].mean(axis=1)
    part = lab == PART
    lost = []
    for m in (0.5, 1.0, 2.0):
        r = find_support(V, F, params=SupportParams(margin_mm=m))
        gone = _removed_mask(r, len(F))
        assert _area(V, F, gone & (lab == TABLE)) >= 0.999 * _area(V, F, lab == TABLE)
        assert not (gone & part & (C[:, 2] > m + 0.5)).any()  # nothing of the dome above the margin
        lost.append(_area(V, F, gone & part))
        assert r.lowest_mm == pytest.approx(m, abs=0.6)
    assert lost[0] < lost[1] < lost[2]


def test_a_ball_on_a_table():
    """A ball sunk 4 mm into the table (as a scan's surface closes the crevice): below its equator
    the ball turns away from the table, so it is kept right down to the crevice."""
    V, F, lab = ball_on_table()
    r = find_support(V, F)
    gone = _removed_mask(r, len(F))
    part, table = lab == PART, lab == TABLE
    # the folded grid meets the table in a sharp crevice of slivers 0.1 mm wide, turned every way:
    # some stay (20 mm²; a fused scan rounds the crevice instead)
    assert _area(V, F, gone & table) >= 0.995 * _area(V, F, table)
    assert _area(V, F, gone & part) <= 1e-3 * _area(V, F, part)
    assert r.lowest_mm < 0.5


def test_a_large_flat_top_stays():
    """The box's top is larger than the table seen around it, so it is the largest plane; nothing
    stands on it and the part lies behind it: it is not the table. The next plane is."""
    V, F, lab = box_on_table(hx=60.0, hy=60.0, H=20.0, half=95.0, seed=3)
    A = face_areas(V, F)
    C = V[F].mean(axis=1)
    top = (lab == PART) & (C[:, 2] > 19.0)
    assert A[top].sum() > A[lab == TABLE].sum()
    r = find_support(V, F)
    gone = _removed_mask(r, len(F))
    assert abs(r.d) < 0.05 and not (gone & top).any() and not (gone & (lab == PART)).any()
    assert _area(V, F, gone & (lab == TABLE)) >= 0.999 * _area(V, F, lab == TABLE)
    assert any("largest" in w for w in r.warnings)  # it says the largest plane was not taken


@pytest.mark.parametrize("what", ["box", "dome", "plate"])
def test_no_table_is_refused_in_words(what):
    if what == "box":  # the box's own top and walls, no table
        V, F, lab = box_on_table(seed=4)
        V, F, _ = compact(V, F[lab == PART])
    elif what == "dome":  # nothing flat at all
        V, F, lab = ball_on_table(seed=5)
        V, F, _ = compact(V, F[lab == PART])
    else:  # a plate's top with a block standing on it, the plate's sides behind: the part's own face (F11)
        Vp, Fp, lp = box_on_table(hx=45.0, hy=30.0, H=8.0, rf=0.5, half=60.0, seed=6)
        Vp, Fp, _ = compact(Vp, Fp[lp == PART])
        Vb, Fb, lb = box_on_table(hx=20.0, hy=15.0, H=25.0, rf=0.5, half=60.0, seed=7)
        Vb, Fb, _ = compact(Vb, Fb[lb == PART])
        V, F = np.vstack([Vp, Vb + [15.0, 0.0, 8.0]]), np.vstack([Fp, Fb + len(Vp)])
    with pytest.raises(SupportRefused) as e:
        find_support(V, F)
    msg = str(e.value)
    assert msg[0].isupper() and msg.endswith(".") and "table" in msg and len(msg) > 40
    assert "Nothing was removed" in msg
    with pytest.raises(SupportRefused):
        remove_support(V, F)


def test_a_tilted_table_at_a_random_pose(box_scene):
    V, F, lab, r0 = box_scene
    R, t = _random_pose(11)
    V2 = _moved(V, R, t)
    r = find_support(V2, F)
    assert float(r.normal @ (R @ [0, 0, 1])) > math.cos(math.radians(0.2))
    assert abs(float(r.normal @ t) + r.d) < 0.05  # the plane passes through the moved origin
    assert np.array_equal(r.faces, r0.faces) or len(np.setxor1d(r.faces, r0.faces)) <= 1e-3 * len(F)


def test_a_table_with_a_hole_and_a_loose_patch_and_a_loose_bit_of_the_part():
    V, F, lab = box_on_table(seed=8)
    C = V[F].mean(axis=1)
    hole = np.linalg.norm(C[:, :2] - [-62.0, 0.0], axis=1) <= 12.0  # a hole in the table
    assert (lab[hole] == TABLE).all()
    V, F, _ = compact(V, F[~hole])
    lab = lab[~hole]
    # a loose patch of table beside it, and a loose bit of the part (a blob above the table)
    Vt, Ft, _ = _swept(lambda Q: _rounded_rect(Q, 1.0, 1.0, 0.5), _box_profile(0.0, 0.0), 10.0, 1.0, 9)
    Vt = Vt + [150.0, 0.0, 0.0]
    Vt[:, 2] = _noise(Vt, NOISE_MM, 10)
    Vs, Fs, _ = box_on_table(hx=3.0, hy=3.0, H=3.0, rf=0.5, half=6.0, seed=12)
    Vs, Fs, _ = compact(Vs, Fs[Vs[Fs].mean(axis=1)[:, 2] > 1.0])
    Vs = Vs + [0.0, 80.0, 20.0]
    n_main, n_patch, nv = len(F), len(Ft), len(V)
    V = np.vstack([V, Vt, Vs])
    F = np.vstack([F, Ft + nv, Fs + nv + len(Vt)])
    r = find_support(V, F)
    gone = _removed_mask(r, len(F))
    assert gone[n_main:n_main + n_patch].all()  # the loose patch of table goes with it
    assert not gone[n_main + n_patch:].any()  # the part's loose bit stays (Remove floaters takes it, or not)
    Fm = F[:n_main]
    assert _area(V, Fm, gone[:n_main] & (lab == TABLE)) >= 0.999 * _area(V, Fm, lab == TABLE)
    assert not (gone[:n_main] & (lab == PART)).any()
    assert r.loose_left == 1  # the blob, said in the result


def test_sigma_and_normals_are_optional_and_filled_vertices_do_not_make_a_table(box_scene):
    V, F, lab, r0 = box_scene
    S = np.full(len(V), 0.1, np.float32)
    r = find_support(V, F, normals=vertex_normals(V, F), sigma=S)
    assert np.array_equal(r.faces, r0.faces)
    # a table that is all made up (filled, sigma 50 mm) is not measured: no table to stand behind
    S2 = S.copy()
    S2[np.unique(F[lab == TABLE])] = 50.0
    with pytest.raises(SupportRefused):
        find_support(V, F, sigma=S2)
    with pytest.raises(ValueError):
        find_support(V, F, sigma=S[:10])
    with pytest.raises(ValueError):
        SupportParams(margin_mm=-1.0)
