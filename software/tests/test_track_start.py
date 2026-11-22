"""The scan's start and a weakly held step (ENGINE_PLAN X4b).

X4 found that the first frame of a scan is registered against a model made of one frame from a prediction of
zero motion and lands 1 to 8 mm off on every simulated scene with a table, and that the dark bracket's whole
3.9 mm error is made in its first two frames and then held by the model. ``PipelineParams.start_frames`` holds the
first frames back, registers them again against a model of the others and only then fuses them
(``ohs.app.pipeline``, "The scan's start"); ``start_detail = "window"`` decides the tracker's detail mode once,
after those frames. X4 also found single frames landing a few mm off where the translation is weakly held, the
jump then carried on by the constant-velocity prediction; ``FusionParams.weak_step_*`` keeps such a jump out of
the next prediction (``ohs.track.markerless``, "A weakly held step"). All off by default.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.app import pipeline as P  # noqa: E402
from ohs.app.pipeline import FramePipeline, PipelineParams  # noqa: E402
from ohs.app.process import trajectory_errors  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.sim.nominal import nominal_calibration  # noqa: E402
from ohs.track.markerless import FusionParams, MarkerlessTracker  # noqa: E402
from ohs.types import TrackResult  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_track_markerless import FPS, H, K, W, arc_trajectory, imu_predictor, render_depth  # noqa: E402

N = 8
DT = 0.1


@pytest.fixture(scope="module")
def reader(tmp_path_factory) -> RecordingReader:
    return RecordingReader(scan_recording(tmp_path_factory.mktemp("start") / "rec", n_frames=N, image_size=(640, 400),
                                          realistic=True, seed=4, depth_every=0))


def _feed(pipe, reader, n=N):
    pipe.add_imu(reader.imu_samples())
    return [pipe.process(reader.frame(i)) for i in range(n)]


# --------------------------------------------------------------------------- the tracker's part
def _tracked(n=4):
    rng = np.random.default_rng(0)
    poses = arc_trajectory(36)[:n + 1]
    gp = imu_predictor(poses, rng)
    tr = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d"))
    depths = [render_depth(T, rng) for T in poses]
    for k in range(n):
        R = gp.relative_rotation((k - 1) / FPS, k / FPS) if k else None
        assert tr.track(depths[k], None, k / FPS, R).ok
    T0 = poses[0]
    return tr, [G.invert_se3(T0) @ T for T in poses], depths


def test_register_measures_a_frame_without_changing_the_tracker():
    tr, truth, depths = _tracked()
    before = (tr.n_integrated, len(tr.trajectory), tr._T_last.copy(), tr._last_step[1].copy())
    T, info = tr.register(depths[4], truth[4])
    assert T is not None and info["reason"] is None and info["fitness"] > 0.5
    assert np.linalg.norm(T[:3, 3] - truth[4][:3, 3]) < 1.5
    assert (tr.n_integrated, len(tr.trajectory)) == before[:2]
    assert np.array_equal(tr._T_last, before[2]) and np.array_equal(tr._last_step[1], before[3])


def test_restart_leaves_the_tracker_as_if_it_had_tracked_those_poses():
    tr, truth, depths = _tracked()
    tr.restart([(depths[k], truth[k], k / FPS) for k in range(4)])
    assert tr.n_integrated == 4 and len(tr.trajectory) == 4 and tr._initialized
    assert np.allclose(tr._T_last, truth[3])
    step = (G.invert_se3(truth[2]) @ truth[3])[:3, 3]
    assert np.allclose(tr._last_step[1], step) and tr._t_last == pytest.approx(3 / FPS)
    res = tr.track(depths[4], None, 4 / FPS, truth[3][:3, :3].T @ truth[4][:3, :3])
    assert res.ok and np.linalg.norm(res.T_wc[:3, 3] - truth[4][:3, 3]) < 1.5
    # rebuild alone keeps the trajectory and the state, and only the model is made again
    n_traj = len(tr.trajectory)
    tr.rebuild([(depths[0], truth[0])])
    assert tr.n_integrated == 1 and len(tr.trajectory) == n_traj


# --------------------------------------------------------------------------- a weakly held step
def _carried(params, steps, weak):
    """Accept poses moving along x by ``steps`` (mm a frame, DT apart); ``weak[k]``: (strength, axis) of frame k."""
    tr = MarkerlessTracker(K, (W, H), params)
    tr._accept(np.eye(4), 0.0, None)
    x, out, moved = 0.0, [], []
    for k, (s, w) in enumerate(zip(steps, weak), start=1):
        x += s
        tr._accept(G.make_se3(np.eye(3), np.array([x, 0.0, 0.0])), k * DT, True, weak=w)
        out.append(float(tr._last_step[1][0]))
        moved.append(tr._weak_mm)
    return out, moved


X = np.array([1.0, 0.0, 0.0])
Y = np.array([0.0, 1.0, 0.0])


def test_a_weakly_held_jump_is_not_carried_into_the_next_prediction():
    """The hole plate 120 mm up (seed 0): at frame 52 one frame landed 4.2 mm off, held at 3.6e-4, and the next two
    predictions started 8 and 23 mm off."""
    steps = [5.0, 5.0, 11.0, 5.0]
    weak = [(1e-2, X), (1e-2, X), (3.6e-4, X), (1e-2, X)]
    hold = FusionParams(voxel_mm=2.0, model_backend="open3d", weak_step_strength=1e-3, weak_step_mm=2.0)
    out, moved = _carried(hold, steps, weak)
    assert out == [pytest.approx(5.0), pytest.approx(5.0), pytest.approx(5.0), pytest.approx(5.0)]
    assert moved == [0.0, 0.0, pytest.approx(6.0), 0.0]
    undo = FusionParams(voxel_mm=2.0, model_backend="open3d", weak_step_strength=1e-3, weak_step_mm=2.0,
                        weak_step_mode="undo")
    out, moved = _carried(undo, steps, weak)
    assert out[2] == pytest.approx(-1.0) and moved[2] == pytest.approx(12.0)  # the next start: back on the line
    # only along the weakly held direction: a weak axis across the jump leaves it as it is
    assert _carried(hold, steps, [(1e-2, X), (1e-2, X), (3.6e-4, Y), (1e-2, X)])[0][2] == pytest.approx(11.0)
    # a frame that holds its translation, or a jump within weak_step_mm, is carried as it is
    assert _carried(hold, steps, [(1e-2, X)] * 4)[0][2] == pytest.approx(11.0)
    assert _carried(hold, [5.0, 5.0, 6.5, 5.0], weak)[0][2] == pytest.approx(6.5)
    # off by default
    off = FusionParams(voxel_mm=2.0, model_backend="open3d")
    assert off.weak_step_strength is None
    assert _carried(off, steps, weak) == ([pytest.approx(s) for s in steps], [0.0] * 4)


def test_weak_step_settings_are_checked():
    with pytest.raises(ValueError):
        FusionParams(weak_step_mode="damp")
    with pytest.raises(ValueError):
        FusionParams(weak_step_strength=-1.0)


# --------------------------------------------------------------------------- the scan's start (the pipeline)
def test_the_start_is_off_by_default(reader):
    assert PipelineParams().start_frames == 0
    with FramePipeline(reader.calibration(), PipelineParams(scale=0.5)) as pipe:
        assert pipe.start_frames == 0 and pipe._start is None
        res = _feed(pipe, reader, 2)
        assert pipe.markerless.n_integrated == 2 and pipe.start_report is None
        assert all("start_window" not in r.track.info for r in res)


def _not_on_open3d(pipe):
    """Without a GPU (the Open3D tracking model) the start is not used: on this recording it went 108 mm off there."""
    assert pipe.start_frames == 0 and pipe._start is None
    assert pipe.params.start_frames > 1


def test_the_start_holds_its_frames_back_then_fuses_them_registered_again(reader):
    k = 5
    pp = PipelineParams(scale=0.5, start_frames=k, start_predict="fit", start_first=True)
    with FramePipeline(reader.calibration(), pp) as pipe:
        if pipe.model_backend != "gpu":
            _not_on_open3d(pipe)
            return
        pipe.add_imu(reader.imu_samples())
        res = []
        for i in range(N):
            res.append(pipe.process(reader.frame(i)))
            if i < k - 1:  # held back: nothing fused, no laser point added, the pose shown as tracked
                assert pipe.markerless.n_integrated == 0 and len(pipe.cloud) == 0
                assert res[-1].track.ok and res[-1].track.info["start_window"] == i + 1
        assert pipe.start_report["registered"] == k and len(pipe.start_report["moved_mm"]) == k
        assert pipe.markerless.n_integrated == N and len(pipe.cloud) > 0
        traj = pipe.trajectory()
        assert np.allclose(traj[0][1], np.eye(4))  # the world is still the first frame's camera
        # the frames after the start are tracked from the start's poses: what the tracker holds is the trajectory
        assert all(np.array_equal(a[1], r.track.T_wc) for a, r in zip(traj[k:], res[k:]))
        gt = reader.ground_truth_poses()
        err = trajectory_errors(gt, [(reader.frame(i).index, T, ok) for i, (_, T, ok) in enumerate(traj)])
        assert err["n"] == N and err["trans_mm"]["max"] < 3.0


def test_a_scan_shorter_than_its_start_is_fused_when_it_is_read(reader):
    with FramePipeline(reader.calibration(), PipelineParams(scale=0.5, start_frames=20)) as pipe:
        if pipe.model_backend != "gpu":
            _not_on_open3d(pipe)
            return
        _feed(pipe, reader, 4)
        assert pipe.markerless.n_integrated == 0
        V, F, *_ = pipe.mesh_with_sigma()
        assert pipe.markerless.n_integrated == 4 and len(F) and pipe.start_report["registered"] == 4


def test_the_start_fit_predicts_from_the_others():
    t = [0.0, 0.1, 0.2, 0.3, 0.4]
    Ps = [G.make_se3(np.eye(3), np.array([1.0 + 20 * s + 30 * s * s, -5 * s, 2.0])) for s in t]
    T = P._fit_start(Ps, t, 1)
    assert np.allclose(T[:3, 3], Ps[1][:3, 3]) and np.allclose(T[:3, :3], np.eye(3))


class _Consensus:
    """Stands in for MarkerlessTracker in the start's sweeps: a frame registers where the model of the others puts it
    (their mean error) plus a bias ``b`` that every registration shares, as measured on the speckled and the dark
    brackets (about 0.2 mm, the same direction for every frame: ENGINE_PLAN X4b, "Why some scenes got worse")."""

    def __init__(self, truth, b):
        self.truth, self.b, self.model = truth, np.asarray(b, float), []

    def rebuild(self, frames):
        self.model = [(int(d), np.asarray(T, float)) for d, T in frames]  # the "depth" is the frame's number here

    def register(self, depth, T_start, gyro=True):
        j = int(depth)
        off = np.mean([T[:3, 3] - self.truth[i][:3, 3] for i, T in self.model], axis=0)
        T = np.asarray(T_start, float).copy()
        T[:3, 3] = self.truth[j][:3, 3] + off + self.b
        return T, {}


def _swept(together, b=(0.0, -0.18, 0.12), first=True, sweeps=2, off=None):
    from types import SimpleNamespace

    t = [k * DT for k in range(10)]
    truth = [G.make_se3(np.eye(3), np.array([80 * s, -10 * s, -100 * s * s])) for s in t]
    pp = PipelineParams(start_frames=10, start_predict="fit", start_first=first, start_sweeps=sweeps,
                        start_together=together)
    tr = _Consensus(truth, b)
    tracked = [T.copy() for T in truth]
    for T in tracked[1:]:  # frames 1 to 9 tracked off frame 0 together (the dark bracket: about 4 mm)
        T[:3, 3] += 0.0 if off is None else np.asarray(off, float)
    out = FramePipeline._refine_start(SimpleNamespace(params=pp), tr, list(range(10)), tracked, t, [True] * 10)
    return np.array([(G.invert_se3(out[0]) @ T)[:3, 3] - T_t[:3, 3] for T, T_t in zip(out, truth)])


def test_the_start_registered_together_does_not_turn_a_shared_bias_into_an_offset():
    """Each frame registered in turn against the others' newest poses (the default): a bias that every registration
    shares moves the frames registered later further, and frames 1 to 9 end up off frame 0 (hard-speck: 0.45 mm, then
    carried by the rest of the scan). With ``start_together`` every frame of a round is registered against the
    others' poses from before the round, so a shared bias moves them all alike: the world goes back to frame 0's
    camera (``start_first``) and the frames stay where they were relative to it."""
    assert np.abs(_swept(True)).max() < 1e-9
    assert np.abs(_swept(True, sweeps=4)).max() < 1e-9
    # what the start is for still happens: frames tracked 4 mm off frame 0 come back to it (by 1/9 of what is left
    # each round)
    assert np.abs(_swept(True, off=(4.0, 0.0, -1.0))).max() < 4.2 / 81 + 1e-9
    assert np.abs(_swept(False)).max() > 0.1  # one at a time, as before (unchanged): the shared bias becomes an offset
    assert PipelineParams().start_together is False


class _Fake:
    """Stands in for MarkerlessTracker in the start's detail decision: ok results with the given strengths."""

    model_status = "fake"

    def __init__(self, strengths):
        self.strengths = list(strengths)
        self.closed = False
        self.detail_mode, self.detail_frame = False, None
        self.last_result = None
        self._n_lost, self._R_acc = 0, np.eye(3)
        self.restarted = None

    def track(self, depth, intensity, t, R_prior, alt_depth=None):
        s = self.strengths.pop(0)
        self.last_result = TrackResult(np.eye(4), True, {"first_frame": True} if s is None else {"trans_strength": s})
        return self.last_result

    def restart(self, frames):
        self.restarted = len(frames)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("rule,switch", [("frame", True), ("window", False)])
def test_detail_mode_after_the_start_and_not_on_its_first_frame(monkeypatch, rule, switch):
    """A scan whose first frame alone looks weak (the one-frame model; 31 of 36 scorecard recordings switch at frame 1
    or 2): the frame rule hands it to the raw-depth tracker at once, the window rule looks at the start's median."""
    d = np.full((400, 640), 300.0, np.float32)
    k = 5
    pp = PipelineParams(scale=0.5, use_lasers=False, use_imu=False, start_frames=k, start_sweeps=0, start_detail=rule)
    with FramePipeline(nominal_calibration(), pp) as pipe:
        pipe.tracker.close()
        pipe.start_frames, pipe._start = k, []  # the decision itself does not depend on the model
        main, probe = _Fake([None, 3e-4, 3e-3, 3e-3, 3e-3]), _Fake([None] + [1e-2] * 9)
        pipe.tracker = main
        monkeypatch.setattr(P, "MarkerlessTracker", lambda *a, **kw: probe)
        monkeypatch.setattr(pipe, "tracking_input", lambda depth: depth)
        for i in range(k):
            pipe._track_markerless(d, i * DT, {}, {})
            pipe._held_now = None
            pipe._trajectory.append((i * DT, np.eye(4), True))
            if pipe._start_due:
                pipe.finish_start()
        assert (pipe.tracker is probe) == switch and pipe.tracker.detail_mode == switch
        assert pipe.tracker.restarted == k and pipe._probe is None and main.closed == switch
        if rule == "window":
            assert pipe.start_report["median_strength"] == pytest.approx(3e-3) and not pipe.start_report["detail_mode"]
            assert main.restarted == k and probe.closed
