"""Tests for the LED projector mask generator."""
import re

import numpy as np
import pytest
from PIL import Image

from ohs.projector.mask import (
    MaskSpec,
    dot_centers,
    projected_dot_mm,
    projector_fov_deg,
    render_mask,
    write_gerber,
    write_mask_files,
)


def test_dot_centers_blue_noise_properties():
    spec = MaskSpec()
    c = dot_centers(spec)
    assert len(c) > 1500
    half = spec.dot_um / 2000
    assert np.all(np.abs(c[:, 0]) <= spec.width_mm / 2 - half + 1e-9)
    assert np.all(np.abs(c[:, 1]) <= spec.height_mm / 2 - half + 1e-9)
    from scipy.spatial import cKDTree

    d, _ = cKDTree(c).query(c, k=2)
    assert d[:, 1].min() >= spec.min_center_dist_mm - 1e-9
    # blue noise: nearest neighbours cluster just above the minimum distance (no big holes)
    assert np.percentile(d[:, 1], 95) < 2.0 * spec.min_center_dist_mm
    assert np.array_equal(c, dot_centers(spec))  # deterministic
    assert not np.array_equal(c[:10], dot_centers(MaskSpec(seed=2))[:10])


def test_render_scale_and_polarity():
    spec = MaskSpec()
    img = render_mask(spec, dpi=1200)
    assert img.shape == (round(4.2 / 25.4 * 1200), round(5.6 / 25.4 * 1200))
    fill = (img == 255).mean()
    assert 0.12 < fill < 0.4
    dark = render_mask(MaskSpec(bright_dots=False), dpi=1200)
    assert (dark == 0).mean() == pytest.approx(fill, abs=0.02)


def test_optics_helpers():
    spec = MaskSpec()
    assert projected_dot_mm(spec, 6.0, 250.0) == pytest.approx(0.042 * 244 / 6)
    h, v = projector_fov_deg(spec, 6.0)
    assert h == pytest.approx(50.0, abs=0.2) and v == pytest.approx(38.6, abs=0.2)


@pytest.mark.slow
def test_gerber_and_files(tmp_path):
    spec = MaskSpec()
    g = write_gerber(tmp_path / "m.gbr", spec).read_text()
    assert g.startswith("G04") and "%MOMM*%" in g and g.rstrip().endswith("M02*")
    assert len(re.findall(r"D03\*", g)) == len(dot_centers(spec))
    info = write_mask_files(tmp_path / "out", spec, dpi=600)
    for k in ("sheet_png", "sheet_pdf", "gerber", "svg"):
        assert info[k].exists()
    with Image.open(info["sheet_png"]) as im:
        assert im.size == (round(215.9 / 25.4 * 600), round(279.4 / 25.4 * 600))
        assert round(im.info["dpi"][0]) == 600
