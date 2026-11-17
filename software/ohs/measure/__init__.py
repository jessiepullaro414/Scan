"""Measuring on a scan (M5): shapes fitted to points, with their uncertainty. Pure functions, no I/O.

* :func:`fit_plane`, :func:`fit_cylinder`, :func:`fit_circle` (:mod:`ohs.measure.fit`): weighted
  fits on true geometric distances; each result has its ± values from the covariance (1σ,
  statistical errors only: calibration and scale errors come on top) and a ``to_dict()``.
* :func:`fit_at` (:mod:`ohs.measure.region`): one click on a mesh grows the region of a plane, a
  cylinder or a hole's edge and fits it; a :class:`RegionFit` with the region's faces, its extent
  (:func:`region_extent`: a plane's outline, a cylinder's span and arc, a hole's edge) and warnings.
* :func:`relate` (:mod:`ohs.measure.relate`): the angles and distances between two fits, as the
  viewer gives them, each with a ± from both fits.
"""
from __future__ import annotations

from ohs.measure.fit import CircleFit, CylinderFit, PlaneFit, fit_circle, fit_cylinder, fit_plane
from ohs.measure.region import KINDS, RegionFit, fit_at, region_extent
from ohs.measure.relate import PARALLEL_DEG, Quantity, Relation, relate

__all__ = ["fit_plane", "fit_cylinder", "fit_circle", "PlaneFit", "CylinderFit", "CircleFit", "fit_at", "RegionFit",
           "region_extent", "KINDS", "relate", "Relation", "Quantity", "PARALLEL_DEG"]
