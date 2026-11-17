"""The report of one scan (FEATURE_PLAN I3): what an engineer hands on with a part.

One self-contained HTML page per scan that reads well on screen and prints cleanly to paper or PDF
from any browser (the owner's decision D1: no PDF library), and the kept features beside it as CSV
and JSON. Everything is built from one plain structure of data, each part as the engine already
gives it and each part optional (:mod:`ohs.report.data`); collecting that data from a scan is the
server's job.

* :func:`build_html`: the page (:mod:`ohs.report.html`);
* :func:`features_csv`, :func:`features_json`, :func:`features_document`: the kept features
  (:mod:`ohs.report.tables`);
* :func:`section_entry`: a kept section, cut on the current mesh, as the report shows it;
* :mod:`ohs.report.numbers`: how a value and its ± are written.
"""
from ohs.report.data import FORMAT_VERSION, PARTS, prepare, section_entry
from ohs.report.html import MEANING, build_html
from ohs.report.tables import CSV_COLUMNS, features_csv, features_document, features_json

__all__ = ["FORMAT_VERSION", "PARTS", "MEANING", "CSV_COLUMNS", "build_html", "features_csv", "features_document",
           "features_json", "prepare", "section_entry"]
