"""Did the asked areas get better? (FEATURE_PLAN N2, :mod:`ohs.guide.passes`)

An asked area of the first scan is found on the merged mesh by where it lies, face by face: the
nearest face of the merged mesh within ``MATCH_MM`` that faces the same way. Thin parts must come out
right: a plate 0.3 mm thick lies on its table (a surface facing the same way 0.3 mm below), or is
seen from both sides (its other side 0.3 mm away, facing the other way); in both the area on the top
side is judged by the top side of the merged mesh, never by the surface under it.
"""
from __future__ import annotations

import numpy as np
import pytest

from ohs.guide.passes import MATCH_MM, area_progress, area_samples

THIN = 0.3


def _grid(x0, y0, x1, y1, z, step=1.0, up=True, shift=0.0):
    """A flat square of triangles at height ``z`` facing up (or down), its grid moved by ``shift`` mm (the
    same square, its corners moved)."""
    xs = np.arange(x0 + shift, x1 + shift + 1e-9, step)
    ys = np.arange(y0 + shift, y1 + shift + 1e-9, step)
    gx, gy = np.meshgrid(xs, ys)
    V = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, float(z))], axis=1)
    nx = len(xs)
    F = []
    for j in range(len(ys) - 1):
        for i in range(nx - 1):
            a, b, c, d = j * nx + i, j * nx + i + 1, (j + 1) * nx + i, (j + 1) * nx + i + 1
            F += [(a, b, d), (a, d, c)] if up else [(a, d, b), (a, c, d)]
    return V, np.asarray(F, dtype=np.int64)


def _join(*parts):
    """Meshes into one; returns ``(V, F, [face range of each part])``."""
    Vs, Fs, ranges, nv, nf = [], [], [], 0, 0
    for V, F in parts:
        Vs.append(V)
        Fs.append(F + nv)
        ranges.append(np.arange(nf, nf + len(F)))
        nv += len(V)
        nf += len(F)
    return np.vstack(Vs), np.vstack(Fs), ranges


def _sigma(V, ranges_F, F, values):
    """Per vertex sigma: each part's value (by the vertices its faces use)."""
    S = np.full(len(V), 0.05)
    for r, s in zip(ranges_F, values):
        S[np.unique(F[r])] = s
    return S


GOOD, BAD, TOL = 0.05, 0.5, 0.3  # 2 x 0.05 passes 0.3 mm; 2 x 0.5 does not


def _first_plate_on_table():
    """The first scan: a table, and a 0.3 mm plate on it whose top is weak (the asked area)."""
    V, F, (table, top) = _join(_grid(-30, -30, 50, 50, 0.0), _grid(0, 0, 20, 20, THIN))
    return V, F, top


@pytest.mark.parametrize("top_good", [True, False])
def test_a_thin_plate_on_its_table_is_judged_by_its_own_top(top_good):
    V1, F1, top = _first_plate_on_table()
    samples = area_samples(V1, F1, [top])
    # the merged mesh: made again, at another resolution (2 mm cells, half a cell over), so its plate's face centres
    # lie up to a millimetre from the first one's; the table runs on under the plate, 0.3 mm below, facing the same
    # way, its face centres straight under the first plate's. (Where the merged plate does not reach as far as the
    # first one did, the table under it would stand for it, up to MATCH_MM from its rim: the rule's limit.)
    V, F, (table, top2) = _join(_grid(-30, -30, 50, 50, 0.0), _grid(-2, -2, 20, 20, THIN, step=2.0, shift=1.0))
    S = _sigma(V, [table, top2], F, [BAD if top_good else GOOD, GOOD if top_good else BAD])
    (r,) = area_progress(samples, V, F, S, None, TOL)
    assert r["found_share"] == pytest.approx(1.0)
    if top_good:
        assert r["state"] == "passes" and r["share_ok"] > 0.95
    else:  # the table under it passes; the plate's top does not, and that is what is said
        assert r["state"] == "weak" and r["share_ok"] < 0.05


@pytest.mark.parametrize("top_good", [True, False])
def test_a_thin_plate_seen_from_both_sides_is_judged_by_the_side_asked_for(top_good):
    # the first scan saw the top (weak); the merged mesh has both sides, 0.3 mm apart, facing away from each other.
    # The bottom's face centres lie straight under the first top's, nearer than the merged top's (2 mm cells, moved):
    # without the normal test the bottom would stand for the top.
    V1, F1, (top,) = _join(_grid(0, 0, 20, 20, THIN))
    samples = area_samples(V1, F1, [top])
    V, F, (top2, bottom) = _join(_grid(-2, -2, 20, 20, THIN, step=2.0, shift=1.0), _grid(0, 0, 20, 20, 0.0, up=False))
    S = _sigma(V, [top2, bottom], F, [GOOD if top_good else BAD, BAD if top_good else GOOD])
    (r,) = area_progress(samples, V, F, S, None, TOL)
    assert r["found_share"] == pytest.approx(1.0)
    assert r["state"] == ("passes" if top_good else "weak"), r


def test_a_thin_plate_whose_asked_side_is_not_in_the_merge_is_not_judged_by_its_other_side():
    # the merge kept only the bottom of the plate, 0.3 mm under the asked top and well measured: the top is "not
    # found", never "passes" on the strength of the other side
    V1, F1, (top,) = _join(_grid(0, 0, 20, 20, THIN))
    samples = area_samples(V1, F1, [top])
    V, F = _grid(0, 0, 20, 20, 0.0, up=False)
    (r,) = area_progress(samples, V, F, np.full(len(V), GOOD), None, TOL)
    assert r["state"] == "not_found" and r["found_share"] == 0.0, r


def test_part_of_an_area_passing_and_an_area_not_in_the_merge():
    V1, F1, (left, right, gone) = _join(_grid(0, 0, 10, 10, 0.0), _grid(10, 0, 20, 10, 0.0), _grid(100, 0, 110, 10, 0.0))
    both = np.concatenate([left, right])
    samples = area_samples(V1, F1, [both, gone])
    V, F, (l2, r2) = _join(_grid(0, 0, 10, 10, 0.0), _grid(10, 0, 20, 10, 0.0))  # the far square is not in the merge
    S = _sigma(V, [l2, r2], F, [GOOD, BAD])
    a, b = area_progress(samples, V, F, S, None, TOL)
    assert a["state"] == "partly" and 0.4 < a["share_ok"] < 0.6 and a["found_share"] == pytest.approx(1.0)
    assert b["state"] == "not_found" and b["found_share"] == 0.0 and b["share_ok"] == 0.0
    # a surface farther away than the rule's reach is not found either
    far = _grid(0, 0, 20, 10, MATCH_MM + 0.5)
    (c, _) = area_progress(samples, *far, np.full(len(far[0]), GOOD), None, TOL)
    assert c["state"] == "not_found"


def test_filled_faces_and_a_mesh_without_sigma():
    V1, F1, (sq,) = _join(_grid(0, 0, 10, 10, 0.0))
    samples = area_samples(V1, F1, [sq])
    (r,) = area_progress(samples, V1, F1, np.full(len(V1), GOOD), np.ones(len(F1), dtype=bool), TOL)
    assert r["state"] == "weak"  # made up by hole filling: not measured, so it does not pass
    (r,) = area_progress(samples, V1, F1, None, None, TOL)
    assert r["state"] == "unknown" and r["share_ok"] is None


def test_faces_not_on_the_mesh_are_refused():
    V1, F1, _ = _join(_grid(0, 0, 10, 10, 0.0))
    with pytest.raises(ValueError, match="faces this mesh does not have"):
        area_samples(V1, F1, [np.array([len(F1)])])


def test_before_and_after_on_the_surface_both_meshes_hold():
    """The merge left the table out (the part was moved): the whole merged mesh is the plate alone. On the whole
    meshes the share that passes "falls" (the well measured table is gone); on the surface both hold, the plate,
    it rose from none to all. The table is said as missing, not as worse."""
    from ohs.guide.passes import common_surface

    # the table as a scan sees it: everywhere but under the plate
    V1, F1, (t1, t2, t3, t4, top) = _join(_grid(-30, -30, 0, 50, 0.0), _grid(20, -30, 50, 50, 0.0), _grid(0, -30, 20, 0, 0.0),
                                          _grid(0, 20, 20, 50, 0.0), _grid(0, 0, 20, 20, THIN))
    table = np.concatenate([t1, t2, t3, t4])
    S1 = _sigma(V1, [table, top], F1, [GOOD, BAD])
    V2, F2 = _grid(0, 0, 20, 20, THIN, step=2.0)  # the plate only, made again at 2 mm
    S2 = np.full(len(V2), GOOD)
    r = common_surface((V1, F1, S1, None), (V2, F2, S2, None), TOL)
    total = 80.0 * 80.0  # the table round the plate (6,000 mm2) and the plate's top (400)
    # found: the plate, and the table's rim round it up to MATCH_MM away (the rule's limit, module docstring)
    rim = 4 * 20.0 * MATCH_MM + 4 * MATCH_MM ** 2
    assert 400.0 / total - 0.005 <= r["found_share"] <= (400.0 + rim) / total + 0.005, r
    assert r["missing_mm2"] >= total - 400.0 - rim - 30.0
    found = r["found_share"] * total
    assert r["before_ok"] <= (found - 400.0) / found + 0.01, r  # before, only the table's rim passed (the plate did not)
    assert r["after_ok"] > 0.98, r  # the plate (and the rim that stands on it) passes now
    # without sigma on either side nothing is said to pass or not
    r = common_surface((V1, F1, None, None), (V2, F2, S2, None), TOL)
    assert r["before_ok"] is None and r["after_ok"] > 0.98
