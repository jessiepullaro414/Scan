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
- Drift beyond about 20 mm can't be closed, because there is no global registration yet.

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
