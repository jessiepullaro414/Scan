"""Aligning several scans of one part and merging them (desktop app plan M4, research plan 4.7).

* :mod:`ohs.align.pairwise`: two scans to each other, from their meshes.
* :mod:`ohs.align.merge`: one model from the raw frames of all aligned scans.
"""
from ohs.align.merge import ScanInput, merge_scans
from ohs.align.pairwise import AlignParams, PairAlignment, ScanCloud, align_pair, scan_cloud, scan_cloud_from_mesh

__all__ = ["AlignParams", "PairAlignment", "ScanCloud", "ScanInput", "align_pair", "merge_scans", "scan_cloud",
           "scan_cloud_from_mesh"]
