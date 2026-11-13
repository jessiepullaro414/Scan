"""Mesh editing for the processing studio: pure functions on ``(V, F)``, no I/O.

``V`` is float64 (N,3) in mm, ``F`` int64 (M,3) vertex indices, consistently oriented. Every
operation returns a new ``(V, F)`` and leaves its inputs alone; the slow ones take an optional
``progress(fraction, message)`` callback.

* :func:`remove_floaters`, :func:`delete_faces`, :func:`keep_faces` (:mod:`ohs.mesh.clean`);
* :func:`fill_holes`, :func:`hole_outlines` (:mod:`ohs.mesh.holes`: Liepa-style smooth fill,
  outer borders are never filled);
* :func:`smooth` (:mod:`ohs.mesh.smoothing`: Taubin, or feature-preserving bilateral normal
  filtering);
* :func:`decimate` (:mod:`ohs.mesh.simplify`: quadric edge collapse, in batches);
* :func:`mesh_stats` (:mod:`ohs.mesh.stats`) and the topology helpers in
  :mod:`ohs.mesh.topology` (edges, boundary loops, outer borders, pieces);
* each vertex's confidence (:mod:`ohs.mesh.confidence`): :func:`sigma_stats`, and
  :func:`carry_sigma` through the operations above;
* :func:`anchor_to_points` (:mod:`ohs.mesh.anchor`): pull the surface onto the laser points
  with a smooth correction ("Refine with lasers");
* :func:`surface_offset`, :func:`offset_stats` (:mod:`ohs.mesh.offset`): how far an edited mesh
  lies from the measured one, signed, per vertex (the deviation map).

* :func:`weak_areas` (:mod:`ohs.mesh.check`): the areas whose confidence misses a tolerance, or
  that hole filling made up (the "Check" answer: what needs another pass).

What "hole" and "outer border" mean is defined once, in :mod:`ohs.mesh.topology`.
"""
from __future__ import annotations

from ohs.mesh.anchor import AnchorParams, anchor_to_points, laser_agreement
from ohs.mesh.check import weak_areas
from ohs.mesh.clean import delete_faces, keep_faces, remove_floaters
from ohs.mesh.confidence import SIGMA_NONE_MM, carry_filled, carry_sigma, filled_area, sigma_stats
from ohs.mesh.holes import fill_holes, hole_outlines
from ohs.mesh.offset import offset_stats, surface_offset
from ohs.mesh.simplify import decimate, deviation
from ohs.mesh.smoothing import smooth
from ohs.mesh.stats import mesh_stats
from ohs.mesh.topology import vertex_normals

__all__ = ["remove_floaters", "fill_holes", "smooth", "decimate", "delete_faces", "keep_faces", "mesh_stats",
           "hole_outlines", "vertex_normals", "sigma_stats", "carry_sigma", "SIGMA_NONE_MM", "AnchorParams",
           "anchor_to_points", "laser_agreement", "carry_filled", "filled_area", "deviation", "surface_offset", "offset_stats", "weak_areas"]
