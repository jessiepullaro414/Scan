"""ohs.cad.nominal: suggested nominal sizes, for an engineer to approve (docs/FEATURE_PLAN.md, C3).

* The plan's examples: a Ø 14.02 ± 0.03 boss suggests 14, a Ø 14.3 ± 0.03 one nothing (with a
  reason), 89.7° ± 0.2° suggests 90°, two planes 0.3° apart "parallel" only when their ± covers it.
* The bias table: a value suggested only because of a known bias says so and names its section of
  ``VALIDATION.md``; the same reading outside the table's size range, or of a feature the table has
  no number for, is not suggested. A hole with a wall is read at a level, and its bias follows it.
* The order: coarse steps before fine ones, two candidates that both fit are both given (nearest
  first), a value already round gives none.
* No suggestion without a ±, nor for a fit whose warning makes its size unreliable; each such
  answer has a reason.
* Accepted values go into a copy of the shape; the measured shape is never changed.
* The table cannot drift from the document: every bias names a section heading of
  ``VALIDATION.md`` and quotes a sentence that is in that section; every warning the module counts
  is one ``ohs/measure/region.py`` writes.
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import Path

import numpy as np
import pytest

from ohs.cad.nominal import BIASES, UNRELIABLE_WARNINGS, apply_nominal, suggest_fit, suggest_relation
from ohs.measure.fit import CylinderFit, PlaneFit
from ohs.measure.relate import relate

SOFTWARE = Path(__file__).resolve().parents[1]
VALIDATION = SOFTWARE / "VALIDATION.md"
REGION = SOFTWARE / "ohs" / "measure" / "region.py"


# --------------------------------------------------------------------------- helpers
def cylinder(diameter: float, diameter_sigma: float, warnings=()) -> dict:
    """A kept cylinder feature (as ``features.json`` holds it) of the given diameter and ±."""
    r = diameter / 2.0
    shape = {"kind": "cylinder", "point": [0.0, 0.0, 0.0], "axis": [0.0, 0.0, 1.0], "radius_mm": r, "rms_mm": 0.05,
             "n": 800, "radius_sigma_mm": diameter_sigma / 2.0, "axis_sigma_deg": 0.5, "point_sigma_mm": 0.02,
             "diameter_mm": diameter, "diameter_sigma_mm": diameter_sigma}
    return {"kind": "cylinder", "shape": shape, "warnings": list(warnings)}


def hole(diameter: float, diameter_sigma: float, warnings=()) -> dict:
    shape = {"kind": "circle", "center": [5.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "radius_mm": diameter / 2.0,
             "rms_mm": 0.05, "n": 200, "radius_sigma_mm": diameter_sigma / 2.0, "center_sigma_mm": 0.03,
             "diameter_mm": diameter, "diameter_sigma_mm": diameter_sigma}
    return {"kind": "hole", "shape": shape, "warnings": list(warnings)}


def wall_warning(level_mm: float) -> str:
    """The warning ``region.py`` gives a hole read from the flat face around it."""
    return ("this hole has a wall: its edge is taken where the surface leaves the flat face around it "
            f"({level_mm:.2f} mm below it)")


def angle_relation(kinds, deg: float, sigma, parallel=False) -> dict:
    return {"kinds": list(kinds), "parallel": parallel, "notes": [],
            "values": [{"name": "angle", "label": "Angle", "value": deg, "sigma": sigma, "unit": "deg"}]}


def plane(normal, point=(0.0, 0.0, 0.0), sn=0.05, so=0.01) -> PlaneFit:
    n = np.asarray(normal, float)
    return PlaneFit(point=np.asarray(point, float), normal=n / np.linalg.norm(n), rms_mm=0.03, flatness_mm=0.2, n=500,
                    normal_sigma_deg=sn, offset_sigma_mm=so)


def tilted(deg: float) -> list[float]:
    t = math.radians(deg)
    return [math.sin(t), 0.0, math.cos(t)]


def nominals(offer) -> list[float]:
    return [s.nominal for s in offer.suggestions]


def only(offers, value: str):
    found = [o for o in offers if o.value == value]
    assert len(found) == 1, [o.value for o in offers]
    return found[0]


# --------------------------------------------------------------------------- the plan's examples
def test_a_boss_of_14_02_suggests_14():
    for form in (None, "boss"):
        offer = only(suggest_fit(cylinder(14.02, 0.03), form=form), "diameter")
        assert offer.measured == pytest.approx(14.02) and offer.sigma == pytest.approx(0.03)
        assert nominals(offer) == [14.0]
        s = offer.suggestions[0]
        assert s.allowed_by == "2 sigma" and s.bias is None
        assert s.difference == pytest.approx(-0.02)
        assert "14" in s.sentence and s.sentence
        assert offer.reason is None


def test_a_boss_of_14_3_suggests_nothing_with_a_reason():
    offer = only(suggest_fit(cylinder(14.3, 0.03)), "diameter")
    assert offer.suggestions == []
    assert offer.reason and offer.reason_code


def test_89_7_degrees_suggests_90():
    offer = only(suggest_relation(angle_relation(("plane", "plane"), 89.7, 0.2)), "angle")
    assert nominals(offer) == [90.0]
    assert offer.suggestions[0].word == "perpendicular"
    assert offer.suggestions[0].directions == "perpendicular"


def test_two_planes_0_3_degrees_apart_are_parallel_only_when_their_sigma_covers_it():
    loose = relate(plane([0, 0, 1], sn=0.2), plane(tilted(0.3), point=(0, 0, 5), sn=0.2))
    offer = only(suggest_relation(loose), "angle")
    assert offer.measured == pytest.approx(0.3, abs=1e-6)
    assert nominals(offer) == [0.0] and offer.suggestions[0].word == "parallel"

    tight = relate(plane([0, 0, 1], sn=0.05), plane(tilted(0.3), point=(0, 0, 5), sn=0.05))
    offer = only(suggest_relation(tight), "angle")
    assert offer.suggestions == []
    assert offer.reason_code == "no_candidate" and offer.reason


def test_a_relation_dict_and_record_give_the_same():
    r = relate(plane([0, 0, 1], sn=0.2), plane(tilted(0.3), point=(0, 0, 5), sn=0.2))
    a = [o.to_dict() for o in suggest_relation(r)]
    b = [o.to_dict() for o in suggest_relation(r.to_dict())]
    assert a == b


# --------------------------------------------------------------------------- the bias table
def test_a_boss_read_small_is_suggested_only_with_its_known_bias():
    # tracked Ø 14 bosses read 0.15-0.19 mm small in radius: 13.66 is 0.34 below 14, far outside 2σ (0.02)
    offer = only(suggest_fit(cylinder(13.66, 0.01), form="boss", poses="tracked"), "diameter")
    assert nominals(offer) == [14.0]
    s = offer.suggestions[0]
    assert s.allowed_by == "bias"
    assert s.bias is not None
    assert any(src["section"].startswith("The fits' ± on today's engine") for src in s.bias["sources"])
    assert "bias" in s.sentence and "VALIDATION.md" in s.sentence

    # without knowing it is a boss there is no bias to use
    offer = only(suggest_fit(cylinder(13.66, 0.01)), "diameter")
    assert 14.0 not in nominals(offer)


def test_the_same_reading_outside_the_tables_size_range_is_not_suggested():
    # 0.34 below 30: the table has no boss of Ø 30, so only 2σ counts
    offer = only(suggest_fit(cylinder(29.66, 0.01), form="boss", poses="tracked"), "diameter")
    assert 30.0 not in nominals(offer)
    assert offer.suggestions == [] and offer.reason_code == "no_candidate"
    assert "13–15, 15–17 mm only" in offer.reason


def test_a_boss_of_14_3_with_tracking_is_offered_14_5_only_with_the_bias():
    # the plan's "Ø 14.3 suggests nothing" holds for a cylinder not known to be a boss (above); known
    # as a tracked boss, which reads 0.30-0.38 mm small in diameter, 14.5 is within reach of the bias
    offer = only(suggest_fit(cylinder(14.3, 0.03), form="boss", poses="tracked"), "diameter")
    assert nominals(offer) == [14.5] and offer.suggestions[0].allowed_by == "bias"


def test_a_feature_without_a_number_in_the_document_gets_no_bias_and_says_so():
    offer = only(suggest_fit(cylinder(14.02, 0.03), form="bore"), "diameter")
    assert nominals(offer) == [14.0]
    assert "no known bias" in offer.suggestions[0].sentence.lower()
    offer = only(suggest_fit(cylinder(13.66, 0.01), form="bore"), "diameter")
    assert 14.0 not in nominals(offer)


def test_a_kept_cylinder_that_says_it_is_a_boss_gets_the_boss_bias_without_being_told():
    """A kept cylinder carries its form (``features.json``, set by the fit): the app need not pass
    it. A ``form`` given by the caller still wins; a record without one, or with null, is as before."""
    boss = {**cylinder(13.66, 0.01), "form": "boss"}
    offer = only(suggest_fit(boss, poses="tracked"), "diameter")
    assert nominals(offer) == [14.0] and offer.suggestions[0].allowed_by == "bias"
    assert nominals(only(suggest_fit({**cylinder(14.3, 0.03), "form": "boss"}, poses="tracked"), "diameter")) == [14.5]
    # the caller's form wins over the record's
    offer = only(suggest_fit(boss, form="bore", poses="tracked"), "diameter")
    assert 14.0 not in nominals(offer) and "bore" in offer.reason
    # a bore says so, and no bias is used
    offer = only(suggest_fit({**cylinder(14.02, 0.03), "form": "bore"}), "diameter")
    assert nominals(offer) == [14.0] and "a bore" in offer.suggestions[0].sentence
    # null, or no form at all: not known, as before
    for rec in ({**cylinder(13.66, 0.01), "form": None}, cylinder(13.66, 0.01)):
        offer = only(suggest_fit(rec, poses="tracked"), "diameter")
        assert 14.0 not in nominals(offer) and "boss or a bore" in offer.reason
    # a RegionFit-like answer with a form attribute counts too
    from types import SimpleNamespace

    shape = CylinderFit(point=np.zeros(3), axis=np.array([0.0, 0.0, 1.0]), radius_mm=6.83, rms_mm=0.05, n=800,
                        radius_sigma_mm=0.005, axis_sigma_deg=0.5, point_sigma_mm=0.02)
    r = SimpleNamespace(kind="cylinder", fit=shape, faces=np.zeros(0), warnings=[], form="boss")
    assert nominals(only(suggest_fit(r, poses="tracked"), "diameter")) == [14.0]


def test_a_hole_with_a_wall_follows_the_level_it_is_read_at():
    # at the 0.5 mm level a Ø 25 hole with a wall reads 0.57-0.70 mm large at true poses
    at_half = hole(25.62, 0.09, warnings=[wall_warning(0.5)])
    offer = only(suggest_fit(at_half, poses="true"), "diameter")
    assert nominals(offer) == [25.0] and offer.suggestions[0].allowed_by == "bias"
    assert any(src["section"].startswith("The Hole tool on holes with a wall")
               for src in offer.suggestions[0].bias["sources"])
    # read 1 mm below the face the same hole reads 24.9: 25.62 is no longer explained by the bias
    at_one = hole(25.62, 0.09, warnings=[wall_warning(1.0)])
    offer = only(suggest_fit(at_one, poses="true"), "diameter")
    assert 25.0 not in nominals(offer)
    # a level the document has no number for: no bias
    at_other = hole(25.62, 0.09, warnings=[wall_warning(0.73)])
    offer = only(suggest_fit(at_other, poses="true"), "diameter")
    assert 25.0 not in nominals(offer)


def test_a_thin_wall_seen_from_both_sides_reads_thick_and_its_bias_is_known():
    rel = {"kinds": ["plane", "plane"], "parallel": True, "notes": [],
           "values": [{"name": "angle", "label": "Angle (parallel)", "value": 0.0, "sigma": 0.05, "unit": "deg"},
                      {"name": "distance", "label": "Distance", "value": 2.45, "sigma": 0.03, "unit": "mm"}]}
    offer = only(suggest_relation(rel, form="wall", poses="tracked"), "distance")
    assert nominals(offer) == [2.0] and offer.suggestions[0].allowed_by == "bias"
    offer = only(suggest_relation(rel), "distance")
    assert 2.0 not in nominals(offer)


def test_parallel_axes_at_no_distance_are_coaxial_only_when_their_angle_allows_parallel():
    def axes(deg, deg_sigma):
        return {"kinds": ["cylinder", "cylinder"], "parallel": True, "notes": [],
                "values": [{"name": "angle", "label": "Angle of the axes (parallel)", "value": deg, "sigma": deg_sigma,
                            "unit": "deg"},
                           {"name": "distance", "label": "Distance between the axes", "value": 0.03, "sigma": 0.03,
                            "unit": "mm"}]}

    offers = suggest_relation(axes(0.1, 0.1))
    assert only(offers, "angle").suggestions[0].word == "parallel"
    d = only(offers, "distance").suggestions
    assert [x.nominal for x in d] == [0.0] and d[0].word == "coaxial"
    d = only(suggest_relation(axes(1.5, 0.1)), "distance").suggestions
    assert [x.nominal for x in d] == [0.0] and d[0].word is None


# --------------------------------------------------------------------------- the order of the steps
def test_coarse_steps_come_before_fine_ones():
    assert nominals(only(suggest_fit(cylinder(14.2, 0.15)), "diameter")) == [14.0]  # not 14.2, not 14.0 and 14.5
    s = only(suggest_fit(cylinder(14.46, 0.03)), "diameter").suggestions
    assert [x.nominal for x in s] == [14.5] and s[0].step == 0.5
    s = only(suggest_fit(cylinder(14.27, 0.02)), "diameter").suggestions
    assert [x.nominal for x in s] == pytest.approx([14.3]) and s[0].step == 0.1
    # angles: 0 and 90 first, then 30, 45, 60, then whole degrees
    assert nominals(only(suggest_relation(angle_relation(("plane", "plane"), 44.8, 0.15)), "angle")) == [45.0]
    assert nominals(only(suggest_relation(angle_relation(("plane", "plane"), 37.1, 0.1)), "angle")) == [37.0]


def test_two_candidates_that_both_fit_are_both_given_nearest_first():
    s = only(suggest_fit(cylinder(14.45, 0.3)), "diameter").suggestions
    assert [x.nominal for x in s] == [14.0, 15.0]
    assert abs(s[0].difference) < abs(s[1].difference)
    s = only(suggest_fit(cylinder(14.5, 0.3)), "diameter").suggestions
    assert sorted(x.nominal for x in s) == [14.0, 15.0]


def test_a_value_already_round_gives_none():
    offer = only(suggest_fit(cylinder(14.0, 0.03)), "diameter")
    assert offer.suggestions == [] and offer.reason_code == "already_round" and offer.reason
    offer = only(suggest_fit(cylinder(14.5, 0.03)), "diameter")
    assert offer.suggestions == [] and offer.reason_code == "already_round"
    offer = only(suggest_relation(angle_relation(("plane", "plane"), 90.0, 0.1)), "angle")
    assert offer.suggestions == [] and offer.reason_code == "already_round"


def test_a_sigma_too_large_to_tell_the_steps_apart_gives_none():
    offer = only(suggest_fit(cylinder(14.4, 1.5)), "diameter")
    assert offer.suggestions == [] and offer.reason_code == "too_many" and offer.reason


# --------------------------------------------------------------------------- when not to suggest
def test_no_sigma_gives_none_with_the_reason():
    # an angle with a hole's direction has no ± (relate gives None)
    offer = only(suggest_relation(angle_relation(("hole", "plane"), 89.9, None)), "angle")
    assert offer.suggestions == [] and offer.reason_code == "no_sigma" and offer.reason
    f = cylinder(14.02, 0.03)
    del f["shape"]["radius_sigma_mm"], f["shape"]["diameter_sigma_mm"]
    offer = only(suggest_fit(f), "diameter")
    assert offer.suggestions == [] and offer.reason_code == "no_sigma"


def test_a_warning_that_counts_gives_none_and_one_that_does_not_is_passed():
    bad = hole(10.02, 0.05, warnings=["the hole's edge is not round (RMS 0.40 mm): a slot or a damaged edge?"])
    offer = only(suggest_fit(bad), "diameter")
    assert offer.suggestions == [] and offer.reason_code == "warning"
    assert "not round" in offer.reason
    ok = hole(10.02, 0.05, warnings=["small hole: 20 points on its edge"])
    assert nominals(only(suggest_fit(ok), "diameter")) == [10.0]
    # a relation whose fit carries such a warning
    rel = angle_relation(("plane", "plane"), 89.7, 0.2)
    warn = "the points scatter 2.1 times more than their sigma (RMS 0.300 mm): a curved or rough surface?"
    offer = only(suggest_relation(rel, warnings=[warn]), "angle")
    assert offer.suggestions == [] and offer.reason_code == "warning"


def test_every_warning_counted_is_one_region_writes():
    source = REGION.read_text(encoding="utf-8")
    for marker, _ in UNRELIABLE_WARNINGS:
        assert marker in source, marker


def test_a_plane_alone_has_no_size_to_suggest():
    assert suggest_fit({"kind": "plane", "shape": plane([0, 0, 1]).to_dict(), "warnings": []}) == []


def test_fits_their_dicts_and_kept_features_go_in():
    fit = CylinderFit(point=np.zeros(3), axis=np.array([0.0, 0.0, 1.0]), radius_mm=7.01, rms_mm=0.05, n=800,
                      radius_sigma_mm=0.015, axis_sigma_deg=0.5, point_sigma_mm=0.02)
    for x in (fit, fit.to_dict(), {"kind": "cylinder", "shape": fit.to_dict(), "warnings": []}):
        assert nominals(only(suggest_fit(x), "diameter")) == [14.0]
    assert nominals(only(suggest_fit(fit, value="radius"), "radius")) == [7.0]


def test_an_offer_is_plain_json():
    import json

    offers = suggest_fit(cylinder(13.66, 0.01), form="boss", poses="tracked")
    d = json.loads(json.dumps([o.to_dict() for o in offers]))
    s = d[0]["suggestions"][0]
    for key in ("nominal", "difference", "step", "word", "allowed_by", "bias", "sentence"):
        assert key in s
    for key in ("value", "label", "unit", "measured", "sigma", "suggestions", "reason_code", "reason"):
        assert key in d[0]


# --------------------------------------------------------------------------- applying accepted values
def test_accepted_values_go_into_a_copy_and_the_measured_shape_stays():
    f = cylinder(14.02, 0.03)
    before = copy.deepcopy(f["shape"])
    out = apply_nominal(f["shape"], diameter_mm=14.0)
    assert f["shape"] == before
    assert out["diameter_mm"] == 14.0 and out["radius_mm"] == 7.0
    assert out["nominal"] == {"diameter_mm": 14.0}
    out = apply_nominal(f["shape"], radius_mm=7.5)
    assert out["diameter_mm"] == 15.0 and out["radius_mm"] == 7.5
    with pytest.raises(ValueError):
        apply_nominal(f["shape"], diameter_mm=14.0, radius_mm=7.5)


def test_a_direction_is_snapped_only_when_both_are_given():
    p = plane(tilted(0.3)).to_dict()
    before = copy.deepcopy(p)
    out = apply_nominal(p, direction="parallel", to=[0.0, 0.0, 1.0])
    assert p == before
    assert np.allclose(out["normal"], [0.0, 0.0, 1.0], atol=1e-12)
    out = apply_nominal(p, direction="perpendicular", to=[1.0, 0.0, 0.0])
    n = np.asarray(out["normal"])
    assert abs(n @ [1.0, 0.0, 0.0]) < 1e-12 and np.linalg.norm(n) == pytest.approx(1.0)
    c = cylinder(14.0, 0.03)["shape"]
    out = apply_nominal(c, direction="parallel", to=[0.0, 0.0, -1.0])
    assert np.allclose(out["axis"], [0.0, 0.0, 1.0])  # the sign of the measured direction is kept
    with pytest.raises(ValueError):
        apply_nominal(p, direction="parallel")
    with pytest.raises(ValueError):
        apply_nominal(p, to=[0.0, 0.0, 1.0])


# --------------------------------------------------------------------------- the table against the document
def _sections(text: str) -> dict[str, str]:
    """Each ``## `` heading of the document and its text up to the next one."""
    parts = re.split(r"^## ", text, flags=re.M)
    return {p.split("\n", 1)[0].strip(): p for p in parts[1:]}


def _plain(s: str) -> str:
    return " ".join(s.split())


def test_every_bias_names_a_section_of_validation_md_and_quotes_it():
    sections = _sections(VALIDATION.read_text(encoding="utf-8"))
    assert BIASES
    for bias in BIASES:
        assert bias.sources, bias
        for src in bias.sources:
            heads = [h for h in sections if h.startswith(src.section)]
            assert len(heads) == 1, f"{src.section!r} is not one heading of VALIDATION.md"
            body = _plain(sections[heads[0]])
            if src.part:
                assert f"**{src.part}" in body, f"{src.part!r} is not a part of {src.section!r}"
            for quote in src.quotes:
                assert _plain(quote) in body, f"{quote!r} is not in {src.section!r}"
