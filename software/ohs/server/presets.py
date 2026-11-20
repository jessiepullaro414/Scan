"""The presets in the app (FEATURE_PLAN P2; APP_API.md "Presets"): the routes' work and what a scan carries.

:class:`PresetManager` (``Studio.presets``) keeps the presets (:class:`ohs.app.presets.PresetStore`, the
user's own in ``<data_dir>/presets/``) and each scan's preset:

* at the start of a scan (:meth:`PresetManager.for_start`) the chosen preset and the values the user
  changed by hand on the Scan page become the scan's record (:func:`ohs.app.presets.scan_record`); the
  record goes into the recording's ``meta.json`` (``preset``) and the project's scan entry
  (``preset``: ``id``, ``name``, ``hand``);
* the Process page reads it (:meth:`PresetManager.view`) and may change a value by hand, or the preset
  (:meth:`PresetManager.set_scan`); the record keeps which values were changed from the preset;
* every processing stage asks :meth:`PresetManager.acting` for the values that act (the preset's named
  values with the hand changes on top), and the stage's ``report.json`` gets the record with the
  values the mesh was made with (:meth:`PresetManager.report_block`), so a result can be traced.

A scan made before presets (no ``preset`` in its entry) and a scan whose preset file is gone act as
General, which names no value: the stages run exactly as they did before presets existed. The view
says so in ``note``.
"""
from __future__ import annotations

import logging
from typing import Any

from ohs.app.presets import (GENERAL_ID, GENERAL_TOLERANCE_MM, KEYS, LABELS, PresetError, PresetStore, acting,
                             scan_record, validate_values)
from ohs.server.protocol import ApiError

__all__ = ["PresetManager", "BEFORE_PRESETS_NOTE"]

log = logging.getLogger(__name__)

BEFORE_PRESETS_NOTE = "This scan was made before presets, so it is made as General."


def _api(e: PresetError) -> ApiError:
    return ApiError(e.status, str(e), e.hint)


class PresetManager:
    """See the module docstring."""

    def __init__(self, studio: Any) -> None:
        from pathlib import Path

        self.st = studio
        self.store = PresetStore(Path(studio.data_dir) / "presets")

    # ------------------------------------------------------------------ the presets
    def _recipe_exists(self, rid: str) -> bool:
        try:
            self.st.processing.recipes.get(rid)
            return True
        except ApiError:
            return False

    def list(self) -> list[dict]:
        return self.store.list()

    def create(self, name: Any, description: Any, values: Any) -> dict:
        try:
            return self.store.create(name, description, values, recipe_exists=self._recipe_exists)
        except PresetError as e:
            raise _api(e) from None

    def delete(self, pid: str) -> None:
        try:
            self.store.delete(pid)
        except PresetError as e:
            raise _api(e) from None

    # ------------------------------------------------------------------ a scan's preset
    def for_start(self, preset_id: str | None, hand: dict | None, mode: str) -> dict:
        """The record of a new scan: ``preset_id`` (None = General), the values changed by hand on the
        Scan page and the scan's mode (a change from the preset's mode is a hand change). 404 for an
        unknown preset, 422 for a value the app does not take."""
        try:
            hand = validate_values(hand, recipe_exists=self._recipe_exists)
            preset = None if preset_id in (None, GENERAL_ID) else self.store.get(str(preset_id))
        except PresetError as e:
            raise _api(e) from None
        hand.pop("mode", None)  # the mode is the scan's own (the start's `mode`)
        named = (preset or {}).get("values") or {}
        if "mode" in named and named["mode"] != mode:
            hand["mode"] = mode
        return scan_record(None if preset is None else preset["id"], preset, hand, mode=mode)

    @staticmethod
    def stored(record: dict) -> dict:
        """What the scan entry keeps of a record."""
        return {"id": record["id"], "name": record["name"], "hand": dict(record.get("hand") or {})}

    def record(self, pid: str, sid: str, entry: dict | None = None) -> dict:
        """The scan's record now: from its entry, the preset looked up again (gone: General, with a note)."""
        entry = entry if entry is not None else self.st.projects.scan_entry(pid, sid)
        mode = entry.get("mode") if entry.get("mode") in ("markerless", "markers") else "markerless"
        kept = entry.get("preset")
        if not isinstance(kept, dict) or not kept.get("id"):
            rec = scan_record(None, None, None, mode=mode)
            rec["note"] = BEFORE_PRESETS_NOTE
            rec["before_presets"] = True
            return rec
        try:
            hand = validate_values(kept.get("hand") or {})
        except PresetError as e:  # an entry edited by hand: its hand values do nothing
            log.warning("scan %s/%s: the preset's hand values are not valid (%s); they are left out", pid, sid, e)
            hand = {}
        pid_ = str(kept["id"])
        preset = None if pid_ == GENERAL_ID else self.store.find(pid_)
        rec = scan_record(pid_, preset, hand, mode=mode)
        if preset is None and pid_ != GENERAL_ID:
            rec["name"] = str(kept.get("name") or pid_)
            rec["missing"] = True
        return rec

    def general(self, pid: str, sid: str) -> dict:
        """General's value of each key for this scan (what a value the preset does not name is)."""
        from ohs.app.pipeline import default_fusion_params, effective_scale

        cfg = self.st.cfg
        width = 1280
        try:
            from ohs.io.recording import RecordingReader

            size = RecordingReader(self.st.projects.recording_dir(pid, sid)).meta.get("image_size")
            if size:
                width = int(size[0])
        except Exception:  # no recording (a merged scan, or freed): the camera's usual width
            pass
        v = cfg.process.voxel_mm
        voxel = float(v) if v is not None else float(default_fusion_params(effective_scale(width)).voxel_mm)
        entry = self.st.projects.scan_entry(pid, sid)
        return {"mode": entry.get("mode") or "markerless", "quality": "standard", "voxel_mm": voxel,
                "thin_walls": str(cfg.process.thin_walls), "recipe": "default",
                "check_tolerance_mm": GENERAL_TOLERANCE_MM}

    def view(self, pid: str, sid: str) -> dict:
        """``GET .../preset``: the record, General's values and the labels, for the Process page."""
        rec = self.record(pid, sid)
        out = dict(rec)
        out.setdefault("before_presets", False)
        out.setdefault("missing", False)
        out["general"] = self.general(pid, sid)
        out["labels"] = dict(LABELS)
        out["keys"] = list(KEYS)
        return out

    def acting(self, pid: str, sid: str) -> dict:
        """The values that act on this scan's processing (the mode is the scan's own and never in here)."""
        rec = self.record(pid, sid)
        out = acting({"values": rec["preset_values"]}, rec["hand"])
        out.pop("mode", None)
        return out

    def set_scan(self, pid: str, sid: str, preset_id: str | None = None, values: dict | None = None) -> dict:
        """``PUT .../preset``: another preset (its own values; the hand changes start over) and / or values
        changed by hand (``None`` for a value: back to the preset's). The next mesh uses them. 409 while the
        scan records or is being processed; 422 for a value the app does not take, and for the mode."""
        if values is not None and "mode" in values:
            raise ApiError(422, "the tracking mode cannot change after scanning",
                           "start a new scan with the other tracking mode")
        with self.st._lock:
            entry = self.st.processing._check_free(pid, sid)
            try:
                checked = validate_values(values or {}, recipe_exists=self._recipe_exists)
                if preset_id is not None and preset_id != GENERAL_ID:
                    self.store.get(str(preset_id))  # 404 when there is none
            except PresetError as e:
                raise _api(e) from None
            rec = self.record(pid, sid, entry)
            if preset_id is not None and preset_id != rec["id"]:
                preset = None if preset_id == GENERAL_ID else self.store.find(str(preset_id))
                rec = scan_record(None if preset is None else preset["id"], preset, {}, mode=rec["values"]["mode"])
            hand = dict(rec["hand"])
            named = rec["preset_values"]
            for k in values or {}:
                if k in checked and checked[k] != named.get(k):
                    hand[k] = checked[k]
                else:  # None, or the preset's own value: no change by hand
                    hand.pop(k, None)
            kept = {"id": rec["id"], "name": rec["name"], "hand": {k: hand[k] for k in KEYS if k in hand}}
            self.st.projects.update_scan(pid, sid, preset=kept)
        log.info("scan %s/%s: preset %s, changed by hand: %s", pid, sid, kept["id"], ", ".join(kept["hand"]) or "nothing")
        return self.view(pid, sid)

    def report_block(self, pid: str, sid: str, used: dict) -> dict:
        """The record for a stage's ``report.json``, with ``used``: the values this mesh was made with."""
        rec = self.record(pid, sid)
        out = {k: rec[k] for k in ("id", "name", "preset_values", "hand", "changed", "values", "note")}
        out["used"] = {k: used.get(k) for k in KEYS if k in used}
        return out
