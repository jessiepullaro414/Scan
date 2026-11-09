"""Printables for the setup wizard: the calibration board, the AprilTag sheet and the LED projector mask.

:func:`generate` writes, into ``paths.printables_dir`` (like ``ohs printables``):

* ``charuco_board.pdf`` / ``.png``: the ChArUco board page at 300 dpi with its 100 mm scale bar
  (:func:`ohs.calib.board.render_charuco_page`), always from the board *as configured* (a
  measured print scale only ever changes the calibration, never the print);
* ``apriltags.pdf`` / ``.png`` (``apriltags_p<k>.png`` for several sheets): 48 tag36h11 stickers
  of ``markers.tag_mm`` (:func:`ohs.calib.board.render_apriltag_sheet`);
* the LED projector mask, the other option for the dot projector
  (:func:`ohs.projector.mask.write_mask_files`): ``projector_mask_sheet.pdf`` / ``.png`` for a
  laser printer on transparency film, ``projector_mask_film.gbr`` / ``.svg`` for a film service,
  plus ``projector_mask_preview.png``, one mask enlarged (the sheet itself is a 1200 dpi page of
  tiny dots that shows nothing at screen size).

The manifest of what was written (names, kinds, paper, time) is kept in the setup state, and
:func:`listing` turns it into ``Printables`` (APP_API.md). Files are served from a strict
whitelist: the name must match :data:`NAME_RE`, be in the manifest and exist in its folder.
:func:`open_folder` shows the folder in the system file manager.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from ohs.server.protocol import ApiError, now_iso

__all__ = ["generate", "listing", "file_path", "open_folder", "NAME_RE", "MEDIA_TYPES", "MASK_DPI", "TAG_COUNT"]

log = logging.getLogger(__name__)

MASK_DPI = 1200
MASK_DOT_UM = 42.0
BOARD_DPI = 300
TAG_COUNT = 48
NAME_RE = re.compile(r"^(charuco_board\.(pdf|png)|apriltags(_p\d{1,2})?\.(pdf|png)|projector_mask_sheet\.(pdf|png)"
                     r"|projector_mask_film\.(gbr|svg)|projector_mask_preview\.png)$")
MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".gbr": "application/vnd.gerber",
               ".svg": "image/svg+xml"}


def _describe(name: str, manifest: dict) -> tuple[str, str]:
    """(title, description) of a printable, in plain words."""
    tag_mm = manifest.get("tag_mm", 20.0)
    dpi = manifest.get("mask_dpi", MASK_DPI)
    if name == "charuco_board.pdf":
        return ("Calibration board (PDF)",
                "Print at 100 % / Actual size (turn off “Fit to page”) on matte paper, glue it flat to glass, "
                "then measure the 100 mm bar with calipers.")
    if name == "charuco_board.png":
        return ("Calibration board (PNG image)",
                f"The same page as a {BOARD_DPI} dpi image, for printers that cannot print the PDF at actual size.")
    if name == "apriltags.pdf":
        return ("AprilTag stickers (PDF)",
                f"{TAG_COUNT} tags of {tag_mm:g} mm for marker mode. Print at 100 % on matte removable label paper and "
                "cut along the dashed lines.")
    if name.startswith("apriltags"):
        m = re.match(r"apriltags_p(\d+)\.png", name)
        sheet = f", sheet {m.group(1)}" if m else ""
        return (f"AprilTag stickers (PNG image{sheet})",
                f"The sticker sheet as a {BOARD_DPI} dpi image. Print at 100 % on matte removable label paper.")
    if name == "projector_mask_sheet.pdf":
        return ("LED projector mask, option B (PDF)",
                "Another option for the dot projector: print at 100 % on laser transparency film "
                f"({dpi} dpi), then cut out the cleanest of the copies.")
    if name == "projector_mask_sheet.png":
        return ("LED projector mask sheet (PNG image)",
                f"The same sheet as a {dpi} dpi image, for printing software that needs an image.")
    if name == "projector_mask_film.gbr":
        return ("LED projector mask for a film service (Gerber)",
                "Send this to a photoplot / film service for a sharper mask with 25 µm dots.")
    if name == "projector_mask_film.svg":
        return ("LED projector mask for a film service (SVG)",
                "The film mask as a vector drawing, for print services that do not take Gerber files.")
    return name, ""


_KINDS = (("charuco_board", "board"), ("apriltags", "tags"), ("projector_mask", "mask"))


def _kind(name: str) -> str:
    return next(k for prefix, k in _KINDS if name.startswith(prefix))


def _preview_for(name: str, names: set[str]) -> str | None:
    kind = _kind(name)
    if kind == "board":
        return "charuco_board.png" if "charuco_board.png" in names else None
    if kind == "tags":
        if name.endswith(".png"):
            return name
        return next((n for n in ("apriltags.png", "apriltags_p1.png") if n in names), None)
    return "projector_mask_preview.png" if "projector_mask_preview.png" in names else None


def _mask_preview(path: Path, spec: Any, dpi: int) -> Path:
    """One mask, enlarged with square pixels, inside a thin frame (PNG)."""
    import cv2
    import numpy as np

    from ohs.projector.mask import render_mask

    img = render_mask(spec, dpi=max(int(dpi), 600))
    k = max(1, int(round(800 / img.shape[1])))
    big = cv2.resize(img, (img.shape[1] * k, img.shape[0] * k), interpolation=cv2.INTER_NEAREST)
    big = cv2.copyMakeBorder(big, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=128)
    if not cv2.imwrite(str(path), np.ascontiguousarray(big)):
        raise OSError(f"cannot write {path}")
    return path


def generate(out_dir: str | Path, spec: Any, paper: str, *, tag_mm: float = 20.0, mask_dpi: int = MASK_DPI,
             progress: Callable[[float, str], None] | None = None) -> dict:
    """Write every printable into ``out_dir``; returns the manifest (see the module docstring)."""
    from ohs.calib.board import render_apriltag_sheet, render_charuco_page
    from ohs.projector.mask import MaskSpec, write_mask_files

    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda f, m: None)
    for old in out.iterdir():  # stale pages of an earlier run (another paper, fewer tag sheets)
        if old.is_file() and NAME_RE.match(old.name):
            try:
                old.unlink()
            except OSError as e:
                log.warning("could not remove the old printable %s: %s", old, e)
    say(0.05, "Drawing the calibration board…")
    pdf, png = render_charuco_page(spec, out / "charuco_board", paper=paper, dpi=BOARD_DPI)
    names = [pdf.name, png.name]
    say(0.25, "Drawing the AprilTag stickers…")
    tpdf, tpngs = render_apriltag_sheet(out / "apriltags", tag_mm=float(tag_mm), ids=range(TAG_COUNT), paper=paper,
                                        dpi=BOARD_DPI)
    names += [tpdf.name, *[p.name for p in tpngs]]
    say(0.45, "Drawing the LED projector mask (about 20 s)…")
    mspec = MaskSpec(dot_um=MASK_DOT_UM)
    info = write_mask_files(out, spec=mspec, dpi=int(mask_dpi), paper=paper)
    names += [Path(info[k]).name for k in ("sheet_pdf", "sheet_png", "gerber", "svg") if k in info]
    say(0.92, "Drawing the previews…")
    try:
        names.append(_mask_preview(out / "projector_mask_preview.png", mspec, int(mask_dpi)).name)
    except Exception as e:  # a preview is a nicety
        log.warning("no projector mask preview: %s", e)
    names = [n for n in dict.fromkeys(names) if NAME_RE.match(n)]
    return {"dir": str(out), "generated": now_iso(), "paper": str(paper).lower(), "files": names,
            "tag_mm": float(tag_mm), "mask_dpi": int(mask_dpi), "board": spec.describe(),
            "mask": {"n_dots": int(info.get("n_dots", 0)), "fov_deg": [float(v) for v in info.get("fov_deg", (0, 0))]}}


def listing(manifest: dict | None, default_dir: str | Path, paper: str) -> dict:
    """``Printables``: what the last run wrote (files that still exist), or an empty list."""
    if not manifest:
        return {"dir": str(Path(default_dir).expanduser()), "generated": None, "paper": str(paper).lower(), "files": []}
    folder = Path(manifest.get("dir") or default_dir)
    present = [n for n in manifest.get("files", []) if NAME_RE.match(str(n)) and (folder / n).is_file()]
    names = set(present)
    files = []
    for n in present:
        title, desc = _describe(n, manifest)
        prev = _preview_for(n, names)
        try:
            size = (folder / n).stat().st_size
        except OSError:
            continue
        files.append({"name": n, "kind": _kind(n), "title": title, "description": desc,
                      "url": f"/api/setup/printables/files/{n}",
                      "preview_url": f"/api/setup/printables/files/{prev}" if prev else None, "bytes": int(size)})
    hidden = {"projector_mask_preview.png"}
    files = [f for f in files if f["name"] not in hidden]
    return {"dir": str(folder), "generated": manifest.get("generated"), "paper": manifest.get("paper", paper),
            "files": files}


def file_path(manifest: dict | None, name: str) -> tuple[Path, str]:
    """``(path, media type)`` of a generated printable; 404 for anything else."""
    if not isinstance(name, str) or not NAME_RE.match(name) or not manifest or name not in manifest.get("files", []):
        raise ApiError(404, f"no printable {name!r}", "generate the printables first, then use the names from "
                                                      "GET /api/setup/printables")
    folder = Path(manifest.get("dir") or "")
    path = (folder / name).resolve()
    try:
        inside = folder.resolve() == path.parent
    except OSError:
        inside = False
    if not inside or not path.is_file():
        raise ApiError(404, f"{name} is no longer in {folder}", "generate the printables again")
    return path, MEDIA_TYPES[path.suffix.lower()]


def open_folder(path: str | Path) -> bool:
    """Show ``path`` in the system file manager; False if that did not work."""
    p = Path(path).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        return True
    except Exception as e:
        log.warning("could not open the folder %s: %s", p, e)
        return False
