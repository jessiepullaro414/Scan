"""Kept features (``ohs.measure.features``): the records of ``scans/<sid>/features.json``, their
validation, the atomic write, and the region side files.
"""
from __future__ import annotations

import copy
import json

import numpy as np
import pytest

import ohs.measure.features as kf


def plane_feature(fid: str = "f-1", name: str = "Fit 1", version: int = 3) -> dict:
    return {"id": fid, "name": name, "kind": "plane",
            "shape": {"kind": "plane", "point": [1.0, 2.0, 3.0], "normal": [0.0, 0.0, 1.0], "rms_mm": 0.05,
                      "flatness_mm": 0.3, "n": 1200, "normal_sigma_deg": 0.02, "offset_sigma_mm": 0.004},
            "seed": {"kind": "plane", "face": 17, "point": [1.0, 2.0, 3.0], "tolerance_mm": None},
            "n_faces": 2300, "tolerance_mm": 0.25, "mean_sigma_mm": 0.08, "correlated_points": 4.5,
            "warnings": ["small region: 23 points"], "mesh_version": version, "extent": None,
            "created": "2026-11-17T10:00:00"}


def hole_feature(fid: str = "f-2") -> dict:
    return {"id": fid, "name": "Fit 2", "kind": "hole",
            "shape": {"kind": "circle", "center": [5.0, 5.0, 2.0], "normal": [0.0, 0.0, 1.0], "radius_mm": 6.0,
                      "diameter_mm": 12.0, "rms_mm": 0.01, "n": 96, "radius_sigma_mm": 0.002,
                      "diameter_sigma_mm": 0.004, "center_sigma_mm": 0.003},
            "seed": {"kind": "hole", "face": 4, "point": [13.0, 5.0, 2.0], "tolerance_mm": None},
            "n_faces": 190, "tolerance_mm": None, "mean_sigma_mm": None, "correlated_points": None,
            "warnings": [], "mesh_version": 3, "extent": None, "created": "2026-11-17T10:01:00"}


def full_doc() -> dict:
    doc = kf.empty()
    doc["frame"] = {"origin": "f-2", "axes": ["f-1", None], "anything": {"nested": [1, 2, 3]}}
    doc["features"] = [plane_feature(), hole_feature()]
    doc["measurements"] = [{"id": "m-1", "name": "Distance 1", "a": [0.0, 0.0, 0.0], "b": [3.0, 4.0, 0.0],
                            "mesh_version": 3, "created": "2026-11-17T10:02:00"}]
    doc["relations"] = [{"id": "r-1", "name": "Plane to hole", "a": "f-1", "b": "f-2",
                         "created": "2026-11-17T10:03:00"}]
    return doc


def test_an_empty_scan_has_an_empty_set(tmp_path):
    doc = kf.load(tmp_path)
    assert doc == {"format_version": 1, "frame": None, "features": [], "measurements": [], "relations": []}
    assert not (tmp_path / "features.json").exists()  # reading writes nothing


def test_save_and_load_round_trip(tmp_path):
    doc = full_doc()
    saved = kf.save(tmp_path, doc)
    assert saved == doc
    assert (tmp_path / kf.FILE_NAME).is_file()
    back = kf.load(tmp_path)
    assert back == doc
    # the shape comes back exactly, with its ± values
    assert back["features"][0]["shape"] == plane_feature()["shape"]
    assert back["features"][1]["shape"]["diameter_sigma_mm"] == 0.004
    # a plain JSON file a person can read
    raw = json.loads((tmp_path / kf.FILE_NAME).read_text(encoding="utf-8"))
    assert raw["format_version"] == 1 and raw["features"][0]["name"] == "Fit 1"


def test_the_frame_passes_through_untouched(tmp_path):
    for frame in (None, {}, {"x": [1, 0, 0], "odd": {"deep": [None, "text", 1.5]}}):
        doc = kf.empty()
        doc["frame"] = copy.deepcopy(frame)
        kf.save(tmp_path, doc)
        assert kf.load(tmp_path)["frame"] == frame
    doc = kf.empty()
    for bad in ([1, 2], "frame", 3):
        doc["frame"] = bad
        with pytest.raises(ValueError, match="frame"):
            kf.validate(doc)


def test_names_are_trimmed_and_ids_are_safe(tmp_path):
    doc = kf.empty()
    f = plane_feature(name="  Top face  ")
    doc["features"] = [f]
    assert kf.validate(doc)["features"][0]["name"] == "Top face"
    for bad_id in ("../x", "a/b", "", "x" * 65, "f 1"):
        doc["features"] = [plane_feature(fid=bad_id)]
        with pytest.raises(ValueError, match="id"):
            kf.validate(doc)


@pytest.mark.parametrize("change, needle", [
    (lambda d: d.update(format_version=2), "newer"),
    (lambda d: d.update(format_version="1"), "format_version"),
    (lambda d: d.pop("features"), "features"),
    (lambda d: d.update(features={}), "features"),
    (lambda d: d["features"][0].update(kind="cone"), "kind"),
    (lambda d: d["features"][0].update(name="   "), "name"),
    (lambda d: d["features"][0].update(name="x" * 81), "name"),
    (lambda d: d["features"][0].update(shape=None), "shape"),
    (lambda d: d["features"][0]["shape"].update(kind="cylinder"), "shape"),
    (lambda d: d["features"][1]["shape"].update(kind="plane"), "shape"),
    (lambda d: d["features"][0]["seed"].update(point=[1, 2]), "point"),
    (lambda d: d["features"][0]["seed"].update(point=[1, float("nan"), 2]), "point"),
    (lambda d: d["features"][0]["seed"].update(face=-1), "face"),
    (lambda d: d["features"][0]["seed"].update(tolerance_mm=-0.1), "tolerance"),
    (lambda d: d["features"][0].update(mesh_version=-1), "mesh_version"),
    (lambda d: d["features"][0].update(mesh_version=1.5), "mesh_version"),
    (lambda d: d["features"][0].update(warnings="many"), "warnings"),
    (lambda d: d["features"][0].update(extent=[1, 2]), "extent"),
    (lambda d: d["features"].append(plane_feature(fid="f-1")), "twice"),
    (lambda d: d["measurements"][0].update(b=[1, 2, "x"]), "b"),
    (lambda d: d["measurements"][0].update(id="f-1"), "twice"),
    (lambda d: d["relations"][0].update(b="f-9"), "f-9"),
    (lambda d: d["relations"][0].update(b="f-1"), "itself"),
])
def test_validation_errors(change, needle):
    doc = full_doc()
    change(doc)
    with pytest.raises(ValueError, match=needle):
        kf.validate(doc)


def test_a_bad_document_is_not_written(tmp_path):
    kf.save(tmp_path, full_doc())
    before = (tmp_path / kf.FILE_NAME).read_bytes()
    doc = full_doc()
    doc["features"][0]["kind"] = "cone"
    with pytest.raises(ValueError):
        kf.save(tmp_path, doc)
    assert (tmp_path / kf.FILE_NAME).read_bytes() == before


def test_the_write_is_atomic(tmp_path, monkeypatch):
    kf.save(tmp_path, full_doc())
    before = (tmp_path / kf.FILE_NAME).read_bytes()

    def broken(src, dst):
        raise OSError("disk unplugged")

    monkeypatch.setattr(kf.os, "replace", broken)
    doc = full_doc()
    doc["features"][0]["name"] = "Renamed"
    with pytest.raises(OSError):
        kf.save(tmp_path, doc)
    monkeypatch.undo()
    # the old file is whole, and no temporary file is left behind
    assert (tmp_path / kf.FILE_NAME).read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [kf.FILE_NAME]


def test_a_damaged_file_says_so(tmp_path):
    (tmp_path / kf.FILE_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="features.json"):
        kf.load(tmp_path)


def test_region_side_files(tmp_path):
    faces = np.array([9, 2, 5, 5, 1], dtype=np.int64)
    kf.save_faces(tmp_path, "f-1", faces)
    p = kf.faces_path(tmp_path, "f-1")
    assert p.parent.name == kf.FACES_DIR and p.suffix == ".npy" and p.is_file()
    back = kf.load_faces(tmp_path, "f-1")
    assert back.dtype == np.uint32 and back.tolist() == [1, 2, 5, 9]  # sorted, each once
    assert kf.load_faces(tmp_path, "f-404") is None
    kf.remove_faces(tmp_path, "f-1")
    kf.remove_faces(tmp_path, "f-1")  # twice is fine
    assert kf.load_faces(tmp_path, "f-1") is None
    with pytest.raises(ValueError, match="id"):
        kf.faces_path(tmp_path, "../escape")


def test_default_names_and_ids():
    items = [{"name": "Fit 1"}, {"name": "Top face"}, {"name": "Fit 4"}]
    assert kf.next_name(items, "Fit") == "Fit 5"
    assert kf.next_name([], "Distance") == "Distance 1"
    assert kf.next_name([{"name": "Bore"}, {"name": "Shaft"}], "Fit") == "Fit 3"
    a, b = kf.new_id("f"), kf.new_id("f")
    assert a != b and a.startswith("f-") and kf.ID_RE.fullmatch(a)
