"""Next-best-view guidance (FEATURE_PLAN N): where to scan a weak area again.

* :func:`suggest_view` / :func:`suggest_views` (:mod:`ohs.guide.views`): for an area of
  :func:`ohs.mesh.check.weak_areas`, the scanner pose (in the scan's frame, at the calibration's
  working distance) that would see most of it, tested against the mesh for the part being in the
  way and for looking into the part from behind; or, when none does, why not.
* :func:`plan_views`: a few such poses for several areas at once, without two for what one sees.
"""
from ohs.guide.views import AZIMUTHS, TILTS_DEG, VIEW_MIN_SHARE, plan_views, scanner_pose, suggest_view, suggest_views

__all__ = ["suggest_view", "suggest_views", "plan_views", "scanner_pose", "TILTS_DEG", "AZIMUTHS", "VIEW_MIN_SHARE"]
