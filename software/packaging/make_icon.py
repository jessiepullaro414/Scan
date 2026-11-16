"""Draw the app's icon, ``ohs/server/assets/ohs.ico``: the logo of ``ui/public/favicon.svg`` (a dark
rounded square, a blue crosshair with a ring, a white dot) at 16-256 px. The window shows it in its
title bar and the taskbar; ``build_exe.py`` also gives it to ``ohs.exe``.

    python packaging/make_icon.py

(run from ``software/``; needs Pillow, which the engine has). Drawn with Pillow rather than
rendered from the SVG so that no SVG renderer is needed; keep the two in step by hand.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "ohs" / "server" / "assets" / "ohs.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def logo(px: int = 256, ss: int = 4) -> Image.Image:
    """The logo at ``px`` x ``px`` (drawn ``ss`` times larger and scaled down for smooth edges)."""
    n = px * ss
    u = n / 64.0  # the SVG's 64-unit grid

    def box(cx: float, cy: float, r: float) -> list[float]:
        return [(cx - r) * u, (cy - r) * u, (cx + r) * u, (cy + r) * u]

    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2 * u, 2 * u, 62 * u, 62 * u], radius=14 * u, fill=(16, 20, 26, 255),
                        outline=(255, 255, 255, 31), width=max(1, round(1 * u)))
    # the crosshair and its ring in the blue gradient (#5aa2ff top left -> #2f6fe8 bottom right)
    mask = Image.new("L", (n, n), 0)
    m = ImageDraw.Draw(mask)
    w = 5 * u
    m.line([32 * u, 12 * u, 32 * u, 52 * u], fill=255, width=round(w))
    m.line([12 * u, 32 * u, 52 * u, 32 * u], fill=255, width=round(w))
    for cx, cy in ((32, 12), (32, 52), (12, 32), (52, 32)):  # round caps
        m.ellipse(box(cx, cy, 2.5), fill=255)
    m.ellipse(box(32, 32, 11), fill=255)  # the ring: 9 +- 2
    hole = Image.new("L", (n, n), 0)
    ImageDraw.Draw(hole).ellipse(box(32, 32, 7), fill=255)
    mask = ImageChops.subtract(mask, hole)
    a, b = (0x5A, 0xA2, 0xFF), (0x2F, 0x6F, 0xE8)
    grad = Image.linear_gradient("L").rotate(45, expand=True).resize((n, n))  # 0 top left .. 255 bottom right
    blue = Image.merge("RGB", [grad.point(lambda t, i=i: round(a[i] + (b[i] - a[i]) * t / 255)) for i in range(3)])
    img.paste(blue, (0, 0), mask)
    d.ellipse(box(32, 32, 3), fill=(255, 255, 255, 255))
    return img.resize((px, px), Image.LANCZOS)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    big = logo(256)
    big.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
