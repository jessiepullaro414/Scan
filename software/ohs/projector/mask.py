"""Random-dot mask for the LED pattern projector (projector option "led").

The LED projector images a small printed mask through an M12 lens onto the scene, the same way a
slide projector works: LED -> diffuser -> mask (at the lens holder's sensor plane) -> M12 lens.
Any random, non-repeating texture lets the stereo matcher work on plain surfaces; this module makes
a blue-noise (Poisson-disk) dot pattern, which has no clumps or repeats.

Outputs, all at exact physical scale:
* PNG + PDF sheets with many copies, for a laser printer on transparency film (default 1200 dpi);
* Gerber RS-274X, for any photoplot/film service (sharper, smaller dots possible);
* SVG, for anything else.

Defaults match the 3.6 mm M12 lens (Arducam M25360H06S, 1/2.5" image field):
5.6 x 4.2 mm mask -> ~76 x 60 deg projected field (close to the laser DOE's 80 x 64 deg).
* printed mask: 42 um dots (2 printer pixels at 1200 dpi) -> ~2.9 mm dots at 250 mm;
* film mask (Gerber/SVG): 25 um dots -> ~1.7 mm dots at 250 mm (better full-resolution depth).
In simulation (realistic optics, laser lines on) the film mask gave 0.23-0.27 % median depth error
and 73-97 % coverage, vs 0.11-0.18 % / 92-98 % for the laser DOE projector.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = [
    "MaskSpec",
    "dot_centers",
    "render_mask",
    "projected_dot_mm",
    "projector_fov_deg",
    "write_mask_sheet",
    "write_gerber",
    "write_svg",
    "write_mask_files",
]

log = logging.getLogger(__name__)

PAPER_MM = {"letter": (215.9, 279.4), "a4": (210.0, 297.0)}


@dataclass
class MaskSpec:
    """Geometry of one projector mask.

    ``bright_dots=True`` gives clear dots on an opaque background (looks like the laser DOE
    pattern). ``False`` prints dark dots on a clear background (more light, lower contrast).
    """

    width_mm: float = 5.6
    height_mm: float = 4.2
    dot_um: float = 42.0
    gap_um: float | None = None  # min clear gap between dots; default = dot size
    seed: int = 1
    bright_dots: bool = True

    @property
    def min_center_dist_mm(self) -> float:
        gap = self.dot_um if self.gap_um is None else self.gap_um
        return (self.dot_um + gap) / 1000.0


# --------------------------------------------------------------------------- optics helpers
DEFAULT_FOCAL_MM = 3.6
FILM_DOT_UM = 25.0


def projected_dot_mm(spec: MaskSpec, focal_mm: float = DEFAULT_FOCAL_MM, distance_mm: float = 250.0) -> float:
    """Size of one projected dot (mm) on a surface ``distance_mm`` from the lens."""
    return spec.dot_um / 1000.0 * (distance_mm - focal_mm) / focal_mm


def projector_fov_deg(spec: MaskSpec, focal_mm: float = DEFAULT_FOCAL_MM) -> tuple[float, float]:
    """(horizontal, vertical) full field angle of the projected pattern in degrees."""
    h = 2 * math.degrees(math.atan(spec.width_mm / 2 / focal_mm))
    v = 2 * math.degrees(math.atan(spec.height_mm / 2 / focal_mm))
    return h, v


# --------------------------------------------------------------------------- pattern
def dot_centers(spec: MaskSpec) -> np.ndarray:
    """Blue-noise dot centres (N,2) in mm, origin at the mask centre (Bridson Poisson-disk).

    Every dot lies fully inside the mask and no two centres are closer than
    ``spec.min_center_dist_mm``. Deterministic for a given ``seed``.
    """
    r = spec.min_center_dist_mm
    half = spec.dot_um / 2000.0
    w, h = spec.width_mm - 2 * half, spec.height_mm - 2 * half
    if w <= 0 or h <= 0:
        raise ValueError("mask is smaller than one dot")
    rng = np.random.default_rng(spec.seed)
    cell = r / math.sqrt(2.0)
    gw, gh = int(math.ceil(w / cell)), int(math.ceil(h / cell))
    grid = -np.ones((gh, gw), dtype=np.int64)
    pts: list[tuple[float, float]] = []

    def fits(x: float, y: float) -> bool:
        if not (0 <= x < w and 0 <= y < h):
            return False
        gx, gy = int(x / cell), int(y / cell)
        for j in range(max(gy - 2, 0), min(gy + 3, gh)):
            for i in range(max(gx - 2, 0), min(gx + 3, gw)):
                k = grid[j, i]
                if k >= 0:
                    px, py = pts[k]
                    if (px - x) ** 2 + (py - y) ** 2 < r * r:
                        return False
        return True

    def add(x: float, y: float) -> None:
        grid[int(y / cell), int(x / cell)] = len(pts)
        pts.append((x, y))

    add(rng.uniform(0, w), rng.uniform(0, h))
    active = [0]
    k_tries = 30
    while active:
        a = active[rng.integers(len(active))]
        ax, ay = pts[a]
        rad = rng.uniform(r, 2 * r, k_tries)
        ang = rng.uniform(0, 2 * math.pi, k_tries)
        placed = False
        for rr, aa in zip(rad, ang):
            x, y = ax + rr * math.cos(aa), ay + rr * math.sin(aa)
            if fits(x, y):
                add(x, y)
                active.append(len(pts) - 1)
                placed = True
                break
        if not placed:
            active.remove(a)
    c = np.asarray(pts, dtype=np.float64)
    c[:, 0] += half - spec.width_mm / 2
    c[:, 1] += half - spec.height_mm / 2
    return c


def render_mask(spec: MaskSpec, dpi: int = 1200, centers: np.ndarray | None = None) -> np.ndarray:
    """Rasterise one mask at ``dpi``. Returns uint8 (H, W): 255 = clear (light passes), 0 = opaque.

    Dots are squares aligned to the device pixel grid, which is what a laser printer reproduces best.
    """
    if centers is None:
        centers = dot_centers(spec)
    px_mm = 25.4 / dpi
    W, H = int(round(spec.width_mm / px_mm)), int(round(spec.height_mm / px_mm))
    dot_px = max(1, int(round(spec.dot_um / 1000.0 / px_mm)))
    img = np.zeros((H, W), dtype=np.uint8) if spec.bright_dots else np.full((H, W), 255, np.uint8)
    val = 255 if spec.bright_dots else 0
    u = np.floor((centers[:, 0] + spec.width_mm / 2) / px_mm - dot_px / 2 + 0.5).astype(int)
    v = np.floor((spec.height_mm / 2 - centers[:, 1]) / px_mm - dot_px / 2 + 0.5).astype(int)
    for du in range(dot_px):
        for dv in range(dot_px):
            uu, vv = np.clip(u + du, 0, W - 1), np.clip(v + dv, 0, H - 1)
            img[vv, uu] = val
    return img


# --------------------------------------------------------------------------- outputs
def write_mask_sheet(
    path: str | Path,
    spec: MaskSpec | None = None,
    dpi: int = 1200,
    paper: str = "letter",
    grid: tuple[int, int] = (6, 8),
    pitch_mm: float = 22.0,
) -> tuple[Path, Path]:
    """Write a print sheet (PNG + PDF, exact scale) with ``grid`` copies of the mask.

    Each copy gets crop marks and a small label; the sheet has a 50 mm scale bar to check that the
    printer did not rescale it. All copies show the same pattern (it is generated once); printing
    many lets you cut out the cleanest one. Returns ``(png_path, pdf_path)``.
    """
    from PIL import Image, ImageDraw, ImageFont

    spec = spec or MaskSpec()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pw_mm, ph_mm = PAPER_MM[paper]
    mm = dpi / 25.4
    page = Image.new("1", (int(round(pw_mm * mm)), int(round(ph_mm * mm))), 1)
    draw = ImageDraw.Draw(page)
    try:
        font = ImageFont.truetype("arial.ttf", int(2.2 * mm))
    except OSError:
        font = ImageFont.load_default()
    cols, rows = grid
    x0 = (pw_mm - (cols - 1) * pitch_mm) / 2
    y0 = 30.0
    s = spec
    tile_img = Image.fromarray(render_mask(spec, dpi)).convert("1")
    for j in range(rows):
        for i in range(cols):
            k = j * cols + i
            cx, cy = x0 + i * pitch_mm, y0 + j * pitch_mm
            left = int(round((cx - s.width_mm / 2) * mm))
            top = int(round((cy - s.height_mm / 2) * mm))
            page.paste(tile_img, (left, top))
            # crop marks 1.5 mm outside the active area
            for sx in (-1, 1):
                for sy in (-1, 1):
                    ex, ey = cx + sx * (s.width_mm / 2 + 1.5), cy + sy * (s.height_mm / 2 + 1.5)
                    draw.line([(ex * mm, ey * mm), ((ex + sx * 2) * mm, ey * mm)], fill=0, width=1)
                    draw.line([(ex * mm, ey * mm), (ex * mm, (ey + sy * 2) * mm)], fill=0, width=1)
            draw.text(((cx - s.width_mm / 2) * mm, (cy + s.height_mm / 2 + 2.5) * mm), f"{k + 1}", fill=0, font=font)
    # scale bar + caption
    by = ph_mm - 25.0
    draw.rectangle([20 * mm, by * mm, 70 * mm, (by + 1.5) * mm], fill=0)
    for t in range(0, 51, 10):
        draw.line([((20 + t) * mm, (by - 2) * mm), ((20 + t) * mm, by * mm)], fill=0, width=2)
    draw.text((20 * mm, (by + 3) * mm), "50 mm - print at 100% / actual size on transparency film", fill=0, font=font)
    fov = projector_fov_deg(spec)
    draw.text(
        (20 * mm, (by + 7) * mm),
        f"OpenHandScan LED projector mask {spec.width_mm}x{spec.height_mm} mm, dots {spec.dot_um:.0f} um, "
        f"{'clear dots' if spec.bright_dots else 'dark dots'}; {DEFAULT_FOCAL_MM} mm lens -> {fov[0]:.0f}x{fov[1]:.0f} deg",
        fill=0,
        font=font,
    )
    png, pdf = path.with_suffix(".png"), path.with_suffix(".pdf")
    page.save(png, dpi=(dpi, dpi))
    page.save(pdf, resolution=dpi)
    return png, pdf


def write_gerber(path: str | Path, spec: MaskSpec | None = None) -> Path:
    """Gerber RS-274X of one mask for a photoplot/film service (units mm, dark = opaque).

    With ``bright_dots`` the file draws an opaque rectangle and clears the dots (LPC); ask the
    service for a positive plot. Dots are round flashes of diameter ``dot_um``.
    """
    spec = spec or MaskSpec()
    c = dot_centers(spec)
    f = lambda v: f"{int(round(v * 1e6)):d}"  # noqa: E731  (FSLAX46Y46 -> 1e-6 mm units)
    hw, hh = spec.width_mm / 2, spec.height_mm / 2
    lines = [
        "G04 OpenHandScan LED projector mask*",
        f"G04 {spec.width_mm}x{spec.height_mm} mm, dot {spec.dot_um} um, seed {spec.seed}*",
        "%FSLAX46Y46*%",
        "%MOMM*%",
        f"%ADD10C,{spec.dot_um / 1000:.6f}*%",
    ]
    if spec.bright_dots:
        lines += [
            "%LPD*%",
            "G36*",
            f"X{f(-hw)}Y{f(-hh)}D02*",
            "G01*",
            f"X{f(hw)}Y{f(-hh)}D01*",
            f"X{f(hw)}Y{f(hh)}D01*",
            f"X{f(-hw)}Y{f(hh)}D01*",
            f"X{f(-hw)}Y{f(-hh)}D01*",
            "G37*",
            "%LPC*%",
        ]
    else:
        lines.append("%LPD*%")
    lines.append("D10*")
    lines += [f"X{f(x)}Y{f(y)}D03*" for x, y in c]
    lines.append("M02*")
    path = Path(path)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def write_svg(path: str | Path, spec: MaskSpec | None = None) -> Path:
    """SVG of one mask in millimetres (black = opaque)."""
    spec = spec or MaskSpec()
    c = dot_centers(spec)
    bg, fg = ("black", "white") if spec.bright_dots else ("white", "black")
    r = spec.dot_um / 2000.0
    body = "".join(
        f'<circle cx="{x + spec.width_mm / 2:.4f}" cy="{spec.height_mm / 2 - y:.4f}" r="{r:.4f}"/>' for x, y in c
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{spec.width_mm}mm" height="{spec.height_mm}mm" '
        f'viewBox="0 0 {spec.width_mm} {spec.height_mm}"><rect width="100%" height="100%" fill="{bg}"/>'
        f'<g fill="{fg}">{body}</g></svg>'
    )
    path = Path(path)
    path.write_text(svg, encoding="utf-8")
    return path


def write_mask_files(
    out_dir: str | Path,
    spec: MaskSpec | None = None,
    dpi: int = 1200,
    paper: str = "letter",
    film_spec: MaskSpec | None = None,
) -> dict:
    """Write everything a builder needs into ``out_dir``. Returns a dict of paths and pattern facts.

    ``spec`` (default 42 um dots) is used for the laser-printer sheet; ``film_spec`` (default
    25 um dots) for the Gerber and SVG meant for a photoplot/film service.
    """
    spec = spec or MaskSpec()
    film_spec = film_spec or MaskSpec(dot_um=FILM_DOT_UM, seed=spec.seed, bright_dots=spec.bright_dots)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    png, pdf = write_mask_sheet(out / "projector_mask_sheet", spec, dpi=dpi, paper=paper)
    gbr = write_gerber(out / "projector_mask_film.gbr", film_spec)
    svg = write_svg(out / "projector_mask_film.svg", film_spec)
    n = len(dot_centers(spec))
    fill = n * (spec.dot_um / 1000.0) ** 2 / (spec.width_mm * spec.height_mm)
    info = {
        "sheet_png": png,
        "sheet_pdf": pdf,
        "gerber": gbr,
        "svg": svg,
        "n_dots": n,
        "fill": fill,
        "fov_deg": projector_fov_deg(spec),
        "dot_mm_at_250": projected_dot_mm(spec),
        "film_dot_mm_at_250": projected_dot_mm(film_spec),
    }
    log.info("projector mask: %d dots, fill %.0f%%, field %.0fx%.0f deg", n, 100 * fill, *info["fov_deg"])
    return info
