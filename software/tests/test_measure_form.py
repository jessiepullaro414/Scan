"""ohs.measure.region: a fitted cylinder says whether it is a boss (the surface faces away from its
axis: a shaft, a pin) or a bore (a hole's wall, the surface faces the axis): ``RegionFit.form``.

On the meshes of ``test_measure_region.py`` (the plate with the boss r 7 and the bore r 6, with
0.02-0.05 mm of noise, and a boss seen from one side):

* the boss reads "boss" and the bore "bore" from clicks at several places on each; the same wall
  with its faces turned the other way reads the other form;
* a plane and a hole have no form; a region whose faces face both ways has none, and says why;
* the form is in ``to_dict`` only on request (``with_form``): today's answer keeps its keys;
* the fits' answers are bit-identical to those of the code before the form existed (the hashes
  below were recorded from it: the full ``to_dict(with_extent=True)``, its keys of that day).
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pytest

from ohs.measure import fit_at
from test_measure_region import BORE, BOSS, arc_wall, bracket, nearest_face, noisy, plate

KEYS = ["kind", "fit", "faces", "edge", "warnings", "tolerance_mm", "angle_tol_deg", "mean_sigma_mm",
        "correlated_points", "extent"]  # the answer's keys before the form existed
CLICKS = [(0.3, 0.25), (2.0, 0.5), (4.0, 0.75)]  # (angle round the axis in rad, share of the height)
# recorded from the code before the form existed: sha256 of the answer (first 16 hex) and the radius
BEFORE = {
    "boss 0.3 0.25": ("f45460a828cac5a7", 6.999004415678423),
    "boss 2.0 0.5": ("810cf71dbbad12ed", 6.999004415679543),
    "boss 4.0 0.75": ("03935b0c50319ad8", 6.999004415678541),
    "bore 0.3 0.25": ("25a846ad4bbc59b8", 5.999622491741518),
    "bore 2.0 0.5": ("040ccf4c19241c0a", 5.9998842165838555),
    "bore 4.0 0.75": ("f2dfe95551b49788", 5.999627257190349),
    "plane": ("6e349c195a76af3e", None),
    "hole with a wall": ("882bd54e1fa53b7d", 5.998921575481812),
    "hole": ("206f8ab95992e081", 12.494272178712512),
    "arc": ("4d415347482ca2d5", 6.858281700468609),
}


def digest(r) -> tuple[str, float | None]:
    d = r.to_dict(with_extent=True)
    d = {k: d[k] for k in KEYS}
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16], d["fit"].get("radius_mm")


@pytest.fixture(scope="module")
def mesh():
    V, F, labels = bracket()
    Vn, S = noisy(V, 1)
    return Vn, F, S, labels


def click(V, F, part: str, t: float, frac: float):
    x, y, r, h = BOSS if part == "boss" else BORE
    z = frac * h if part == "boss" else -frac * h
    return nearest_face(V, F, (x + r * math.cos(t), y + r * math.sin(t), z))


@pytest.mark.parametrize("part", ["boss", "bore"])
@pytest.mark.parametrize("t,frac", CLICKS)
def test_the_boss_reads_boss_and_the_bore_bore(mesh, part, t, frac):
    V, F, S, labels = mesh
    face, point = click(V, F, part, t, frac)
    r = fit_at(V, F, face, point, "cylinder", sigma=S)
    assert set(labels[r.faces]) == {part}
    assert r.form == part
    assert not any("boss or a bore" in w for w in r.warnings)
    assert digest(r) == BEFORE[f"{part} {t} {frac}"]  # the fit itself is as it was


def test_a_boss_seen_from_one_side_and_its_faces_turned_over():
    """A boss seen over 80 degrees of its round, its points 0.1 mm off and its sigma pessimistic:
    a boss. The same wall with every face's corners in the other order (its normals facing the
    axis, as a bore's would) reads "bore": the form is the surface's side, not the curvature."""
    V, F = arc_wall(7.0, 80.0, 8.0, 0.5, noise=0.1, seed=5)
    S = np.full(len(V), 1.5)
    face, point = nearest_face(V, F, (7.0, 0.0, 4.0))
    r = fit_at(V, F, face, point, "cylinder", sigma=S)
    assert digest(r) == BEFORE["arc"]
    assert r.form == "boss"  # arc_wall's corners run so that its normals face away from the axis
    flipped = fit_at(V, F[:, [0, 2, 1]], face, point, "cylinder", sigma=S)
    assert flipped.form == "bore"
    assert flipped.fit.radius_mm == pytest.approx(r.fit.radius_mm, abs=1e-9)


def test_a_plane_and_a_hole_have_no_form(mesh):
    V, F, S, _ = mesh
    face, point = nearest_face(V, F, (0.0, 15.0, 0.0))
    p = fit_at(V, F, face, point, "plane", sigma=S)
    assert p.form is None and digest(p) == BEFORE["plane"]
    face, point = nearest_face(V, F, (BORE[0] + BORE[2] + 3.0, BORE[1], 0.0))
    w = fit_at(V, F, face, point, "hole", sigma=S)
    assert w.form is None and digest(w) == BEFORE["hole with a wall"]
    Vh, Fh, _ = plate(80.0, 80.0, 0.5, [(5.0, -3.0, 12.5)])
    Vh, Sh = noisy(Vh, 5)
    face, point = nearest_face(Vh, Fh, (21.0, -3.0, 0.0))
    h = fit_at(Vh, Fh, face, point, "hole", sigma=Sh)
    assert h.form is None and digest(h) == BEFORE["hole"]


def test_a_region_whose_faces_face_both_ways_has_no_form(mesh):
    """``cylinder_form`` on the boss's region, its faces as they are, then with every other band of
    faces turned over (half facing out, half in): no clear majority, so no form, and the reason."""
    from ohs.measure.region import FORM_MAJORITY, cylinder_form

    V, F, S, _ = mesh
    face, point = click(V, F, "boss", 2.0, 0.5)
    r = fit_at(V, F, face, point, "cylinder", sigma=S)
    form, why = cylinder_form(V, F, r.faces, r.fit)
    assert form == "boss" and why is None
    G = F.copy()
    c = V[F[r.faces]].mean(axis=1)
    turn = r.faces[np.floor(np.degrees(np.arctan2(c[:, 1] - BOSS[1], c[:, 0] - BOSS[0])) / 30.0) % 2 == 0]
    G[turn] = G[turn][:, [0, 2, 1]]
    form, why = cylinder_form(V, G, r.faces, r.fit)
    assert form is None
    assert "boss or a bore" in why and "%" in why
    # a clear majority is enough: a tenth of the faces turned over (noise, a fold) leaves a boss
    assert FORM_MAJORITY <= 0.9
    G = F.copy()
    few = r.faces[:: 10]
    G[few] = G[few][:, [0, 2, 1]]
    assert cylinder_form(V, G, r.faces, r.fit) == ("boss", None)


def test_the_form_is_in_the_answer_only_on_request(mesh):
    V, F, S, _ = mesh
    face, point = click(V, F, "bore", 2.0, 0.5)
    r = fit_at(V, F, face, point, "cylinder", sigma=S)
    assert "form" not in r.to_dict() and "form" not in r.to_dict(with_extent=True)
    d = r.to_dict(with_form=True)
    assert d["form"] == "bore" and json.loads(json.dumps(d)) == d
    assert set(d) - set(r.to_dict()) == {"form"}
    face, point = nearest_face(V, F, (0.0, 15.0, 0.0))
    assert fit_at(V, F, face, point, "plane", sigma=S).to_dict(with_form=True)["form"] is None
