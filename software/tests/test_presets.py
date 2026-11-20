"""Presets by object type, the engine side (FEATURE_PLAN P2): :mod:`ohs.app.presets`.

* exactly the two presets P1's measurements support: General (names nothing) and "Thin sheet metal
  and small parts" (the voxel, 0.5 mm, and nothing else), its card with P1's numbers;
* a preset can name only the values the app lets the user set; anything else is refused in plain words;
* the user's own presets are kept like recipes (``<data_dir>/presets/``), a file edited into nonsense acts
  as General;
* a scan's record: a value changed by hand wins over the preset and is recorded as changed; a preset
  whose file is gone acts as General.
"""
from __future__ import annotations

import json

import pytest

from ohs.app.presets import (BUILTIN_PRESETS, GENERAL_ID, KEYS, THIN_ID, PresetError, PresetStore, acting,
                             changed_keys, scan_record, validate_values)


def refused(fn, status: int, *needles: str) -> PresetError:
    with pytest.raises(PresetError) as e:
        fn()
    assert e.value.status == status, (e.value.status, str(e.value))
    text = (str(e.value) + " " + e.value.hint).lower()
    assert e.value.hint and all(n.lower() in text for n in needles), text
    assert "—" not in text  # plain words, no em dashes
    return e.value


def test_exactly_the_two_measured_presets():
    assert [p["id"] for p in BUILTIN_PRESETS] == [GENERAL_ID, THIN_ID]
    general, thin = BUILTIN_PRESETS
    assert general["name"] == "General" and general["values"] == {}
    assert thin["name"] == "Thin sheet metal and small parts" and thin["values"] == {"voxel_mm": 0.5}
    card = " ".join(v for v in thin["card"].values() if v)
    for number in ("0.5 mm", "1 mm", "1.03 to 1.08", "1.32 to 1.45", "2.18 to 2.43", "1.70", "1.71 to 1.90",
                   "2.53 to 2.66", "1.2 to 2.4 times", "graphics memory", "large panel"):
        assert number in card, number
    for p in BUILTIN_PRESETS:
        assert "—" not in json.dumps(p, ensure_ascii=False)


def test_values_are_checked_as_the_app_checks_them():
    assert validate_values({"voxel_mm": 0.5, "quality": "full", "thin_walls": "on", "mode": "markers",
                            "check_tolerance_mm": 0.1, "recipe": "body-panel"}) == {
        "mode": "markers", "quality": "standard", "voxel_mm": 0.5, "thin_walls": "on", "recipe": "body-panel",
        "check_tolerance_mm": 0.1}
    assert validate_values({"voxel_mm": None}) == {} and validate_values(None) == {}
    refused(lambda: validate_values({"colour": "red"}), 422, "no value called 'colour'", "voxel_mm")
    refused(lambda: validate_values({"depth_range": [150, 450]}), 422, "depth range is not a setting")
    refused(lambda: validate_values({"gap_guard_px": 4}), 422, "gap guard")
    refused(lambda: validate_values({"voxel_mm": 0.1}), 422, "at least 0.25 mm", "at most 10 mm")
    refused(lambda: validate_values({"voxel_mm": "fine"}), 422, "must be a number")
    refused(lambda: validate_values({"voxel_mm": True}), 422, "must be a number")
    refused(lambda: validate_values({"check_tolerance_mm": 0}), 422, "above 0 mm")
    refused(lambda: validate_values({"check_tolerance_mm": 101}), 422, "at most 100 mm")
    refused(lambda: validate_values({"quality": "ultra"}), 422, "standard, best or preview")
    refused(lambda: validate_values({"thin_walls": "yes"}), 422, "auto, on or off")
    refused(lambda: validate_values({"mode": "fast"}), 422, "markerless or markers")
    refused(lambda: validate_values({"recipe": "Not An Id"}), 422, "recipe")
    refused(lambda: validate_values({"recipe": "gone"}, recipe_exists=lambda r: False), 422, "no recipe 'gone'")
    refused(lambda: validate_values([1, 2]), 422, "object")


def test_the_users_presets_are_kept_like_recipes(tmp_path):
    store = PresetStore(tmp_path / "presets")
    assert [p["id"] for p in store.list()] == [GENERAL_ID, THIN_ID]
    p = store.create("  Brake   discs ", "Cast iron, 0.75 mm", {"voxel_mm": 0.75, "thin_walls": "on"})
    assert p["name"] == "Brake discs" and p["builtin"] is False and p["values"] == {"voxel_mm": 0.75, "thin_walls": "on"}
    assert (tmp_path / "presets" / f"{p['id']}.json").is_file()
    assert [q["id"] for q in store.list()] == [GENERAL_ID, THIN_ID, p["id"]]
    assert store.get(p["id"])["values"] == p["values"] and store.find(THIN_ID)["values"] == {"voxel_mm": 0.5}
    refused(lambda: store.create("", None, {}), 400, "name")
    refused(lambda: store.create("x" * 81, None, {}), 400, "at most 80")
    refused(lambda: store.create("general", None, {}), 409, "built-in")
    refused(lambda: store.create("Odd", None, {"depth_range": [1, 2]}), 422, "depth range")
    refused(lambda: store.delete(THIN_ID), 409, "built-in")
    refused(lambda: store.delete("nope-1234"), 404, "no preset")
    refused(lambda: store.delete("../etc"), 404, "no preset")
    refused(lambda: store.get("nope-1234"), 404, "GET /api/presets")
    # a file edited by hand into values the app does not take: listed, but its values do nothing
    bad = tmp_path / "presets" / "odd-0000.json"
    bad.write_text(json.dumps({"id": "odd-0000", "name": "Odd", "values": {"voxel_mm": 0.01}}), encoding="utf-8")
    assert store.get("odd-0000")["values"] == {}
    store.delete(p["id"])
    assert store.find(p["id"]) is None and p["id"] not in [q["id"] for q in store.list()]


def test_a_scans_record():
    thin = PresetStore.builtin(THIN_ID)
    # General names nothing: nothing acts, every value is General's (None) but the scan's own mode
    g = scan_record(None, None, None, mode="markerless")
    assert g["id"] == GENERAL_ID and g["name"] == "General" and g["changed"] == [] and g["note"] is None
    assert g["values"] == {k: ("markerless" if k == "mode" else None) for k in KEYS}
    assert acting(None, None) == {} and acting(PresetStore.builtin(GENERAL_ID), None) == {}
    # the thin preset names the voxel
    t = scan_record(THIN_ID, thin, None, mode="markerless")
    assert t["name"] == "Thin sheet metal and small parts" and t["values"]["voxel_mm"] == 0.5 and t["changed"] == []
    assert acting(thin, None) == {"voxel_mm": 0.5}
    # a value changed by hand wins and is recorded as changed; one equal to the preset's is no change
    h = scan_record(THIN_ID, thin, {"voxel_mm": 0.75, "thin_walls": "on"}, mode="markers")
    assert h["values"]["voxel_mm"] == 0.75 and h["values"]["thin_walls"] == "on" and h["values"]["mode"] == "markers"
    assert h["changed"] == ["voxel_mm", "thin_walls"] and h["preset_values"] == {"voxel_mm": 0.5}
    assert acting(thin, {"voxel_mm": 0.75}) == {"voxel_mm": 0.75}
    assert changed_keys({"voxel_mm": 0.5}, {"voxel_mm": 0.5}) == []
    # a preset whose file is gone: General, with the scan's own hand changes, and a note saying so
    gone = scan_record("brake-discs-1a2b", None, {"thin_walls": "on"}, mode="markerless")
    assert gone["preset_values"] == {} and gone["values"]["voxel_mm"] is None and gone["values"]["thin_walls"] == "on"
    assert "no longer in the app" in gone["note"] and "General" in gone["note"]
