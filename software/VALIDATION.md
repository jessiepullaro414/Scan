# OpenHandScan: end-to-end validation on simulated data

This document reports how accurately the whole processing chain works on data from the simulator
(`ohs.sim`). It covers calibration from recordings, the per-frame pipeline, offline processing and
export, compared against the simulator's ground truth. **Everything here is simulation.** The
numbers are an upper bound on what the real scanner will do; see
[Simulation limitations](#simulation-limitations).

Environment: Windows 11, Python 3.13, numpy 2.5.4, opencv-contrib 5.0.0, open3d 0.20.0 (CPU),
16 logical CPUs. Data: 1280x800 per camera, realistic lens model (distortion, 0.3 deg camera
rotation; rectification is not trivial), dot projector and both laser lines on. Each scan is 36
frames at 30 fps (1.2 s of hand-held motion: 63 mm and 28 deg of camera travel on the sphere scan)
with IMU at 416 Hz.

## How to reproduce

```
# fast checks (about 2 min for the three files)
C:\Users\Garage\.venvs\ohs\Scripts\python.exe -m pytest software\tests\test_app_pipeline.py software\tests\test_e2e_calibration.py software\tests\test_e2e_scan.py -q
# the full runs behind the table below (about 7 min); -s prints one "METRICS {...}" line per run
C:\Users\Garage\.venvs\ohs\Scripts\python.exe -m pytest software\tests\test_e2e_calibration.py software\tests\test_e2e_scan.py -m slow -s -q
```

## Results (full runs)

| Item | Metric | Result | Expectation |
|---|---|---|---|
| **(a) Stereo calibration**, 20 board views | fx error, left / right | -0.05 / -0.01 px | |
| | cx / cy error (left) | -0.01 / -0.14 px | |
| | baseline error | +0.005 mm | |
| | relative rotation error | 0.011 deg | |
| | row error after rectification, median / p95 | 0.006 / 0.025 px | < 0.3 px |
| | depth scale error (triangulation at 200-450 mm) | 0.001 % | |
| | reprojection rms | 0.075 px | < 0.5 px |
| **(b) Laser planes**, 8 views, true stereo, **margin** (default) | normal angle, vertical / horizontal | 0.0056 / 0.0054 deg | |
| | d error, vertical / horizontal | -0.016 / -0.018 mm | |
| | laser-point error, median / max (vertical; horizontal) | 0.011 / 0.13 mm; 0.028 / 0.24 mm | |
| (b) same views, **board** region | normal angle, V / H | 0.078 / 0.037 deg | |
| | d error, V / H | +0.26 / -0.13 mm | |
| | laser-point error, median / max (V; H) | 0.16 / 1.7 mm; 0.20 / 1.7 mm | |
| (b) same views, board + margin (`both`) | laser-point error, median (V; H) | 0.058; 0.048 mm | |
| (b) **estimated** stereo, margin | normal angle, V / H | 0.0077 / 0.013 deg | |
| | d error, V / H | -0.028 / -0.007 mm | |
| | laser-point error, median / max (V; H) | 0.019 / 0.17 mm; 0.14 / 0.75 mm | |
| (b) estimated stereo, board | laser-point error, median (V; H) | 0.16; 0.23 mm | |
| **(c) IMU calibration**, 7 s (1.2 s still, then rotation), 210 frames | R_ci error (estimated stereo / true stereo) | 0.025 / 0.031 deg | |
| | time-offset error | 0.019 / 0.014 ms | |
| | gyro-bias error | 0.087 mrad/s (0.005 deg/s) | |
| | rotation residual rms | 0.035 deg | |
| **(d) Markerless scan**, sphere on plane, true calibration | frames tracked | 36 / 36 (100 %) | > 90 % |
| | trajectory error, median / max | 0.19 mm, 0.035 deg / 0.29 mm, 0.077 deg | < 3 mm / 1 deg |
| | mesh distance, median / p95 | 0.20 / 0.67 mm (sphere 0.12, table 0.20) | median < 0.3 mm |
| | laser points, median / p95 | 0.068 / 0.28 mm (sphere 0.084) | median < 0.2 mm |
| **(e) Marker mode**, same recording | frames tracked | 36 / 36 | |
| | trajectory error, median / max | 0.94 mm, 0.22 deg / 3.8 mm, 0.93 deg | |
| | mesh, median / p95 | 0.71 / 2.0 mm (sphere 0.11, table 0.72) | |
| | laser points, median / p95 | 0.18 / 0.60 mm | |
| | tag map: tags, `refine_map` rms | 5 tags, 0.28 mm | |
| **(f) Markerless scan, car panel** (nearly featureless) | frames tracked | 36 / 36 | |
| | trajectory error, median / max | 0.63 mm, 0.039 deg / 0.97 mm, 0.065 deg | |
| | mesh, median / p95 | 0.29 / 1.17 mm (panel 0.11, floor 0.35) | |
| | laser points, median / p95 | 0.067 / 0.23 mm | |
| (f) same, tracking on raw depth (`track_filter_px=0`, the original design) | trajectory error, max | 13.3 mm, 2.4 deg | |
| **(g) Full chain**: (d) with the calibration estimated in (a)-(c) | frames tracked | 36 / 36 | > 90 % |
| | trajectory error, median / max | 0.23 mm, 0.035 deg / 0.34 mm, 0.079 deg | < 3 mm / 1 deg |
| | mesh, median / p95 | 0.27 / 0.77 mm | median < 0.3 mm |
| | laser points, median / p95 | 0.078 / 0.29 mm | median < 0.2 mm |

SGBM and Open3D are multi-threaded, so repeated runs can differ in the third significant digit.

Half resolution (640x400, the live preview), sphere scene, 24 frames (fast test): trajectory
error max 0.49 mm / 0.085 deg, mesh median 0.20 mm (2 mm voxels), laser median 0.15 mm.
The same recording without the IMU drifts 30 mm / 12 deg: the scene is symmetric about the
sphere axis, and the tracker flags it (`info["degenerate"]`).

## Timings

These are medians per frame on the CPU described above, from the full runs. Offline processing runs
at full resolution (1280x800); the live preview runs at half resolution (640x400, measured in the
fast tests).

| Stage (ms) | Offline 1280x800, markerless | Offline 1280x800, markers | Live 640x400, markerless |
|---|---|---|---|
| rectify (both images) | 2 | 2 | 1 |
| depth (SGBM + sub-pixel refinement) | 121-125 | 125 | 20-24 |
| laser lines (extract L+R, triangulate) | 24-26 | 26 | 9-12 |
| tracking-depth filter | 36-39 | - | 5 |
| ICP: ray cast / Gauss-Newton | 100-116 / 15-25 | - | 29 / 7-8 |
| TSDF integration: tracking model / reconstruction | 5 / 16-18 | - / 18 | 4 / 5 |
| marker detection + pose | - | ~44 | - |
| **total** | **348-361** | **217** | **86-100** |

Mesh extraction takes about 0.9 s per scan (0.8 M faces at 1 mm voxels); cleaning and export add
about 1 s. A 36-frame scan processes in about 15 s offline (`report.json` `wall_time_s` 14.8). The
simulator renders about 1 s per full-resolution stereo frame.

## GPU processing (M3, 2026-11-09)

The heavy steps now run on any OpenCL GPU (`ohs.compute`), and the CPU path above is the fallback.
- **Depth:** census semi-global matching on the GPU (`ohs/vision/depth_gpu.py`).
- **Tracking model:** a 2 mm TSDF on the GPU, with trilinear ray casting (`ohs/track/volume_gpu.py`).

"GPU mode" below means GPU depth and the GPU tracking model, the defaults when a GPU exists. "CPU mode" is `OHS_COMPUTE=cpu`.

**Depth against the true depth**, 1280x800, 8 frames of the sphere and car-panel scenes:

| | GPU | CPU (SGBM) |
|---|---|---|
| median abs. error | 0.316 mm | 0.369 mm |
| 95th percentile | 1.41 mm | 2.14 mm |
| off by > 2 mm | 1.8 % | 5.6 % |
| disparity off by > 1 px | 0.9 % | 4.1 % |
| bias | -0.002 mm | +0.117 mm |
| valid | 89.8 % (98.2 % where dots land) | 93.6 % (97.6 %) |

- **Where the GPU keeps no depth:** pixels with no dots near them, i.e. outside the projector's field. SGBM's depth there is mostly wrong (54 % of it is off by more than 2 mm).
- **640x400:** the GPU is also ahead there: off by > 2 mm 5.3 % vs 8.8 %, disparity off by > 1 px 1.1 % vs 2.8 %.

**Trajectory error, full runs** (median / max):

| Scan | VALIDATION (CPU, above) | GPU mode |
|---|---|---|
| sphere, 1280x800, 36 frames | 0.19 mm, 0.035 deg / 0.29 mm, 0.077 deg | 0.108 mm, 0.023 deg / 0.168 mm, 0.039 deg |
| car panel, 1280x800 | 0.63 mm, 0.039 deg / 0.97 mm, 0.065 deg | 0.110 mm, 0.025 deg / 0.261 mm, 0.034 deg |
| sphere, 640x400 (fast test) | max 0.49 mm / 0.085 deg | max 0.211 mm / 0.035 deg |

- **Mesh accuracy:** in GPU mode the mesh median is 0.065 mm (sphere) and 0.088 mm (car panel), against 0.20 mm and 0.29 mm above.

**Robustness over 15 scans** (3 scenes with extra seeds, at 1280x800 and 640x400; max trajectory error per scan):
- The GPU tracking model is better than Open3D's on 12 of 15 scans with SGBM depth, and on 11 of 15 with GPU depth.
- **This set exposed a weakness of the CPU path that the single validation seed hid.** On 3 of the 15 recordings (the sphere scene with extra seeds) the Open3D tracking model slides 13-42 mm with either depth. The GPU model stays below 0.4 mm on the same recordings.
  - The cause: Open3D's nearest-voxel ray cast drops most surface hits near the borders of the observed area. Those borders are exactly where stereo depth carries camera-fixed bias.
  - The GPU ray cast keeps a hit only when it is 8 mm inside the observed surface and its voxel has been fused at least 8 times (`FusionParams.gpu_border_mm`, `gpu_weight_threshold`).
- **GPU depth against SGBM:** on 26 of the 30 (scan, model) pairs, GPU depth tracks at or below SGBM's error. Three remaining pairs differ by a few thousandths of a degree or 0.05 mm, which is tracking noise. On the fourth, Open3D's model fails with either depth.
- **Depth fix that this set forced:** on the car panel, the area below the projector's field has no dots. Early GPU depth carried smooth but wrong depth into it from the neighbouring dots, and tracking slid 1.8 mm. Depth is now kept only where at least 20 % of a 15 x 15 window holds census texture.

**Speed (`ohs bench`, 90 frames, Radeon 680M integrated GPU in the Ryzen 7 7735U):** ms per frame, mean.

| Stage | CPU | GPU |
|---|---|---|
| depth | 123.9 | 68.7 |
| ray cast (tracking model) | 116 | 7.2 |
| whole frame, full quality | 338 (2.96 fps) | 187 (5.34 fps), **1.81x** |
| whole frame, preview quality | 83 (12.0 fps) | 58.5 (17.1 fps), 1.42x |

- **Shared power:** on this integrated GPU, the CPU stages that remain (laser lines, ICP) run slightly slower while the GPU works, because both share power and memory bandwidth.

**Speed on the owner's laptop** (i9-12900H, RTX 3070 Ti Laptop 8 GB, on AC power, same bench). All 50 GPU tests pass on NVIDIA's OpenCL compiler.

| Stage (ms, mean) | CPU | GPU |
|---|---|---|
| depth | 116.4 | 36.6 |
| ray cast (tracking model) | 111.9 | 5.9 |
| whole frame, full quality | 308 (3.24 fps) | 119 (8.38 fps), **2.58x** |
| whole frame, preview quality | 66.2 (15.1 fps) | 41.2 (24.2 fps), 1.60x |

- **Accuracy matches the 680M runs:** trajectory max 0.25 mm / 0.053 deg (GPU) against 0.68 mm / 0.174 deg (CPU).
- **Where the RTX still waits:**
  - The census-SGM kernels give it too little parallel work: depth takes 37 ms at full resolution, and at 640x400 it is no faster than the CPU.
  - ICP is slower in GPU mode, because the denser depth gives more source points.
  - Laser extraction, the tracking filter and the 1 mm reconstruction integration still run on the CPU.
  - These are the next tuning targets.
- **In the app:** a 15 s simulated scan (449 frames) meshed at full quality in about 1 min 50 s on the GPU.
- **Results files:** in `~/.ohs/bench/results/`; `ohs bench` reproduces them on any machine.

## GPU tuning (M3, 2026-11-10)

The first tuning round, from `ohs bench` per-stage tables on both machines. The tuning targets above turned out to be ordering and sampling more than kernels.

**What changed:**
- **The laser lines run in the background.** Two CPU threads extract them while the depth runs (on the GPU, or in SGBM's threads). On the laptop the frame now never waits for them. On the Radeon it still waits a little, because there the depth step's own CPU work competes with them. One thread was slower than two.
- **ICP samples every 8th pixel at its finest level** at full resolution (no level closer than 8 px at 1280 px width, scaled with the width; a 640 px preview keeps its strides of 16, 8 and 4).
  - First the strides themselves scaled with the width. At 320x200 that meant every 2nd pixel, and the CPU path's live-scan test then crashed in Open3D's ray cast in 2 of 3 runs. The minimum spacing leaves everything below full resolution as it was.
  - On five simulated scans the trajectory RMS went 0.13 / 0.24 / 0.08 / 1.06 / 0.74 → 0.14 / 0.19 / 0.08 / 0.69 / 0.68 mm.
  - It is 3–4 times faster.
  - Neighbouring pixels share the camera-fixed dot error, so the denser sampling added bias, not information.
- **The confidence TSDF weighs its pixels on the GPU.** Several full-image numpy passes on the CPU are gone, and one image fewer is uploaded. Its brick search now samples the same share of a brick at every voxel size: at preview every 4th pixel instead of every 2nd, and the mesh keeps its area within 0.06 %.
- **Stereo-matching work-groups (not changed):** a sweep of `GpuTuning.lines_v`, `lines_h` and `dpt` found the defaults fastest on both GPUs (22.5 ms per frame on the RTX, 72.8 ms on the 680M), and every setting gives the same disparities. Larger work-groups don't fit: the vertical SGM kernel is limited to 256 work-items on both GPUs.

**Speed, GPU mode** (ms per frame, mean; same bench, same accuracy):

| Machine | Quality | Before (a024ec4) | After (279d709) |
|---|---|---|---|
| RTX 3070 Ti laptop | full | 124.4 (8.0 fps) | **67.2 (14.9 fps)** |
| | preview | 53.1 (18.8 fps) | **28.4 (35.2 fps)** |
| Radeon 680M | full | 217.4 (4.6 fps) | **172.0 (5.8 fps)** |
| | preview | 73.0 (13.7 fps) | **67.0 (14.9 fps)** |

- The intermediate commit 880aa6b benched 72.6 / 33.0 ms on the laptop and 170.1 / 66.3 ms on the Radeon. The final version's minimum ICP spacing did not slow the preview down. The laptop's 5–14 % gain since then is probably mostly noise and temperature: its run was an hour later.
- The CPU path got faster too. On the laptop at full quality it went from 332 ms (3.0 fps) to 264 ms (3.8 fps), and its trajectory error fell from 0.33 mm / 0.075° to 0.24 mm / 0.028°. The GPU path is now 3.9 times faster than the CPU path at full quality and 2.3 times at preview.
- Trajectory error on the bench in GPU mode: 0.13 mm / 0.017° median at full quality (it was 0.15 mm / 0.035°), and 0.21 mm / 0.025° at preview (unchanged).
- **Per-stage times can mislead.** The tracking model's GPU integration is queued and returns at once, so the next frame's depth step waits for it: on the laptop the matcher alone takes 22.5 ms, while "depth" shows 27.2 ms. The whole-frame time is the number to trust.
**Round 2: the last two CPU steps on the GPU** (2b1eae0):
- **The tracking filter** (`ohs/track/filter_gpu.py`) gives `tracking_depth`'s map in four kernels:
  - edge test and block average;
  - a row and a column Gaussian pass;
  - an erosion with the offsets that `cv2.distanceTransform` itself rates within the radius;
  - the bilinear upsampling.

  On the bench recording 12 of 15.4 M pixels differ in validity, and the values agree within 0.014 mm.
- **The confidence TSDF's brick search** puts the frame's rays into a GPU hash set, and only the frame's brick list comes back. It gives the same bricks in the same order, and the same volume bit for bit. Beyond 512 bricks from the first camera (4 m at 1 mm voxels) it falls back to the CPU search.
- **A CPU filter bug found on the way:** the full-resolution path blurred `depth * valid`, and NaN × 0 is NaN, so a pixel without depth emptied the blur radius around it. It now blurs only the valid depth, as the half-resolution path always did. The pipeline uses the half-resolution path, so its scans were not affected.

| Machine | Quality | Round 1 (279d709) | Round 2 (2b1eae0) |
|---|---|---|---|
| RTX 3070 Ti laptop | full | 67.2 (14.9 fps) | **49.1 (20.4 fps)** |
| | preview | 28.4 (35.2 fps) | **25.5 (39.2 fps)** |
| Radeon 680M | full | 172.0 (5.8 fps) | **156.3 (6.4 fps)** |
| | preview | 67.0 (14.9 fps) | **63.0 (15.9 fps)** |

- On the laptop the filter went from 11.5 to 1.6 ms and the integration from 7.4 to 2.4 ms. "depth" went from 27.2 to 23.8 ms because it waits less on the queued GPU work: the matcher alone takes 22.5 ms. On the Radeon the filter went from 17 to 6.7 ms, mostly upload and download through the iGPU's driver, and the integration from about 15 to 9.7 ms.
- Since this morning (a024ec4) the GPU path is 2.5 times faster at full quality on the laptop (8.0 → 20.4 fps) and 2.1 times at preview. Trajectory errors are unchanged from round 1.
- **What limits it now:**
  - On the laptop the stereo matcher is 23.8 of 49 ms.
  - The two laser threads take about 16 ms each, so once the depth step gets below about 16 ms they become what the frame waits for.
  - At preview, ICP (5.3 ms, on the CPU) is the biggest tracking stage.
  - Every frame still crosses PCIe several times: the depth and sigma are downloaded, then uploaded again for the filter and the reconstruction, and the filtered depth goes up for the tracking model. Keeping them on the GPU would be the next step.

## Straighten: loop closure (M3 phase 2, 2026-11-09)

`ohs/track/posegraph.py` closes loops where a scan passes over an earlier part again:
- **Keyframes** every 30 mm, 10° or 2 s.
- **Candidates:** revisits that look at the same spot from a similar direction after at least 150 mm of travel.
- **Verification:** point-to-plane ICP, which moves only along the directions the surfaces actually measure, with limits on fitness, overlap and how much drift is plausible.
- **The fix:** a pose graph solved with Open3D's Levenberg-Marquardt and line process.
- **Safety:** it changes a scan only when the loops were off by more than 0.2 mm, their residual at least halves, and no odometry edge moves more than the drift model allows. Otherwise the scan comes back unchanged, with a plain verdict.

**Synthetic drift** added to good poses (a random walk per frame). "Gap" is the distance between the two passes' surfaces along their normals, i.e. what shows as doubled surfaces. RMS, mm, before → after:

| Scan | Drift per frame | Trajectory vs truth | Gap |
|---|---|---|---|
| bracket orbit, 60 frames (test) | 0.05° / 0.2 mm | 1.52 → 1.33 | 0.76 → 0.085 |
| bracket orbit, 60 frames (test) | 0.1° / 0.3 mm | 1.95 → 1.56 | 1.42 → 0.11 |
| sphere orbit, 180 frames | 0.05° / 0.2 mm | 2.15 → 1.53 | 1.90 → 0.10 |
| car-panel sweep, 180 frames | 0.1° / 0.3 mm | 3.57 → 2.41 | 2.44 → 0.25 |
| 1-minute scan, 1280x800, 720° | 0.05° / 0.2 mm | 2.73 → 2.33 | 1.34 → 0.10 |

**Real tracking:**
- The tracked poses of the car-panel sweep and of a 400° sphere orbit come back unchanged ("the passes already line up"): the gap is 0.04-0.08 mm.
- Frame-to-model tracking re-registers to its own model when it returns. What drift it has lies along directions no loop can measure: sliding along the panel, turning about the sphere's axis.

**No false loops:**
- True poses come back bit-identical in every scenario, including 48 loops on the 1-minute scan.
- Short scans give no candidates.
- Deliberately wrong candidates are rejected: offsets of 12-150 mm and tilts of 6°.
- A corrupted loop among good ones is pruned by the line process.

**Speed:** 8.1 s for a 1-minute 1280x800 scan on the Radeon 680M PC (105 keyframes, 48 candidates), and about 1 s for short scans.

**Limits:**
- Loops correct what the surfaces fix: distance and tilt, which is what doubles surfaces. They don't correct sliding along a plane or turning about a sphere's axis.
- The trajectory error against the truth improves much less than the gap. In single runs its maximum grew by up to 0.26 mm along unmeasured directions.
- Drift beyond about 20 mm couldn't be closed: see the next section for what changed that.

## Straighten for large drift (M3 phase 3, 2026-11-10)

Research plan 4.4.1. Three additions to Straighten (`ohs/track/posegraph.py`, "Large drift"):
- **A wider candidate gate** that grows with the camera travel between the two views.
- **A second check** for a candidate that fails: against wider drift bounds, first by ICP from the tracked pose, then from a global first guess (FPFH features matched by Fast Global Registration, else RANSAC, in Open3D; a guess implausibly far from the tracked pose is skipped). A loop that needs a large correction counts only when a second loop also passes.
- **A drift model scaled to what the loops show.** The default model allows about 2 mm between the ends of a 60-frame orbit. With more drift than that, the graph's line process took the loops as disagreeing and pruned them all.

**Setup:** the 400° bracket orbit of the tests (60 frames) and a 760° one (120 frames), 640x400, with drift added to the true poses: a random walk (as before, larger), or a steady drift (the same small motion after every frame, like a gyro bias). Errors are measured on true surface points seen by both passes, at 64 (400°) and 81 (760°) revisit pairs found from the truth, the same pairs for every method: the **gap** along the surface normal (doubled surfaces) and the **slide** along the surface. RMS, mm:

| Orbit | Drift per frame | End of scan off by | Gap: before → old → new | Slide: before → old → new |
|---|---|---|---|---|
| 400° | random 0.05° / 0.2 mm (the tests) | 2 mm | 1.01 → 0.27 → **0.17** | 2.1 → 2.0 → 2.0 |
| | random 0.3° / 1 mm | 11 mm | 5.3 → 3.5 → **0.98** | 12.6 → 12.0 → 11.7 |
| | random 0.5° / 2 mm | 11 mm | 8.1 → 8.1 (gave up) → **1.9** | 12.1 → 12.1 → 11.7 |
| | steady 0.2° / 1 mm | 17 mm | 8.1 → 1.3 → **1.1** | 19.5 → 19.2 → 22.3 |
| | steady 0.4° / 2 mm | 35 mm | 19.0 → 2.7 → **2.2** | 38.4 → 37.7 → 44.7 |
| | steady 0.8° / 4 mm | 78 mm | 50.1 → 50.1 (nothing verified) → **2.7** | 73.9 → 73.9 → 75.5 |
| 760° | random 0.05° / 0.2 mm | 1 mm | 1.04 → 0.21 → **0.18** | 1.5 → 1.8 → 1.9 |
| | random 0.3° / 1 mm | 7 mm | 6.0 → 2.5 → **1.1** | 9.4 → 10.8 → 6.3 |
| | random 0.5° / 2 mm | 31 mm | 9.8 → 9.8 → **1.6** | 15.1 → 13.9 → 11.6 |
| | steady 0.2° / 1 mm | 43 mm | 5.9 → 1.5 → **0.98** | 23.2 → 23.1 → 23.9 |
| | steady 0.4° / 2 mm | 87 mm | 9.4 → 3.6 → **2.0** | 47.3 → 47.2 → 48.7 |
| | steady 0.8° / 4 mm | 172 mm | 12.2 → 8.8 → **3.9** | 98.0 → 98.3 → 96.0 |

- **Doubled surfaces close in every case,** 3–18 times. The old version gave up on a random walk ten times the usual (the line process pruned every loop) and verified nothing at 78 mm.
- **Sliding is not fixed, by either version.** With steady drift the new one slides up to 17 % more. Loops measure distance and tilt, not sliding along the table that most of each view shows. The trajectory against the truth improves in most cases (400°, random 0.5°: 10.6 → 5.8 mm RMS) but stays far off with steady drift.
- **The global first guess** is what verified 1–3 loops at 78 mm (FGR is randomised: the gap after varies between 2.7 and 2.8 mm). Below about 35 mm, ICP from the tracked pose with the wider bounds was enough. On the CPU's depth, FGR once matched a view to its mirror image, 180° off: RANSAC is the fallback, and a guess farther from the tracked pose than the drift bounds is not tried.
- **Small drift:** the gap is a little better than before, the slide unchanged. True poses still come back unchanged (12 loops on the 400° orbit, 47 on the 760° one).
- **Time:** more candidates (14–18 instead of 5 on the 400° orbit, the cap of 48 on the 760° one): 3–6 s and 7–9 s.
- **Not tested:** drift beyond the gate (120 mm between two views), tracking losses that jump, symmetric or repetitive parts where a global match can be wrong, and real recordings.

## Laser-curve adjustment (M3 phase 3, 2026-11-10)

Research plan 7.3, poses only (`ohs/track/curve_ba.py`, run by Straighten). Where the laser curves of different frames cross on the surface, they must meet. All poses are solved together with the tracked motion as a prior, plus a weak term of each frame's depth against the fused surface. The poses change only when, fitted on half the crossings, the other half meet clearly better.

**Setup:** a 400° hand-held orbit around the sphere on the table, 60 frames at 1280x800, with drift added to the true poses, then Straighten (loops), then the adjustment, then a fuse. The mesh error is the median distance to the analytic surfaces, in mm.

| Drift per frame | Crossings | Held-out ratio | Sphere: Straighten → + crossings | Table: Straighten → + crossings | Trajectory RMS |
|---|---|---|---|---|---|
| none (true poses) | 596 | 1.86: unchanged | 0.086 → 0.086 | 0.089 → 0.089 | 0 |
| random 0.05° / 0.2 mm | 597 | 0.73 | 0.166 → **0.118** | 0.117 → **0.107** | 1.31 → 0.99 |
| random 0.1° / 0.3 mm | 580 | 0.46 | 0.238 → **0.141** | 0.191 → **0.172** | 1.50 → 0.88 |
| steady 0.1° / 0.5 mm | 630 | 0.46 | 0.973 → **0.484** | 0.270 → **0.258** | 8.49 → 5.79 |

The true poses give 0.086 (sphere) and 0.089 mm (table).

- **The weak dense term matters.** Without it, the frames that see few crossings (the far table) turned freely: in the first row with drift, the table went from 0.117 to 0.341 mm while the sphere improved.
- **The held-out test is what keeps it safe.** Fitting crossings that already meet bends good poses: with the true poses the fit moved the cameras by 0.38 mm RMS and the sphere got worse (0.086 → 0.098 mm). At 640x400 the lasers scatter more (0.15 mm median miss at the truth). There it also refused a small drift that it would have made worse (bracket orbit: trajectory 1.30 → 1.69 mm if applied). A real tracked 36-frame pass gave 63 crossings, under the minimum of 150, so it was left alone.
- **Few crossings on short scans.** A frame's horizontal line meets another frame's vertical line near where the two lines cross in the image, where the extraction drops points. Sweeping in two directions, or around the part, makes crossings.
- **Time:** about 8 s for 60 frames at full resolution (curves 2.6 s, depth 4.4 s, solve 0.8 s), plus a second fuse when the poses change. Up to 200 frames are used; the rest follow by interpolation.
- **Not done:** the laser planes and the camera-IMU time offset as unknowns (self-calibration), the IMU term. Not tested on real recordings.

## Degeneracy-aware tracking (M3 phase 3, 2026-11-09)

The tracker now reports which motions the surface can't pin down, `info["weak"]`: "slide" (a flat area) and "turn" (a sphere's axis, a flat area's normal). The live app turns them into hints. Two ways from `docs/RESEARCH_PLAN.md` (4.3.1, 4.3.2) to also *act* on weak directions were tried and dropped, because they made tracking worse.

**Setup:** GPU depth and GPU tracking model, realistic rendering. The scans:
- 6 short car-panel scans (24–36 frames, 640x400 and 1280x800);
- 9 short sphere-on-plane scans;
- 3 car-panel sweeps (180 frames, 12 s, two passes back and forth, 640x400).

Maximum trajectory error against the truth, mm:

| Scans | Gyro | Tracker as shipped | Remap weak directions | Stable sampling |
|---|---|---|---|---|
| Car panel, 6 short | on | 0.26–1.62 | 0.24–3.91 | 0.39–2.55 |
| | off | 0.59–3.44 | 17.3–82.5 | 0.84–12.4 |
| Sphere on plane, 9 short | on | 0.17–0.35 | 0.28–22.5 | 0.19–0.34 |
| | off | 24.4–132 | 19.9–81.7 | 50.5–302 |
| Car-panel sweeps, 3 × 12 s | on | 1.9 / 3.0 / 5.8 | 57 / 13 / 48 | 4.2 / 4.6 / 5.0 |
| | off | 3.3 / 3.0 / 7.0 | 131 / 50 / 432 | 4.3 / 5.2 / 7.4 |

- **Remapping** (Zhang, Kaess and Singh 2016): ICP never moves along weak directions, so they stay at the prediction. It was tried with a threshold that catches the panel's weakest directions (an eigenvalue ratio of 1e-3; the panel's is 1e-4 to 6e-4). That throws away the little, correct information the crease and the hole edge give, and the errors of the gyro and of the constant-velocity model build up instead, worst on the sweeps' turnarounds. The shipped tracker's soft priors blend the two. At the shipped threshold (5e-5), remapping would only touch the sphere's unobservable turn, which the gyro prior already holds.
- **Geometrically stable sampling** (Gelfand et al. 2003, a vectorised and balanced variant, from a grid twice as dense): the points that say the most about weak directions sit at grazing angles (a sphere's rim, the flanks of a crease), where stereo depth is least reliable. Leaving out points more than 60° from the view ray didn't fix it. It may pay off once points carry a depth confidence (phase 3, item 2).

**The weak-direction hints** use the existing `degenerate_eig_ratio` (5e-5):
- **Car-panel scans:** nothing is flagged (their weakest ratios are 1e-4 to 6e-4).
- **Sphere scans:** "turn" is flagged in 87–100 % of frames, about the axis through the sphere. With the gyro that needs no hint (0.17–0.35 mm); without it the hint shows, and those scans drift 24–132 mm.
- **A flat area with nothing else in view** gives "slide" and "turn" (`tests/test_track_markerless.py`). The slide hint shows even with the gyro, which holds rotations only.

## Depth confidence (M3 phase 3, 2026-11-09)

Each depth pixel now has a standard deviation, and each mesh vertex one too. Fusion still averages every frame equally: weighting frames by their sigma made the mesh worse.

**Per-pixel sigma** (`DepthEstimator.compute_with_sigma`, `SigmaModel` in `ohs/vision/depth.py`):
- σ_d comes from the Lucas-Kanade residual of the sub-pixel refinement, with a floor and terms for nearby holes, grazing incidence and the near end of the range. σ_z = z²·σ_d / (f·B).
- Fitted per backend on 12 simulated recordings with true depth (bracket, car panel, sphere on plane; full and half resolution; two seeds each).

| Backend | 2σ covers the true depth error | Per recording | Extra time at full resolution |
|---|---|---|---|
| GPU (Census SGM, OpenCL) | 96.7 % | 92.1–99.2 % | about 4–5 ms (the sigma model runs as kernels) |
| CPU | 95.9 % | 90.3–99.1 % | about 43 ms |

**Reconstruction** (`ConfidenceTsdf`, `ohs/track/recon_tsdf.py`, the default with a GPU):
- With equal weights it averages like Open3D: on the same frames its mesh has 72,404 vertices against Open3D's 72,402, in the same places apart from the rim of the scanned area (`tests/test_track_recon_tsdf.py`).
- **Weighting by 1/σ²** (Curless and Levoy 1996; Nguyen et al. 2012) **made the mesh worse**: the median error rose 15–25 % and more stray surface was left. A gentler weight, flat up to a knee, gained nothing. Both were dropped.
- On the GPU it integrates a frame in about 20–28 ms at full resolution (Open3D on the CPU: 16–19 ms). Its numpy twin takes about 0.5 s, so without a GPU the default stays Open3D, with no confidence.

**Per-vertex sigma** ("honest averaging", research plan 7.10):
- Each voxel keeps the variance of what it averaged and counts only *independent looks*. A frame counts as a new look only once the voxel's pixel has moved by the dot spacing (10 px at 1280 px) since the last one; nearby frames see the same dots, so their errors are correlated. σ² = max(Σw²σ² / (Σw)² · n, observed scatter) / looks.
- A scanner held still gets one look however many frames it takes, so its sigma stays that of one frame (tests).
- On the simulated scans, 2σ covers the true surface error for 94.9–100 % of the vertices.
- The sigma finds the worse parts. On the car-panel scan, the vertices with σ ≤ 1 mm have a median error of 0.070 mm and a p95 of 0.30 mm; over all vertices it is 0.085 and 0.47 mm.

**In the app:** "Confidence" shading in the viewer and the Process page, with a legend and the share of the surface within 0.1 / 0.3 mm. The viewer also shows the sigma under the cursor. Each vertex's sigma follows every mesh edit (`ohs.mesh.carry_sigma`; filled holes are "not measured") and is written to `mesh.ply` as the vertex property `quality`.

## Refine with lasers (M3 phase 3, 2026-11-10)

The laser lines are triangulated on their calibrated planes, independently of the dense stereo. A new Process stage pulls the fused surface onto them where the two disagree (`ohs.mesh.anchor_to_points`, research plan 4.2.4). A per-frame check also drops laser points that disagree with that frame's dense depth (4.2.3).

**Setup:** the car panel and the sphere on a plane, 1280x800, fused at the true poses, so only depth errors count. Errors are signed distances to the analytic surfaces, for the vertices within 3 mm of a laser point. The laser points themselves are 0.044 mm (car) and 0.053 mm (sphere) from the truth (median; p95 0.22 and 0.28 mm).

Median error near the laser lines, before → after:

| Dense depth | Car panel | Sphere on a plane |
|---|---|---|
| As simulated (no bias) | 0.0359 → 0.0359 mm | 0.0423 → 0.0426 mm |
| Biased: 0.1 % scale and a 0.3 mm bowl, fixed to the camera | 0.236 → 0.046 mm (mean −0.242 → −0.023) | 0.262 → 0.064 mm (mean −0.258 → −0.028) |
| A 0.3 mm wave of 40 mm wavelength on the mesh | 0.117 → 0.097 mm | 0.117 → 0.096 mm |

- **What it fixes:** smooth biases of the dense depth, the kind calibration errors and real optics leave. A 40 mm wave is near its limit (the correction is smooth on a 5 mm scale).
- **What it can't fix on the simulator:** the fused surface is already as accurate as the laser points (0.036–0.042 mm against 0.044–0.053 mm), so there is nothing to gain without a bias. Each frame's line also has correlated errors of about 0.05–0.08 mm (its plane, its centre biases), and following them made the unbiased sphere 19 % worse. So corrections under 0.1 mm are not applied; from 0.2 mm they apply in full.
- **Where the lasers swept:** only there. On these short scans 7–13 % of the vertices lie within 3 mm of a laser point; elsewhere the mesh keeps its bias, and the correction fades out within about 15 mm.
- **The solver:** the plan's least-squares form with a bi-Laplacian took 12 s and did not converge on 500,000 vertices. Normalized convolution by heat diffusion on the mesh takes about 0.8 s, with three rounds of robust re-weighting (Tukey).
- **In the app** (a 449-frame simulated scan, 65,000 laser points): as simulated, the stage says the mesh already agrees (0.08 mm median difference) and changes nothing, so the edits stay. With its track mesh pushed 0.4 mm off, it pulled 9.7 % of the surface back: the median distance to the lines fell from 0.41 to 0.07 mm. The stage only changes the mesh when at least 1 % of it would move (unbiased scans: 0.4–0.7 %).
- **The per-frame check** keeps a laser point within 4 sigma (at least 1 mm) of the dense depth at its pixel or 6 px to either side. Reading only the point's own pixel dropped 0.3–3.3 % of the true points, almost all on the horizontal line: it runs along the epipolar rows, so the matcher has nothing to hold on to there. With the side samples it drops 0.00–0.12 % (4 clean scans). The simulator has no glossy reflections yet (research plan 6.2), so what it catches on real parts is untested.

## Refine from the images: spacetime stereo trial (M3 phase 3, 2026-11-10)

A trial build of research plan 7.2 (`ohs.track.spacetime`). Small patches of the fused surface are searched along their normals for where the left and right images of every frame agree, and the vertices move onto the patches. Each frame lights a patch with a different arrangement of dots.

**Setup:** four 36-frame simulated scans at 1280x800 with the realistic lens: the sphere on a plane and the car panel, two seeds each. Track and fuse run as in the app (tracked poses, 0.06–0.13 mm median pose error), then the stage runs with its defaults. Errors are distances to the analytic surfaces, for the vertices it refined. "Fit only" moves the vertices onto the same patches without the image search: what a local surface fit alone does.

Median / p95 error, and the share off by more than 0.2 mm:

| Scan | Region | Fused | Fit only | Refined from the images |
|---|---|---|---|---|
| Sphere, seed 0 | sphere | 0.081 / 0.276 mm, 12.4 % | 0.066 / 0.175 mm, 3.2 % | **0.044 / 0.129 mm, 0.9 %** |
| | table | 0.044 / 0.149 mm, 1.6 % | 0.024 / 0.093 mm, 0.7 % | **0.019 / 0.072 mm, 0.1 %** |
| Sphere, seed 1 | sphere | 0.062 / 0.281 mm, 10.1 % | 0.042 / 0.232 mm, 6.4 % | **0.021 / 0.084 mm, 0.1 %** |
| | table | 0.050 / 0.161 mm, 2.1 % | 0.028 / 0.097 mm, 0.5 % | **0.026 / 0.089 mm, 0.2 %** |
| Car panel, seed 0 | panel | 0.043 / 0.148 mm, 1.6 % | 0.028 / 0.113 mm, 0.7 % | **0.023 / 0.093 mm, 0.7 %** |
| Car panel, seed 1 | panel | 0.046 / 0.152 mm, 2.1 % | 0.026 / 0.112 mm, 1.6 % | **0.022 / 0.078 mm, 0.5 %** |

- **What it measured:** 45 and 60 % of the sphere scans' surface, 25 and 19 % of the car panel's. A patch needs dots in at least 16 frames. The rest of the surface keeps the fused mesh.
- **The whole sphere**, measured or not: median 0.088 → 0.048 mm and 0.071 → 0.025 mm. The whole panel: 0.043 → 0.024 mm and 0.047 → 0.023 mm.
- **At the true poses** (seed 0, fused at them too) the sphere goes 0.071 → 0.015 mm (p95 0.337 → 0.097; fit only 0.035), 4.7 times, as the prototype found. The panel goes 0.036 → 0.012 mm (fit only 0.013). So with tracked poses the tracking error is now the limit. (On the same tracked mesh, the true poses give 0.017 mm.)
- **Refining the poses against the images, first try: no gain.** Each frame's pose was fitted to where that frame alone put the refined patches (its own left-right cost minimum), along the measurable directions, then the patches were searched again. The poses became more consistent: the error after a best rigid fit fell from 0.051 to 0.027 mm and from 0.133 to 0.046°. The shape didn't improve: 0.037 → 0.038 mm after a rigid fit to the truth, against 0.019 mm with the true poses. Smoothing the corrections over time didn't help either. A frame's own offsets carry its camera-fixed matching error, which a per-frame fit turns back into pose error (research plan 7.2).
- **Where the gain comes from:** on the sphere, mostly the images (fit only gets a third of it). On the panel and the table, mostly the fit: the fused mesh at 1 mm voxels is noisy from vertex to vertex, and a smooth patch over 7 mm removes that. The panel's simulated paint shows the dots with little contrast, and most of its cost curves are flat.
- **Around the panel** (its unmodelled edges and the stand, labelled "floor" by the analytic check) the result is worse than the fit alone (0.072 → 0.051 mm, fit only 0.037). The simulator can't say whether that is real.
- **Speed** (Radeon 680M, with other work running): 11–18 s for 36 frames, of which the GPU search is 2–5 s. Placing the patches, finding which frames see them (ray casts on the CPU) and moving the vertices take the rest. A 449-frame scan in the app (120 frames read): about 25 s, 55 % of the surface measured. The CPU twin of the search does about 20,000 patch-frame-offset comparisons a second, so a short scan takes about 20 minutes without a GPU.
- **What didn't work, on the way:** patches sized in mm (4 mm is 4 px at 640x400, too small to match); a quadric fitted to the 2.5 mm the patch covers (its curvature is noise); testing for texture at every offset (frames dropping in and out made sharp false minima); and a smooth correction field instead of moving the vertices onto the patches (the patches improved, the mesh barely did). Exact bilinear rectification instead of OpenCV's 1/32-pixel remap changed nothing.
- **Not covered:** real speckle and MJPEG, glossy paint, the bracket's sharp edges, real recordings. The vertex sigmas are kept as they were: the curvature of the cost did not predict the error, so the stage doesn't claim a lower sigma yet.

## Measurement-safe edits (M3 phase 3, 2026-11-10)

**Simplify to a tolerance** (`decimate(..., max_error_mm=)`). The deviation is how far each vertex of the mesh before lies from the surface after, checked against every face nearby:

| Mesh | Tolerance | Triangles kept | 99.9 % within | Largest |
|---|---|---|---|---|
| 449-frame scan, 853,000 triangles | 0.01 mm | 72 % | 0.007 mm | 0.017 mm |
| | 0.02 mm | 55 % | 0.016 mm | 0.038 mm |
| | 0.05 mm | 29 % | 0.045 mm | 0.089 mm |
| | 0.1 mm | 15 % | 0.089 mm | 0.14 mm |
| The same, holes up to 30 mm filled | 0.02 mm | 55 % | 0.016 mm | 0.062 mm |
| Car panel, short scan | 0.02 / 0.05 mm | 66 / 44 % | 0.017 / 0.051 mm | 0.057 / 0.109 mm |
| Sphere on a plane, short scan | 0.02 / 0.05 mm | 60 / 34 % | 0.015 / 0.046 mm | 0.061 / 0.129 mm |

- The first version bounded only the quadric's mean distance; its largest deviation was about 1 mm whatever the tolerance. That came from straightening the ragged outer edge, and from flattening folds where a vertex's faces point every way: a fill patch lying back to back with a scan face, or the fold where the sphere meets the table. Both are now held.
- About 8 s for 850,000 triangles, plus about 6 s to measure the deviation that the edit reports.

**Smoothing within 2σ** (`smooth(..., limit=2 * sigma)`), checked on the fused car panel and sphere, error against the analytic surface (median, low-σ half / high-σ half):

| | Car panel | Sphere on a plane |
|---|---|---|
| Before | 0.054 / 0.137 mm | 0.045 / 0.100 mm |
| Bilateral 50 %, plain or within 2σ (the same) | 0.042 / 0.119 mm | 0.036 / 0.089 mm |
| Taubin 50 %, plain or within 2σ (the same) | 0.047 / 0.127 mm | 0.040 / 0.094 mm |

The limit never binds here: smoothing moves points by hundredths of a millimetre, while these short scans' σ is about 0.5 mm. Smoothing doesn't hurt the confident half on the simulator either. The limit is a guarantee for scans where σ is small, not a gain on these.

**Filled faces** are flagged by `fill_holes`. The flags follow every edit (carried exactly by delete, keep and smooth; by nearest face for floaters and simplify) and reach `mesh.ply`; tests cover it.

**Watertight:** `fill_holes(..., include_borders=True)` closes the outer edge within the size limit, for example a sphere cut flat where it sat on the table.

## What the metrics mean

- **Stereo errors** compare the estimated raw intrinsics/extrinsics (`K1`, `K2`, `R`, `T`) with
  the simulator's truth. The **row error** is how well the rectification matches the truth. Random
  3D points (200-450 mm) are projected into the *true* raw images, mapped through the *estimated*
  undistortion and rectification, and the vertical disparity `|v_left - v_right|` is measured.
  Stereo matching assumes it is zero. The **depth scale error** triangulates those points with the
  estimated rectified geometry.
- **Laser planes**: the angle between the estimated and true plane normals, and the offset error
  `d_est - d_true` (mm at the camera origin). The **laser-point error** is what matters in use.
  Rays through a pixel grid of the camera are intersected with the true and the estimated plane,
  over depths of 150-450 mm. The table gives the median and maximum distance between the two hits;
  the maximum occurs at far, grazing rays. With the estimated stereo calibration, planes are
  compared in the raw left-camera frame (`n_raw = R1^T n`), because the two calibrations define
  slightly different rectified frames.
- **IMU**: `R_ci` angle error (in the raw camera frame for the estimated-stereo case), the time
  offset error and `|bias_est - bias_true|`.
- **Trajectory error**: `pose_error(inv(T_gt[f]) @ T_gt[i], T_est[i])` after aligning the world at
  the first tracked frame `f`. The world is the camera at that frame, where `T_est = I`. The table
  gives the rotation (deg) and translation (mm) of the relative error, with the median and maximum
  over all frames.
- **Mesh / laser accuracy**: `mesh.ply` vertices and the voxel-averaged `laser_points.ply` (0.2 mm)
  are mapped into the simulator world with that same first-frame alignment. Their distance to the
  analytic surfaces is measured: sphere + table plane, or the car-panel height field + floor. Each
  point counts against its nearest surface. Drift is therefore *included*, and nothing is fitted.
  For (g) only the known rotation between the estimated and the true rectified frame
  (`R1_true R1_est^T`, 0.008 deg here) is compensated, because it changes how the frame is defined,
  not the shape. The mesh p95 comes mostly from TSDF artefacts at grazing angles, at occlusion
  edges and at the 450 mm depth cut-off.

## Findings and fixes

1. **Camera-fixed depth errors break frame-to-model tracking. This was fixed in the pipeline.**
   - **What went wrong:** with the trackers wired as specified, the sphere scan drifted 22 deg / 56 mm over 36
     frames. The car panel drifted 6.5 deg / 24 mm.
   - **Diagnosis:** the same tracker on ground-truth depth gave 0.08 deg / 0.2 mm, so the tracker
     itself was fine. The SGBM disparity error has a dot-scale component (std about 0.3 px) that is
     *fixed to the camera*. The projected dots stay at nearly the same image positions from frame to
     frame, and the error correlates about 0.4 between frames 0.1 s apart. In experiments that added
     parts of the measured SGBM error to the true depth, this high-pass part alone reproduced the
     drift. The low-frequency part (0.18 deg) and uncorrelated noise of the same size (0.45 deg) did
     not. The TSDF keeps the pattern, and ICP locks onto it: the plane, about 95 % of the pixels,
     pulls the pose back toward the previous camera. With the rotation held by the gyro, ICP
     translates the camera about 7 mm per step to re-align the patterns. The 0.1 deg default gyro
     prior was also too weak; a calibrated gyro is good to about 0.01 deg per frame.
   - **Fix (`ohs.app.pipeline`):**
     - ICP runs against a *separate tracking TSDF* (2 mm voxels).
     - That TSDF is fed with a filtered depth (`tracking_depth`): edge-aware normalised Gaussian
       smoothing with sigma 6 px at full resolution (4 px at half). The valid area is eroded by
       2 sigma, so that no camera-fixed boundary artefacts remain.
     - The gyro prior is set to 0.02 deg per frame.
     - The raw depth is fused at the tracked poses into the 1 mm reconstruction TSDF, so the mesh
       keeps full detail.
     - Cost: about 45 ms per frame at full resolution.
     - `PipelineParams(track_filter_px=0)` restores the original single-model behaviour, shown in
       row (f) for comparison.
2. **The laser stripe is biased where it crosses ink, so laser calibration uses the white margin.**
   - **The problem:** on the board, stripe centres are pulled toward white squares. Board-region
     planes are tilted by 0.04-0.08 deg and offset by up to 0.26 mm, and laser points move by
     0.16-0.20 mm median and up to 1.7 mm.
   - **The fix:** the blank paper around the printed pattern is coplanar with the board, and using
     it cuts this about tenfold (row b). `ohs.calib.laser_planes` gained a
     `region="board"|"margin"|"both"` option on `make_observation`, `board_points_from_line`,
     `fit_laser_planes` and `check_laser_planes`. The low-level default stays `"board"` for backward
     compatibility; `calib_flow.lasers_from_recording` defaults to `"margin"`.
   - **The margin band:** taken from the actual printed layout (`calib.board.charuco_page_layout`).
     It keeps 5 mm from the pattern, the scale bar, the label text and the paper edge. The clearance
     was varied from 2 to 8 mm with no change in residuals (0.02 mm rms).
   - **Fallback:** a laser that cannot be fitted from the margin alone is refitted on board + margin,
     and the report says so.
3. **The simulator's calibration page did not match the printed page. The printed page is the
   source of truth, so the simulator was fixed.**
   - **The disagreement:** the simulator centred the board on a blank letter page (27.95 mm above
     and below). `render_charuco_page` prints it 18.4 mm from the top, with the scale bar and two
     lines of text below.
   - **Detection was already fine:** the textures were pixel-identical (cv2.aruco defaults,
     non-legacy pattern), and `detect_charuco` finds all 70 corners on simulated views with 0.05 px
     median error.
   - **The fix:** `sim.scene.charuco_board` now renders the printed page raster, with the board area
     unchanged to within 1e-4 albedo.
4. **Mesh extraction from a TSDF with fewer than 4 fused frames raised inside Open3D.** Open3D keeps
   only voxels whose weight is strictly above the threshold, and the threshold was capped at the frame
   count. This is fixed in `track.markerless` (one line), and the pipeline also guards against it.
5. **Marker mode** is less accurate than markerless + IMU on this scene. In most frames only 2-3 of
   the six 20 mm tags are seen in stereo, and their corners are degraded by the dots and laser lines
   (reprojection rms 0.3-0.55 px). Typical error is 0.9 mm / 0.2 deg. The worst frame is a one-tag
   PnP fallback at 3.8 mm / 0.9 deg. The table plane in the mesh blurs accordingly (median 0.7 mm).
   - `refine_map()` runs at the end and its map is saved (`marker_map.json`), but the model keeps the
     poses tracked online, because `refine_map` re-estimates poses by Kabsch alone and has no public
     per-frame output.
   - At 640x400 with the dots on, tags mostly fail. They fail cleanly (`ok=False`), as documented.

## Simulation limitations

- **Surfaces:** Lambertian only. There is no specular reflection, no interreflection and no
  subsurface scattering. Subsurface scattering in plastics, skin and paint broadens and biases real
  laser stripes, and shiny metal saturates.
- **Imaging:** no motion blur and no rolling shutter. The OV9281 has a global shutter, but exposure
  time still blurs at hand speed. The stereo pair is perfectly synchronised. Frames are lossless PNG,
  whereas the real camera streams MJPEG, whose artefacts on dots and laser lines are not modelled.
- **Optics:** the lens is exactly Brown-Conrady (k1, k2, p1, p2); defocus and blur are simple
  Gaussians. Laser sheets are perfect planes with a Gaussian profile; real line generators have a
  slight "smile" and are not perfectly planar. The dot pattern is an ideal pinhole projection.
- **Calibration board:** perfectly flat, printed at exact scale, with no glare from the glass and
  the margins intact.
- **IMU:** white noise, a random-walk bias and a constant time offset. There are no scale-factor,
  axis-misalignment, temperature or USB-latency variations, and the clock sync is near ideal.
- **Scenes and motion:** analytic primitives with low-contrast paint noise and no clutter. Scans are
  short (1.2 s, about 25 deg of sweep), and smooth with a small tremor.
  - Loop closure now has tests on simulated orbits and sweeps of 180-720 frames (see "Straighten" above).
  - Fast or jerky motion is still untested.
- **Tuning on the simulator:** the parameters were tuned on this same simulator: the tracking filter
  (sigma 6 px, 2 sigma erosion), the 0.02 deg gyro prior and the 5 mm margin clearance. Real depth
  errors may have a different structure, so re-check them on hardware recordings.
  `track_filter_px` and `fusion.gyro_sigma_deg` are the parameters to revisit first.

## Known limitations of the pipeline

- Full-resolution processing takes about 0.35 s per frame on this CPU, so it is offline only. The
  live half-resolution pipeline runs at about 90-100 ms per frame (about 10 fps), with two TSDFs
  in memory.
- Without an IMU, symmetric or featureless scenes drift: the yaw about a sphere's axis and sliding
  on a plane are unobservable. The tracker flags this as `degenerate` but cannot fix it.
- The laser margin needs the board printed by `render_charuco_page` on the paper given to
  `lasers_from_recording(paper=...)`, with its margins intact. A trimmed page falls back to the
  board region, which is less accurate.
- Mesh p95 is 0.7-1.2 mm, from TSDF artefacts at grazing angles, depth edges and the depth-range
  cut-off. The laser cloud, at 0.07 mm median, is the more accurate surface sample. It is exported
  separately and is not yet used to refine the mesh.
