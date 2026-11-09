"""ChArUco calibration board, AprilTag sticker printables, board detection and board pose.

Board frame ``B`` (OpenCV ``CharucoBoard`` convention, millimetres): origin at the top-left outer
corner of the chessboard as printed, ``x`` along the ``squares_x`` direction (to the right on the
page), ``y`` along ``squares_y`` (down the page), ``z = x cross y`` (into the board, i.e. away from
a camera looking at the printed face). ChArUco corner ``i`` is the inner chessboard corner at
``((i % (squares_x - 1) + 1) * square_mm, (i // (squares_x - 1) + 1) * square_mm, 0)``.

Printables are exact-scale *bilevel* raster pages. Every length is converted with
``px = round(mm * dpi / 25.4)`` and Pillow writes the PDF with ``resolution=dpi``, so one pixel is
exactly ``25.4 / dpi`` mm on paper. Bilevel pages are stored losslessly in the PDF
(CCITT G4); a greyscale page would be JPEG-compressed by Pillow and ring around the pattern edges.
"Light" lines (AprilTag cut lines) are therefore drawn dashed rather than grey. A PNG of the same
page, with dpi metadata, is written next to the PDF.

Note (OpenCV >= 4.6, including 5.x): boards with an even number of rows use the newer ChArUco
layout. Boards printed by this module always match the detector; boards printed by OpenCV < 4.6
would need ``CharucoBoard.setLegacyPattern(True)``. The pattern is pixel-identical to
``CharucoBoard.generateImage`` (cv2.aruco defaults), which the simulator uses as its texture.

Page layout (:func:`charuco_page_layout`, :class:`CharucoPageLayout`): where
:func:`render_charuco_page` puts the board, the scale bar and the label text on the paper. The
white paper around the board is coplanar with it once the page is glued to glass, and laser-plane
calibration uses that blank band (:meth:`CharucoPageLayout.margin_mask`) because a laser stripe
crossing black/white ink has its centre pulled toward the white side. The simulator's
``charuco_board`` scene renders this same page.

OpenCV 5 notes: ``CharucoDetector.detectBoard`` returns corners as (N, 2) float32 and ids as (N,)
int32 (OpenCV 4.x returned (N, 1, 2) / (N, 1)), and ``(None, None)`` when nothing is found.
"""
from __future__ import annotations

import functools
import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..geometry import make_se3, project_points, transform_points

__all__ = [
    "CharucoSpec",
    "PAPER_SIZES_MM",
    "APRILTAG_DICTIONARY",
    "MIN_POSE_CORNERS",
    "aruco_dictionary",
    "make_charuco_board",
    "board_corner_points",
    "render_board_image",
    "render_charuco_page",
    "render_charuco_page_image",
    "charuco_page_layout",
    "CharucoPageLayout",
    "render_apriltag_sheet",
    "detect_charuco",
    "estimate_board_pose",
    "reprojection_errors",
    "board_outline_uv",
    "BoardViewRenderer",
]

log = logging.getLogger(__name__)

#: Paper sizes (width, height) in portrait orientation, millimetres.
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {"letter": (215.9, 279.4), "a4": (210.0, 297.0)}
#: Dictionary used for the AprilTag stickers (markers mode).
APRILTAG_DICTIONARY = "DICT_APRILTAG_36h11"
#: Minimum number of ChArUco corners for a board pose.
MIN_POSE_CORNERS = 6

PAGE_MARGIN_MM = 10.0  # keep clear of the non-printable border of consumer printers
MAX_POSE_RMS_PX = 5.0  # a board pose with a larger reprojection rms is treated as a failure
_FONT_EM_MM = 3.2


# --------------------------------------------------------------------------- board definition
@dataclass(frozen=True)
class CharucoSpec:
    """Geometry of a ChArUco board (``squares_x`` x ``squares_y`` chessboard squares)."""

    squares_x: int = 11
    squares_y: int = 8
    square_mm: float = 20.0
    marker_mm: float = 15.0
    dictionary: str = "DICT_5X5_100"

    def __post_init__(self) -> None:
        if self.squares_x < 2 or self.squares_y < 2:
            raise ValueError("a ChArUco board needs at least 2 x 2 squares")
        if not 0.0 < self.marker_mm < self.square_mm:
            raise ValueError("marker_mm must be positive and smaller than square_mm")
        if not hasattr(cv2.aruco, self.dictionary):
            raise ValueError(f"unknown ArUco dictionary {self.dictionary!r}")

    @property
    def size_mm(self) -> tuple[float, float]:
        """(width, height) of the chessboard area in mm."""
        return self.squares_x * self.square_mm, self.squares_y * self.square_mm

    @property
    def n_corners(self) -> int:
        """Number of ChArUco (inner chessboard) corners."""
        return (self.squares_x - 1) * (self.squares_y - 1)

    def describe(self) -> str:
        return (
            f"ChArUco {self.squares_x} x {self.squares_y} squares, square {self.square_mm:.2f} mm, "
            f"marker {self.marker_mm:.2f} mm, {self.dictionary}"
        )


def aruco_dictionary(name: str) -> cv2.aruco.Dictionary:
    """Predefined ArUco/AprilTag dictionary by its OpenCV name, e.g. ``"DICT_5X5_100"``."""
    if not hasattr(cv2.aruco, name):
        raise ValueError(f"unknown ArUco dictionary {name!r}")
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def make_charuco_board(spec: CharucoSpec) -> cv2.aruco.CharucoBoard:
    """OpenCV board object for ``spec`` (lengths in mm)."""
    dictionary = aruco_dictionary(spec.dictionary)
    n_markers = (spec.squares_x * spec.squares_y) // 2
    if n_markers > dictionary.bytesList.shape[0]:
        raise ValueError(f"{spec.dictionary} has fewer than the {n_markers} markers this board needs")
    return cv2.aruco.CharucoBoard(
        (int(spec.squares_x), int(spec.squares_y)), float(spec.square_mm), float(spec.marker_mm), dictionary
    )


def board_corner_points(spec: CharucoSpec) -> np.ndarray:
    """(n_corners, 3) float64 ChArUco corner coordinates in the board frame (mm), indexed by id."""
    i = np.arange(spec.n_corners)
    nx = spec.squares_x - 1
    x = (i % nx + 1) * spec.square_mm
    y = (i // nx + 1) * spec.square_mm
    return np.stack([x, y, np.zeros_like(x, dtype=np.float64)], axis=1).astype(np.float64)


def render_board_image(spec: CharucoSpec, px_per_mm: float, border_bits: int = 1) -> np.ndarray:
    """Rasterise the bare board pattern (no margin) at an arbitrary, exact scale.

    Square and marker edges sit at ``round(mm * px_per_mm)`` (pixel-edge coordinates), so the
    pattern has the correct physical size to within half a pixel everywhere. Returns (H, W) uint8.
    """
    if px_per_mm <= 0:
        raise ValueError("px_per_mm must be positive")
    board = make_charuco_board(spec)
    sx, sy, s = spec.squares_x, spec.squares_y, spec.square_mm
    ex = np.round(np.arange(sx + 1) * s * px_per_mm).astype(int)
    ey = np.round(np.arange(sy + 1) * s * px_per_mm).astype(int)
    img = np.full((ey[-1], ex[-1]), 255, np.uint8)

    obj = np.asarray(board.getObjPoints(), dtype=np.float64).reshape(-1, 4, 3)
    ids = np.asarray(board.getIds()).reshape(-1)
    centres = obj[:, :, :2].mean(axis=1)
    cells = np.floor(centres / s).astype(int)  # (col, row) of the square holding each marker
    has_marker = np.zeros((sy, sx), dtype=bool)
    has_marker[cells[:, 1], cells[:, 0]] = True
    for r in range(sy):  # ChArUco: every white square holds a marker, the others are black
        for c in range(sx):
            if not has_marker[r, c]:
                img[ey[r] : ey[r + 1], ex[c] : ex[c + 1]] = 0

    dictionary = board.getDictionary()
    for k in range(len(ids)):
        x0, y0 = obj[k, 0, :2]
        x1, y1 = obj[k, 2, :2]
        u0, v0 = int(round(x0 * px_per_mm)), int(round(y0 * px_per_mm))
        side = min(int(round(x1 * px_per_mm)) - u0, int(round(y1 * px_per_mm)) - v0)
        if side < int(dictionary.markerSize) + 2 * int(border_bits):  # < 1 px per marker cell
            raise ValueError(f"px_per_mm={px_per_mm} is too small to draw the markers")
        marker = cv2.aruco.generateImageMarker(dictionary, int(ids[k]), side, borderBits=border_bits)
        img[v0 : v0 + side, u0 : u0 + side] = marker
    return img


# --------------------------------------------------------------------------- printables
def _mm2px(mm: float, dpi: float) -> int:
    return int(round(mm * dpi / 25.4))


def _paper_mm(paper: str) -> tuple[float, float]:
    key = paper.lower().replace(" ", "")
    if key not in PAPER_SIZES_MM:
        raise ValueError(f"unknown paper {paper!r}; choose one of {sorted(PAPER_SIZES_MM)}")
    return PAPER_SIZES_MM[key]


def _output_paths(path: str | Path) -> tuple[Path, Path]:
    """``(pdf, png)`` paths for ``path`` given with a .pdf/.png suffix or without one."""
    p = Path(path)
    base = p.with_suffix("") if p.suffix.lower() in (".pdf", ".png") else p
    base.parent.mkdir(parents=True, exist_ok=True)
    return base.parent / (base.name + ".pdf"), base.parent / (base.name + ".png")


def _font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=max(int(size_px), 6))
    except (TypeError, OSError):  # Pillow without FreeType: fixed bitmap font
        return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, lines: list[str], max_w_px: int, size_px: int) -> ImageFont.ImageFont:
    """Largest font <= ``size_px`` for which every line fits into ``max_w_px``."""
    size = int(size_px)
    while True:
        font = _font(size)
        widest = max(draw.textbbox((0, 0), s, font=font)[2] for s in lines)
        if widest <= max_w_px or size <= 8:
            return font
        size = int(size * 0.9)


def _draw_scale_bar(draw: ImageDraw.ImageDraw, x0: int, y0: int, dpi: float, font: ImageFont.ImageFont,
                    length_mm: float = 100.0) -> None:
    """Alternating 10 mm black/white segments; the outer edges are exactly ``length_mm`` apart."""
    h = _mm2px(2.5, dpi)
    line = max(1, _mm2px(0.2, dpi))
    x1 = x0 + _mm2px(length_mm, dpi)
    draw.rectangle([x0, y0, x1 - 1, y0 + h - 1], fill=255, outline=0, width=line)
    n_seg = int(round(length_mm / 10.0))
    for k in range(0, n_seg, 2):
        a = x0 + _mm2px(10.0 * k, dpi)
        b = x0 + _mm2px(10.0 * (k + 1), dpi)
        draw.rectangle([a, y0, b - 1, y0 + h - 1], fill=0)
    draw.text((x1 + _mm2px(2.0, dpi), y0 + h // 2), f"{length_mm:.0f} mm", font=font, fill=0, anchor="lm")


def _dashed_line(draw: ImageDraw.ImageDraw, p0: tuple[int, int], p1: tuple[int, int], dpi: float) -> None:
    """Thin dashed (visually light) axis-aligned line for cutting."""
    on, off = _mm2px(1.0, dpi), _mm2px(1.5, dpi)
    (xa, ya), (xb, yb) = p0, p1
    length = max(abs(xb - xa), abs(yb - ya))
    horizontal = abs(xb - xa) >= abs(yb - ya)
    pos = 0
    while pos < length:
        end = min(pos + on, length)
        if horizontal:
            draw.line([(min(xa, xb) + pos, ya), (min(xa, xb) + end, ya)], fill=0, width=1)
        else:
            draw.line([(xa, min(ya, yb) + pos), (xa, min(ya, yb) + end)], fill=0, width=1)
        pos += on + off


def _to_bilevel(img: Image.Image) -> Image.Image:
    arr = np.asarray(img.convert("L"))
    return Image.fromarray(arr >= 128)  # bool array -> mode "1" (True = white)


def _save_pages(pages: list[Image.Image], pdf_path: Path, png_paths: list[Path], dpi: float, title: str) -> None:
    bilevel = [_to_bilevel(p) for p in pages]
    for im, png in zip(bilevel, png_paths):
        im.save(png, dpi=(dpi, dpi))
    bilevel[0].save(
        pdf_path, "PDF", resolution=float(dpi), save_all=True, append_images=bilevel[1:], title=title,
        author="OpenHandScan",
    )
    log.info("wrote %s (%d page(s)) and %s", pdf_path, len(pages), ", ".join(str(p) for p in png_paths))


@dataclass(frozen=True)
class CharucoPageLayout:
    """Where :func:`render_charuco_page` prints things, in millimetres.

    ``page_mm`` is the paper (width, height) as printed (landscape when ``landscape``);
    ``board_origin_mm`` the position of the board's outer top-left corner (board frame origin)
    measured from the page's top-left corner, at the exact raster position. All other lengths are
    in the board frame ``B`` (x along ``squares_x``, y along ``squares_y``): ``ink_boxes_mm`` are
    ``(xmin, xmax, ymin, ymax)`` boxes around the printed ink outside the board (scale bar, label
    text), measured on the bilevel page raster.
    """

    spec: CharucoSpec
    paper: str
    dpi: float
    landscape: bool
    page_mm: tuple[float, float]
    board_origin_mm: tuple[float, float]
    ink_boxes_mm: tuple[tuple[float, float, float, float], ...]

    @property
    def paper_bounds_mm(self) -> tuple[float, float, float, float]:
        """The paper rectangle ``(xmin, xmax, ymin, ymax)`` in the board frame."""
        x0, y0 = self.board_origin_mm
        return (-x0, self.page_mm[0] - x0, -y0, self.page_mm[1] - y0)

    def margin_mask(self, xy_b: np.ndarray, clearance_mm: float = 5.0, edge_clearance_mm: float | None = None) -> np.ndarray:
        """Mask of board-frame points (N, 2) on blank paper around the board.

        A point qualifies if it is on the paper at least ``edge_clearance_mm`` (default
        ``clearance_mm``) from the paper edge, and at least ``clearance_mm`` outside the board
        pattern and every ink box. The clearance keeps the laser-line profile window and its
        along-line smoothing clear of printed ink and of the paper edge.
        """
        xy = np.asarray(xy_b, dtype=np.float64).reshape(-1, 2)
        x, y = xy[:, 0], xy[:, 1]
        c = float(clearance_mm)
        e = c if edge_clearance_mm is None else float(edge_clearance_mm)
        xmin, xmax, ymin, ymax = self.paper_bounds_mm
        ok = (x > xmin + e) & (x < xmax - e) & (y > ymin + e) & (y < ymax - e)
        w, h = self.spec.size_mm
        ok &= ~((x > -c) & (x < w + c) & (y > -c) & (y < h + c))
        for a, b, lo, hi in self.ink_boxes_mm:
            ok &= ~((x > a - c) & (x < b + c) & (y > lo - c) & (y < hi + c))
        return ok

    def to_dict(self) -> dict:
        return {
            "paper": self.paper,
            "dpi": float(self.dpi),
            "landscape": bool(self.landscape),
            "page_mm": [float(v) for v in self.page_mm],
            "board_origin_mm": [float(v) for v in self.board_origin_mm],
            "paper_bounds_mm": [float(v) for v in self.paper_bounds_mm],
            "ink_boxes_mm": [[float(v) for v in b] for b in self.ink_boxes_mm],
        }


def _charuco_page_canvas(spec: CharucoSpec, paper: str, dpi: float) -> tuple[Image.Image, dict]:
    """Draw the printable page (greyscale PIL image before bilevel conversion) and its geometry."""
    pw, ph = _paper_mm(paper)
    bw, bh = spec.size_mm
    footer_mm = 19.0  # gap, scale bar, two text lines
    content_w, content_h = max(bw, 125.0), bh + footer_mm

    def slack(w: float, h: float) -> float:
        return min(w - 2 * PAGE_MARGIN_MM - content_w, h - 2 * PAGE_MARGIN_MM - content_h)

    s_portrait, s_landscape = slack(pw, ph), slack(ph, pw)
    if max(s_portrait, s_landscape) < 0:
        raise ValueError(
            f"{spec.describe()} ({bw:.0f} x {bh:.0f} mm) does not fit on {paper} paper with "
            f"{PAGE_MARGIN_MM:.0f} mm margins; use fewer or smaller squares"
        )
    landscape = s_landscape > s_portrait
    page_w_mm, page_h_mm = (ph, pw) if landscape else (pw, ph)
    W, H = _mm2px(page_w_mm, dpi), _mm2px(page_h_mm, dpi)

    canvas = np.full((H, W), 255, np.uint8)
    pattern = render_board_image(spec, dpi / 25.4)
    x0 = _mm2px((page_w_mm - bw) / 2.0, dpi)
    y0 = _mm2px((page_h_mm - content_h) / 2.0, dpi)
    canvas[y0 : y0 + pattern.shape[0], x0 : x0 + pattern.shape[1]] = pattern

    page = Image.fromarray(canvas, "L")
    draw = ImageDraw.Draw(page)
    lines = [
        f"OpenHandScan calibration board: {spec.describe()}",
        "Print at 100% / actual size (no 'fit to page'), glue to glass or a flat panel. "
        f"Check: the bar is 100 mm, each square {spec.square_mm:.2f} mm.",
    ]
    # footer aligned with the board's left edge unless the text would then overrun the right margin
    margin_px = _mm2px(PAGE_MARGIN_MM, dpi)
    nominal = _font(_mm2px(_FONT_EM_MM, dpi))
    text_w = max(draw.textbbox((0, 0), s, font=nominal)[2] for s in lines)
    fx = x0 if x0 + text_w <= W - margin_px else max(margin_px, (W - text_w) // 2)
    font = _fit_font(draw, lines, W - margin_px - fx, _mm2px(_FONT_EM_MM, dpi))
    y_bar = y0 + pattern.shape[0] + _mm2px(5.0, dpi)
    _draw_scale_bar(draw, fx, y_bar, dpi, font)
    y_text = y_bar + _mm2px(2.5 + 2.5, dpi)
    for k, s in enumerate(lines):
        draw.text((fx, y_text + k * _mm2px(_FONT_EM_MM * 1.3, dpi)), s, font=font, fill=0)
    info = {"landscape": landscape, "page_mm": (page_w_mm, page_h_mm), "board_px": (x0, y0),
            "pattern_shape": pattern.shape}
    return page, info


def _ink_boxes(bilevel: np.ndarray, info: dict, dpi: float) -> tuple[tuple[float, float, float, float], ...]:
    """Bounding boxes (board-frame mm) of the printed ink outside the board area."""
    dark = bilevel < 128
    x0, y0 = info["board_px"]
    h, w = info["pattern_shape"]
    dark[y0 : y0 + h, x0 : x0 + w] = False
    if not dark.any():
        return ()
    k = max(3, _mm2px(1.5, dpi))  # merge glyphs into words / lines
    grown = cv2.dilate(dark.astype(np.uint8), np.ones((k, k), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grown, connectivity=8)
    ppm = float(dpi) / 25.4
    boxes = []
    for lab in range(1, n):
        bx, by, bw, bh = (int(v) for v in stats[lab, :4])
        sub = dark[by : by + bh, bx : bx + bw] & (labels[by : by + bh, bx : bx + bw] == lab)
        ys, xs = np.nonzero(sub)
        if len(xs) == 0:
            continue
        boxes.append((
            (bx + xs.min()) / ppm - x0 / ppm, (bx + xs.max() + 1) / ppm - x0 / ppm,
            (by + ys.min()) / ppm - y0 / ppm, (by + ys.max() + 1) / ppm - y0 / ppm,
        ))
    return tuple(sorted(boxes, key=lambda b: (b[2], b[0])))


def render_charuco_page_image(spec: CharucoSpec, paper: str = "letter", dpi: float = 300) -> tuple[np.ndarray, CharucoPageLayout]:
    """The page :func:`render_charuco_page` prints, as a bilevel uint8 array (0 / 255), and its layout.

    With ``dpi = 254`` one pixel is exactly 0.1 mm (the simulator's board texture resolution).
    """
    page, info = _charuco_page_canvas(spec, paper, dpi)
    bilevel = np.where(np.asarray(page.convert("L")) >= 128, 255, 0).astype(np.uint8)
    ppm = float(dpi) / 25.4
    x0, y0 = info["board_px"]
    layout = CharucoPageLayout(
        spec=spec,
        paper=paper.lower().replace(" ", ""),
        dpi=float(dpi),
        landscape=bool(info["landscape"]),
        page_mm=(float(info["page_mm"][0]), float(info["page_mm"][1])),
        board_origin_mm=(x0 / ppm, y0 / ppm),
        ink_boxes_mm=_ink_boxes(bilevel, info, dpi),
    )
    return bilevel, layout


@functools.lru_cache(maxsize=16)
def charuco_page_layout(spec: CharucoSpec, paper: str = "letter", dpi: float = 300) -> CharucoPageLayout:
    """Layout of the page :func:`render_charuco_page` prints for ``spec`` (cached).

    Raises ``ValueError`` if the board does not fit on ``paper``.
    """
    return render_charuco_page_image(spec, paper, dpi)[1]


def render_charuco_page(spec: CharucoSpec, path: str | Path, paper: str = "letter", dpi: int = 300) -> tuple[Path, Path]:
    """Write an exact-scale printable ChArUco page as PDF and PNG.

    The page holds the board, a 100 mm scale bar and a label with the board spec and the printing
    instructions. Landscape is used when the content fits better that way. ``path`` may end in
    ``.pdf``, ``.png`` or have no suffix; both files are written. Returns ``(pdf_path, png_path)``.
    :func:`charuco_page_layout` describes where everything lands on the paper.
    """
    page, info = _charuco_page_canvas(spec, paper, dpi)
    pdf_path, png_path = _output_paths(path)
    _save_pages([page], pdf_path, [png_path], dpi, title=spec.describe())
    log.info("ChArUco page: %s, %s %s, %d dpi", spec.describe(), paper,
             "landscape" if info["landscape"] else "portrait", dpi)
    return pdf_path, png_path


def render_apriltag_sheet(
    path: str | Path,
    tag_mm: float = 20.0,
    ids: Iterable[int] = range(48),
    paper: str = "letter",
    dpi: int = 300,
) -> tuple[Path, list[Path]]:
    """Write printable tag36h11 sticker sheets (PDF with one page per sheet, plus PNGs).

    ``tag_mm`` is the side of the outer black square, i.e. the distance between the corners the
    detector reports. Each sticker has a white quiet zone of at least one module, its id printed
    small below the tag, and dashed cut lines around it. Returns ``(pdf_path, [png_paths])``.
    """
    id_list = [int(i) for i in ids]
    dictionary = aruco_dictionary(APRILTAG_DICTIONARY)
    n_dict = int(dictionary.bytesList.shape[0])
    if not id_list:
        raise ValueError("no tag ids given")
    if min(id_list) < 0 or max(id_list) >= n_dict:
        raise ValueError(f"tag36h11 ids must be in 0..{n_dict - 1}")
    if len(set(id_list)) != len(id_list):
        raise ValueError("duplicate tag ids would make the marker map ambiguous")
    if tag_mm <= 0:
        raise ValueError("tag_mm must be positive")

    modules = int(dictionary.markerSize) + 2  # data bits + black border
    module_mm = tag_mm / modules
    quiet = max(module_mm, 2.0)
    label_mm = 3.0
    cell_w, cell_h = tag_mm + 2 * quiet, tag_mm + 2 * quiet + label_mm
    header_mm, footer_mm = 8.0, 8.0

    pw, ph = _paper_mm(paper)

    def grid(w: float, h: float) -> tuple[int, int]:
        cols = int((w - 2 * PAGE_MARGIN_MM) // cell_w)
        rows = int((h - 2 * PAGE_MARGIN_MM - header_mm - footer_mm) // cell_h)
        return max(cols, 0), max(rows, 0)

    def pages_needed(g: tuple[int, int]) -> float:
        return math.ceil(len(id_list) / (g[0] * g[1])) if g[0] * g[1] > 0 else math.inf

    gp, gl = grid(pw, ph), grid(ph, pw)
    landscape = pages_needed(gl) < pages_needed(gp)  # fewer sheets wins; portrait on a tie
    page_w_mm, page_h_mm = (ph, pw) if landscape else (pw, ph)
    cols, rows = gl if landscape else gp
    if cols * rows == 0:
        raise ValueError(f"a {tag_mm} mm tag does not fit on {paper} paper")
    per_page = cols * rows
    n_pages = math.ceil(len(id_list) / per_page)
    W, H = _mm2px(page_w_mm, dpi), _mm2px(page_h_mm, dpi)
    tag_px = _mm2px(tag_mm, dpi)
    if tag_px < modules:
        raise ValueError("dpi too low for this tag size")

    gx0_mm = (page_w_mm - cols * cell_w) / 2.0
    gy0_mm = PAGE_MARGIN_MM + header_mm
    label_font = _font(_mm2px(2.2, dpi))

    pages: list[Image.Image] = []
    for p in range(n_pages):
        chunk = id_list[p * per_page : (p + 1) * per_page]
        contiguous = chunk == list(range(chunk[0], chunk[0] + len(chunk)))
        id_text = f"ids {chunk[0]}..{chunk[-1]}" if contiguous else f"{len(chunk)} ids"
        header = (
            f"AprilTag tag36h11 stickers, tag {tag_mm:.1f} mm (outer black square), {id_text}, sheet {p + 1}/{n_pages}. "
            "Print at 100% / actual size on matte label paper, cut along the dashed lines."
        )
        page = Image.new("L", (W, H), 255)
        draw = ImageDraw.Draw(page)
        font = _fit_font(draw, [header], W - 2 * _mm2px(PAGE_MARGIN_MM, dpi), _mm2px(_FONT_EM_MM, dpi))
        draw.text((_mm2px(PAGE_MARGIN_MM, dpi), _mm2px(PAGE_MARGIN_MM, dpi)), header, font=font, fill=0)
        used_rows = math.ceil(len(chunk) / cols)
        for k, tag_id in enumerate(chunk):
            r, c = divmod(k, cols)
            cx_mm, cy_mm = gx0_mm + c * cell_w, gy0_mm + r * cell_h
            tx, ty = _mm2px(cx_mm + quiet, dpi), _mm2px(cy_mm + quiet, dpi)
            tag = cv2.aruco.generateImageMarker(dictionary, tag_id, tag_px, borderBits=1)
            page.paste(Image.fromarray(tag, "L"), (tx, ty))
            # the id goes in the label strip below the quiet zone, which must stay blank
            y_label = _mm2px(cy_mm + 2 * quiet + tag_mm + 0.5 * label_mm, dpi)
            draw.text((tx, y_label), f"id {tag_id}", font=label_font, fill=0, anchor="lm")
        # dashed cut lines on the boundaries of the occupied cells (rows fill left to right)
        row_counts = [min(cols, len(chunk) - r * cols) for r in range(used_rows)]
        gx0, gy0 = _mm2px(gx0_mm, dpi), _mm2px(gy0_mm, dpi)
        for r in range(used_rows + 1):
            n_c = max(row_counts[r - 1] if r >= 1 else 0, row_counts[r] if r < used_rows else 0)
            y = _mm2px(gy0_mm + r * cell_h, dpi)
            _dashed_line(draw, (gx0, y), (_mm2px(gx0_mm + n_c * cell_w, dpi), y), dpi)
        for c in range(cols + 1):
            depth = sum(1 for n in row_counts if n >= max(c, 1))
            x = _mm2px(gx0_mm + c * cell_w, dpi)
            _dashed_line(draw, (x, gy0), (x, _mm2px(gy0_mm + depth * cell_h, dpi)), dpi)
        _draw_scale_bar(draw, _mm2px(PAGE_MARGIN_MM, dpi), H - _mm2px(PAGE_MARGIN_MM + 4.0, dpi), dpi, font)
        pages.append(page)

    pdf_path, png_path = _output_paths(path)
    if n_pages == 1:
        png_paths = [png_path]
    else:
        png_paths = [png_path.with_name(f"{png_path.stem}_p{k + 1}.png") for k in range(n_pages)]
    _save_pages(pages, pdf_path, png_paths, dpi, title=f"AprilTag tag36h11 {tag_mm:.1f} mm")
    log.info("AprilTag sheet: %d tags of %.1f mm, %d x %d per page, %d page(s)", len(id_list), tag_mm, cols, rows, n_pages)
    return pdf_path, png_paths


# --------------------------------------------------------------------------- detection + pose
def _as_gray_u8(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img)
    if a.ndim == 3:
        a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY if a.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
    if a.dtype != np.uint8:
        a = np.clip(a, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(a)


def _charuco_detector(spec: CharucoSpec) -> cv2.aruco.CharucoDetector:
    charuco_params = cv2.aruco.CharucoParameters()
    charuco_params.tryRefineMarkers = True  # recover markers missed at oblique views
    det_params = cv2.aruco.DetectorParameters()
    # These two also drive the sub-pixel refinement of the ChArUco corners (default eps 0.1 px).
    det_params.cornerRefinementMinAccuracy = 0.01
    det_params.cornerRefinementMaxIterations = 50
    return cv2.aruco.CharucoDetector(make_charuco_board(spec), charuco_params, det_params)


def detect_charuco(img: np.ndarray, spec: CharucoSpec) -> tuple[np.ndarray, np.ndarray]:
    """Detect ChArUco corners with sub-pixel refinement.

    Returns ``(corners (N, 2) float32, ids (N,) int32)`` sorted by id (OpenCV pixel-centre
    convention); empty arrays when the board is not found.
    """
    gray = _as_gray_u8(img)
    corners, ids, _, _ = _charuco_detector(spec).detectBoard(gray)
    if corners is None or ids is None or len(ids) == 0:
        return np.zeros((0, 2), np.float32), np.zeros((0,), np.int32)
    corners = np.asarray(corners, dtype=np.float32).reshape(-1, 2)
    ids = np.asarray(ids, dtype=np.int32).reshape(-1)
    order = np.argsort(ids, kind="stable")
    return corners[order], ids[order]


def _dist(D: np.ndarray | None) -> np.ndarray:
    return np.zeros(5) if D is None else np.asarray(D, dtype=np.float64).reshape(-1)


def _residuals(T: np.ndarray, obj: np.ndarray, uv: np.ndarray, K: np.ndarray, D: np.ndarray) -> np.ndarray:
    rvec, _ = cv2.Rodrigues(np.asarray(T[:3, :3], dtype=np.float64))
    proj, _ = cv2.projectPoints(obj, rvec, np.asarray(T[:3, 3], np.float64), K, D)
    return np.asarray(uv, dtype=np.float64).reshape(-1, 2) - proj.reshape(-1, 2)


def _refine_lm(obj: np.ndarray, uv: np.ndarray, K: np.ndarray, D: np.ndarray, T: np.ndarray) -> np.ndarray:
    rvec, _ = cv2.Rodrigues(np.asarray(T[:3, :3], dtype=np.float64))
    rvec, tvec = cv2.solvePnPRefineLM(obj, uv, K, D, rvec, np.asarray(T[:3, 3], np.float64).reshape(3, 1).copy())
    return make_se3(cv2.Rodrigues(rvec)[0], tvec.reshape(3))


def reprojection_errors(
    T_cb: np.ndarray, corners: np.ndarray, ids: np.ndarray, spec: CharucoSpec, K: np.ndarray, D: np.ndarray | None = None
) -> np.ndarray:
    """Per-corner reprojection error vectors (N, 2) in pixels (detected minus projected) for a board pose."""
    obj = board_corner_points(spec)[np.asarray(ids, dtype=int).reshape(-1)]
    return _residuals(np.asarray(T_cb, np.float64), obj, corners, np.asarray(K, np.float64), _dist(D))


def estimate_board_pose(
    corners: np.ndarray, ids: np.ndarray, spec: CharucoSpec, K: np.ndarray, D: np.ndarray | None = None
) -> tuple[bool, np.ndarray | None]:
    """Board pose ``T_cb`` (board -> camera, mm, ``p_c = T_cb @ p_b``) from detected corners.

    Needs at least :data:`MIN_POSE_CORNERS` non-collinear corners. Uses IPPE (planar PnP, both
    solutions tried) and Levenberg-Marquardt refinement, then drops badly refined corners
    (reprojection error > max(1 px, 4 x median); in distant views the sub-pixel refinement can
    lock onto a nearby marker corner) and refits. ``D=None`` means no distortion (rectified
    images). Returns ``(False, None)`` on failure.
    """
    ids = np.asarray(ids, dtype=np.int64).reshape(-1)
    uv = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    if len(ids) < MIN_POSE_CORNERS or len(ids) != len(uv):
        return False, None
    if ids.min() < 0 or ids.max() >= spec.n_corners or len(np.unique(ids)) != len(ids):
        log.debug("invalid ChArUco ids for %s", spec.describe())
        return False, None
    board = make_charuco_board(spec)
    if board.checkCharucoCornersCollinear(ids.astype(np.int32).reshape(-1, 1)):
        log.debug("ChArUco corners are collinear; no pose")
        return False, None
    obj = board_corner_points(spec)[ids]
    K = np.asarray(K, dtype=np.float64)
    Dv = _dist(D)
    try:
        n, rvecs, tvecs, _ = cv2.solvePnPGeneric(obj, uv, K, Dv, flags=cv2.SOLVEPNP_IPPE)
    except cv2.error as exc:
        log.debug("solvePnPGeneric failed: %s", exc)
        return False, None
    T, best = None, np.inf
    for rvec, tvec in zip(rvecs[:n], tvecs[:n]):  # both IPPE solutions, keep the better refined one
        cand = _refine_lm(obj, uv, K, Dv, make_se3(cv2.Rodrigues(rvec)[0], tvec.reshape(3)))
        if np.any(transform_points(cand, obj)[:, 2] <= 0):
            continue
        rms = float(np.sqrt(np.mean(np.sum(_residuals(cand, obj, uv, K, Dv) ** 2, axis=1))))
        if rms < best:
            T, best = cand, rms
    if T is None:
        return False, None
    keep = np.ones(len(ids), dtype=bool)
    for _ in range(2):
        res = np.linalg.norm(_residuals(T, obj[keep], uv[keep], K, Dv), axis=1)
        bad = res > max(1.0, 4.0 * float(np.median(res)))
        if not bad.any():
            break
        cand = keep.copy()
        cand[np.flatnonzero(keep)[bad]] = False
        if cand.sum() < MIN_POSE_CORNERS or board.checkCharucoCornersCollinear(ids[cand].astype(np.int32).reshape(-1, 1)):
            break
        keep = cand
        T = _refine_lm(obj[keep], uv[keep], K, Dv, T)
    rms = float(np.sqrt(np.mean(np.sum(_residuals(T, obj[keep], uv[keep], K, Dv) ** 2, axis=1))))
    if rms > MAX_POSE_RMS_PX:
        log.debug("board pose rejected (rms %.2f px)", rms)
        return False, None
    if not keep.all():
        log.debug("board pose: dropped %d outlier corner(s), rms %.3f px", int((~keep).sum()), rms)
    return True, T


class BoardViewRenderer:
    """Synthetic grey images of a printed ChArUco board seen by one camera (tests, self-test, demos).

    The board texture (pattern plus a white paper border) is warped with the plane homography of
    the ideal pinhole camera at ``supersample`` x resolution and area-averaged down (anti-aliased
    edges). Lens distortion ``D`` is then applied by remapping: for every distorted output pixel,
    its undistorted source position comes from ``cv2.undistortPoints`` (precomputed once per
    camera). Finally optical blur and Gaussian sensor noise are added.
    """

    def __init__(
        self,
        spec: CharucoSpec,
        K: np.ndarray,
        image_size: tuple[int, int],
        D: np.ndarray | None = None,
        supersample: int = 4,
        px_per_mm: float = 12.0,
        paper_border_mm: float = 8.0,
        levels: tuple[float, float, float] = (25.0, 225.0, 70.0),
    ) -> None:
        self.spec = spec
        self.K = np.asarray(K, dtype=np.float64).reshape(3, 3)
        self.image_size = (int(image_size[0]), int(image_size[1]))
        self.D = None if D is None or not np.any(D) else np.asarray(D, dtype=np.float64).reshape(-1)
        self.ss = int(supersample)
        self.black, self.white, background = (float(v) for v in levels)
        border = int(round(paper_border_mm * px_per_mm))
        self._tex = cv2.copyMakeBorder(
            render_board_image(spec, px_per_mm), border, border, border, border, cv2.BORDER_CONSTANT, value=255
        )
        b_mm = border / px_per_mm
        # texture pixel (centre convention) -> board mm
        s, o = 1.0 / px_per_mm, 0.5 / px_per_mm - b_mm
        self._A = np.array([[s, 0, o], [0, s, o], [0, 0, 1.0]])
        self._bg = float(np.clip((background - self.black) / (self.white - self.black) * 255.0, 0, 255))
        w, h = self.image_size
        if self.D is None:
            self._origin, self._canvas, self._map = (0, 0), (w, h), None
        else:
            uu, vv = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
            pts = np.stack([uu.ravel(), vv.ravel()], axis=1).reshape(-1, 1, 2)
            crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 50, 1e-10)
            ideal = cv2.undistortPoints(pts, self.K, self.D, R=None, P=self.K, criteria=crit).reshape(h, w, 2)
            x0, y0 = int(np.floor(ideal[..., 0].min())) - 2, int(np.floor(ideal[..., 1].min())) - 2
            x1, y1 = int(np.ceil(ideal[..., 0].max())) + 2, int(np.ceil(ideal[..., 1].max())) + 2
            self._origin, self._canvas = (x0, y0), (x1 - x0 + 1, y1 - y0 + 1)
            self._map = ((ideal[..., 0] - x0).astype(np.float32), (ideal[..., 1] - y0).astype(np.float32))

    def render(
        self, T_cb: np.ndarray, blur_px: float = 0.7, noise_std: float = 1.0, rng: np.random.Generator | None = None
    ) -> np.ndarray:
        """Grey uint8 image (H, W) of the board at pose ``T_cb`` (board -> camera, mm)."""
        T_cb = np.asarray(T_cb, dtype=np.float64)
        ss, (x0, y0), (cw, ch) = self.ss, self._origin, self._canvas
        Kc = self.K.copy()
        Kc[0, 2] -= x0
        Kc[1, 2] -= y0
        S = np.array([[ss, 0, 0.5 * ss - 0.5], [0, ss, 0.5 * ss - 0.5], [0, 0, 1.0]])
        H = S @ Kc @ np.column_stack([T_cb[:3, 0], T_cb[:3, 1], T_cb[:3, 3]]) @ self._A
        big = cv2.warpPerspective(
            self._tex, H, (cw * ss, ch * ss), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=self._bg
        )
        img = cv2.resize(big, (cw, ch), interpolation=cv2.INTER_AREA).astype(np.float32)
        if self._map is not None:
            img = cv2.remap(img, self._map[0], self._map[1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        img = self.black + (self.white - self.black) / 255.0 * img
        if blur_px > 0:
            img = cv2.GaussianBlur(img, (0, 0), float(blur_px))
        if noise_std > 0:
            rng = np.random.default_rng(0) if rng is None else rng
            img = img + rng.normal(0.0, noise_std, img.shape).astype(np.float32)
        return np.clip(np.round(img), 0, 255).astype(np.uint8)


def board_outline_uv(T_cb: np.ndarray, spec: CharucoSpec, K: np.ndarray, margin_mm: float = 0.0) -> np.ndarray:
    """Image polygon (4, 2) of the board's outer rectangle for an ideal pinhole ``K``.

    Vertex order: board (0,0), (W,0), (W,H), (0,H). ``margin_mm > 0`` shrinks the rectangle
    inwards by that amount on every side (negative grows it). Vertices behind the camera are NaN.
    """
    W, H = spec.size_mm
    m = float(margin_mm)
    if 2 * m >= min(W, H):
        raise ValueError("margin_mm is larger than half the board")
    P = np.array([[m, m, 0.0], [W - m, m, 0.0], [W - m, H - m, 0.0], [m, H - m, 0.0]])
    return project_points(transform_points(np.asarray(T_cb, dtype=np.float64), P), np.asarray(K, np.float64))
