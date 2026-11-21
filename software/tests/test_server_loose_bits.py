"""Removing loose bits says what it took (UX_PLAN U14; APP_API.md "EditEntry.removed").

The Default recipe removes pieces under 5 mm and under 1 % of the triangles: a thin or small part of the real part
can go with the noise. The edit's summary now says how many pieces went and how large the largest was, and the edit
entry carries the numbers (``removed``) so the page can show what was removed.
"""
from __future__ import annotations

import numpy as np

from ohs import mesh as M
from ohs.server.processing import ProcessManager, removed_pieces


def _box(x0: float, size: float) -> tuple[np.ndarray, np.ndarray]:
    """A closed box (12 triangles) at x0, ``size`` mm on each side."""
    c = np.array([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)], dtype=float) * size
    c[:, 0] += x0
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return c, F


def _scene() -> tuple[np.ndarray, np.ndarray]:
    parts = [_box(0.0, 50.0), _box(100.0, 4.2 / np.sqrt(3)), _box(200.0, 2.0 / np.sqrt(3))]
    V, F, off = [], [], 0
    for v, f in parts:
        V.append(v)
        F.append(f + off)
        off += len(v)
    return np.vstack(V), np.vstack(F)


def test_the_summary_says_how_large_the_largest_removed_bit_was():
    V, F = _scene()
    V2, F2, kept = M.remove_floaters(V, F, min_size_mm=5.0, return_kept=True)
    info = removed_pieces(V, F, kept)
    assert info == {"pieces": 2, "triangles": 24, "largest_mm": 4.2}
    s = ProcessManager._summary("remove_floaters", {"min_size_mm": 5.0}, V, F, V2, F2, {"components": 3},
                                {"components": 1}, kept=kept)
    assert s == "Removed 2 loose bits, the largest 4.2 mm across, smaller than 5 mm (24 triangles)"


def test_one_bit_and_none():
    V, F = _scene()
    V = V[:16]
    F = F[:24]  # the big box and the 4.2 mm one
    V2, F2, kept = M.remove_floaters(V, F, min_size_mm=5.0, return_kept=True)
    s = ProcessManager._summary("remove_floaters", {}, V, F, V2, F2, {"components": 2}, {"components": 1}, kept=kept)
    assert s == "Removed 1 loose bit, 4.2 mm across, smaller than 5 mm (12 triangles)"
    V3, F3, kept3 = M.remove_floaters(V2, F2, min_size_mm=5.0, return_kept=True)
    assert ProcessManager._summary("remove_floaters", {}, V2, F2, V3, F3, {"components": 1}, {"components": 1},
                                   kept=kept3) == "No loose bits matched: nothing was removed"
    assert removed_pieces(V2, F2, kept3) is None


def test_a_large_piece_removed_for_its_few_triangles_is_named_by_its_size():
    """Under 1 % of the triangles goes even when it is large: the size makes the user look (a thin tab)."""
    V, F = _scene()
    big = _box(300.0, 30.0)
    V = np.vstack([V, big[0]])
    F = np.vstack([F, big[1] + 24])
    V2, F2, kept = M.remove_floaters(V, F, min_fraction=0.3, return_kept=True)
    info = removed_pieces(V, F, kept)
    assert info["pieces"] == 3 and info["largest_mm"] == round(30.0 * np.sqrt(3), 1)


def test_the_summary_names_the_rule_that_took_the_largest_bit():
    """UX_PLAN R23: "297 loose bits removed, the largest 40.8 mm across" under a label that reads "smaller than 5 mm":
    the label's other half (under 1 % of the mesh) took it, and the line says so; the entry says it too."""
    V, F = _scene()
    big = _box(300.0, 30.0)
    V = np.vstack([V, big[0]])
    F = np.vstack([F, big[1] + 24])
    params = {"min_size_mm": 5.0, "min_fraction": 0.3}
    V2, F2, kept = M.remove_floaters(V, F, **params, return_kept=True)
    info = removed_pieces(V, F, kept, params)
    assert info["largest_rule"] == "share" and info["largest_triangles"] == 12
    s = ProcessManager._summary("remove_floaters", params, V, F, V2, F2, {"components": 4}, {"components": 1}, kept=kept)
    assert s == "Removed 3 loose bits, the largest 52.0 mm across but under 30 % of the mesh (36 triangles)"
    # the size took it: said as the size
    V2, F2, kept = M.remove_floaters(V[:24], F[:36], **params, return_kept=True)
    assert removed_pieces(V[:24], F[:36], kept, params)["largest_rule"] == "size"
    # keep the largest piece only
    V2, F2, kept = M.remove_floaters(V, F, keep_largest_only=True, return_kept=True)
    s = ProcessManager._summary("remove_floaters", {"keep_largest_only": True}, V, F, V2, F2, {"components": 4},
                                {"components": 1}, kept=kept)
    assert s == "Kept the largest piece only: removed 3 other pieces, the largest 52.0 mm across (36 triangles)"
    assert removed_pieces(V, F, kept, {"keep_largest_only": True})["largest_rule"] == "largest_only"
