"""Kept features over the API (APP_API.md "Kept features"): fits, two-point measurements and
relations saved with a scan, renamed and deleted, and fitted again when the mesh has changed.

The records and the file are :mod:`ohs.measure.features`; the fits are the fit route's
(:meth:`ProcessManager.fit <ohs.server.processing.ProcessManager.fit>`), so a kept fit has exactly
the numbers a click in the viewer gives. This module adds:

* **keeping a fit:** ``POST .../features`` fits like ``POST .../fit`` and keeps the answer, the
  click it was grown from (its ``seed``) and the mesh version, in one step; the region goes to its
  own file;
* **staleness:** a feature fitted on another mesh version than the current one is *stale*
  ("fitted on an earlier mesh"). Reading marks it; nothing is refitted on its own;
* **fit again:** ``POST .../features/refit`` refits stale features on the current mesh from their
  seeds. Face numbers change with an edit, so the seed's face is found again as the measured face
  nearest the saved point; when that face is not under the point any more (the region was cut
  away), the feature is not fitted on some other surface. A feature that cannot be fitted again
  stays as it was, still marked, and the answer says why;
* **the extent** (FEATURE_PLAN K2): a fit is kept and fitted again with where its region lies on
  its shape (the fit route's ``extent``, asked for with ``with_extent``). ``features.json`` keeps
  its small part, the outline goes to its own file (:func:`ohs.measure.features.split_extent`),
  served by ``GET .../features/{id}/outline``. A fit kept before extents existed has none; "fit
  again" gives it one (from its own click, on its own mesh, when it is not stale);
* **a cylinder's form:** a fit is kept and fitted again with whether a cylinder is a boss or a bore
  (the fit route's ``form``, asked for with ``with_form``; null for a plane or a hole, and when the
  fit cannot tell). A fit kept before forms existed has no ``form`` and is not fitted again for it;
  its next "fit again" sets it;
* **relations from the engine:** every relation shown (a kept one in ``GET .../features``, any two
  kept fits in ``GET .../features/relate``) carries :func:`ohs.measure.relate.relate` on the two
  kept shapes, its values with their 1σ, and ``stale`` when one of the fits was made on an earlier
  mesh. Nothing of it is stored: it is worked out when shown;
* **the part's frame** (FEATURE_PLAN K3): ``POST .../features/frame`` builds the part's coordinate
  system from three kept fits (:func:`ohs.measure.frame.part_frame`) and keeps it under ``frame``;
  a refusal of the engine is answered 422 with its own words, a fit made on an earlier mesh 409.
  Reading shows whether the frame is still current (:func:`ohs.measure.features.frame_state`);
  nothing is rebuilt on its own. ``GET .../features/frame/mesh.ply`` (or ``.stl``) is the scan's
  current result mesh turned into the frame, made on request: the scan's own files stay as they are;
* **for CAD** (FEATURE_PLAN C2): :meth:`FeatureManager.snapshot` reads the kept set and the planes'
  outlines for :mod:`ohs.server.cad`, which serves them as a STEP file and a FreeCAD macro;
* **nominal values** (FEATURE_PLAN C3): ``GET .../features/{id}/nominal`` gives what
  :mod:`ohs.cad.nominal` offers for a kept fit or relation (a fit's ``form`` from its record; the
  poses, tracked or true, from the scan's processing report: :meth:`FeatureManager.poses`);
  ``POST`` keeps an approved value (the one offered, or one entered) beside the measured ones and
  ``DELETE .../nominal/{value}`` forgets it (``ohs.measure.features``, "Approved nominal values").
  Fitting again keeps the approvals; reading marks one **check again** when the measurement has
  changed since and no longer allows it by the engine's rule
  (:func:`ohs.cad.nominal.check_fit_nominal`), with the old and the new measured value. Nothing is
  re-approved or dropped on its own. Offers that only a known bias allows are made only when asked
  for (``with_bias``, off by default); an approval made that way keeps the rule (``with_bias``
  true in its record) and is judged by it when measured again;
* **a relation's move** (after C3): ``PUT .../features/{id}/move`` chooses whether a kept relation
  is listed only in the CAD export (the default) or one of its two fits moves to suit it
  (:mod:`ohs.cad.moves`, applied by :mod:`ohs.server.cad`); it can also be chosen with an approval
  (``move`` in ``POST .../nominal``). Kept on the relation (``ohs.measure.features``, "A
  relation's move"), with its place in the order the moves are applied;
* **kept sections** (FEATURE_PLAN I1) are :mod:`ohs.server.sections`'s: it writes the ``sections``
  list through :meth:`FeatureManager.change_sections` and reads it with :meth:`FeatureManager.snapshot`.
  ``GET .../features`` answers as before, without them.
* **kept seams** (FEATURE_PLAN I4) are :mod:`ohs.server.flush`'s, in the same way: the ``seams`` list,
  written through :meth:`FeatureManager.change_seams`. ``GET .../features`` answers as before, without them.

**Locking.** One lock per scan around reading, changing and writing ``features.json``. Fits run
outside it (they can take a second); a refit is only kept when its feature has not been changed
or deleted meanwhile.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

import numpy as np

import ohs.measure.features as kf
from ohs.server.protocol import ApiError, plain_reason, sentence

__all__ = ["FeatureManager"]

log = logging.getLogger(__name__)

GONE_MIN_MM = 2.0  # fit again: the surface under a saved click is gone when the nearest face is farther than this
GONE_EDGES = 1.5  # ... and than this many of that face's longest edge
TRUE_POSES_MM = 0.001  # given poses this close to the ground truth (every frame) are the true poses ...
TRUE_POSES_DEG = 0.001  # ... and this close in rotation
_HINT_FILE = ("The scan's mesh and recording are untouched. Restore the file features.json in the scan's folder "
              "from a backup, or delete it to start the fitted shapes again.")
#: an item the page asked for that is not in the scan's list (deleted meanwhile, or another window)
_GONE = "Reload the page to see the scan's current list."


class FeatureManager:
    """The kept features of every scan of the :class:`~ohs.server.studio.Studio` ``st``."""

    def __init__(self, st: Any) -> None:
        self.st = st
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _lock(self, pid: str, sid: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault((pid, sid), threading.Lock())

    def _dir(self, pid: str, sid: str):
        self.st.projects.scan_entry(pid, sid)  # 404 for an unknown project or scan
        return self.st.projects.scan_dir(pid, sid)

    def _load(self, pid: str, sid: str) -> dict:
        d = self._dir(pid, sid)
        try:
            return kf.load(d)
        except ValueError as e:
            log.warning("the kept features of %s/%s cannot be read: %s", pid, sid, e)
            raise ApiError(500, "The fitted shapes and measurements of this scan cannot be read.", _HINT_FILE) from None

    def _save(self, pid: str, sid: str, doc: dict) -> dict:
        return kf.save(self.st.projects.scan_dir(pid, sid), doc)

    def _version(self, pid: str, sid: str) -> int | None:
        """The current mesh's version, None while the scan has no mesh."""
        sp = self.st.processing.scan(pid, sid)
        with sp.lock:
            return None if sp.state["base"] is None else int(sp.state["version"])

    @staticmethod
    def _view(item: dict, version: int | None) -> dict:
        """A feature or measurement as the API shows it: with ``stale`` when it was made on another mesh."""
        return {**item, "stale": version is None or item.get("mesh_version") != version}

    @staticmethod
    def _frame_view(frame: dict | None, features: list[dict]) -> dict | None:
        """The kept frame as the API shows it: with ``readable``, ``out_of_date`` and why
        (:func:`ohs.measure.features.frame_state`); None without one."""
        state = kf.frame_state(frame, features)
        return None if state is None else {**frame, **state}

    @staticmethod
    def _relation_view(rel: dict, features: list[dict], version: int | None) -> dict:
        """A kept relation as the API shows it: with ``relation``, the engine's record for its two
        fits (:meth:`Relation.to_dict <ohs.measure.relate.Relation.to_dict>`), and ``stale`` when
        one of them was fitted on another mesh version than the current one. A pair the engine
        cannot relate (a damaged shape) has ``relation`` null and ``error`` with its words."""
        from ohs.measure.relate import relate

        by_id = {f["id"]: f for f in features}
        a, b = by_id.get(rel["a"]), by_id.get(rel["b"])
        out = {**rel, "stale": any(f is None or version is None or f["mesh_version"] != version for f in (a, b))}
        try:
            out["relation"] = relate({"kind": a["kind"], "shape": a["shape"]}, {"kind": b["kind"], "shape": b["shape"]}).to_dict()
        except (ValueError, TypeError, KeyError) as e:
            why = plain_reason(e, "")
            out["relation"] = None
            out["error"] = sentence(f"These two fitted shapes cannot be related: {why}" if why else
                                    "These two fitted shapes cannot be related.")
        return out

    def _set_view(self, doc: dict, version: int | None, poses: str = "tracked") -> dict:
        """The kept set as ``GET .../features`` shows it: each feature and relation with its approved
        nominal values and their "check again" marks (:meth:`nominal_views`)."""
        views = self.nominal_views(doc, poses)
        return {"format_version": doc["format_version"], "frame": self._frame_view(doc["frame"], doc["features"]),
                "mesh_version": version,
                "features": [{**self._view(f, version), "nominals": views.get(f["id"], [])} for f in doc["features"]],
                "measurements": [self._view(m, version) for m in doc["measurements"]],
                "relations": [{**self._relation_view(r, doc["features"], version), "nominals": views.get(r["id"], [])}
                              for r in doc["relations"]]}

    # ------------------------------------------------------------------ nominal values (C3)
    def poses(self, pid: str, sid: str) -> tuple[str, str]:
        """Which rows of the bias table apply to this scan (``ohs.cad.nominal``, ``poses``), and why,
        from the scan's processing report (``result/report.json``, copied from the stage the current
        mesh was made by):

        * no report, or one that cannot be read: "tracked" (a scan as a user makes it);
        * the report does not say ``"poses": "given"``: "tracked", the poses were tracked;
        * ``"poses": "given"`` and the report's ground truth (a simulated recording) shows the given
          poses equal the true ones (every frame within :data:`TRUE_POSES_MM` and
          :data:`TRUE_POSES_DEG`): "true", the scan was fused at its true poses;
        * ``"poses": "given"`` otherwise: "tracked". The app gives poses only from its own straighten
          and fuse stages, which start from the tracked poses, so the tracked biases apply."""
        path = Path(self.st.projects.scan_dir(pid, sid)) / "result" / "report.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "tracked", "no processing report: the poses are taken as tracked"
        except (OSError, ValueError):
            return "tracked", "the processing report cannot be read: the poses are taken as tracked"
        if not isinstance(report, dict) or report.get("poses") != "given":
            return "tracked", "the processing report says the poses were tracked"
        gt = report.get("ground_truth") if isinstance(report.get("ground_truth"), dict) else {}

        def worst(key: str) -> float | None:
            stats = gt.get(key)
            x = stats.get("max") if isinstance(stats, dict) else None
            return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None

        rot, trans = worst("rot_deg"), worst("trans_mm")
        if gt.get("n") and rot is not None and trans is not None and rot <= TRUE_POSES_DEG and trans <= TRUE_POSES_MM:
            return "true", ("the processing report says the poses were given and equal the simulator's true poses "
                            f"(within {TRUE_POSES_MM:g} mm and {TRUE_POSES_DEG:g}°): the biases at true poses apply")
        return "tracked", ("the processing report says the poses were given (straightened or fused again from the "
                           "tracked poses): the biases with tracking apply")

    @staticmethod
    def _related(doc: dict, rel: dict) -> tuple[dict | None, list[str]]:
        """A kept relation's engine record (``Relation.to_dict()``, None when the two fits cannot be
        related) and its two fits' warnings."""
        from ohs.measure.relate import relate

        by_id = {f["id"]: f for f in doc["features"]}
        a, b = by_id.get(rel["a"]), by_id.get(rel["b"])
        if a is None or b is None:
            return None, []
        warnings = list(a.get("warnings") or []) + list(b.get("warnings") or [])
        try:
            return relate({"kind": a["kind"], "shape": a["shape"]}, {"kind": b["kind"], "shape": b["shape"]}).to_dict(), warnings
        except (ValueError, TypeError, KeyError):
            return None, warnings

    def _check(self, doc: dict, key: str, item: dict, value: str, nominal: float, poses: str,
               with_bias: bool = False):
        """:func:`ohs.cad.nominal.check_fit_nominal` (or ``check_relation_nominal``) for one value of a
        kept fit or relation as it is measured now, by the rule given (``with_bias``: whether a known
        bias alone may allow it)."""
        from ohs.cad.nominal import NominalCheck, check_fit_nominal, check_relation_nominal

        if key == "features":
            return check_fit_nominal(item, nominal, value=value, poses=poses, with_bias=with_bias)
        rel, warnings = self._related(doc, item)
        if rel is None:
            unit = kf.NOMINAL_UNITS.get(value, "mm")
            return NominalCheck(value, unit, float(nominal), None, None, False, None, None,
                                "the two fits cannot be related now")
        return check_relation_nominal(rel, value, nominal, poses=poses, warnings=warnings, with_bias=with_bias)

    @staticmethod
    def _differs(a: float | None, b: float | None) -> bool:
        if a is None or b is None:
            return (a is None) != (b is None)
        return abs(float(a) - float(b)) > 1e-9 * max(1.0, abs(float(a)))

    def _nominal_view(self, doc: dict, key: str, item: dict, n: dict, poses: str) -> dict:
        """An approval as the API shows it: as kept, with the value measured now (``measured_now``,
        ``sigma_now``) and ``check_again``: true when the measurement has changed since the approval
        and the new one no longer allows the nominal (:meth:`_check`, by the rule it was approved
        under: a known bias counts only for one approved with ``with_bias``), with ``check_reason``
        saying the old and the new measured value and why. Nothing is changed."""
        chk = self._check(doc, key, item, n["value"], n["nominal"], poses, bool(n.get("with_bias", False)))
        changed = (chk.measured is None or self._differs(n.get("measured"), chk.measured)
                   or self._differs(n.get("sigma"), chk.sigma))
        again = bool(changed and not chk.allowed)
        reason = None
        if again:
            unit = "°" if n["unit"] == "deg" else " mm"

            def shown(x, s):
                return "nothing" if x is None else f"{x:.2f}" + ("" if s is None else f" ± {s:.2f}") + unit

            reason = (f"check again: it was approved against {shown(n.get('measured'), n.get('sigma'))}, and it now "
                      f"measures {shown(chk.measured, chk.sigma)}. {chk.reason[:1].upper()}{chk.reason[1:]}")
        return {**n, "measured_now": chk.measured, "sigma_now": chk.sigma, "check_again": again, "check_reason": reason}

    def nominal_views(self, doc: dict, poses: str) -> dict[str, list[dict]]:
        """Every kept fit's and relation's approvals as the API shows them (:meth:`_nominal_view`), by
        their id; items without approvals are left out. For :mod:`ohs.server.cad` too."""
        out: dict[str, list[dict]] = {}
        for key in ("features", "relations"):
            for item in doc[key]:
                if item.get("nominals"):
                    out[item["id"]] = [self._nominal_view(doc, key, item, n, poses) for n in item["nominals"]]
        return out

    def _nominal_item(self, doc: dict, item_id: str) -> tuple[str, dict]:
        """A kept fit or relation by its id: ``("features" | "relations", the record)``; 404 otherwise."""
        for key in ("features", "relations"):
            for it in doc[key]:
                if it["id"] == item_id:
                    return key, it
        if any(m["id"] == item_id for m in doc["measurements"]):
            raise ApiError(404, "A measurement between two points has no nominal value: only fitted shapes and "
                                "relations have one.", "Pick a fitted shape or a relation.")
        raise ApiError(404, "This fitted shape or relation is no longer in the scan.", _GONE)

    def _offers(self, doc: dict, key: str, item: dict, poses: str, value: str | None, with_bias: bool = False) -> list:
        """The engine's offers for a kept fit (``suggest_fit``, its own ``form``) or relation
        (``suggest_relation`` on the engine's record, with both fits' warnings): a list of ``Offer``.
        ``with_bias``: also the values a known bias alone allows (off unless asked)."""
        from ohs.cad.nominal import suggest_fit, suggest_relation

        if key == "features":
            if value not in (None, *kf.FIT_NOMINAL_VALUES):
                raise ApiError(422, "A fitted shape's nominal value is its diameter or its radius.",
                               "Pick the diameter or the radius.")
            return suggest_fit(item, poses=poses, value=value, with_bias=with_bias)
        if value is not None:
            raise ApiError(422, "A relation offers values for all its sizes at once.", _GONE)
        rel, warnings = self._related(doc, item)
        if rel is None:
            raise ApiError(409, f"The two fitted shapes of \"{item['name']}\" cannot be related.",
                           "Fit them again, or delete the relation.")
        return suggest_relation(rel, poses=poses, warnings=warnings, with_bias=with_bias)

    def _nominal_answer(self, doc: dict, key: str, item: dict, poses: tuple[str, str], value: str | None,
                        with_bias: bool = False) -> dict:
        p, why = poses
        return {"id": item["id"], "name": item["name"], "kind": "fit" if key == "features" else "relation",
                "form": item.get("form") if key == "features" else None, "poses": p, "poses_reason": why,
                "with_bias": bool(with_bias), "move": item.get("move") if key == "relations" else None,
                "offers": [o.to_dict() for o in self._offers(doc, key, item, p, value, with_bias)],
                "nominals": [self._nominal_view(doc, key, item, n, p) for n in item.get("nominals") or []]}

    def nominal(self, pid: str, sid: str, item_id: str, value: str | None = None, with_bias: bool = False) -> dict:
        """``GET .../features/{id}/nominal``: what is offered for a kept fit (its diameter, or its
        radius with ``value="radius"``) or a kept relation (each of its values), as
        :mod:`ohs.cad.nominal` offers it, with the poses used and why (:meth:`poses`), and its
        approvals (with "check again"). ``with_bias``: also the values only a known bias allows
        (each marked so); without it those are left out, and each offer's ``bias_only`` names them.
        A plane alone has no offers. 404 for an unknown id."""
        self._dir(pid, sid)
        poses = self.poses(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
        key, item = self._nominal_item(doc, item_id)
        return self._nominal_answer(doc, key, item, poses, value, with_bias)

    def approve(self, pid: str, sid: str, item_id: str, value: str, nominal: float, source: str,
                with_bias: bool = False, move: str | None = None) -> dict:
        """``POST .../features/{id}/nominal``: keep an approved nominal value for a kept fit's size or
        one of a kept relation's values, against the value measured now. ``source`` "suggested": the
        value must be one offered now (422 otherwise), under the rule asked for (``with_bias``: the
        offers include those only a known bias allows); "entered": any number that makes sense for
        that kind of value (:func:`ohs.measure.features.check_nominal_value`). The rule is kept with the
        approval (``with_bias``: true, left out when false), so "check again" judges it by that rule.
        It replaces an approval of the same value (for a fit, of its size: a diameter and a radius are
        one size). ``move`` (a relation only): "a", "b" or "listed", chosen with it
        (:meth:`set_move`); None leaves the choice as it is. The measured values are not touched.
        Returns the answer of :meth:`nominal`."""
        self._dir(pid, sid)
        poses = self.poses(pid, sid)
        if source not in kf.NOMINAL_SOURCES:
            raise ApiError(422, "The page did not say whether the value was offered or typed.", _GONE)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            key, item = self._nominal_item(doc, item_id)
            if move is not None:
                self._check_move(key, move)
            if key == "features" and item["kind"] == "plane":
                raise ApiError(422, "A plane alone has no size to approve.", "Approve a relation between two planes instead.")
            values = kf.FIT_NOMINAL_VALUES if key == "features" else kf.RELATION_NOMINAL_VALUES
            if value not in values:
                raise ApiError(422, f"A {'fitted shape' if key == 'features' else 'relation'} has no such size to "
                                    "approve.", "Pick one of the sizes it shows.")
            try:
                nominal = kf.check_nominal_value(value, nominal)
            except ValueError as e:
                raise ApiError(422, plain_reason(e, "This number cannot be used for this size."),
                               "Enter a number that makes sense for this size.") from None
            offers = self._offers(doc, key, item, poses[0], value if key == "features" else None, with_bias)
            offer = next((o for o in offers if o.value == value), None)
            if offer is None:
                raise ApiError(422, f"The relation \"{item['name']}\" has no {value.replace('_', ' ')} to approve.",
                               "Pick one of the sizes it shows.")
            chk = self._check(doc, key, item, value, nominal, poses[0])
            word = chk.word
            if source == "suggested":
                hit = next((s for s in offer.suggestions if abs(s.nominal - nominal) <= 1e-9), None)
                if hit is None:
                    offered = ", ".join(f"{s.nominal:g}" for s in offer.suggestions) or "nothing"
                    raise ApiError(422, f"{nominal:g} is not offered now for this {value.replace('_', ' ')} "
                                        f"(offered: {offered}).",
                                   "Approve one of the values offered, or type the value yourself.")
                word = hit.word
            entry = {"value": value, "nominal": nominal, "unit": offer.unit, "word": word, "source": source,
                     "measured": float(offer.measured), "sigma": None if offer.sigma is None else float(offer.sigma),
                     **({"with_bias": True} if with_bias else {}), "approved": kf.now()}
            same = kf.FIT_NOMINAL_VALUES if key == "features" else (value,)
            item["nominals"] = [n for n in item.get("nominals") or [] if n["value"] not in same] + [entry]
            if move is not None:
                self._choose_move(doc, item, move)
            saved = self._save(pid, sid, doc)
            key, item = self._nominal_item(saved, item_id)
        return self._nominal_answer(saved, key, item, poses, value if key == "features" else None, with_bias)

    # ------------------------------------------------------------------ a relation's move (after C3)
    @staticmethod
    def _check_move(key: str, move: str) -> None:
        if key != "relations":
            raise ApiError(422, "A move is chosen for a relation: a fitted shape alone does not move.",
                           "Choose the move on a relation.")
        if move not in ("listed", *kf.MOVE_SHAPES):
            raise ApiError(422, "The app does not know this kind of move.",
                           "Choose to list the relation in the notes only, or to move one of its two shapes.")

    @staticmethod
    def _choose_move(doc: dict, rel: dict, move: str) -> None:
        """Set a kept relation's move (``ohs.measure.features``, "A relation's move"): "listed" takes
        it away; "a" or "b" keeps it, with the next place in the order, unless the same shape was
        already chosen (then it keeps its place)."""
        if move == "listed":
            rel.pop("move", None)
            return
        if (rel.get("move") or {}).get("shape") == move:
            return
        last = max((int(r["move"]["order"]) for r in doc["relations"] if r.get("move")), default=0)
        rel["move"] = {"shape": move, "order": last + 1, "chosen": kf.now()}

    def set_move(self, pid: str, sid: str, item_id: str, move: str) -> dict:
        """``PUT .../features/{id}/move``: which of a kept relation's two fits moves to suit its
        approved values in the CAD export (``ohs.cad.moves``): "a", "b", or "listed" (only listed in
        the notes, the default). Choosing again the shape already chosen keeps its place in the order
        the moves are applied; another choice is placed last. The measured fits are not touched.
        Returns the relation as ``GET .../features`` shows it. 404 for an unknown id, 422 for a fit
        or another value."""
        self._dir(pid, sid)
        version = self._version(pid, sid)
        poses = self.poses(pid, sid)[0]
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            key, item = self._nominal_item(doc, item_id)
            self._check_move(key, move)
            self._choose_move(doc, item, move)
            saved = self._save(pid, sid, doc)
            _, item = self._nominal_item(saved, item_id)
        views = self.nominal_views(saved, poses)
        return {**self._relation_view(item, saved["features"], version), "nominals": views.get(item_id, [])}

    def unapprove(self, pid: str, sid: str, item_id: str, value: str) -> None:
        """``DELETE .../features/{id}/nominal/{value}``: forget an approval (404 when there is none).
        A record left with none loses its ``nominals`` key, as it was before."""
        self._dir(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            _, item = self._nominal_item(doc, item_id)
            kept = item.get("nominals") or []
            rest = [n for n in kept if n["value"] != value]
            if len(rest) == len(kept):
                raise ApiError(404, f"\"{item['name']}\" has no approved {value.replace('_', ' ')} any more.", _GONE)
            if rest:
                item["nominals"] = rest
            else:
                item.pop("nominals", None)
            self._save(pid, sid, doc)

    @staticmethod
    def _name(name: str | None, default: str) -> str:
        if name is None:
            return default
        try:
            return kf.clean_name(name)
        except ValueError as e:
            raise ApiError(422, plain_reason(e, "This name cannot be used."),
                           f"Give a name of 1 to {kf.NAME_MAX} characters.") from None

    @staticmethod
    def _find(doc: dict, item_id: str) -> tuple[str, int]:
        for key in ("features", "measurements", "relations"):
            for i, it in enumerate(doc[key]):
                if it["id"] == item_id:
                    return key, i
        raise ApiError(404, "This fitted shape, measurement or relation is no longer in the scan.", _GONE)

    @staticmethod
    def _record(fit: dict, seed: dict, fid: str, name: str, created: str) -> tuple[dict, dict | None]:
        """A feature record from the fit route's answer (asked ``with_extent`` and ``with_form``), and
        the large part of its extent for the side file (or None). The faces are left out: they go to
        their own file."""
        extent, side = kf.split_extent(fit.get("extent"))
        rec = {"id": fid, "name": name, "kind": fit["kind"], "shape": fit["shape"], "seed": seed,
               "n_faces": int(fit["n_faces"]), "tolerance_mm": fit["tolerance_mm"],
               "mean_sigma_mm": fit["mean_sigma_mm"], "correlated_points": fit["correlated_points"],
               "warnings": list(fit["warnings"]), "mesh_version": int(fit["version"]), "extent": extent,
               "form": fit.get("form"), "created": created}
        return rec, side

    @staticmethod
    def _keep_files(d, fid: str, fit: dict, side: dict | None) -> None:
        """Write a fit's region and outline files (an outline kept before and no longer needed goes)."""
        kf.save_faces(d, fid, np.frombuffer(base64.b64decode(fit["faces"]), dtype="<u4"))
        if side is None:
            kf.remove_outline(d, fid)
        else:
            kf.save_outline(d, fid, side, int(fit["version"]))

    # ------------------------------------------------------------------ routes
    def get(self, pid: str, sid: str) -> dict:
        """``GET .../features``: the scan's kept set, each feature and measurement marked ``stale``
        when it was made on another mesh version than the current one."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
        return self._set_view(doc, self._version(pid, sid), self.poses(pid, sid)[0])

    def snapshot(self, pid: str, sid: str, plane_outlines: bool = False) -> tuple[dict, dict, int | None]:
        """The scan's kept set as ``features.json`` holds it (no ``stale`` marks), read under the
        scan's lock, with (when ``plane_outlines``) each kept plane's outline by its id, only one
        kept for the fit's own mesh version (as :func:`ohs.measure.features.load_outline` gives it),
        and the current mesh's version (None before there is a mesh). Reads only; 404 for an
        unknown project or scan. For the CAD export (:mod:`ohs.server.cad`)."""
        d = self._dir(pid, sid)
        outlines: dict[str, dict] = {}
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            for f in doc["features"] if plane_outlines else []:
                if f["kind"] == "plane":
                    ol = kf.load_outline(d, f["id"])
                    if ol is not None and ol["mesh_version"] == f["mesh_version"]:  # not left over from another fit
                        outlines[f["id"]] = ol
        return doc, outlines, self._version(pid, sid)

    def change_sections(self, pid: str, sid: str, fn: Callable[[list[dict], dict], Any]) -> Any:
        """Change the scan's kept sections (FEATURE_PLAN I1) under the scan's lock, for
        :mod:`ohs.server.sections`: ``fn(sections, doc)`` gets the list (copies of the records, to
        change in place) and the whole kept set (to read), and returns the answer. The list is
        written only when it changed, and left out of the file when it is empty (as before any
        section was kept). An error raised by ``fn`` writes nothing. 404 for an unknown project or scan."""
        self._dir(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            before = list(doc.get("sections") or [])
            sections = [dict(s) for s in before]
            out = fn(sections, doc)
            if sections != before:
                if sections:
                    doc["sections"] = sections
                else:
                    doc.pop("sections", None)
                self._save(pid, sid, doc)
        return out

    def change_seams(self, pid: str, sid: str, fn: Callable[[list[dict], dict], Any]) -> Any:
        """Change the scan's kept seams (FEATURE_PLAN I4) under the scan's lock, for
        :mod:`ohs.server.flush`, as :meth:`change_sections` does for the sections: ``fn(seams, doc)``
        changes copies of the records in place and returns the answer; the list is written only when
        it changed, and left out of the file when it is empty. An error raised by ``fn`` writes
        nothing. 404 for an unknown project or scan."""
        self._dir(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            before = list(doc.get("seams") or [])
            seams = [dict(s) for s in before]
            out = fn(seams, doc)
            if seams != before:
                if seams:
                    doc["seams"] = seams
                else:
                    doc.pop("seams", None)
                self._save(pid, sid, doc)
        return out

    def add_fit(self, pid: str, sid: str, kind: str, face: int, point: Any, tolerance_mm: float | None,
                version: int | None, name: str | None) -> dict:
        """``POST .../features``: fit as ``POST .../fit`` does and keep it. Returns the feature with
        its region (``faces``, base64 as in the fit's answer)."""
        self._dir(pid, sid)
        name = None if name is None else self._name(name, "")
        fit = self.st.processing.fit(pid, sid, kind, face, point, tolerance_mm, version, with_extent=True,
                                     with_form=True)
        seed = {"kind": kind, "face": int(face), "point": [float(x) for x in point],
                "tolerance_mm": None if tolerance_mm is None else float(tolerance_mm)}
        d = self.st.projects.scan_dir(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            fid = kf.new_id("f")
            rec, side = self._record(fit, seed, fid, name or kf.next_name(doc["features"], "Fit"), kf.now())
            doc["features"].append(rec)
            try:
                self._keep_files(d, fid, fit, side)
                self._save(pid, sid, doc)
            except BaseException:
                kf.remove_faces(d, fid)
                kf.remove_outline(d, fid)
                raise
        return {**self._view(rec, int(fit["version"])), "faces": fit["faces"]}

    def add_measurement(self, pid: str, sid: str, a: Any, b: Any, version: int | None, name: str | None) -> dict:
        """``POST .../features/measurements``: keep a two-point distance. ``version``: the mesh it
        was measured on (the current one when not given)."""
        self._dir(pid, sid)
        current = self._version(pid, sid)
        mv = current if version is None else int(version)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            rec = {"id": kf.new_id("m"), "name": self._name(name, kf.next_name(doc["measurements"], "Distance")),
                   "a": [float(x) for x in a], "b": [float(x) for x in b], "mesh_version": mv, "created": kf.now()}
            doc["measurements"].append(rec)
            self._save(pid, sid, doc)
        return self._view(rec, current)

    def add_relation(self, pid: str, sid: str, a: str, b: str, name: str | None) -> dict:
        """``POST .../features/relations``: keep that two features are to be related. Returns it as
        ``GET`` shows it (with the engine's record)."""
        self._dir(pid, sid)
        version = self._version(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            ids = {f["id"] for f in doc["features"]}
            for ref in (a, b):
                if ref not in ids:
                    raise ApiError(422, "One of the two shapes to relate is not kept.",
                                   "Keep both fitted shapes first, then relate them.")
            if a == b:
                raise ApiError(422, "A fitted shape cannot be related to itself.", "Pick two different shapes.")
            rec = {"id": kf.new_id("r"), "name": self._name(name, kf.next_name(doc["relations"], "Relation")),
                   "a": a, "b": b, "created": kf.now()}
            doc["relations"].append(rec)
            self._save(pid, sid, doc)
        return self._relation_view(rec, doc["features"], version)

    def relate(self, pid: str, sid: str, a: str, b: str) -> dict:
        """``GET .../features/relate?a=&b=``: how two kept fits relate, as a kept relation shows it,
        without keeping it: ``{"a", "b", "stale", "relation"}``. 404 for an id that names no kept
        fit, 422 for one fit twice."""
        self._dir(pid, sid)
        version = self._version(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
        ids = {f["id"] for f in doc["features"]}
        for ref in (a, b):
            if ref not in ids:
                raise ApiError(404, "One of these fitted shapes is no longer in the scan.", _GONE)
        if a == b:
            raise ApiError(422, "A fitted shape cannot be related to itself.", "Pick two different shapes.")
        out = self._relation_view({"a": a, "b": b}, doc["features"], version)
        return {"a": a, "b": b, "stale": out["stale"], "relation": out["relation"],
                **({"error": out["error"]} if "error" in out else {})}

    def rename(self, pid: str, sid: str, item_id: str, name: str) -> dict:
        """``PATCH .../features/{id}``: a new name for a feature, measurement or relation."""
        new = self._name(name, "")
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            key, i = self._find(doc, item_id)
            doc[key][i] = {**doc[key][i], "name": new}
            saved = self._save(pid, sid, doc)
            item = saved[key][i]
        version = self._version(pid, sid)
        return self._relation_view(item, saved["features"], version) if key == "relations" else self._view(item, version)

    def delete(self, pid: str, sid: str, item_id: str) -> None:
        """``DELETE .../features/{id}``: forget an item; a feature takes its relations, region and outline with it."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            key, i = self._find(doc, item_id)
            del doc[key][i]
            if key == "features":
                doc["relations"] = [r for r in doc["relations"] if item_id not in (r["a"], r["b"])]
            self._save(pid, sid, doc)
            if key == "features":
                kf.remove_faces(self.st.projects.scan_dir(pid, sid), item_id)
                kf.remove_outline(self.st.projects.scan_dir(pid, sid), item_id)

    def faces(self, pid: str, sid: str, fid: str) -> dict:
        """``GET .../features/{id}/faces``: a feature's region, for the mesh version it was fitted on."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            feat = next((f for f in doc["features"] if f["id"] == fid), None)
            faces = None if feat is None else kf.load_faces(self.st.projects.scan_dir(pid, sid), fid)
        if feat is None or faces is None:
            raise ApiError(404, "The surface this shape was fitted on is no longer kept.", _GONE)
        return {"id": fid, "mesh_version": int(feat["mesh_version"]), "n_faces": int(len(faces)),
                "faces": base64.b64encode(faces.astype("<u4").tobytes()).decode("ascii")}

    def outline(self, pid: str, sid: str, fid: str) -> dict:
        """``GET .../features/{id}/outline``: a feature's whole outline, for the mesh version it was
        fitted on: a plane's loops (2D in its basis ``origin``, ``u``, ``v``, as ``region_extent``
        gives them) or a hole's edge (3D). 404 for a cylinder (it has no outline; its span and arc are
        in ``extent``), and for a fit kept before extents existed (fit it again)."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            feat = next((f for f in doc["features"] if f["id"] == fid), None)
            out = None if feat is None else kf.load_outline(self.st.projects.scan_dir(pid, sid), fid)
        if out is not None and out["mesh_version"] != feat["mesh_version"]:
            out = None  # left over from another fit of it (a write that did not finish): not this fit's
        if feat is None:
            raise ApiError(404, "This fitted shape is no longer in the scan.", _GONE)
        ext = feat.get("extent")
        if out is None and ext is not None and ext.get("kind") == "hole" and ext.get("edge") is not None:
            out = {"kind": "hole", "edge": ext["edge"]}  # a short edge is kept in features.json itself
        if out is None:
            why = ("A cylinder has no outline: its length and its arc are shown instead." if ext is not None
                   else "This shape was kept before outlines were measured, so it has none.")
            raise ApiError(404, why, "Press Fit again to measure its outline." if ext is None else
                           "Look at its length and arc instead.")
        out.pop("mesh_version", None)
        return {"id": fid, "mesh_version": int(feat["mesh_version"]), **out}

    def refit(self, pid: str, sid: str, ids: list[str] | None = None) -> dict:
        """``POST .../features/refit``: fit the stale features (all, or those of ``ids``) again on the
        current mesh, from their seeds, and those kept before extents existed (``extent`` null): a
        current one of those from its own click, so its shape stays the same and it gains its
        extent. Returns the set as ``GET`` does, which ones were refitted and which could not be
        (with the reason); those stay as they were, still marked."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
        if ids is not None:
            known = {f["id"] for f in doc["features"]}
            for i in ids:
                if i not in known:
                    raise ApiError(404, "One of these fitted shapes is no longer in the scan.", _GONE)
        version = self._version(pid, sid)
        if version is None:
            raise ApiError(409, "This scan has no mesh yet.", "Press Make mesh first.")
        todo = [f for f in doc["features"] if (f["mesh_version"] != version or f.get("extent") is None)
                and (ids is None or f["id"] in ids)]
        done: dict[str, tuple[dict, dict]] = {}
        failed: list[dict] = []
        if todo:
            V, F, Fl, v = self._mesh(pid, sid)
            C = V[F].mean(axis=1)
            measured = np.flatnonzero(~Fl) if Fl is not None and len(Fl) == len(F) else None
            for f in todo:
                seed = f["seed"]
                p = np.asarray(seed["point"], dtype=np.float64)
                if f["mesh_version"] == v and 0 <= int(seed["face"]) < len(F):
                    face = int(seed["face"])  # not stale (only its extent is missing): its own click
                elif measured is not None and len(measured):
                    face = int(measured[np.argmin(np.einsum("ij,ij->i", C[measured] - p, C[measured] - p))])
                elif len(C):
                    face = int(np.argmin(np.einsum("ij,ij->i", C - p, C - p)))
                else:
                    face = None
                gone = self._gone(V, F, C, face, p)
                if gone is not None:
                    failed.append({"id": f["id"], "detail": sentence(gone),
                                   "hint": "Click the shape again on this mesh, or delete it."})
                    continue
                try:
                    fit = self.st.processing.fit(pid, sid, f["kind"], face, seed["point"], seed["tolerance_mm"], v,
                                                 with_extent=True, with_form=True)
                except ApiError as e:
                    failed.append({"id": f["id"], "detail": e.detail, "hint": e.hint})
                    continue
                done[f["id"]] = (f, {**fit, "face": face})
        refitted: list[str] = []
        d = self.st.projects.scan_dir(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            for i, cur in enumerate(doc["features"]):
                hit = done.get(cur["id"])
                if hit is None:
                    continue
                before, fit = hit
                if cur != before:  # changed meanwhile (renamed, refitted): kept as it is now
                    continue
                seed = {**cur["seed"], "face": fit["face"]}
                rec, side = self._record(fit, seed, cur["id"], cur["name"], cur["created"])
                doc["features"][i] = {**cur, **rec}
                self._keep_files(d, cur["id"], fit, side)
                refitted.append(cur["id"])
            if refitted:
                self._save(pid, sid, doc)
        return {"features": self._set_view(doc, self._version(pid, sid), self.poses(pid, sid)[0]), "refitted": refitted,
                "failed": failed}

    # ------------------------------------------------------------------ the part's frame (K3)
    def build_frame(self, pid: str, sid: str, primary: str, secondary: str, origin: Any, axes: str = "ZX",
                    flip_primary: bool = False, flip_secondary: bool = False, secondary_zero: bool = False) -> dict:
        """``POST .../features/frame``: build the part's frame from three kept fits and keep it.

        ``primary`` and ``secondary`` are kept fit ids; ``origin`` is one too, or a point
        ``{"point": [x, y, z], "sigma_mm": s | None}`` in the scan's frame. Each fit goes to
        :func:`~ohs.measure.frame.part_frame` as its id, name and shape. Replaces a frame kept
        before. Returns the frame as ``GET`` shows it. 422 for an unknown fit or a refusal of the
        engine (its message as the detail), 409 before there is a mesh and for a fit made on an
        earlier mesh (its numbers are not this mesh's)."""
        from ohs.measure.frame import part_frame

        self._dir(pid, sid)
        version = self._version(pid, sid)
        if version is None:
            raise ApiError(409, "This scan has no mesh yet.",
                           "Press Make mesh, fit the shapes, then set the part's axes.")
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            by_id = {f["id"]: f for f in doc["features"]}
            slots = []
            for role, ref in (("primary", primary), ("secondary", secondary), ("origin", origin)):
                if isinstance(ref, dict):  # a point as the origin
                    point = {"kind": "point", "point": [float(x) for x in ref["point"]]}
                    if ref.get("sigma_mm") is not None:
                        point["sigma_mm"] = float(ref["sigma_mm"])
                    slots.append(point)
                    continue
                f = by_id.get(ref)
                if f is None:
                    raise ApiError(422, f"The shape chosen for the {role} is not kept.",
                                   "Pick one of the kept shapes, or keep the shape first.")
                if f["mesh_version"] != version:
                    raise ApiError(409, f"The {role} \"{f['name']}\" was fitted on an earlier mesh, before the "
                                        "last edit.", "Press Fit again on it first, then set the part's axes.")
                slots.append({"id": f["id"], "name": f["name"], "fit": f["shape"]})
            try:
                frame = part_frame(*slots, axes=axes, flip_primary=flip_primary, flip_secondary=flip_secondary,
                                   secondary_zero=secondary_zero)
            except ValueError as e:
                raise ApiError(422, plain_reason(e, "These shapes cannot set the part's axes."),
                               "Pick other shapes, or change the options.") from None
            doc["frame"] = frame.to_dict()
            saved = self._save(pid, sid, doc)
        return self._frame_view(saved["frame"], saved["features"])

    def remove_frame(self, pid: str, sid: str) -> None:
        """``DELETE .../features/frame``: forget the part's frame (nothing happens without one)."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            if doc["frame"] is not None:
                doc["frame"] = None
                self._save(pid, sid, doc)

    def frame_mesh(self, pid: str, sid: str, name: str) -> tuple[bytes, str, int | None]:
        """``GET .../features/frame/mesh.ply`` (or ``mesh.stl``): the scan's current result mesh
        (``result/mesh.ply``) with its vertices and normals turned into the part's frame
        (:meth:`PartFrame.mesh_to_part <ohs.measure.frame.PartFrame.mesh_to_part>`); the PLY keeps
        each vertex's sigma and count of looks and each face's filled flag. Made on request in a temporary folder: the
        scan's own files are not touched. Returns the file's bytes, its download name and the mesh
        version read before it (the file is that version or a newer one). 404 without a frame or a
        mesh, 409 for a frame this version cannot read. An out-of-date frame is used as it is (the
        viewer says so)."""
        from ohs.measure.frame import PartFrame
        from ohs.track.export import load_ply, save_mesh, write_stl

        if name not in ("mesh.ply", "mesh.stl"):
            raise ApiError(404, "The mesh in the part's axes comes as PLY or STL only.", "Download one of those two.")
        with self._lock(pid, sid):
            frame = self._load(pid, sid)["frame"]
        if frame is None:
            raise ApiError(404, "The part's axes are not set for this scan.", "Set them first, from the fitted shapes.")
        try:
            pf = PartFrame.from_dict(frame)
        except (ValueError, TypeError, AttributeError, KeyError) as e:
            log.warning("the part's frame of %s/%s cannot be read: %s", pid, sid, e)
            raise ApiError(409, "This version of the app cannot read the part's axes of this scan.",
                           "Set the axes again, or remove them.") from None
        version = self._version(pid, sid)
        path, _ = self.st.projects.file_path(pid, sid, "mesh.ply")  # 404 before there is a mesh
        m = load_ply(path)
        V, N = pf.mesh_to_part(m["vertices"], m["normals"])
        F = m["faces"] if m["faces"] is not None else np.zeros((0, 3), dtype=np.int64)
        with tempfile.TemporaryDirectory(prefix="ohs-frame-") as tmp:
            out = Path(tmp) / name
            if name == "mesh.stl":
                write_stl(out, V, F)
            else:
                save_mesh(out, V, F, N, quality=m["quality"], filled=m["filled"], looks=m.get("looks"))
            data = out.read_bytes()
        return data, f"mesh-part-frame.{name.rsplit('.', 1)[1]}", version

    @staticmethod
    def _gone(V: np.ndarray, F: np.ndarray, C: np.ndarray, face: int | None, p: np.ndarray) -> str | None:
        """Why a saved click cannot be fitted again, when the surface under it is gone: the nearest
        measured face is farther from the clicked point than :data:`GONE_MIN_MM` and than
        :data:`GONE_EDGES` of its own longest edge (on a coarse mesh the face centres lie far
        apart). None when the surface is still there. Without this a fit whose region was cut away
        would be fitted again on whatever surface is nearest, under the same name."""
        if face is None:
            return "the mesh has no measured surface to fit on"
        dist = float(np.linalg.norm(C[face] - p))
        tri = V[F[face]].astype(np.float64)
        edge = float(np.linalg.norm(tri - tri[[1, 2, 0]], axis=1).max())
        if dist <= max(GONE_MIN_MM, GONE_EDGES * edge):
            return None
        return (f"the surface under this fit is gone: the nearest measured surface is {dist:.1f} mm from where "
                "it was clicked")

    def _mesh(self, pid: str, sid: str) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, int]:
        """The current mesh's vertices, faces, filled flags and version: the arrays the fit route
        keeps per mesh version (:meth:`ProcessManager.mesh_arrays
        <ohs.server.processing.ProcessManager.mesh_arrays>`, loaded once, shared with it)."""
        v, V, F, _, Fl = self.st.processing.mesh_arrays(pid, sid)
        return V, F, Fl, v
