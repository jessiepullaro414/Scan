"""Short launches on the graphics card in the fusion (ENGINE_PLAN X5, 2026-11-22), and how they are seen.

Windows resets a display driver whose command runs over 2 s; a 3 s launch of the refine timed the Radeon driver out
and the PC with it. Before Make mesh runs at 0.5 mm (8 times the voxels of 1 mm), every launch of the mesh model is
bounded and its time can be recorded (``ohs.compute.launches``):

* the record is off by default and records nothing; on, it keeps each launch's kernel, size and time;
* the pool's pieces (``_chunks``) cover every byte once, none over the size;
* on the GPU, the model fused in batches of bricks (two forced sizes) and with its pool grown in small pieces equals
  the model fused one launch a frame, bit for bit, every channel;
* with a forced tiny cap no fusion launch takes more bricks than the cap;
* the self-check passes on a sane model and fails, in plain words, when a launch ended in an error or the card's
  memory changed (a driver reset part way).
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from ohs.compute import get_device, launches
from ohs.track import recon_tsdf as RT
from ohs.track.recon_tsdf import NVOX, ConfidenceTsdf

gpu = pytest.mark.skipif(get_device() is None, reason="no OpenCL GPU")


@pytest.fixture
def record():
    launches.enable(True)
    launches.reset()
    yield
    launches.enable(None)
    launches.reset()


def test_the_record_is_off_by_default_and_counts_when_on(monkeypatch, record):
    class Ev:  # a finished command of 0.3 s
        command_execution_status = 0
        profile = SimpleNamespace(start=1_000, end=1_000 + 300_000_000)

    launches.enable(False)
    launches.note("x.k", Ev(), 10)
    launches.timed("x.t", 5, 0.5)
    assert launches.summary() == {}
    launches.enable(True)
    launches.note("x.k", Ev(), 10)
    launches.note("x.k", Ev(), 40)
    launches.timed("x.t", 5, 0.1)
    s = launches.summary()
    assert s["x.k"]["launches"] == 2 and s["x.k"]["largest_items"] == 40
    assert s["x.k"]["longest_s"] == pytest.approx(0.3) and s["x.k"]["over_slow"] == 2
    assert s["x.t"]["over_slow"] == 0 and launches.longest_s() == pytest.approx(0.3)
    bad = SimpleNamespace(command_execution_status=-5)  # a command ended in an error (a driver reset)
    launches.note("x.k", bad, 1)
    assert launches.summary()["x.k"]["errors"] == 1


def test_pieces_cover_every_byte_once():
    for n, size in ((4, 64), (2048, 64), (1 << 20, 4096), (12345 * 4, 1000), (64, 64)):
        ch = RT._chunks(n, size)
        assert ch[0][0] == 0 and sum(s for _, s, _ in ch) == n
        assert all(o2 == o1 + s1 for (o1, s1, _), (o2, _, _) in zip(ch, ch[1:]))
        assert all(0 < s <= size and s % 4 == 0 for _, s, _ in ch)
        assert [last for *_, last in ch] == [False] * (len(ch) - 1) + [True]
    assert RT._chunks(2048) == [(0, 2048, True)]  # what fits is one piece, as before


def frames(n=5, size=(320, 240), seed=0):
    """Depth maps of a tilted plane with a bump at about 300 mm, seen from cameras moving sideways: fusion inputs."""
    rng = np.random.default_rng(seed)
    w, h = size
    K = np.array([[300.0, 0, w / 2], [0, 300.0, h / 2], [0, 0, 1]])
    out = []
    for i in range(n):
        T = np.eye(4)
        T[0, 3] = 6.0 * i
        vs, us = np.mgrid[0:h, 0:w].astype(np.float64)
        z = 300.0 + 0.15 * (us - w / 2) + 0.05 * (vs - h / 2)
        z -= 20.0 * np.exp(-((us - w / 2 + 3 * i) ** 2 + (vs - h / 2) ** 2) / 800.0)
        z += rng.normal(0.0, 0.2, z.shape)
        z[(us // 16 + vs // 16 + i) % 7 == 0] = 0.0  # patches without depth: the gap guard acts
        sig = rng.uniform(0.2, 2.0, z.shape)
        out.append((z.astype(np.float32), sig.astype(np.float32), T))
    return K, size, out


def fuse(forced=None, cap=None, chunk=None, monkeypatch=None):
    K, size, fr = frames()
    if chunk is not None:
        monkeypatch.setattr(RT, "CHUNK_BYTES", chunk)
    m = ConfidenceTsdf(K, size, voxel_mm=1.0, trunc_mm=4.0, backend="gpu", initial_bricks=64,
                       back_mm=2.0, opposite_mm=1.0)
    m.batch_bricks, m.max_batch_bricks = forced, cap
    for d, s, T in fr:
        m.integrate(d, s, T)
    keys, ch = m.download()
    m.close()
    return keys, ch


@gpu
def test_batched_equals_one_launch_bit_for_bit(monkeypatch):
    k0, c0 = fuse(forced=10 ** 9)  # one launch a frame
    assert len(k0) > 1000 and c0["nobs"].max() >= 3 and np.isfinite(c0["tsdf"]).all()
    for forced in (7, 333):
        k, c = fuse(forced=forced)
        assert np.array_equal(k, k0)
        assert all(np.array_equal(c[name], c0[name]) for name in c0), forced
    k, c = fuse(chunk=64 * 1024, monkeypatch=monkeypatch)  # the pool grown and downloaded in small pieces
    assert np.array_equal(k, k0) and all(np.array_equal(c[name], c0[name]) for name in c0)


@gpu
def test_no_fusion_launch_is_over_a_tiny_cap(record):
    cap = 50
    fuse(cap=cap)
    s = launches.summary()
    assert s["mesh_model.integrate"]["launches"] > 5 * 10  # many batches a frame
    assert s["mesh_model.integrate"]["largest_items"] <= cap * NVOX
    assert s["mesh_model.integrate"]["longest_s"] > 0  # timed on the device (the queue has profiling)
    assert {"mesh_model.brick_search", "mesh_model.pool_fill", "mesh_model.gap_row", "mesh_model.download"} <= set(s)


@gpu
def test_the_self_check_fails_after_a_reset_in_plain_words():
    K, size, fr = frames(n=2)
    m = ConfidenceTsdf(K, size, voxel_mm=1.0, trunc_mm=4.0, backend="gpu")
    for d, s, T in fr:
        m.integrate(d, s, T)
    m.self_check()  # a sane model passes
    good = m._last_ev
    m._last_ev = SimpleNamespace(command_execution_status=-5)  # what a launch cut off by a reset reports
    with pytest.raises(launches.GpuResetError, match="graphics driver seems to have been reset.*no mesh was written"):
        m.download()
    m._last_ev = good
    m.download()
    cl = m.cl  # the card's memory changed (a reset loses it): the pattern is gone
    cl.enqueue_fill_buffer(m.queue, m._canary, np.uint32(0), 0, 16)
    with pytest.raises(launches.GpuResetError, match="graphics card"):
        m.download()
    m.close()
