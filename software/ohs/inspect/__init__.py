"""Inspection: the scan held against what the part should be (FEATURE_PLAN, section I).

* :mod:`ohs.inspect.reference`: a scan compared with a reference model (STL, OBJ or PLY) of the
  part: reading it, aligning the scan to it, the deviation, and the answer for a tolerance.
* :mod:`ohs.inspect.gapflush`: the flush between two panels along a seam (and the gap, which is
  worked out but not offered: the scan closes narrow gaps; see ``GAP_NOTE``).
"""
from ohs.inspect.gapflush import (DEFAULT_RULE, EDGE_RULES, GAP_NOTE, GAP_OFFERED, GapFlush, GapFlushStation,
                                  SeamStats, measure_gap_flush)
from ohs.inspect.reference import (Comparison, Deviation, DeviationRegion, ReferenceAlignment, ReferenceModel,
                                   ScanSurface, ToleranceSummary, UnitsFinding, align_by_datums, align_by_points,
                                   align_by_shape, check_units, compare_with_reference, deviation_from_reference,
                                   given_alignment, read_reference, reference_from_arrays, tolerance_summary)

__all__ = ["Comparison", "Deviation", "DeviationRegion", "ReferenceAlignment", "ReferenceModel", "ScanSurface",
           "ToleranceSummary", "UnitsFinding", "align_by_datums", "align_by_points", "align_by_shape", "check_units",
           "compare_with_reference", "deviation_from_reference", "given_alignment", "read_reference",
           "reference_from_arrays", "tolerance_summary",
           "DEFAULT_RULE", "EDGE_RULES", "GAP_NOTE", "GAP_OFFERED", "GapFlush", "GapFlushStation", "SeamStats",
           "measure_gap_flush"]
