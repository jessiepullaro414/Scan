"""Aligning several scans of one part and merging them (desktop app plan M4, research plan 4.7).

* :mod:`ohs.align.pairwise`: two scans to each other, from their meshes.
* :mod:`ohs.align.many`: several scans at once, through the links between them.
* :mod:`ohs.align.merge`: one model from the raw frames of all aligned scans.
"""
from ohs.align.many import ManyAlignment, align_many
from ohs.align.merge import ScanInput, merge_scans
from ohs.align.pairwise import (AlignParams, PairAlignment, ScanCloud, align_pair, pose_from_point_pairs, scan_cloud,
                                scan_cloud_from_mesh)

__all__ = ["AlignParams", "ManyAlignment", "PairAlignment", "ScanCloud", "ScanInput", "align_many", "align_pair",
           "merge_scans", "pose_from_point_pairs", "scan_cloud", "scan_cloud_from_mesh"]
