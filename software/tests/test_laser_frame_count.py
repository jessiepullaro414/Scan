"""LaserCloud counts the frames that gave laser points, not the lines added (REVIEW F9).

The pipeline adds one batch of points per laser line, so a frame of the two-laser scanner used to
count twice (four lasers: four times) in ``LaserCloud.n_frames`` and the report's
``lasers.frames_with_points``.
"""
from __future__ import annotations

import numpy as np
import pytest

from ohs import geometry as G
from ohs.track.fusion import LaserCloud


def _line(rng: np.random.Generator, n: int = 50) -> np.ndarray:
    return rng.uniform(-30, 30, size=(n, 3)) + [0.0, 0.0, 250.0]


def test_a_frame_with_two_lines_counts_once():
    rng = np.random.default_rng(0)
    lc = LaserCloud()
    T = G.make_se3(G.so3_exp([0.1, 0.0, 0.0]), [1.0, 2.0, 3.0])
    for _ in range(3):  # three exposures at the same pose are three frames
        assert lc.add_frame({"vertical": _line(rng), "horizontal": _line(rng)}, T) == 100
    empty = np.full((4, 3), np.nan)
    assert lc.add_frame({"vertical": empty, "horizontal": np.zeros((0, 3))}, T) == 0  # nothing: not a frame
    assert lc.add_frame({"vertical": empty, "horizontal": _line(rng, 7)}, T) == 7  # one line seen: a frame
    assert lc.add_frame({}, T) == 0
    assert lc.n_frames == 4 and lc.n_additions == 7 and len(lc) == 307
    assert lc.raw_counts() == {"vertical": 150, "horizontal": 157}
    # a lone add is one frame, as before
    assert lc.add(_line(rng), T, "vertical") == 50 and lc.n_frames == 5 and lc.n_additions == 8
    lc.clear()
    assert lc.n_frames == 0 and lc.n_additions == 0 and len(lc) == 0


def test_the_points_are_those_of_add():
    """add_frame stores exactly what one add per line stores."""
    rng = np.random.default_rng(1)
    T = G.make_se3(G.so3_exp(rng.normal(scale=0.3, size=3)), rng.normal(scale=10, size=3))
    pts = {"vertical": _line(rng), "horizontal": _line(rng)}
    a, b = LaserCloud(), LaserCloud()
    a.add_frame(pts, T)
    for name, P in pts.items():
        b.add(P, T, name)
    assert a.names == b.names and a.raw_points().tobytes() == b.raw_points().tobytes()


# --------------------------------------------------------------------------- the pipeline and the report
N = 8


@pytest.fixture(scope="module")
def rec(tmp_path_factory):
    pytest.importorskip("open3d")
    from recordings import scan_recording  # noqa: PLC0415

    return scan_recording(tmp_path_factory.mktemp("lfc") / "rec", n_frames=N, image_size=(640, 400),
                          realistic=True, seed=4, depth_every=0)


def test_the_pipeline_counts_frames_not_lines(rec):
    """Every frame of the two-laser recording sees both lines; all are given the same pose (the
    count must not merge frames by pose)."""
    from ohs.app.pipeline import FramePipeline, PipelineParams  # noqa: PLC0415
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    reader = RecordingReader(rec)
    with FramePipeline(reader.calibration(), PipelineParams(use_imu=False, tracking=False)) as pipe:
        res = [pipe.process(reader.frame(i), np.eye(4)) for i in range(N)]
        lines = [sum(1 for P in r.laser_points.values() if np.isfinite(P).all(axis=1).any()) for r in res]
        assert lines == [2] * N, lines
        cloud = pipe.laser_cloud()
        assert cloud.n_frames == N and cloud.n_additions == 2 * N


def test_the_report_counts_frames_with_points(rec, tmp_path):
    from ohs.app.process import process_recording  # noqa: PLC0415

    rep = process_recording(rec, tmp_path / "out")
    assert rep["frames"]["processed"] == N and rep["frames"]["tracked_fraction"] == 1.0
    assert set(rep["lasers"]["planes"]) == {"vertical", "horizontal"}
    assert rep["lasers"]["frames_with_points"] == N, rep["lasers"]
