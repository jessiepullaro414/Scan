"""Presets by object type (FEATURE_PLAN P2): a named bundle of the values the app already lets the user set.

A preset names values for the scan (``mode``), for Make mesh (``quality``, ``voxel_mm``, ``thin_walls``),
the recipe Make mesh runs (``recipe``) and the tolerance the Check panel starts with
(``check_tolerance_mm``). **A value a preset does not name is General's**, and General names none:
General is exactly what the app does without presets (the scan's own mode, the quality asked for,
the voxel size of that quality, ``[process] thin_walls`` from the settings, the Default recipe, the
Check panel's own 0.3 mm). So a scan with General runs the very same code as a scan made before
presets existed.

The presets come from P1's measurements (``VALIDATION.md``, "Presets: which settings matter,
measured"; the owner's decision D5): General, the default, and "Thin sheet metal and small parts",
which sets only the voxel size (0.5 mm). Nothing else earned a place in a preset.

Kept like recipes: the built-in ones here, the user's own as JSON files in ``<data_dir>/presets/``
(:class:`PresetStore`). A user's preset can name only the values of :data:`KEYS`, each checked as the
app checks it elsewhere (:func:`validate_values`); anything else is refused in plain words
(:class:`PresetError`, with an HTTP status for the server and a hint).

A scan carries its preset as a *record* (:func:`scan_record`): the preset's id and name, the values
the user changed by hand for this scan (``hand``), and every value it resolved to (``values``; a
``None`` there is General's value, decided when the mesh is made). The values that act on the
pipeline are :func:`acting`: the preset's named values with the hand changes on top. A preset whose
file is gone acts as General (the record says so in ``note``); the scan's own hand changes stay.

Imports nothing from the server and nothing heavy.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import secrets
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["KEYS", "LABELS", "GENERAL_ID", "THIN_ID", "BUILTIN_PRESETS", "VOXEL_RANGE", "TOLERANCE_RANGE",
           "GENERAL_TOLERANCE_MM", "PresetError", "PresetStore", "validate_values", "scan_record", "acting",
           "changed_keys"]

log = logging.getLogger(__name__)

#: the values a preset can name, in the order the app shows them
KEYS = ("mode", "quality", "voxel_mm", "thin_walls", "recipe", "check_tolerance_mm")
#: each value in the app's words
LABELS = {"mode": "Tracking mode", "quality": "Quality", "voxel_mm": "Mesh detail",
          "thin_walls": "Thin walls and edges", "recipe": "Recipe after meshing",
          "check_tolerance_mm": "Tolerance"}
GENERAL_ID = "general"
THIN_ID = "thin-sheet-small-parts"
VOXEL_RANGE = (0.25, 10.0)  # mm, as the Fuse stage and recipes take it (ohs.server.processing.VOXEL_RANGE)
TOLERANCE_RANGE = (0.0, 100.0)  # mm, above 0 and at most 100, as the Check route takes it
GENERAL_TOLERANCE_MM = 0.3  # what the Check panel starts with (ui/src/lib/check.ts DEFAULT_TOLERANCE_MM)
MODES = ("markerless", "markers")
QUALITIES = ("standard", "best", "preview")
THIN_WALLS = ("auto", "on", "off")
#: values a preset cannot hold because the app does not let the user set them (yet), with the reason
NOT_SETTABLE = {"depth_range": "the depth range is not a setting of the app",
                "min_depth_mm": "the depth range is not a setting of the app",
                "max_depth_mm": "the depth range is not a setting of the app",
                "gap_guard": "the gap guard is not a setting of the app",
                "gap_guard_px": "the gap guard is not a setting of the app"}

BUILTIN_PRESETS: list[dict] = [
    {"id": GENERAL_ID, "name": "General", "builtin": True,
     "description": "What the app does for any part: 1 mm mesh detail, the Default recipe, every setting as it is.",
     "card": {"changes": "Nothing. Every setting stays as it is in the app.",
              "why": "Measured on seven simulated parts, the settings as they are give the best result on panels, "
                     "brackets, castings and large parts. No other setting beat the scan-to-scan spread.",
              "costs": None},
     "values": {}},
    {"id": THIN_ID, "name": "Thin sheet metal and small parts", "builtin": True,
     "description": "Finer mesh detail (0.5 mm instead of 1 mm), so thin walls come out closer to their real "
                    "thickness.",
     "card": {"changes": "Finer mesh detail (0.5 mm instead of 1 mm): thin walls come out closer to their real "
                         "thickness. Nothing else changes.",
              "why": "Measured on three test scans: walls 0.5, 1 and 2 mm thick came out 1.03 to 1.08, 1.32 to 1.45 "
                     "and 2.18 to 2.43 mm thick, instead of 1.70, 1.71 to 1.90 and 2.53 to 2.66 mm at 1 mm. Making "
                     "the mesh took 1.2 to 2.4 times as long and needed more graphics memory. A detailed casting "
                     "gains a little too; a large panel or a door gains nothing.",
              "costs": "Takes about twice as long to make the mesh. No gain on large panels."},
     "values": {"voxel_mm": 0.5}},
]


class PresetError(ValueError):
    """A refusal in plain words: ``status`` (HTTP), the message and a ``hint`` saying what to do."""

    def __init__(self, status: int, message: str, hint: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.hint = hint


def _fmt(x: float) -> str:
    return f"{x:g}"


def _number(key: str, v: Any, lo: float, hi: float, *, lo_open: bool) -> float:
    label = LABELS[key].lower()
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise PresetError(422, f"{label} must be a number of millimetres", f"send a number for {key}")
    v = float(v)
    if (v <= lo if lo_open else v < lo) or v > hi:
        rng = f"{'above' if lo_open else 'at least'} {_fmt(lo)} mm and at most {_fmt(hi)} mm"
        raise PresetError(422, f"{label} must be {rng} (got {_fmt(v)} mm)", f"choose a {label} {rng}")
    return v


def _choice(key: str, v: Any, choices: tuple[str, ...]) -> str:
    if not isinstance(v, str) or v not in choices:
        words = ", ".join(choices[:-1]) + f" or {choices[-1]}"
        raise PresetError(422, f"{LABELS[key].lower()} must be {words} (got {v!r})", f"use {words}")
    return v


def validate_values(values: Any, *, recipe_exists: Any = None) -> dict:
    """The values a preset (or a hand change) names, checked as the app checks each of them: unknown keys,
    wrong types and values out of range are refused (:class:`PresetError`, 422). ``None`` for a value
    means "not named" and is dropped. ``recipe_exists(id) -> bool``, when given, checks the recipe."""
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise PresetError(422, "the values must be an object of names and values",
                          'for example {"voxel_mm": 0.5}')
    out: dict[str, Any] = {}
    for k, v in values.items():
        if k in NOT_SETTABLE:
            raise PresetError(422, f"a preset cannot set {k}: {NOT_SETTABLE[k]}",
                              f"a preset can set {', '.join(KEYS)}")
        if k not in KEYS:
            raise PresetError(422, f"a preset has no value called {k!r}", f"a preset can set {', '.join(KEYS)}")
        if v is None:
            continue
        if k == "mode":
            out[k] = _choice(k, v, MODES)
        elif k == "quality":
            out[k] = _choice(k, "standard" if v == "full" else v, QUALITIES)  # "full": the old name of standard
        elif k == "thin_walls":
            out[k] = _choice(k, v, THIN_WALLS)
        elif k == "voxel_mm":
            out[k] = _number(k, v, *VOXEL_RANGE, lo_open=False)
        elif k == "check_tolerance_mm":
            out[k] = _number(k, v, *TOLERANCE_RANGE, lo_open=True)
        elif k == "recipe":
            if not isinstance(v, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", v):
                raise PresetError(422, f"the recipe must be a recipe's id (got {v!r})", "pick a recipe from GET /api/recipes")
            if recipe_exists is not None and not recipe_exists(v):
                raise PresetError(422, f"there is no recipe {v!r}", "pick a recipe from GET /api/recipes")
            out[k] = v
    return {k: out[k] for k in KEYS if k in out}


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-")
    return s or "preset"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class PresetStore:
    """The built-in presets plus the user's own as JSON files in ``<data_dir>/presets/``. Thread-safe."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    @staticmethod
    def builtin(pid: str) -> dict | None:
        for p in BUILTIN_PRESETS:
            if p["id"] == pid:
                return copy.deepcopy(p)
        return None

    def _path(self, pid: str) -> Path | None:
        if not isinstance(pid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", pid):
            return None
        return self.root / f"{pid}.json"

    @staticmethod
    def _view(p: dict) -> dict:
        try:
            values = validate_values(p.get("values") or {})
        except PresetError as e:  # a file edited by hand: shown, but its values do nothing
            log.warning("preset %s: %s; it acts as General", p.get("id"), e)
            values = {}
        return {"id": p["id"], "name": str(p.get("name") or p["id"]), "builtin": False,
                "description": str(p.get("description") or ""), "card": None, "values": values,
                "created": p.get("created")}

    def list(self) -> list[dict]:
        out = [copy.deepcopy(p) for p in BUILTIN_PRESETS]
        user: list[dict] = []
        if self.root.is_dir():
            for f in sorted(self.root.glob("*.json")):
                try:
                    p = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(p, dict) and p.get("id") == f.stem and self.builtin(f.stem) is None:
                        user.append(self._view(p))
                except (OSError, ValueError) as e:
                    log.warning("skipping preset file %s: %s", f, e)
        user.sort(key=lambda p: (p["name"].lower(), p["id"]))
        return out + user

    def find(self, pid: str) -> dict | None:
        """The preset, or None when there is none of that id (a user's preset whose file is gone)."""
        b = self.builtin(pid)
        if b is not None:
            return b
        path = self._path(pid)
        if path is None:
            return None
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return self._view(p) if isinstance(p, dict) and p.get("id") == pid else None

    def get(self, pid: str) -> dict:
        p = self.find(pid)
        if p is None:
            raise PresetError(404, f"there is no preset {pid!r}", "pick a preset from GET /api/presets")
        return p

    def create(self, name: Any, description: Any, values: Any, *, recipe_exists: Any = None) -> dict:
        n = " ".join(str(name or "").split())
        if not n:
            raise PresetError(400, "the preset's name is empty", "type a name for the preset")
        if len(n) > 80:
            raise PresetError(400, f"the preset's name is too long ({len(n)} characters)", "use at most 80 characters")
        if any(n.lower() == b["name"].lower() for b in BUILTIN_PRESETS):
            raise PresetError(409, f"“{n}” is the name of a built-in preset", "choose another name")
        vals = validate_values(values, recipe_exists=recipe_exists)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            for _ in range(100):
                pid = f"{_slug(n)}-{secrets.token_hex(2)}"
                if self.builtin(pid) is None and not (self.root / f"{pid}.json").exists():
                    break
            else:  # pragma: no cover
                raise PresetError(500, "could not find a free preset id", "try another name")
            rec = {"id": pid, "name": n, "description": " ".join(str(description or "").split())[:500],
                   "values": vals, "created": _now()}
            tmp = self.root / f".{pid}.json.tmp"
            tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
            tmp.replace(self.root / f"{pid}.json")
        log.info("saved preset %s (%s)", pid, n)
        return self._view(rec)

    def delete(self, pid: str) -> None:
        if self.builtin(pid) is not None:
            raise PresetError(409, f"“{self.builtin(pid)['name']}” is a built-in preset and cannot be deleted",
                              "only presets you saved can be deleted")
        path = self._path(pid)
        with self._lock:
            try:
                if path is None:
                    raise FileNotFoundError
                path.unlink()
            except FileNotFoundError:
                raise PresetError(404, f"there is no preset {pid!r}", "pick a preset from GET /api/presets") from None


def changed_keys(preset_values: dict, hand: dict) -> list[str]:
    """The hand changes that differ from what the preset names (General's value when it names none)."""
    return [k for k in KEYS if k in hand and hand[k] != preset_values.get(k)]


def acting(preset: dict | None, hand: dict | None) -> dict:
    """The values that act on the pipeline: the preset's named values with the hand changes on top.
    ``preset`` None (General, or a preset that is gone) names nothing."""
    out = dict((preset or {}).get("values") or {})
    out.update({k: v for k, v in (hand or {}).items() if v is not None})
    return {k: out[k] for k in KEYS if k in out}


def scan_record(preset_id: str | None, preset: dict | None, hand: dict | None, *, mode: str | None = None) -> dict:
    """What a scan stores about its preset: ``id`` and ``name`` (General when ``preset_id`` is None),
    ``hand`` (the values changed by hand for this scan), ``preset_values`` (what the preset names),
    ``changed`` (hand changes that differ from it), ``values`` (every value it resolved to, ``None`` =
    General's, decided when the mesh is made; ``mode`` = the scan's own mode when given) and ``note``
    (why it acts as General, or None). ``preset`` is the preset found for ``preset_id`` (None when gone)."""
    hand = {k: v for k, v in (hand or {}).items() if v is not None}
    if preset_id is None or preset_id == GENERAL_ID:
        pid, name, pv, note = GENERAL_ID, "General", {}, None
    elif preset is None:
        pid, name, pv = preset_id, preset_id, {}
        note = "This scan's preset is no longer in the app, so the scan is made as General."
    else:
        pid, name, pv, note = preset["id"], preset["name"], dict(preset.get("values") or {}), None
    act = acting({"values": pv}, hand)
    values: dict[str, Any] = {k: act.get(k) for k in KEYS}
    if mode is not None and "mode" not in act:
        values["mode"] = mode
    return {"id": pid, "name": name, "preset_values": pv, "hand": {k: hand[k] for k in KEYS if k in hand},
            "changed": changed_keys(pv, hand), "values": values, "note": note}
