"""ChArUco / AprilTag printables, detection and board pose (area C)."""
import re

import cv2
import numpy as np
import pytest
from PIL import Image

from ohs import geometry as G
from ohs.calib.board import (
    APRILTAG_DICTIONARY,
    BoardViewRenderer,
    CharucoSpec,
    aruco_dictionary,
    board_corner_points,
    board_outline_uv,
    detect_charuco,
    estimate_board_pose,
    make_charuco_board,
    render_apriltag_sheet,
    render_board_image,
    render_charuco_page,
)

SPEC = CharucoSpec()
K = np.array([[720.0, 0, 639.5], [0, 720.0, 399.5], [0, 0, 1]])


def _longest_dark_run(row):
    edges = np.flatnonzero(np.diff(np.concatenate([[0], (row < 128).astype(np.int8), [0]])))
    return int((edges[1::2] - edges[::2]).max()) if len(edges) else 0


@pytest.mark.parametrize("paper,size_mm", [("letter", (279.4, 215.9)), ("a4", (297.0, 210.0))])
def test_charuco_page_has_exact_scale(tmp_path, paper, size_mm):
    dpi = 300
    pdf, png = render_charuco_page(SPEC, tmp_path / f"board_{paper}.pdf", paper=paper, dpi=dpi)
    assert pdf.exists() and png.exists() and png.suffix == ".png"
    im = Image.open(png)
    # 220 x 160 mm board -> landscape; page pixels = paper size at dpi
    assert im.size == (round(size_mm[0] / 25.4 * dpi), round(size_mm[1] / 25.4 * dpi))
    assert im.info["dpi"][0] == pytest.approx(dpi, abs=0.01)
    media = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", pdf.read_bytes())
    assert float(media.group(1)) == pytest.approx(size_mm[0] / 25.4 * 72, abs=0.2)
    assert float(media.group(2)) == pytest.approx(size_mm[1] / 25.4 * 72, abs=0.2)

    page = np.asarray(im.convert("L"))
    corners, ids = detect_charuco(page, SPEC)
    assert len(ids) == SPEC.n_corners
    px_per_square = SPEC.square_mm * dpi / 25.4
    nx = SPEC.squares_x - 1
    right = {int(i): c for i, c in zip(ids, corners)}
    steps = [np.linalg.norm(right[i + 1] - right[i]) for i in right if i % nx != nx - 1]
    downs = [np.linalg.norm(right[i + nx] - right[i]) for i in right if i + nx in right]
    assert np.mean(steps) == pytest.approx(px_per_square, abs=0.3)
    assert np.mean(downs) == pytest.approx(px_per_square, abs=0.3)
    # the 100 mm scale bar below the board
    y0 = int(corners[:, 1].max() + px_per_square)
    bar = max(_longest_dark_run(r) for r in page[y0 : y0 + int(15 * dpi / 25.4)])
    assert bar == pytest.approx(100.0 * dpi / 25.4, abs=1.5)
    # detection still works on a camera-like, downscaled copy of the page
    small = cv2.resize(page, None, fx=0.35, fy=0.35, interpolation=cv2.INTER_AREA)
    assert len(detect_charuco(small, SPEC)[1]) == SPEC.n_corners


def test_board_too_large_for_paper(tmp_path):
    big = CharucoSpec(squares_x=14, squares_y=10, square_mm=25.0, marker_mm=18.0)  # 350 x 250 mm
    with pytest.raises(ValueError):
        render_charuco_page(big, tmp_path / "x", paper="a4")


def test_board_raster_matches_opencv_layout():
    ours = render_board_image(SPEC, 10.0)
    ref = make_charuco_board(SPEC).generateImage((2200, 1600), marginSize=0, borderBits=1)
    assert ours.shape == ref.shape and np.count_nonzero(ours != ref) == 0
    assert np.allclose(board_corner_points(SPEC), make_charuco_board(SPEC).getChessboardCorners())


def test_apriltag_sheet(tmp_path):
    dpi = 300
    pdf, pngs = render_apriltag_sheet(tmp_path / "tags", tag_mm=20.0, ids=range(48), paper="letter", dpi=dpi)
    assert len(pngs) == 1 and pdf.exists()
    page = np.asarray(Image.open(pngs[0]).convert("L"))
    assert page.shape == (round(279.4 / 25.4 * dpi), round(215.9 / 25.4 * dpi))  # 48 tags fit in portrait
    det = cv2.aruco.ArucoDetector(aruco_dictionary(APRILTAG_DICTIONARY))
    corners, ids, _ = det.detectMarkers(page)
    assert sorted(np.asarray(ids).ravel().tolist()) == list(range(48))
    side = np.mean([np.linalg.norm(c.reshape(4, 2)[1] - c.reshape(4, 2)[0]) for c in corners])
    assert side == pytest.approx(20.0 * dpi / 25.4, abs=2.0)
    # more ids than fit on one sheet -> several pages in one PDF
    pdf2, pngs2 = render_apriltag_sheet(tmp_path / "many", ids=range(100, 230), paper="a4", dpi=150)
    n_pages = len(re.findall(rb"/Type\s*/Page(?!s)", pdf2.read_bytes()))
    assert len(pngs2) >= 2 and n_pages == len(pngs2)
    with pytest.raises(ValueError):
        render_apriltag_sheet(tmp_path / "bad", ids=[1, 1])


def test_detection_and_pose_on_synthetic_view():
    rng = np.random.default_rng(3)
    R = G.so3_exp([0.35, -0.3, 0.1])
    T_cb = G.make_se3(R, np.array([0.0, 0.0, 330.0]) - R @ np.array([110.0, 80.0, 0.0]))
    img = BoardViewRenderer(SPEC, K, (1280, 800)).render(T_cb, rng=rng)
    corners, ids = detect_charuco(img, SPEC)
    assert corners.dtype == np.float32 and corners.shape == (len(ids), 2) and len(ids) >= 60
    truth = G.project_points(G.transform_points(T_cb, board_corner_points(SPEC)[ids]), K)
    err = np.linalg.norm(corners - truth, axis=1)
    assert np.sqrt(np.mean(err**2)) < 0.15

    ok, T = estimate_board_pose(corners, ids, SPEC, K)
    rot, trans = G.pose_error(T_cb, T)
    assert ok and rot < 0.1 and trans < 0.3

    # a grossly wrong corner is dropped as an outlier
    bad = corners.copy()
    bad[10] += 6.0
    ok, T2 = estimate_board_pose(bad, ids, SPEC, K)
    assert ok and G.pose_error(T_cb, T2)[0] < 0.1

    # outline: margin 0 encloses all corners, the shrunk outline lies inside it
    poly0 = board_outline_uv(T, SPEC, K).astype(np.float32)
    poly5 = board_outline_uv(T, SPEC, K, margin_mm=5.0)
    assert poly0.shape == (4, 2)
    assert all(cv2.pointPolygonTest(poly0, (float(u), float(v)), False) > 0 for u, v in corners)
    assert all(cv2.pointPolygonTest(poly0, (float(u), float(v)), False) > 0 for u, v in poly5)


def test_detection_edge_cases():
    c, i = detect_charuco(np.full((800, 1280), 128, np.uint8), SPEC)
    assert c.shape == (0, 2) and i.shape == (0,)
    pts = board_corner_points(SPEC)
    T = G.make_se3(np.eye(3), [-110.0, -80.0, 400.0])
    uv = G.project_points(G.transform_points(T, pts), K).astype(np.float32)
    ids = np.arange(SPEC.n_corners)
    assert estimate_board_pose(uv[:5], ids[:5], SPEC, K) == (False, None)  # too few corners
    row = ids[:10]  # one chessboard row: collinear
    assert estimate_board_pose(uv[row], row, SPEC, K) == (False, None)
    ok, T_est = estimate_board_pose(uv, ids, SPEC, K)
    assert ok and np.allclose(T_est, T, atol=1e-6)
