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

## Large and detailed parts (simulator, 2026-11-10)

Two real parts' 3D scans, rendered by the simulator's new `TriangleMesh` object (Open3D's Embree ray caster) with the usual dots, lasers and noise. Both were painted with low-contrast paint noise and set on a dark floor. They were processed as the Studio does (Make mesh, then Straighten with its laser crossings) and compared with the true mesh.
- **Door trim panel**, 946 × 868 × 135 mm, 0.49 M triangles: the Thunk3D ArcherS car door scan on Sketchfab (CC BY).
  - 1,410 frames (47 s): five overlapping passes along the panel, 150 mm apart.
- **Carburetor**, 186 × 186 × 196 mm, 0.5 M triangles: "Carburetor – Artec 3D Scanner" by Artec Group (CC BY 3.0).
  - 1,080 frames: two orbits looking down 30–65°.
- The models stay out of the repository; `ohs.sim.scene.mesh_on_floor` loads any OBJ, PLY, STL or glTF file.

**The door works.**

| | Door trim panel |
|---|---|
| Frames tracked | 1,410 of 1,410 |
| Trajectory error vs truth | median 0.34 mm, max 0.82 mm, 0.04° |
| Mesh vertices vs the true panel | median 0.07 mm, 92 % within 0.5 mm, p95 0.79 mm |
| Seen surface reconstructed | 99.5 % within 1 mm |
| Straighten | 25 loops, already within 0.12 mm: unchanged |
| Laser crossings | 1,661, within the lasers' scatter: unchanged |
| Time on the Radeon PC | track and fuse 280 s, Straighten 12 s, crossings 26 s |

![Door error map](docs/images/sim_door_error_map.png)

The larger errors are where structured light always struggles:
- the speaker grille's slots, which the matcher bridges;
- thin skirts where the panel's edge meets the floor.

A metre of panel, scanned in five passes, did not drift.

**The carburetor exposed a tracking weakness: the tracking filter erases detailed parts.** It is now fixed (below).
- Only 596 of 1,080 frames tracked, and the trajectory slid away by up to 1.7 m, while the gyro kept the rotation right (0.09°).
- The tracking filter keeps only 3–9 % of the carburetor's depth pixels, against 52–78 % in the raw depth. It erodes 12 px around every depth step, and on a part like this every pixel is near one.
- What is left is the floor, a plane, which cannot hold the scanner's sideways motion.

The machined bracket preset suffered too, only less visibly. The trial below used 90 frames, median / max trajectory error in mm:

| Tracking input | Carburetor (300 frames) | Bracket | Car panel | Sphere | Door (400 frames) |
|---|---|---|---|---|---|
| Filtered (before) | lost: 1,190 mm by frame 336 | 1.01 / 6.90 | **0.10 / 0.31** | **0.13 / 0.22** | **0.18 / 0.34** |
| Raw depth, single model | 1.75 / 2.32 | | | | |
| Filtered, raw where the filter dropped it, whole scan | **1.48 / 1.86** | **0.36 / 0.95** | 0.16 / 0.52 | 0.24 / 0.44 | 0.16 / 0.51 |
| Raw only in frames that keep < 50 % | 2.01 / 3.75 | 0.27 / 2.31 | | 0.13 / 0.22 | 0.18 / 0.34 |
| **Shipped: decided at the start of the scan** | **1.48 / 1.86** | **0.36 / 0.95** | **0.10 / 0.31** | **0.13 / 0.22** | **0.18 / 0.34** |

**The fix: decide at the start of the scan whether the filtered depth can hold the pose.**
- **What decides it.** Each ICP now measures how well its input holds the weakest translation while the gyro holds the rotation: the translation block's smallest eigenvalue over the problem's largest (`info["trans_strength"]`).
  - With a filtered-only model, smooth parts hold 1e-3 to 1e-2: sphere p10 1.2e-3, car panel 2.6e-3, door 4.8e-3.
  - The bracket holds 1.1e-5 to 1.9e-5 and the carburetor 2.5e-5 to 3.8e-5.
- **The probe.** For the first 10 frames of a scan a second tracker runs on the filtered depth plus the raw depth wherever the filter dropped a pixel.
  - If the filtered tracker holds less than 5e-4 in any of those frames, the second tracker takes over for the whole scan, consistent from frame 0 (`detail_mode`). The report gives `detailed_part_from_frame`.
  - The threshold is above the 2e-4 a grown model needs, because a one-frame model flatters the filtered depth: the carburetor read 3.0e-4, 2.1e-4, then 1.3e-4 and falling, while smooth parts never dropped below 1.1e-3.
  - The carburetor and the bracket switch at frame 1. The sphere, car panel and door never switch, so their results are bit-identical to before.
- **What did not work**, in the order tried:
  - **Switching frame by frame** whenever the tracker's own "slide" flag or the strength said so: the carburetor still slid 0.7 m, and the bracket's median doubled. Once the raw frames put the part into the model, the filter's leftover fragments of it look well held, but they are biased.
  - **Switching for good after 2 weak frames in 5:** this deadlocked (5 of 300 frames tracked). The model didn't contain the part yet, so frames with the part failed the fitness gate and were never fused. Fusing them at the filtered pose freed it, but the slid frames left the model smeared (carburetor 49 mm).
  - **Raw depth only far (> 16–24 px) from the filter's surviving pixels:** a total failure, with 1 of 300 carburetor frames tracked.
  - **The filter's kept share, or the strength computed from the input's own normals:** neither separates the scenes. The floor inflates the share, and the dots' noise inflates self-normal constraints.
- **What is left of the per-frame idea:** a frame that fails a gate tries the raw-filled depth before it is lost. Weak frames are not retried: on the door that fired on 4.6 % of the frames (views mostly of floor) and made the worst frame worse (0.82 → 1.21 mm).
- **Not covered:**
  - A scan that starts on a smooth part and only later reaches a detailed one stays on the filtered depth. Start such scans on the detailed part.
  - On the CPU path, with no GPU (SGBM depth, Open3D tracking model), the probe misses the carburetor. Open3D's young model has noisy normals, which flatter the filtered depth (2e-3 to 9e-3 over the first 11 frames). The bracket is still caught at frame 3, and smooth parts stay above 2e-3. Detailed parts need a GPU for now.

**The full carburetor scan with the fix** (1,080 frames, processed as above):

| | Carburetor |
|---|---|
| Frames tracked | 1,080 of 1,080 (was 596) |
| Trajectory error vs truth | median 1.72 mm, max 3.22 mm; rotation median 0.33°, max 0.61° |
| Mesh vertices vs the true part | median 0.80 mm, p95 2.7 mm, p99 4.4 mm |
| Seen surface reconstructed | 72 % within 1 mm, 97 % within 2 mm |
| Straighten | 40 loops, already within 0.17 mm: unchanged |
| Laser crossings | 604, within the lasers' scatter: unchanged |

![Carburetor error map](docs/images/sim_carb_error_map.png)

- The carburetor is still the hardest case. Its trajectory error is 5× the door's, its rotation error 18×, and its edges carry stereo skirts (yellow). The raw depth's camera-fixed dot error is what the filter was there to remove, and here it can't be.
- **Speed:** the probe costs a second tracker for 10 frames, +5–6 ms on the 90-frame bench's mean on the Radeon. Medians are unchanged within noise (probe on / off in the same run: full 155.6 / 153.5 ms, preview 66.4 / 63.9 ms). Over a real scan it amortises to under 1 %.

## The waits a user feels (2026-11-10)

Maintenance plan task F1. The Radeon 680M column is `ohs bench --frames 90 --quality both --device gpu`, three runs on the idle PC at 6e1c102; a range is the three runs. The RTX 3070 Ti column comes from the round 2 bench above (2b1eae0); the tail and the engine start have not been measured there yet. The targets were agreed with the owner on 2026-11-10.

| What the user feels | Radeon 680M | RTX 3070 Ti | Target |
|---|---|---|---|
| Live scanning (preview quality) | 15.1–15.3 fps | 39.2 fps | at least 15 fps on integrated graphics, 30 fps on a gaming GPU |
| Processing after the scan, full quality | 6.2–6.3 fps (158–162 ms a frame) | 20.4 fps (49.1 ms) | |
| → wait per minute of scanning, full quality | 9.5–9.7 min | about 2.9 min | at most 3 min on integrated graphics, 1 min on a gaming GPU |
| → the same at preview quality | 3.9–4.0 min | about 1.5 min | |
| Tail after the last frame, full quality (3 s scan, 1.09 M faces) | 1.8–1.9 s | not measured | |
| → the same at preview quality (0.25 M faces) | 0.33–0.37 s | not measured | |
| Engine start, new process to first status message | 1.9–2.0 s | not measured | |

- **Where it stands:** live scanning meets its target on both machines, on the Radeon only just. The wait after a scan misses its target by a factor of about 3 on both.
- **The wait per minute** is the bench's mean frame time × 3,600 frames (60 fps, every frame processed). It leaves out reading and decoding the frames. In the frame-step runs of the next section, `process_recording` as a whole took 199 s for the carburetor scan (1,080 frames) and 256 s for the door (1,410 frames), against 177 s and 222 s of frame time. That is about 19 ms more per frame and 3–6 s at the end, so the real wait is about 13 % above the bench's figure: about 11 min per minute of scanning on the Radeon.
- **The tail** at full quality: mesh extraction 0.56–0.59 s, cleaning 0.72–0.76 s, PLY 0.05–0.07 s, STL 0.46–0.47 s, laser points file 0.01 s. It grows with the scan. Not covered: the app's own step after processing, which reads the mesh back into the project and shows it.
- **The engine start:** the port accepts a connection after 1.1–1.2 s, and the app's "hello" message follows at once. The first status message comes about 0.8 s later. That time is in the engine, not in the connection (the WebSocket handshake takes 0.00–0.02 s). The 0.8 s start figure in the maintenance plan was taken differently and did not include Python's own start.

## Processing fewer frames after a scan: measurement (2026-11-10)

Maintenance plan task F2, step 1: measure only, no code change. Full quality on the Radeon 680M GPU path, through `process_recording` with the app's settings, every run twice.

- **What `frame_step` does:** with step s, every s-th recorded frame is processed. Skipped frames are never decoded, and every per-frame stage (rectify, laser extraction, depth, tracking, fusion) runs only on the processed frames. The laser profiles of skipped frames are dropped with them. All IMU samples are kept.
- **Real step:** the device records 60 frames a second, the simulated scans in this document 30. At the same hand speed, a 30 fps recording at step s shows the tracker the same motion per frame as a 60 fps recording at step 2s. Checked by simulating the sphere and the panel scan a second time at 60 fps with the same hand motion: 30 fps step 1 against 60 fps step 2 gives a mesh median of 0.0654 against 0.0665 mm (sphere) and 0.0886 against 0.0871 mm (panel). "Real step" below is the step on the 60 fps device.

Recordings at 60 fps (72 frames; the bracket 180):

| Scan | Real step | Wait per minute of scanning | Trajectory median / max, mm | Mesh median / p95, mm | Floor noise, mm |
|---|---|---|---|---|---|
| Sphere | 1 | 9.7–9.8 min | 0.111 / 0.185 | 0.0565 / 0.287 | 0.074 |
| | 2 | 5.1 min | 0.096 / 0.144 | 0.0665 / 0.309 | 0.091 |
| | 3 | 3.5 min | 0.090 / 0.158 | 0.0752 / 0.322 | 0.107 |
| | 4 | 2.7–2.9 min | 0.093 / 0.124 | 0.0832 / 0.338 | 0.122 |
| Car panel | 1 | 9.6–9.8 min | 0.087 / 0.324 | 0.0716 / 0.425 | 0.116 |
| | 2 | 5.0–5.1 min | 0.084 / 0.332 | 0.0871 / 0.456 | 0.156 |
| | 3 | 3.5 min | 0.113 / 0.266 | 0.1004 / 0.488 | 0.189 |
| | 4 | 2.7–2.9 min | 0.106 / 0.271 | 0.1094 / 0.509 | 0.211 |
| Bracket | 1 | 10.0–10.2 min | 0.479 / 0.889 | 0.0944 / 0.420 | 0.080 |
| | 2 | 5.1 min | 0.203 / 0.581 | 0.0902 / 0.384 | 0.088 |
| | 3 | 3.5 min | 0.304 / 0.446 | 0.0918 / 0.400 | 0.100 |
| | 4 | 2.6 min | 0.531 / 0.823 | 0.0916 / 0.412 | 0.108 |

The two long scans (30 fps recordings, so steps 1–4 are real steps 2–8):

| Scan | Real step | Wait per minute, frames only / whole run | Trajectory median / max, mm | Part median / p95, mm | Complete within 1 mm | Floor noise, mm |
|---|---|---|---|---|---|---|
| Carburetor (1,080 frames) | 2 | 4.9 / 5.5 min | 1.720 / 3.216 | 0.802 / 2.722 | 71.6 % | 0.070 |
| | 4 | 2.5 / 2.8 min | 1.576 / 2.766 | 0.746 / 2.490 | 75.1 % | 0.088 |
| | 6 | 1.7 / 1.9 min | 1.603 / 2.606 | 0.739 / 2.386 | 75.0 % | 0.099 |
| | 8 | 1.3 / 1.5 min | 1.566 / 2.395 | 0.717 / 2.280 | 75.2 % | 0.111 |
| Door (1,410 frames) | 2 | 4.7 / 5.4 min | 0.336 / 0.816 | 0.070 / 0.785 | 99.5 % | 0.081 |
| | 4 | 2.4 / 2.8 min | 0.263 / 0.720 | 0.075 / 0.683 | 99.4 % | 0.100 |
| | 6 | 1.6 / 1.9 min | 0.223 / 0.957 | 0.066 / 0.632 | 99.4 % | 0.111 |
| | 8 | 1.2 / 1.5 min | 0.213 / 0.859 | 0.069 / 0.579 | 99.3 % | 0.124 |

- **Time:** the wait falls almost exactly as 1 / step. The time per processed frame rises by 1–4 % at step 4 on the long scans (carburetor 163.5 → 167.8 ms, door 157.4 → 164.0 ms) and by 12–25 % on the scans of 36–72 frames, where a few frames carry the fixed costs of the start.
- **Trajectory:** holds up to real step 8. On the long scans it improves, because drift adds up over fewer steps (carburetor: median 1.72 → 1.57 mm, rotation 0.33 → 0.19°).
- **Large parts:** the error against the part does not get worse (carburetor median 0.80 → 0.72 mm; door median unchanged, p95 0.79 → 0.58 mm).
- **Smooth surfaces get noisier.** On the sphere and the panel the mesh median rises by 18–22 % at real step 2 and by 47–53 % at real step 4; the floor noise rises by 23–34 % at real step 2. Fewer frames average less noise away. The noise grows more slowly than 1 / √frames would give (× 1.23 at half the frames, not × 1.41).
- **Spread:** the GPU path repeats exactly: the trajectories of the two runs are byte-identical, and the mesh files differ only in the order of the vertices. The same hand motion recorded twice (30 fps and 60 fps, same real step) agrees within 2 % on the sphere and the panel. The rise in noise is far beyond that. The bracket is the exception: its trajectory error jumps between steps without a trend (0.48 / 0.20 / 0.30 / 0.53 mm), and its two recordings are 30–60 % apart, so it speaks neither for nor against.
- **Result under the plan's stop rule:** the mesh error and the noise rise beyond the spread, so the default stays at 1 on this evidence.
- **What this says about the figures above:** the accuracy validated in this document comes from 30 fps recordings, which is real step 2. Processing every frame of a 60 fps scan is 15–18 % better than that on smooth surfaces (mesh median 0.0565 against 0.0665 mm on the sphere, 0.0716 against 0.0871 mm on the panel). Real step 2 would give the accuracy validated here and halve the wait, from about 9.8 to about 5 min per minute of scanning on the Radeon. Which of the two becomes the default is the owner's decision (F2 step 3).
- **Floor noise** is this section's own measure: floor vertices at least 10 mm from the object, in 5 mm cells that all four steps of a recording cover, a plane fit per cell, 1.4826 × the median absolute deviation. It compares steps of one recording, not recordings with each other.
- **Motion per recorded frame** in the 30 fps scans: 1.7–3.4 mm and 0.17–0.82° median (carburetor at most 4.96 mm and 1.08°). That is a hand speed of about 50–100 mm/s and 5–25°/s: a calm hand.
- **Not measured:** fast or jerky hand motion and motion blur; Straighten and the laser-curve adjustment with fewer laser crossings (the laser points fall as 1 / step: carburetor 210 k → 55 k at real step 8); marker mode; the CPU path; the RTX laptop; a real IMU, whose gyro prior of 0.02° per processed frame is not widened for a longer gap between frames.

## Picking frames by motion against a fixed step: measurement (2026-11-10)

Maintenance plan task F2, step 2. The setting `process.frame_motion_deg` lets the gyro choose the frames: a frame is processed when the hand has turned by that angle since the last processed frame, or when `process.frame_max_gap` recorded frames have passed. It is off by default. Measured at code b890475, full quality on the Radeon 680M GPU path, through `process_recording` with the app's settings, one run each (the GPU path repeats exactly: six reruns of rows from the section above gave byte-identical trajectories). Scoring and floor noise as in the section above; the floor noise compares rows of one recording only.

**Steady hand (the 60 fps recordings above, cap 4).** The hand turns at a near-constant rate, so an angle picks the very frames of a fixed step: sphere 0.5° and bracket 0.5° pick the frames of step 2, sphere 0.9° and bracket 1.0° those of step 3, panel 0.4° those of step 2. Same frames, same result. Only the panel at a third of the frames differs:

| Scan | Setting | Frames | Trajectory median / max, mm | Mesh median / p95, mm | Floor noise, mm |
|---|---|---|---|---|---|
| Car panel | step 3 | 24 | 0.113 / 0.266 | 0.1004 / 0.488 | 0.1885 |
| | 0.7°, cap 4 | 24 | 0.117 / 0.351 | 0.1001 / 0.495 | 0.1890 |

**The two long scans (30 fps recordings; a gap of n frames is 2n on the 60 fps device):**

| Scan | Setting | Frames (by angle / by cap) | Trajectory median / max, mm | Mesh median / p95, mm | Part median / p95, mm | Complete within 1 mm | Floor noise, mm |
|---|---|---|---|---|---|---|---|
| Carburetor | step 2 | 540 | 1.576 / 2.766 | 0.284 / 1.193 | 0.746 / 2.490 | 75.1 % | 0.0896 |
| | 1.0°, cap 4 | 540 (539 / 0) | 1.562 / 2.777 | 0.297 / 1.196 | 0.737 / 2.484 | 75.5 % | 0.0898 |
| | 1.0°, cap 2 | 559 (508 / 50) | 1.606 / 2.953 | 0.293 / 1.198 | 0.747 / 2.496 | 74.8 % | 0.0885 |
| Door | step 2 | 705 | 0.263 / 0.720 | 0.109 / 0.551 | 0.075 / 0.683 | 99.43 % | 0.1007 |
| | 0.27°, cap 4 | 708 (677 / 30) | 0.259 / 0.787 | 0.094 / 0.512 | 0.072 / 0.661 | 99.44 % | 0.1035 |
| | 0.35°, cap 2 | 723 (348 / 374) | 0.271 / 0.711 | 0.112 / 0.550 | 0.077 / 0.682 | 99.43 % | 0.0993 |

**A hand that changes speed.** Two new 60 fps recordings of 270 frames (sphere and car panel) with the usual hand path on a warped clock: slow for 1 s, fast for 0.9 s (frames 78–131), slow again, a hover of 0.4 s, slow. Median speed and turn rate of the camera: slow 40–65 mm/s and 10–30°/s; fast 241 mm/s and 77°/s on the sphere (peak 356 mm/s, 94°/s), 154 mm/s and 28°/s on the panel (peak 294 mm/s, 39°/s); hover 2 mm/s. The angles were chosen to land on the frame counts of steps 2, 3 and 4; the count of step 4 needs cap 8.

| Scan | Setting | Frames | In the fast part | Trajectory median / max, mm | Drift across the fast part, mm | Mesh median / p95, mm | Floor noise, mm |
|---|---|---|---|---|---|---|---|
| Sphere | every frame | 270 | 54 | 0.240 / 0.394 | 0.327 | 0.0538 / 0.317 | 0.0642 |
| | step 2 | 135 | 27 | 0.201 / 0.353 | 0.122 | 0.0606 / 0.333 | 0.0783 |
| | step 2, one frame later | 135 | 27 | 0.154 / 0.354 | 0.345 | 0.0612 / 0.321 | 0.0779 |
| | 1.0°, cap 4 | 138 | 54 | 0.182 / 0.398 | 0.322 | 0.0593 / 0.333 | 0.0718 |
| | step 3 | 90 | 18 | 0.222 / 0.419 | 0.423 | 0.0664 / 0.335 | 0.0909 |
| | 1.6°, cap 4 | 87 | 27 | 0.206 / 0.410 | 0.345 | 0.0678 / 0.347 | 0.0901 |
| | step 4 | 68 | 13 | 0.217 / 0.367 | 0.328 | 0.0712 / 0.341 | 0.1011 |
| | 2.0°, cap 8 | 72 | 27 | 0.179 / 0.351 | 0.115 | 0.0705 / 0.358 | 0.0947 |
| Car panel | every frame | 270 | 54 | 0.138 / 0.448 | 0.273 | 0.0651 / 0.396 | 0.0963 |
| | step 2 | 135 | 27 | 0.164 / 0.419 | 0.190 | 0.0791 / 0.414 | 0.1231 |
| | step 2, one frame later | 135 | 27 | 0.169 / 0.429 | 0.158 | 0.0924 / 0.429 | 0.1217 |
| | 0.4°, cap 4 | 135 | 46 | 0.129 / 0.397 | 0.165 | 0.0743 / 0.404 | 0.1198 |
| | step 3 | 90 | 18 | 0.267 / 0.440 | 0.159 | 0.0883 / 0.421 | 0.1425 |
| | 0.7°, cap 4 | 88 | 26 | 0.119 / 0.291 | 0.351 | 0.0863 / 0.426 | 0.1433 |
| | step 4 | 68 | 13 | 0.172 / 0.476 | 0.281 | 0.0978 / 0.436 | 0.1585 |
| | 0.8°, cap 8 | 72 | 24 | 0.102 / 0.324 | 0.047 | 0.0917 / 0.437 | 0.1549 |

- **Tracking and time:** no run lost a frame, with either way of choosing. A processed frame costs the same either way (159–172 ms), so the wait depends on the number of frames only. Choosing the frames takes 0.2–0.5 ms per recorded frame.
- **How much the choice of frames alone moves the figures:** the rows "step 2, one frame later" process the other half of the frames. At the same count the mesh median moves by up to 17 % (panel 0.0791 against 0.0924 mm) and the trajectory median by up to 25 % (sphere 0.201 against 0.154 mm); the floor noise by about 1 %. One such control per recording, so this is a rough yardstick.
- **Same number of frames, steady hand: no measurable difference.** Mostly it is the same frames. On the long scans the part error, the completeness and the floor noise agree within about 3 %; the door's lower mesh median at 0.27° is inside the yardstick above.
- **A sliding hand:** the door turns too little for a normal angle. At 1.5°, cap 4 every pick but one is by the cap, which is exactly fixed step 4. The cap gives what a fixed step gives, nothing more.
- **A hand that changes speed:** the fast part gets 1.7–2 times as many frames as with a fixed step. The trajectory median is lower in all six pairs (sphere −7 to −18 %, inside the yardstick; panel −21 to −55 %, beyond it, with every error under 0.5 mm). The drift across the fast part goes both ways. The floor noise at the same count is 1–8 % lower on the sphere and within 3 % on the panel, partly because those runs have a few more frames. A small advantage that two recordings do not prove. It saved no scan here, because no fixed step failed.
- **It does not bring the noise back.** At half to a quarter of the frames the floor noise is 12–60 % above every frame, as in the section above. Fewer frames average less, however they are chosen.
- **An angle is not a fraction of the frames.** 0.5°, cap 4 keeps half the frames of the steady sphere scan, 174 of 270 of the sphere scan with the changing hand and 111 of 270 of the panel one (counts from the picker; those two were not processed). The wait then depends on how the hand moved.
- **Checked in review:** the panel rows "step 3" and "0.7°, cap 4" of the last table were run a second time on their own: byte-identical trajectories. The frame counts were recomputed with a separate script.
- **Not measured:** a real IMU (the simulated gyro starts with the calibrated bias and exact timing, so this is the best case for the angle); motion blur; a hand faster or jerkier than about 350 mm/s and 90°/s; Straighten and the laser stages with frames picked by motion; marker mode; the CPU path; the RTX laptop.

## Standard and Best quality (2026-11-10)

The owner's decision after the two measurements above (maintenance plan F2 step 3): the app offers two levels of the final mesh. **Standard**, the default, processes at most 30 frames per second of scanning (`[process] standard_frame_rate`): every 2nd frame of the 60 fps camera, every frame of a 30 fps recording. **Best** processes every frame. Picking frames by motion stays a setting, off.

- **What the figures in this file are:** the validation scans are 30 fps recordings, so every figure here outside the two sections above is what Standard gives on the 60 fps device, and none of them changes. Best is the "step 1" row of "Processing fewer frames after a scan": on smooth surfaces a mesh median 15–18 % lower, at about twice the wait.
- **The wait:** computed from the bench, not timed on a real scan. Standard needs 30 processed frames per second of scanning: about 4.8 times the scan's duration on the Radeon 680M (half of the 9.5–9.7 in "The waits a user feels") and about 1.5 times on the RTX 3070 Ti laptop (20.4 frames a second). Best is twice that. The targets in "The waits a user feels" (3 times and 1 time) are closer and not met yet.
- **Checked in review:** on the 60 fps recording `panel_vary_60` (270 frames) Standard processed 135 frames and its trajectory is byte-identical to the `frame_step = 2` run of the section above; Best processed 270 and is byte-identical to `frame_step = 1`; the old quality name "full" gives the same as Standard. The engine snapshot (30 fps recordings, 90 frames, GPU path) is identical to the one before the change.
- **Not measured:** a recording from the real camera. The frame rate is taken from the median time between the frames; jitter and dropped frames are covered by tests on made-up timestamps only.

## A narrower stereo search per frame: tried, not adopted (2026-11-10)

Maintenance plan F3, the simple form: one disparity range per frame, from the tracking model's last ray cast and the previous frame's depth plus 15 mm, with a full-range frame at the start, after a lost frame and every 10 frames. The plan's stop rule hit (a bench gain under 10 %), so the code is not in the engine; it is kept on the branch `experiment/f3-narrow-depth-search` for reference.

Bench on the Radeon 680M (GPU path, 90 frames, Standard, 1280×800), five pairs of runs off / on, three by the subagent that built it and two repeated in review:

| | Off | On | Change |
|---|---|---|---|
| Matcher, mean per frame | 78.8–84.5 ms | 72.6–75.7 ms | −5 to −14 % |
| Whole frame, mean | 159.0–163.9 ms | 152.6–156.4 ms | −2 to −7 % |
| Whole frame, median | 151.9–164.6 ms | 144.4–147.4 ms | −4 to −12 % |
| Wait per minute of scanning (every frame) | 9.54–9.81 min | 9.16–9.38 min | |
| Disparities searched, mean | 208 | 172.3 (10 of 90 frames at full range) | −17 % |

- **Why it does not pay:** in the simulated scans the depth in view runs from about 180 mm to the 450 mm limit in every frame, because the floor recedes behind the part. One range per frame has to cover all of it: with the margin about 163 to 486 mm. And the matcher's time does not fall in step with the range: 31 % fewer disparities gave 26 % less matcher time, since census, the sub-pixel step and the speckle filter do not depend on it.
- **Accuracy with it on:** within the 2 % yardstick of this file. The largest change was the trajectory median, +1.4 % (sphere and car panel at 1280×800); the mesh median moved by less than 0.1 %. These are the builder's runs; in review only the bench trajectory was repeated (median 0.1263 → 0.1257 mm).
- **A weakness found on the way:** a surface outside the narrowed range is not left without depth. Both matchers find a wrong match inside the range for 11 to 33 % of its pixels (planes, a 32-disparity range), 30 to 100 mm off, and the uniqueness and left-right checks let it through. Any later attempt at a narrowed search (a range per pixel) has to deal with this first.
- **GPU:** the disparity count and minimum are fixed when the OpenCL program is built (1.3 to 1.5 s per build on the 680M). The experiment makes the minimum a kernel argument and keeps one cached program per count.
- **Not measured:** the RTX laptop; the CPU path's timing; long scans and real parts against a far background, where the range could be narrower; a range per pixel.

## Where a frame's time goes (profile, 2026-11-10)

A profile of the frame after F2 to F4, to pick the next speed step. Radeon 680M, GPU path, Standard, the 90-frame bench (`ohs bench`), 1280×800. Measured with throwaway scripts that wrap functions with wall-clock timers (not kept in the repo); the engine's code was not changed. Python's cProfile gave wrong figures with the laser threads running and is not used here.

Stages, mean per frame over six runs (the bench's own timers):

| Stage | ms | What runs |
|---|---|---|
| Rectify | 2.3–2.4 | CPU |
| Depth (stereo matcher) | 79–82 | GPU; the main thread waits |
| Wait for the two laser threads | 16–17 | the threads start before the depth and should be done when it is |
| Tracking and fusion | 56–58 | ray cast 16, ICP 7.5–8, tracking filter 6–7, fusion 10, model update 1.8 |
| Laser points | 4–5 | CPU |
| **Whole frame** | **159–161** | median 152–155 |

Device time of the GPU work, from OpenCL's profiling events:

| Kernel | ms per run |
|---|---|
| `sgm_horizontal` | 32.0 |
| `sgm_vertical` | 23.7 |
| `census` | 6.3 |
| `lk_prep` | 1.4 |
| `sigma_z` | 1.0 |
| the 11 other matcher kernels together | 5.2 |
| **Matcher, sum** | **69.7** |
| Volume: ray cast / integrate / upload | 3.6 / 3.7 / 0.5 |

- **The matcher is GPU-bound:** 70 of its 79–82 ms are device time, and 56 of those are the two SGM kernels.
- **Tracking is mostly host time:** the volume's kernels take about 8 ms of device time per frame, the stage takes 56–58 ms. `MarkerlessTracker.track` is 37.7 ms per frame: finishing the ray cast 17.6 (7.0 of it waiting for the device), ICP 9.6, source points 2.8, cleaning the depth 1.9. The pipeline's tracking filter is 6.8 and the fusion 10.7.
- **The first frames cost more:** `_probe_track` took 78 ms per call, in the first 13 frames only. Over 90 frames that is 11 ms per frame; over a real scan of thousands of frames it is nothing. The bench overstates tracking by about that much.
- **Reading the recording is not in the bench's frame:** `cv2.imread` takes 16–17 ms per frame, one frame at a time with nothing read ahead. In Make mesh that is about a tenth of the wait per frame (the job on the 355-frame simulator scan ran at 4.8–5.4 frames a second, 185–208 ms per frame, against the bench's 160).
- **The mesh at the end:** 570 ms for 1.09 million triangles (436 ms of it the conversion to Open3D), clean-up 758 ms, STL export 442 ms. Once per job.

### The laser threads wait for Python's lock while the matcher runs

Laser extraction alone takes 13.9 ms for one image and 20.6 ms for two at once. In a frame each of the two threads needed 92 ms, of which about 21 ms was its own CPU time. `cv2.sepFilter2D` looked like 35 ms per call from Python; alone it takes 1.7–1.9 ms. The rest is waiting for Python's lock (the GIL) after the call returns.

The cause is in `GpuStereoMatcher._run` (`ohs/vision/depth_gpu.py`). Its OpenCL calls take 0.08 ms (kernel enqueues) + 0.5 ms (non-blocking copies) + 3.5 ms (the blocking copy at the end) of its 82 ms. The other 78 ms are spent in the first output copy: it is non-blocking, pyopencl returns an event for it that keeps the host array alive, the code drops that event at once, and pyopencl's clean-up of a dropped copy event waits for the copy to finish **without releasing the lock**. So the main thread holds the lock for nearly the whole matcher run, and the laser threads only get going when it is over.

Experiment: the events of the matcher's non-blocking copies kept alive until its blocking copy has returned (patched in from outside, the engine's code unchanged). Two pairs of bench runs:

| | Off | On | Change |
|---|---|---|---|
| Whole frame, mean | 159.5 / 160.4 ms | 153.3 / 152.9 ms | −3.9 % / −4.7 % |
| Whole frame, median | 151.6 / 151.7 ms | 144.9 / 145.4 ms | −4.4 % / −4.1 % |
| Wait for the laser threads | 16.2 / 16.0 ms | 0.7 / 1.3 ms | |
| Laser threads, wall time summed | 183.8 / 186.3 ms | 62.7 / 68.3 ms | |
| Depth | 79.4 / 80.2 ms | 88.0 / 87.2 ms | +8 to +9 ms |
| Wait per minute of scanning (every frame) | 9.57 / 9.62 min | 9.20 / 9.17 min | |

- **Same results in both modes:** trajectory median 0.126297 mm, depth error median 0.314194 mm, 1,088,827 triangles, 90 of 90 frames tracked, to the last digit printed.
- **The depth stage gets slower by 8 to 9 ms** once the laser threads really run beside it. Why is not looked into (the integrated GPU shares its power budget and memory with the CPU; the main thread also has to get the lock back). The frame still gains 6 to 7 ms.
- **The same pattern elsewhere:** `ohs/track/filter_gpu.py` and `ohs/track/recon_tsdf.py` also drop the events of non-blocking uploads. Whether those waits matter is not measured.
- **Not measured:** the snapshot guard on the patched run; the CPU path (it has no matcher on the GPU); the RTX laptop; live scanning, where the app's server threads are held up by the same lock for about 78 ms in every frame.

**Built on 2026-11-11 (plan F5):** the matcher now keeps those events itself. Old and new code, runs alternated (new, old, new, old, new):

| | Old | New |
|---|---|---|
| Whole frame, mean | 160.8 / 161.4 ms | 152.7 / 151.4 / 151.9 ms |
| Whole frame, median | 152.7 / 154.7 ms | 146.3 / 145.7 / 145.6 ms |
| Wait for the laser threads | 16.3 / 16.4 ms | 1.4 / 1.2 / 0.9 ms |
| Depth | 80.9 / 81.6 ms | 86.4 / 85.7 / 87.0 ms |
| Wait per minute of scanning (every frame) | 9.65 / 9.68 min | 9.16 / 9.08 / 9.11 min |

The mean frame is 5 to 6 % shorter. The accuracy figures are the same in all five runs, and the snapshot (`tools/snapshot.py`, 90 frames, preview and full) is identical to the one from before.

**Built on 2026-11-11 (plan F6):** the frame pass behind Make mesh now gets its images from a thread that reads up to two frames ahead (`RecordingReader.iter_ahead`). Timed on a real pass, not the bench: `process_recording` with default settings on the first 150 frames of a 355-frame simulator recording on disk (1280x800, Radeon 680M, GPU path), the time taken between the first and the last frame's progress call, old and new runs alternated (new, old, new, old, new, old):

| | Old | New |
|---|---|---|
| Time per frame, mean | 169.0 / 172.9 / 167.5 ms | 149.7 / 152.1 / 151.2 ms |
| Time per frame, median | 164.3 / 168.6 / 161.0 ms | 147.7 / 149.3 / 148.7 ms |
| Whole pass with its mesh | 28.5 / 29.0 / 28.3 s | 25.7 / 26.0 / 25.8 s |

That is 17 to 21 ms less per frame, about 11 %. All 150 frames are tracked and the mesh has 426,639 vertices and 845,817 faces in all six runs; the snapshot (`tools/snapshot.py`, 90 frames, preview and full) is identical to the one from before. Not measured: the RTX laptop, a recording on a slow disk, the CPU path. The later passes (`ohs/track/posegraph.py`, `spacetime.py`, `curve_ba.py`) still read their frames one at a time.

**Built on 2026-11-11 (plan F7): the tracker's time on the host.** A line-by-line timing of the frame pass on the same recording (335 frames timed, 149.9 ms per frame: depth 88.4, tracking with fusion 53.2, laser 4.2, rectify 2.5) shows where the tracker's 53 ms go:

| Part of tracking, per frame | Time | Of it, waiting for the device |
|---|---|---|
| Model view (ray cast) | 14.8 ms | 5.5 ms |
| Fusion into the reconstruction | 9.4 ms | 5.4 ms |
| ICP | 8.5 ms | none |
| Tracking filter | 5.8 ms | 4.6 ms |
| Counting the valid pixels (pipeline and tracker) | 3.7 ms | none |
| Source points | 2.7 ms | none |
| Fusion into the tracking volume | 2.4 ms | 1.7 ms |
| Depth range check, gyro prior, the rest | about 6 ms | none |

So about 17 ms are waits, and the host work that could go without changing a result was in two places. The model view built vertices and normals for all 1,024,000 pixels (8.2 ms) though ICP reads them only where a source point lands: it now builds them at those pixels, with the same arithmetic. And `np.count_nonzero` on a float32 depth map is slow: timed alone on a 1280x800 map, 3.6 ms where counting `depth != 0` takes 0.26 ms and gives the same number. Same measurement as for F6, old and new runs alternated (old, new, old, new, old, new):

| | Old | New |
|---|---|---|
| Time per frame, mean | 150.3 / 149.4 / 151.3 ms | 141.2 / 140.9 / 143.7 ms |
| Time per frame, median | 147.1 / 146.8 / 148.7 ms | 138.5 / 138.4 / 140.2 ms |
| Whole pass with its mesh | 25.7 / 25.5 / 25.9 s | 24.7 / 24.2 / 24.6 s |

That is 8 to 9 ms less per frame, about 6 %. All 150 frames are tracked and the mesh has 426,639 vertices and 845,817 faces in all six runs; the snapshot (`tools/snapshot.py`, 90 frames, preview and full) is identical to the one from before. Not measured: the RTX laptop, the CPU path (its model view comes from Open3D and is unchanged). What is left of the tracker is mostly waiting and the ICP's own arithmetic; the waits can be used for other work (noted under task E5 in `docs/MAINTENANCE_PLAN.md`).

**Built on 2026-11-11 (plan E4): the fusion no longer waits for the device.** The reconstruction volume's `integrate` ended with a wait for the GPU (5.4 ms per frame in the timing above), only to keep two small host arrays alive. It now returns as soon as the fusion is queued: the two arrays are kept until the next frame, and whatever reads or releases the volume waits first. The pass then goes on with the laser points and the next frame's rectification while the device works. A first version that still waited for the two small uploads gained nothing (140.0 / 141.5 / 140.3 ms per frame against 140.9 / 140.6 / 141.1), which shows what the wait was: the device was busy with the tracking volume's fusion, queued just before on the same GPU. Same measurement as for F7, old and new runs alternated:

| | Old | New |
|---|---|---|
| Time per frame, mean | 142.8 / 141.8 / 140.9 ms | 136.5 / 138.7 / 137.7 ms |
| Time per frame, median | 139.9 / 138.3 / 138.3 ms | 134.5 / 136.5 / 135.1 ms |
| Whole pass with its mesh | 25.1 / 24.3 / 24.3 s | 23.6 / 23.9 / 23.7 s |

That is about 4 ms less per frame, 3 %. All 150 frames are tracked and the mesh has 426,639 vertices and 845,817 faces in all six runs; the snapshot is identical to the one from before. A new test fuses frames with and without a wait after each and compares the volumes bit for bit, with the pool growing, a reset and a close straight after a frame. The fusion time the app reports per frame is now the time to queue it, not the time the device takes. Not measured: the RTX laptop.

**Built on 2026-11-11 (plan E5, first part): the laser points are computed beside the tracking.** The laser points of a frame need its depth and its laser lines, not its pose. They are now triangulated in one of the two laser threads while the frame is tracked and fused, and collected afterwards; the cloud and the count of rejected points are still updated on the main thread in the same order. Same measurement, old and new runs alternated:

| | Old | New |
|---|---|---|
| Time per frame, mean | 137.0 / 137.0 / 137.4 ms | 134.3 / 133.9 / 133.4 ms |
| Time per frame, median | 134.5 / 134.6 / 134.9 ms | 131.6 / 131.3 / 131.9 ms |
| Whole pass with its mesh | 23.7 / 23.9 / 23.6 s | 23.5 / 23.2 / 23.0 s |

That is about 3.3 ms less per frame, 2.4 %. All 150 frames are tracked and the mesh has 426,639 vertices and 845,817 faces in all six runs; the snapshot is identical. A new test, passing on the old code too, compares each frame's laser points, the cloud and the rejected counts with the direct computation, bit for bit. In the per-frame timings `laser_triangulate` is now the wait that is left after tracking, and `laser_triangulate_cpu` the work's own time. Not measured: the CPU path and the RTX laptop.

**Built on 2026-11-11 (plan E5, second part): the tracking filter reads the depth on the GPU and starts early.** The matcher's depth map was downloaded and then uploaded again for the tracking filter (4 MB), and the pass waited for the filter before doing anything else. The filter now reads the map from the matcher's own buffer on the device (the same values: the downloaded array is a copy of it), starts as soon as the depth is there, and is collected when the frame is tracked; the device filters while the host works out the gyro prior. Same measurement, old and new runs alternated:

| | Old | New |
|---|---|---|
| Time per frame, mean | 135.1 / 135.6 / 134.1 ms | 131.5 / 131.2 / 132.3 ms |
| Time per frame, median | 132.1 / 133.5 / 132.3 ms | 130.1 / 128.8 / 130.3 ms |
| Whole pass with its mesh | 23.4 / 23.4 / 23.6 s | 22.8 / 22.8 / 23.1 s |

That is about 3.2 ms less per frame, 2.4 %. All 150 frames are tracked and the mesh has 426,639 vertices and 845,817 faces in all six runs; the snapshot is identical. Two new tests: the filter started ahead, from an array or from a device buffer, gives the map of a plain call bit for bit; and what the tracker gets for a frame is the filter of that frame's depth map bit for bit (this one passes on the old code too). The buffer is used only where the device divides exactly, so that the depth on it is the depth the pipeline returns; elsewhere the map is uploaded as before, but the filter still starts early. In the per-frame timings `track_filter` is now the wait that is left. Not measured: the RTX laptop.

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

(The held-out test described here, half the crossings at random, was the default until 2026-11-21. Since then whole frames are held out: see "Straighten's laser crossings on what Make mesh runs".)

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

**Per-vertex sigma** ("honest averaging", research plan 7.10). **Replaced on 2026-11-13** by the model of "The vertex sigma" further down; the rule and the numbers in this list are the first version's (it was 4 to 5.5 times too large on flat surface and too small at edges):
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

**Pieces that hang on by a vertex** (2026-11-13). A fused scan has lone triangles and small flaps that touch the surface at a single vertex: 1,710 lone triangles on 12 meshes, and on each Standard scan (1.4–1.7 million triangles) 58–101 such pieces, the largest 173 triangles and 17 mm long. With pieces counted by shared vertices they were part of the surface, and Remove floaters left 11–19 lone triangles and 340–870 faces of flaps on each scan. Against the true surface (four scans fused with the true poses) the surface is 0.03–0.04 mm off in the median and 0.20–0.27 mm at 90 %; these pieces are 0.18–0.33 mm off in the median and 0.8–1.0 mm at 90 %: the noisy fringe. Remove floaters now counts a piece as faces joined through shared edges, so each flap is judged by its own size. With the Default recipe's limits (5 mm, 1 % of the triangles) 4,714 more faces go on the 12 meshes (0.03 % of a scan), no face stays that went before, and no lone triangle is left. The edit takes about 1.1 s on 1.7 million triangles (0.45 s before).

**Filled faces** are flagged by `fill_holes`. The flags follow every edit (carried exactly by delete, keep and smooth; by nearest face for floaters and simplify) and reach `mesh.ply`; tests cover it.

**Watertight:** `fill_holes(..., include_borders=True)` closes the outer edge within the size limit, for example a sphere cut flat where it sat on the table.

## Fitted shapes against the truth (M5 first slice, 2026-11-12)

One click in the viewer fits a plane, a cylinder or a hole (`ohs.measure.fit_at`) and shows a ± value. This checks those numbers on simulated scans of parts with known shapes.

**In short:**
- **The ± values are too small, mostly because of systematic errors.** They describe the scatter of the points around the fit. The scan's shapes are also biased (stereo, fusion, tracking), the same way on every seed, and the ± does not know that.
- **No single factor repairs it.** For ±2σ to cover the truth 95 % of the time, the ± would have to grow by 1 to 5 times at true poses and by 5 to 60 times with tracking, depending on the quantity.
- **With tracking, a Ø 14 mm boss reads 0.4 mm large and a Ø 16 mm shaft 1.1 mm large**, shown as ± 0.06 to 0.08 mm.
- **The Ø 25 mm hole in the car panel cannot be fitted at all:** the stereo depth fills the hole in, and the fused opening is Ø 12 to 14 mm.

**Setup:**
- Scenes: `bracket` (240 frames, a 400° orbit at 200–280 mm) and `car_panel` (200 frames, a 400° orbit centred on its hole), 1280x800, realistic lenses and noise, dots and lasers on, seeds 0–4.
- Processed as the app's Make mesh at Standard quality on the GPU path, then the Default recipe's edits (remove floaters, fill holes up to 5 mm). Meshes of 1.3–1.7 M faces; the median vertex sigma is 0.41–0.45 mm.
- Two pose sources: the true poses (fusion and fitting only), and real tracking (what a user gets; all frames tracked, trajectory error 0.1–1.25 mm median). Straighten changed none of the ten scans.
- Per shape, 3 clicks on the true surface, so 15 fits per shape and pose source. The tracked meshes are aligned to the truth by a best fit first, so position errors are after that alignment.

**At true poses** (error median / worst; ± is the median 1σ shown):

| Shape | Quantity | Error | ± shown | ±2σ covers |
|---|---|---|---|---|
| Boss, Ø 14 (two of them) | diameter | −0.08 to −0.12 / −0.22 mm | 0.06 mm | 47–67 % |
| | axis direction | 5.6° / 10.3° | 1.2° | 0 % |
| | axis position | 0.2 / 0.25 mm | 0.05 mm | 0 % |
| Shaft, Ø 16 | diameter | +0.21 / +0.55 mm | 0.07 mm | 33 % |
| | axis direction | 0.15° / 0.54° | 0.19° | 73 % |
| | axis position | 0.17 / 0.35 mm | 0.05 mm | 7 % |
| Table | normal | 0.0003° / 0.0009° | 0.0002° | 73 % |
| | offset | 1.7 / 2.3 µm | 0.3 µm | 0 % |
| Base top | normal | 0.026° / 0.051° | 0.021° | 100 % |
| | offset | 0.013 / 0.016 mm | 0.005 mm | 27 % |
| Upright face | normal | 0.08° / 0.23° | 0.05° | 60 % |
| | offset | 0.065 / 0.100 mm | 0.010 mm | 0 % |

**With tracking:**

| Shape | Quantity | Error | ± shown | ±2σ covers |
|---|---|---|---|---|
| Boss, Ø 14 | diameter | +0.36 to +0.39 / +0.61 mm | 0.06 mm | 0 % |
| | axis direction | 7.7–8.5° / 13.8° | 1.1° | 7 % |
| | axis position | 0.29–0.37 / 0.79 mm | 0.05 mm | 0–7 % |
| Shaft, Ø 16 | diameter | +1.07 / +1.68 mm | 0.08 mm | 13 % |
| | axis direction | 0.45° / 1.37° | 0.23° | 47 % |
| | axis position | 0.72 / 1.09 mm | 0.05 mm | 7 % |
| Base top | offset | 0.027 / 0.037 mm | 0.005 mm | 0 % |
| Upright face | offset | 0.48 / 1.27 mm | 0.011 mm | 0 % |
| | normal | 0.16° / 1.13° | 0.06° | 33 % |

- Convex features come out 0.2–0.5 mm fatter in radius with tracking, much the same on every seed.
- On two seeds the tracked upright face sits about 1.2 mm off: frames fused with 0.7–1 mm of relative pose error distort the mesh.

**Why the ± is too small:**
- **Bias.** At true poses the spread from seed to seed is about the size of the ± (0.4–1.6 times). The rest of the error is the same on every seed: the upright face 0.06 mm off, the shaft's radius 0.15 mm large (the stereo depth fattens foreground objects), and the 8 mm boss walls wavy by ±0.3 mm (the TSDF at the corners with the plate and the cap), which tilts their axis by 5–6°.
- **Neighbouring vertices share their errors.** On the planes the true errors of vertices 1 mm apart correlate 0.5–0.6. A fit that treats them as independent is too sure by about 7–10 times.
- **The vertex sigma is 2–6 times too large**, which hides most of the second point today: 2σ covers 99–100 % of the vertices where 95 % would be right. If sigma is made honest, the fits' ± shrink and their coverage falls further.

Factor by which the ± would have to grow for ±2σ to cover 95 %:

| Quantity | True poses | Tracking |
|---|---|---|
| Radius | 2.0 | 10.9 |
| Axis direction | 3.9 | 5.0 |
| Axis position | 2.3 | 10.5 |
| Plane normal | 2.1 | 5.6 |
| Plane offset | 3.8 | 61 |

**The regions a click takes** (share of the true face taken, median and smallest, at true poses):

| Shape | Share taken | Tolerance used | True noise of the face (RMS) |
|---|---|---|---|
| Bosses | 98 % (96 %) | 1.2–1.4 mm | 0.37 mm |
| Shaft | 62 % (38 %) | 0.14 mm | 0.36 mm |
| Base top | 79 % (21 %) | 0.10 mm | 0.15 mm |
| Upright face | 64 % (28 %) | 0.16 mm | 0.26 mm |
| Table near the part | 77 % (54 %) | 0.05 mm | 0.095 mm |

- **Regions stop early on the shaft and the planes.** The tolerance comes from the seed patch, which sits on the best-measured spot, so it is below the face's own noise.
- **On the bosses the seed is larger than the 8 mm wall**, takes in the edges, and the tolerance becomes 1.2–1.8 mm.
- A click takes 0.4 s; a table plane of 330–400k points 1.1 s (worst 2.3 s).

**"Flat within":** on truly flat faces the label (largest minus smallest distance) read 0.14 mm (table), 0.32 mm (base top) and 0.48 mm (upright face), all of it error. Robust versions (the central 99 %, or 6 × RMS) are only 10–25 % smaller. The label is about 3 times the region's tolerance: the region only takes points within the tolerance, so the label mostly restates it, and on a part that really is out of flat it would cut the deviation off.

**The hole:**
- 30 clicks (5 seeds, 3 clicks, 2 pose sources): 26 answered "the faces around this hole's edge are not flat", 3 fitted a wrong loop (Ø 13.7 mm against the true 25.2), and 2 hit a bug (a filled pin hole nearer the click than the hole's edge; fixed the same day with a test).
- The fused panel follows the truth only down to a radius of 11–12 mm. Then a lip sags into the hole; the opening is Ø 11.9–13.9 mm, 0.9–2.9 mm below the panel.
- The cause is in each depth map: one camera does not see the background through the hole near its edge (it is 120 mm below, a step of about 75 px in disparity), and the stereo matcher carries the panel 1–3 mm into the hole. Fusing views from all round adds these up.
- With a block 10 mm under the hole the panel ends near the true edge, but a skirt joins it to the block, and the hole still cannot be fitted.
- So there is no hole-diameter bias to quote. The simulator needs a hole with an edge that can be measured (a sheet with thickness, or a bore with a wall).

**What follows:**
- The app must not show the ± as an accuracy. Until the biases are dealt with, it is the fit's own scatter.
- Open work, in the plans: holes filled in by the stereo depth; convex features fattened; the pessimistic vertex sigma; the region's tolerance; the flatness label.

Scripts and raw results: scratchpad `m5v\` of the session of 2026-11-12 (`summary_all_clicks.md`).

**Measured again on 2026-11-13**, with the engine as it is now: the new vertex sigma, the gap guard on, and the Fit tool's refusal of a drifted cylinder. The same ten recordings, clicks and method; 15 fits per shape and pose source (the two bosses together: 30). Error is the median of its size.

| Shape | Quantity | True poses, before → now | Tracking, before → now |
|---|---|---|---|
| Bosses, Ø 14 | radius | 0.05 → 0.16 mm (all small) | 0.18 → 0.39 mm (all large) |
| | axis direction | 5.6° → 7.5° | 8.0° → 12.5° |
| | axis position | 0.18 → 0.07 mm | 0.32 → 0.61 mm |
| | clicks answered | 30 → 26 of 30 | 30 → 27 of 30 |
| Shaft, Ø 16 | radius | 0.10 → 0.08 mm (worst 0.27 → 0.16) | 0.53 → 0.60 mm (worst 0.84 → 1.30) |
| | axis position | 0.17 → 0.03 mm | 0.72 → 0.74 mm |
| Base top | normal / offset | 0.026° → 0.010° / 0.013 → 0.004 mm | 0.045° → 0.015° / 0.027 → 0.012 mm |
| Upright face | normal / offset | 0.079° → 0.035° / 0.065 → 0.023 mm | 0.16° → 0.09° / 0.48 → 0.72 mm |
| Car-panel hole, Ø 25.2 | clicks answered | 0 → 15 of 15 | 3 (a wrong loop, Ø 13.7) → 15 of 15 |
| | diameter | Ø 24.0 (1.2 mm small, worst 1.6) | Ø 24.1 (1.1 mm small, worst 1.4) |
| | centre | 0.18 mm | 0.32 mm |

- **The hole can be measured now**, about 1.2 mm small in diameter. Its ± (0.11 mm in radius) does not cover that; the centre's ± (0.2 mm) does.
- **Planes and the shaft at true poses are better.** A plane's region also takes more of its face: the table 30 % → 91 %.
- **The bosses are worse**, and with tracking by a factor of two in radius. Three things changed for them, told apart on the same meshes:
  - *The new sigma as the fit's weight.* On meshes that are the same as before to the vertex (true poses, guard off), a boss fitted with the old sigma reads 0.05 mm small with its axis 5.6° off; with the new sigma 0.13 mm and 8.6°; with no weights at all 0.04 mm and 4.6°. The new sigma holds bias terms (curvature, border, gaps) that are large and very uneven on an 8 mm stub, so `1 / sigma²` leaves the fit to a narrow band of the wall. The shaft does not change (0.10 mm either way).
  - *The gap guard.* It takes the rim off the walls: at true poses the bosses read another 0.03–0.1 mm smaller (0.15 mm with the old sigma as weight).
  - *Refused clicks.* With the guard, 3 or 4 of 30 clicks on a boss answer "its curvature is not clear": the seed has fewer points to see the round with. Another click on the same boss is answered.
- **The ± is still too small almost everywhere**, as expected: the new sigma is smaller on flat surface and the errors are biases. Share of the fits whose ± 2σ covers the truth, before → now: a cylinder's radius 49 → 32 % (true poses) and 4 → 0 % (tracking); axis direction 24 → 39 % and 20 → 33 %; axis position 2 → 93 % and 4 → 2 %; plane normal 69 → 58 % and 20 → 31 %; plane offset 16 → 22 % and 13 → 9 %.
- **Other weights, tried on the same meshes** (as shipped, guard on), bosses, radius / axis direction / axis position:

| Weight of each point in the fit | True poses | Tracking |
|---|---|---|
| `1 / sigma²` (as it is) | 0.16 mm / 7.5° / 0.07 mm | 0.39 mm / 12.5° / 0.61 mm |
| The same for every point of the region | 0.12 mm / 3.8° / 0.25 mm | 0.22 mm / 5.4° / 0.63 mm |
| No point more than the region's median | 0.20 mm / 4.9° / 0.12 mm | 0.32 mm / 10.8° / 0.58 mm |
| Sigma kept within 2 times the region's median | 0.14 mm / 7.5° / 0.09 mm | 0.35 mm / 10.6° / 0.63 mm |

  Equal weights give the bosses back most of what they lost with tracking (and a better axis than before), and the shaft a little (0.60 → 0.49 mm). They cost the bosses' axis position at true poses (0.07 → 0.25 mm), and the planes: base top offset 0.004 → 0.007 mm (true poses) and 0.012 → 0.017 mm (tracking), the table's offset 0.002 → 0.008 mm. So a candidate is equal weights for cylinders only.

**Adopted on 2026-11-13 (the user's decision): a cylinder's fit weighs every point of its region the same**, with the region's median sigma for the ± values; planes keep `1 / sigma²`. With the engine changed, the bracket's cylinders read (before → after the change; the planes' numbers are identical):

| Shape | Quantity | True poses | Tracking |
|---|---|---|---|
| Bosses, Ø 14 | radius | 0.16 → 0.12 mm | 0.39 → 0.22 mm |
| | axis direction | 7.5° → 3.8° | 12.5° → 5.4° |
| | axis position | 0.07 → 0.25 mm | 0.61 → 0.63 mm |
| Shaft, Ø 16 | radius | 0.08 → 0.09 mm | 0.60 → 0.49 mm |
| | axis direction | 0.12° → 0.16° | 0.40° → 0.53° |
| | axis position | 0.03 → 0.04 mm | 0.74 → 0.68 mm |

Against the study of 2026-11-12 the bosses with tracking are now 0.18 → 0.22 mm in radius and 8.0° → 5.4° in axis direction; their axis position stays worse (0.32 → 0.63 mm). The shaft's ± at true poses shrinks (0.053 → 0.031 mm) and covers its radius for 20 % of the fits instead of 87 %. It rests on two bosses and one shaft of one simulated part.

**The bosses' two remaining losses, looked into on 2026-11-13** (the same meshes, gap guard on):

- **The axis position with tracking is the mesh's, not the fit's.** A circle through the vertices of the true wall, seen along the true axis (no Fit tool in it), is 0.55 mm off centre in the median of the ten walls; the Fit tool answers 0.58 mm, and within 0.1 mm of the wall's own position on each. On the meshes of 2026-11-12 (four scans) the walls were 0.48 mm off and that Fit tool said 0.52. So the tracked scan has a boss 0.4–0.6 mm from its place after the best fit of the whole scan, and the Fit tool reports it there. Two tracking runs of one recording put the same boss up to 0.3 mm apart. The gap guard adds to it: without the guard the ten walls are 0.41 mm off, with it 0.55 mm, and none of the ten is nearer.
- **At true poses the wall is 0.25 mm off centre and leans 4.4°**, the same way on every seed: a bias of the depth and the fusion on an 8 mm wall (since found: mostly the mesh's truncation; "Where a tracked scan's shape errors come from"). The Fit tool reports 0.23 mm and 4.3°. The 0.07 mm of the `1 / sigma²` weights was the fit landing nearer the truth than the wall it was fitted to.
- **The refused clicks were all the same one**: the side of the boss that faces the upright, refused at the seed with "its curvature is not clear (radius 7 ± 1 mm)". The radius was right. The ± came from the vertex sigma, 0.6–0.8 mm on a wall whose points scatter 0.2–0.3 mm.
- **Changed: a seed that bends through 45° of the round or more is judged by its scatter where the sigma is pessimistic.** On these meshes a real cylinder's seed covers 60° to 274° of its round (100 seeds), and the seed of a flat face clicked as a cylinder at most 36° of the cylinder fitted to it (114 seeds on the table, the base, the upright and the floor). Result: 59 of 60 boss clicks are answered (52 before); the new answers read like the others (bosses' radius with tracking 0.22 → 0.23 mm in the median, the worst 0.45 mm as before; at true poses unchanged). All 110 clicks on flat faces are refused as before, each for the same reason, and so are the eight meshes of the Fit tool's short-scan case. A side effect: some shaft clicks pass with their first, smaller seed, and the shaft's radius with tracking reads 0.49 → 0.52 mm in the median (the worst 1.03 mm as before). A cylinder seen over less than 45° with a pessimistic sigma is refused as before.

Scripts and rows: scratchpad `m5v2\` (`run.sh`, `compare.py`, `weights.py`, `run_var.sh`, `compare_var.py`, `boss_diag.py`, `seed_study.py`).

## The Check answer against the truth (M5 third slice, 2026-11-12)

`ohs.mesh.weak_areas` passes a face when 2 × its mean vertex sigma is within the tolerance. Measured on the 20 meshes of the study above (bracket and car panel, 5 seeds, true poses and tracking), per face and weighted by area. "Part" leaves out the table and the floor, which are 87–97 % of the surface.

**In short: the answer is safe but too pessimistic to be useful, and no single factor repairs it.**

| Surface | Tolerance | Engine says OK | Truly within | Of the passed surface, truly within |
|---|---|---|---|---|
| Bracket, whole scan | 0.3 / 0.5 / 1.0 mm | 10 / 24 / 56 % | 93–94 / 98 / 99.6–99.9 % | 100 / 100 / 99.8–100 % |
| Car panel, whole scan | 0.3 / 0.5 / 1.0 mm | 11 / 34 / 58 % | 90 / 96 / 99 % | 100 / 100 / 99.3 % |
| Bracket part, true poses | 0.3 / 0.5 / 1.0 mm | 0.1 / 4 / 39 % | 83 / 94 / 99 % | 100 / 100 / 99.4 % |
| Bracket part, tracked | 0.3 / 0.5 / 1.0 mm | 0.1 / 3 / 38 % | 60 / 76 / 90 % | 100 / 100 / 92 % (76–99 % over seeds) |
| Car panel part | 0.3 / 0.5 / 1.0 mm | 57 / 76 / 95 % | 92 / 92 / 93 % | 100 / 100 / 97 % |

- **Safe:** what it passes is practically always good, and up to 0.5 mm it flags all of the truly bad surface.
- **Not useful:** at 0.3 mm it lists 90 % of the surface as needing another pass, and only 6–11 % of that really is out.
- **At 1.0 mm it misses real errors:** 11 % (true poses) to 27 % (tracked) of the bracket's truly bad surface passes; on the panel, 42 % of the bad edge and hole-rim surface passes.

**A smaller factor** (k below 2, as if sigma were that much too large): on the whole scan k = 0.5 is the smallest that keeps "passed and truly within" at 95 % (95.6 % in the worst case, below 95 % on some seeds). On the bracket part no k below 1 works: 80–86 % at k = 0.67, 63–85 % at k = 0.5. The panel part could take k = 0.67 to 1 up to 0.3 mm.

**Why:**
- On flat surface the 95th percentile of the true error is about 0.4 × sigma: sigma is about 2.5 times too large there.
- On the bracket itself it is 0.7–1.7 × sigma: bias at edges and small features, and the pose error of tracked scans, which sigma does not know.
- At the panel's border and where its hole is filled in, faces with a sigma of 0.3–1 mm sit 3–5 mm from the truth.

**What follows:** the Check panel is not built into the app yet. It needs a sigma that is right on flat surface (about 2.5 times smaller) and a bias term near edges, small features, hole rims, borders and depth steps, and for tracking. The harness can check any new version (scratchpad `m5v\check\`, `partA_run.py`).

## The deviation map against the truth (M5 second slice, 2026-11-12)

Same meshes. Smoothing as the Process page applies it, then `ohs.mesh.surface_offset` against the mesh before.

**The Smooth tab's default (Taubin 50 %, edges kept, within 2σ), 5 seeds:**

| Scan | Map: RMS / 95 % / largest move | True error RMS, before → after | True error median |
|---|---|---|---|
| Bracket, true poses | 0.038 / 0.083 / 0.46 mm | 0.145 → 0.136 mm | 0.029 → 0.023 mm |
| Bracket, tracked | 0.033 / 0.071 / 0.51 mm | 0.187 → 0.180 mm | 0.056 → 0.053 mm |
| Car panel, true poses | 0.047 / 0.105 / 0.54 mm | 0.389 → 0.384 mm | 0.037 → 0.029 mm |
| Car panel, tracked | 0.044 / 0.096 / 0.57 mm | 0.399 → 0.394 mm | 0.075 → 0.069 mm |

- **Smoothing helps a little:** the RMS error falls 2–6 % and the median 5–20 %, on flat surface where noise dominates. Of the vertices the map shows moving by more than 0.02 mm, 58–60 % got closer to the truth and 30–36 % further away.
- **The map is right about what it shows:** its value correlates 0.91–0.92 with the real change of the error on the bracket (0.53–0.58 on the panel, where almost nothing moves).
- **Vertices mostly slide:** the mean move is 0.13–0.14 mm, of which about 0.04 mm is across the surface. The map shows only that part, which is the part that changes the shape.
- **The "within 2σ" limit does nothing today:** with a sigma of about 0.4 mm it never binds. Taubin with and without it gives the same numbers.

**Edges** (bracket, true poses): smoothing rounds them, and they are the only class that gets worse.

| Setting | Mean error on edges | Edge vertices worse by more than 0.05 mm | Worst inward move | Harmed vertices the map shows above 0.05 / 0.1 mm |
|---|---|---|---|---|
| Taubin 50 % (the default) | 0.259 → 0.279 mm | 28 % | 0.47 mm | 65 % / 24 % |
| Bracket recipe (bilateral 25 %) | 0.259 → 0.272 mm | 16.5 % | 0.18 mm | 77 % / 8 % |
| Body-panel recipe (bilateral 50 %) | 0.259 → 0.287 mm | 33 % | 0.30 mm | 87 % / 16 % |
| Bilateral 100 %, no limit | 0.259 → 0.318 mm | 51 % | 0.41 mm | 92 % / 67 % |

- The map shows the rounding (edges exceed 0.1 mm three times as often as flat surface) but undersized: its largest edge value is 0.27 mm where the worst real move is 0.47 mm.
- **At a scale limit of 0.1 mm most of the harm is hidden; at 0.05 mm most of it shows.** The legend's default limit was changed to 0.05 mm for this.
- The colour cannot tell harm from help: of the edge vertices above 0.1 mm, 74 % got worse and 21 % better.

Scripts and tables: scratchpad `m5v\check\` (`partA_tables.md`, `partB_tables.md`).

## Holes filled in: the cause, and fixes tried (2026-11-12)

A study with prototypes outside the repository. Since 2026-11-13 the guard it recommends is in the engine and **on by default** ("The gap guard in the engine" at the end of this section).

**In short:**
- **The fill is the stereo matcher's, all of it.** Its windows spread the panel's depth into a hole whose background it cannot see. The fusion adds nothing: fused from true depth, the same hole fits Ø 26.17 (truth 25.20).
- **It is not a half-occlusion effect.** The fill is on every side of the hole, so a left-right check does not remove it.
- **Fusion acts as a union.** No frame ever measures the background, so nothing clears a filled voxel; the fused hole is set by the worst frames.
- **A guard that drops depth within 8 px of a patch without depth opens a Ø 25 mm hole** to a fit of Ø 24.4–25.2, for about 1.5 ms per frame on the GPU. It costs coverage on side walls and makes convex features read smaller.
- **Small holes over a far, dark background stay closed** (Ø 6 and Ø 10 mm with the floor 120 mm below) with every fix tried.

**Setup:** simulated 1280x800 scans, Standard quality, GPU path, the Default recipe's edits, true poses unless "tracked". The car panel's hole as the engine fits it on the true surface: Ø 25.20 mm. A new scene, the plate (below), has holes with walls.

**Cause** (car panel, seeds 0 and 1, 30 frames at the true pose). Behind the hole, as a median share of the in-hole pixels: 86 % is seen by the left camera only (the 120 mm step is about 75 px of disparity, close to the hole's own width), 77 % is in the dot projector's shadow, 5 % is both lit and seen by both cameras. The background is matched correctly for 0–0.1 % of the pixels at every stage.

| Matcher stage (GPU) | In-hole pixels given the panel's depth | Without depth | Fill reach, median / 90 % / largest | Opening per frame |
|---|---|---|---|---|
| Raw best match | 55 % | 0 % | 2.30 / 6.95 / 12.2 mm | Ø 19.1 |
| + uniqueness | 40 % | 35 % | 1.61 / 4.43 / 10.8 mm | Ø 20.0 |
| + texture support | 35 % | 52 % | 1.36 / 3.16 / 7.6 mm | Ø 20.0 |
| + the existing left-right check | 35 % | 54 % | 1.35 / 3.14 / 7.6 mm | Ø 20.0 |
| Final (median, speckle, validity) | 36 % | 58 % | 1.33 / 2.93 / 5.4 mm | Ø 19.9 |
| CPU path (SGBM), final | 41 % | 32 % | 1.66 / 6.33 / 11.5 mm | Ø 20.75 |

- **Every side:** the fill's mean direction against the baseline has a cosine of −0.05, and 43–48 % of it is on the +x side. It is window fattening (census 9x7, texture support 15x15, the SGM paths) into a dark region with nothing to match. The typical reach is 1.3 mm, about 4 px at 250 mm.
- **The left-right check** rejects about 2 % of the fill. A full check against the right view's own map removes about 12 %: the right view fills its hole the same way, so the two agree.
- **The sigma flags it:** fill pixels have a median `sigma_z` of 34 mm (10 % below 4.8 mm), against 0.48 mm on the panel 1–4 mm outside the edge.
- **With a lit block 10 mm under the hole,** 85 % of the hole is matched correctly; the fill shrinks to 0.5 mm median and becomes one-sided (62–83 % on the half-occluded side). The dark, far background is the bad case.

**Fixes, fused at true poses.** Hole: the engine's fit on the car panel, seeds 0 / 1 / 2. Bracket (seed 0): mesh error of the part, radius error of the two bosses and the shaft, and the share of the side walls with a vertex within 1 mm.

| Fix | Hole fit Ø (truth 25.20) | Bracket error, median / 95 % | Bosses / shaft radius error | Side walls covered (shaft) | Cost |
|---|---|---|---|---|---|
| None | fails (opening Ø 12.5, sagging 1.45 mm) | 0.097 / 0.65 mm | −0.01 / −0.07 / +0.12 mm | 97.5 % (83 %) | |
| Full left-right check | 18.24 | 0.079 / 0.63 mm | +0.04 / −0.03 / +0.16 mm | 97.6 % (81 %) | +66 ms per frame |
| Erode 8 px, both sides of a depth edge | 24.98 / 25.04 / 24.84 | 0.065 / 0.50 mm | −0.35 / −0.41 / −0.18 mm | 86 % (58 %) | 7 % of pixels |
| **Guard: 8 px, on the side without depth only** | **24.41 / 24.54 / 24.38** | **0.066 / 0.49 mm** | **−0.14 / −0.16 / −0.12 mm** | **95.3 % (74 %)** | **1.0–1.7 ms, 5 % of pixels** |
| Drop `sigma_z` above 3 mm | 25.34 / 25.37 / 25.37 | 0.053 / 0.39 mm | −0.07 / −0.10 / −0.06 mm | 85.5 % (58 %) | 24 % of pixels |
| Carve free space in the TSDF (weight 4) | fails | 0.148 / 1.11 mm | −0.27 / −0.32 / −0.16 mm | 92 % (82 %) | |
| Stricter matcher settings (support 40 %, census 7x5) | fails | | | | |

- **The guard:** a pixel without depth whose 5x5 neighbourhood is mostly without depth too is a "patch" (single gaps between dots do not count); depth within 8 px of a patch is dropped. Its GPU kernels, a numpy twin and the CPU path agree exactly (0 differing pixels on 3 frames).
- **Erosion width alone** (seed 0): 4 / 6 / 8 / 10 px fit Ø 21.1 / 23.0 / 25.0 / 26.6. The 8 px is tuned on this simulator.
- **What the guard changes elsewhere:** the car panel keeps 99.97 % coverage and its 95 % mesh error falls from 2.31 to 0.83–0.94 mm. Bosses and the shaft read 0.09–0.24 mm smaller in radius: the shaft goes from +0.12 to −0.12 mm, so it overshoots.
- **Carving cannot work on the car panel:** there is no measured background to carve with. It thins the bracket.

**With tracking** (trajectory error median / largest; one seed each, so not conclusive):

| Scan | None | Guard in the matcher (the tracker sees it too) | Guard on the fused depth only |
|---|---|---|---|
| Car panel, seed 0 | 0.14 / 0.38 mm; hole fails | 0.14 / 0.45 mm; Ø 24.50 | 0.14 / 0.38 mm; Ø 25.15 |
| Bracket, seed 0 | 0.73 / 1.33 mm | **1.29 / 5.20 mm** | 0.73 / 1.33 mm |
| Bracket, seed 1 | 1.25 / 2.50 mm | 1.30 / 2.51 mm | 1.25 / 2.50 mm |
| Plate, floor 120 mm, seed 0 | 0.70 / 8.01 mm; Ø 25 fails | 1.80 / 7.77 mm; Ø 22.46 | 0.70 / 8.01 mm; Ø 22.58 |

- The guard inside the matcher also changes what the tracker sees, and one bracket scan tracked much worse. On the fused depth only, tracking is unchanged by construction.
- The bracket's tracked cylinders vary a lot from seed to seed, more than any fix moves them (shaft radius error with no fix +0.50 / +0.63 mm; with the fused-only guard +0.53 / +0.94 mm).

**The plate scene** (study script `plate_scene.py`; the simulator needed no change): a 160x90x3 mm plate with through holes Ø 25, 10 and 6 mm and real hole walls, a 28 mm block with a blind bore Ø 10 x 8 mm, the floor 10 mm or 120 mm below, seeds 0–2. Hole fit Ø at true poses:

| Hole, floor | None | Guard | `sigma_z` above 3 mm dropped |
|---|---|---|---|
| Ø 25, 120 mm | fails / 12.74 / 13.71 | 23.84 / 23.64 / 23.68 | 24.63 / 24.53 / 24.53 |
| Ø 10 and Ø 6, 120 mm | closed | closed | closed |
| Ø 25, 10 mm | fails ("not flat") | fails | fails |
| Ø 10, 10 mm | fails | fails | 10.07 / 9.42 / 9.62 |
| Ø 6, 10 mm | closed | closed | closed |
| Blind bore Ø 10 (Cylinder on its wall) | 9.20–9.25, axis 4.5–8.5° off | | 9.43–9.65 |

- **Where the top surface ends is close to the truth** even where the fit fails: with the guard, Ø 25.3–25.7 at 120 mm and Ø 26.4–26.7 at 10 mm.
- **Floor 10 mm below:** the mesh runs from the plate down the wall to the floor, so the Hole tool says "not flat, use Cylinder", and a 3 mm wall is scanned too poorly for a cylinder. The Hole tool would have to fit the edge of the flat top region instead of the mesh's boundary loop.
- **Small holes at 120 mm** are filled completely in every frame (Ø 10: 83 % of its pixels, Ø 6: 67 %): the background's parallax, about 19 mm, is larger than the hole, so no pixel inside is left without depth for a guard to start from. A "projected dot within R px" test was tried and rejected: it removed 25–35 % of the plate's own depth first.

**What follows:**
- The candidate fix is the guard on the depth that is fused, not inside the matcher. It was measured with the numpy prototype; a GPU version inside the fusion (about 1 ms) is not built.
- It changes every scan (about 5–8 % fewer depth pixels fused, thinner side-wall coverage, convex features 0.2–0.5 mm smaller in diameter), so it is not adopted without a decision.
- Still unknown: more tracking seeds; whether 8 px suits real images (ambient light, MJPEG, the real dot density); small holes over a dark background, which need another cue (the laser lines' end points trace a rim); the remaining −0.7 to −1.4 mm on Ø 25 at true poses.

Scripts, diffs and tables: scratchpad `holes\` (`RESULTS.md`, `proto_*.diff`, `res\`).

**The gap guard in the engine (2026-11-13).** The guard on the fused depth is now built into the reconstruction (`ohs/track/recon_tsdf.py`, "The gap guard"; `PipelineParams.gap_guard_px`), on the GPU, in the numpy twin and for the Open3D reconstruction. On more tracked scans it opens the holes every time, but on a small part with many edges it costs more with tracking than was known. It was first committed switched off; **it is on by default since the user's decision of 2026-11-13** (holes are what car parts are mounted by), and `gap_guard_px=0` turns it off.

How it was checked: 16 simulated recordings (car panel and bracket, seeds 0–4; the plate 120 mm and 10 mm above the floor, seeds 0–2), Standard quality. Each was tracked once and then fused four times: at that same tracked trajectory and at the true poses, with the guard off and on. So off and on differ in nothing but the guard.

| Scene | Poses | Measure | Guard off | Guard on |
|---|---|---|---|---|
| Car panel, Ø 25 hole (as fitted: 25.20) | tracked | Hole fit | fails on 4 of 5, Ø 11.5 on one | Ø 23.8–24.3 |
| | true | Hole fit | fails on 5 of 5 | Ø 23.6–24.1 |
| | both | Panel error, 95 % | 1.8–2.5 mm | 0.6–1.0 mm |
| | both | Panel covered | 100 % | 99.97–100 % |
| Plate 120 mm above a dark floor, Ø 25 | tracked | Hole fit | fails, or Ø 10.7–12.1 | Ø 22.0–22.1 |
| | true | Hole fit | fails, or Ø 11.4–12.2 | Ø 23.4–23.6 |
| | both | Plate error, 95 % | 3.0–3.4 mm | 1.7–2.2 mm |
| Plate 10 mm above the floor, Ø 25 | both | Hole fit | fails ("not flat") | fails ("not flat") |
| | both | Plate error, 95 % | 3.0–3.2 mm | 2.0–2.2 mm |
| Bracket | true | Object error, median / 95 % | 0.092–0.097 / 0.60–0.65 mm | 0.060–0.066 / 0.49–0.51 mm |
| | true | Radius error: bosses / shaft | −0.08 to −0.18 / +0.07 to +0.11 mm | −0.10 to −0.22 / −0.02 to −0.12 mm |
| | true | Object covered | 95.8–96.2 % | 94.2–95.3 % |
| | tracked | Object error, median | 0.12–0.30 mm | 0.11–0.24 mm (better on 5 of 5) |
| | tracked | Object error, 95 % | 0.78–1.34 mm | 0.91–1.45 mm (worse on 4 of 5, by 0.1–0.2 mm) |
| | tracked | Radius error: left boss / right boss / shaft | +0.19 to +0.39 / +0.18 to +0.44 / +0.46 to +0.86 mm | +0.30 to +0.66 / +0.20 to +0.45 / +0.55 to +0.94 mm |
| | tracked | Object covered | 72.6–94.4 % | 67.7–91.8 % (1.1 to 7.4 points less) |

- **Holes:** the Ø 25 hole over a far background can be measured on every one of the 16 runs where it could not before, 1.1 to 1.6 mm small at true poses and 0.9 to 3 mm small with tracking. Small holes (Ø 6–10 mm) and the hole 10 mm above the floor are as before.
- **The floor and the flat surface get better** everywhere (floor error 95 %: 0.29 → 0.23 mm at true poses, 0.36 → 0.28 mm tracked).
- **The cost is on the bracket with tracking:** less of the part is covered, its worst errors grow, and the bosses read 0.01–0.27 mm larger than without the guard. The likely reason: a voxel next to a gap keeps only the frames in which it was away from the gap, so fewer frames average out the tracking error there.
- **The sigma with the guard:** counted on the depth as the matcher made it, the "observations next to missing depth" all but vanish with the guard on, and 2σ then covered only 90–94 % (first run, car panel). The count now treats what the guard drops as missing. With that, 2σ covers 96.5–96.8 % (tracked) and 97.7–97.9 % (true poses) of the car-panel scans, as without the guard, and 95.8–96.8 % and 98.0–98.3 % of the bracket's. On the bracket's small faces with tracking it covers less than without the guard (upright 70–100 % against 79–100 %, base 89–96 % against 91–99 %), and the panel's outer edge and rim 77–81 % against 84–89 %.
- **Speed:** a frame takes 1.2 to 1.6 ms longer with the guard on (median over the 16 recordings: 91.6 → 92.8 ms tracked poses, 90.4 → 92.0 ms true poses). Switched off, a scan comes out as before the guard: the same vertices and the same sigma.
- **The CPU path** (the Open3D reconstruction, which gets its depth guarded; one car-panel and one bracket scan at true poses): the car panel's error falls from 0.129 / 2.53 to 0.111 / 1.91 mm (median / 95 %), but its hole still fails to fit (the CPU matcher fills further in than 8 px); the bracket's object error falls from 0.149 / 2.0 to 0.128 / 0.57 mm and its shaft reads +0.09 mm instead of +0.39 mm, with 3.7 points less coverage (95.5 → 91.8 %). A frame takes 13 ms longer there (157 → 170 ms).
- **An idea, since tried:** decide per voxel instead of per pixel. A filled-in voxel is next to a gap in every frame that sees it; real surface next to a moving shadow is also seen away from one. Keeping every observation of a voxel that was seen away from a gap a few times should keep the bracket's coverage and still open the holes. Tried on 2026-11-16 and rejected (next section).
- **The tracked bracket rows above are out of date.** With the tracking of 2026-11-16 (trajectory error 0.49–0.76 mm median) the guard costs the tracked bracket 0.7–2.0 points of coverage, not 1.1–7.4; its 95 % error is better with the guard than without on all 5 seeds (0.45–0.49 against 0.50–0.96 mm), and the bosses no longer read larger (next section).

Scripts and rows: scratchpad `holes\` (`guard_ab.py`, `guard_ab_summary.py`, `res\guard_ab.jsonl`).

## The gap guard per voxel: tried and rejected (2026-11-16)

A study with a patched copy of the engine outside the repository; nothing in the engine changed. The idea is the last one in "The gap guard in the engine": hold back what the guard would drop until the voxel has been seen away from a gap K times, then fuse it after all.

**In short:** no. It wins back half of the bracket's lost coverage at best (K=2: 0.4–1.0 points) and gives back the bias the guard removes, so the bracket's errors return to the level without the guard, and K=2 closes the holes again by 0.4–0.8 mm. K=4 wins back almost nothing. The guard stays per pixel.

**The rule as built:** an observation the per-pixel guard would drop is held aside while its voxel has fewer than K observations away from a gap; only observations within 2 voxels of the surface count. Once a voxel has K, it fuses everything. When the mesh is made, the held observations are merged into the mean and the count of the voxels that reached K. 16 more bytes per voxel (64 → 80 B with the thin-wall channels), 1.1–1.3 ms more per frame on the GPU (fusion alone: off 21.1, per pixel 23.0, K=2 24.3, K=4 24.1 ms; 6 alternating rounds of 60 frames). Checked exactly: K=0 gives the mesh without the guard and a very large K today's mesh, vertex for vertex.

**Setup:** the 16 recordings of "The gap guard in the engine", on the engine of 3c0aa5f, each tracked once and fused at the tracked and the true poses with four guards: off, per pixel (today), per voxel K=2 and K=4. Ranges over seeds; hole fits from 3 clicks per recording.

| Scene | Poses | Measure | Off | Per pixel (today) | K=2 | K=4 |
|---|---|---|---|---|---|---|
| Car panel, Ø 25 hole (as fitted: 25.20) | tracked | Hole fit | fails 13 of 15 | 23.82–24.23 | 23.03–23.71 | 23.88–24.20 |
| | true | Hole fit | fails 15 of 15 | 23.49–24.11 | 23.26–23.53 | 23.71–24.10 |
| | tracked | Panel error, 95 % | 2.03–2.18 mm | 0.60–0.80 | 0.59–0.82 | 0.57–0.75 |
| Bracket | tracked | Object error, median | 0.094–0.175 mm | 0.073–0.227 | 0.090–0.172 | 0.089–0.171 |
| | tracked | Object error, 95 % | 0.50–0.96 mm | **0.45–0.49** | 0.48–0.54 | 0.48–0.54 |
| | tracked | Object covered | 96.3–96.8 % | 94.5–95.6 | 95.2–96.0 | 94.5–95.6 |
| | tracked | Radius error: shaft / bosses | +0.01 to +0.18 / −0.31 to −0.10 mm | −0.07 to +0.07 / −0.27 to −0.07 | +0.01 to +0.18 / −0.32 to −0.13 | +0.01 to +0.16 / −0.31 to −0.13 |
| | true | Object error, median / 95 % | 0.090–0.098 / 0.49–0.62 mm | **0.056–0.062 / 0.42–0.43** | 0.087–0.095 / 0.46–0.48 | 0.086–0.095 / 0.46–0.48 |
| | true | Object covered | 96.2–96.6 % | 94.3–95.3 | 94.9–95.8 | 94.4–95.5 |
| Plate 120 mm above the floor, Ø 25 | tracked | Hole fit | 10.3–23.4 | 18.1–22.1 | 15.6–21.5, 1 fails | 17.7–21.9 |
| | true | Hole fit | 23.4–23.8, 1 fails | 23.4–23.6 | 22.8–23.2 | 23.2–23.5 |
| Both plates | both | Plate error, 95 % | 2.4–3.1 mm | 1.3–2.0 | 1.6–2.6 | 1.5–2.2 |

- Coverage of the car panel and the plates is 99.4–100 % in every variant. The floor's 95 % error is the same as with today's guard or slightly better (K=4 by about 0.05 mm).
- The Ø 25 hole 10 mm above the floor fits in every variant (about Ø 24.4–25.8): the Hole tool reads holes with a wall since 2026-11-14.

**Why it fails** (fused at true poses with K never reached, counting each voxel's observations away from a gap):
- **The bracket's lost voxels are never seen away from a gap.** Of the about 1,450 voxels with 3 or fewer fused observations (seed 0), 70 % have none away from a gap and only 5 % have 3. No K brings them back.
- **The rest get their bias back.** 84 % of the bracket's surface voxels have observations near a gap, and 87 % of those are seen away from one 16 times or more, so they unlock at any K. The observations near a gap are biased (the upright's surface sinks by about 0.06 mm), so the error returns to the level without the guard. **The guard's gain on the bracket is a bias filter**, everywhere, not only at holes, and the per-voxel rule removes exactly that.
- **The premise does not hold for the hole either.** In the 3 mm inside the rim, 49 % of the filled voxels are seen away from a gap twice or more, 38 % four times or more: frames where the fill reaches further than the guard's 8 px. That is why K=2 closes the holes by 0.4–0.8 mm.
- Not explained: K=4 covers slightly less than per pixel on the bracket's upright and side walls in some runs (96.0 against 96.4 %), although it fuses more observations. Probably the restored bias moves the surface more than 1 mm from part of the truth; not checked.

Scripts, the patch and rows: scratchpad `voxguard\` (`voxguard_recon_tsdf.diff`, `vox_ab.py`, `vox_summary.py`, `diag_vfar.py`, `bench_fuse.py`, `res\vox_ab.jsonl`, `res\diag_vfar.jsonl`).

## The Hole tool on holes with a wall (2026-11-14)

The Hole tool fitted a circle to an open edge of the mesh. Where the mesh runs on from the flat face down a hole's wall (a bore, a hole in a thick plate, a hole with a floor close under it) there is no open edge at the rim, and the tool answered "no hole edge within 10 mm" or "the faces around this hole's edge are not flat". It now reads such a hole from the flat face around the click (`ohs/measure/region.py`, "A hole with a wall"). A hole that has an open edge is measured as before.

**How:**
- The flat face grows from the click as for the Plane tool, and its edges are searched for a hole. This only finds the hole.
- The hole's edge is the line inside it where the surface is 0.5 mm below the face (`RIM_DROP_MM`; three of the face's tolerances when that is more): the points where the mesh's edges cross that level, with a circle fitted to them.
- Inside that line the surface must go on down to 1.5 mm or more. A shallower dip is a dent, or a hole that the scan has closed, and the answer says so.
- Where the surface rises from the face by 1.5 mm or more, the edge is the foot of a boss or a step, and the answer says that. A lower bump counts as the face.

**Why a level, and not where the flat face ends:** the face's region stops where its own tolerance says, and that depends on the click. Read at the region's edge, the Ø 25 mm hole of the plates below came out between 27.2 and 39.7 mm over 17 clicks (one was refused). Read at a fixed level it is the same from every click within 0.02 mm.

**On scans** (the plates of "Holes filled in": a 3 mm plate with holes of Ø 25, 10 and 6 mm, 10 mm or 120 mm above the floor; three recordings each, Standard quality, GPU path, the engine of 2026-11-14; three clicks per hole, 3 mm outside the true rim, so nine answers per row):

| Plate | Hole | Poses | Before | Now |
|---|---|---|---|---|
| 10 mm above the floor | Ø 25 | true | refused 9 of 9 ("not flat") | Ø 25.57–25.70, centre within 0.22 mm |
| | | tracked | refused 9 of 9 | Ø 25.35–25.51 on 8 clicks; 1 refused ("the surface rises") |
| | Ø 10 | true | refused 9 of 9 | Ø 10.28–10.60, centre within 0.13 mm |
| | | tracked | refused 9 of 9 | Ø 9.66–10.10 |
| | Ø 6 | both | refused ("no hole edge") | refused: "the surface dips only 0.7–1.0 mm … a dent, or a hole that the scan has closed" |
| 120 mm above the floor | Ø 25 | true | Ø 23.43–23.60 | the same (it has an open edge) |
| | | tracked | Ø 21.90–22.09 | the same |
| | Ø 10 and Ø 6 | both | refused ("no hole edge") | refused: "the surface dips only 0.6–1.0 mm …" (2 clicks of 36 still "no hole edge") |

- **A hole over a near floor can be measured now**, about 0.6 mm large in diameter at the true poses (Ø 25: +0.57 to +0.70 mm; Ø 10: +0.28 to +0.60 mm), and −0.3 to +0.5 mm with tracking. A tracked mesh's centre is given in its first camera's frame here, so its 0.75–1.16 mm include the tracking error.
- **The reading depends on the level, because a fused rim is a rounded shoulder and not a corner.** The Ø 25 mm hole reads 26.7 mm at 0.15 mm below the face, 26.1 mm at 0.3 mm, 25.6 mm at 0.5 mm, 24.9 mm at 1 mm and 24.3 mm at 2 mm; the Ø 10 mm hole 11.7, 11.2, 10.6, 9.7 and 9.1 mm. On these plates the true diameter is crossed at about 0.8–0.9 mm. The level was not tuned to that: one plate thickness, one floor distance and one scanning distance do not fix a rule. 0.5 mm is the first level clearly below the face's own scatter.
- **Holes the scan has closed are refused in words.** The Ø 6 mm holes, and the Ø 10 mm hole over the far floor, are dents 0.6–1.0 mm deep in the mesh. With the depth test left out the tool gave Ø 5.3–7.9 mm for the Ø 10 mm hole and Ø 2.8–4.1 mm for the Ø 6 mm holes, two of those answers without a warning about the edge.
- **The fit's ± does not cover this.** It comes from the scatter of the points about the circle, not from the shoulder's bias: ± 0.08–0.10 mm in diameter for the Ø 25 mm hole (1σ), against +0.6 mm, and ± 0.17–0.20 mm for the Ø 10 mm hole.
- **Not measured:** real scans; a countersunk or chamfered hole (the level then lies on the chamfer, and the diameter read is the chamfer's at that depth); holes in a wall that is not flat.

**Tests** (`tests/test_measure_region.py`; they failed first with "no hole edge within 10 mm"): a bore in a plate from two sides, a blind hole with a floor 3 mm down, the same answer from four clicks, the foot of a boss refused, a 0.9 mm dent refused. The 22 tests that were there pass unchanged.

Scripts: scratchpad `shape\` of the session of 2026-11-14 (`hole_eval.py`, `hole_summ.py`; `results\hole_eval6.jsonl`).

## The vertex sigma: why it was too large, and the model that replaced it (2026-11-13)

A study with prototypes outside the repository, and then the model built into the engine: **adopted on 2026-11-13** ("In the engine" at the end of this section). The geometry is not touched by any of it; the sigma stored with every new scan is.

**In short:**
- **The pixel sigma is about right. The way a voxel averages it is not.** A few observations with a hugely inflated sigma (pixels next to a gap, where the matcher multiplies sigma by up to 121) dominate the mean of the variances, and three smaller effects add to it. Together: 4 to 5.5 times too large on flat surface.
- **The "independent looks" rule is not the cause.** Errors of one surface point are almost uncorrelated between frames here, and the true error of a fused point falls as 1 / √(observations).
- **A candidate model is 3.4 times tighter on flat surface and larger at edges.** With it the Check answer passes 56–58 % of a scan at ± 0.3 mm (today 10.6 %; truly within 92 %), and 99.8 % of what it passes is truly within.
- **It does not reach every goal:** it is still about 1.5 times too large on flat surface, edges with tracking reach 89 % within 2σ, and a per-vertex sigma cannot see a tracking error that shifts a whole region.

**Setup:** the 20 meshes of the fitted-shapes study, every recording processed again with extra per-voxel channels (geometry identical to 1e-13 mm), and 440 single frames against the renderer's true depth. Models were fitted on seeds 0–2 and are reported on seeds 3–4. Region classes as in the Check study. A calibrated 1σ gives a median |error| / σ of 0.67 and 95 % within 2σ.

**The pixel sigma** (single frames, flat well-seen pixels): median |error| / σ is 0.60 on the bracket and 0.48 on the car panel, with 92 % and 97 % within 2σ. Next to pixels without depth the sigma is 40–95 mm where the error is about 0.7 mm: that factor is tuned to cover the tail, not as a variance.

**Where the factor comes from** (vertex sigma against a calibrated one, true poses, median factors):

| Surface | Total | Inflated pixels in the mean of the variances | Other pixel inflation (incidence, the cap) | Depth along the ray instead of the normal | Looks counted below observations |
|---|---|---|---|---|---|
| Car panel, flat | 4.1x | 1.24x | 2.17x | 1.41x | 1.20x |
| Bracket, faces | 4.9x | 2.28x | 1.29x | 1.34x | 1.64x |
| Bracket, table | 5.5x | 1.06x | 2.70x | 2.02x | 1.04x |

- **The observed-scatter term of the `max()` rule never wins** (0 % of the area): sigma is always the predicted term.
- **Looks:** errors of one surface point decorrelate after 5–10 px of image movement, and frames 0.1 s apart move 16 px (correlation 0.03). The true fused error is 0.245 mm at 10 observations and 0.042 mm at 150. On the bracket's own faces it is the other way round: about 15 effective looks against 26 counted, because the error there has a bias that stays.
- **Interpolating onto the vertices is exact** (2e-6 mm); the default for depth without sigma is never used.

**What predicts the bias** (rank correlation with the true |error|, tracked / true poses): the share of observations within 6 px of a gap +0.55 / +0.30; the mesh's curvature +0.60 / +0.22; distance to the mesh border −0.40 to −0.53; scatter beyond what the pixels predict marks the 0.3–4.6 mm errors of fill-in surfaces. Depth steps, grazing views and 1 / √(observations) added nothing once these were in.

**The candidate model:** the scatter of the distance along the normal divided by the looks, plus four bias terms: observations near a gap, scatter beyond the prediction, curvature, and nearness to the mesh border. Five fitted constants. Seeds 3–4, median |error| / σ and share within 2σ:

| Poses, sigma | Flat | Table, floor | Edge | Small feature | Hole rim | Border |
|---|---|---|---|---|---|---|
| True, today | 0.15 / 99.6 % | 0.16 / 100 % | 0.56 / 74.5 % | 0.18 / 99.5 % | 0.13 / 99.2 % | 0.17 / 95.3 % |
| True, candidate | 0.40 / 98.8 % | 0.48 / 96.7 % | 0.52 / 91.4 % | 0.12 / 99.0 % | 0.34 / 95.8 % | 0.38 / 99.2 % |
| Tracked, today | 0.18 / 98.1 % | 0.16 / 100 % | 0.69 / 73.8 % | 0.34 / 97.5 % | 0.13 / 99.3 % | 0.18 / 95.5 % |
| Tracked, candidate | 0.46 / 95.7 % | 0.54 / 96.7 % | 0.53 / 89.0 % | 0.21 / 95.7 % | 0.33 / 97.4 % | 0.38 / 99.3 % |

- The median sigma on flat surface goes from 0.141 to 0.041 mm; at edges it rises from 0.40 to 0.59–0.65 mm.
- **Today's sigma covers edges only 74 % of the time.** The earlier "2σ covers 95–100 % of the vertices" holds over a whole mesh because flat surface dominates.
- **Tracking:** on the bracket's flat faces the tracked error is twice the one at true poses (0.165 against 0.080 mm median) and the candidate covers 89 % there. That is a shift of a whole region; a constant floor fitted for it came out at 0. It would need a per-scan pose-quality term (not tried).
- Car-panel edges reach 88 % (true poses) and 86 % (tracked).

**The Check answer with it** (`weak_areas`, k = 2, all 20 meshes pooled by area):

| Poses | Tolerance | Truly within | Today: passed / of that truly within | Candidate: passed / of that truly within (lowest mesh) |
|---|---|---|---|---|
| True | 0.1 mm | 66 % | 0.3 % / 100 % | 13.6 % / 98.2 % (97.7 %) |
| True | 0.2 mm | 85 % | 2.7 % / 100 % | 41.4 % / 99.6 % (99.4 %) |
| True | 0.3 mm | 92.5 % | 10.6 % / 100 % | 56.0 % / 99.8 % (99.6 %) |
| True | 0.5 mm | 97.6 % | 28.6 % / 100 % | 71.1 % / 99.9 % (99.9 %) |
| Tracked | 0.1 mm | 65 % | 0.3 % / 100 % | 19.4 % / 98.7 % (98.4 %) |
| Tracked | 0.3 mm | 91.7 % | 10.5 % / 100 % | 57.6 % / 99.8 % (99.7 %) |
| Tracked | 0.5 mm | 97.1 % | 28.5 % / 100 % | 71.6 % / 99.9 % (99.9 %) |

- Seeds 3–4 alone give the same numbers within a point. Of the truly bad area, 98–99 % is still flagged up to 0.5 mm.
- A variant with a larger bias term passes less (49–52 % at 0.3 mm) and reaches 93.6 % on tracked edges.

**Cost** (measured in the real pipeline on one car-panel scan, where the patched engine gave the same geometry and the same calibration as the study): 28 → 44 bytes per voxel (the pool of one scan 448 → 704 MB); 3.2 → 9.4 ms of host time per frame, most of it a distance transform on the CPU that the matcher already computes on the GPU; 5.7 s more at mesh extraction (numpy, not optimised).

**What follows:**
- The candidate makes the Check panel useful (it passes more than half of a scan instead of a tenth, and stays safe).
- Risks: simulator only (two scenes, one way of moving); five constants fitted on it; the model divides by the looks, counted as one per 10 px of image movement, and whether a real dot pattern's errors decorrelate over that distance is not known (here they do, within 5–10 px). The CPU reconstruction path still has no sigma.

**In the engine (adopted 2026-11-13, `ohs/track/recon_tsdf.py`):**
- **What was built:** the fusion kernel keeps, per voxel, the scatter along the normal, the count of observations next to missing depth, and the predicted variance without the matcher's factor. Two more kernels measure the distance to missing depth on the GPU (Euclidean, as the GPU matcher measures it; the prototype used a CPU transform with a slightly different metric). The curvature and border terms are added when the mesh is made. The numpy twin does the same, and the tests compare the two.
- **The geometry is unchanged:** the same vertices to 1e-13 mm on the six scans whose processing repeats exactly (four at true poses, two tracked).
- **It is the prototype's sigma:** on the scan the prototype was run on, the median ratio of the two is 1.000 (1st to 99th percentile 0.81–1.17, from the two distance metrics).
- **Held-out scans with the engine's sigma** (seeds 3–4: three meshes at true poses, two tracked), median |error| / σ and share within 2σ:

| Poses | Flat | Table, floor | Edge | Small feature | Hole rim | Border |
|---|---|---|---|---|---|---|
| True | 0.44 / 98.9 % | 0.54 / 96.7 % | 0.55 / 91.6 % | 0.11 / 99.7 % | 0.32 / 96.6 % | 0.41 / 99.1 % |
| Tracked | 0.47 / 97.2 % | 0.56 / 96.0 % | 0.55 / 89.8 % | 0.16 / 98.2 % | 0.34 / 97.2 % | 0.38 / 99.3 % |

- **The Check answer on those meshes** (passed / of that truly within, lowest mesh in brackets): at ± 0.3 mm 58.5 % / 99.8 % (99.7 %) at true poses and 59.7 % / 99.8 % (99.6 %) tracked, where 92 % is truly within and the earlier sigma passed 11 %. At ± 0.1 mm 16.9 % / 99.2 % and 20.8 % / 98.5 %; at ± 0.5 mm 72.5 % and 72.9 %, both 99.9 %.
- **Cost, measured:** 28 → 40 bytes per voxel (the earlier per-pixel variance sum is no longer kept), the pool of one scan 448 → 640 MB. A frame takes 88 → 90 ms (medians of two runs each, alternating; the host's share of fusion stays 2.9 ms). Making the mesh takes 1.1–1.7 s longer (0.7–0.9 million vertices; the prototype: 5.7 s).
- **Preview quality (640 px, 2 mm voxels) was not in the study.** With the constants as fitted, 2σ covered only 91–92 % of the vertices there (true poses, two scans). The errors next to gaps, edges and borders are about twice as large at twice the voxel size, so the bias constants that are lengths now grow with the voxel size (never below the fitted 1 mm). With that, 2σ covers 98.1–98.4 % on four scans (two of them not used for the choice) and the flat panel reads 0.49 / 98.8 %, as at Standard quality. The panel's outer edge and hole rim, where the error at this quality is 2.6 mm (median), are covered 52–55 % (the earlier sigma: 28 %).
- **Still true:** car-panel edges reach 86–90 %; the sigma on flat surface is about 1.5 times too large; a tracking error that shifts a whole region is not seen; all of it is simulated. Not run: this model with the CPU matcher (its factor is taken out the same way, but the constants were fitted with the GPU matcher's sigma).
- **Smoothing's "within 2σ" limit binds now** (measured on one car-panel and one bracket scan, true poses, the Smooth tab's default): it holds back 18 % and 28 % of the vertices. The RMS error after smoothing is the same with and without the limit (to 0.001 mm). The median error on flat surface improves a little less with it: panel 0.0203 → 0.0174 mm against 0.0168 mm without the limit; the bracket's table 0.0271 → 0.0236 mm against 0.0211 mm. Nothing gets worse than before smoothing.
- **Not measured again:** a fit's ±, which follows the smaller sigma where the points agree with it. The Confidence legend's shares within 0.1 / 0.3 mm rise.

Scripts, the diff and tables: scratchpad `sigma\` (`RESULTS.md`, `proto_sigma_v2_recon_tsdf.diff`, `validate_prod.py`, `check_prod.py`, `preview_check.py`, `res\`).

## Where a tracked scan's shape errors come from (2026-11-14)

A study outside the repository. It points to two changes ("The two changes" below): **the first, 4 mm in the GPU tracking model, is in the engine since the same day**; the second, a shorter band in the mesh on both sides, is not made. A third, a band in the mesh that is short only behind the surface, came out of a prototype and **is in the engine since the same day for scans without markers** ("In the engine since 2026-11-14", after the prototype). "As it was" in the tables is the engine before that change.

**In short:**
- **Tracking is the largest share, and the tracker makes it itself.** With the true depth and the true poses the bracket's mesh is 0.010 mm from the part (median). The measured depth brings that to 0.065 mm and tracking to 0.11–0.22 mm (0.156 mm in the median), with the whole part about 0.3 mm too fat. Tracking does the same when it is fed the true depth.
- **The cause is the tracking model's truncation.** The model (2 mm voxels) marks everything up to 8 mm behind a surface as inside the part. Near a convex edge and on a thin feature that reaches into the open air on the other side, so the model swells as the scan goes round. Frames are registered to the swollen model, the cameras land 0.3–0.5 % too far out, and the mesh is fused too fat.
- **With 4 mm instead of 8 mm in the tracking model** the share of the part more than 0.3 mm off falls from 42 % to 20 % (13 % at true poses), and the upright face's offset from 0.72 to 0.17 mm. This holds for the GPU model only: the Open3D model of the CPU path gets worse with it. The GPU model has 4 mm since 2026-11-14.
- **A rest remains:** each frame is pulled about 0.1 mm ahead along the direction of motion, which leaves the camera path 0.05–0.2 % too large. Not solved.
- **The mesh itself has the same fault at a smaller size** (1 mm voxels, 4 mm truncation): a sheet thinner than 4 mm that is seen from both sides comes out too thick. A 2 mm flange reads 3.2 mm at true poses and 4.5 mm with tracking.

**Setup:**
- The five bracket recordings of the fitted-shapes study (240 frames, a 400° orbit, 1280x800), processed as the app's Make mesh at Standard quality on the GPU path, then the Default recipe's edits.
- The renderer's true depth of every frame, on the rectified left camera's pixel grid (it agrees with the matcher's depth to 0.004 mm in the median; the matcher's own scatter is 0.5 mm).
- Depth sources: the matcher's depth; the true depth at the matcher's pixels with the matcher's sigma (only the values change); the same plus random noise of the matcher's size, correlated over 7 px.
- Pose sources: the true poses; tracking on that depth.
- The mesh against the part's exact shape: every measured vertex's signed distance (+ = outside the material), on the part without the table. A tracked mesh is first aligned by a best fit, as in the fitted-shapes study.

**The shares** (medians of the five scans):

| Depth | Poses | Median distance | 95 % level | More than 0.3 mm off | Mean (+ = too fat) |
|---|---|---|---|---|---|
| True | True | 0.010 mm | 0.40 mm | 6.5 % | +0.04 mm |
| True plus noise | True | 0.028 mm | 0.41 mm | 6.7 % | +0.04 mm |
| Matcher | True | 0.065 mm | 0.50 mm | 13 % | +0.02 mm |
| Matcher | Tracked (the app as it was, 8 mm in the tracking model) | 0.156 mm | 1.18 mm | 42 % | +0.29 mm |
| True | Tracked (8 mm) | 0.260 mm | 0.70 mm | 44 % | +0.30 mm |
| Matcher | Tracked (the app now, 4 mm on the GPU path) | 0.148 mm | 0.60 mm | 20 % | +0.15 mm |

- **Fusion alone:** the median is small, but 6.5 % of the part is more than 0.3 mm off. That is the mesh's own truncation at edges (below).
- **The matcher's depth:** random noise explains 0.03 mm of its 0.065 mm; the rest is its patterns.
- **Tracking:** the shaft's radius reads +0.47 mm, the bosses' +0.23 mm, the upright face sits 0.72 mm off (0.09–0.88 mm over the five scans).

**The camera path is too large.** After a best fit of the tracked camera centres onto the true ones, a similarity fit still wants to shrink the path by 0.3–0.5 % on every scan (0.8–1.4 mm at the median distance of 257 mm). In the camera's own frame the error is much the same for the whole scan: the tracked camera stands about 0.6 mm farther out from the part than the real one did. A surface seen from a camera that stands too far back is fused too near that camera, so every convex shape grows.

**One step from the truth.** The tracking model was built at the true poses from the true depth; every fourth frame was then registered to it from its true pose with the tracker's own ICP. A perfect chain answers "stay".
- With 8 mm truncation the answer drifts away from the part as the scan goes on: 0.0 mm in the first sixth of the scan, 0.55 mm in the last. In the model the part's surfaces move towards the camera at the same time: the base by 0.28 mm, the bosses by 0.2–0.4 mm. The table does not move.
- With 4 mm the answer stays within 0.13 mm for the whole scan and the surfaces stay where they are.
- A flat face far from an edge is not affected: the face of an 8 mm wall seen from both sides stays within 0.03 mm at either setting. A 4 mm wall swells by 0.83 mm a side at 8 mm, and is within 0.1 mm at 4 mm (exact depth, a wall on a table, 36 views).

**The truncation of the tracking model, on the five scans** (matcher's depth, tracked; GPU path):

| Truncation | More than 0.3 mm off | 95 % level | Mean (+ = too fat) | Upright face offset | Shaft radius | Boss radius |
|---|---|---|---|---|---|---|
| 8 mm (as it was) | 42 % | 1.18 mm | +0.29 mm | 0.72 mm | +0.47 mm | +0.23 mm |
| 6 mm | 31 % | 0.70 mm | +0.22 mm | 0.24 mm | +0.30 mm | +0.15 mm |
| 4 mm (now) | 20 % | 0.60 mm | +0.15 mm | 0.17 mm | +0.17 mm | +0.03 mm |
| 3 mm | 17 % | 0.59 mm | +0.09 mm | 0.09 mm | +0.12 mm | −0.05 mm |
| True poses | 13 % | 0.50 mm | +0.02 mm | 0.01 mm | 0.00 mm | −0.13 mm |

- **The median distance does not follow:** 0.156 mm at 8 mm and 0.148 mm at 4 mm. Two scans get better (0.22 → 0.07 and 0.21 → 0.15 mm), one stays, two get worse (0.15 → 0.30 and 0.16 → 0.22 mm) while their 95 % levels fall from 1.4 to 0.6 mm.
- **3 mm is one and a half voxels:** the model covers less of each view (76 → 70 % in the one-step check), the bosses' axis tilt grows from 3.4° to 5.1° and the table's offset from 0.001 to 0.010 mm.
- The camera path with 4 mm is 0.05–0.2 % too large (with the true depth 0.04–0.13 %).

**Other scenes with 4 mm** (GPU path): the end-to-end tests' sphere and car-panel scans keep their numbers (the full sphere scan's worst pose error 0.149 → 0.153 mm, the car panel's 0.297 → 0.293 mm), and so do the five 200-frame car-panel scans of the fitted-shapes study (pose error 0.20 → 0.19 mm in the median, the hole 0.94 → 0.90 mm small in diameter). The sphere scan without the gyro, which the tracker flags as it is, drifts 59 mm instead of 38 mm. The whole test suite on the GPU path: 749 passed, 1 failed, and that one is the benchmark test's CPU half.

**The CPU path does not take it.** The Open3D tracking model gets worse as the truncation shrinks: the half-resolution sphere scan's worst pose error is 0.43 mm at 8 mm, 1.6 mm at 6 mm and 5.3 mm at 4 mm, and 14 tests of the suite fail (how many of them by the change and how many by the machine being busy at the time was not separated). With the true depth it behaves like the GPU model at 4 mm, so it is the noisy depth it cannot take. A likely reason, not tested: that model never clears the space in front of a surface, so it needs the wide band to average bad depth away.

**What remains after 4 mm.** In the one-step check a frame is pulled along the camera's own x axis: +0.19 mm on the scans that sweep towards +x, −0.09 mm on a scan that sweeps the other way, so about 0.13 mm of it follows the motion and 0.05 mm is fixed to the camera. The pull along the viewing direction is zero. The gyro holds each frame's rotation, so steps that are a little too long make the orbit a little too wide. The lead is there with the unfiltered matcher's depth as well; with the true depth, unfiltered, it is 0.06–0.08 mm and does not flip. Where it comes from is open.

**The mesh's own truncation** (1 mm voxels, 4 mm): the same rule, half the size. A wall thinner than 4 mm that is seen from both sides swells: with exact depth a 1 mm wall reads 2.3 mm, a 2 mm wall 2.8 mm and a 3 mm wall 3.1 mm; at 2 mm truncation each is right within 0.1 mm a side. Faces within 4 mm of a convex edge swell too, which is the 6.5 % above. On the five bracket scans at true poses:

| Truncation of the mesh | True depth: 95 % level / more than 0.3 mm off / boss axis tilt | Matcher's depth: the same | Boss radius (matcher) | Table offset (matcher) |
|---|---|---|---|---|
| 4 mm (as it is) | 0.40 mm / 6.5 % / 3.6° | 0.50 mm / 13 % / 4.4° | −0.13 mm | 0.008 mm |
| 3 mm | 0.21 mm / 3.4 % / 3.2° | 0.41 mm / 9.9 % / 3.9° | −0.25 mm | 0.001 mm |
| 2 mm | 0.11 mm / 1.1 % / 0.5° | 0.37 mm / 7.8 % / 1.6° | −0.36 mm | 0.031 mm |

- **What 2 mm repairs:** the edges, and the bosses' lean of 4–5° and their axis 0.25 mm off centre (0.09 mm at 2 mm), which the fitted-shapes study listed as a bias of the depth and the fusion. Flat faces scatter less: the base top's vertices 0.13 → 0.06 mm.
- **What it costs with the matcher's depth:** the bosses read 0.36 mm small in radius instead of 0.13 mm. With the true depth they are right (−0.02 mm), so the shrinkage is the depth's and the gap guard's, and the swelling had been hiding 0.15 mm of it. Flat faces sink by 0.03 mm. A boss wall has 20 % fewer vertices, the part 3 % fewer.

**A sheet-metal part** (one simulated scan as the bracket's: a 2 mm foot on the table, a 2 mm flange and ribs of 1 and 3 mm, all seen from both sides; the thickness is the distance between the planes fitted to a wall's two faces):

| | 2 mm flange | 1 mm rib | 3 mm rib |
|---|---|---|---|
| As it was (tracking model 8 mm), tracked | 4.46 mm | 3.32 mm | 5.19 mm |
| Tracking model 4 mm (now) | 3.37 mm | 2.89 mm | 3.85 mm |
| Tracking model 4 mm, mesh 3 mm | 2.89 mm | 2.33 mm | 3.82 mm |
| Tracking model 4 mm, mesh 2 mm | 2.73 mm | 1.95 mm | 3.78 mm |
| True poses, mesh 4 mm (as it is) | 3.17 mm | 2.73 mm | 3.19 mm |
| True poses, mesh 3 mm | 2.61 mm | 1.86 mm | 2.96 mm |
| True poses, mesh 2 mm | 2.07 mm | 1.23 mm | 2.89 mm |

With tracking 0.7–1 mm stays even with both changes: the tracking model's 2 mm voxels cannot hold a 1–2 mm sheet.

**The two changes** (both change scan results):
- **A. The GPU tracking model's truncation 8 → 4 mm: in the engine since 2026-11-14** (the owner's decision). `FusionParams.gpu_trunc_mult` gives the GPU model its own truncation in voxels; the pipeline sets 2 for its tracking model (`TRACK_GPU_TRUNC_MULT`), and `report.json` records the result as `params.tracking.trunc_mm`. The Open3D model of the CPU path keeps 8 mm, so the two paths differ in one more setting and a scan processed without a GPU is as fat as before. A tracker made without the pipeline is unchanged.
  - **Guard:** `test_the_gpu_tracking_model_keeps_a_thin_wall_in_place` fuses the 4 mm wall above into the pipeline's own tracking model and wants its face within 0.15 mm. Before the change it failed with −0.87 mm. `test_the_open3d_tracking_model_keeps_its_wide_truncation` holds the CPU path at 8 mm.
  - **Suite after the change:** GPU path 752 passed, 2 skipped; CPU path 706 passed, 48 skipped. The benchmark test whose CPU half failed in the study passes: the study's switch had changed both paths.
  - **The five bracket scans again, with the engine as it is now:** the same as the study's 4 mm runs within the run-to-run scatter (19.9 % more than 0.3 mm off, 95 % level 0.60 mm, median 0.148 mm, mean +0.15 mm, upright face 0.17 mm, shaft radius +0.18 mm, boss radius +0.03 mm).
  - **Every tracked number elsewhere in this document that was measured on the GPU path before 2026-11-14 was made with 8 mm.** The end-to-end tests' sphere and car-panel scans keep theirs (above); parts with edges, such as the bracket of the fitted-shapes study, read up to 0.3 mm less fat now and were not measured again there.
- **B. The mesh's truncation 4 → 2 mm (or 3 mm): not made.** Measured above at true poses and on the sheet part, and then on other scenes (next paragraph): it is not a clear gain as it stands. A rule that is short only behind the surface was then tried as a prototype (the paragraph after that), and its form with the cut at 2 voxels is in the engine since 2026-11-14.

**B on other scenes** (2 mm, together with A, GPU path, 1 mm voxels):
- **Smooth scenes get a little worse.** The end-to-end tests' full sphere scan: mesh distance 0.061 → 0.065 mm in the median and 0.257 → 0.301 mm at the 95 % level. Their car panel: 0.083 → 0.086 and 0.389 → 0.401 mm. The five 200-frame car-panel scans: the floor's offset 0.002 → 0.007 mm, the hole's centre 0.33 → 0.42 mm, its diameter 0.90 → 0.87 mm small.
- **Poor poses get clearly worse.** Marker mode, whose poses are 0.9 mm off in the median: mesh distance 0.49 → 0.61 mm in the median and 1.48 → 2.47 mm at the 95 % level. A narrow band cannot average frames that do not agree.
- **A model that also tracks does not take it** (the car panel with `track_filter_px=0`, an Open3D model): pose error 1.4 → 3.0 mm in the median, as with A on the CPU path.
- **Not measured properly:** half resolution and the processing studio's preview (2 mm voxels). The switch of the study set 2 mm there too, which is one voxel and too little. The five failures it gave in the processing studio's tests (two on the GPU path, three on the CPU path; those tests scan at half resolution) were not looked into further and say nothing about two voxels.
- So a shorter band alone trades the thin walls and the edges against smooth surfaces and poor poses. A rule that is short only behind the surface, or that weighs what lies behind a surface less, is the thing to try next.

**A band that is short only behind the surface: prototype (2026-11-14).** Tried in a scratch copy of the engine, and the numbers of this paragraph are the prototype's. Its form with the cut at 2 voxels went into the engine the same day ("In the engine since 2026-11-14" below). The rule changes the mesh model's integration only (the GPU kernel and its numpy twin in `ohs/track/recon_tsdf.py`). In front of a surface the band stays 4 voxels. Behind a surface an observation counts in full down to a cut. Beyond the cut it either does not count ("plain stop") or counts with a weight of 0.001 down to the 4 voxels of today ("low weight"). "Behind" is measured along the view, as the signed distance is, or at right angles to the surface (the distance times the cosine between the ray and the depth map's normal).

*Exact depth* (the engine's mesh model at 1280x800 and 1 mm voxels, a wall on a table, 36 views; wall thickness read in mm. In the last two columns every view is fused at a pose that is off by a random error; median of three seeds):

| Rule behind the surface | 1 mm wall | 2 mm wall | 3 mm wall | 2 mm wall, poses 0.3 mm off | Thick block, poses 0.9 mm off: median distance / table offset |
|---|---|---|---|---|---|
| Today (4 mm along the view) | 2.26 | 2.64 | 3.13 | 2.69 | 0.21 / −0.05 mm |
| Stop at 3 mm along the view | 1.90 | 2.36 | 2.87 | 2.28 | 0.23 / −0.06 mm |
| Stop at 2 mm along the view | 0.86 | 1.98 | 2.80 | 1.85 | 0.50 / −0.18 mm |
| Fading from 1 to 3 mm along the view | 1.06 | 2.01 | 2.81 | 1.94 | 0.51 / −0.23 mm |
| Stop at 2 mm at right angles | 2.12 | 2.43 | 2.91 | 2.39 | 0.23 / −0.07 mm |
| Stop at 1.5 mm at right angles | 1.62 | 1.99 | 2.91 | 2.02 | 0.28 / −0.19 mm |

- **The band behind the surface has to stay two to three times as deep as the frames disagree.** Shorter, and the surface sinks inward: the observations a little behind it are dropped while those in front of it all count. At 0.3 mm of disagreement (tracking) a cut at 1.5–2 mm costs nothing; at 0.9 mm (marker mode) it does.
- **Fading weights are worse than a stop** that gives the walls the same thickness (five other fades were tried; all worse where the poses disagree).
- **Along the view a stop shrinks faces that lie between voxel planes** by 0.05–0.1 mm a side (the 3 mm wall reads 2.80 mm), and on real scans it sinks the table. At right angles a thick wall is as today: a 7 mm wall reads 6.898 mm with both. Today's rule, seen at right angles, is a cut at 4 mm times the cosine (2.8 mm at 45°).
- **A plain stop below 1.73 voxels (√3) makes pinholes:** grid cells at the surface are left without all eight values, and marching cubes leaves them open. With the low weight those cells have their values again, and the walls read as with the plain stop.

*The five bracket scans* (matcher's depth, GPU path, with change A; medians):

| Mesh rule | True poses: more than 0.3 mm off / 95 % level / boss radius / boss tilt | Tracked: more than 0.3 mm off / 95 % level / median | Tracked: shaft radius / boss radius / boss tilt | Tracked: table offset |
|---|---|---|---|---|
| Today | 13.0 % / 0.50 mm / −0.13 mm / 4.4° | 19.9 % / 0.60 mm / 0.148 mm | +0.18 mm / +0.03 mm / 3.5° | 0.001 mm |
| 3 mm on both sides | 9.9 % / 0.41 mm / −0.25 mm / 3.9° | 15.3 % / 0.49 mm / 0.139 mm | +0.18 mm / −0.07 mm / 2.3° | 0.013 mm |
| 2 mm on both sides (B) | 7.8 % / 0.37 mm / −0.36 mm / 1.6° | 11.4 % / 0.43 mm / 0.124 mm | +0.14 mm / −0.18 mm / 1.4° | 0.058 mm |
| Stop at 2 mm at right angles, low weight | 10.5 % / 0.43 mm / −0.27 mm / 3.2° | 16.5 % / 0.51 mm / 0.138 mm | +0.10 mm / −0.10 mm / 1.7° | 0.006 mm |
| Stop at 1.5 mm at right angles, low weight | 10.5 % / 0.43 mm / −0.32 mm / 1.9° | 14.8 % / 0.50 mm / 0.131 mm | +0.07 mm / −0.16 mm / 1.6° | 0.014 mm |

- The plain stops read within 0.6 points of their low-weight forms (15.9 % and 14.4 % tracked).
- With the true depth at true poses the 1.5 mm rule leaves 1.4 % of the part more than 0.3 mm off (today 6.5 %, 2 mm on both sides 1.1 %), and the bosses' radius is right (−0.01 mm).
- Cuts at 1.25 and 1 mm at right angles read 13.3 % and 13.0 % tracked, with the table 0.022 and 0.038 mm off and the smooth scenes below worse.

*The sheet part* (thickness read: 2 mm flange / 1 mm rib / 3 mm rib):

| Mesh rule | True poses | Tracked |
|---|---|---|
| Today | 3.17 / 2.73 / 3.19 mm | 3.37 / 2.89 / 3.85 mm |
| 3 mm on both sides | 2.61 / 1.86 / 2.96 mm | 2.89 / 2.33 / 3.82 mm |
| 2 mm on both sides (B) | 2.07 / 1.23 / 2.89 mm | 2.73 / 1.95 / 3.78 mm |
| Stop at 2 mm at right angles, low weight | 2.72 / 2.04 / 3.06 mm | 3.02 / 2.46 / 3.82 mm |
| Stop at 1.5 mm at right angles, low weight | 2.48 / 1.72 / 2.95 mm | 2.90 / 2.24 / 3.79 mm |

*Smooth scenes and poor poses* (the end-to-end tests' mesh distance, median / 95 % level; the five 200-frame car-panel scans):

| Mesh rule | Full sphere | Car panel | Marker mode | Five car-panel scans: hole diameter / hole centre |
|---|---|---|---|---|
| Today | 0.061 / 0.256 mm | 0.083 / 0.389 mm | 0.489 / 1.478 mm | −0.90 / 0.33 mm |
| 3 mm on both sides | 0.061 / 0.259 mm | 0.083 / 0.383 mm | 0.516 / 1.722 mm | not run |
| 2 mm on both sides (B) | 0.065 / 0.301 mm | 0.086 / 0.401 mm | 0.607 / 2.469 mm | −0.87 / 0.42 mm |
| Stop at 2 mm at right angles, low weight | 0.061 / 0.257 mm | 0.083 / 0.393 mm | 0.508 / 1.562 mm | −0.91 / 0.33 mm |
| Stop at 1.5 mm at right angles, low weight | 0.062 / 0.263 mm | 0.086 / 0.413 mm | 0.552 / 1.772 mm | −1.01 / 0.40 mm |

The half-resolution sphere scan keeps its numbers with the last three rows (0.118 / 0.52 mm). 3 mm on both sides was run as three voxels there, the two right-angle rules as 2 and 1.5 voxels.

*Pinholes* (open boundary loops in the raw mesh of the tracked scans: the bracket's median of five / the car panel's median of five / the sheet part):

| Mesh rule | Open loops |
|---|---|
| Today | 1201 / 600 / 1232 |
| 3 mm on both sides | 2957 / not run / 3146 |
| 2 mm on both sides (B) | 8989 / 2553 / 9373 |
| Stop at 2 mm at right angles, plain | 1538 / 836 / 1528 |
| Stop at 2 mm at right angles, low weight | 1330 / 774 / 1343 |
| Stop at 1.5 mm at right angles, plain | 2299 / 1425 / 2332 |
| Stop at 1.5 mm at right angles, low weight | 1537 / 1116 / 1524 |

On the processing studio test's short recording (10 frames, 640x400, two seeds): today 57 and 66 loops, the plain stop at 1.5 mm 261 and 219, with the low weight 67 and 74.

*The test suite in the scratch copy:*
- **Plain stop at 1.5 mm:** GPU path 751 passed, 1 failed; CPU path 706 passed. The failure is `test_overlays` of the processing studio: one of the extra pinholes has a perimeter below 0.0005 mm, which the hole list rounds to 0.0.
- **Low weight at 1.5 mm:** GPU path all passed (752). CPU path 705 passed, 1 failed: `test_the_refined_mesh_is_closer_to_the_truth`, which sits on its limit there (maintenance plan, A6). The CPU path does not use the rule (its mesh model is Open3D's), and alone the test passed four times of four (twice with today's rule, once with each stop at 1.5 mm), its 95 % move 0.290–0.300 mm against the limit of 0.3 mm.
- **Low weight at 2 mm:** GPU path all passed. The CPU path was not run again.

*What it comes to:*
- **Stop at 2 mm at right angles, with the low weight: gains at almost no cost where measured.** The tracked bracket goes from 19.9 % to 16.5 % more than 0.3 mm off, the bosses' tilt from 3.5° to 1.7°, the 2 mm flange from 3.37 to 3.02 mm. The sphere and both car-panel sets keep their numbers. Marker mode's 95 % level goes from 1.48 to 1.56 mm, and the raw mesh has more pinholes (11 % on the bracket, 29 % on the car panel).
- **At 1.5 mm it gains a little more** (14.8 %, flange 2.90 mm) **and costs more:** marker mode 1.77 mm, the car panel's hole 0.11 mm smaller, the car panel's 95 % level 0.39 → 0.41 mm, more pinholes again (28 % on the bracket, 86 % on the car panel).
- **A plain 3 mm on both sides gets as far as the 1.5 mm rule** on the bracket and the sheet part and leaves the smooth scenes alone, with one number changed. It makes two and a half times today's pinholes, and marker mode reads 1.72 mm.
- **Every shorter rule shows the small bosses' shrinkage** that the swelling hides today: their radius with tracking goes from +0.03 mm to −0.07 … −0.18 mm.
- **Thin walls stay 0.9–1 mm too thick with tracking under every rule.** That part is the tracking model's 2 mm voxels.

**In the engine since 2026-11-14 (the owner's decision): the stop at 2 voxels at right angles with the low weight, automatic by mode.**
- **The setting:** `PipelineParams.thin_walls`, `[process] thin_walls` in the config file, and "Thin walls and edges" on the app's Settings page. `"auto"` (the default) turns the rule on for a scan without markers and leaves marker mode as it was, because of marker mode's cost above. `"on"` and `"off"` force it. `report.json` records the setting and the cut as `params.reconstruction`. The rule itself is `ConfidenceTsdf(back_mm=...)`; the cut is `BACK_VOXELS` (2 voxels) and the weight beyond it `BACK_WEIGHT` (0.001).
- **The CPU path is unchanged.** Its mesh model is Open3D's, which cannot take the rule, so the two paths differ in one more thing: a scan processed without a GPU keeps the thicker walls. `"on"` there logs a warning.
- **Guards:**
  - `test_the_mesh_model_keeps_a_thin_wall_thin`: the pipeline's own mesh model on a 2 mm wall reads 2.65 mm with `"off"` and 2.43 mm with `"auto"`; the test wants less than 2.48 mm.
  - `test_a_short_band_behind_the_surface_keeps_a_thin_plate_thin`: a 5 mm plate seen square on from both sides (2 mm voxels) reads 11.4 mm with the whole band and 5.4 mm with the short one, on the GPU and in numpy.
  - `test_thin_walls_follows_the_mode_unless_it_is_forced`.
- **Off is as before:** with the rule off, every channel of a fused volume is bit-identical to the engine before the change, on the GPU and in numpy (13 frames).
- **Speed:** one integration at 1280x800 with 1 mm voxels (Radeon 680M, a wall on a table, median of 108, three rounds) takes 22.0–22.2 ms before, 21.6–22.0 ms with the rule off and 21.9–22.5 ms with it on.
- **The scans again, with the engine itself:** the prototype's numbers.
  - Bracket, five scans: 10.5 % more than 0.3 mm off at true poses; tracked 16.2 % (the prototype's run read 16.5 %; the raw meshes have the same 1330 open loops), 95 % level 0.51 mm, median 0.138 mm, shaft radius +0.10 mm, boss radius −0.10 mm, boss tilt 1.7°.
  - Sheet part: 2.72 / 2.04 / 3.06 mm at true poses, 3.02 / 2.46 / 3.82 mm tracked.
  - End-to-end tests: full sphere 0.061 / 0.257 mm, car panel 0.083 / 0.393 mm. Marker mode is as before, 0.489 / 1.478 mm: the rule is off there.
  - Five car-panel scans: floor offset 0.002 mm, hole 0.91 mm small in diameter.
- **The vertex sigma keeps its coverage** (on the prototype's meshes, whose fused channels are the engine's): 2 sigma covers 99.2 % of the tracked bracket's vertices (99.2 % before) and 99.6 % at true poses (99.5 %), and 96.3 % on the car panel (96.2 %). The bracket's median sigma falls from 0.47 to 0.43 mm.
- **Suite after the change** (763 tests): GPU path 757 passed, 2 skipped; CPU path 708 passed, 51 skipped; and 4 failed on both, all in `test_app_cli.py`: the tests' stand-in for `PipelineParams` did not have the new field. With the field added, that file and `test_app_live.py` pass on both paths (66 tests each); the whole suite was not run again after that.
- **Every Standard, Best or Preview mesh of a scan without markers that was made on the GPU path before 2026-11-14 had the whole band.** Smooth scenes keep their numbers. Parts with edges and thin walls read a little thinner now, and small bosses about 0.1 mm smaller in radius; the fitted-shapes study was not measured again.
- **Still open:** with tracking, thin walls stay about 1 mm too thick (the tracking model's 2 mm voxels); a mesh model for the CPU path that can take the rule.

**Thin walls with tracking: the tracking model (study of 2026-11-14 in scratch copies of the engine; its last variant is in the engine since the same day, "In the engine" below).** With tracking a thin wall reads 0.3–1.2 mm thicker than at the true poses. The tracked mesh takes its walls from the tracking model: a frame is registered to the model's faces, so where the model's wall is too thick the cameras on its two sides stand too far apart.

*The tracking model on exact depth* (a wall on a table, 36 views, 1280x800; where the wall's face sits after both sides were seen, + = deeper than it is):

| Grid / band | 1 mm wall | 2 mm wall | 3 mm wall | 4 mm wall |
|---|---|---|---|---|
| 2 mm / 4 mm (the engine) | −0.53 mm | +0.15 mm | +0.40 mm | 0.00 mm |
| 1.5 mm / 3 mm | −0.27 mm | +0.25 mm | +0.01 mm | +0.01 mm |
| 1 mm / 4 mm | −0.65 mm | −0.41 mm | −0.04 mm | +0.02 mm |
| 1 mm / 3 mm | −0.46 mm | −0.25 mm | +0.10 mm | +0.01 mm |
| 1 mm / 2 mm | +0.06 mm | +0.04 mm | +0.09 mm | +0.01 mm |

Only a 1 mm grid with a 2 mm band holds every wall. A finer grid alone does not: the band has to be no wider than the wall.

*On scans* (matcher's depth, tracked, GPU path, Standard quality). The sheet part in three recordings (seeds 0–2; 2 mm flange / 1 mm rib / 3 mm rib, the range over the three) and the five bracket scans (share of the part more than 0.3 mm off, per scan and in the median; the part's mean offset, + = too fat):

| Tracking model | Sheet part: flange / 1 mm rib / 3 mm rib | Bracket per scan | Median | Mean offset |
|---|---|---|---|---|
| True poses (no tracking) | 2.72–2.85 / 1.96–2.04 / 3.04–3.06 mm | | 10.5 % | −0.02 mm |
| The engine (2 mm grid, 4 mm band) | 3.01–3.20 / 2.46–2.80 / 3.66–4.30 mm | 12 / 16 / 47 / 14 / 27 % | 16.2 % | +0.12 mm |
| 1 mm grid, 2 mm band | 2.69 / 2.22 / 2.99 mm (seed 0 only) | 45 / 19 / 52 / 63 / 24 % | 45 % | +0.12 mm |
| The engine's grid; nothing integrated from more than 2 mm behind a surface at right angles | 2.70–2.89 / 2.26–2.56 / 2.90–3.61 mm | 43 / 13 / 21 / 13 / 15 % | 15.1 % | −0.01 mm |
| The same, but only for voxels integrated 4 times already | 2.72–2.98 / 2.41–2.56 / 3.19–3.71 mm | 13 / 13 / 32 / 13 / 13 % | 13.3 % | +0.01 mm |
| The same with 16 times | 2.83–3.07 / 2.42–2.73 / 3.55–4.13 mm | 12 / 13 / 43 / 13 / 16 % | 13.0 % | +0.05 mm |
| The same with 1.5 mm and 4 times | 2.63–2.89 / 2.31–2.41 / 2.95–3.37 mm | 18 / 15 / 19 / 18 / 15 % | 17.7 % | −0.06 mm |
| More than 2 mm behind along the view, voxels integrated 4 times | 2.60–2.79 / 1.99–2.36 / 2.78–3.08 mm | 34 / 30 / 14 / 43 / 17 % | 30.1 % | −0.13 mm |

- **The 1 mm grid with the 2 mm band reaches the true poses' walls and is too fragile.**
  - The second frame of a scan is registered to a model made of one frame, with the camera already 5 mm on. That model gives the ray cast 80,000 pixels where the engine's gives 123,000, the registration's fitness is 0.47 against 0.80, and the pose lands 3–11 mm off and stays there.
  - On the bracket it slips by 6 mm at one frame of one scan and by 400 mm for a frame of another. It costs 15 ms a frame (tracking 78 ms against 63 ms) with a window of 512 voxels.
  - With a 3 mm band the start is sound and the walls are not (2.85 / 2.41 / 3.74 mm on seed 0).
- **The cut along the view repairs the sheet part and shrinks the bracket.** Seen at a slant, a voxel just inside a surface lies "deep" along the view, so thick bodies lose the votes that hold their surface: the bracket comes out 0.13 mm too thin and the car panel's hole 0.17 mm smaller.
- **At right angles, for every voxel, the bracket is right in four scans and slips in the fifth** (43 % in one run and 63 % in a second run of the same scan)**:** 5–6 mm at one frame, where the scene holds one direction weakly (the registration's weakest-to-strongest ratio is 4e-5 there, at the tracker's own limit of 5e-5; the engine's model sits just above it and gets through).
- **At right angles, only for voxels that were integrated 4 times, nothing slips** (largest step between two frames 1.5 mm; the engine's 2.0 mm), the bracket's fatness is gone and the thin walls are a third to a half of the way to the true poses' values.
  - The end-to-end tests' sphere and car panel keep their numbers (0.061 / 0.257 mm and 0.084 / 0.393 mm), and so do the five car-panel scans (hole 0.90 mm small in diameter, floor 0.001 mm).
  - The sphere scan without the gyro, which the tracker flags, drifts 109 mm instead of 59 mm.
- **What stays open:** walls under about 2 mm with tracking. They need the 1 mm grid, and a start and a hold in weak directions that take it.

**In the engine since 2026-11-14 (the owner's decision): the stop at right angles for voxels integrated 4 times.**
- **What it is:** `GpuTsdfVolume(back_mm=..., back_after=...)`, set through `FusionParams.gpu_back_voxels` (in voxels) and `gpu_back_after`. The pipeline's GPU tracking model asks for 1 voxel, 2 mm (`TRACK_GPU_BACK_VOXELS`), after 4 integrations; `report.json` records it as `params.tracking.back_mm`. The Open3D model of the CPU path has no such rule, and a tracker made without the pipeline is unchanged.
- **Guard:** `test_the_gpu_tracking_model_keeps_a_1_mm_wall_in_place` fuses a 1 mm wall into the pipeline's own tracking model from all round: its face sat 0.53 mm off before the change and sits 0.07 mm off now (the test allows 0.2 mm). Walls of 2 mm and more are as before on exact depth (the rule cuts at 2 mm); what it changes for them on scans comes from the noisy depth and the slanted views.
- **Off is as before:** with the rule off, the volume after 36 views is bit-identical to the engine before the change.
- **Time:** the bracket scan processed twice each way, in turn: tracking 66.8 and 66.2 ms a frame with the rule, 68.1 and 67.5 ms without; the whole frame 179 and 178 ms against 183 and 179 ms. No cost that can be measured.
- **The scans again with the engine itself:** the study's numbers. Bracket 13 / 13 / 33 / 13 / 13 %, median 13.4 %, mean offset +0.01 mm, largest step 1.5 mm; sheet part 2.72–2.98 / 2.41–2.56 / 3.19–3.71 mm; end-to-end sphere 0.061 / 0.257 mm and car panel 0.084 / 0.393 mm; five car-panel scans: hole 0.90 mm small, floor 0.001 mm; the sphere without the gyro 109 mm.
- **Suite after the change** (769 tests): GPU path 767 passed, 2 skipped; CPU path 717 passed, 52 skipped.
- **Every tracked number in this document that was measured on the GPU path before this change was made without it.** Smooth scenes keep theirs. A part with edges reads about 0.1 mm less fat (the bracket's mean offset +0.12 → +0.01 mm), and its small bosses 0.08 mm smaller in radius (−0.10 → −0.18 mm; at the true poses −0.27 mm).

Scripts: scratchpad `shape\` of the session of 2026-11-14 (`edit_track1.py`, `edit_track2.py`: the switches; `wall_proto.py`, `start_probe.py`, `tab_sheet.py`, `chain11.sh` to `chain18.sh`).

**Limits of this study:** one part with five seeds and one sheet part with one seed, all simulated; one scan pattern (an orbit); Standard quality only.

Scripts and rows: scratchpad `shape\` of the session of 2026-11-14 (`run.sh`, `run_variant.py`, `measure.py`, `table.py`, `traj.py`, `icp_bias.py`, `wall_proto.py`, `sheet.py`; `results\measure.jsonl`, `results\sheet.jsonl`). The prototype: `edit_back.py`, `edit_back2.py`, `edit_back3.py` (the rule, applied to a copy of `recon_tsdf.py`), `wall_recon.py`, `run_c.sh`, `loops_count.py`, `panel_table.py`, `summ3.py`; `results\wall_recon.jsonl`, `results\wall_recon2.jsonl`.

## Four line lasers (simulator, 2026-11-15)

The 4-laser variant adds a second vertical-line laser on the left of the cameras (`vertical_2`) and a second horizontal-line laser low on the right (`horizontal_2`). The software takes any set of line lasers: a name says the direction of the line (`vertical…`, `horizontal…`), the setting `[lasers] lines` says which lasers the build has, and the calibration fits one plane per laser. With the two standard lasers every result is bit for bit what it was (`tools/snapshot.py`: the code before this work against the code now, identical).

Everything below is from the simulator. Nothing here has been measured on hardware.

**How a point gets its plane.** The two lines of a direction are extracted together. Where they come closer than the extraction can separate, that stretch is dropped. Each remaining point is triangulated with both planes of its direction. For a vertical line, a plane is ruled out when its 3D point does not land on a vertical line in the other camera's image. A horizontal line runs along the camera rows, so that check cannot rule out a plane there. In both directions a plane is ruled out when the dot-stereo depth around the point (the median of five samples) is closer to the other plane's point by more than the depth's own tolerance. A point that still fits both planes is kept only where the two 3D points agree, which is where the sheets cross; otherwise it is dropped. A laser on the left of the cameras is measured from the right camera, because the camera farther from the sheet sees it at the better angle: this brought the error of `vertical_2` down to that of `vertical` (0.035 against 0.036 mm median on the bracket).

### Where the two new lasers point

Two sheets of the same direction that both cross the centre line meet at some distance, and around that distance their lines lie on top of each other in the image. The first build aimed all four sheets at the centre 250 mm out, which puts both meetings inside the working range. Eleven aims of the second pair were compared (`ohs/sim/nominal.py`, `LASER_LAYOUTS`) on 15-frame scans at 220, 250, 290 and 330 mm, two seeds each, 1280x800, true poses, so 120 frames per row:

| Aim of the second pair | Points per frame | Horizontal points dropped as merged, at the worst distance | Points on the wrong plane | Part covered | Walls covered |
|---|---|---|---|---|---|
| **Bracket** | | | | | |
| two lasers | 692 | – | – | 0.110 | 0.038 |
| both cross at 250 mm (`4-cross250`, the first build) | 977 | 24 % at 220 mm | 161, up to 1.4 mm off | 0.145 | 0.090 |
| both cross at 350 mm (`4-cross350`) | 1,095 | 15 % at 250 mm | 18, about 100 mm off | 0.145 | 0.076 |
| both cross at 450 mm (`4-cross450`) | 1,117 | 37 % at 290 mm | 27, up to 1.1 mm off | 0.144 | 0.085 |
| both cross at 600 mm (`4-cross600`) | 1,152 | 40 % at 330 mm | 33, up to 7 mm off | 0.139 | 0.085 |
| both straight ahead (`4-straight`) | 1,438 | none | none | 0.115 | 0.039 |
| both turned away, 2° and 5° (`4-apart`) | 1,493 | none | none | 0.115 | 0.038 |
| **`vertical_2` crosses at 250 mm, `horizontal_2` 5° away (`4`, chosen)** | **1,367** | **none** | **none** | **0.139** | **0.061** |
| `vertical_2` crosses at 450 mm, `horizontal_2` 5° away (`4-v450-hapart`) | 1,419 | none | none | 0.127 | 0.064 |
| **Sphere on a plane** | | | | | |
| two lasers | 833 | – | – | 0.176 | 0.125 |
| both cross at 250 mm | 1,263 | 19 % at 220 mm | 194, up to 1.9 mm off | 0.212 | 0.155 |
| both cross at 450 mm | 1,319 | 30 % at 290 mm | 173, up to 3.3 mm off | 0.213 | 0.172 |
| both turned away | 1,675 | none | none | 0.182 | 0.127 |
| **`vertical_2` crosses at 250 mm, `horizontal_2` 5° away (`4`, chosen)** | **1,562** | **none** | **none** | **0.222** | **0.153** |

"Covered" is the share of the true surface (sampled every 1 mm) with a laser point within 1 mm after the 15 frames. "Walls" are the part's faces more than 60° from the table plane. "None" for dropped points means under 0.5 %.

- **A crossing sheet lights the walls; a sheet that does not cross adds points on what the first pair already sees.** With the second pair crossing, the bracket's walls are covered up to 2.4 times as well as with two lasers. With the second pair straight ahead or turned away the walls gain nothing.
- **A crossing pair loses its points around the meeting distance,** in a band about 20 mm deep, and the points on the wrong plane are there too. For the horizontal pair the band costs 15–48 % of the horizontal points at one of the four distances, wherever the crossing is put.
- **The chosen aim crosses only the vertical pair.** The left laser is the mirror of the right one, so the two vertical sheets meet 254 mm from the left camera. The vertical pair loses 3–12 % of its points at 250–330 mm and under 1 % at 220 mm. The low laser points 5° down, away from the centre line, so the two horizontal sheets are 39 mm or more apart up to 450 mm from the camera (they meet about 640 mm out). In the 240 frames no point of either direction went to the wrong plane.
- **What it gives up:** on the bracket the walls are covered 0.061 against 0.085–0.090 with a crossing horizontal pair. On the sphere there is no difference (0.153 against 0.155). The aim is two numbers in `hardware/scanner.scad`. A build that scans mostly deep parts can cross both pairs at 450 mm and accept the band at 290 mm.
- **On a car panel** (3 seeds at 250, 290 and 330 mm, chosen aim): 1,406, 1,268 and 1,342 points per frame against 804 with two lasers (all distances), no horizontal point dropped, none on the wrong plane, median error 0.065–0.095 mm.

### A tracked scan with two and with four lasers

The sphere-on-plane scan through `process_recording` (realistic camera model, markerless tracking, default settings at full resolution, GPU path), once rendered with two lasers and once with four at the chosen aim:

| | 640x400, 24 frames | | 1280x800, 36 frames | |
|---|---|---|---|---|
| | 2 lasers | 4 lasers | 2 lasers | 4 lasers |
| Laser points | 11,883 | 23,680 | 26,357 | 53,484 |
| Laser points: median / 95 % error | 0.140 / 0.60 mm | 0.135 / 0.55 mm | 0.061 / 0.28 mm | 0.061 / 0.26 mm |
| Mesh vertices with a laser point within 1 mm | 4.9 % | 8.8 % | 5.1 % | 9.5 % |
| Mesh: median / 95 % error | 0.118 / 0.52 mm | 0.119 / 0.51 mm | 0.061 / 0.26 mm | 0.061 / 0.26 mm |
| Trajectory: median / largest position error | 0.16 / 0.22 mm | 0.16 / 0.28 mm | 0.11 / 0.15 mm | 0.13 / 0.20 mm |
| Trajectory: largest rotation error | 0.039° | 0.039° | 0.025° | 0.025° |
| Frames tracked | all | all | all | all |

- **Twice the laser points, as accurate as with two lasers,** and the lines pass over nearly twice as much of the surface.
- **The mesh does not change.** It is built from the dot-stereo depth; the laser points come into it only through "Refine with lasers", which is measured further down ("Refine with lasers with two and with four lasers").
- **Tracking is slightly less exact in position** (the largest error 0.05–0.06 mm larger; one scan each, so this may be noise): four lines cover more of the dot pattern that the depth is matched on.
- **Cost:** a frame takes 59 → 89 ms at 640x400 and 142 → 195 ms at 1280x800 (median per frame, Radeon 680M, nothing else running). The extraction of the lines is 10 → 28 ms and 16 → 62 ms of that; at 640x400 the tracker's share also rose by 7 ms in this run. Not optimised at that point; the next line is.
- **Cost after a first speed-up (2026-11-15, plan E8):** two helpers of the extraction do less work for the same output (bit for bit, `tests/test_vision_laser_speed.py`). Old and new code alternated on the recordings of the table above, Radeon 680M, GPU path, nothing else running:

  | | 640x400 | 1280x800 |
  |---|---|---|
  | Extraction of one image, alone, four lasers: old | 13.0 / 13.2 ms | 35.7 / 37.3 ms |
  | The same, new | 9.6 / 9.6 ms | 26.7 / 25.5 ms |
  | The same with two lasers (not changed) | 3.7 ms | 11.6 ms |
  | Whole frame, median, four lasers: old | 88.1 / 87.4 / 85.1 ms | 185.2 / 184.6 / 186.5 ms |
  | The same, new | 83.1 / 93.4 / 84.0 ms | 166.3 / 169.9 / 167.1 ms |
  | Of it, waiting for the lines: old | 33.1 / 29.2 / 32.1 ms | 61.9 / 49.6 / 59.8 ms |
  | The same, new | 26.8 / 29.1 / 27.9 ms | 40.9 / 41.1 / 44.0 ms |
  | Whole frame with two lasers, same session | 63.4 ms | 133.6 ms |

  At 1280x800 the frame is 17 to 19 ms shorter (about 10 %). At 640x400 the gain is about 3 ms and within the spread of these 24-frame runs (one new run read 93.4 ms). Four lasers still cost about 33 ms a frame over two at 1280x800 and about 20 ms at 640x400. What is left in the extraction: linking the candidates of each row into lines is a loop in Python (8.5 of the 25 ms per image at 1280x800, 3.4 of 9.6 ms at 640x400); two ways to do part of it in bulk were measured and would save 3 to 4 % of the frame, which was not worth their code. Assigning each point to its plane (`laser_points`) takes 6.4 ms a frame alone against 1.1 ms with two lasers; the frame does not wait for it (0.3 ms).
- **The same on a PC with a separate graphics card (2026-11-15):** the laptop with the RTX 3070 Ti Laptop GPU, `tools/frame_timing.py` at d4af63a, one run, on the charger in Windows' "Best performance" mode, with the laptop's usual background programs running. The script simulates its own short scans, so the figures are not those of the table above:

  | | 640x400 | 1280x800 |
  |---|---|---|
  | Extraction of one image, alone: two lasers | 3.1 ms | 9.5 ms |
  | Four lasers, old | 10.1 ms | 28.6 ms |
  | Four lasers, new | 7.6 ms | 18.7 ms |
  | Whole frame, median: two lasers | 23.3 / 22.8 ms | 43.5 / 41.8 ms |
  | Four lasers, old | 34.6 / 34.8 ms | 80.0 / 84.8 ms |
  | Four lasers, new | 33.4 / 32.2 ms | 61.2 / 65.0 ms |
  | Of it, waiting for the lines: old | 4.9 / 9.8 ms | 26.9 / 27.8 ms |
  | The same, new | 4.5 / 4.5 ms | 11.5 / 12.8 ms |
  | One extraction (four lasers) in a thread: alone / beside the matcher | 7.6 / 7.6 ms | 19.2 / 18.8 ms |
  | Two extractions beside the matcher, each | 14.8 ms | 31.9 ms |
  | One extraction in a second process: alone / while the matcher runs | 7.7 / 7.9 ms | 19.4 / 20.1 ms |

  There the frame is 19 to 20 ms shorter at 1280x800 (about a quarter) and 1 to 2 ms at 640x400. Four lasers still cost about 20 ms a frame over two at 1280x800 and about 10 ms at 640x400. Work on the processor is not slowed by the matcher there (on the Radeon PC it is, 4 to 7 times: `docs/MAINTENANCE_PLAN.md`, F8). What makes the frame wait on the laptop is that the two extractions hold each other up: each takes 32 ms when both run, against 19 ms alone, and the depth is done after 23 ms. The likely reason is the linking loop, which is Python code and so runs in one thread at a time; that is not measured apart. The script's "matcher alone" read 26.9 ms (640x400, four lasers) and 29.6 ms (1280x800, two lasers) where the matcher beside an extraction took 10.5 and 21.5 ms: most likely the graphics card clocking up after a pause. The script now runs the matcher a few times before each case; that is not rerun on the laptop.
- **Tracking no longer waits for the lines (2026-11-15, plan E9): the same results, and no shorter frame.** The frame used to collect the laser lines before it was tracked; now a laser thread collects them and triangulates while the frame is tracked. Old and new code alternated with `tools/frame_timing.py`, median ms a frame with four lasers:

  | | 640x400 | 1280x800 |
  |---|---|---|
  | RTX laptop, old | 32.0 / 30.8 / 32.0 / 32.3 | 51.0 / 61.7 / 53.9 / 56.4 |
  | RTX laptop, new | 31.0 / 32.5 / 33.8 / 31.4 | 52.5 / 62.0 / 53.5 / 55.8 |
  | Radeon PC, old | 126.1 / 120.3 / 121.7 / 131.1 | 226.3 / 219.2 / 214.2 / 227.5 |
  | Radeon PC, new | 128.8 / 109.1 / 117.5 / 113.8 | 213.0 / 226.3 / 208.9 / 212.9 |

  On both machines the wait for the lines goes to zero and the tracking takes about that much longer (laptop, 1280x800: wait 7–13 ms to 0, tracking 15–20 to 25–34 ms; Radeon: wait 72–80 to 0–5 ms, tracking 54–59 to 100–107 ms). The laser threads and the tracker do not really work at the same time: on the Radeon PC the processor is slowed by the GPU, and on the laptop the likely reason is that both are mostly short Python and numpy steps, which run in one thread at a time. The Radeon PC also ran slower in this session than in the morning's table above with the same code (two lasers 154 against 135 ms at 1280x800); the reason is not known, so compare within this table only. The snapshot of 90 frames (preview and full, GPU path) is identical before and after.
- **The linking loop compiled with numba (2026-11-15, plan E10; optional, the same lines without it).** The loop that links each row's candidates into lines was the Python part left in the extraction. With numba installed it runs as compiled code outside Python's lock. numba on and off in turns with `tools/frame_timing.py` on the Radeon PC, median ms a frame:

  | | 640x400 | 1280x800 |
  |---|---|---|
  | Four lasers, without numba | 112.8 / 113.1 / 117.1 / 119.7 | 213.9 / 208.3 / 214.2 / 212.7 |
  | Four lasers, with numba | 100.5 / 95.9 / 99.9 / 103.8 | 174.9 / 175.0 / 181.7 / 175.9 |
  | Two lasers (do not use it) | 70–83 | 146–152 |
  | RTX laptop: four lasers, without numba | 33.3 / 33.6 / 31.7 / 40.5 | 66.2 / 68.4 / 58.0 / 54.3 |
  | RTX laptop: four lasers, with numba | 34.0 / 32.0 / 31.0 / 31.5 | 56.4 / 51.7 / 42.9 / 44.2 |
  | RTX laptop: two lasers | 23–25 | 37–47 |

  That is about 36 ms a frame less at 1280x800 (17 %) and about 16 ms at 640x400 (14 %). The lines are the same bit for bit (`tests/test_vision_laser_link.py`; the 120 four-laser images of the two recorded scans). Starting numba costs about a second when the pipeline is built, and 6.4 s the first time ever, when the loop is compiled. On the laptop (the last three rows; one "on" and one "off" run of two rounds each, twice) the frame is about 13 ms shorter at 1280x800 in each pair of runs, about a fifth; the laptop itself ran 6 to 8 ms slower during the first pair, two lasers included, so compare the first two figures of a row with each other and the last two with each other. At 640x400 nothing stands out of the spread there. One image alone takes 13.5–14.1 ms with numba against 19.2–19.4 ms at 1280x800, and 5.3–5.4 against 7.7–7.9 ms at 640x400.

### Refine with lasers with two and with four lasers (2026-11-15)

What four lasers do for the mesh. The method is that of "Refine with lasers" above: the sphere on a plane and the car panel at 1280x800, fused at the true poses so that only depth errors count, once as simulated and once with a smooth bias put into the dense depth (0.1 % in scale and a 0.3 mm bowl, fixed to the camera); then `ohs.mesh.anchor_to_points` with its defaults. Errors are signed distances to the analytic surfaces. "Near the lines" are the vertices within 3 mm of a laser point. A throwaway script; the two-laser columns of the 36-frame scans repeat the figures of the earlier study (0.26 → 0.06 mm on the sphere, 0.23 → 0.04 mm on the panel).

With the biased depth, 150-frame scans:

| | Sphere, 2 lasers | Sphere, 4 lasers | Car panel, 2 lasers | Car panel, 4 lasers |
|---|---|---|---|---|
| Laser points in the cloud | 117,555 | 222,495 | 109,957 | 187,812 |
| Share of the surface near the lines | 16.0 % | 21.3 % | 12.0 % | 17.3 % |
| Median error near the lines, before → after | 0.275 → 0.048 mm | 0.283 → 0.042 mm | 0.248 → 0.043 mm | 0.255 → 0.039 mm |
| Mean offset near the lines, before → after | −0.272 → −0.027 mm | −0.284 → −0.029 mm | −0.265 → −0.065 mm | −0.283 → −0.042 mm |
| Share of the mesh moved | 16.4 % | 20.4 % | 11.3 % | 16.7 % |
| Mean offset of the whole mesh, before → after | −0.308 → −0.266 mm | −0.308 → −0.252 mm | −0.425 → −0.399 mm | −0.425 → −0.381 mm |

The same on 36-frame scans: the share near the lines is 7.4 % and 13.6 % on the sphere and 13.7 % and 18.2 % on the panel; the median error near the lines goes 0.260 → 0.061 mm and 0.275 → 0.056 mm on the sphere, 0.232 → 0.044 mm and 0.233 → 0.047 mm on the panel; the whole mesh's mean offset −0.280 → −0.259 mm and −0.280 → −0.240 mm on the sphere, −0.385 → −0.353 mm and −0.386 → −0.343 mm on the panel.

- **Where the lines pass, four lasers correct as well as two**, within a few hundredths of a millimetre either way.
- **They pass over more of the part:** 1.3 to 1.4 times the surface on the 150-frame scans and 1.3 to 1.8 times on the 36-frame ones, with 1.7 to 2 times the laser points. The two extra lines partly sweep where the first two already did.
- **Most of the mesh is still out of reach.** With four lasers the lines come within 3 mm of 14 to 21 % of the surface, so the whole mesh keeps most of its bias: its mean offset falls by 0.04 to 0.06 mm with four lasers, by 0.02 to 0.04 mm with two. A longer scan does not raise the share much on these scenes (with two lasers 7 → 16 % on the sphere and 14 → 12 % on the panel). Likely reason, not checked: the simulated hand keeps the scanner aimed at the middle of the part, so the lines stay near the middle of the picture, and a longer scan also sees more surface. Sweeping the lines over the part on purpose is what would raise the share; that is not simulated here.
- **Without a bias the stage changes nothing with either build:** 0.2 to 0.8 % of the mesh would move, under the 1 % at which the app applies it, and the error near the lines stays where it was (0.025–0.026 mm on the 150-frame sphere, 0.026–0.029 mm on the panel).
- **A 0.3 mm wave of 40 mm wavelength** on the mesh goes 0.113–0.116 → 0.091–0.097 mm near the lines with both builds, as before.
- **The laser points themselves** are as far from the truth with four lasers as with two (median 0.059 and 0.060 mm on the sphere, 0.052 and 0.053 mm on the panel), and about 1 % of them are set aside as outliers with either build (up to 2 % on the panel with four lasers).
- **Not covered:** tracked poses (the stage then also meets tracking errors), the bracket, and real scans, where the dense depth's real biases are what the stage is for.

### Calibration of four planes

The laser calibration fits the planes of a direction one after the other from the board points of all views (the plane with the most points first, then the next from what is left) and names them by where each sheet starts: a plane's intercept with the camera's x axis (vertical) or y axis (horizontal) is about where its laser sits. It needs nothing of the build's design, so other positions and aims calibrate the same way.

| Views (simulated board, 8 views, 200–390 mm) | Aim | Plane angle error | Plane offset error | Laser-point error from it, median / largest |
|---|---|---|---|---|
| rendered calibration set | `4` (chosen) | 0.002–0.008° | 0.009–0.037 mm | 0.010–0.037 / 0.07–0.19 mm |
| | `4-cross250` | 0.002–0.004° | 0.000–0.024 mm | 0.010–0.035 / 0.05–0.25 mm |
| | `4-cross350` | 0.004–0.009° | 0.023–0.043 mm | 0.018–0.048 / 0.07–0.22 mm |
| the app's virtual board camera | `4` (chosen) | 0.005–0.011° | 0.026–0.040 mm | 0.023–0.033 / 0.14–0.21 mm |

The ranges are over the four planes. The two-laser calibration's limits in the tests are 0.03° and 0.1 mm; all four planes are inside them, each under its right name.

- **Views at the meeting distance do no harm.** With three extra views at 254 mm, square to the camera, where both pairs of `4-cross250` lie on top of each other, the fit leaves out the points within 2 mm of the other sheet (51 vertical, 264 horizontal) and the planes are as good as without those views.
- **Views on one side of the meeting distance are enough to tell the planes apart.** With `4-cross250` and 8 views all nearer than 230 mm, or all farther than 280 mm, all four planes were found and named right. What the accuracy follows is the spread of distances: 0.009–0.015° and up to 0.9 mm largest point error from the 8 near views (191–218 mm), 0.003–0.007° and up to 0.33 mm from the 8 far views (295–392 mm). The app's guide asked for views nearer and farther than a meeting distance inside 150–450 mm until 2026-11-15, which was more than the fit needs; it no longer does (`docs/MAINTENANCE_PLAN.md`, F7).
- **A meeting distance outside 150–450 mm is not reported.** With the chosen aim the capture panel's note (the two lines run into each other about 254 mm away) speaks of the vertical pair only.

### Four lasers, one at a time (2026-11-16)

With each laser on its own Pico pin (firmware 1.2.0: `EMIT v h d v2 h2`; the second vertical laser on GP0, the second horizontal one on GP1), the app's laser calibration takes, for every view, the frame with all lasers on (the one the guide judged) and then one frame per laser with only that laser lit. It waits 3 camera frames after each switch (1 on the simulator), and the board must not move by more than 2 px meanwhile. The recording says which laser each frame shows (`meta["lit"]`). The solve uses only the one-laser frames and fits each laser on its own frames: no separation of a pair, no naming by where the sheets start, and no points left out near the other sheet of the pair.

Same simulated board camera and poses as the last row of the table above (aim `4`, 8 views):

| | Plane angle error | Plane offset error | Laser-point error from it, median / largest |
|---|---|---|---|
| All four lines at once (the table above) | 0.005–0.011° | 0.026–0.040 mm | 0.023–0.033 / 0.14–0.21 mm |
| One laser at a time | 0.003–0.006° | 0.007–0.022 mm | 0.010–0.024 / 0.09–0.17 mm |

- **About twice as accurate**, mostly because each laser keeps all its points: with all lines on, the extraction drops the stretches where the two lines of a pair run close, and the fit leaves out points within 2 mm of the other sheet.
- **In the app** (the simulator through the server, `tests/test_server_calibration.py`): each view has its four one-laser frames, deleting a view deletes them, the result lists each view once, and the four planes are within 0.2° and 0.5 mm of the truth by name. The session says `one_at_a_time` and its guidance asks the user to hold the board still.
- **When it is used:** four lasers configured, `[lasers] own_channels` true (the default) and either the simulator or a Pico with firmware 1.2.0 or later. With older firmware the lasers are calibrated together, as before, and the app says why. A build whose pairs share a pin sets `own_channels = false`.
- **Brightness per laser** (`[lasers] brightness`, percent of the family's level): sent to the Pico with the levels; with all at 100 the firmware gets the old three-level `EMIT`, so a 2-laser build and older firmware see no change (`tests/test_devices_pico.py`).
- **Not done:** the command line's `ohs calibrate lasers` still records all lines at once; nothing of this has run on the real scanner. The self-test checks each laser on its own since 2026-11-16 (802b442).

### Limits

- Simulator only. The real lasers' line width and speckle, and how well two lines of one direction can be told apart on a dark or shiny part, are not known.
- The two lasers of a direction share one switch channel and are on together.
- Around 254 mm the two vertical lines cross in the image. Those stretches are dropped (3–12 % of the vertical points at 250–330 mm). With the first build's aim, kept points near a meeting distance were up to 1.4 mm off; with the chosen aim none was seen in 240 frames, which does not prove there are none.
- The laser points do not improve the mesh by themselves; "Refine with lasers" uses them. With four lasers it reaches about a third to four fifths more of the surface, which on these scans is still about a fifth of it (above).
- Not measured: more than two lasers per direction; lines that are far from vertical or horizontal in the image; the CPU path's speed.

**Tests:** `tests/test_vision_laser.py` (two lines per direction: tilted, curved, gapped, crossing; the plane of every kept point against the truth), `tests/test_calib_laser_sets.py`, `tests/test_sim_board_camera.py`, `tests/test_sim_datasets.py`, `tests/test_sim_nominal.py`, `tests/test_e2e_scan.py` and `tests/test_track_curve_ba.py` (tracked scans with four lasers), `tests/test_server_calibration.py` and `tests/test_server_setup.py` (the laser set through the app).

**Suite with the variant in** (819 tests, the code of 2026-11-15 with the chosen aim): GPU path 813 passed, 6 skipped; CPU path 763 passed, 56 skipped. After the faster extraction and the change to the calibration guide (828 tests, commit c706c2b): GPU path 822 passed, 6 skipped; CPU path 772 passed, 56 skipped. With tracking no longer waiting for the lines (829 tests, commit bb5ca37): GPU path 823 passed, 6 skipped; CPU path 772 passed, 56 skipped, 1 failed: the calibration wizard's stereo step got 8 of the 10 views it wants in the time it allows, in a run that took a third longer than usual. That test passed twice on its own afterwards and does not use the changed code (`docs/MAINTENANCE_PLAN.md`, F10). With the linking loop compiled by numba (839 tests, commit 9b4cc6c, numba installed): GPU path 833 passed, 6 skipped; CPU path 783 passed, 56 skipped; the wizard test passed in both. After the night of 2026-11-15/16 (several scans, the faster point assignment, the trial exe; 859 tests, commit bb7b30c): GPU path 853 passed, 6 skipped; CPU path 803 passed, 56 skipped.

Scripts: scratchpad `lasers4\` of the session of 2026-11-14/15 (`study2.py`, `summarise2.py`, `compare_scans.py`; results in `s2\`, `s3\`, `cmp2\`) and `shape\one_side.py`.

## Aligning and merging two scans (simulator, 2026-11-15)

A first slice of M4 (`ohs/align/`, research plan 4.7): two scans of one part are aligned to each other from their meshes, and merged into one model from their raw frames. In the app since the same day as "Align two scans" on the project page (`docs/APP_API.md`, "M4 first slice"); the figures below are the engine's.

**How it aligns** (`ohs.align.align_pair`):

- **The table is set aside.** On a table scan one plane is most of the mesh: 93–97 % on the bracket and sphere scans, 65–68 % on the car panel. Left in, it decided everything: with the table, every first guess on the bracket came out about 100° wrong, with a *better* score than the truth (the tables lie on each other in any position). The largest plane, when it holds 30 % or more of the surface, is taken for the support; it and whatever lies behind it are removed (a mesh grows a fringe at a table's rim and underside: 1–7 % of what was left on the GPU path and 26–33 % on the CPU path, where it made the result unsteady), and the rest is matched as the part.
- **First guesses from the shape:** FPFH features matched by Fast Global Registration and by RANSAC with eight seeds. On the bracket the RANSAC guesses start 4–40 mm and 2–17° off, and FGR's is usually wrong altogether.
- **An ICP that is free in all six directions** from every guess. The ICP of Straighten keeps the starting pose along directions the surfaces pin weakly; with it the guesses ended 2–14 mm from the truth, each somewhere else. Free, eight of nine end at the same pose.
- **The pick and the checks:** the pose with the largest share of matching surface wins. It is accepted only if a second guess ended at the same pose, if no guess that ended elsewhere scores nearly as well (a symmetric part, a surface that slides on itself), and if the residuals within 3 mm stay tight. Otherwise it says in words what to do (a rough starting pose, more overlap).
- **Same setup:** when the part was not moved on the table between the scans, the two table planes are made to coincide (height and tilt) and the part is fitted once more along the three motions that are left. A first version ran an ICP on part and table points together and gave a different answer on every run (0.08° to 0.58° on the same pair).

**Alignment against the truth.** Two 60-frame scans of one scene from two sides, each fused at its true poses, so that each scan's world is exact and the true motion between them known. "Off" is how far the source scan's part points land from where the true motion puts them (median / largest).

| Scans | Sides apart | Part within 1 mm of the other scan | Part only: off, rotation | Same setup: off, rotation | Guesses agreeing |
|---|---|---|---|---|---|
| Bracket, 1280x800 | 101° | 76 % | 0.52 / 0.53 mm, 0.07° | 0.51 / 0.53 mm, 0.02° | 8 of 9 |
| Bracket, 1280x800 | 56° | 94 % | 0.19 / 0.31 mm, 0.15° | 0.20 / 0.31 mm, 0.14° | 9 of 9 |
| Car panel, 1280x800 | 102° | 97 % | 0.28 / 0.31 mm, 0.03° | 0.05 / 0.14 mm, 0.02° | 9 of 9 |
| Bracket, 640x400 | 101° | 48–49 % | 1.13 / 3.18 mm, 0.36° | 1.08 / 2.48 mm, 0.23° | 8 of 9 |
| Bracket, 1280x800 | 169° (opposite sides) | — | refused | refused | 1–4 of 9 |

- **It works from the shape alone** where a good share of the part is in both scans, in 1 to 5 s a pair, and it is as good as the meshes: about 0.2–0.5 mm on the bracket at full resolution, 0.05 mm on the car panel with the table's help, about 1 mm at 640x400 (2 mm voxels).
- **It refuses what it cannot decide.** Opposite sides of the bracket share too little: the best guess was 0.77 mm off, but only 1 to 4 of 9 guesses reached it and others scored as well, so the pair is refused with "the part fits in more than one position". Two scans of the sphere on its plane are refused the same way (the sphere is symmetric), and a table with nothing on it for having no part.
- **With tracked scans** the same pairs come out 1.86 mm (101°) and 1.46 mm (56°) from the motion between the first cameras, with the same share matching and the same tight residuals (0.16–0.18 mm). That figure holds each scan's own tracking error, which moves a scan's mesh in its own world; it is not the alignment's error alone.
- **Not the same on every run in the last digit:** Open3D's RANSAC shares one random generator between its threads. On the pairs above the accepted pose was the same in every run; the count of agreeing guesses on the refused pair varied (1 to 4).

**The merged model against the true surface** (`ohs.align.merge_scans`: every frame of both recordings fused into one model; bracket, 1280x800, true poses). The part is what lies above the table; the error is measured along each vertex's normal to the simulator's true surface; "good surface" counts vertices within 0.5 mm.

| | Part vertices | Median error | More than 0.3 mm off | Good surface |
|---|---|---|---|---|
| Sides 101° apart: scan A alone | 12,969 | 0.221 mm | 46.5 % | 10,433 |
| Scan B alone | 22,370 | 0.106 mm | 32.2 % | 19,121 |
| Merged at the true motion | 25,551 | 0.146 mm | 39.0 % | 21,383 (1.12 times B) |
| Merged at the aligned pose, same setup (0.53 mm, 0.02° off) | 25,358 | 0.219 mm | 41.9 % | 20,772 (1.09 times) |
| Merged at the aligned pose, part only (0.48 mm, 0.07° off) | 22,365 | 0.168 mm | 33.4 % | 18,989 (0.99 times) |
| Sides 56° apart: scan B alone | 22,302 | 0.136 mm | 33.7 % | 18,954 |
| Merged at the true motion | 23,596 | 0.161 mm | 39.7 % | 19,644 (1.04 times) |
| Merged at the aligned pose, same setup (0.45 mm, 0.14° off) | 23,961 | 0.201 mm | 41.8 % | 19,880 (1.05 times) |
| Merged at the aligned pose, part only (0.22 mm, 0.15° off) | 29,645 | 0.366 mm | 52.6 % | 22,571 (1.19 times) |
| Opposite sides (169°): scan B alone | 12,864 | 0.249 mm | 48.3 % | 10,036 |
| Merged at the true motion | 21,516 | 0.369 mm | 52.3 % | 16,841 (1.61 times A) |

- **The merge itself is right:** at the true motion the merged model holds the union of the two scans, once, and its error lies between the errors of its two inputs.
- **It does not yet give a better model than the better single scan.** On these pairs scan B had already seen most of what scan A saw, so the union adds 4–12 % of good surface, and the merged surface takes on some of the worse scan's error. At the aligned poses (0.2–0.5 mm and 0.02–0.15° off) the result is less steady: with the table's help the share more than 0.3 mm off rises by 2–3 points; without it one merge lost 3,200 vertices of the part (101°) and one doubled part of the surface (56°: 29,645 vertices where 23,600 belong, half of them more than 0.3 mm off).
- **Where merging pays is opposite sides:** 1.6 times the good surface of one scan. That pair the alignment refuses, so it needs a rough pose from the user or a third scan in between; and its merged surface is worse than either scan alone (median 0.37 against 0.22–0.25 mm), because the bracket's thin walls are now seen from both sides (`ROADMAP.md`, "walls under about 2 mm").
- **With tracked scans** (101° apart) there is no gain: 16,867 good vertices merged at the motion between the first cameras, 17,123 at the aligned pose (same setup), against 17,286 for scan B alone; the share more than 0.3 mm off is 45–54 % against 34 %.
- **A refinement of all frames of both scans together was tried the same day and is not kept.** It was Straighten across scans: pairs of frames of different scans that look at the same place, their depth clouds matched by ICP from the aligned poses, one pose graph over both scans' keyframes. It ran, and found nothing to correct: at the aligned poses the frame pairs already fit each other within 0.02–0.03 mm (scans fused at true poses, where scan B's frames are 0.55 mm from the truth) and 0.04–0.06 mm (tracked scans, whose frames are 0.8–1.0 mm from the truth). With Straighten's own limits it found 1 frame pair between scans 101° apart and 4 at 56°; with wider ones (75°, 100 mm) 11–12, all accepted. A single frame's cloud is mostly table, as a mesh is, and does not see a part that sits half a millimetre off. An independent review of the module also found that one surviving link could have moved a whole scan. The module is not in the repository.
- **With tracked scans the aligned pose is worse for the merge than the motion between the first cameras:** 54.3 % against 45.1 % of the merged part more than 0.3 mm off (101° apart) and 57.6 % against 39.5 % (56°), although the two meshes fit each other closely at the aligned pose (0.16–0.18 mm). Each tracked scan's mesh is a little displaced and swollen in its own world, and fitting the two meshes to each other puts those errors into the pose.
- **What this says:** the limit is not the pose but each scan's own surface, which is off in a way that depends on where the scan looked from (46 % and 32 % of the part more than 0.3 mm off in these two scans). Merged, the two versions of a shared face are averaged. What should help, in this order: a fusion that takes each face from the scan that saw it best (research plan 4.5.1), longer scans, and for scans with little in common a rough pose from the user (tags, picked points), which the alignment already accepts. For a part that was moved, the frames' table has to be taken out before the merge, which is not built: today both tables are fused.

**Reading the error figures:** the single scans here are short (60 frames, about a 40° sweep) and the error is taken along the normal at every vertex of the part, edges included, so the shares more than 0.3 mm off are larger than the bracket figures elsewhere in this file. Compare within this section.

**A part that was moved** (built the same day): given each scan's support plane, the merge leaves out every frame's depth and laser points on or behind it and up to 2 mm in front (`FramePipeline.process(clip_plane=...)`). On the bracket pair the merged model then has 4,332 vertices instead of 106,441 and nothing within 5.6 mm of the table; the part's bottom edge goes with it (the margin, and the rim the gap guard takes next to missing depth). **A rough pose from picked points** (`pose_from_point_pairs`): three or more matching points, fitted rigidly; points picked a millimetre or two off give a start the alignment refines to the same result as before.

**In the app** (tried by hand on the two tracked bracket scans at 1280x800, through the real server): the page aligned them from the shape (accepted: 80 % of the part within 1 mm, 0.23 mm RMS, 8 of 9 guesses agreeing), showed both meshes on each other in two colours, merged 120 frames with a progress count into a new scan of 561,465 vertices, and the project listed it as merged from the two. In demo mode: picking three point pairs on the two views, aligning from them, the not-accepted case with its prompt to pick points, and the confirmation before a merge at a pose that was not accepted. Not tried: real scans, a cancelled merge on the real server, small screens.

**Several scans at once** (`ohs.align.align_many`, 2026-11-16): every pair is aligned; the first scan stays where it is and each other scan is placed through the chain of best links that reaches it (a maximum spanning tree by overlap); where the accepted links form a loop, all of them are solved together in a pose graph (Open3D's global optimisation, as in Straighten) and a link that does not fit is left out. Front, side and back of the bracket (scans at 20°, 110° and 200°, fused at true poses):

| | 1280x800 | 640x400 |
|---|---|---|
| Front–back pair on its own | refused (too little in common) | accepted, 37 % overlap |
| How the back scan is placed | through the side scan | through the side scan, the front–back link checks the loop |
| Side scan: part points off (median / largest) | 0.51 / 0.53 mm | 1.13 / 1.19 mm |
| Back scan: part points off | 0.80 / 0.90 mm | 1.69 / 2.17 mm |
| Time for the three pairs | 6.4 s | 4.9 s |

Merged from all three at 1280x800, the part has 22,556 vertices within 0.5 mm of the true surface, against 10,433 for the front scan alone and 19,121 for the best single scan (the side): opposite sides are now covered without a picked start, at the price of the back scan's placement error (0.8 mm) adding to the errors of the chain. In the tests the same three scans at 640x400 are placed to 1.2 and 1.8 mm on the GPU path and 0.4–0.6 mm on the CPU path, with the merged part 1.2 times the largest single scan's.

One warning from building it: on a made-up box-shaped part without a table, seen from three sides so that each scan sees only some faces, one run accepted a pose 7.5 mm and 4.5° off, with two guesses agreeing (the faces can slide along each other, and Open3D's guesses differ from run to run). The real scans did not do this, but a part made of a few flat faces is where the alignment is weakest: look at the scans together before merging.

**Several scans in the app** (tried by hand the same night, through the real server): front, side and back of the bracket listed on the page, aligned at once ("All 3 scans placed through 2 links.", the back scan placed through the side scan, front–back not accepted at 50 % overlap), shown in three colours on each other, and merged (180 frames) into a new scan of 697,072 vertices that opened in the viewer. Not tried in the browser: picking points on a pair of three scans, a scan that is not placed (both covered by the demo's tests only).

**Would taking each place from the scan that saw it best beat the averaging?** (a study, 2026-11-16, scratch script `fusion_study.py`; nothing in the engine). Two bracket scans in one world; where both saw a place (vertices within 1 mm), only one copy is kept: the one with the smaller sigma, or from the scan that looked more squarely at it, or, as an upper bound no rule can pass, the one with the smaller true error. Share of the part more than 0.3 mm off:

| | 101° apart, true poses | 56° apart, true poses | 101° apart, tracked |
|---|---|---|---|
| Scan A alone / scan B alone | 46.5 % / 32.2 % | 46.5 % / 33.7 % | 50.5 % / 34.3 % |
| Merged model (averaged in the fusion) | 39.0 % | 39.7 % | 45.1 % |
| Best by sigma | 41.3 % | 43.4 % | 44.3 % |
| Best by viewing angle | 41.2 % | 44.1 % | 43.8 % |
| Best by true error (the upper bound) | 40.1 % | 41.9 % | 42.7 % |

Even the upper bound is no better than the averaging, so the answer is no, and it is not built. Where two scans saw the same place, they are mostly wrong in the same way: every row has the same mean offset of the part, −0.14 to −0.19 mm with true poses (the surface lies a little inside the part), which is the fusion's and the bracket's thin walls' error (`ROADMAP.md`, "walls under about 2 mm"), not a scan's view. A merged model gains coverage, not accuracy; making it more accurate means making each scan's surface more accurate.

**Scans of different calibrations** (2026-11-16): each scan's frames now go through a pipeline of its own calibration and fuse into one model that is passed from pipeline to pipeline (the models take a new camera with `set_camera`; `FramePipeline(model=...)`, `release_model()`); the grid is the finest of the scans' own. The models were checked first: frames of a second camera (another size, focal length and centre) fused after a first camera's give exactly the voxels, bit for bit, that a model made for the second camera gives, on the GPU, in numpy and in Open3D's model. Then the bracket: scan A at 640x400 and scan B (110°) recorded with another camera at 800x500, merged at the true motion on a 1.6 mm grid:

| | GPU path | CPU path |
|---|---|---|
| Part surface (points at 1 mm): scan A / scan B / merged | 2,319 / 5,748 / 6,322 | 2,164 / 5,520 / 6,291 |
| Scan A's part to the merged one (median / 80 % / 95 %) | 0.81 / 1.08 / 2.03 mm | 0.79 / 1.06 / 1.88 mm |
| Scan B's part to the merged one | 0.70 / 0.92 / 1.19 mm | 0.68 / 0.89 / 1.10 mm |

The figures are of the same kind as the two scans of one calibration (each scan's part where the merged one has it; a finer grid than scan A's own, so its points lie a little further from the merged surface). Merges of scans of one calibration are unchanged (same code path, same grid). One scale (`pipeline.scale`) applies to every scan.

**Tags as a start** (`ohs.align.tags`, 2026-11-16): the AprilTags a scan saw are found in its recording at the poses of its mesh (each view triangulated, snapped to the printed square, the median of the views kept), and the corners of the tags two scans share give the start, which the alignment refines from the shape. On two scans of the simulated sphere on its tagged table, 150° apart (640x400, 60 frames each, true poses):

| | GPU path | CPU path |
|---|---|---|
| Tags found (worst corner off the truth) | 5 and 4 tags, 0.74 mm | 5 and 4 tags, 0.74 mm |
| Start from the three shared tags (corners' RMS; the tags' corners then land) | 0.41 mm; 0.35 mm off | 0.44 mm; 0.32 mm off |
| From the shape alone | refused: fits in more than one position | refused |
| From the tags, refined (the tags' corners land) | accepted, 0.26 mm off | accepted, 1.02 mm off |
| Through the server (pose error) | 0.33 mm, 0.04° | 0.87 mm, 0.04° |

Two things were needed besides the tags. A start from tags is kept along the directions the shape pins only weakly (`AlignParams.start_weak_rel`, 3 % of the strongest): free, the refinement spun the sphere 139 mm (at the tags) away from a 0.35 mm start, to where the two partial views overlap most; with Straighten's 0.3 % the step on the shared table still drifted 13 mm, at 3 % it stayed and fitted better (33 % against 31 % overlap). Points picked by hand are not kept that way: they are a few millimetres off, and a rough start held along a weak direction stayed 1.9 mm off on the made-up part. And the tags must be looked for in enough frames: tags at the edge of the view are in 2–3 frames of 60, so 24 frames found one shared tag and 120 (every frame here, 1.3 s a scan) three. Not tried: real tags, tags on the part itself with the part moved, a marker-mode scan's own map (read, not measured).

**Sure or not sure** (2026-11-17, `docs/MAINTENANCE_PLAN.md`, F11; the owner's direction: we cannot know what the parts will be, so find the pose from whatever angle when enough is shared, and when not sure say so and let the person align). The engine had accepted a wrong pose: the 640x400 bracket scan against the bracket's true shape, with 33 stray points left on the table, turned 180° and 105 mm off, because 8 of 9 first guesses fell into that pose together and "two guesses agree" cannot catch that. `align_pair` itself now has four things, and the paragraphs "How it aligns" above describe the engine before them:

- **A table test** with the three guards of `ohs/mesh/support.py`: a large plane is the table only if something stands on it, no more of the scan lies well behind it than in front (more than twice the 1.5 mm band, so that a noisy table is not taken for a face), and the part does not go on behind it beside what stands on it. A plane that fails is a face of the part and stays; the next largest plane is tried once. (A block scanned all round had lost itself: its top face, 31 % of the surface, was taken for the table.)
- **Stray bits dropped:** the part's points joined over gaps of 3 voxels form pieces, and pieces under 10 % of the largest are left out of the matching (not out of any mesh).
- **Turned starts:** the best pose is refined again from itself turned half round about each principal axis of either part, and a quarter round both ways about the thinnest direction (8 starts).
- **A rule for sure.** From the shape, a pose is accepted only when overlap is at least 15 % and the fit at most 0.7 mm (as before), every other pose scores under 0.9 of it, and two independent starts reached it (a turned start counts only when it found the pose from elsewhere). Otherwise the answer is "not sure", with the reason in words and up to 4 poses to look at. Picked points are checked against the shapes too: "not sure, check your points" when another pose scores 1.25 times better or the refinement pulls the scan more than 10 mm from them. A start from tags is not overruled (a sphere turned round always overlaps more of itself).

What the margin of 0.9 rests on: on every pair that was found, the next pose scored 0.24–0.79 of the right one; on a ball, a sphere and a plain plate 0.93–1.0.

| Case (engine defaults; time per alignment) | Before | After |
|---|---|---|
| Bracket 640x400, 110° → 20°, part only | accepted, 1.18 mm / 0.38°, 1.4 s | accepted, 1.14 mm / 0.35°, 3.9 s |
| The same pair, same setup | accepted, 1.12 mm / 0.23°, 1.3 s | accepted, 1.10 mm / 0.22°, 3.7 s |
| 200° → 20° | accepted, 1.94 mm / 0.45°, 1.5 s | accepted, 1.90 mm / 0.47°, 3.5 s |
| 200° → 110° | accepted, 0.67 mm / 0.42°, 1.3 s | accepted, 0.61 mm / 0.40°, 2.5 s |
| Three scans at once (110° and 200° placed in 20°) | 1.19 and 1.74 mm, 4.3 s | 1.17 and 1.68 mm, 10.1 s |
| Sphere 150° apart, from the tags | accepted, 0.27 mm, 0.05 s | accepted, 0.18 mm, 0.8 s |
| Sphere, shape only | refused (more than one position) | not sure (rival) |
| The project's 1280x800 scans, all 6 pairs, part moved | all accepted, 0.43–0.78 mm, 1.4–2.2 s | all accepted, the same errors, 2.6–3.6 s |
| Bracket 1280x800 against its true shape, stray points left in | refused (one guess) | not sure, the right pose first (0.30 mm) |
| **Bracket 640x400 against its true shape, stray points left in** | **accepted wrong, 104.8 mm / 179.8°** | **not sure, the right pose first (0.34 mm)** |
| Ball against itself turned, 3 poses | accepted (the points were identical) | 3 of 3 not sure, the right pose first |
| Thin plate (1 mm) on its table, against itself turned and scan against scan | refused: too little of the part | the same: the plate lies within the table's band |
| Front → side at 12 random poses | 12 of 12 right | 12 of 12 sure and right |
| Side against itself at 24 random poses | 24 of 24, 0.6 s each | 24 of 24 sure, 2.5 s each |

- **No wrong pose is accepted in any case measured, and no pair that was accepted became "not sure".** The poses of accepted pairs are the same to 0.01–0.06 mm.
- **The price is time:** an alignment from the shape takes 1.5 to 4 times as long (the turned starts), the three bracket scans at once 10 s instead of 4 s.
- **First guesses stay at 8.** With 16 the 1280x800 scan-against-reference case becomes sure, with 24 both do (3 and 7 starts reach the right pose), at about 3.5 times the old time instead of 2; every scan pair was already sure and right with 8. The comparison with a reference model keeps its 24 (`ohs/inspect/reference.py`, margin 0.97: the test block scored 0.89–0.92 turned round).
- **The robust final fit stays.** Against a least-squares polish on 6 scan pairs it was better on all 6 (median 0.70 against 1.10 mm).
- **In the app:** a pose the engine was not sure of is labelled "Not sure" with the engine's sentence and the poses it had in mind, each to look at laid over the first scan and to choose; a merge is offered only on a pose the engine was sure of, one the person chose, or one from their own points (after a confirmation). For three or more scans the verdict names the pairs it was not sure of and the page leads to picking points; choosing between poses there is not built.
- **Tried through the real server and page** (the tracked 1280x800 scans of the test projects): bracket front + side accepted as before (80 % of the part within 1 mm, 0.23 mm RMS, 7 of 9 guesses, 4 s); front + back accepted at 50 % overlap, and with all three at once the front–back link agrees with the chain through the side scan to 0.25 mm (11 s for the three pairs); the two ball scans are accepted from their tags ("The shapes alone fit about as well another way: the tags decided.") and from the shape alone are "Not sure" (turned round fits 95 % as well, 55 mm away) with four poses listed; "Look" shows a pose over the first scan (pose 2 visibly wrong, the tables crossing), "Choose this pose" marks it as the person's choice, and without a choice the Merge section offers nothing. Not tried: a merge at a chosen pose, picked points that the shapes contradict.
- **Limits:** the thresholds rest on simulated parts; Open3D's RANSAC is not repeatable, so a pair near the limits can be sure on one run and not sure on the next; a thin plate (1 mm) lying on its table goes with the table for the alignment and picked points do not help (tags, or a scan of it standing up, do).
- Tests: `tests/test_align_sure.py` (10); scripts and logs in the scratchpad `f11\` of the session of 2026-11-17 (`measure.py`, `compare.py`, `lsq.py`).

**Limits:** no Poisson meshing in a merge; simulator only. (A part's own flat face of 30 % or more of a scan made without a table was taken for the support until 2026-11-17; see "Sure or not sure".)

**Tests** (`tests/test_align.py`, 10 tests, both compute paths; `tests/test_app_pipeline.py` for the clip plane): a known motion of 100° found again to under 0.1 mm on a made-up part, with and without the table; a part turned over (each scan its own table); a rough pose given; a symmetric part, a bare table, two unrelated shapes and a single unconfirmed guess refused in words; a detail scan inside a larger one; the two simulated bracket scans aligned and merged, the merged part larger than either scan and in the place of both; Poisson refused (and, since 2026-11-16, scans of different calibrations merged instead of refused; `tests/test_track_recon_tsdf.py` for the models' change of camera). An independent review of the first version found eleven defects; the ones that could accept a wrong pose are fixed (the fit check could not fail, one guess alone was accepted, a pose 2–10 mm away never counted as a rival). On the laptop's NVIDIA GPU the tests pass with the same figures to the last digit shown (three runs). Full suites with the slice in (847 tests, commit 493d127): GPU path 841 passed, 6 skipped; CPU path 791 passed, 56 skipped.

Scripts: scratchpad `merge\` of the session of 2026-11-15 (`eval1.py`, `merge_eval.py`; results `w\final_matrix2.log`).

## A trial exe (2026-11-16)

The engine and the app packaged as a Windows program with PyInstaller (`software/packaging/build_exe.py`; plan M6), built on the Radeon PC from the engine's environment:

- **Size and time:** a folder of about 720 MB with `ohs.exe` (Open3D, OpenCV, SciPy, PyOpenCL, numba and the app's server and window included), built in about 5 minutes. No installer yet; compressed it would be smaller, not measured.
- **It works as `ohs` does:** `ohs.exe bench` on the GPU tracked 30 of 30 frames with the same accuracy as from Python (trajectory 0.09 mm median, depth 0.308 mm at full quality; 0.14 mm and 0.356 mm at preview); `ohs.exe app` served the page and the API, and an alignment of two scans through it gave the same verdict as from Python (80 %, 0.23 mm RMS, 8 of 9). A four-laser recording processed with `ohs.exe process` gave the same mesh and laser points as from Python (compared sorted, every coordinate equal).
- **numba inside the program:** the first four-laser scan compiled the line linking (7.2 s for 24 frames, about 3.4 s of it compiling); the next one used the compiled code cached in the user's cache folder (3.8 s), as expected from numba's code.
- **Starting the app from the program:** 0.88 s until the app accepts a connection and 1.59 s until its first status message (the bench, run from the program). The bench could not time it in the first build: it launched the app with Python's `-c`, which a packaged program does not take; it now launches `ohs.exe app` itself.
- **Not done:** an installer, signing, the version in the file's properties, a test on a PC without the development tools, the window (pywebview) seen opening.

**A smaller build (the same day).** `build_exe.py` now leaves out what neither the engine nor the app uses: Open3D's rendering resources (42 MB, only for Open3D's own windows), its PyTorch ops library, examples and ML code (`packaging/hooks/hook-open3d.py`); OpenCV's ffmpeg library for video files (29 MB; the scanner grabs camera frames and reads images; `hook-cv2.py`); numba's tests (`hook-numba.py`); plotly, dash, IPython, jedi and the rest that Open3D's web viewers pull in (about 80 MB; `import open3d` takes three names from plotly and dash, so the program answers them with empty stand-ins, `rthook_stub_viewers.py`); SciPy's stats, io, signal, integrate, interpolate, cluster and odr; Pillow's AVIF codec. Numba cache files from the source tree are no longer copied in.

| | Folder | Files | Zipped (deflate) |
|---|---|---|---|
| Everything (before) | 706 MB | 6,645 | 266 MB |
| Smaller build | 522 MB | 314 | 184 MB |

- **Same results:** `ohs.exe bench` on the GPU at both qualities gives the same figures as the full build (30 of 30 frames; 0.09 / 0.308 mm and 0.14 / 0.356 mm). A four-laser recording processed with it gives the same mesh vertices (75,268) and laser points (23,680) as the full build and Python, compared sorted, and the same trajectory file byte for byte; numba compiles and caches as before. The app serves the page, `/api/health`, settings and projects, and an alignment and merge of two bracket scans through its API gives the same verdict, transform and merged scan (146,421 vertices) as the full build. On the CPU path the two builds differ only within that path's run-to-run spread.
- **With the left-out modules blocked**, the test suite passes from Python (759 passed, 54 skipped; slow tests 53 passed, 3 skipped).
- **What it gives up:** Open3D's own windows and web viewers, and OpenCV's video files and streams; the engine uses none of them. What is left is mostly Open3D.dll (157 MB), OpenCV (108 MB), numba's LLVM (115 MB) and two OpenBLAS copies (20 MB each); cutting those needs custom builds of the libraries, or the program without numba (about 120 MB less, four-laser line linking in Python).
- **Not checked:** the window (pywebview) opening, a real camera through the program.

**The window, and a build with thin walls (2026-11-16, later).** Built again from main at 785f41a
(thin walls seen from opposite sides):
- **The window did not open in either earlier build.** `ohs.exe app` printed "module 'webview' has no
  attribute 'create_window'" and opened the browser instead. The launcher imports pywebview by
  name, and pywebview imports its Windows backend (WinForms through pythonnet) the same way, so
  PyInstaller packed neither: only pywebview's data folder came along, and Python took it for an
  empty package. `build_exe.py` now names the package for PyInstaller (`--hidden-import webview
  --collect-submodules webview`).
- **With that, the window opens.** It is titled "OpenHandScan Studio", 1440×900, with its WebView2
  process. It shows the Projects page with the user's projects. Closing it stops the app as
  designed ("OpenHandScan Studio stopped.", the WebView2 process gone, the port free).
- **Size:** 525 MB, 325 files (pythonnet and the WinForms backend add 2 MB).
- **Same results:** the bracket recording s0 through `ohs.exe process` against Python from the
  checkout gives the same trajectory and laser points byte for byte, and the same mesh (461,696
  vertices, 910,618 faces; vertices, normals, sigma and faces equal when compared sorted). The
  report gives `opposite_mm` 1.0 in both.
- **Still not checked:** a real camera through the program, a PC without the development tools.

**Built again (2026-11-22)** from main at 3aa0cc3, with everything since (the UX review's pages,
the trash, the remake staging, the log, the engine's new settings, all off):
- **Size:** 526 MB, 329 files; `ohs.exe --version` gives 0.3.0.
- **The bench from the program** (GPU, full quality, 30 frames): 30 of 30 tracked, trajectory
  0.09 mm median, depth 0.308 mm median, the same as in the earlier builds and from Python.
- **The app from the program**, on a test data folder: the window "OpenHandScan Studio" opens;
  the simulator connects; a live scan tracks ("Tracking OK", 713 frames in 24 s); Stop saves it
  and Make mesh runs on the GPU to a finished mesh (713 of 713 frames tracked, calibration
  valid); the scan moved to the trash and put back returns to its place in the project; the
  log's file is served; the result viewer shows the mesh with the toolbar inside the 3D view.
- **Still not checked:** a real camera through the program, a PC without the development tools,
  an installer and signing (not made).

## The confidence model on the CPU path: measured, not adopted (2026-11-16)

Without a GPU the mesh comes from Open3D's model, which has no short band behind the surface
(`ROADMAP.md`, "models for the CPU path that take a short band"). The engine's confidence model
has a numpy twin with the band (`PipelineParams.recon = "confidence"`). One bracket scan (640x400,
60 frames, 20°, fused at its true poses), part vertices against the simulator's surface (scratch
script `cpu_recon_study.py`):

| | Integration a frame | Part vertices | Median off | More than 0.3 mm off | Mean |
|---|---|---|---|---|---|
| CPU path, Open3D's model (today) | 8 ms | 4,349 | 0.477 mm | 66.0 % | −0.414 mm |
| CPU path, confidence model in numpy | 140 ms | 4,122 | 0.459 mm | 64.0 % | −0.352 mm |
| GPU path, Open3D's model | 11 ms | 8,138 | 0.517 mm | 81.2 % | −0.506 mm |
| GPU path, confidence model (today) | 6 ms | 7,763 | 0.510 mm | 80.4 % | −0.473 mm |

Two points better for 18 times the integration (a 600-frame scan: about 80 s more), so it is not
made the default; the CPU path's error is mostly its depth, not the band. (The GPU path's shares
are not comparable with the CPU path's: its mesh covers almost twice as much of the part,
including the thin edges where the errors are largest.) What the band does for tracking on the
CPU path (Open3D's tracking model, 8 mm) is not measured here and stays open.

## A finer tracking model for thin walls: studied, not adopted (2026-11-16)

The question was whether a second, finer (1 mm) GPU tracking model next to the tracker's 2 mm one
lets a tracked scan hold walls under 2 mm. Same method as "Where a tracked scan's shape errors come
from": the five bracket recordings and three sheet recordings (a 2 mm flange, a 1 mm and a 3 mm
rib), Standard quality, GPU path, on a scratch copy of the engine (scratch scripts
`walls/edit_fine.py`, `run_w.sh`, `tabw.py`, `scale_fit.py`). Bracket = share of the part more than
0.3 mm off per scan; sheet = measured thickness in mm per scan (flange / 1 mm rib / 3 mm rib).

| Tracking | Bracket per scan (%) | Median | Sheet s0 | Sheet s1 | Sheet s2 |
|---|---|---|---|---|---|
| True poses | 10 / 11 / 11 / 10 / 10 | 10.5 % | 2.72 / 2.04 / 3.06 | 2.72 / 1.96 / 3.05 | 2.85 / 1.99 / 3.04 |
| Today's engine | 14 / 13 / 32 / 13 / 13 | 13.3 % | 2.86 / 2.41 / 3.54 | 2.72 / 2.49 / 3.19 | 2.98 / 2.56 / 3.71 |
| One 1 mm model instead | 12 / 16 / 41 / 13 / 27 | 15.9 % | 2.95 / 2.44 / 3.76 | 2.80 / 2.57 / 3.44 | 3.08 / 2.62 / 3.92 |
| Switch to the 1 mm model after 10 frames | 12 / 13 / 31 / 12 / 13 | 12.6 % | 2.75 / 2.29 / 3.24 | 2.64 / 1.91 / 2.73 | 2.93 / 2.57 / 3.73 |
| Refine against the 1 mm model, 2 mm band | 11 / 13 / 53 / 16 / 16 | 16.3 % | 2.71 / 2.34 / 3.32 | 2.62 / 2.23 / 2.85 | 2.86 / 2.45 / 3.52 |
| Refine, 2 mm band, 1 mm behind | 12 / 13 / 36 / 13 / 13 | 12.9 % | 2.66 / 2.33 / 3.23 | 2.53 / 1.13 / 2.66 | 2.81 / 2.37 / 3.25 |
| Refine, 3 mm band, 1 mm behind (best) | 13 / 13 / 26 / 14 / 13 | 13.3 % | 2.72 / 2.35 / 3.27 | 2.55 / 1.93 / 2.77 | 2.85 / 2.39 / 3.26 |

- **Switching** slips (7.6 mm at one bracket frame, 9.9 mm on a sheet scan). **Refining** (a few
  finest-level ICP steps against the 1 mm model, held to the 2 mm result by a prior, taken only if
  it moves under 1.5 mm) never slipped. Other forms tried (a stronger prior, a 4 mm band with 1, 1.5
  or 2 mm behind) land between these rows.
- **Why the best form is not a win:** its walls are 0.1–0.4 mm thinner than today's, but on sheet
  s1 they go below the true-pose values (flange 2.55, 3 mm rib 2.77 mm), and the 1 mm rib still
  reads 2.35–2.39 mm on two scans of three. It costs about 37 ms of tracking a frame (frame
  178 → 212 ms on the Radeon 680M, about +19 %) and 512 MB more GPU memory. Smooth scenes keep their
  numbers (full sphere 0.060 / 0.257 mm, car panel 0.083 / 0.393 mm); the car panel's hole reads
  0.08 mm smaller.
- **What sets a tracked wall's thickness is the camera path's scale.** Over 33 tracked sheet runs
  (11 tracking models × 3 scans), a wall's thickness above its true-pose value follows how far out
  the tracked cameras stand after a best fit: flange 0.77 × out (correlation 0.98), 3 mm rib
  2.12 × out + 0.16 mm (0.96), 1 mm rib 1.27 × out + 0.29 mm (0.70). Today's engine stands
  +0.17 / +0.04 / +0.20 mm out on the three scans; the finer models mostly move that offset rather
  than shrink it. This is the pull along the direction of motion noted above.
- **The 2 mm flange was the mesh model's limit before the opposite-sides rule (the same day; since then 2.10 to 2.18 mm at true poses):** 2.72–2.85 mm even at true poses, so no tracking
  change brings it near 2.0. Only the 1 mm rib keeps a rest (about +0.3 mm with a correct path) that
  is not the path's scale; no variant removed it, and it is not explained.

So the next steps for thin walls are the camera path's scale and the mesh model, not a finer
tracking grid. Limits: simulated, orbit pattern only, one run per recording (bracket s2 swings
25–53 % across variants, so one scan can move a median); the CPU path was not run.

## The camera path's scale: studied, nothing adopted (2026-11-16)

The question was why each tracked frame is pulled about 0.1 mm along the direction of motion
("What remains after 4 mm" above) and whether fixing it shrinks the path. GPU path, on a scratch
copy of the engine (scratch folder `scale/`: `step.py` for the one-step check, `out.py` for how far
out the cameras stand after a rigid best fit). One-step numbers are the mean pull along the camera's
x on about 60 frames (standard error 0.02–0.04 mm on the bracket, 0.06–0.10 mm on the sheet).

**Where the pull comes from:**
- **Only about 1.5 % of the ICP points hold x** (about 130 of 9,600 at the finest level, on the
  side faces); the table holds it to ±0.01 mm. Any small bias on the side faces shows as a pull.
- **The model's side faces sit 0.1–0.2 mm too deep, even with true depth at true poses**: a view that
  looks past a wall edge-on votes "free" into the voxels just outside it and nothing into those
  inside. Integrating only within the band removes it in the one-step check but swells whole scans
  (cameras +0.29–0.54 mm out, 3 mm rib 3.7–4.6 mm): those free-space votes are what keep the model
  from swelling.
- **The share fixed to the camera** (+0.09 to +0.14 mm with true depth) comes from the ICP source
  normals' forward differences: a point needs valid right and lower neighbours, so right- and
  bottom-facing faces lose their last sample before a depth step (125 left-facing against 73
  right-facing points on the bracket). Central differences remove it.
- **The share that follows the motion** (about ±0.11 mm on the bracket, ±0.17–0.20 mm on the sheet)
  comes with the matcher's depth values and is absent with true depth at the matcher's pixels. It
  sits on the left-facing faces and the base's sides. Not explained.
- **Ruled out as the main cause:** the model's lag (+0.119 mm with the frame itself in the model
  against +0.143 mm), the tracking filter (raw input pulls the same), the back rule, normal
  smoothing, the weight threshold, the motion prior and gyro (the check starts at the true pose).

**The pull is a weak lever on the path.** A known shift injected into every tracked frame of the
three sheet scans moves the cameras' "out" by about 0.3 mm per mm of shift:

| Injected per frame | Out, sheet s0 / s1 / s2 |
|---|---|
| None (today) | +0.17 / +0.04 / +0.20 mm |
| −0.15 mm along the motion | +0.09 / +0.01 / +0.15 mm |
| +0.15 mm along the motion | +0.21 / +0.08 / +0.26 mm |

So the measured pull accounts for 0.03–0.1 mm of the sheet's out; the rest is most likely the thin
walls being too thick in the 2 mm tracking model. With today's engine the bracket's path is not
too large at all (out −0.06 / +0.06 / +0.12 / −0.06 / +0.04 mm, mean +0.02, not following the
sweep), and the car panel's stands 0.01–0.04 mm in. The "0.05–0.2 % too large" above predates the
back rule.

**Prototype, symmetric source normals** (central differences in `_source_points`; off by default,
scratch `central.diff`). A correct repair of a real asymmetry, within the scatter on scans:

| | Today's engine | Symmetric normals |
|---|---|---|
| Bracket, more than 0.3 mm off per scan | 14 / 12 / 32 / 13 / 13 % (median 13.3 %) | 21 / 13 / 12 / 14 / 13 % (median 13.4 %) |
| Bracket out | −0.06 / +0.06 / +0.12 / −0.06 / +0.04 mm | −0.09 / +0.08 / +0.06 / −0.05 / +0.09 mm |
| Sheet out | +0.17 / +0.04 / +0.20 mm | +0.16 / +0.04 / +0.18 mm |
| Sheet s0 / s1 / s2, 3 mm rib | 3.54 / 3.19 / 3.71 mm | 3.53 / 3.18 / 3.71 mm |
| Car panel hole (five scans), diameter / centre | −0.90 / 0.33 mm | −0.92 / 0.31 mm |
| Sphere without the gyro, drift | 109 mm | 128 mm |

The end-to-end tests pass with it (7 of 7) with the same mesh numbers, and the time per frame is
unchanged. On a sweep the other way the one-step pull grows with it (−0.08 → −0.21 mm on the
reversed bracket), because the matcher's share no longer cancels the camera-fixed one. Not adopted.

Limits: simulated, orbit pattern only, one run per recording; the one-step check on bracket s0 and
sheet s0 only (the sheet without true depth); the CPU path was not run; the whole suite was not run
on the prototype.

## Thin walls seen from opposite sides: adopted (2026-11-16)

The mesh model's half of the thin-wall problem. With the cut at 2 voxels behind a surface (above, "A
band that is short only behind the surface"), a 2 mm flange still read 2.72–2.85 mm and a 1 mm rib
about 2.0 mm at true poses. Why:
- **The flange:** the voxel plane on its face gets 40 % of its votes from the far side, with a mean
  of −3.1 mm, and half of those count in full: the wall is exactly as thick as the cut, and noise
  decides. The value there is −0.72 mm instead of 0.
- **The 1 mm rib:** the far side's "inside" counts in full 2 mm behind the far face, which is 1 mm
  into the air.
- **A shorter cut everywhere** (1.5 voxels) helps the walls only partly, opens 15–20 % more pinholes,
  and sinks a table when frames disagree.

**The rule** (`ohs.track.recon_tsdf`, "Seen from opposite sides"; on wherever the short band is,
that is, in a markerless scan with `thin_walls` "auto"):
- Every voxel keeps a second mean with the cut at 1 voxel. It also keeps the count of observations
  that saw it in front of their surface, and the sum D of the observed surfaces' unit normals: plus
  for "in front", minus for "deeper than the cut".
- Two faces of a wall add up in D; one surface's observations cancel however it was looked at.
- Where such opposite pairs make 5–15 % of a voxel's observations, its value goes over to the
  second mean. Everywhere else the mesh is value for value what it was.

How it was measured: the matcher's depth of each recording was cached once and replayed into the mesh
model (scratch folder `mesh/`). "Tracked" uses today's engine's own trajectories, so only the mesh
model differs; the replay reproduces the earlier meshes exactly. Sheet = flange 2 mm / rib 1 mm /
rib 3 mm, in mm.

| | Before | Opposite sides |
|---|---|---|
| **True poses** | | |
| Sheet s0 | 2.72 / 2.04 / 3.06 | 2.18 / 1.28 / 3.06 |
| Sheet s1 | 2.72 / 1.96 / 3.05 | 2.16 / 1.25 / 3.05 |
| Sheet s2 | 2.85 / 1.99 / 3.04 | 2.10 / 1.24 / 3.04 |
| Bracket, more than 0.3 mm off per scan | 10.5 / 11.0 / 10.9 / 10.3 / 10.4 % | 10.0 / 10.3 / 10.2 / 9.8 / 9.8 % |
| Bracket boss radius / tilt | −0.27 mm / 3.2° | −0.28 mm / 2.9° |
| Open loops, sheets / bracket median | 691 / 728 / 728 / 708 | 691 / 729 / 727 / 709 |
| **Tracked** | | |
| Sheet s0 | 2.86 / 2.41 / 3.54 | 2.61 / 1.97 / 3.53 |
| Sheet s1 | 2.72 / 2.49 / 3.19 | 2.26 / 1.66 / 3.17 |
| Sheet s2 | 2.98 / 2.56 / 3.71 | 2.60 / 2.20 / 3.71 |
| Bracket, more than 0.3 mm off per scan | 13.6 / 12.5 / 32.7 / 13.1 / 13.3 % | 12.7 / 11.6 / 31.3 / 11.8 / 12.6 % |
| Bracket mean offset / boss radius | +0.015 / −0.18 mm | +0.008 / −0.19 mm |
| Five car panels: hole diameter / centre / floor | −0.90 / 0.34 / 0.002 mm | −0.90 / 0.32 / 0.002 mm |
| Open loops, sheets / bracket / car panel median | 1359 / 1217 / 1579 / 1304 / 773 | 1359 / 1216 / 1573 / 1303 / 773 |
| **End-to-end tests** (median / 95 %) | | |
| Full sphere / half-res sphere | 0.0609 / 0.2574, 0.1184 / 0.5169 | identical |
| Car panel | 0.0838 / 0.3933 | 0.0838 / 0.3934 |
| **Exact depth, a wall on a table, 36 views** | | |
| 1 / 2 / 3 / 7 mm wall | 2.12 / 2.43 / 2.91 / 6.90 | 0.92 / 2.00 / 2.91 / 6.90 |
| 7 mm block, frames 0.9 mm off: table offset | −0.07 | −0.07 |

- **At true poses it reaches the ceiling** of any mesh rule, which is each face fused from its own
  side's views alone: 2.12–2.14 / 1.03–1.31 / 2.91–2.94 mm.
- **Tracked, the rest is the poses.** Walls are still 0.3–0.6 mm (flange) and 0.7–1.2 mm (1 mm
  rib) too thick; the same ceiling tracked is 2.19–2.51 mm on the flange. The 1 mm rib's rest at a
  correct path (+0.3 mm, "A finer tracking model" above) is still not explained.
- **2 sigma** covers the bracket as before (99.5 % / 99.5 %).

**What was tried and dropped:**
- **The shares without the normals** (+12 bytes per voxel): the same walls, but the bosses shrank by
  0.04–0.06 mm, the car panel's hole by 0.05 mm, and a table sank 0.09 mm when frames were 0.9 mm off.
- **View directions instead of the normals:** clean on every scan measured, but two views of a
  plane from opposite azimuths at 45° look as far apart as the two sides of a wall. A noisy plane
  reached a share of 0.040 against the start of 0.03.

**Cost:** 24 bytes more per voxel (40 → 64), so 640 MB → 1 GB on these scans. The largest scan the
Radeon 680M's pool takes falls from about 299,000 to about 187,000 bricks; these scans use
21,000–24,000. One integration at 1280×800 took 19.1 / 18.9 / 19.5 ms before and 22.6 / 22.1 /
22.7 ms with the rule (three rounds, taking turns), and making the mesh 3.5–3.7 s before against
4.1–4.4 s. In a whole scan the integration runs on the GPU beside the next frame's work, so most
of it is hidden. The bracket recording s0 through `process_recording` (Standard, GPU path, old and
new engine taking turns, three rounds, run from a scratch folder) gave:
- a frame's median: 148.7 / 143.0 / 142.7 ms before, 145.9 / 145.4 / 147.0 ms with the rule
  (means 148.4 against 150.3 ms, about +1 %);
- the whole run, mesh included: 18.5 / 17.6 / 17.6 s before, 18.4 / 18.3 / 18.5 s with the rule
  (+0.5 s).

Guards: `tests/test_recon_opposite_sides.py` covers:
- a 1 mm and a 2 mm wall keep their thickness;
- a noisy table seen from one side is left alone bit for bit (largest share 0.044 against the start
  of 0.05);
- frames 0.9 mm off give the same table and the same open loops;
- the first mean is untouched;
- the GPU and numpy agree.

`test_app_pipeline.py` checks that the pipeline hands the rule to markerless scans only, that a model
handed on in a merge keeps it, and that the report says so. The camera-switch test runs with it too.

Limits:
- Simulated, orbit pattern only, one run per recording.
- The CPU path's mesh comes from Open3D's model, which has no such rule: unchanged there.
- Marker mode is unchanged (`thin_walls` "auto" leaves it as it was).
- The processing studio's Preview and Best quality were not measured separately.

## The fits' ± on today's engine (2026-11-16)

The study of "Fitted shapes against the truth" again, on the same ten recordings (bracket and car panel, seeds 0–4) processed with today's engine (thin walls, the 4 mm tracking band, the opposite-sides rule), with the same clicks and method: 15 fits per shape and pose source, 200 plane and cylinder fits and 30 holes in all.

**The fits themselves got better** since 2026-11-13, mostly with tracking:

| Shape | Quantity | True poses, 2026-11-13 → now | Tracking, 2026-11-13 → now |
|---|---|---|---|
| Bosses, Ø 14 | radius | −0.15 / −0.18 → −0.26 / −0.23 mm | +0.40 / +0.34 → −0.15 / −0.16 mm |
| | axis direction | 8.2° / 6.6° → 2.2° / 1.8° | 14.2° / 10.8° → 3.7° / 4.9° |
| | axis position | 0.07 → 0.11 / 0.17 mm | 0.48 / 0.64 → 0.21 / 0.13 mm |
| | clicks answered | 26 → 30 of 30 | 27 → 30 of 30 |
| Shaft, Ø 16 | radius | −0.08 → −0.13 mm | +0.60 → +0.02 mm |
| | axis position | 0.03 → 0.06 mm | 0.74 → 0.12 mm |
| Upright face | offset | −0.023 → −0.027 mm | +0.72 → −0.008 mm (worst +0.33) |
| Car-panel hole, Ø 25.2 | diameter | 1.2 → 1.3 mm small | 1.1 → 1.1 mm small |

(Median errors; two numbers are the two bosses.) At true poses the bosses now read 0.23–0.26 mm small in radius, the same on every seed: the shorter band behind the surface shows the shrinkage the 8 mm band hid ("Where a tracked scan's shape errors come from").

**The ± was still too small**: ±2σ covered the true error for 13–55 % of plane fits and 4–73 % of cylinder quantities. Most of the gap is not bias but correlation: a fused scan's errors come in patches. Measured on each region's own residuals, the correlation of two vertices falls to 1/e at 1.5–2.5 mm (4 mm on the tracked table), and the sum of the correlation over a vertex's neighbours, N_c, is 12–19 on the bosses and the shaft and 6–60 on the planes (in the median per shape; larger with tracking). A fit that takes its points as independent is too sure by about √N_c, 2.5–8 times.

**Adopted: the region widens its fit's ± by √N_c** (`ohs.measure.region`, "The ± values"; `correlated_points` in the answer). N_c is summed in bins of half an edge length, through 2 edge lengths in any case, then while the correlation stays above 0, to at most 12 edge lengths (12 mm on these meshes). The "through 2 edge lengths" matters: at true poses a fused plane's nearest bins jump between 0 and 0.6 (the mesh's own pattern), and stopping at the first bin at or below 0 gave answers that differed by up to 5.5 times in √N_c between two samples of the same region; with the rule, at most 1.3 times. Measured on at most 50,000 points of the region picked by id, the first 3,000 of them against all: the click takes 15–80 ms more (median click 372 → 423 ms, the slowest 1.7 s as before). The fits and their regions are unchanged, to the digit.

Share of fits whose ±2σ covers the error, before → now, pooled over the shapes:

| Quantity | True poses: the true error | Tracking: the true error | True poses: the spread from scan to scan | Tracking: the spread from scan to scan |
|---|---|---|---|---|
| Plane normal | 55 → 95 % | 45 → 71 % | 78 → 98 % | 64 → 91 % |
| Plane offset | 25 → 78 % | 13 → 44 % | 75 → 96 % | 18 → 62 % |
| Cylinder radius | 4 → 42 % | 22 → 84 % | 89 → 100 % | 62 → 100 % |
| Cylinder axis direction | 73 → 100 % | 36 → 100 % | 93 → 100 % | 82 → 100 % |
| Cylinder axis position | 36 → 100 % | 27 → 100 % | 98 → 100 % | 73 → 100 % |

("The spread from scan to scan" is each fit's error less the median error of its shape over the 15 fits: what is left once the shape's bias is taken out.)

What the widened ± does not cover:

- **Biases, the same on every scan.** The bosses' radius at true poses (−0.23 to −0.26 mm against a ± of 0.10 mm: covered 7–27 %), with tracking −0.15 mm (73–80 %); the shaft at true poses −0.13 mm (93 %). No ± made from one scan's points can see these.
- **Tracking errors that move a whole face.** The tracked upright face sits −0.14, −0.01, +0.32, −0.07 and +0.09 mm off on the five seeds (its offset is covered for 27 %); its points agree with each other.
- **The large planes' offsets**: the table and the car panel's floor (300–700k points, 400 mm across) are off by 0.3–7 µm in the median against ± of 0.4–0.9 µm. Their correlation reaches beyond the 12 mm measured. With tracking these numbers are also after the best fit of the whole scan, which the table dominates. A few µm do not matter for a part.

The hole's centre was covered before (100 %) and is not widened; its diameter reads 1.1–1.3 mm small with ± 0.22 mm, and no ± covers that.

Two other models were tried on the same fits and not adopted:
- **A floor per quantity** added to the ± (taken on seeds 0–2, tested on 3–4) covers 95–100 % of the held-out fits, but its numbers (0.08–0.16 mm on a radius or an offset) come from two simulated parts: an accuracy claim before the real parts of `ROADMAP.md` section 3, item 6.
- **The ± from the residuals' own scatter** (dropping the sigma's floor) times √N_c covers less of the shaft's spread from scan to scan (33–53 %): the fit hides what the residual field cannot show (a uniform offset becomes the radius), and today the sigma's pessimism stands in for that.

Guard tests: `tests/test_measure_correlated.py`:
- on a flat sheet with independent noise, N_c stays below 2;
- on a sheet and a half shaft with noise in 2 mm patches, the ± matches the spread over 24 draws (0.86–1.08 times) where the plain ± would be 0.13–0.14 times, and ±2σ covers 88–100 %;
- the same click gives the same factor;
- a hole has none.

Limits: simulated scans of two parts only, at Standard quality (1 mm voxels); the factor follows the mesh's edge length, so Preview's 2 mm mesh is measured the same way but was not checked against the truth. Two fits on the same scan share some of their errors; a relation's ± (lib/fits.ts) still takes them as independent. Real scans may have longer patches.

Scripts and rows: scratchpad `m5v3\` of the session of 2026-11-16 (`run.sh`, `compare.py`, `candidates.py`, `compare_models.py`, `cand2.py`, `rules2.py`; `results\` before, `results2\` with the widened ±).

## Smoothing's "within 2σ" limit on today's engine (2026-11-16)

The Smooth step can keep each vertex within twice its own sigma ("within 2σ"; until 2026-11-17 on by default in the Smooth tab and in both recipes; see the end of this section). With the old sigma it never took effect ("The deviation map against the truth"). Measured again on the meshes of "The fits' ± on today's engine" (bracket and car panel, 5 seeds, true poses and tracking), each setting with and without the limit, errors against the true surface:

| Setting | Vertices the limit holds back (all / at edges) | Median error, before → after (free / within 2σ) | RMS error, before → after (free / within 2σ) |
|---|---|---|---|
| Taubin 50 % (the Smooth tab), bracket, true poses | 29 % / 0 % | 0.0245 → 0.0184 / 0.0208 mm | 0.1104 → 0.1014 / 0.1016 mm |
| Taubin 50 %, bracket, tracking | 36 % / 0 % | 0.0504 → 0.0475 / 0.0479 mm | 0.1353 → 0.1289 / 0.1290 mm |
| Taubin 50 %, car panel, true poses | 19 % / 7 % | 0.0322 → 0.0253 / 0.0267 mm | 0.3292 → 0.3207 / 0.3208 mm |
| Taubin 50 %, car panel, tracking | 23 % / 12 % | 0.0655 → 0.0598 / 0.0601 mm | 0.3470 → 0.3393 / 0.3394 mm |
| Bilateral 25 % (bracket recipe) and 50 % (body-panel recipe), all four | 0 % | the same with and without | the same with and without |

- **The limit now binds, but in the wrong places.** It holds back a fifth to a third of the vertices, all on well-measured surface (median sigma 0.09–0.13 mm, so 2σ is about 0.2 mm), where smoothing removes noise: at true poses it keeps 10–20 % of that gain from happening. It never holds back the bracket's edges and only 7–12 % of the panel's, because at edges and rims the sigma is large (median 0.54–0.79 mm: it holds the bias terms of curvature, gaps and borders), and that is where smoothing does harm: Taubin rounds 26–30 % of the bracket's edge vertices by more than 0.05 mm, with or without the limit.
- **The bilateral recipes never reach the limit:** their moves stay below 2σ everywhere.
- So "within 2σ" protects nothing that needs protecting and costs a little where smoothing helps. What would protect edges is a limit that is small where the surface turns sharply (the edge detection of `keep_edges`), not one that follows the sigma.

Changing or removing the option changes what a mesh edit gives, so it is the owner's decision; the code is unchanged. Scripts and rows: scratchpad `m5v3\check\` of the session of 2026-11-16 (`run_smooth.py`, `agg.py`, `B_*.json`).

**Smoothing that finds flat surface by itself (prototypes, the same night).** The owner asked for smoothing that detects flat surface and leaves the rest. Two prototypes keep a share `w` (0 to 1) of each vertex's smoothing move; both use the denoised normals that `keep_edges` already computes (bilateral filter, 8 rounds):

- **Flat:** `w` from how much the surface turns within 2 mm of the vertex (RMS angle between its normal and its neighbours'): 1 below 4°, 0 above 12°. It holds 11–25 % of the vertices.
- **Edge:** `w` = 0 within 1.5 mm of an edge sharper than 25°, rising to 1 at 3 mm; curved surface away from edges is smoothed in full. It holds 3–9 %.

Same 20 meshes; means over the seeds (true poses and tracking alike unless said):

| Setting, scene | Edge vertices made worse by > 0.05 mm: free / 2σ / flat / edge | Median error after: free / 2σ / flat / edge (before) | Shaft, mean error after: free / flat / edge (before) |
|---|---|---|---|
| Taubin 50 %, bracket, true poses | 30 / 30 / 0.9 / 2.6 % | 0.0184 / 0.0208 / 0.0179 / 0.0181 mm (0.0245) | 0.171 / 0.177 / 0.174 mm (0.183) |
| Taubin 50 %, bracket, tracking | 26 / 26 / 0.4 / 1.5 % | 0.0475 / 0.0479 / 0.0475 / 0.0475 mm (0.0504) | 0.173 / 0.179 / 0.176 mm (0.184) |
| Taubin 50 %, car panel, true poses | 20 / 20 / 3.6 / 8.1 % | 0.0253 / 0.0267 / 0.0243 / 0.0248 mm (0.0322) | – |
| Taubin 50 %, car panel, tracking | 18 / 18 / 2.9 / 7.3 % | 0.0598 / 0.0601 / 0.0600 / 0.0598 mm (0.0655) | – |
| Bilateral 25 % (bracket recipe), bracket, true poses | 23 / 23 / 0.1 / 1.4 % | 0.0252 / 0.0252 / 0.0244 / 0.0249 mm (0.0245) | 0.177 / 0.180 / 0.179 mm (0.183) |
| Bilateral 50 % (body-panel recipe), bracket, true poses | 46 / 46 / 0.1 / 2.9 % | 0.0242 / 0.0242 / 0.0234 / 0.0238 mm (0.0242) | 0.186 / 0.191 / 0.190 mm (0.195) |

- **Both nearly end the harm at edges.** The edge vertices' mean error stays where it was (bracket 0.252 → 0.253 mm with flat, 0.279 mm free), and the share made worse falls from 17–46 % (bracket) and 3–20 % (car panel) to 0.1–3.6 % (flat) and 0.7–8.1 % (edge).
- **The noise on flat surface goes as with free smoothing:** the median error is the same or a little better (Taubin, bracket, true poses: 0.0179 mm against 0.0184 free and 0.0208 within 2σ). The car panel's gently curved surface is smoothed as much as without a limit (its mean error 0.0245 → 0.0208 mm with all three).
- **The price:** small round features such as the Ø 16 shaft keep a little of their noise (0.177 against 0.171 mm, flat; 0.174 mm, edge), and the RMS error over everything is about 1 % higher (0.1028 against 0.1014 mm), from the outliers at edges and rims that free smoothing pulls in and these leave.
- **Of the two, "flat" protects edges best** (at most 3.6 % made worse, against 8.1 %); "edge" keeps more of the smoothing on round features.

Both are options, off by default, chosen in the Smooth tab's "Where to smooth" (Everywhere / Off edges / Flat only, with the numbers above in its hover card): `flat_only` since 2026-11-16, `away_from_edges` since 2026-11-17 (the owner's decision: both, explained, not the default).

**The two built-in recipes use "Off edges" since 2026-11-17** (the owner: "if it doesn't protect the edges it's probably not worth it", then "Off edges is fine"). "Body panel" (bilateral 50 %) and "Bracket / mechanical" (bilateral 25 %) sent "within 2σ", which their moves never reached; they now send `away_from_edges`. What that changes is the two bilateral rows above: on the bracket the edge vertices made worse by more than 0.05 mm fall from 23 % to 1.4 % (25 %) and from 46 % to 2.9 % (50 %), the median error stays the same or a little better (0.0252 → 0.0249 and 0.0242 → 0.0238 mm), and the Ø 16 shaft keeps a little of its noise (0.179 against 0.177 mm, 0.190 against 0.186 mm). The bilateral settings were not measured on the car panel; with Taubin 50 % the panel's share made worse fell from 20 % to 8.1 %. A recipe someone saved, and an edit already applied, keep what they had. The Smooth tab's own switch "Stay within each point's uncertainty" is off by default since the same day (the owner: "Off by default please"; `ui/src/lib/smoothEdit.ts`); it can still be turned on, and its line now says that it holds back well-measured surface and does not protect edges. Through the app on the bracket's back scan (1280x800): the Bracket recipe ran in 10 s, "Moved the surface by 0.021 mm on average and 0.29 mm at most". Scripts and rows: scratchpad `m5v3\check\` (`run_flat.py`, `agg2.py`).

## The part's coordinate system: its ± (2026-11-17)

`ohs.measure.frame.part_frame` builds the part's own axes from three fitted features
([`docs/FEATURE_PLAN.md`](docs/FEATURE_PLAN.md), K3; the engine only so far). The frame's ± (its
angle, its turn about the first axis, its origin) is carried over from the fits' own ± values.
Checked on fits to noisy points with a known true frame, 200 seeded trials per case, 0.02–0.1 mm
of noise per point (`tests/test_measure_frame.py`):

| Features | Whole angle: ±2σ covers / mean (error/σ)² | Turn | Origin |
|---|---|---|---|
| Top face, side face, hole | 99.0 % / 0.92 | 94.5 % / 0.91 | 100 % / 0.85 |
| Face, hole, hole | 98.5 % / 0.81 | 97.0 % / 0.76 | 99.0 % / 0.93 |
| Three faces (a corner) | 98.5 % / 0.93 | 94.5 % / 1.02 | 99.0 % / 0.84 |
| Face, side face, hole, strict 3-2-1 | 100 % / 0.98 | 94.5 % / 1.13 | 99.5 % / 0.98 |

For an error in three directions at twice its RMS, 95–99.3 % is expected, and a mean of 1. With
exact fits the frame is exact to 1e-9 mm, and a mesh turned into the frame, saved and loaded has
its hole at the origin to 1e-4 mm (the file's 32-bit numbers).

- **What the ± covers:** the spread from scan to scan that the fits' ± covers. It leaves out
  what is the same on every scan (a hole's edge read small, the calibration's scale).
- **A hole used for its direction has no ±** (only its centre has one); the frame says so and
  gives no ± for what depends on it.
- **Not yet tried on meshes of simulated scans:** these trials use points with independent
  noise. On a scan the fits' ± is widened for errors that come in patches ("The fits' ± on
  today's engine"); how well the frame's ± holds there is still to be measured.

## Relations between two fits, and where a fit's region lies (2026-11-17)

`ohs.measure.relate.relate` gives what the viewer shows for two fits (the angle; the distance
between parallel planes or axes, or the closest distance of skew axes; an axis or a hole's centre
to a plane; two holes' centres), now each with a 1σ carried over from both fits
([`docs/FEATURE_PLAN.md`](docs/FEATURE_PLAN.md), K2; the engine only so far). The values equal the
viewer's own (`ui/src/lib/fits.ts`) to 1e-6 on a shared set of cases
(`tests/data/relations.json`, read by a Python test and a UI test).

**The ± of a relation**, on fits to noisy points with a known truth, 200 seeded trials per case
(`tests/test_measure_relate.py`):

| Case | ±2σ covers | Mean (error/σ)² |
|---|---|---|
| Parallel planes 30 mm aside: angle / distance | 94.5 % / 97.5 % | 1.13 / 0.84 |
| Planes at 90° / at 40°: angle | 95.0 % / 95.5 % | 0.90 / 0.87 |
| Parallel axes: angle / distance | 97.0 % / 97.0 % | 1.15 / 0.88 |
| Coaxial axes: distance | 95.5 % | 1.21 |
| Skew axes at 60°: angle / distance | 96.5 % / 98.0 % | 0.89 / 0.87 |
| Axis along a plane: angle / distance | 96.0 % / 97.0 % | 1.04 / 0.86 |
| Axis square to a plane: angle | 95.0 % | 1.21 |
| Two holes in one face: centre to centre | 94.5 % | 1.10 |
| Hole in a side face to a plane | 94.5 % | 0.97 |

- **Where it is off:** a fit's ± across its direction is split evenly over the two directions.
  For cylinders seen over part of their round that is not so: skew axes fitted on 180° arcs are
  covered 99.5 % of the time (the ± too large by about 1.4), on 90° arcs 89.5 % (too small by
  about 1.2). A test holds it to that. Mending it needs the fits to keep their full covariance.
- **No ±:** any angle with a hole's direction (a hole has a ± for its centre only), and a hole's
  depth below a face parallel to its own gets the face's ± alone, which is too small.
- **The viewer's own ±** for the distance between parallel planes leaves out the tilt of the two
  normals over the sideways distance between the planes' points; the engine's includes it. The
  values agree.
- As everywhere: the ± is the spread from scan to scan, not the accuracy.

**Extents** (`RegionFit.extent`): a plane's outline in the plane's own 2D axes (outer loops and
holes told apart, specks under two mesh edges left out), its smallest rectangle and its area; a
cylinder's span along its axis and the arc it covers; a hole's edge. The fits themselves are
unchanged: identical numbers and regions before and after on a simulated bracket scan (10
clicks) and on the tests' meshes.

**What it costs on a click:**

| Click | Fit alone | Extent |
|---|---|---|
| The table of a real-size scan (804,372 triangles; the region 394,044 faces, 2,810 loops, 722 after the specks) | 1.15–1.22 s | 0.12–0.14 s |
| A whole plate, 1M triangles (synthetic) | 0.56 s | 0.05 s |
| One side of a box, 1M triangles | 0.19 s | 0.02 s |
| A whole tube, 1M triangles | 1.21 s | 0.10 s |
| 120° of a pitted tube, 685k faces | 1.39 s | 0.09 s |
| A hole | 0.10 s | 0 |

The first version took 23 s on that table: it tested every loop of the outline against every
other, which the synthetic meshes, with a handful of loops, did not show. Found by trying it in
the app on the scan. Loops are now only tested against larger loops whose box holds them, all
points at once (49 tests instead of 262,450), and
`test_a_plane_with_hundreds_of_loops_costs_less_than_its_fit` (a plate with 529 holes: 6.4 s
before against a 0.17 s fit, 0.02 s now) guards it.

- **A fault found on the way and mended:** the test for "is this vertex on the region's border"
  summed a linear function of the neighbours' numbers, which cancels on regular grids, so a
  border vertex could be missed. It now uses fixed random weights.
- **Not yet tried:** extents on meshes of tracked scans beyond the table above; the extent is
  not sent to the viewer yet.

## Sections: the mesh cut by a plane (2026-11-17)

`ohs.mesh.section.section` cuts a mesh with a plane into ordered lines in the plane's own 2D
axes, each point with the mesh's sigma and each stretch marked measured or filled
([`docs/FEATURE_PLAN.md`](docs/FEATURE_PLAN.md), I1; the engine and the DXF and SVG writers so
far). On shapes with known truth (`tests/test_mesh_section.py`): a box gives its rectangle and
area, a cylinder cut at an angle its ellipse, a sphere its circles, a torus two loops; the same
cut of a mesh in another pose gives the same profile; planes through vertices, along edges and
in a face are handled exactly (crossing points are computed once per edge, so lines join
without a distance test).

On real-size scans:

| Cut | Time | Lines (closed) | Points | Length |
|---|---|---|---|---|
| The ball-on-a-table scan (804,372 triangles), through the ball 60 mm up | 30 ms | 6 (0) | 288 | 105.3 mm |
| The same, upright through the ball and across the table | 32 ms | 6 (0) | 1,092 | 485.2 mm |
| The same, along the noisy table 0.3 mm above it (a stress case) | 124 ms | 908 (672) | 28,921 | 10,903 mm |
| The simulated bracket scan (715,226 triangles), upright through the bracket | 30 ms | 5 (0) | 1,171 | 532.3 mm |

- Every total length equals the plain sum over the crossed faces to 0.001 mm; no point is
  repeated and no segment has zero length.
- **A scan seen from one side gives open lines:** the ball is a cap and the bracket's side
  walls are missing, so their sections are arcs and steps with gaps, not closed loops. That is
  the scan's shape, not a fault of the cut.
- **The files:** DXF (R12, ASCII, mm, the measured and the not-measured stretches on two
  layers) read back by our own reader and by FreeCAD 1.1.4's importer to 1e-6 mm; SVG at 1:1
  (a millimetre prints as a millimetre), not mirrored, with a scale bar. Not tried: AutoCAD or
  other programs; the SVG has been checked as XML, not looked at.

## A scan compared with a reference model (2026-11-17)

`ohs.inspect.reference` reads a reference model (STL, OBJ, PLY), aligns the scan to it and
gives each scan vertex's signed distance from the reference, with the share of the surface
within a tolerance and the areas outside it ([`docs/FEATURE_PLAN.md`](docs/FEATURE_PLAN.md), I2;
the engine only so far).

**On shapes with known truth** (`tests/test_inspect_reference.py`): a noisy copy of the
reference at a random pose aligns from its shape and reads zero within the noise; a boss made
1 mm taller reads 1.00 mm on its cap and is found as one region; an inside-out reference
gives the same deviations; a reference in inches or metres is not guessed (the alignment
refuses until the factor is applied or kept); shares are by area, so a finer mesh in one place
changes nothing.

**Best fit against datums**, on a block 0.5 mm too long:

| Alignment | One end | The other end |
|---|---|---|
| Best fit from the shape | 0.24 mm | 0.26 mm |
| On the part's datums (no fit) | 0.00 mm | 0.50 mm |

A best fit spreads an overall error over the part; an alignment on datums shows where it is.
The result says which was used, and a best fit says how far it is from the datum pose.

**At real size:** the simulated bracket scan (the bracket on its table, 715,226 triangles)
against the bracket's true shape.

| Step | Time |
|---|---|
| Reading the reference and checking its units | 0.5 s |
| Aligning from the shapes | 4.7 s |
| The deviation and the answer at one tolerance | 0.3 s |
| The answer at each further tolerance | 0.01–0.02 s |

- **The pose is found from the shape alone with the table in the scan:** 0.10° and 0.92 mm
  from the pose the scan's own frames give, which themselves differ by up to 1.2 mm (tracking).
- **Within ± 0.1 / 0.3 / 0.5 mm:** 61 % / 87 % / 95 % of the compared surface (by area); the
  median is 0.009 mm and the robust spread 0.10 mm. At the frames' pose instead of the fitted
  one, 69 % within ± 0.3 mm: a best fit takes the tracking's overall error out, as it must.
- **The table** is 98 % of the scan's area. It is "not on the reference" (more than 3 mm from
  it, or outside its box, or facing the other way) and counts in no share. 74 % of the
  reference was not scanned (the far side, the bottom).
- **No area exceeds the scan's own uncertainty at ± 0.3 mm:** on the part the sigma's median
  per area is 0.35–1.2 mm, so 2σ is wider than the tolerance on 90 % of the compared surface.
  The answer says so for each area: a deviation inside the scan's own 2σ is not evidence of a
  real difference.
- **On a tracked scan fused at true poses** (the cached 640×400 recording of the tests): the
  median distance from the reference is 0.20 mm (CPU path 0.17), against 0.23 mm (0.20) by an
  independent ray cast to the scene; from the shape it lands 0.7 mm from the true pose.

**Found on the way, in the alignment engine** (worked round here, not changed there; now
`docs/MAINTENANCE_PLAN.md`, F11): with 33 stray points left on the table, the alignment from
shape accepted a pose turned 180°.

- **Not tried:** real scans and CAD exports from other programs; OBJ files; references that
  cannot be wound one way; a STEP file as the reference (it needs a reader we do not have).

## Gap and flush between two panels: flush yes, gap no (2026-11-17)

An experiment with a stop rule ([`docs/FEATURE_PLAN.md`](docs/FEATURE_PLAN.md), I4): along a seam
between two body panels, can the scan give the **flush** (the step between the two surfaces)
and the **gap** (edge to edge)? The rule, set beforehand: flush is offered if it reads within
0.2 mm; gap only if its bias is the same within 0.2 mm across gap sizes.

**The scene** (`panel_seam` in `ohs/sim/scene.py`): two panels of 240 × 80 mm, slightly curved,
each with a hemmed edge (the surface rolls over a half circle of 1 mm radius), 120 mm above a
dark floor that is all that shows through the gap; the seam turned 30° against the stereo
baseline. Gap and flush can change along the seam. Three recordings of 60 frames, each
processed with tracking and at the true poses (GPU path, Standard quality, defaults):

| Recording | Gap | Flush |
|---|---|---|
| taper | 2 → 6 mm | 0 |
| taper_flush2 | 6 → 2 mm | 2 mm |
| flush_ramp | 3 mm | 0 → 2 mm |

`ohs.inspect.gapflush.measure_gap_flush` follows the seam from two clicks, takes a section
across it every 3 mm (`ohs.mesh.section`), fits each panel's surface line over 12 mm ending
2 mm inside the edge, and reads the flush between the two lines at the seam's middle.

**Flush** (stations along the seam; error = reading − truth):

| Poses | True flush | Stations | Mean error | Spread | 95 % of the errors within | Stated ± | Within 2σ |
|---|---|---|---|---|---|---|---|
| Tracked | 0 mm | 88 | +0.005 mm | 0.075 mm | 0.145 mm | 0.119 mm | 100 % |
| Tracked | 0.5 mm | 20 | −0.016 | 0.111 | 0.200 | 0.122 | 100 % |
| Tracked | 1 mm | 18 | +0.001 | 0.111 | 0.221 | 0.117 | 94 % |
| Tracked | 1.5 mm | 20 | −0.080 | 0.081 | 0.223 | 0.121 | 95 % |
| Tracked | 2 mm | 86 | +0.011 | 0.122 | 0.251 | 0.131 | 98 % |
| True | 0 mm | 87 | +0.005 | 0.081 | 0.144 | 0.119 | 100 % |
| True | 2 mm | 82 | +0.015 | 0.109 | 0.203 | 0.128 | 98 % |

- **The mean along a seam is within 0.04 mm of the truth** in all six scans, and the bias is
  at most 0.08 mm at any flush. The error does not depend on the gap's size.
- **A single station is noisier:** within 0.2 mm 95 % of the time at flush 0, and 0.20–0.26 mm
  at 0.5–2 mm, so about one station in ten to twenty is more than 0.2 mm off. The lowest and
  the highest along a seam are stretched by that noise (a true 2.00 everywhere reads 1.77 /
  2.02 / 2.33): the mean is the number to trust.
- **The stated ± is honest:** 94–100 % of the stations lie within 2σ.

**Gap: not measurable.** In every scan, tracked and at the true poses, the gap is closed:
where it is, the mesh shows a shallow valley about 1 mm deep and about 8 mm wide (at 5.2 mm of
gap the surface dips 1.0 mm, at 3.1 mm it dips 0.85 mm). Neither panel's edge is in the mesh.
All sections are bridged, and no gap reading is trusted.

| Edge rule | Bias at a true gap of 2 / 3 / 4 / 5 / 6 mm (tracked) | Change across gaps |
|---|---|---|
| The outermost point of each panel | no reading (bridged) | |
| Where the profile has dropped 0.25 mm below its line | +4.17 / +2.87 / +3.52 / +3.76 / +3.86 mm | 1.29 mm |
| Dropped 0.5 mm | +1.99 / +1.17 / +1.35 / +1.49 / +1.23 mm | 0.83 mm |
| Dropped 1 mm | – / −2.11 / −2.75 / −2.93 / −3.36 mm | 1.25 mm |

- The bias changes by 0.8–1.3 mm across the gap sizes, against the rule's 0.2 mm. These
  readings measure the fill's valley, not edges. The numbers at the true poses are the same,
  so it is not tracking.
- It is the known fault of "Holes filled in": over a far, dark background the stereo depth
  carries the surface into a narrow opening, and holes of Ø 6–10 mm stay closed. A gap of
  2–6 mm is narrower still. No rule applied to the mesh afterwards can recover the edges.
- **The laser lines do not save it:** the laser cloud does not fill the gap, but it ends
  1.5–2.6 mm short of the true edges with 1.2–2.1 mm of scatter; a gap from it was 4.3 mm too
  wide. Likely because laser points that disagree with the frame's dense depth are dropped, and
  at the seam that depth is the fill; not checked.

**By the stop rule:** flush is offered, with each station's ± and the mean along the seam; gap
is worked out for the record and **not offered** (`GAP_OFFERED = False`, with the reason in
`GAP_NOTE`).

- **Limits of the experiment:** one seed per recording, one hem radius (1 mm), one turn of the
  seam (30°), the GPU path only, the simulator only. A real gap has an inner panel a few tens
  of millimetres behind it, not a dark floor at 120 mm, which may read differently either way.
- Repeat it with `python tools/gapflush_study.py run <folder>` (about 5.5 minutes) and
  `summary <folder>`.

## Removing the table from a single scan (2026-11-17)

The edit "Remove the table" ([`docs/FEATURE_PLAN.md`](docs/FEATURE_PLAN.md), X1;
`ohs/mesh/support.py`) finds the surface the part stands on and removes it. A face is table
when it faces within 45° of the plane's normal, its centre is at most a margin above the plane,
and it is joined through such faces to the plane's own region; everything behind the plane
goes; the part's walls stay at any height. Measured on the three simulated scans of the
bracket on its table (the front, the side and the back view; 715,226 triangles and about as
many) with the bracket's true shape as the judge (`ohs.inspect.reference`), and on the ball on
its table (804,372 triangles) judged by its sphere.

Table left near the part / part lost, in mm²:

| Margin | Front | Side | Back | Ball: table left |
|---|---|---|---|---|
| 0 mm | 962 / 5.3 | 1,870 / 3.1 | 1,025 / 1.0 | 2,793 |
| 0.5 mm | 180 / 9.5 | 460 / 11.7 | 236 / 12.3 | 1,617 |
| **1 mm (the default)** | **0.7 / 18.0** | **3.3 / 18.6** | **0.5 / 16.9** | **519** |
| 1.5 mm | 0 / 18.8 | 3.3 / 18.6 | 0.5 / 17.7 | 194 |
| 2 mm | 0 / 18.8 | 3.3 / 18.6 | 0.5 / 17.7 | 135 |
| 3 mm | 0 / 23.9 | 3.3 / 18.6 | 0.5 / 17.7 | 91 |

- **1 mm is the knee** on all three views: under 4 mm² of table is left near the part, and
  more margin takes no more table there but costs more of any part that slopes into the table.
- **What is lost of the part** at 1 mm is 17–19 mm², all of it within 1 mm of the table (the
  lower half of the rounded foot where part and table blend). A plain 1 mm slab would lose
  50–70 mm², and the 2 mm slab the merge of several scans uses, 118–171 mm².
- **The ball** is a cap that starts 27 mm above the table in this scan, so nothing of it is
  lost. The table left there is 64 loose pieces at the scan's border, 285–494 mm from the ball
  and rising 1–5 mm with a sigma of about 1 mm; the Default recipe's Remove floaters takes
  them. The edit says how many such pieces it left.
- **What it costs by rule:** a surface that meets the table at less than 45° (a dome, a
  chamfer) loses its bottom millimetre, and a part thinner than the margin lying flat goes
  with the table.
- **When it says no:** a flat surface is taken for a table only when something stands on it,
  little lies behind it, and the part does not go on behind it beside what stands on it. A
  box whose top is larger than the table seen around it keeps its top. A scan with no table
  is refused in words and nothing is removed.
- It takes 1.2–1.9 s on these meshes (Remove floaters 0.4 s, Fill holes 0.8 s, Smooth 2.4 s).
- In the app on the front view: 699,380 faces (98 % of the mesh) found as the table, flat to
  0.09 mm; after the edit the bracket alone is left (15,846 triangles); undo brings the table
  back.
- **Not tried:** real scans; a table that is not flat (a cloth, a turntable with a rim).

### A thin part lying flat on the table

The owner's point: the thing scanned may be a fraction of a millimetre thick. As first built,
the edit took any flat part thinner than its margin away with the table. Now a raised area
inside the margin that is steady in height (a plateau, not a foot rising into a wall) is
compared with **the table right round it** (the faces 3–10 mm outside it):

- **kept** as a thin part when it rises at least 4 times that table's scatter, its top is
  parallel to the table, it is 20 mm across or more, and the table round it is flat;
- **warned about** when it rises 2.5 times the scatter or more but fails one of those: it
  goes with the table, and the preview draws it in the warning colour, lists it (where, how
  large, how high) and offers "Keep this";
- **"uneven"** where the table itself rises in many places (near a scan's border): one
  warning for all of them, not drawn.

Each scan's own limit is in the finding and shown in the preview: on these four tables a part
thinner than 0.18–0.19 mm cannot be told from the table at all, and from 0.28–0.31 mm it stays.

A disc raised on each of the four real tables (the bracket's three views, the ball scan), at
three places, by the agent's measurement. K kept, W warned and drawn, u in the "uneven"
warning only, – gone without a word; thickness 0.1 / 0.2 / 0.3 / 0.5 / 0.8 mm:

| Table, place | 10 mm across | 30 mm across | 100 mm across |
|---|---|---|---|
| Bracket front, near the part | – W W W W | – K K K K | – u K K K |
| Bracket front, 75 mm out | – – W W W | – K K K K | – u K K K |
| Bracket front, at the border | – – u u u | – u u K K | u – K K K |
| Bracket side, near | – W W W W | – K K K K | – – K K K |
| Bracket side, 75 mm out | – W W W W | – u K K K | – – K K K |
| Bracket side, border | – u u u u | – – u u u | – u u K K |
| Bracket back, near | – W W W W | – K K K K | – – K K K |
| Bracket back, 75 mm out | – W W W W | – K K K K | – u K K K |
| Bracket back, border | – – u – – | – u u u u | u u u K K |
| Ball, near | – W W W W | – K K K K | – – K K K |
| Ball, 75 mm out | – – W W W | – W K K K | – u K K K |
| Ball, border | u u – – – | – u u u K | u u u K K |

- **Near the part and 75 mm out, nothing of 0.3 mm or more and 30 mm or more across is
  lost:** it is kept. Small parts (10 mm) of 0.2–0.3 mm and more are warned about, drawn and
  can be kept with one click.
- **What still goes without a word:** 0.1 mm everywhere (below every table's limit); 0.2 mm
  in places; and 10 mm discs at the border of two scans. At a scan's border the table rises
  by itself in steady patches of 0.2–0.6 mm, so a thin part there is at best in the
  "uneven" warning.
- **No false thin part is kept** on any of the four bare tables; 0, 1, 1 and 3 areas are
  warned about there (small raised bits at the mesh's border), and 54–71 are "uneven".
- **The review's own check** (a 40 mm disc 75 mm from the bracket on the front view): before
  this, kept at 0.8 mm, warned at 0.5 mm, gone without a word at 0.3 mm (it was judged against
  the scan's vertex sigma, 0.2 mm there, though the table is flat to 0.07 mm). Now kept at
  0.3, 0.5 and 0.8 mm; at 0.2 mm, at this table's limit, 43 % of its top goes.
- Thick parts are as before: the table left and the part lost on the bracket's three views
  are unchanged. A click takes 2.3–3.0 s now.
- **The merge of several scans is not covered by this:** with "The part was moved or turned
  over" each frame's depth within 2 mm of that scan's table is left out before fusing, so a
  flat part thinner than 2 mm keeps none of its pixels and is missing from the merged scan
  (checked on `FramePipeline._clip_depth`: discs of 0.3, 0.8 and 1.5 mm keep 0 %, 2.5 and
  4 mm keep 100 %). **Mended the same day: the next section.**

### The merge of several scans keeps the part's foot and thin parts (2026-11-17)

The owner's decision. When the part was moved or turned over between the scans, each scan's
table must stay out of the merged model. Until now every frame's depth and laser points within
2 mm of that scan's table were left out. Now each scan's own mesh says where its table is bare
and where the part stands (`ohs.mesh.support.support_map`, a map of 1 mm cells on the table's
plane, made from the single-scan table finding). A frame's point is kept when it lies more
than 2 mm above the plane, or, where the part or a thin part stands, anywhere from 1 mm behind
the plane upward. What is left of a table in the merged mesh is then taken out by the
single-scan rule (`table_left`), and counted.

The simulated bracket, its tracked front, side and back scans, judged against the bracket's
true shape. "Table left" is surface not on the true shape within 3 mm of a table; "foot
covered" is how much of the true wall next to the base has merged surface within 1 mm.

| Merge | Table left | Foot covered at 0–1 / 1–2 / 2–4 mm | Within ± 0.3 mm | Part vertices | Median error |
|---|---|---|---|---|---|
| Front + side, before (2 mm cut) | 0 mm² | **0 / 0 / 0 %** | 79.3 % | 15,881 | 0.104 mm |
| Front + side, now | 2.1 mm² | **40 / 50 / 51 %** | 79.4 % | 17,155 | 0.109 mm |
| Front + side + back, before | 0 mm² | **0 / 0 / 0 %** | 78.7 % | 17,020 | 0.102 mm |
| Front + side + back, now | 2.1 mm² | **52 / 70 / 70 %** | 76.7 % | 18,857 | 0.113 mm |

- **The foot is back.** The bottom 4 mm of the bracket was missing from every merge of a
  moved part; half to two thirds of it is now there (a single scan covers 16–30 % of the
  bottom millimetre; the rest is not seen by any scan).
- **The new surface at the foot is less accurate than the rest,** as the foot is in a single
  scan: the share within ± 0.3 mm of the three-scan merge falls from 78.7 to 76.7 % and the
  median error rises by 0.01 mm, because 1,800 more vertices are in it, next to the table.
- **2 mm² of table is left** (0 before); the rule took 3–4 faces and 147–224 laser points
  out of the merged scan.
- A merge takes 23–28 s now (15–23 s before): each scan's table map costs about 3.6 s.

A thin plate (a new simulator scene, `thin_plate`: 60 × 40 mm with a notch), scanned lying
flat, then turned over and moved, then slid and turned; fused at the true poses and merged at
the true motion:

| Plate, pair | Before | Now: top / bottom covered | Table left | Thickness reads |
|---|---|---|---|---|
| 1.0 mm, turned over | **gone (0 faces)** | 100 / 100 % | 278 mm² | 1.42 mm |
| 1.0 mm, slid | **gone** | 100 % / – | 76 mm² | 0.99 mm above the table |
| 0.5 mm, turned over | **gone** | 100 / 99.7 % | 196 mm² | 1.28 mm |
| 0.5 mm, slid | **gone** | 100 % / – | 58 mm² | 0.49 mm above the table |

- **A thin part survives a merge now;** before, it was missing altogether.
- **Turned over, the plate reads 0.3–0.8 mm too thick.** Its two faces are seen from opposite
  sides, the thin-wall effect of the fusion ("Thin walls seen from opposite sides"); the same
  frames fused without any table rule read the same, so it is not the map's doing. Slid on
  the table, it reads right to 0.01 mm.
- **The table left** (58–278 mm²) is raised areas of the bare tables that the rule cannot tell
  from a thin part (for example 15 mm across and 0.21 mm high, 300 mm from the plate). They
  are kept on purpose and named in a warning: a little table left is better than a part lost.
- **A scan whose table cannot be told apart** falls back to the old 2 mm cut for that scan
  alone, with a warning that names it. When the part was not moved, nothing changes: the mesh
  is the same vertex for vertex.
- Through the app on the bracket's front and side scans: both tables found, "the part is kept
  right down to it", one raised area on the side scan kept and named, the uneven borders said
  once per scan.
- Repeat it with `python tools/merge_table_study.py` (see its head for the two cases).

**A small thin part beside a large one: two more things had to change (later the same day).**
The tables above are from before them; the figures after are below.

1. **The small-piece cleaning.** A merge drops loose pieces under 1 % of the triangles. A piece
   that lies over a scan's thin part (a raised area kept or warned about) is now kept whatever
   its size (`ohs.align.merge.clean_merged`), and the report says what the cleaning left out
   (`mesh.cleaning`; on the bracket 50 pieces, 29 mm², none of them over a thin part).
2. **The gap guard.** That was not what lost the small area: the fusion did. Its gap guard
   leaves out depth next to a patch without depth, because the matcher carries a surface into
   a patch it could not match. To the guard, the bare table the merge had just left out looked
   like such a patch, so it ate the rim of everything the clip kept: all of an area 14 mm
   across, and the foot of the part. The guard, the distance to missing depth and the laser
   points' depth check are now worked out on the depth as the camera saw it
   (`FramePipeline` keeps it as `seen`; `ConfidenceTsdf.integrate(seen=...)`); only the depth
   the clip keeps is fused. A real hole in the depth is guarded as before, and a frame nothing
   was clipped from is fused bit for bit as before (`tests/test_merge_guard.py`).

And the report now says of each thin part what is true of the merged mesh (`in_merge`: kept,
in part or missing, from the share of its cells with merged surface over them), where before
it said "kept" of what the merge meant to keep.

| Merge (bracket, part moved) | Table left near the part / in all | Foot covered at 0–1 / 1–2 / 2–4 mm | Within ± 0.3 mm | Part vertices | Time |
|---|---|---|---|---|---|
| Front + side, guard on the clipped depth | 2.1 / 2.1 mm² | 40 / 50 / 51 % | 79.4 % | 17,155 | 14–22 s |
| Front + side, guard on what was seen | **0 / 221 mm²** | **46 / 52 / 52 %** | 78.1 % | 17,964 | 20.4 s |
| Front + side + back, guard on the clipped depth | 2.1 / 2.1 mm² | 52 / 70 / 70 % | 76.7 % | 18,857 | |
| Front + side + back, guard on what was seen | **0.5 / 151 mm²** | **59 / 72 / 71 %** | 74.4 % | 19,852 | 30.3 s |
| For comparison: the tables left in (front + side / all three) | 6,740 mm² near | 46 / 52 / 56 % and 61 / 72 / 71 % | 74.4 / 73.9 % | 18,924 / 20,204 | 16.7 / 22.6 s |

| Plate, pair | Top / bottom covered | Table left, before → now | Thickness reads |
|---|---|---|---|
| 1.0 mm, turned over | 100 / 100 % | 278 → 2,062 mm² | 1.42 mm (unchanged) |
| 1.0 mm, slid | 100 % / – | 76 → 585 mm² | 0.99 mm (unchanged) |
| 0.5 mm, turned over | 100 / 99.9 % | 196 → 1,764 mm² | 1.28 mm (unchanged) |
| 0.5 mm, slid | 100 % / – | 58 → 505 mm² | 0.49 mm (unchanged) |

- **The small thin area is in the merge:** the side scan's raised area 14 mm across had 0
  faces in the merged mesh and has 539 (589 in its own scan); the report's "kept" is now true.
- **The foot is covered as well as with the tables left in** (46 / 52 / 52 % against
  46 / 52 / 56 %), with no table next to the part.
- **The price is table left away from the part:** 151–221 mm² on the bracket and
  505–2,062 mm² on the plate scenes. It lies over raised areas a scan could not tell from a
  thin part, which a merge keeps on purpose and names in a warning; the guard used to eat them
  without a word. On the plates 1,328 of 2,062, 539 of 585, 1,044 of 1,764 and 459 of 505 mm²
  lie over such areas; the rest on the turned-over plates (about 730 mm²) is not traced, most
  likely the rings round them where the other scan's table shows. Two of these areas the
  single-scan rule even calls thin parts (26 mm across and 0.25 mm high, 23 mm and 0.30 mm, on
  bare table): its false alarms on these scenes are open.
- **The share within ± 0.3 mm falls a little** (79.4 → 78.1 % and 76.7 → 74.4 %): the part's
  rim next to the left-out table, less accurate than the rest, now stays.
- **The 2 mm cut for a scan whose table cannot be told apart** is a clip too, so it also
  changed: on front + side it keeps a little of the foot (0 / 6.5 / 48 %) and leaves 18 mm².
- A merge takes 20–30 s (one more depth image uploaded per frame).
- **Through the app** (the tracked front and side scans, merged at the aligned pose, 24 s):
  both tables found; 450 and 682 triangles (159 and 218 mm²) of table ring taken out next to
  the part; the side scan's 14 mm area kept and named in its warning; "46 small loose pieces
  (24 mm²) were left out as too small; 0 small pieces over a thin part were kept." In the
  viewer the kept area is a small patch 200 mm from the bracket, which makes the merged scan's
  bounding box 348 mm long: what "kept and named" looks like when the area was the table's own.

## What a scan sees of its calibration (calibration health, 2026-11-18)

Every scan's `report.json` now has a `health` block ([`docs/FEATURE_PLAN.md`](docs/FEATURE_PLAN.md),
H1; `ohs/calib/scan_health.py`; its keys are in `docs/APP_API.md`). It only writes down; which
of these signs really show a stale calibration is the next task (H2), and nothing reads the
block yet. On a simulated bracket scan with the true calibration (600 frames at 30 a second,
1280x800, tracked, GPU path), so these are the values of a calibration that is right:

| Sign | Value |
|---|---|
| Row error (every 30th frame: 20 frames, 8,298 matches) | median 0.058 px, 90 % under 0.17 px, signed median 0.002 px |
| Laser points minus stereo depth, horizontal line (263,778 points) | median +0.005 mm; half within 0.56 mm; 5–95 %: −1.51 to +1.44 mm; 210 dropped by the depth check |
| The same, vertical line (156,984 points) | median +0.001 mm; half within 0.09 mm; 5–95 %: −0.25 to +0.27 mm; none dropped |
| Laser points against the mesh, per line (signed median) | horizontal −0.005 mm, vertical +0.016 mm; 95 % within 0.31 and 0.21 mm |
| Laser points against the mesh, all | median 0.060 mm, 95 % within 0.26 mm |
| Tracking residual (599 frames) | median 0.34 mm, 95 % under 0.41 mm |
| Tracker's rotation against the gyro's | median 0.0045°, 95 % under 0.0099°, largest 0.019° |
| Temperature (the IMU chip) | 26.5–26.6 °C during the scan; none known for the simulator's calibration |

- **The horizontal line scatters six times as much against the stereo depth as the vertical
  one** (half within 0.56 mm against 0.09 mm), with the same median. Probably because a
  horizontal line runs along the image rows, where the stereo depth is weakest; not looked
  into. A limit for this sign (H2) will have to be per line.
- **One sign was tried against a fault already:** a vertical laser plane moved 1.5 mm along
  its normal moves that line's laser-minus-stereo median past 0.8 mm and leaves the other
  line's within 0.05 mm (8 frames, 640x400 sphere, `tests/test_scan_health.py`).
- **It changes nothing it measures.** At given poses (the Straighten and Fuse stages) the
  mesh, the poses and the laser points are bit-identical with and without the block on both
  paths; tracked, on the GPU path. On the CPU path a tracked scan does not repeat from run to
  run with or without it (`docs/MAINTENANCE_PLAN.md`, F13), so it cannot be compared there.
- **Cost:** three runs each way, alternating, nothing else running, the PC on the "High
  performance" power plan: 95.75 s without and 98.33 s with the block (+2.6 s, 2.7 %).
  Almost all of it is at the end, the laser cloud against the mesh, which builds the mesh's
  search tree twice; the per-frame part does not show. Not timed on the CPU path.
- **The calibration file** has four optional keys since the same day: `id` (new on every
  save), `created`, `temperature_c`, and `steps` (when each of stereo, lasers and IMU was last
  calibrated, and at what temperature). An old file loads and saves as it was.
- **Through the app** (Make mesh on the bracket's back scan, 60 frames at 1280x800, 23 s): the
  result's report has the block, with the same picture as the long scan (row error median
  0.060 px; horizontal line half within 0.56 mm of the stereo depth, vertical 0.09 mm; tracker
  against gyro 0.0045°) and Straighten's entry filled in ("no_candidates", nothing changed).
- **Not in the block:** a merged scan has none. The four-laser build's per-line split is not
  checked by a test of its own. Nothing here is from a real scanner.

## Which signs show a stale calibration (calibration health, 2026-11-18)

[`docs/FEATURE_PLAN.md`](docs/FEATURE_PLAN.md), H2. Recordings rendered with the true calibration
(1280x800, realistic lenses, both lasers; 120 and 90 frames), processed with `process_recording`'s
defaults (every frame, GPU path) and a calibration that is slightly wrong, one fault at a time
(`tools/calib_health_study.py`; 98 runs, kept as JSON, and its tables rebuild from them). A cost is
how far a product moved from the run with the true calibration on the same recording, judged
against the simulator's true shapes, each product placed by ICP on its own: **mesh** (the part's
median distance and median offset), **measure** (the largest change among the bracket's shaft
diameter and the heights of base top, tab top, upright top and shaft axis above the table),
**laser** (the laser cloud), **refined** (the mesh after Refine with lasers). A product is
"0.1 mm off" when any of these reaches 0.1 mm. The floor: a 0.001° pitch already moves them by up
to 0.03 mm, because tracking takes a slightly different path.

**The answer.** Three signs pass the rule (they move clearly before a product is 0.1 mm off, and
stay quiet on hard scenes with a right calibration). Their limits are in `ohs/calib/health.py`,
for the GPU path, provisional until the real scanner (H5):

| Part | Sign in `report["health"]` | Check | Recalibrate | True calibration, 7 scenes |
|---|---|---|---|---|
| Cameras | row error, `row_error.median_px` | 0.15 px | 0.3 px | 0.046–0.066 px; 0.126 px on the speckled bracket |
| Lasers, per line | signed median against the mesh, `laser_mesh.per_line.<line>.median_mm` | 0.05 mm | 0.10 mm | within ±0.016 mm on every scene, both lines |
| IMU | tracker's rotation against the gyro's, `tracking.gyro_jump_deg.median` | 0.015° | 0.03° | 0.0023–0.0074°; 0.0099° on the speckled bracket |

### The true calibration on every scene

| Sign | bracket s0 | bracket s1 | dark bracket | speckled bracket | 0.5 mm plate | sphere | car panel |
|---|---|---|---|---|---|---|---|
| Row error, median (px) | 0.062 | 0.063 | 0.066 | **0.126** | 0.052 | 0.047 | 0.046 |
| Vertical line − mesh, median (mm) | +0.004 | +0.007 | +0.003 | +0.015 | +0.002 | −0.000 | +0.005 |
| Horizontal line − mesh, median (mm) | −0.010 | −0.003 | +0.007 | +0.016 | −0.002 | −0.002 | −0.003 |
| Tracker vs gyro, median (°) | 0.0047 | 0.0059 | 0.0050 | **0.0099** | 0.0025 | 0.0023 | 0.0074 |
| (dropped) vertical laser − stereo depth (mm) | +0.002 | −0.001 | +0.002 | +0.003 | −0.001 | +0.003 | −0.006 |
| (dropped) horizontal laser − stereo depth (mm) | +0.018 | +0.026 | −0.051 | **−0.131** | −0.022 | −0.018 | −0.016 |
| (dropped) both lines − mesh, median abs (mm) | 0.064 | 0.075 | 0.072 | 0.118 | 0.054 | 0.084 | 0.073 |
| (dropped) tracking residual (mm) | 0.36 | 0.35 | 0.35 | 0.59 | 0.11 | 0.11 | 0.20 |

The dark bracket has an albedo of 0.12; the speckled bracket has strong speckle and noise and
stands in for a shiny part (the renderer has no specular reflection).

### Bracket, seed 0: the signs and the costs per fault

| Fault | Size | Row error px | V − mesh mm | H − mesh mm | Gyro jump ° | mesh | measure | laser | refined |
|---|---|---|---|---|---|---|---|---|---|
| true | – | 0.062 | +0.004 | −0.010 | 0.005 | 0 | 0 | 0 | 0 |
| cameras pitch | 0.001° (floor) | 0.061 | +0.005 | −0.015 | 0.005 | 0.03 | 0.03 | 0.02 | 0.03 |
| | 0.01° | 0.124 | +0.015 | −0.060 | 0.004 | 0.03 | 0.10 | 0.03 | 0.10 |
| | 0.02° | 0.253 | +0.023 | −0.127 | 0.005 | 0.05 | 0.09 | 0.03 | 0.06 |
| | 0.05° | 0.632 | +0.054 | −0.195 | 0.005 | 0.14 | 0.17 | 0.03 | 0.17 |
| | 0.1° | 1.300 | −0.278 | – | – | tracking lost (0.8 % of frames) | | | |
| cameras yaw | 0.01° | 0.064 | −0.110 | −0.149 | 0.005 | 0.08 | 0.05 | 0.01 | 0.08 |
| | 0.02° | 0.065 | −0.218 | −0.290 | 0.005 | 0.10 | 0.16 | 0.02 | 0.15 |
| | 0.05° | 0.065 | −0.551 | −0.717 | 0.005 | 0.15 | 0.26 | 0.05 | 0.51 |
| | 0.1° | 0.070 | −1.094 | −1.431 | 0.005 | 0.37 | 0.51 | 0.12 | 0.94 |
| cameras roll | 0.02° | 0.076 | +0.073 | −0.013 | 0.005 | 0.02 | 0.04 | 0.01 | 0.04 |
| | 0.05° | 0.118 | +0.178 | −0.017 | 0.005 | 0.03 | 0.08 | 0.03 | 0.07 |
| | 0.1° | 0.200 | +0.353 | −0.025 | 0.006 | 0.11 | 0.14 | 0.07 | 0.12 |
| baseline | 0.1 % | 0.062 | +0.184 | +0.166 | 0.005 | 0.02 | 0.06 | 0.00 | 0.09 |
| | 0.2 % | 0.062 | +0.366 | +0.331 | 0.005 | 0.10 | 0.12 | 0.02 | 0.33 |
| | 0.5 % | 0.062 | +0.909 | +0.876 | 0.005 | 0.30 | 0.29 | 0.03 | 0.90 |
| | 1 % | 0.062 | +1.991 | +1.730 | 0.005 | 0.57 | 0.59 | 0.09 | 1.68 |
| vertical laser tilt | 0.02° | 0.062 | +0.120 | −0.010 | 0.005 | 0.00 | 0.00 | 0.02 | 0.03 |
| | 0.05° | 0.062 | +0.293 | −0.010 | 0.005 | 0.01 | 0.00 | 0.06 | 0.24 |
| | 0.1° | 0.062 | +0.576 | −0.010 | 0.005 | 0.00 | 0.00 | 0.14 | 0.49 |
| | 0.2° | 0.062 | +1.118 | −0.010 | 0.005 | 0.00 | 0.00 | 0.25 | 0.58 |
| horizontal laser tilt | 0.02° | 0.062 | +0.004 | +0.216 | 0.005 | 0.00 | 0.00 | 0.02 | 0.09 |
| | 0.05° | 0.062 | +0.004 | +0.556 | 0.005 | 0.00 | 0.00 | 0.10 | 0.55 |
| | 0.1° | 0.062 | +0.004 | +1.117 | 0.005 | 0.00 | 0.01 | 0.23 | 1.00 |
| | 0.2° | 0.062 | +0.004 | +2.181 | 0.005 | 0.00 | 0.00 | 0.31 | 0.49 |
| vertical laser offset | 0.05 mm | 0.062 | −0.078 | −0.010 | 0.005 | 0.00 | 0.00 | 0.01 | 0.00 |
| | 0.1 mm | 0.062 | −0.159 | −0.010 | 0.005 | 0.00 | 0.01 | 0.01 | 0.10 |
| | 0.2 mm | 0.062 | −0.319 | −0.010 | 0.005 | 0.00 | 0.01 | 0.04 | 0.21 |
| | 0.5 mm | 0.062 | −0.794 | −0.010 | 0.005 | 0.00 | 0.00 | 0.15 | 0.18 |
| horizontal laser offset | 0.05 mm | 0.062 | +0.004 | −0.131 | 0.005 | 0.01 | 0.01 | 0.02 | 0.07 |
| | 0.1 mm | 0.062 | +0.004 | −0.252 | 0.005 | 0.00 | 0.01 | 0.05 | 0.32 |
| | 0.2 mm | 0.062 | +0.004 | −0.497 | 0.005 | 0.00 | 0.00 | 0.12 | 0.57 |
| | 0.5 mm | 0.062 | +0.004 | −1.231 | 0.005 | 0.01 | 0.01 | 0.30 | 0.64 |
| IMU rotation | 0.5° | 0.062 | +0.004 | −0.010 | 0.008 | 0.02 | 0.01 | 0.00 | 0.02 |
| | 1° | 0.062 | +0.004 | −0.011 | 0.014 | 0.03 | 0.01 | 0.00 | 0.03 |
| | 2° | 0.062 | +0.004 | −0.011 | 0.026 | 0.02 | 0.02 | 0.00 | 0.02 |
| | 5° | 0.062 | +0.003 | −0.011 | 0.066 | 0.04 | 0.02 | 0.01 | 0.04 |
| IMU time offset | 2 ms | 0.062 | +0.003 | −0.010 | 0.005 | 0.03 | 0.01 | 0.00 | 0.03 |
| | 5 ms | 0.062 | +0.003 | −0.010 | 0.007 | 0.01 | 0.01 | 0.00 | 0.01 |
| | 10 ms | 0.062 | +0.003 | −0.009 | 0.012 | 0.00 | 0.02 | 0.00 | 0.02 |
| | 20 ms | 0.062 | +0.001 | −0.009 | 0.021 | 0.00 | 0.02 | 0.00 | 0.02 |

### The smallest fault that puts a product 0.1 mm off, and what the kept signs read there

Linear between the two sizes around it; a size counts only when every larger size also costs
0.1 mm.

| Fault | bracket s0 | bracket s1 | speckled | dark | Row error px | V − mesh mm | H − mesh mm | Gyro ° |
|---|---|---|---|---|---|---|---|---|
| cameras pitch | 0.025° | 0.039° | 0.040° | ~0.012° | 0.31 / 0.51 / 0.52 / 0.20 | ≤0.06 | −0.14 / gone | ≤0.007 |
| cameras yaw | 0.012° | 0.010° | 0.009° | < 0.02° (not resolved) | 0.064–0.128 | −0.11 to −0.14 | −0.14 to −0.18 | ≤0.010 |
| cameras roll | 0.066° | 0.038° | – | – | **0.14 / 0.09** | +0.23 / +0.16 | −0.02 / +0.01 | 0.005–0.007 |
| baseline | 0.10 % | 0.07 % | – | – | 0.062 | +0.19 / +0.15 | +0.17 / +0.14 | 0.005 |
| vertical laser tilt | 0.030° | 0.022° | – | – | 0.062 | +0.18 / +0.16 | ≤0.01 | 0.005 |
| horizontal laser tilt | 0.020° | 0.022° | – | – | 0.062 | ≤0.01 | +0.22 / +0.30 | 0.005 |
| vertical laser offset | 0.10 mm | > 0.2 mm | > 0.1 mm | > 0.1 mm | 0.062 | −0.17 (s0) | ≤0.01 | 0.005 |
| horizontal laser offset | 0.055 mm | 0.10 mm | > 0.1 mm | > 0.1 mm | 0.062 | ≤0.01 | −0.14 / −0.28 | 0.005 |
| IMU rotation | > 5° | > 5° | – | – | – | – | – | 0.066–0.070 at 5° |
| IMU time offset | > 20 ms | > 10 ms | > 10 ms | > 10 ms | – | – | – | 0.012–0.014 at 10 ms |

### What this says

- **How little it takes.** A hundredth to a few hundredths of a degree between the cameras, a
  tenth of a percent of baseline, two hundredths of a degree of a laser plane or 0.05–0.1 mm of
  its offset put a product 0.1 mm off. An IMU fault never did (up to 5° and 20 ms): the
  tracker's own fit holds the pose on these parts.
- **How each fault shows.** A pitch shows in the row error. The yaw and the baseline do not
  show in the row error at all; they move both laser lines' gaps the same way and by about the
  same amount. A laser fault moves its own line only. A pitch moves the horizontal line too
  (−0.13 mm at 0.02°), so the row error is read first.
- **The launch check's 0.6 px comes late.** At 0.6 px (a pitch of about 0.05°) the products are
  already 0.12–0.17 mm off, and at 1.3 px (0.1°) tracking was lost on every scene. Its limit is
  unchanged; lowering it is the owner's decision.
- **Dropped from the status** (they stay in the report; `health.DROPPED` says why): the laser
  points against the stereo depth (the horizontal line reads −0.13 mm on the speckled bracket
  with the true calibration and only 0.2–0.4 mm where a fault costs 0.1 mm), the row error's
  90 % and signed values, the depth check's drops and the points per frame (gross faults only),
  both lines together against the mesh (0.05–0.12 mm by scene), the tracking residual
  (0.11–0.59 mm by scene), the gyro jump's 95 %.
- **The IMU limits are not tied to a product,** since no IMU fault cost 0.1 mm: "check" is 1.5
  times the hardest true scene, "recalibrate" 1.5 times the gyro prior's own sigma (0.02° a
  frame). They catch a rotation from about 1° and a time offset from about 12–15 ms, and the
  sign grows with how fast the scanner turns (12–22° a second here).

### What no sign catches rightly before a product is 0.1 mm off

1. **A camera roll (about the optical axis) reads as a vertical-laser fault.** It costs 0.1 mm
   at 0.038–0.066°, where the row error reads 0.09–0.14 px, under its check limit, and the
   vertical line's gap reads 0.16–0.23 mm. So it is caught, but the wrong part is named, and
   redoing the lasers would not mend it.
2. **On the CPU path only the row error can be used.** With the true calibration the CPU path
   reads 0.036° for the gyro sign (over "recalibrate") and 0.08–0.13 mm for the horizontal
   line's gap, because its tracking is less accurate and does not repeat
   (`docs/MAINTENANCE_PLAN.md`, F13). The row error is the same on both paths (0.0618 px) and
   still caught the pitch. A laser or IMU fault cannot be seen there
   (`health.CPU_PATH = ("row_error",)`).
3. **Thin margins on the dark bracket.** A pitch costs 0.1 mm there at about 0.012°, where the
   row error reads 0.20 px: over "check", under "recalibrate". The yaw's crossing there is not
   resolved: at 0.02°, the smallest size run, the mesh is already 0.55 mm off (both gaps read
   0.27 and 0.35 mm, well past "recalibrate"), and it did not grow steadily with the fault.
4. **A thin margin on the speckled bracket:** its row error with the true calibration is
   0.126 px against a "check" limit of 0.15 px. A harder real surface may read "Check
   recommended" with a good calibration; the status warns, it does not block.
5. **Not measured:** two faults at once (a yaw and a baseline error push the gaps opposite
   ways and could cancel there while the mesh bends), a bent head that moves cameras and lasers
   together, faults in the negative direction, the four-laser build, the temperature (the
   simulator has no warm-up: H5), Straighten's correction (only the server's stages fill it in).

**Cut to keep the study to 70 minutes of computing:** the second seed got a reduced sweep; the
hard scenes 4 faults at 1–2 sizes each; the plate, sphere and car panel the true calibration
only; default settings, not the app's Standard quality; the row error from 4 frames a scan.
The costs have a floor of about 0.03 mm, so a crossing near 0.1 mm is coarse (the pitch read
0.103 mm at 0.01° and 0.087 at 0.02°). The bracket's boss diameters were left out of
"measure": their fits moved 4 mm between two runs of the same calibration.

**CPU-path check** (bracket s0): the row error at a 0.02° pitch is 0.2525 px, as on the GPU; a
yaw of 0.02° / 0.1° reads −0.25 / −1.02 mm (vertical) and −0.22 / −1.41 mm (horizontal); the
product costs run 0.10–0.12 mm even for faults that cost nothing on the GPU path, so costs
cannot be measured to 0.1 mm there.

Tests: `tests/test_calib_health_study.py` (41: the perturbation helpers, the limits, and one
fault per part past its "check" limit on a 12-frame scan with the true calibration below it);
50 pass with H1's on both paths. Repeat with `python tools/calib_health_study.py all <folder>`.

## The calibration's status (calibration health, 2026-11-19)

[`docs/FEATURE_PLAN.md`](docs/FEATURE_PLAN.md), H3. `ohs.calib.health.status` joins the launch
check, the last 2-minute check and the last scans made with this calibration into **Valid**,
**Check recommended** or **Recalibration required**, with the part, one sentence and the wizard
step to do next; `scan_status` says the same of one scan alone, in its report (`health_status`).
It warns; it never blocks a scan. The limits are those of the section before; four choices were
added here, each one named constant in `ohs/calib/health.py`, all provisional until the real
scanner:

| Choice | Value | What it rests on |
|---|---|---|
| A 2-minute check (or a calibration step) older than this: Check recommended | 14 days | nothing measured: the simulator cannot say how fast a real scanner drifts |
| How many of the last scans must agree before the status moves | 2 of the last 3 | one hard surface reads high with a right calibration (the speckled bracket's row error 0.126 px against 0.15) |
| The launch check's row error from which a check is recommended | 0.3 px (its 0.6 px alert is unchanged) | the table below |
| The fewest processed frames for a scan's laser and IMU signs to count | 90 | the short-scan figures below |

**The launch check against a scan's row error** (3 idle frames at half resolution, 6 groups of
frames per scene on the seven scenes, in full-resolution pixels as a scan's row error is):

| Calibration | The launch check reads | Its own state |
|---|---|---|
| True | 0.054–0.115 px (median per scene); up to 0.148 px for one group on the speckled bracket | ok |
| Pitch 0.01° | 0.14–0.18 px | ok |
| Pitch 0.02° | 0.26–0.31 px | ok |
| Pitch 0.03° | 0.40–0.44 px | ok |
| Pitch 0.05° | 0.65–0.71 px | drift, on every scene |

It reads about 0.03 px above a scan's row error at the same pitch, and a right calibration
reached 0.148 px once, so the scans' "check" limit of 0.15 px cannot judge it honestly; 0.3 px
can, as "Check recommended, confirm with the 2-minute check".

**Short scans.** The true calibration on pieces of the study's recordings: on the speckled
bracket the horizontal line against the mesh reads 0.04–0.11 mm at 12 frames and 0.03–0.08 mm
at 30, against 0.016 mm at 90, past both laser limits; the plain and dark brackets stay under
0.031 mm; the row error holds at 0.12–0.13 px on every piece. So a scan under 90 processed
frames judges the cameras only, and says that the lasers and the IMU need a longer scan.

**The status replayed over the study's 98 runs** (each run as one scan's own status, GPU path;
30 pairs of fault and recording):

| Outcome | Pairs | Which |
|---|---|---|
| The right part named before the fault costs 0.1 mm | 10 | |
| The right part, first at the smallest size that was run, already past 0.1 mm | 5 | dark pitch and yaw, second-seed baseline and yaw, speckled yaw |
| The wrong part named first | 4 | camera roll on both seeds (named: vertical laser); pitch 0.01° on both seeds (named: horizontal laser, "check"; the cameras from 0.02°) |
| No warning before 0.1 mm | 0 | |
| The fault never cost 0.1 mm | 11 | 8 warned, with the right part; 3 IMU time offsets silent |

- **No false alarm:** the true calibration is Valid on all seven scenes.
- **Where the wrong part is named, the words say so:** for one laser line off, the action is
  "Redo the laser calibration" and then "If later scans still show it, redo the camera
  calibration: a small turn of one camera looks just the same".
- **On the CPU path** the pitch is caught; a yaw, a laser offset and an IMU rotation are not
  judged there by design, although they cost 0.1 mm or more: the status says "the lasers and
  the IMU cannot be judged on this computer", neither stale nor fine.
- **Silent and harmless:** an IMU rotation up to 1° and a time offset up to 10 ms, a pitch of
  0.001°. None cost 0.1 mm.
- **Two scans, not one:** with "2 of the last 3" a real fault moves the calibration's status on
  the second scan after it; the first scan's own report already says it. A gross camera fault
  is caught by the launch check at the next connect.

Tests: `tests/test_calib_health_status.py` (43, on hand-made data: every rule),
`tests/test_server_calib_health.py` (5), `tests/test_calib_health_scans.py` (6, slow: real
processing with the study's wrong calibrations; each processing runs in a child process with a
15-minute limit, because the CPU path can crash natively, `docs/MAINTENANCE_PLAN.md`, F6).
Not verified: anything on real hardware; the four-laser build; two faults at once.

**Without a GPU (since 2026-11-20).** With the CPU path's defaults at full resolution the true
calibration stayed Valid for all three parts on the bracket and on the dark, speckled and 0.5 mm
plate scenes (lines 0.013 mm at most, gyro 0.0093° a frame at most, the speckled bracket's row
error 0.129 px against 0.15 px); the 0.2 mm laser fault read Recalibration required for the lasers
(-0.357 mm) and the 3° IMU fault for the IMU (0.0335°): the same limits as with a GPU. A scan of
that path without those settings (an older one, half resolution, or a setting off) is judged for
the cameras only, and its note says which.

## The CPU path's ray cast: a crash, a hang and runs that differed (2026-11-19)

`MAINTENANCE_PLAN.md` F6 and F13. On a computer without a usable GPU the tracker's model is
Open3D's voxel grid, and each frame is registered against a ray cast of it. That ray cast (Open3D
0.19 and 0.20) ran out of room in a buffer it keeps from cast to cast, on every cast of a growing
model: it then left blocks out, and read a few entries nobody had written. The tracker now keeps
that buffer larger than any cast can need (`MarkerlessTracker._reserve_fragments`).

**How often it ran out of room.** The 90-frame simulated bracket at 1280x800 (H2's `bracket-s0`,
seed 7), processed on the CPU path with Open3D's messages let through: 49 ray casts of the two
trackers (the probe and the main one), **71 warnings** "Could not generate full range map", one or
two a cast (the warm-up cast and the cast itself). With the fix: none
(`tests/test_track_markerless.py::test_open3d_ray_cast_always_has_room_for_its_fragments`).

**Before and after**, the same recording, `process_recording` in a new process each run, several
threads, power plan High performance. "Before" is the cast as it was (a warm-up cast after each
growth of 3 %, no room kept). Position error: of the camera, against the simulator's truth,
aligned at the first frame.

| Calibration | Code | Runs | Ended | Frames tracked | Position error, median | Last frame | Runs alike |
|---|---|---:|---|---:|---:|---:|---|
| true | before | 4 | 1 access violation, 3 clean | 85–87 of 90 | 47–50 mm | 267–271 mm | no: 3 different sets of poses |
| true | after | 2 | clean | 90 of 90 | 7.8 mm | 9.4 mm | yes, bit for bit |
| pitched 0.03° | before | 4 | 1 clean, 1 heap corruption, 1 access violation, 1 hang (2 h, stopped by hand) | 87 of 90 | 18.9 mm | 274 mm | (one run) |
| pitched 0.03° | after | 6 | clean | 88 of 90 | 22.5 mm | 359 mm | yes, bit for bit |
| true | GPU path | 1 | clean | 90 of 90 | 0.37 mm | 0.61 mm | (known to repeat) |
| pitched 0.03° | GPU path | 1 | clean | 90 of 90 | 0.24 mm | 0.90 mm | |

- "Alike, bit for bit": the same poses, and the same mesh vertices as a set (428,366 with the
  true calibration; the file has them in another order each run, as on the GPU path).
- A run takes 46–48 s after and 41–54 s before (GPU path: 21 s), so the time is about the same:
  the warm-up casts are gone, and the sizing casts are rare.
- **What it does not mend.** The CPU path is still far from the GPU path on this scan: its
  tracker turns 0.043° a frame away from the gyro (GPU: 0.005°), the last frame ends 3.6° off,
  and with the pitched calibration it loses the part. The laser lines then read 0.11 and 0.18 mm
  off the mesh with a right calibration, which is why the calibration status judges only the
  cameras on this path. That is what is left of F13.

## The CPU matcher's depth in detail mode (2026-11-19)

`MAINTENANCE_PLAN.md` F13. After the ray cast fix above the CPU path repeated its results, which
made its remaining error measurable: on the 90-frame bracket at 1280x800 it ended 9.4 mm and 3.6°
from the truth where the GPU path ends 0.61 mm and 0.17°.

**Which half carries it.** The CPU path changes the depth matcher and the tracker's model at once.
Taken apart (true calibration; "turn" is the tracker's turn per frame against the scanner's true
turn, minus meaning it turned less):

| Depth + model | Position error, median / last frame | Rotation, last frame | Tracker against the gyro, a frame | Turn |
|---|---:|---:|---:|---:|
| GPU + GPU (the default) | 0.37 / 0.61 mm | 0.17° | 0.0051° | -0.3 % |
| GPU + Open3D | 0.76 / 0.43 mm | 0.45° | 0.0075° | -0.9 % |
| CPU + GPU | 3.91 / 5.73 mm | 2.31° | 0.0265° | -3.6 % |
| CPU + Open3D (no GPU at all) | 7.78 / 9.42 mm | 3.65° | 0.0429° | -6.5 % |

The depth carries most of it. The Open3D model itself checked out against an exact surface (a
noise-free sphere on a plane, integrated at known poses and cast at a known pose: point-to-plane
error -0.001 mm on both models).

**What is wrong in the depth.** The bracket is a detailed part, so the tracker gets the filtered
depth plus the raw depth where the filter dropped a pixel ("Large and detailed parts"). Raw depth
against the simulator's true depth, by distance to the nearest pixel without depth:

| Matcher | 1 px | 2 px | 3–6 px | beyond 15 px |
|---|---:|---:|---:|---:|
| CPU (SGBM) | +0.49 mm | +0.30 mm | +0.25 mm | +0.19 mm |
| GPU | +0.37 mm | +0.16 mm | about 0 | about 0 |

The holes lie where the projected dots leave gaps, so they move with the scanner: an error fixed
to the camera, which holds every frame near the previous pose.

**The rule and its size.** With the CPU matcher, detail mode leaves its raw depth out within a
few pixels of a hole (`without_hole_borders` in `ohs/app/pipeline.py`):

| Left out within | Median | Last frame | Rotation, last frame |
|---|---:|---:|---:|
| 0 px (before) | 7.78 mm | 9.42 mm | 3.65° |
| 3 px | 2.5 mm | 4.9 mm | 1.30° |
| **6 px (kept)** | **0.88 mm** | **0.95 mm** | **0.58°** |
| 10 px | 1.84 mm | 0.40 mm | 0.34° |

**With the rule, CPU path** (the first two rows re-measured in review, two runs identical; the
hard scenes are the study's numbers):

| Scene | Before: last frame, rotation | After |
|---|---|---|
| Bracket, true calibration | 9.42 mm, 3.65° | 0.95 mm, 0.58° (90 of 90 tracked) |
| Bracket, calibration pitched 0.03° | 359 mm, 2.59° (88 of 90) | 1.18 mm, 0.56° (90 of 90) |
| Dark bracket | 245 mm, 0.77° | 167 mm, 0.13° |
| Speckled bracket | 33 mm, 0.94° | 23 mm, 0.13° |
| 0.5 mm plate | 85 mm, 0.42° | 36 mm, 0.12° |
| Sphere, car panel, the 8-frame recording | 0.31 mm, 0.60 mm, 0.04 mm | the same (never in detail mode) |

- The GPU path is untouched: the same poses and the same mesh vertices on the bracket, bit for
  bit, before and after.
- The rotation is right on every scene now; the hard scenes still slide (dark, speckled, plate),
  which is a separate fault of the CPU path (`MAINTENANCE_PLAN.md` F14).
- The laser lines still read 0.12 mm off the mesh on this path with a right calibration (the CPU
  matcher reads about 0.08 px too far over the whole image), so the calibration status still
  judges only the cameras there.

## What a scan takes on disk (data retention, 2026-11-19)

Measured with `GET /api/projects/{pid}/disk` (`ohs/server/disk.py`, FEATURE_PLAN R1) on the
example projects of the simulated scanner (Studio's demo replay and the simulated datasets).
Sizes are the files' sizes; 1 MB is 1,048,576 bytes and 1 KB 1,024 bytes, as the app shows them.
Frame count and duration are each recording's own (`meta.json`, or `project.json` where the
recording has no duration). Every frame is one 8-bit grey PNG with both eyes side by side,
written at PNG compression level 1 (`RecordingWriter`, `png_compression=1`, `ohs/io/recording.py`).
All recordings ran at 30 frames a second (29.85 to 30.07).

**Per scan** (only scans with a recording; shares are of the scan's whole folder):

| Scan | Quality | Eye size | Frames | Seconds | Scan folder | Bytes a frame | Recording a second | Recording | Stages | Undo | Result | Other |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| my-first-scan / 20261109-104221 | full | 1280×800 | 449 | 14.9 | 688.6 MB | 1232 KB | 36.2 MB | 79 % | 7 % | 5 % | 9 % | 0 % |
| my-first-scan / 20261109-155808 | none | 640×400 | 525 | 17.5 | 164.6 MB | 320 KB | 9.4 MB | 100 % | 0 % | 0 % | 0 % | 0 % |
| my-first-scan / 20261109-184008 | preview | 1280×800 | 481 | 16.1 | 620.5 MB | 1232 KB | 36.1 MB | 93 % | 2 % | 2 % | 3 % | 0 % |
| my-first-scan / 20261109-184037 | full | 1280×800 | 584 | 19.6 | 885.2 MB | 1232 KB | 36.0 MB | 79 % | 3 % | 11 % | 7 % | 0 % |
| my-first-scan / 20261110-200857 | standard | 1280×800 | 355 | 11.9 | 572.4 MB | 1232 KB | 36.0 MB | 75 % | 4 % | 10 % | 11 % | 0 % |
| my-first-scan / 20261112-190513 | standard | 1280×800 | 619 | 20.7 | 934.4 MB | 1232 KB | 36.0 MB | 80 % | 5 % | 8 % | 7 % | 0 % |
| my-first-scan / 20261113-141218 | standard | 1280×800 | 761 | 25.4 | 1055.2 MB | 1232 KB | 36.1 MB | 87 % | 2 % | 5 % | 6 % | 0 % |
| my-first-scan / 20261113-154619 | standard | 1280×800 | 734 | 24.5 | 1066.1 MB | 1232 KB | 36.1 MB | 83 % | 4 % | 7 % | 6 % | 0 % |
| bracket / 20261115-202720 | standard | 1280×800 | 60 | 2.0 | 167.1 MB | 1210 KB | 35.5 MB | 42 % | 11 % | 13 % | 32 % | 2 % |
| bracket / 20261115-202720-2 | standard | 1280×800 | 60 | 2.0 | 91.0 MB | 1192 KB | 35.0 MB | 77 % | 0 % | 0 % | 23 % | 0 % |
| bracket / 20261115-211837 | standard | 1280×800 | 60 | 2.0 | 204.0 MB | 1190 KB | 34.9 MB | 34 % | 10 % | 26 % | 29 % | 0 % |

"Quality" is the last processing's ("full" is the old name of standard; "none": never
processed). A stage folder holds 19 to 24 MB for each stage run (track, straighten,
spacetime; track and refine of the preview scan 5.9 MB each), the undo cache 13 to 95 MB, and
the result 62 to 65 MB at standard or full quality in my-first-scan (15.6 MB at preview), 21 to
60 MB in the bracket project. In scan 20261113-154619, `mesh.stl` is 38.4 MB of the result's
62.0 MB.

**All 1280×800 recordings together:** 4,163 frames, 139.0 s, 1,260,338 bytes (1231 KB) a frame
and 36.0 MB a second of scanning. The IMU, frame list and events add 0.4 to 0.8 MB a recording.
Of the 6,284.7 MB of those ten scan folders: recording 79.7 %, stages 4.1 %, undo 7.7 %,
result 8.4 %, other files (state, thumbnail, kept features) 0.1 %.

**Scans without a recording** (made by merging, or imported results): 2.6 to 170.3 MB each, of
which the result is 60 % to 100 %.

**Projects:** my-first-scan 5,987.1 MB (recordings 4,962.3 MB, stages 219.0, undo 406.3,
result 396.5); bracket-two-sides 776.5 MB (recordings 210.8, stages 108.1, undo 130.5, result
324.6). The trash held one deleted project of 976.2 MB, counted on its own.

**PNG level against size and time to save** (30 frames of the 2560×800 side-by-side image of
scan 20261113-154619 encoded again in memory with OpenCV, one thread, on this PC while other
heavy jobs ran, so the times are rough):

| Level | Bytes a frame | Of the raw image | Time a frame |
|---:|---:|---:|---:|
| 0 (stored) | 2004 KB | 100 % | 21 ms |
| 1 (today) | 1232 KB | 62 % | 55 ms |
| 2 | 1226 KB | 61 % | 72 ms |
| 3 | 1206 KB | 60 % | 97 ms |
| 6 | 1178 KB | 59 % | 173 ms |
| 9 | 1178 KB | 59 % | 159 ms |

Levels above 1 save at most 4 % more space for two to three times the time. The simulated frames
are synthetic (rendered dots and noise); real camera frames may compress differently (roadmap
question 9).

**What the route costs:** one walk of the project folder, 56 to 148 ms for my-first-scan
(4,680 files, 5,987 MB) on this PC while other jobs ran, 20 to 119 ms for bracket-two-sides. It runs
in the server's thread pool and the project page asks for it after it has painted.

Tests: `tests/test_server_disk.py` (4: every number equals the files' sizes, the trash on its
own, links not followed, unknown projects), `ui/src/lib/diskUse.test.ts`.
Not verified: real camera frames; a recording at 60 frames a second.

## Why an area is weak, and where to scan it from (2026-11-19)

FEATURE_PLAN N1, the engine half. The Check answer's areas (`ohs/mesh/check.py`, `weak_areas`) get a
reason each, and `ohs/guide/views.py` a pose to scan each from. `mesh.ply` carries each vertex's
count of independent looks (`float looks`, decision D6). Simulator only; scratch scripts in the
session's `n1\` and `n1rev\` folders.

**The reasons, in the order they are tried.** A rule holds for an area when it holds for at least
half of its vertices (at most 400 of them for the rules that need the camera path).

| Reason | Holds when | Where the limit comes from |
|---|---|---|
| `not_seen` | the area is filled-in surface, or most of it lies within 3 mm (at 1 mm voxels) of a long open border of the mesh | twice the length over which the model's border term falls off; border loops of 15 mm or less (small holes) do not count |
| `steep` | even the squarest look was more than 59.6° off the surface's normal | where the GPU matcher's sigma is twice its square-on value |
| `far` | even the nearest look was farther than 1.41 times the working distance (359 mm at 254 mm) | stereo noise grows with the square of the depth: twice the noise |
| `few_looks` | the area's median looks are under half those of the surface that passes | the noise part of sigma goes with 1/sqrt(looks) |
| `edge` | the mesh's own curvature term alone is above the tolerance | another pass does not lower that term |
| `unknown` | none holds, or the data are missing (the answer says which) | never a guess |

The working distance is read from the calibration: where the laser sheets cross the cameras'
centre line, 254 mm on the nominal scanner. What a frame "saw": the vertex is in its image and depth
range, faces the camera, and the ray to the camera hits nothing of the mesh.

**On simulated scans** (a thin plate on its table, 640x400, tolerance 1.0 mm):

| How it was scanned | Result |
|---|---|
| from 24 to 30° above the table only | `steep` for 99.8 % of the listed area (squarest look 69° off) |
| from 385 to 415 mm only | `far` for 98 % (nearest look 412 to 418 mm) |
| square on at the working distance (control) | no `steep`, no `far` area |
| a patch seen in one 12-frame pass, merged with a 60-frame scan | `few_looks` holds for at least 80 % of the area round it (2 looks against 11); on the patch's rim `not_seen` comes first |

On the CPU path (the numpy twin of the model, no looks) `steep` and `far` come out the same. Open3D's
model, the CPU path's default, has no sigma, so its Check lists nothing to guide.

**On a real-size scan** (809,000 faces at 1 mm voxels, 355 frames, an older file without looks),
found in review:

| Tolerance | Share that passes | `steep` | `far` | `edge` | `unknown` | Areas with a pose |
|---|---|---|---|---|---|---|
| 0.1 mm | 35.5 % | 19 | 5 | 21 | 5 | 29 of 50 |
| 0.3 mm | 76.1 % | 20 | 1 | 0 | 29 | 50 of 50 |

- At 0.1 mm the mesh's curvature term alone is above the tolerance on 58.5 % of the vertices (its
  median is 0.055 mm): a 1 mm-voxel mesh cannot meet 0.1 mm over most of a part, whatever is
  scanned. That is why `edge` is tried after the reasons a second pass can act on. In the first
  version it was tried before them, and the largest area (158,656 mm², seen 62° off from 377 mm)
  was told "no pose"; 15 of 50 areas had one.
- Time, first version: the plain Check takes 1.3 s (0.1 mm) and 0.5 s (0.3 mm); with the reasons
  13.5 s, the poses 3 to 4 s, the short plan 2.5 s, about 20 s in all.
- Time, since the route (2026-11-20): the plain Check is unchanged (its route asks for no reasons);
  reasons, poses and plan come from a second route. Back to back on a loaded PC they took 33 to
  56 s before and 11 to 16 s after (2.1 to 3.6 times faster; the plain Check took 4 to 9 s in the
  same rounds). In the app, PC still busy: 8.3 s for the first call at 0.1 mm, 2.7 s at 0.3 mm on
  the same mesh, 0.02 s when asked again. Not yet timed on a quiet PC; the target is under 5 s.
  What changed: the rays of all of an area's directions are cast in one call, the mesh's parts
  (ray scene, open border, curvature) are made once per mesh version and side by side, and at
  most 120 frames of the camera path are looked at, evenly in time (no reason and no pose changed
  on this scan with the cap: 19 steep, 5 far, 21 edge, 5 unknown; 29 poses).

**The pose.** 25 directions (0, 20, 40 and 55° off the area's normal, 8 round each ring), the
scanner at the working distance with the cameras' centre line through the area's centre. From each,
up to 300 of the area's vertices are tested from both cameras: out of view, facing away, steep, or
blocked by the mesh. The score is the share of the area seen plus the share of the open border
beside it that is looked past from outside; the squarest direction within 0.02 of the best wins.
Under 10 % seen there is no pose and the answer says why. An area whose only reason is `edge` gets
none.

**Thin parts.** Shadow rays start 0.05 mm off the surface. Measured: a sheet 0.06 mm over a point
blocks it, one 0.04 mm over it does not; a sheet of any thickness farther away blocks. On a 0.5 mm
plate the top face is seen from above and the bottom face faces away; the table 0.3 mm beside the
plate is blocked from 55° on the far side; a 0.5 mm roof blocks as a 10 mm one does; a 1 mm fin
shades exactly its geometric shadow.

**The extra value changes nothing else.** With `looks` the PLY header gains two lines, each vertex
record is the old bytes plus 4, the face bytes are the same. On the 90-frame bench recording (GPU
path, full and preview quality) vertices, faces, normals, sigma, poses and laser points are the same
before and after. File size +7.4 % (27.60 to 29.65 MB); reading it costs 2.2 ms per 530,000
vertices; frame time is untouched. Read by `load_ply`, the server's edits, STL export and the
app's TypeScript reader, each tested with and without it. Since 2026-11-20 it follows every edit in
the app as the sigma does (surface that hole filling makes has 0, read as "no looks here").

**The whole loop.** The bracket scanned from one side, then 3 short passes (20 frames each), merged
at true poses; 1280x800, GPU path, tolerance 1.0 mm. Guided: the engine's plan for the part's
areas. Random: 3 directions aimed at the part at the working distance. The gain is in points of the
share of the *first scan's surface* that meets the tolerance:

| Seed | Guided | Random |
|---|---|---|
| 0 | +15.3 | +5.0 |
| 1 | +12.0 | +11.1 |
| 2 | +14.4 | +0.6 |
| 3 | +15.2 | +12.5 |
| 4 | +9.2 | +12.8 |
| Mean (range) | +13.2 (9.2 to 15.3) | +8.4 (0.6 to 12.8) |

- **Guided is better on 4 of 5 seeds;** seed 4 loses (+9.2 against +12.8).
- **The same from run to run since 2026-11-20:** two runs of all five seeds gave the same plan and
  the same numbers. Before, the areas' samples were picked by vertex number, and the GPU path
  lists the same vertices in a different order each run: seed 0 gave +7.9 in one run and +15.3 in
  another, seed 1 +17.7 and +10.4. Samples are now picked by position (the table above).
- **On the whole part's surface** (new surface counts too) guided is about the same as random:
  +24.8 points (19.5 to 31.1) against +23.1 (5.4 to 36.5), though never as bad as random's worst.
  Random passes from other sides find surface the first scan never saw, which no weak area points
  to.
- **At 640x400 the guided passes show no gain.** GPU path, seed 0, with the first sampling: +4.5
  against random's +5.5. CPU path (numpy twin, no looks), seed 0: +3.1 with the first sampling,
  which was luck; +0.1 and -0.1 in two runs with samples picked by position (random +2.7). The
  loop's test runs at 1280x800 on the GPU path and is skipped on the CPU path (the numpy model is
  far too slow at 1280x800); its assertions are unchanged. Open for the owner.
- Limits: simulator, one part, true poses, 3 passes.

Guards: `tests/test_mesh_looks.py`, `test_mesh_check_reasons.py`, `test_mesh_check_context.py` (the
same answer whatever the order of the vertices and faces), `test_mesh_carry_looks.py`,
`test_guide_views.py`, `test_guide_loop.py` (slow), `test_server_check_why.py`.

## A second pass that joins the first (FEATURE_PLAN N2, 2026-11-20)

The simulated bracket replayed as the device through the real server, 1280x800, GPU path, the
"General" preset, the part not moved. Measured by an Opus agent. The first scan is one 90-frame
side sweep after a 2 s hold: a replay loses its first frames before the scan starts, and without
the hold 48 to 54 frames of the sweep were recorded, so the scan and the area asked for changed
from run to run (in one run the pass's alignment was refused, rightly, as "not sure which way it
goes"). The pass is 60 frames round N1's pose for the largest area on the part that has a place to
scan from: the same "not seen" area at the part's foot in every run, 4,760 to 4,950 mm².

| Run | Check share at 1.0 mm, first to merged (whole meshes) | Areas, first to merged | Asked area that passes afterwards |
|---|---|---|---|
| a | 0.916 to 0.928 | 159 to 176 | 33 % of 4,759 mm² |
| b | 0.910 to 0.923 | 92 to 142 | 15 % of 4,919 mm² |
| c | 0.911 to 0.924 | 90 to 131 | 32 % of 4,951 mm² |

- Alignment accepted every time: 70 to 81 % of the part within 1 mm, about 0.3 mm RMS.
- On the surface both scans hold (run c: all of the first scan's surface is found on the merged
  mesh, 5 mm² missing): 0.911 to 0.920. The merged scan lists more areas than the first because it
  holds more surface; that is why the app tells the change on the shared surface.
- **The gain is small and real:** the pass adds what the first scan saw badly; the asked area
  passed nowhere before. Where both scans saw a place the merge averages it, so the well-seen
  surface is no more accurate than before (as "Aligning and merging two scans" found).
- Before the hold, with 48 to 54 frames: 0.928 to 0.934, 0.930 to 0.937, 0.932 to 0.939 (30 to 37 %
  of the asked area).
- At 640x400 the tracked pass was refused (residual spread 0.72 mm against the 0.7 mm limit; it was
  2.9 mm off): the refusal was right.
- CPU path: the chain runs and merges; the meshes have no sigma there, so only the extent of the
  shared surface is told.
- **A merge follows the first scan's preset:** two bracket scans with "Thin sheet metal and small
  parts" merge at 0.5 mm (1,966,197 vertices on the GPU path; before the fix at the default); with
  General the merged mesh is the same as before on both paths (106,426 and 146,477 vertices).
- Frame time with and without a link to a first scan, 3 interleaved pairs on a busy PC: ratios
  1.17, 0.95, 0.85. Nothing of this runs per frame.
- In the app with the simulator (the ball on its table, first scan at 0.5 mm voxels, a 12 s pass,
  part not moved): alignment accepted from the tags, merged at 0.5 mm with both tables kept
  (3.4 million triangles); the whole meshes read 64.8 % and 64.7 % at 0.3 mm, and on the shared
  surface "stayed about the same". The comparison took 187 s there on a busy PC.

## Hints while scanning (FEATURE_PLAN N3, 2026-11-20)

An experiment with a stop rule: kept only if scans that follow the hints end with a clearly higher
Check share. Measured by an Opus agent with a simulated closed loop (scratch scripts in the
session's `n3\` folder): 180 frames at 30 frames a second, 640x400, in stretches of 0.5 s; every
3rd frame goes through the real scan session with live tracking, and the live model is rebuilt every
1.5 s. Three operators with the same hand model: one does what the hint on screen says after each
stretch (come round: orbit that way at 30° a second; higher or lower: 12°; closer: 40 mm; turn: aim
30 mm towards the lit patch), one keeps to the scripted orbit, one makes as many moves as the
follower did, at random. Each recording is then processed at its true poses.

Check share at 1.0 mm, whole mesh (follow / scripted / random):

| Scene, path | Seed 0 | Seed 1 | Seed 2 | Mean |
|---|---|---|---|---|
| Bracket, GPU | 0.805 / 0.695 / 0.765 | 0.783 / 0.720 / 0.747 | 0.755 / 0.728 / 0.864 | 0.781 / 0.714 / 0.792 |
| Car panel, GPU | 0.534 / 0.533 / 0.543 | 0.607 / 0.570 / 0.622 | 0.487 / 0.576 / 0.622 | 0.543 / 0.560 / 0.596 |
| Bracket, CPU | 0.748 / 0.757 / 0.777 | 0.723 / 0.750 / 0.759 | not run | 0.735 / 0.753 / 0.768 |

- The scripted share's spread over seeds is 0.017 (bracket) and 0.023 (car panel).
- Bracket: following the hints is +0.067 over the scripted path, more than the spread, but -0.011
  against random moves made as often. Car panel: -0.017 and -0.053. The part alone, bracket:
  0.298 / 0.268 / 0.291. At 0.5 mm the picture is the same.
- **Not adopted:** the gain is from moving more, not from where the hints point.
- Cost, measured back to back on a busy PC (ratios only): the per-frame part takes 0.09 to 0.14 ms,
  about 0.04 % of a frame; rebuilding the live model takes about 2.5 times as long with it (66-83
  ms to 175-227 ms, every 1.5 s or slower). Switched off it is one test per frame.
- With it on or off, every frame's pose, depth and laser points and the live mesh's vertices are
  the same (hashes over a 90-frame recording, 3 interleaved pairs).
- Limits: simulation; 3 seeds (2 on the CPU path); 6 s scans; the operator's reactions are a model.

## SGBM's paths from above, and the CPU path's slides (2026-11-20)

MAINTENANCE_PLAN F14, first round (an Opus agent; scratch in the session's `f14\` folder). CPU path
only; the GPU path is not touched. Runs repeat bit for bit; timings were taken on a fully loaded PC
and are not evidence. Three more recordings were made with only the seed changed: dark bracket
(seed 12, "dark2"), speckled bracket (13, "speck2"), 0.5 mm plate (14, "plate2").

**The depth bias: found.** OpenCV's SGBM, in its 3-way mode and every single-pass mode, sums its
path costs only from the left, the right and above; no path comes up from below. Between the dots a
pixel inherits the disparity of the pixels above it. Where the disparity grows down the image (a
bench seen from above at an angle) that reads too far.

Synthetic dot planes, 1280x800, no simulator (mean disparity error, px):

| Plane | 3-way SGBM | GPU matcher | SGBM's two-pass modes |
|---|---|---|---|
| disparity +0.05 px a row | -0.055 | 0.000 | 0.007 or less |
| disparity -0.05 px a row | +0.066 | 0.000 | 0.007 or less |
| slanted sideways | 0.003 | | |
| facing the cameras | 0.003 | | |

- Single-pass `sgbm` mode doubles it (-0.108 px).
- Not the images: with the simulator's gain mismatch, vignetting, noise, blur, speckle, lasers or
  ambient light changed one at a time, the bias stays (-0.070 to -0.084 px).
- Not our rectification or depth conversion: the GPU matcher shares them and reads 0.000.
- Not the sub-pixel (LK) step: it corrects a pixel by its textured neighbours, which are right, so
  it cannot mend the pixels between the dots where the bias sits. Raw SGBM -0.106 px, after the
  step -0.075, after six steps -0.069; the 30 % of pixels the step leaves alone read -0.19 px.

**A fix, measured: match the pair upside down too, and average** (`flip_average`; a pixel needs a
disparity in both). It cancels the bias by symmetry and costs twice the SGBM time. Mean disparity
error after the sub-pixel step (px):

| Scene | 3-way | Flip-average | Two-pass, 4 paths | GPU |
|---|---|---|---|---|
| Bracket, 1280 | -0.075 | +0.008 | +0.008 | 0.000 |
| Car panel, 1280 | -0.068 | +0.011 | +0.010 | +0.001 |
| Dark bracket, 1280 | -0.084 | +0.007 | | +0.003 |
| Sphere, 640 (8 frames) | -0.012 | -0.003 | -0.003 | +0.001 |

- A constant correction is not justified: the bias follows the vertical disparity gradient, the dot
  density and the block size (-0.075 px on the bracket at 1280, -0.012 px at 640).
- Next to holes (dark bracket): 3-way reads -0.10 px within 2 px and -0.09 px at 2 to 6 px;
  flip-average -0.054, -0.016, and +0.008 beyond. Most of F13's "biased next to holes" is this.
- Laser lines off the mesh with a right calibration: +0.06 to +0.14 mm on 10 of 11 scenes before;
  -0.035 to +0.019 mm with flip-average on every scene but the pitched calibration.

**The slides: mechanism.** A runaway of the constant-velocity prediction on geometry that barely
holds the pose, in the first frames. On the dark bracket the position error per frame was 0, 4, 12,
65, 85, 104 ... 155 mm, then it stayed near 167 mm; on dark2 about 20 mm a frame for 19 frames,
then 348 mm. The rotation stays right. The dark part is 4.6 % of the image, so the scene is almost
only the bench, a plane. At frame 1 the tracker moves 18.7 mm where the true step is 3 mm, and the
prediction carries that on.

Which half carries it (90 frames, last frame's position error):

| Combination | Dark | Dark2 | Plate 0.5 mm |
|---|---|---|---|
| GPU depth + Open3D model | 15 mm | 1.1 mm | 20 mm |
| CPU depth + GPU model | 262 mm | 53 mm | 2.7 mm |

The dark scenes slide mainly because of the CPU depth, the plate mainly because of Open3D's model.
ICP started at the true pose against a one-frame model drifts 19 mm (CPU depth, Open3D model),
4.8 mm (GPU depth, Open3D model), 2.6 to 2.7 mm (GPU model): the geometry is barely held on any
path.

In the CPU depth two things push: the bias above (with flip-average the plate goes from 36 to
0.90 mm, the dark bracket from 167 to 138 mm), and F13's 6 px rule round holes, which leaves a dark
part almost without raw depth. But the radius is a chaotic knob. Last frame with flip-average on:

| Radius | Dark | Dark2 | Speckled | Plate |
|---|---|---|---|---|
| 0 px | 18 mm (rotation 0.43°) | | | |
| 2 px | 28 mm | 2.1 mm | **222 mm** (runs away from frame 76) | 10 mm |
| 3 px | **293 mm** | 14 mm | 1.6 mm | 0.90 mm |
| 4 px | | | 1.75 mm | 2.25 mm |
| 6 px (today's) | 138 mm | **426 mm** | | 0.90 mm |

No radius is good on every scene. Also tried, not a fix: the GPU model's border test on Open3D's
ray cast (bracket 0.95 to 2.34 mm, dark 167 to 167 mm, dark2 249 to 354 mm, plate 36 to 11.9 mm).

**Every scene, today against flip-average with a 2 px rule** (CPU path, 90 frames; position error
median / last, rotation at the last frame, tracker against gyro per frame, laser lines off the mesh):

| Scene | Median (mm) | Last (mm) | Rotation (°) | Gyro (°/frame) | Lines h / v (mm) |
|---|---|---|---|---|---|
| Bracket | 0.88 → 0.73 | 0.95 → 0.43 | 0.577 → 0.263 | 0.0086 → 0.0060 | 0.117 / 0.117 → -0.006 / 0.016 |
| Bracket, calibration pitched 0.03° | 1.18 → 0.58 | 1.18 → 0.37 | 0.564 → 0.353 | 0.0091 → 0.0070 | -0.034 / 0.135 → -0.093 / 0.037 |
| Dark | 166.6 → 27.5 | 167.2 → 28.0 | 0.129 → 0.150 | 0.0065 → 0.0051 | 0.114 / 0.102 → 0.000 / 0.006 |
| Dark2 | 348.1 → 1.00 | 248.6 → 2.09 | 0.139 → 0.137 | 0.0099 → 0.0065 | 0.101 / 0.107 → -0.002 / 0.017 |
| Speckled | 31.1 → 4.26 | 23.1 → **221.8** | 0.127 → 0.163 | 0.0093 → 0.0089 | 0.116 / 0.087 → 0.006 / 0.019 |
| Speck2 | 3.73 → 1.60 | 2.31 → 1.28 | 0.204 → 0.181 | 0.0116 → 0.0100 | 0.118 / 0.087 → 0.001 / 0.008 |
| Plate 0.5 mm | 15.4 → 6.88 | 36.2 → 10.2 | 0.115 → 0.087 | 0.0042 → 0.0035 | 0.062 / 0.079 → -0.003 / 0.005 |
| Plate2 0.5 mm | 18.6 → 10.0 | 49.6 → 26.7 | 0.134 → 0.073 | 0.0040 → 0.0032 | 0.085 / 0.076 → 0.005 / -0.002 |
| Sphere | 0.23 → 0.13 | 0.31 → 0.10 | 0.045 → 0.005 | 0.0030 → 0.0029 | 0.084 / 0.097 → -0.006 / 0.011 |
| Car panel | 0.54 → 0.19 | 0.60 → 0.46 | 0.064 → 0.036 | 0.0081 → 0.0078 | 0.089 / 0.102 → -0.009 / 0.006 |
| 8 frames, half resolution | 0.10 → 0.09 | 0.04 → **0.07** | 0.007 → **0.012** | 0.0083 → **0.0129** | -0.007 / 0.029 → -0.035 / 0.007 |

All frames tracked except dark2 before (89 of 90) and the speckled bracket after (89 of 90). With
this change the GPU path stayed bit for bit (camera path and laser points the same bytes, the
mesh's vertices the same set, on the bracket, the plate and the 8-frame recording).

**Not adopted** (the owner's rule: better on every scene). Flip-average with a 2 px rule is better
on 9 of 11 scenes but worse on the speckled bracket (last frame 23 to 222 mm) and slightly on the
8-frame recording. Flip-average alone is worse on dark2 (249 to 426 mm) and on the bracket's median
(0.88 to 1.10 mm). A 3 px rule: dark 293 mm. No rule: rotation 0.42 to 0.75°.

**The calibration's signs on this path, with flip-average:** the lasers could be judged (right
calibrations 0.035 mm or less on all scenes, the 0.2 mm laser fault reads -0.351 mm; without it
right calibrations read 0.06 to 0.14 mm, over the 0.05 mm limit). The IMU sign is thin at half
resolution (0.0129° a frame with a right calibration against the 0.015° limit; the fault 0.0339°).
Neither is switched on.

**Second round (2026-11-20): the depth fix and a velocity guard as options that are off.**
`DepthParams.flip_average` is the fix above; with it detail mode leaves out 2 px round holes
instead of 6. `FusionParams.velocity_max_accel` is a guard on the Open3D model: the velocity a
frame carries into the next prediction may change by at most 2 m/s² times the frame time, and the
first one after a start or a lost frame is at most 300 mm/s (the simulated hand reaches 0.48 m/s²
and 119 mm/s over these recordings). The pose ICP found is kept.

CPU path, position error median / last frame (mm) and rotation at the last frame. "C": guard,
depth fix, 2 px rule. "Cw8": C with the ray cast's weight threshold 8 instead of 3 at full
resolution (the 8-frame recording, at half resolution, runs without it and without the depth fix).

| Scene | Today | Guard alone | Guard + fix, 6 px | Guard + fix, 3 px | C | Cw8 |
|---|---|---|---|---|---|---|
| Bracket | 0.88 / 0.95, 0.577° | | | | 0.73 / 0.43, 0.263° | 0.47 / 0.20, 0.161° |
| Bracket, pitched 0.03° | 1.18 / 1.18, 0.564° | | | | 0.59 / 0.27, 0.350° | 0.35 / 0.19, 0.224° |
| Dark | 166.6 / 167.2, 0.129° | 194.6 / **195.1** | 122 / 123, **0.202°** | 162 / 162, 0.022° | 31.7 / 32.0, **0.176°** | 41.7 / 42.3, 0.028° |
| Dark2 | 348 / 249, 0.139° | 406 / **622** | 61 / **498** | 1.6 / 15.3, 0.130° | 1.0 / 2.4, 0.137° | 2.3 / 0.9, 0.067° |
| Speckled | 31.1 / 23.1, 0.127° | 29.8 / 22.5 | 3.4 / 20.0, 0.062° | 4.9 / 1.9, **0.148°** | 4.0 / 1.5, **0.181°** | 5.3 / 5.4, 0.116° |
| Speck2 | 3.73 / 2.31, 0.204° | 3.6 / **2.8** | 9.9 / **9.7** | 1.2 / 0.95, 0.135° | 1.1 / 0.88, 0.162° | 1.4 / 1.2, 0.117° |
| Plate 0.5 mm | 15.4 / 36.2, 0.115° | 14.8 / **40.4** | 1.8 / 1.8 | 2.6 / 2.9 | 6.4 / 8.1, 0.088° | 5.2 / 5.7, 0.043° |
| Plate2 0.5 mm | 18.6 / 49.6, 0.134° | 16.7 / 46.8 | 1.0 / 1.2 | 3.2 / 4.3 | 11.2 / 11.8, 0.075° | 5.1 / 5.7, 0.030° |
| Sphere | 0.23 / 0.31, 0.045° | | | | 0.13 / 0.10, 0.005° | 0.13 / 0.11, 0.004° |
| Car panel | 0.54 / 0.60, 0.064° | | | | 0.19 / 0.46, 0.036° | 0.17 / 0.42, 0.018° |
| 8 frames, half resolution | 0.10 / 0.04, 0.007° | | | | the same as today | the same as today |

Bold: worse than today. Every scene tracks every frame in C and Cw8 (today dark2 loses one).

- **Tracker against the gyro** (median per frame): today 0.0030 to 0.0116°; C 0.0029 to 0.0091°,
  better or the same on every scene; Cw8 better everywhere but the car panel, **0.0087° against
  0.0081°**.
- **Laser lines off the mesh** with a right calibration: today 0.06 to 0.14 mm; C and Cw8 -0.009
  to +0.016 mm at full resolution. The 0.2 mm laser fault reads -0.357 mm. The IMU sign reads
  0.0027 to 0.0093° a frame with a right calibration and 0.0335° with the 3° fault.
- **The weight threshold matters with the guard in place:** 8 instead of 3 takes the bracket from
  0.43 to 0.20 mm and the plates from 8.1 and 11.8 to 5.7 mm, and no frame is lost to it any more;
  the dark and speckled brackets' positions are worse with it (32 to 42 mm, 1.5 to 5.4 mm) and
  their rotations better (0.176° to 0.028°, 0.181° to 0.116°). Without the guard, the depth fix
  with threshold 8 loses 4 frames on the dark bracket and ends 104 mm off.
- **No combination is better than today on every number of every scene,** so none is on by
  default. The nearest is Cw8, whose one miss is the car panel's gyro number above. The owner
  decides.
- With both options on, the GPU path gave the same camera path and laser points byte for byte and
  the same mesh vertices on the bracket.

**Switched on (2026-11-20, the owner's decision D7).** The CPU path's defaults, with nothing set
by hand, 90 frames at 1280x800 (the last row: 8 frames at 640x400):

| Scene | Position error, median / last (mm) | Rotation, last frame | Tracker against gyro (° a frame) | Laser lines h / v (mm) | Before: last, rotation |
|---|---|---|---|---|---|
| Bracket | 0.47 / 0.20 | 0.161° | 0.0055 | -0.006 / +0.013 | 0.95 mm, 0.577° |
| Bracket, calibration pitched 0.03° | 0.35 / 0.19 | 0.224° | 0.0062 | -0.072 / +0.034 | 1.18 mm, 0.564° |
| Dark | 41.74 / 42.32 | 0.028° | 0.0048 | -0.000 / +0.004 | 167.2 mm, 0.129° |
| Dark2 | 2.30 / 0.90 | 0.067° | 0.0063 | -0.003 / +0.011 | 248.6 mm, 0.139° |
| Speckled | 5.28 / 5.35 | 0.116° | 0.0083 | -0.005 / +0.001 | 23.1 mm, 0.127° |
| Speck2 | 1.36 / 1.17 | 0.117° | 0.0093 | -0.002 / +0.006 | 2.31 mm, 0.204° |
| Plate 0.5 mm | 5.15 / 5.69 | 0.043° | 0.0031 | -0.004 / +0.005 | 36.2 mm, 0.115° |
| Plate2 0.5 mm | 5.05 / 5.66 | 0.030° | 0.0034 | +0.001 / -0.001 | 49.6 mm, 0.134° |
| Sphere | 0.13 / 0.11 | 0.004° | 0.0027 | -0.006 / +0.011 | 0.31 mm, 0.045° |
| Car panel | 0.17 / 0.42 | 0.018° | 0.0087 (before 0.0081: accepted) | -0.008 / +0.005 | 0.60 mm, 0.064° |
| 8 frames, half resolution | 0.10 / 0.04 | 0.007° | 0.0083 | -0.007 / +0.029 | the same bit for bit |

- Every scene tracks every frame. The camera paths are the same bytes as the "Cw8" runs above.
- The depth fix and threshold 8 act from 960 px of width, the guard at every size.
- The GPU path with the same code: the same camera path and laser points byte for byte and the same
  mesh on the bracket (427,312 vertices; repeated in review), the 0.5 mm plate and the 8-frame
  recording.
- Frame time on the bracket, CPU path, a quiet PC: 445 to 532 ms before, 603 to 730 ms after; of
  it the matcher 189 to 212 ms before, 337 to 410 ms after. Ray cast and ICP are unchanged.
- The calibration status, if it judged this path (it does not yet): every right calibration at
  1280x800 would read Valid (lines 0.013 mm at most against 0.05, gyro 0.0093° at most against
  0.015°), the 0.2 mm laser fault "Recalibrate, lasers" (0.357 mm), the 3° IMU fault "Recalibrate,
  IMU" (0.0335°).


## Keeping only the frames Standard uses (R3, 2026-11-20)

Setup: simulated 1280x800 recordings at 60 frames a second, 120 frames (2 s): bracket (seed 0) and
car panel (seed 1); every simulated and example recording before was 30 frames a second. Processed
with the app's Standard and Best parameters. The thinned copy holds the frames at positions 0, 2,
4, ... with their own indices and time stamps; motion sensor, events, calibration and ground truth
unchanged.

| Scene, path | Standard mesh, full against thinned | Camera path and laser points |
|---|---|---|
| Bracket, GPU | the same in every value (389,973 vertices, 768,590 faces) | the same bytes |
| Car panel, GPU | the same (407,381 / 805,906) | the same bytes |
| Bracket, CPU | the same (387,125 / 760,397) | the same bytes |
| Car panel, CPU | the same (413,079 / 817,611; 2 thinned runs, 2 full runs) | the same bytes |

"The same" means every vertex's position, normal, sigma and count of looks bit for bit, and every
face; `mesh.ply` lists the vertices in a different order from run to run on both paths, with the
full recording too. Recording size: 147.1 to 73.6 MB (bracket), 144.9 to 72.5 MB (car panel).

Best quality from every frame, against Best from the thinned recording (which then gives the
Standard mesh). Error along each vertex's normal to the true surface, whole mesh, GPU path:

| | Area within 0.5 mm | Median error | More than 0.3 mm off | Laser points | Camera path RMS |
|---|---|---|---|---|---|
| Bracket, every frame | 263,735 mm² | 0.100 mm | 9.5 % | 65,686 | 1.03 mm |
| Bracket, thinned | 254,333 mm² (-3.6 %) | 0.089 mm | 8.6 % | 33,875 | 0.74 mm |
| Car panel, every frame | 302,255 mm² | 0.067 mm | 7.5 % | 101,770 | 0.22 mm |
| Car panel, thinned | 287,564 mm² (-4.9 %) | 0.086 mm | 11.7 % | 51,237 | 0.20 mm |

In the app (a 120-frame scan at 60 frames a second, default recipe): planned and freed
155,318,253 bytes; after Make mesh at Standard the camera path is the same bytes and all 389,649
vertices have the same position, sigma and looks; 12 of 769,362 faces differ, all made by hole
filling (MAINTENANCE_PLAN F15: not the thinning).

Limits: two simulated scenes, 2 s each; real 60 frames a second recordings may differ.

## Repeatable edits (MAINTENANCE_PLAN F15, 2026-11-20)

`[process] repeatable_edits` (off by default): the app's mesh edits run on the mesh numbered by
position, so the same mesh gives the same result whatever order it lists its points in. Built and
measured by an Opus agent; the "off" row for the example mesh is from the review.

**What depends on the order today** (each edit on a mesh and on the same mesh with its points and
faces shuffled, three shuffles; a grid, a box and a sphere with holes, and a 12,392-point mesh from
a simulated scan):

| Edit | After a shuffle |
|---|---|
| Fill holes | other triangles (at a 40 mm limit and when closing everything; holes up to 5 mm come out the same) |
| Simplify | other triangles on every mesh |
| Smooth (both kinds, "flat areas only", "away from edges") | the same choice, last bits only: 1.3e-12 mm at most |
| Remove loose pieces | the same, except two pieces of the same size tying for largest |
| Remove the table | the same on the meshes tried |
| Carrying sigmas, looks and filled flags after simplifying | follows the numbering (a new point half way along an edge is as near to both ends) |

**Off: nothing changes.** Every edit on the example mesh (409,127 points, 808,631 triangles) with
the code before and after: loose pieces, fill at 15 mm (809,514 triangles) and 40 mm (809,569),
Taubin and bilateral smoothing, simplify to 200,000 (200,064), the carried sigmas and flags, the
table's faces (789,483): every array the same. On the test meshes 138 arrays the same; the Default,
Body panel and Bracket recipes and one with every edit, through the server on one scan, give the
same result files byte for byte on both paths.

**On: what changes.**

| Mesh, fill at 30 mm | Patch area, on against off | New faces, off / on |
|---|---|---|
| Grid with holes | the same | 164 / 172 |
| Box | the same | 37 / 37 (identical) |
| Sphere | 0.0001 mm² | 4 / 4 |
| Sheet with a seam of doubled points | +0.003 % | 70 / 70 |
| 0.3 mm plate, a hole in each face | the same | 246 / 238 |
| Real mesh | -0.16 % (166.63 to 166.36 mm²) | 51 / 49 (22 differ) |

- The surface that was there and the holes left open are the same on every mesh. New patch points
  lie up to 0.88 mm (grid) and 0.05 mm (real mesh) from the other version's: other diagonals over
  the same hole.
- Normals on the real mesh: 11 of 12,392 old points turned by more than 1°, two of them by 90° and
  170°, at a fold where the faces round the point nearly cancel in either version.
- The 0.3 mm plate: each hole is closed in its own face, flat to 1e-6 mm, and no triangle reaches
  across, with the option on and off.
- Simplify in plain position order moved the thin plate's surface 0.59 mm instead of 0.30 mm
  (equal-cost collapses bunched at one end); with runs of 64 points shuffled by a fixed shuffle it
  is 0.30 mm again.
- Times on the example mesh, off / on (medians of 3, a busy PC): fill 5 mm 1.3 / 1.2 s, fill 15 mm
  1.5 to 1.7 / 1.3 to 1.5 s, loose pieces 0.8 to 1.1 / 1.1 s, smooth 7.4 to 8.5 / 5.2 to 5.3 s,
  simplify 9.9 / 8.8 s. The renumbering itself takes 0.28 s.

## Projects larger than memory (FEATURE_PLAN R5, 2026-11-20)

**Method.** A copy of the project "My first scan", served by its own server on its own data folder.
Each job and page ran one at a time; Make mesh, Straighten, Fuse, Align and Merge each on a fresh
server. The server process was sampled twice a second with `tools/memory_peaks.py`, which reads
Windows' own counters: working set and commit, now and at the process's peak (so a peak between two
samples is not missed), the process's graphics memory, and the system's available memory. The PC:
Ryzen 7 7735U, Radeon 680M (its shared graphics memory is ordinary RAM), 19.8 GB, Windows 11.
Another agent's test suite (about 2.2 GB), its server (about 1.4 GB), Claude (about 5 GB) and
Defender ran at the same time; 9 to 12 GB were available before each job. Allowance for the
server: 19.8 GB less about 4 GB for Windows and about 1.5 GB for the browser tab, so about 14 GB.
Measured by an Opus agent.

**The project.** Eight scans at Best, 355 to 761 frames at 30 frames a second: seven at 1280x800
(about 0.81 million triangles each), one at 640x400 (0.36 million). One was then fused at 0.5 mm:
3,341,378 triangles. A scan recorded through the Scan page for 120 s as a looped replay: 2,459
frames (the recorder kept about 20 frames a second on the busy PC), 1.52 million triangles, about
3.0 GB of recording. Two merges: 7 scans (3,983 frames) and 8 scans with the long one (6,440
frames). A real large project by the sizes of "What a scan takes on disk": 8 x 3,600 frames x
1.2 MB = 35 GB of recordings.

**Peaks** (the server process; share of 19.8 GB; before the crossing fix unless marked):

| Job or page (server side) | Peak working set | Peak commit | Share | Time |
|---|---|---|---|---|
| Make mesh Best, 0.81M triangles, 355 to 761 frames (7 scans) | 7.63 to 8.44 GB | 10.8 to 11.4 GB | 39 to 43 % | 129 to 415 s |
| Make mesh Best, one of them, after the fix (GPU / CPU path) | 1.42 / 1.39 GB | 4.92 / 2.94 GB | 7 % | 111 / 228 s |
| Make mesh Best, 640x400, 525 frames | 1.05 GB | 3.15 GB | 5 % | 105 s |
| Make mesh Best, 2,459 frames | 2.95 GB | 6.01 GB | 15 % | 765 s |
| Straighten alone | 7.61 GB | 9.20 GB | 38 % | 174 s |
| Fuse at 1.0 / 0.7 / 0.5 mm (0.81M / 1.66M / 3.34M triangles) | 1.00 / 1.81 / 7.04 GB (plus 2.2 GB graphics) | 3.64 / 5.29 / 9.38 GB | 5 / 9 / 36 % (47 % with graphics) | 104 / 115 / 149 s |
| Edits at 0.81M: fill holes / smooth / simplify / remove table / floaters | 0.54 / 0.65 / 0.94 / 0.34 / 0.23 GB | 2.07 GB at most | under 5 % | 0.6 to 50 s |
| The same edits at 3.35M | 1.53 / 2.00 / 3.14 / 0.70 / 0.24 GB | 4.28 GB at most | 16 % at most | 0.5 to 102 s |
| Undo and redo from the cache, 0.81M / 3.35M | 0.44 / 1.09 GB at most | 2.25 GB at most | under 6 % | 0.1 to 3 s |
| Fit, keep a feature, the features, 0.81M / 3.34M | 0.36 / 0.75 GB | 1.9 GB | under 4 % | 4 to 9 s |
| Report (HTML), 0.81M / 3.34M | 0.43 / 1.06 GB | 2.2 GB | 5 % | 0.7 to 2.2 s |
| Check, then its reasons, 9 scans one after another on one server | 2.48 GB at the end | 3.68 GB | 13 % | 0.4 to 11 s each |
| Every scan's viewer routes, 9 scans, twice | 1.41 GB | 2.51 GB | 7 % | 1 to 17 s each |
| Align 8 scans (28 pairs) | 0.82 / 0.86 GB | 1.99 GB | 4 % | 307 / 145 s |
| Merge 7 scans, 3,983 frames | 1.07 GB | 3.71 GB | 5 % | 513 s |
| Merge 8 scans, 6,440 frames | 2.54 GB | 5.49 GB | 13 % | 797 s |
| Scanning live, 2 min | 0.53 GB | 2.23 GB | 3 % | 121 s |
| Disk report; free-space plans; moves to "recording and result" and "result only" | 0.68 GB at most | 1.76 GB | 3 % | 0.1 to 1.7 s |

- An idle server takes 0.22 GB (1.32 GB commit). Once the GPU has been used, commit stays near 3 GB
  (the OpenCL driver's reserve).
- During four Make mesh runs before the fix, available memory touched 0 GB; once that fell on the
  8 GB peak and pages were read back in. So this PC did swap that night, with the other load.
- After every job memory comes back (0.2 to 0.8 GB working set). On one server each opened scan
  keeps its Check arrays and ray-casting scene until the server restarts: 0.26 GB to 1.67 GB over
  9 scans, about 0.1 GB per million triangles a scan. The viewer's routes keep about 0.04 GB for a
  0.8M scan; opening them all a second time added nothing.

**Trends.**
- Make mesh at Best: 1 to 1.4 GB besides Straighten's crossings, almost whatever the frames.
- Fuse: 1.0 / 1.8 / 7.0 GB at 0.8 / 1.66 / 3.34 million triangles; faster than the triangles below
  0.7 mm.
- Edits: about 0.24 GB plus 0.9 GB per million triangles (simplify, the most).
- Check and reasons: about 0.25 GB plus 0.7 GB per million triangles.
- Merging N scans of F frames: about 0.3 GB while fusing (frames add about 20 MB per 1,000), plus
  about 1.5 GB per million triangles of the merged model at the end; about 0.12 s a frame. Eight
  3,600-frame scans: about an hour and a few GB.
- So memory does not limit a scan's length or the number of scans. What did was the crossings (a
  scanner held still or moved slowly), now mended, and very fine fusing (0.5 mm and below).

**The laser crossings** (`ohs/track/curve_ba.py`, `find_crossings`). The candidates are the pairs
of laser points within 3 mm among the curves of the 200 frames it samples. Before: about 157 bytes
a candidate (146 / 155 / 156 / 158 at 4.3 / 13.2 / 22.2 / 45.8 million). Now the candidates are
filtered 2^19 at a time, in order: 16 bytes a candidate (the pairs themselves) plus one piece.

| Scan (200 sampled frames, 1280x800) | Candidates | Before | After |
|---|---|---|---|
| The example scans (a looped short replay) | 46 million | 7.57 GB for Make mesh | 1.42 GB for Make mesh |
| The 2,459-frame scan | 13.6 million | 2.95 GB for Make mesh | not measured again |
| A slow scan, 2° a second (simulated) | 61 million | about 10.6 GB by the law, not run | 2.28 GB for the crossings (36 s) |
| Nearly still (simulated) | 84 million | about 14.2 GB by the law (17 GB commit), not run | 2.28 GB for the crossings (46 s) |

The same after the fix: Make mesh at Best on one example scan, before and after, on the GPU path
and the CPU path, gives the same camera path and laser points (bytes), the same Straighten report
but for its timings (967 crossings on the GPU path, 975 on the CPU path), and the same mesh (every
vertex's values with its sigma and looks, every face named by its vertices' values). Guard:
`tests/test_track_curve_ba_memory.py`, which keeps a copy of the old function and compares every
array bit for bit, also on a made-up scan held still (4.4 million candidates: 608 MB before, 87 MB
now, against a bound of 201 MB).

**The browser side** (the bytes the pages ask for, and `plyParse.ts` under Node; the tab itself was
not measured). The viewer asks for 28.8 MB for a 0.81M scan and 115.6 MB at 3.34M. The Align page
with eight scans asks for 254.6 MB of meshes: it downloads up to eight full meshes at once. A
parsed mesh is 26 to 27 bytes a triangle (21.9 MB at 0.81M, 87.4 MB at 3.34M), twice that while
parsing. Eight meshes kept: 229 MB of arrays; the tab adds its graphics copies and search trees,
roughly two to three times that.

## Presets: which settings matter, measured (FEATURE_PLAN P1, 2026-11-20)

`tools/preset_study.py` varies one setting at a time around today's Make mesh (the app's
`process_params` with the default config, then the Default recipe) on simulated scans, 1280x800,
GPU path, and judges each against the true shape: the object's mesh error (median / p95, mm, after
placing the whole scan on the truth), coverage (the share of the object's surface seen by the
processed frames that the mesh reaches within 1 mm), the scene's fits, and the time. A setting
counts only when it moves a result by more than the range over three recordings of the scene
(other seeds: other noise and another hand path; for the door and the carburettor, other stretches
of their one long recording). Measured by an Opus agent; times were taken on a very busy PC, so
only rough ratios are given. The full tables come from `python tools/preset_study.py table <work>`.

**The spread from scan to scan (today's settings):**

| Scene | Error, median (mm) | Error, p95 (mm) | Coverage | Fit | Range over the three |
|---|---|---|---|---|---|
| Sphere (36 frames) | 0.077 / 0.063 / 0.080 | 0.28 / 0.27 / 0.38 | 0.864 / 0.901 / 0.867 | radius -0.09 / -0.11 / -0.18 | 0.017 / 0.11 / 0.037 / radius 0.09 |
| Car panel (60) | 0.035 / 0.037 / 0.026 | 0.16 / 0.20 / 0.16 | 0.985 / 0.992 / 0.997 | Ø 25 hole +1.07 / +0.54 / -0.23 | 0.011 / 0.044 / 0.013 / hole 1.30 |
| Bracket (90) | 0.170 / 0.488 / 0.694 | 0.74 / 1.09 / 1.00 | 0.924 / 0.823 / 0.818 | base height +0.006 / +0.036 / +0.018 | 0.52 / 0.36 / 0.11 / base 0.031 |
| Plate 0.5 mm, lying flat (60) | 0.032 / 0.031 / 0.034 | 0.16 / 0.14 / 0.14 | 1.000 each | thickness 0.493 / 0.492 / 0.504 | 0.002 / 0.023 / 0 / thickness 0.011 |
| Walls 0.5 / 1 / 2 mm, standing (120) | 0.123 / 0.144 / 0.114 | 0.88 / 1.11 / 1.28 | 0.950 / 0.909 / 0.922 | walls read 1.71·1.81·2.66 / 1.70·1.71·2.58 / 1.70·1.90·2.53 | 0.030 / 0.40 / 0.042 / walls 0.02·0.19·0.13 |
| Door, 141-frame stretches | 0.052 / 0.039 / 0.042 | 0.38 / 0.43 / 0.35 | 0.989 / 0.925 / 0.966 | | 0.013 / 0.077 / 0.064 |
| Carburettor, 135-frame stretches | 0.230 / 0.157 / 0.175 | 1.00 / 0.69 / 1.04 | 0.701 / 0.696 / 0.679 | | 0.073 / 0.35 / 0.022 |

The bracket's spread is wide because the scan is placed on the truth over the whole scene, where
the table rules, so where tracking left the part on the table rules its error.

**Voxel size** (seed 0; median / p95 / coverage / fit; `*` = beyond the spread; time against today's):

| Scene | 0.5 mm | 0.75 mm | 1.0 mm (today) | 1.5 mm | 2.0 mm |
|---|---|---|---|---|---|
| Sphere | 0.075 / 0.29 / 0.871 / r -0.09 (2.4x) | 0.076 / 0.28 / 0.868 / -0.07 | 0.077 / 0.28 / 0.864 / -0.09 | 0.077 / 0.28 / 0.856 / -0.11 | 0.083 / 0.33 / 0.850 / -0.25* (0.9x) |
| Car panel | 0.039 / 0.16 / 0.987 / hole +0.3 (2.2x) | 0.035 / 0.15 / 0.986 / +0.6 | 0.035 / 0.16 / 0.985 / +1.1 | 0.035 / 0.22* / 0.982 / +1.9 | 0.034 / 0.75* / 0.979 / +2.8* (0.9x) |
| Bracket | 0.157 / 0.58 / 0.936 (1.6x) | 0.165 / 0.65 / 0.933 | 0.170 / 0.74 / 0.924 | 0.184 / 1.12* / 0.909 | 0.207 / 1.76* / 0.883 (0.5x) |
| Plate | 0.035 / 0.16 / 1.000 / t 0.49 (1.7x) | t 0.49 | 0.032 / 0.16 / 1.000 / t 0.49 | t 0.49 | 0.031 / 0.16 / 1.000 / t 0.49 |
| Walls 0.5·1·2 | 1.06*·1.44*·2.43* (1.4x) | 1.41*·1.61*·2.50* | 1.71·1.81·2.66 | 2.11*·2.31*·3.30* | 2.19*·2.51*·3.88* (0.4x) |
| Door | 0.060 / 0.41 / 0.989 (1.3x) | 0.053 / 0.38 / 0.989 | 0.052 / 0.38 / 0.989 | 0.051 / 0.41 / 0.987 (0.6x) | 0.051 / 0.45 / 0.985 (0.6x) |
| Carburettor | 0.187 / 0.67 / 0.724* (1.8x) | 0.207 / 0.75 / 0.717 | 0.230 / 1.00 / 0.701 | 0.285 / 1.89* / 0.664* | 0.338* / 3.14* / 0.622* (0.7x) |

- **Only the voxel passes the stop rule, and only on thin standing walls.** Paired on all three
  scans, 0.5 mm voxels read the 0.5 / 1 / 2 mm walls 0.62 to 0.67 / 0.37 to 0.45 / 0.23 to 0.35 mm
  closer to the truth, and the 0.5 mm wall's coverage rises by 0.13 to 0.36. It also helps the
  carburettor (p95 -0.11 to -0.38 mm, coverage +0.01 to +0.02) and the bracket, inside their
  spreads; it makes the panel's Ø 25 hole read 0.6 to 0.8 mm smaller, inside its 1.3 mm spread; it
  does nothing for the door.
- **Coarser voxels (1.5 to 2 mm)** take 0.6 times the time on the door with no measured loss, but
  open the panel's hole by 1.4 to 2.4 mm and thicken thin walls by 0.4 to 1.2 mm.
- **The gap guard trades holes against thin walls** (8 px today): 4 px reads the 0.5 mm wall 0.4
  to 0.5 mm closer and lifts the carburettor's coverage by 0.09 to 0.14, but closes the Ø 25 hole
  by 2.8 to 3.7 mm; 12 px opens it by 2.9 to 3.5 mm and drops the carburettor's coverage to 0.43;
  0 px deletes one face of the 0.5 mm wall on all three scans.
- **Quality:** Preview is worse on every scene (the hole -3 mm, p95 several times as large, one
  face of the 0.5 mm wall gone on two of three scans). Best at 60 frames a second gave no gain on
  the one bracket recording tried (0.303 against 0.223 mm median, 2.5 times the time).
- **Thin walls off** thickens the walls by 0.3 to 0.75 mm; "on" equals "auto" in a scan without
  markers.
- **Depth range:** 200 to 350 mm loses tracking on four scenes (the table lies beyond 350 mm);
  150 to 600 mm changes nothing; 100 to 600 mm does not run on the Radeon 680M's GPU matcher (368
  disparities, 256 work items a group) and falls back to the CPU.
- **Edits after meshing:** a fill size of 0 to 80 mm changes nothing measurable (the Hole tool's
  diameter included). Smoothing off edges gains 0.005 to 0.013 mm in the median and makes 0.2 to
  5 % of the edge vertices worse; smoothing everywhere makes 12 to 35 % of them worse. All inside
  the spread except on the plate.

**Thin parts.** The 0.5 mm plate lying flat reads 0.489 to 0.504 mm with every voxel from 0.5 to
2 mm, every gap guard from 0 to 12 px, thin walls off and every recipe, with 98 to 100 % of its top
kept; Preview reads 0.47 mm (its tracking failed on two scans); thinner plates were not measured.
Standing walls, seen from both sides:

| Setting | 0.5 mm wall reads | 1 mm wall | 2 mm wall |
|---|---|---|---|
| Today (1 mm voxels) | 1.70–1.71 | 1.71–1.90 | 2.53–2.66 |
| 0.5 mm voxels | 1.03–1.08 | 1.32–1.45 | 2.18–2.43 |
| 1.5 to 2 mm voxels | 2.09–2.19 | 2.31–2.52 | 3.3–3.9 |
| Thin walls off | 2.20–2.34 | 2.17–2.65 | 2.96–3.29 |

So at today's 1 mm voxels nothing thinner than about 1.7 mm reads true, and at 0.5 mm the floor is
about 1.0 to 1.1 mm.

**The presets this supports:** "General" (today) and "Thin sheet metal and small parts" (voxel
0.5 mm). A detailed casting wants the same value. A coarser voxel for large panels gains time, not
accuracy, and harms holes.

Limits: simulation only; one setting at a time; the same recording judged twice gave the same
camera path and faces and a median error 0.004 mm apart (0.1702 and 0.1742 mm: the study's own
placement on the truth; fits within 0.0004 mm).

## The scorecard's baseline (ENGINE_PLAN X0, 2026-11-20)

`tools/scorecard.py` (what it does and how it judges: `docs/ENGINE_PLAN.md`, X0). Standard
quality, default settings, markerless, the Default recipe; the part placed alone by its own best
fit. Seeds 0 to 4 for the bracket and the 200-frame car panel, 0 to 2 for the others; "a..b" is
the range over seeds, and b - a is the scene's spread. Errors are distances to the simulator's
true surface in mm; fits are the reading minus the truth. Timings were taken on a busy PC and are
left out. The saved files: `tools/scorecard_baselines/2026-11-20-gpu-core.json` and
`2026-11-20-cpu-quick.json`.

**GPU path (Radeon 680M), set `core`, 36 recordings:**

| scene (recordings) | poses | part median | p95 | share > 0.3 mm | coverage | path max | fits |
|---|---|---|---|---|---|---|---|
| sphere, end to end (1) | tracked / true | 0.059 / 0.055 | 0.248 / 0.242 | 0.027 / 0.028 | 0.870 / 0.865 | 0.15 | radius -0.089 / -0.084 |
| car panel, end to end (1) | tracked / true | 0.038 / 0.039 | 0.168 / 0.208 | 0.036 / 0.044 | 0.965 / 0.966 | 0.29 | Ø25 +1.34 / +1.23 against 25.20 |
| car panel, 200 frames (5) | tracked | 0.021..0.022 | 1.68..1.89 | 0.075..0.080 | 1.000 | 0.37..0.90 | Ø25 -0.97..-1.37; its centre 0.08..0.61 off |
| | true | 0.022 | 1.87..2.33 | 0.080..0.092 | 1.000 | | Ø25 -1.09..-1.71 |
| bracket, 240 frames (5) | tracked | 0.075..0.228 | 0.454..0.492 | 0.115..0.316 | 0.989..0.993 | 1.15..2.40 | boss radius -0.07..-0.27; shaft radius -0.07..+0.07; upright face -0.14 / +0.01 / +0.33 / -0.07 / +0.10 |
| | true | 0.058..0.063 | 0.413..0.429 | 0.097..0.103 | 0.991..0.993 | | boss radius -0.22..-0.30; shaft radius -0.09..-0.17 |
| sheet part (3) | tracked | 0.105..0.255 | 0.52..0.72 | 0.16..0.39 | 0.98 | 2.4..3.6 | flange +0.25..+0.60; 1 mm rib +0.66..+1.21; 3 mm rib +0.17..+0.72 |
| | true | 0.058..0.073 | 0.50..0.57 | 0.11..0.14 | 0.985..0.988 | | flange +0.12..+0.18; 1 mm rib +0.23..+0.27; 3 mm rib +0.03..+0.04 |
| standing walls (3) | tracked | 0.125..0.151 | 0.72..0.80 | 0.27..0.32 | 0.955..0.973 | 1.2..3.9 | the 0.5 / 1 / 2 mm walls read +1.18..1.26 / +0.70..0.91 / +0.54..0.66 thick |
| | true | 0.056..0.065 | 0.64..0.73 | 0.15..0.17 | 0.96..0.975 | | +0.76..0.84 / +0.40..0.62 / +0.16..0.35 |
| 0.5 mm plate (3) | tracked | 0.033..0.035 | 0.13..0.15 | at most 0.005 | 1.000 | 1.9..4.0 | thickness -0.008..+0.002; top kept 0.99..1.00 |
| | true | 0.027..0.029 | 0.10..0.11 | 0 | 1.000 | | thickness -0.001..-0.006 |
| hole plate, 10 mm up (3) | tracked | 0.073..0.084 | 1.55..1.79 | 0.21..0.24 | 0.997 | 2.0..2.3 | Ø25 +0.47 / -0.20 / +0.38; Ø10 +0.07 / -0.07 / -0.20; bore -0.40..-0.82; Ø6 refused |
| | true | 0.053..0.056 | 1.23..1.95 | 0.17..0.18 | 0.998 | | Ø25 +0.58..+0.75; Ø10 +0.28..+0.60 |
| hole plate, 120 mm up (3) | tracked | 0.035..0.071 | 1.69..1.99 | 0.21..0.24 | 0.991 | 3.9..16.3 | Ø25 -6.9 / -2.9 / -3.2 (seed 0 lost tracking); Ø10 and Ø6 refused |
| | true | 0.034..0.040 | 1.24..1.31 | 0.16..0.17 | 0.994 | | Ø25 -1.40..-1.57; bore -0.03..+0.10 |
| dark bracket (2) | tracked | 0.079..0.081 | 0.58..0.69 | 0.13 | 0.85..0.87 | 2.3..6.3 | last frame 0.70..3.79 |
| speckled bracket (2) | tracked | 0.093..0.118 | 0.61..0.74 | 0.15..0.23 | 0.67..0.96 | 1.4..1.6 | last frame 0.92..1.13 |
| 0.5 mm plate, hard (2) | tracked | 0.028..0.032 | 0.13 | at most 0.004 | 1.000 | 2.6 | last frame 0.47..1.38; thickness -0.005..-0.009 |
| seams: taper / taper with flush / flush ramp (1 each) | tracked | 0.033 / 0.034 / 0.026 | 0.66 / 0.36 / 0.17 | | 1.000 | 1.2 / 3.6 / 1.3 | flush 0.010 / 0.015 / 0.003; gap +1.49 / +1.51 / +0.07 |

Set `full` adds, on the same path: the door, median 0.045..0.055 tracked and 0.040..0.050 at true
poses, p95 0.36..0.44 and 0.34..0.46, coverage 0.92..0.99; the carburettor, median 0.125..0.179
and 0.118..0.162, p95 0.67..1.01 and 0.66..0.96, coverage 0.68..0.71.

**No GPU, set `quick`:**

| recording | poses | median | p95 | coverage | path max | fit |
|---|---|---|---|---|---|---|
| sphere, end to end | tracked | 0.053 | 0.215 | 0.751 | 0.20 | radius -0.200 |
| | true | 0.061 | 0.304 | 0.785 | | radius +0.090 |
| 0.5 mm plate, seed 0 | tracked | 0.037 | 0.186 | 1.000 | **39.1** (last frame 37.0) | thickness **-0.387** (it reads 0.11 mm); top kept **0.345** |
| | true | 0.035 | 0.129 | 1.000 | | thickness -0.014; top kept 0.999 |

Without a GPU, tracking on the 0.5 mm plate slides 39 mm, and the plate is then nearly gone from
the mesh: thinned to 0.11 mm with two thirds of its top removed, without a word. The part's
median error does not show it (what is left lies on the plate). ENGINE_PLAN X0a.

**The Ø25 hole's two signs.** The sign follows the recording. It does not depend on where the
click lands (1.5 or 3 mm out read the same), on the placement (part alone or whole scene), or on
the tool's level (it reads the open edge at every level). The 60-frame pans read +1.07 / +0.54 /
-0.23 against 25.0 tracked, exactly the "Presets" numbers; the 36-frame end-to-end pan +1.54. The
200-frame orbit round the hole reads -0.77 to -1.17 tracked and -0.89 to -1.51 at true poses: the
fused rim drops at a radius of 10.6 to 11.9 mm against the true 12.6 mm, fill reaching inwards
from every side the orbit looked from. The other 0.2 mm between the two studies was the
reference (25.0 and 25.20).

**A paired replay, as an example: the gap guard at 4 px against today's 8 px** (hole plate 120 mm
up and the 0.5 mm plate, seeds 0 to 2, fusing the cached matcher depth again; feeding the cached
depth back reproduced the first run on all six). The plate: nothing moves but the vertical
laser line against the mesh, +0.006 to +0.008 mm on all three (the spread is 0.006). The hole
plate at true poses: p95 +0.32 to +0.54 mm, the share over 0.3 mm +0.018 to +0.027, the Ø25 hole
closes by 3.7 to 4.1 mm on two seeds and the bore reads 0.59 to 0.88 mm larger on all three. The
verdict's last line names both scenes as worse, so 4 px stays off, as "Presets" found (4 px
closes Ø25 by 2.8 to 3.7 mm).

**Repeating.** The same recording processed and scored twice gives the same number in every
column but the timings, on both paths (quick set only).

**Against earlier numbers.** The same within rounding: the bracket's share over 0.3 mm (11.5 to
31.6 %, about 10 % at true poses), its bosses and upright face ("The fits' ± on today's engine"),
the 200-frame and 60-frame panel holes, the walls and the sheet ("Presets", "Thin walls seen from
opposite sides"), the 0.5 mm plate (0.492 to 0.502), the sphere, and the door ("Presets").
Different:

- **Car panel, end to end:** 0.038 / 0.17 mm against 0.083 / 0.39. The panel is now placed
  alone, the fill past its edge is kept out of the placement, and it is not slid along itself.
- **Car panel p95, 200 frames:** 1.7 to 2.3 mm. 7 to 9 % of the panel's vertices are fused fill
  hanging up to 15 mm past its free edge, over a drop of 60 to 120 mm. This is the engine
  (ENGINE_PLAN X0a); the scorecard also reports the share as `part_beyond_edge`.
- **Bracket median:** 0.075 to 0.228 against "about 0.14": the part alone, not the whole scene.
- **Carburettor:** median 0.125 to 0.179 against 0.157 to 0.230 in "Presets" (the part alone,
  probably); p95 and coverage agree. The 0.80 / 2.7 of 2026-11-10 and the door's 0.07 / 0.79 of
  the same day are superseded.
- **Hole plate 120 mm up, Ø25 at true poses:** 23.4 to 23.6 against 24.38 to 24.54 in "Holes
  filled in" (2026-11-12). That 24.38 to 24.54 is the car panel's row there; the plate's own row was 23.64 to 23.84, so the plate moved 0.1 to 0.3 mm. What is not explained is the car panel's: 24.38 to 24.54 in the prototype against 23.5 to 24.1 in the engine from 2026-11-13; the recordings may differ. (Corrected 2026-11-23.) Tracked: 18.1 / 22.1 / 21.8 against 22.0 to 22.1; seed 0 lost
  tracking (X0a).
- **Hole plate 10 mm up, at true poses:** Ø25 +0.58 to +0.75 against +0.15 to +0.5.
- **Dark and speckled scenes:** last frame 0.47 to 3.79 mm against 1.1 to 3.8; the two plates
  now end at 0.47 and 1.38. Why is not known.

Limits: simulation only; one recording each for the seams, the end-to-end scans and the no-GPU
set, so those have no spread; a sheet's drift along itself is not scored; the bracket's fits on
the 90-frame hard scenes mostly do not answer (faces never scanned), and a click more than 1 mm
from the mesh counts as no answer.

### The comparison checks its evidence first (2026-11-24, from the second outside review)

A comparison could end with "Better or the same on every scene." when runs had failed, were missing, or the
result set was empty: a recording missing on one side dropped out, a failed run compared as n/m, and no
verdicts at all gave the success sentence. Guard tests showed it (19 of 20 failing before the change,
`tests/test_tools_scorecard_incomplete.py`). Now every run the baseline has (recording and pose source) must be
there, must have finished on both sides, and must still give every judged number the baseline measured.
Otherwise the table starts with "INCOMPLETE", the last line reads "Not judged: the comparison is incomplete."
and names what is missing ("Not run: ...", "Failed: ...", "Not measured any more: ..."), and `compare` and
`replay` end with exit status 1. A scene where nothing was measured on both sides is "not judged", no longer
"identical". A lost reading is never taken as 0. A recording this PC cannot have (the door's and the
carburettor's model files) is named under "Not compared" and is not counted as missing. A complete comparison
reads exactly as before. The saved baselines compared with one another came out complete (72 runs, nothing
lost), so the check raises no false alarm on them. Not done: no scorecard run on recordings was made for this.

### Every result carries the identity of the run that made it (2026-11-24, from the second outside review)

A cached scorecard result was used again without a check that today's code and recording had made it; the
engine's fingerprint left out `ohs/server`, through which both chains run; a saved baseline kept no
fingerprint. Guard tests showed all three (16 of 17 failing before the change,
`tests/test_tools_scorecard_identity.py`). Now every result and every baseline entry holds the run's identity:
the engine's code (every Python file of `ohs`, the server's included), the scorecard's own job and scoring
code, the recording (its files by name and size, the small ones by content), the calibration, the settings,
the chain, the pose source, the libraries' versions and the device. A cached result is used again only when
its identity is today's; otherwise the job runs again and the old result and mesh are moved into an `old`
folder beside it, never deleted. With `--history` old results are shown as they are, marked as old: a
comparison built on them is not judged and no baseline is saved from them. A comparison names both sides'
identities and says where they differ. Results and baselines from before this date have no identity: the
five saved baselines still load and compare, named "without identity (saved before 2026-11-24)"; the cache
of earlier runs is rerun when a command reaches it, or used as history.
The cost: any edit to a Python file of the engine or the server makes the next scorecard command run its jobs
again. Not done: no real job has run with an identity yet (no recording was processed for this); the graphics
card's name in the identity was not exercised.

## Small defects from the second outside review (2026-11-24)

Each was checked against the code, shown by a guard test that failed first, and mended by itself
(ENGINE_PLAN, "A second outside review").

**The rotation logarithm near a half turn (R2-10).** `so3_log` (`ohs/geometry.py`) read the axis of a rotation
within 1e-4 rad of a half turn from a matrix it took for symmetric and forced the largest component positive:
a rotation about -X by pi - 9e-5 came back about +X, 1.8e-4 off in the matrix (0.0103 degrees), and a mixed
axis 0.0038 degrees off. The plain formula also lost accuracy before that limit (a round trip off by about
1e-15 / (pi - angle)^2: 2e-8 at pi - 2e-4). Within 0.1 rad of a half turn the axis now comes from the symmetric
part, its sign from the antisymmetric part and the angle from atan2: the round trip is under 1e-12 on six
axes at fifteen angles up to and past pi (66 of 98 guard tests failed before). More than 0.1 rad from a half
turn the result is the same bit for bit (4,000 random rotations). Results that pass through a rotation
within 0.1 rad of a half turn change in their last digits.

**Frames with laser points (R2-9).** The report's `lasers.frames_with_points` counted each laser line as a
frame: 16 for an 8-frame scan with two lasers. It now counts the frames that gave laser points (8); the
per-line count is kept as `LaserCloud.n_additions`. The stored points are the same byte for byte. Nothing in
the app or the UI read the number; only someone reading `report.json` sees it change. The two places that
add laser points at the scan's start run on the graphics card's model only and were not exercised by the
processor-path tests.

**The tracker's ICP and its priors (R2-3).** `MarkerlessTracker._icp` multiplied the priors' precision by
`icp_sigma_mm` squared and also divided the data term by it, so the priors were weighed against the data by
the fourth power. With 121 points 1 mm behind a plane and a translation prior of 1 mm the step was -0.999484
mm at sigma 0.5 and -0.883212 mm at sigma 2.0, where the closed form gives -0.997938 and -0.968000; it gives
those now. At the default of 1.0 mm, the only value in use, nothing changes: the division was by 1.0, and 28
tracked frames (processor path, with and without gyro priors) are the same byte for byte. The graphics
card's path was not run for this fix on its own.

**The live recorder's queue (R2-8).** The recorder took every frame the camera gave it into a queue with no
bound: with the disk held, 483 of 500 pushed frames were waiting in it (the guard test). It now holds at most
1 GiB or 20 s of frames waiting for the disk (`RECORD_MAX_BYTES`, `RECORD_MAX_SECONDS` in `ohs/app/live.py`;
1 GiB is 512 full grey stereo pairs, four times the backlog at which it already warns). Past that the
recording stops: every frame taken before is written, the folder stays a valid recording, and `meta.json`
(`recording_stopped`), `events.csv` and the recording's error say why with the numbers ("Recording stopped:
the disk could not keep up (1.1 GB of frames were waiting to be written). The 812 frames recorded up to then
are kept."). Later frames are refused and counted, never dropped in silence or renumbered; a write error's
text is kept beside the stop's. 3,000 frames pushed at a healthy writer never trip it. `ohs scan` and `ohs
record` end with the reason. In the app the reason shows only after Stop until the server passes it on (the
next change). Not tried: a real camera, a real slow disk.

**A recording after a failed frame write (R2-6).** A frame whose file could not be written still got its row
in `frames.csv`: a recording of 8 frames with two failed writes listed 8 on reopening, and reading a listed
frame raised. And `meta.json` was written over in place: cut short, it no longer parsed and the reader raised
on opening. Now a failed frame is not listed; its index, time, file and error go into `frames_failed.csv` at
once, and `meta.json` says `incomplete` with `n_frames_failed` at close. The other frames keep their indices
and times, so the gap shows. `meta.json` is replaced whole (a temporary file, written through to the disk,
then renamed); an interruption leaves the old file as it was. A reader that meets a `meta.json` that does not
parse reads the frames anyway, logs it, and reports `meta_damaged`. A complete recording is written exactly as
before (no new file, no new key). `n_frames` in the metadata stays the number of frames handed to the writer,
as an existing test requires. Open: `ohs process` reads the mode from the metadata, so a markers recording
with damaged metadata would be processed markerless unless `--mode` is given; a half-written PNG is left on
the disk, unlisted. Not tried: a real full disk.

**Gyro cover across a gap (R2-4).** While tracking was lost, the tracker took the product of the frames' gyro
rotations as the whole rotation since the last good pose, also when an interval in between had no IMU cover.
On the box scene (depth lost on two frames, one interval without gyro) the recovery then started from a
rotation that was too small, with the tight gyro sigma, and landed 2.38 degrees and 6.9 mm off; 0.61 degrees
and 1.7 mm of that stayed to the end of the scan. Now, when any interval since the last good pose was not
covered, the frame recovers as a frame without a gyro does (from the last good pose, with the wide rotation
sigma), says `gyro_prior: false` and reports the gap (`gyro_gap`: intervals and seconds): 0.04 degrees and
0.29 mm at the recovery. The cover is counted afresh after every accepted pose. Scans with full gyro cover
track the same byte for byte, losses that are covered included (28 frames and a 16-frame sequence with two
covered losses, processor path). Not run: the graphics card's tracker and its start, which restores the
cover state; a whole recording with an IMU gap put into it.

**A full mesh model (R2-5).** When the mesh model's pool of blocks is full, surface seen later is not fused. That
was logged once and said nowhere in the saved result. The model now counts what it refused (distinct blocks,
requests, integration calls, the first call), and the result holds a block `mesh_capacity` (blocks and
megabytes held, the limit, blocks refused, the frame of the first refusal) and, when anything was refused, a
warning: "The mesh model ran out of room after frame N: surface seen after that was not added (X of Y blocks
of surface left out; the model holds Z blocks, M MB). The mesh is incomplete there. Scan a smaller area at a
time, or make the mesh at a coarser mesh detail." The mesh and the recording are kept. Tested with the pool
forced small on both of the model's backends in one class (the graphics card's test is written and was not
run here). A scan that never fills the pool has the same mesh, laser points, path and report as before, plus
the block saying it was not full; a scan made with Open3D's model has no such status. The warning reaches
the user after Follow the camera today; Straighten's and Make mesh's stage cards, "Make only the thin walls
finer" and a merge do not pass it on yet (open). Seen on the way and not looked into: the processor path's
per-vertex sigmas differ between two runs of the same code on a handful of 80,000 vertices, by about 1e-6 mm.

**The recorder's stop in the app (R2-8, second part).** The server ignored the recorder's refusal: with the
budget set to 4 frames and a slow disk the scan went on "scanning" and counted 903 frames as recorded when 21
had been kept (the guard test). Now a scan whose recorder stops at its budget stops by itself: the user gets
the recorder's sentence at once as a notice (sent as an error, the only level the app shows as a toast while
scanning), the status message carries `record_stopped`, and the scan is saved through the normal stop with
the frames written and that sentence as its error. Frames refused after the stop are no longer counted as
recorded. A normal scan is unchanged. Open: the IMU calibration step ignores its recorder's stop and a write
error alike (it would solve from the shorter recording without a word); a settings key for the budget. Not
seen in the app's window; no real slow disk.

**`ohs process` and damaged metadata (R2-6, follow-up).** The command took the scan's mode from the metadata
and fell back to markerless, so a markers recording with a `meta.json` that does not parse was processed
markerless in silence. It now stops with "The recording's meta.json is damaged, so the scan's mode is not
known. Give it with --mode markers or --mode markerless." unless `--mode` is given. A recording without a
`meta.json`, or without a mode in it, keeps the markerless default.

**The noise profile after a matcher fallback (R2-7).** When the depth matcher fell back from the graphics card
to the processor mid-scan, the mesh model went on using the graphics card's noise profile (120, 5) for frames
the processor matcher (60, 8) had made, and the saved frame rows lost the event. Each frame is now fused with
the profile of the matcher that made its depth; what was fused before stays. The switch is saved in the frame
row, in `params.compute.fallbacks` and as a warning ("From frame N the depth was computed on the processor:
the graphics card's matcher failed (...). Uncertainty figures after that frame use the processor matcher's
noise model."). Size of the old error on a small scan with the switch after frame 3 of 8: vertices the same,
median sigma the same, the tail understated (95th percentile 0.94 mm instead of 1.07, 99th 2.18 instead of
3.05), mostly near holes. Without a fallback nothing changes but an empty `fallbacks` list (tracker byte for
byte; mesh, laser points and path identical; sigmas within the processor path's own run-to-run noise of
about 2e-6 mm). Open: a merge saves no fallback history; the fine-walls step re-fuses with one profile. Not
run: a real graphics-card fallback and the graphics card's model path.

**The same in a merge and in the thin walls' step (R2-7, the open part, 2026-11-27).** Read from the code first:
a merge computes every frame's depth again through the same pipeline as a scan, so each frame was already fused
with its own matcher's profile; what it lacked was the record. It now saves the fallback in its report
(`compute.fallbacks`: scan, frame, stage, from, to, reason) and warns, naming the scan ("From frame 3 of
"Front" the depth was computed on the processor: ..."); the warning reaches the merged scan's report as a
merge's other warnings do. "Make only the thin walls finer" does not run the matcher: it fuses the depth
Follow the camera kept, and fused all of it with the profile the model had when the scan began, so frames
kept after a fallback got the graphics card's profile. A kept frame made after a fallback now carries its
matcher's profile in the kept depth's index, and the finer model fuses it with that. Without a fallback
nothing changes: a merge's report has no `compute` block, the kept depth's index has no new field (byte for
byte the same file), and the finer model is given the profile it already holds; a kept depth written before
loads and fuses as it did. Checked before and after the change by the agent: the merge's report and the thin
walls' index, finer mesh and report the same; the merged mesh file is not the same byte for byte between two
runs of the SAME code on the processor path (the same vertices within 6e-7 mm), so a file comparison cannot
show it there. Tests: 5 in `tests/test_matcher_fallback_merge_walls.py` with the stand-in matcher that fails
at a chosen frame (4 failed before the change); with the merge, thin-walls and stage files around it 70 passed, 5 skipped
on the processor and 75 passed with the graphics card, no driver timeout, lint clean. Not done: a real
graphics-card failure; a whole Make mesh with the thin walls' step on and a fallback (the depth is kept only
when the model is on the graphics card, and the stand-in matcher is the processor's); not seen in the app.

**The motion sensor calibration step and its recorder (R2-8, third part).** The step ignored its recorder: a
recording that stopped because the disk could not keep up, or one with a frame that could not be written, was
worked out without a word. Now the step stops as soon as its recorder stops and says so at once. A recording
with a frame that could not be written is refused (the board's poses and the sensor's readings no longer pair
up over the gap); one cut below 22 s is refused with the same minimum a Stop pressed too early gets; a longer
cut one is worked out and the result's first advice says what happened. Each refusal says what to do (free
space or use a faster disk, record the step again). Tested with the calibration tests' fake camera; not seen
on the Setup page; no real slow disk.

**The budget as a setting (R2-8, the last part, 2026-11-27).** The two limits are now `[live] max_waiting_mb`
(1024, as before: 1 GiB) and `[live] max_waiting_seconds` (20). With the defaults a recorder is built exactly
as before (it is handed nothing and uses its own constants). Every recorder of a live session takes them: the
app's scan, the app's motion sensor calibration, and `ohs record`, `ohs scan` and the command line's
calibration captures (`ohs.app.live.recorder_budget`). A value under 64 MB or 1 s, or not a number, is refused
with the key's name when the settings file is read or saved; set in code past that check it is raised to the
minimum, so no recorder refuses its first frames (a recorder at 64 MB takes full 1280 x 800 pairs). 28 tests in
`tests/test_recorder_budget_setting.py` (26 failed before the change), with the config, live session,
recorder and record-stop files 87 passed on the processor; the 87 UI tests. Seen in the running app: the
Settings page's Advanced part lists "Most memory waiting for the disk 1024 MB" and "Most time waiting for
the disk 20 s" under Live preview. Not done: no scan was recorded with a changed budget in the app (the
server test starts one on the simulator and reads its recorder's limits); no real slow disk.

**The whole suite and the exe after both (commit ca5a97e, 2026-11-27).** The whole test suite on a frozen copy
of ca5a97e, which holds both changes above: 2496 passed, 7 skipped with the graphics card (43 min) and 2411
passed, 92 skipped on the processor (40 min); no failure, no driver timeout (the count of the driver's fault
reports was 5 before and after). The exe rebuilt from the same commit is 527 MB, starts in 2 s and serves the
page; it aligns the two simulated bracket scans as before (99 % of the smaller scan within 1 mm, typically
0.12 mm, 9 of 9 starting guesses agree); its wall-thickness readings are the same as Python's on 4 of 4
clicks and its hole readings on 12 of 12 (nine read, three refused, as in Python). Not done in the exe: a
recording with a changed budget, a matcher fallback, Straighten with the tags on.

**A full mesh model in a merge and in the fine-walls step (R2-5, second part).** A merge now saves its model's
`mesh_capacity` and warns once, naming the scan and the frame ("The mesh model ran out of room after frame 3
of "Front": surface seen after that was not added ..."); the warning reaches the merge's result and the
merged scan's report. "Make only the thin walls finer" returns its finer model's `mesh_capacity`; the server
does not turn that into the stage's warning yet (open, with Straighten's and Make mesh's stage results).
Tested with the pool forced small on the processor path; the graphics card's path not run.

## The no-GPU plate slide, and a check of the camera path (ENGINE_PLAN X0a, 2026-11-20)

Without a GPU the 0.5 mm plates slide in their first frames. On plate seed 0 the tracker's steps
were 3.7, 10.6, 9.4, 8.1, 4.5, 4.7, 4.9 mm where the hand moved 1.4 mm a frame; by frame 8 the
path is 37 mm off and stays there with the right speed. All of the error lies along the table
(0.00 to 0.05 mm across it, rotation right to 0.04°). While it slides the tracker's own numbers
look healthy: fitness 0.61 to 0.73, RMSE 0.12 to 0.23 mm, 5,300 to 8,100 inliers.

**Is it the no-GPU defaults switched on on 2026-11-20 (D7)? No: it is older, and they made it
smaller.** Tracked, no GPU; each cell is path max / last frame in mm, the plate's thickness
error in mm, and the share of its top kept:

| recording | today (D7 on) | all three put back | flip average off | guard off | ray-cast threshold 3 |
|---|---|---|---|---|---|
| 0.5 mm plate, seed 0 | 39.1 / 37.0, -0.387, 0.345 | 33.8 / 29.1, -0.346, 0.470 | 22.8 / 21.0, -0.300, 0.601 | 29.3 / 28.6, -0.359, 0.460 | 41.3 / 38.8, -0.397, 0.329 |
| seed 1 | 15.3 / 15.3, -0.228, 0.763 | 80.2 / 76.0, -0.414, 0.026 | 53.3 / 52.6, -0.373, 0.990 | 15.1 / 14.6, -0.235, 0.767 | 29.4 / 20.1, -0.318, 0.721 |
| seed 2 | 14.0 / 12.8, -0.228, 0.668 | 71.7 / 63.1, -0.464, 0.000 | 72.0 / 67.6, -0.464, 0.000 | 13.6 / 12.2, -0.238, 0.674 | 17.0 / 16.2, -0.257, 0.623 |
| 90-frame plate | 8.0 / 5.7, -0.074, 0.899 | 37.5 / 36.2, -0.289, 0.712 | 16.0 / 11.5, -0.230, 0.759 | 7.05 / 3.5, -0.066, 0.907 | 12.4 / 8.1, -0.145, 0.867 |
| 90-frame plate 2 | 8.3 / 5.7, -0.143, 0.900 | 49.6 / 49.6, -0.298, 0.975 | 28.5 / 28.5, -0.246, 0.849 | 9.4 / 5.9, -0.155, 0.887 | 16.1 / 11.8, -0.266, 0.778 |

So every plate is damaged without a GPU, not only seed 0: seeds 1 and 2 read 0.27 mm thick with
67 to 76 % of the top, the 90-frame plates 0.07 and 0.14 mm thin with 90 %.

**The cause is the tracking model.** ICP started at the true pose of frame 1, against the model
made from frame 0; how far it moves the camera (the tracker on filtered depth / the probe on raw
depth):

| depth from | model | moves, mm |
|---|---|---|
| no-GPU matcher | Open3D's | 3.4 / 6.6 |
| GPU matcher | Open3D's | 5.4 / 6.0 |
| no-GPU matcher | the GPU's | 0.8 / 0.6 |
| GPU matcher | the GPU's | 0.4 / 1.2 |

Frame 0 against its own one-frame model moves 0.65 mm with Open3D's model and 0.05 mm with the
GPU's. The pull comes from table pixels beside the model's holes and borders (for the probe,
half of it from the raw depth within 24 px of the image border, where the no-GPU matcher is
0.13 mm off); the plate's edges add almost nothing. The GPU's model drops hits within 8 mm of
where its observations end. The first jump is then carried on: the probe's first step was
6.9 mm from a prediction of zero, a first velocity of 206 mm/s, under the 300 mm/s that is let
through, and the speed stays nearly constant, so the acceleration guard acts on three frames
only.

**Tried, not adopted** (path max in mm, the five plates in the table's order; today 39.1 / 15.3
/ 14.0 / 8.0 / 8.3): the model's normals from its vertex map, not run / 9.2 / 14.6 / 3.2 / 7.0;
a first velocity from zero, 11.6 / 15.3 / 12.1 / 6.0 / 8.9; both, 6.0 / 9.2 / 10.2 / 4.1 / 7.0.
Both together are better on every plate and still 4 to 10 mm off; not judged on any other scene.

**The check that warns** (`ohs/track/path_check.py`): for every 0.8 s of tracked frames a
quadratic in time is fitted to the camera's position; the largest distance of a frame from its
window's fit is the jump, and above 4 mm the scan's report carries a warning. It reads the
tracked path only and changes no result.

| path | recordings | their true path error | the jump it reads | warns |
|---|---|---|---|---|
| GPU | 46 of set `full` that held | within 6.3 mm | 0.3 to 3.1 mm | none |
| GPU | hole plate 120 mm up, seeds 0 and 1 | 16 and 7 mm | 5.7 and 7.5 mm | both |
| no GPU | 3 that held | within 4.8 mm | 0.8 to 3.8 mm | none |
| no GPU | 18 that did not | 5.9 to 44 mm | 2.0 to 20 mm | 12 |

The 12: the three 0.5 mm plates, the three wall recordings, brackets seeds 2 and 3, and the four
dark or speckled ones. The 6 it misses drift slowly at a steady wrong speed: the three sheets
(6.1 to 9.4 mm off, reading 2.1 to 3.9), bracket seed 0 (7.1 mm, reading 2.9) and the two
90-frame plates (8.0 and 8.3 mm, reading 2.0 and 3.2), which are thinned without a warning. A
real hand's jerk might read as a slide; the simulator's hand is smooth, so that is not measured.

**The no-GPU baseline** (`tools/scorecard_baselines/2026-11-20-cpu-thin.json`, 21 recordings,
tracked path max in mm): sphere 0.19; brackets 4.5 to 7.1 (part median 0.48 to 0.69 mm, against
0.08 to 0.23 on the GPU path); sheets 6.1 to 9.4; walls 5.9 to 17.9; 0.5 mm plates 14.0 to 39.1;
90-frame plates 8.0 and 8.3; dark bracket 43.5 and 9.8; speckled 8.3 and 11.6. Without a GPU the
whole camera path is weak, not only on plates. With the check built in, both paths' scorecards
are the same in every row (no GPU: plates, walls and sphere compared; GPU: plates and sphere).

## The no-GPU tracking model's pull: settings tried (ENGINE_PLAN X0a, 2026-11-21)

Five settings for the tracking model the no-GPU path uses, all built and all off (what each is:
`docs/ENGINE_PLAN.md`, X0a). Short names here: **vmap** = the model's normals from its vertex
map; **border8** = no ray-cast hit within 8 mm of where the model's observations end (the GPU
model's rule); **rest** = the first velocity bounded from rest. No GPU, tracked; no value was
tuned on these recordings (8 mm is the GPU model's own, the others came from a synthetic plate).

**First look: the five plates and one bracket.** Path max / last frame in mm, then the plate's
thickness error in mm and the share of its top kept (the bracket: its median error in mm):

| setting | plate seed 0 | seed 1 | seed 2 | 90-frame plate | 90-frame plate 2 | bracket seed 0 |
|---|---|---|---|---|---|---|
| today | 39.1 / 37.0, -0.387, 0.34 | 15.3 / 15.3, -0.228, 0.76 | 14.0 / 12.8, -0.228, 0.67 | 8.0 / 5.7, -0.074, 0.90 | 8.3 / 5.7, -0.143, 0.90 | 7.1 / 3.8, 0.644 |
| vmap | 8.7 / 7.8, -0.126, 0.85 | 9.2 / 9.2, -0.106, 0.88 | 14.6 / 13.5, -0.206, 0.64 | 3.2 / 0.7, -0.007, 1.00 | 7.0 / 5.3, -0.106, 0.91 | 7.7 / 3.2, 0.556 |
| rest | 11.3 / 10.5, -0.223, 0.77 | 16.4 / 16.4, -0.235, 0.76 | 13.3 / 11.6, -0.252, 0.67 | 6.4 / 2.4, -0.045, 0.94 | 7.9 / 5.9, -0.150, 0.89 | 7.1 / 3.8 (it does not act) |
| border8 | 8.1 / 7.2, -0.114, 0.86 | 8.3 / 8.3, -0.073, 0.92 | 7.2 / 5.8, -0.098, 0.86 | 3.5 / 2.0, -0.013, 0.99 | 5.5 / 4.7, -0.060, 0.94 | 5.5 / 2.3, 0.576 |
| vmap + border8 | 5.4 / 3.7, -0.031, 0.95 | 6.7 / 6.7, -0.044, 0.95 | 5.6 / 4.8, -0.059, 0.90 | 4.6 / 1.9, -0.008, 1.00 | 3.7 / 2.7, -0.015, 0.99 | 14.6 / 11.6, 0.548 |
| vmap, normals not smoothed | 3.3 / 2.5, -0.004, 0.99 | 9.3 / 5.6, -0.076, 0.94 | 8.5 / 5.7, -0.050, 0.93 | 6.0 / 3.4, -0.049, 0.97 | 5.3 / 3.6, -0.038, 0.96 | **31.9 / 22.1, 6.82** |
| tracking truncation 2 voxels | **509 / 509**, -0.408, 0.00 | | | | | |
| probe without the image's border band (24 px) | 10.8 / 9.9, -0.206, 0.79 | 14.3 / 14.3, -0.201, 0.79 | 19.1 / 17.7, -0.310, 0.49 | 4.9 / 2.5, -0.021, 0.98 | 8.9 / 6.5, -0.181, 0.87 | 4.9 / 2.2, 0.603 |

**The 21 recordings of the no-GPU baseline.** Path max in mm / the part's median error in mm:

| recording | today | vmap | vmap + border8 | border8 |
|---|---|---|---|---|
| sphere, end to end | 0.2 / 0.053 | 0.2 / 0.052 | 0.2 / 0.052 | 0.2 / 0.052 |
| bracket seed 0 | 7.1 / 0.644 | 7.7 / 0.556 | 14.6 / 0.548 | 5.5 / 0.576 |
| bracket seed 1 | 4.5 / 0.692 | 3.8 / 0.643 | 6.3 / 0.620 | 7.0 / 0.594 |
| bracket seed 2 | 6.3 / 0.476 | 3.9 / 0.732 | 6.3 / 0.785 | 3.4 / 0.427 |
| bracket seed 3 | 6.3 / 0.483 | 5.1 / 0.583 | 5.0 / 0.410 | 5.9 / 0.305 |
| bracket seed 4 | 4.8 / 0.583 | 3.1 / 0.511 | **44.3** / 0.423 | 10.2 / 0.344 |
| sheet, seeds 0 / 1 / 2 (path max) | 9.4 / 6.4 / 6.1 | 5.6 / 4.5 / 7.3 | 14.6 / 5.2 / 6.2 | 10.7 / 5.7 / 6.1 |
| walls, seeds 0 / 1 / 2 (path max) | 17.9 / 17.1 / 5.9 | 10.0 / 12.6 / 7.6 | 9.4 / 20.2 / 9.8 | 14.3 / 10.8 / 4.1 |
| 0.5 mm plate, seeds 0 / 1 / 2 (path max) | 39.1 / 15.3 / 14.0 | 8.7 / 9.2 / 14.6 | 5.4 / 6.7 / 5.6 | 8.1 / 8.3 / 7.2 |
| dark bracket 1 / 2 (path max) | 43.5 / 9.8 | **429.8 / 49.5** | **309.5 / 26.0** | **176.9 / 87.3** |
| speckled bracket 1 / 2 (path max) | 8.3 / 11.6 | 5.7 / 2.4 | frames lost / 6.5 | 19.9 / 2.6 |
| 90-frame plates 1 / 2 (path max) | 8.0 / 8.3 | 3.2 / 7.0 | 4.6 / 3.7 | 3.5 / 5.5 |

Thin parts: no plate reads thinner and no plate keeps less of its top under any of the three
(0.5 mm plates: today -0.387 / -0.228 / -0.228 mm with 0.34 / 0.76 / 0.67 of the top; vmap +
border8 -0.031 / -0.044 / -0.059 with 0.95 / 0.95 / 0.90; border8 -0.114 / -0.073 / -0.098 with
0.86 / 0.92 / 0.86). The walls and the sheet are mixed under all three: the 0.5 mm wall loses its
face on one or two seeds, one wall recording reads 2.5 mm thick (13 mm with vmap), and the
sheet's flange and ribs move by up to 3 mm on one seed.

**Verdict.** Each of the three is worse on several scenes: the brackets (one seed slides, a boss
answers one click fewer), the sheet, the walls, the dark bracket above all, the speckled bracket;
so none is switched on. Judged again after the alarm below was mended, border8 is still worse on
those scenes; the plates' thickness row then reads "better".

The camera path's check warns on 12 of the 21 recordings today, on 9 with vmap, 7 with vmap +
border8 and 7 with border8. With every setting off, both paths give what they gave before:
processed afresh, the GPU path's plates, sphere and a bracket, and the no-GPU path's plate seed
0, sphere and bracket seed 0, are identical to the saved baselines in every row.

**The scorecard's "THICKER" alarm** fired on the plates because they read thicker, although they
moved from 0.11 to 0.47 mm for a true 0.5 mm. It now alarms only when a thin part reads thicker
and further from the truth by more than 0.05 mm; thicker towards the truth is "LESS THIN" and no
alarm.

## The gap guard by the matcher's doubt (ENGINE_PLAN X1, 2026-11-21): measured, not adopted

GPU path, Standard, the part placed alone. The idea: beside dropping all fused depth within 8 px
of a patch without depth (today's guard), also drop depth within a wider reach whose matcher
sigma is above a limit. Settings `PipelineParams.gap_guard_sigma_mm`, `gap_guard_sigma_px`,
`gap_guard_sigma_angle`, all off.

**Paired replays on the cached matcher depth** (tuned on the hole plates, the 200-frame car
panel, the 0.5 mm plate and the standing walls; at true poses; the change against today, per
seed):

| setting, on top of today's 8 px | panel Ø25 (today -1.30..-1.71) | panel p95 (today 1.87..2.33) | hole plate 120 mm up, Ø25 (today -1.40..-1.57) | walls 0.5 / 2 mm | walls' coverage, points |
|---|---|---|---|---|---|
| 3 mm, 10 px | +0.66..+0.98 | -0.34..-0.45 | +0.71..+0.81 | 0.5 mm +0.19 on one seed; 2 mm -0.14 / +0.11 / +0.14 | -0.9..-2.8 |
| 3 mm, 12 px | +0.67..+1.15 | -0.56..-0.75 | +0.65..+0.75 | 0.5 mm +0.15 / +0.35; 2 mm face lost on one seed | -1.8..-4.6 |
| 3 mm, 16 px | +0.74..+1.22 | -0.78..-0.99 | +0.69..+0.77 | 0.5 mm +0.32 / +0.55; 2 mm face lost | -3.2..-8.0 |
| 2 mm, 16 px | +1.21..+1.67 | -0.79..-1.02 | +1.24..+1.30 | 0.5 mm +0.24 / +0.65; 2 mm lost | -4.1..-10.1 |
| 5 mm, 16 px | +0.32..+0.95 | -0.75..-0.96 | +0.39..+0.45 | 0.5 mm +0.33 / +0.47; 2 mm lost | -2.3..-6.5 |
| 3 mm, 16 px, steep-view factor taken out | +0.63..+1.22 | -0.72..-0.95 | +0.66..+0.79 | 0.5 mm +0.25 / +0.23; 2 mm lost | -2.3..-6.2 |
| today's guard off, 3 mm, 8 px | about 0 | about 0 | about 0 | within 0.006 mm | about 0 |

The 0.5 mm plate lying flat does not change in any digit at any reach from 10 to 48 px. The
Ø10 hole near the floor overshoots at 3 mm, 16 px (-0.71 / -1.60 / -0.37 against +0.60 / +0.42 /
+0.28 today). The last row is the finding: within about 8 px of a patch the matcher's hole
factor puts nearly every sigma above 3 mm, so the gate is a wider distance guard.

**Full compares against the baseline, 36 recordings, 3 mm** (the change, over seeds):

| row | today | 10 px | 12 px | 16 px |
|---|---|---|---|---|
| car panel Ø25, tracked | -1.15 | +0.71..+0.84 | +0.83..+1.00 | +0.87..+1.15 |
| car panel p95, tracked / true | 1.78 / 2.13 | -0.42 / -0.40 | -0.64 / -0.66 | -0.93 / -0.93 |
| car panel, share past its free edge, tracked / true | 0.070 / 0.079 | -0.008 / -0.009 | -0.011 / -0.014 | -0.015 / -0.018 |
| hole plate 120 mm up, Ø25, true | -1.51 | +0.71..+0.81 | +0.65..+0.75 | +0.69..+0.77 |
| end-to-end panel Ø25 (a one-sided pan: reads wide), tracked / true | +1.34 / +1.23 | +0.36 / +0.45 further | +0.39 / +0.49 further | +0.39 / +0.49 further |
| bracket median / p95, true | 0.061 / 0.420 | -0.001 / +0.002 | -0.003 / -0.002 | -0.006 / -0.019 |
| bracket, right boss | answers | one click fewer | one seed 0.05 mm further | one seed worse |
| 0.5 mm wall, true | +0.79 | +0.19 on one seed | +0.15 / +0.35 thicker | +0.32 / +0.55 thicker |
| 2 mm wall, true | +0.26 | +0.11 / +0.14 thicker | a face lost on one seed | a face lost on one seed |
| sheet, 1 mm rib / 3 mm rib, true | +0.25 / +0.04 | +0.06 on one seed / worse | +0.08 / +0.07 thicker | +0.11 / +0.10 thicker |
| 0.5 mm plates | | identical | identical | identical |
| coverage within 1 mm, points: bracket | | -0.1..-0.7 | -0.3..-1.4 | -0.7..-2.2 |
| coverage: walls | | -1.0..-2.4 | -2.0..-4.2 | -3.3..-7.3 |
| coverage: dark bracket | | -1.8..-3.0 | -2.6..-3.4 | -3.5..-3.8 |
| coverage: speckled bracket | | -0.9 / -19.3 | -1.3 / -20.3 | -1.8 / -21.0 |
| coverage: sphere | | -2.9 | -4.1 | -6.0 |

Every compare ends "Worse on" the brackets, the walls, the dark and speckled brackets, both hole
plates, the car panel and the sheet, with alarms on thin parts (thicker; a face lost) and small
features. Holes and the panel's free edge improve; standing walls, ribs, bosses and coverage pay
for it. Not adopted.

With the settings off the results are what they were: the GPU path processed afresh is identical
to the baseline, and the no-GPU quick set is identical even with the settings on (its surface
model has no sigma). Frame time was not readable on the busy PC; the gate adds one comparison a
pixel inside a kernel that already runs.

## Hole rims from the laser lines' ends (ENGINE_PLAN X2, 2026-11-21): measured, not built

The idea: a laser line ends where the surface ends, so the ends of the laser curves at a rim,
collected over a scan, give the hole or the edge without the matcher's fill. Before building,
the engine's own laser extraction, stereo check and depth check were run on the cached
recordings (hole plates 10 and 120 mm up, seeds 0 to 2; the 200-frame car panel, seeds 0 to 4;
the end-to-end panel) and put into the world at the true poses. Each run of kept points has two
ends. A rim end lies within 1 mm of the face and 4 mm of a true rim or free edge, its line goes
on into the material and crosses the rim at more than 60° from the tangent. Distances are in
the face's plane, in mm: + means the end stops short of the rim, inside the material. One pixel
along the line is about 0.4 mm.

**Today's extraction** (it trims 2 points off each end of a segment):

| recordings | feature | ends a scan | median | 10 % | 90 % | within ±0.2 mm |
|---|---|---|---|---|---|---|
| hole plate 120 mm up | Ø25 rim | 114 | +0.99 | +0.33 | +3.42 | 6 % |
| | Ø10 / Ø6 rim | 141 / 39 | +1.70 / +1.05 | +0.54 / +0.30 | +3.60 / +3.38 | 1 % / 5 % |
| | free edge | 479 | +0.93 | +0.40 | +3.20 | 2 % |
| hole plate 10 mm up | Ø25 / Ø10 / Ø6 rim | 114 / 143 / 38 | +1.00 / +1.70 / +1.05 | +0.34 / +0.54 / +0.29 | +3.45 / +3.60 / +3.39 | 4 % / 1 % / 5 % |
| | free edge | 482 | +0.97 | +0.43 | +3.19 | 2 % |
| car panel, 200 frames | Ø25 rim | 383 | +1.25 | +0.57 | +3.37 | 1 % |
| | free edge | 316 | +1.07 | +0.46 | +3.07 | 2 % |
| end-to-end panel | free edge | 48 | +1.00 | +0.60 | +3.23 | 2 % |

**Without the trim** (the best today's extraction can give):

| recordings | feature | ends a scan | median | 10 % | 90 % | within ±0.2 mm | more than 0.5 mm past the rim |
|---|---|---|---|---|---|---|---|
| hole plate 120 mm up | Ø25 / Ø10 / Ø6 rim | 123 / 153 / 39 | +0.55 / +1.48 / +0.59 | -0.16 / -0.05 / -0.12 | +3.37 / +3.63 / +3.28 | 28 % / 16 % / 34 % | 1.1 % / 0 / 0 |
| hole plate 10 mm up | Ø25 / Ø10 / Ø6 rim | 125 / 154 / 39 | +0.56 / +1.54 / +0.60 | -0.16 / -0.02 / -0.12 | +3.36 / +3.65 / +3.29 | 27 % / 17 % / 31 % | 0.8 % / 0 / 0 |
| car panel, 200 frames | Ø25 rim | 437 | +0.67 | -0.03 | +3.33 | 22 % | 0.2 % |
| every scene | free edges | 333 to 511 (end-to-end: 54) | +0.32..+0.37 | -0.26..-0.14 | +3.05..+3.28 | 28 to 32 % | 1.0 to 5.6 % |

**Circles fitted to the untrimmed ends** (the diameter read, minus the truth):

| fit | Ø25, six hole plates | Ø25, five car panels | Ø10 | Ø6 |
|---|---|---|---|---|
| least squares | +0.86..+1.15 | +1.10..+1.35 | +1.32..+1.80 | +0.79..+1.51 |
| robust (Tukey) | +0.24..+0.97 | +0.79..+0.99 | +1.04..+1.78 | +0.12..+1.65 |
| the densest band of ends | -0.14..+1.16 | +0.44..+0.49 | -0.16..+3.24 | -0.92..+1.37 |

The stop rule asks for a diameter within 0.2 mm on 8 of 9 clicks; no fit comes near it on any
hole, so nothing was built.

What sets where a line ends:

- The trim of 2 points puts every end 1.0 to 1.2 px (0.4 to 0.5 mm) inside the rim.
- About 40 % of the ends stop 1 to 4 mm short: a dot of the projector merging with the line, or
  one outlier row, breaks the line within its last 20 rows, and the piece beyond is shorter than
  the shortest segment the extraction keeps (20 rows, about 8 mm). The line is there in the
  image, 10 px past the reported end. The width check and the outlier rule account for part of
  it (65 short ends of 200 become 56 without the width check, 44 with a looser outlier rule);
  the rule of one candidate a row and the exclusion where two lines cross account for none.
- The end points lie 0.13 to 0.24 mm off the face, against about 0.05 mm for laser points
  elsewhere: the line's profile is cut there.
- Ends almost never run past a rim: a line's end says "the surface reaches at least this far".

**The depth check drops almost nothing**, so there are no laser points to win back from it (true
poses, stereo-checked points, every second frame):

| recording | dropped by the depth check / points | of those, on the true surface |
|---|---|---|
| the three seams | 5 / 21,993; 3 / 19,609; 0 / 21,650 | 4; 3; 0 |
| end-to-end panel; car panel seeds 0 and 1 | 15 / 14,626; 6 / 79,610; 9 / 80,040 | 15; 6; 8 |
| hole plates 120 and 10 mm up | 7 / 73,192; 7 / 92,345 | 7; 7 |
| walls; 0.5 mm plate; sheet; bracket | 32 / 40,553; 15 / 28,044; 52 / 89,574; 50 / 85,561 | 32; 13; 50; 48 |

None of the dropped panel points lies within 3 mm of the free edge, and no rim end was made by
the depth check. So the seam's laser cloud stopping 1.5 to 2.6 mm short in "Gap and flush" is
not the depth check, as that section guessed; it is the extraction's rules above.

Limits: simulator only; the simulator's line has one width (sigma 0.4 mm), and a real line's
width and speckle can only push the ends further out; true poses only.

## Poses refined against the finished scan (ENGINE_PLAN X3, 2026-11-21): measured, not adopted, nothing built

GPU path, the scorecard's recordings. The largest error the engine makes on this path is the gap
between tracked and true poses (bracket median 0.075 to 0.228 mm against 0.058 to 0.063; the
sheet's flange +0.25 to +0.60 against +0.12 to +0.18). Two ideas to close it after the scan were
measured: register every frame again against the finished model (A), and let Straighten's
laser-crossing adjustment act (B). Neither can work, and the reason is the finding.

**What the tracked path's error is.** The placed tracked camera seen from the true camera (x
right, y down, z towards the part; mm). "Slow" is a running mean over 21 frames (about 2 s),
"fast" the rest; "out" is how far the camera stands back from the point it looks at.

| recording | constant x / y / z | slow rms x / y / z | fast rms x / y / z | largest step | out |
|---|---|---|---|---|---|
| bracket seed 0 | +0.23 / -0.06 / +0.02 | 0.17 / 0.10 / 0.06 | 0.15 / 0.07 / 0.06 | 0.86 | -0.05 |
| bracket seed 1 | +0.04 / +0.05 / -0.07 | 0.33 / 0.10 / 0.09 | 0.24 / 0.08 / 0.06 | 1.63 | +0.07 |
| bracket seed 2 (the bad one) | +0.27 / +0.17 / -0.17 | 0.31 / 0.13 / 0.12 | 0.18 / 0.07 / 0.06 | 0.91 | +0.14 |
| bracket seed 3 | +0.22 / -0.06 / +0.02 | 0.36 / 0.14 / 0.12 | 0.19 / 0.08 / 0.07 | 0.84 | -0.04 |
| bracket seed 4 | +0.03 / +0.05 / -0.07 | 0.24 / 0.08 / 0.06 | 0.23 / 0.10 / 0.08 | 1.76 | +0.07 |
| sheet seeds 0 / 1 / 2 | z -0.19 / -0.04 / -0.21 | x 0.21 / 0.49 / 0.34 | x 0.39 / 0.41 / 0.35 | 3.15 / 3.08 / 2.44 | +0.19 / +0.07 / +0.22 |
| walls seeds 0 / 1 / 2 | z -0.25 / -0.25 / -0.25 | x 0.17 / 0.21 / 0.29 | x 0.23 / 0.30 / 0.26 | 1.20 / 2.56 / 1.38 | +0.25 / +0.25 / +0.24 |
| sphere, end to end | +0.15 / -0.28 / -0.07 | at most 0.02 | at most 0.03 | 0.09 | +0.07 |
| car panel, 200 frames | -0.03 / -0.05 / +0.01 | 0.09 / 0.08 / 0.02 | 0.09 / 0.10 / 0.04 | 0.51 | -0.01 |

Most of the error is along the motion (x), half of it slow and half fast; on every sheet and wall
recording the camera stands 0.19 to 0.25 mm out (In question since 2026-11-23: the offset depends on how the path is placed, 0.00 to 0.07 mm when placed by the camera centres, and sheet seed 1 has 0.07. What does not depend on that is the relative pose of the frames of a wall's two sides: the raw frames read every wall within 0.07 mm at true poses and 0.3 to 1.0 mm too thick tracked. Why tracking places the sides apart is being tested; `docs/ENGINE_PLAN.md`, "An outside review", third round.); rotation is small (under 0.05° slow). At true
poses the scorecard's own placement already reads 0.1 to 0.25 mm along a sheet or a wall, so the
constant column carries that much doubt there.

**Which part of it costs the mesh.** The cached matcher depth was fused at the true poses plus
one part of the tracked error, and scored by the scorecard's judge (the tracked poses fed back
give the scorecard's tracked numbers exactly). The share is how much of the gap between true and
tracked that part brings back:

| recording, measure (tracked / true) | the constant translation | the slow part | the fast part |
|---|---|---|---|
| bracket seed 2, median (0.228 / 0.058) | 57 % | 98 % | 2 % |
| bracket seed 2, upright face (+0.326 / -0.008) | 66 % | 103 % | 0 % |
| bracket seeds 0 / 3, median | 37 / 76 % | 85 / 91 % | 22 / 44 % |
| sheet seed 0, flange (+0.604 / +0.180) | 119 % | 108 % | 20 % |
| sheet seed 0, 1 mm rib (+0.969 / +0.270) | 76 % | 106 % | 28 % |
| sheet seeds 1 / 2, flange | 175 / 152 % | 132 / 106 % | 83 / 33 % |
| walls seed 0, 0.5 / 1 / 2 mm | 79 / 101 / 74 % | 84 / 106 / 90 % | 58 / -6 / 16 % |
| walls seed 1, 0.5 / 1 / 2 mm | 108 / 125 / 130 % | 107 / 111 / 99 % | 17 / 19 / 19 % |
| walls seed 2, 0.5 / 1 / 2 mm | 81 / 94 / 118 % | 100 / 100 / 105 % | 6 / -15 / 31 % |

The slow error explains all of it on all eleven recordings (85 to 132 %); the jitter and the jumps
explain little. Most of the slow error is one translation in the camera's own frame, the same
for every frame. Taken out of the tracked poses with the truth's value (their rotation, drift and
jitter kept):

| share of the offset taken out | sheet seed 0, flange / 1 mm rib | walls seed 0, 1 mm / 2 mm wall | bracket seed 2, median |
|---|---|---|---|
| 0 (tracked) | +0.604 / +0.969 | +0.808 / +0.660 | 0.228 |
| 0.5 | +0.404 / +0.789 | +0.670 / +0.530 | 0.152 |
| 1 | +0.214 / +0.628 | +0.534 / +0.394 | |
| true poses | +0.180 / +0.270 | +0.534 / +0.354 | 0.058 |

**The scan cannot see this offset.** Across those four fuses the scan's own vertex sigma does not
move (the sheet's median 0.083 mm at every row, the walls 0.087 to 0.088, the bracket 0.087) and
is even a little lower than at true poses. The laser crossings meet better with the offset than
without it (sheet 0.092 against 0.115 mm, bracket 0.059 against 0.091), and a fit of a common
offset to the crossings gives values unrelated to the truth. The offset leaves the scan
consistent with itself, so nothing that refines against the scan can find it. It comes from
tracking.

**Idea A, tried as a prototype** (the tracker's own ICP against the finished 1 mm mesh, the gyro
holding the rotation; tuned on walls seed 0, then checked on one sheet, the bad bracket and the
car panel):

| recording | today / true poses | what the refinement gives |
|---|---|---|
| walls seed 0, 1 mm wall | +0.808 / +0.534 | +0.893, +0.956, +1.000 after rounds 1, 2, 3: thicker each round |
| the same, the model's borders left out | | +0.606, then +0.379 (below the true-pose value) and the 0.5 mm wall's face lost; the frames slide 0.7 to 0.8 mm |
| the same, weak directions held | | +0.774: almost no move |
| sheet seed 0, flange | +0.604 / +0.180 | +0.745, then +0.857 |
| bracket seed 2, share over 0.3 mm | 0.316 / 0.102 | 0.265, then 0.227 |
| car panel, 200 frames, path median / max | 0.138 / 0.366 | 0.220 / 0.613, then 0.277 / 0.710 |

It misses its stop rule on every variant: the flange and the walls get thicker (the finished
mesh still has its own thin-wall excess, so registering against it pushes the cameras further
out each round), the smooth panel's path is 60 to 100 % worse, and only the bad bracket seed
improves. One round would cost about 30 % of Make mesh.

**Idea B: why the laser-crossing adjustment does not act, and where it acts wrongly.** Run as
Straighten runs it, on 16 recordings:

| recordings | crossings | their miss, tracked / true (mm) | held-out ratio (acts under 0.85) | at true poses |
|---|---|---|---|---|
| brackets, seeds 0 to 4 | 3,238 to 4,762 | 0.053..0.071 / 0.038..0.050 | 0.97..1.16: does not act | 1.33..1.76 |
| sheets, seeds 0 to 2 | 2,558 to 2,725 | 0.092..0.123 / 0.085..0.119 | 1.01..1.09: does not act | 1.12..1.23 |
| walls seed 0 | 535 | 0.101 / 0.106 | **0.84: acts** | 0.61 |
| walls seed 1 | 1,005 | 0.068 / 0.067 | 0.92: does not act | 0.88 |
| walls seed 2 | 414 | 0.197 / 0.208 | **0.72: acts** | 0.47 |
| car panel, 0.5 mm plate, hole plate, end-to-end panel | 520 to 15,459 | at the truth's own | 1.03..1.04: does not act | 1.09..1.51 |

On 13 of 16 the crossings already meet at the lasers' own scatter at the tracked poses, so there
is nothing for it to do; forced, it makes the path worse on 12 of 15. On two of the three wall
recordings it does act, and there its own test is fooled: the test also passes at the true poses,
because the crossings of one frame share that frame's errors and a random split of crossings is
not an independent check. Fused at its poses, walls seed 0 reads +1.543 / +1.236 / +1.124 (today
+1.229 / +0.808 / +0.660: thicker) and walls seed 2 +0.880 / +0.434 / -0.049 (today +1.256 /
+0.911 / +0.535): its 2 mm wall then reads 1.95 mm, 0.2 mm under its true-pose value. The path's
median goes from 0.53 to 0.76 and from 1.50 to 1.84 mm. **This happens in the app today**
(Straighten runs inside Make mesh), and the scorecard does not show it, because the scorecard
runs the frame pass only. Holding out blocks of 10 frames instead of single crossings refuses the
walls (1.41 to 1.49) but also refuses the genuine drift case the tests keep (0.88 against 0.50).

Limits: simulated orbits, one run a recording, the GPU path only; timings rough.

## Straighten's laser crossings on what Make mesh runs (ENGINE_PLAN X0a, 2026-11-21)

**The scorecard's app chain.** Until now the scorecard ran the frame pass only. With
`--chain app` it runs what the app's Make mesh runs at Standard quality with default settings:
Follow the camera, Straighten (loop closures, then the laser-crossing adjustment, each acting
only when its own test allows, with a new fuse when the poses moved), Refine with lasers (kept
only when it changes enough), then the Default recipe. It calls the server's own functions with
the server's own parameters. Checked against a real Make mesh through the server on a
standing-walls recording: every engine call has the same arguments, the camera path and the
laser points are byte for byte the same, and the meshes are the same up to the numbering of
their vertices (the GPU hands out its voxel blocks in the order its threads finish, so two runs
of the same code number them differently; the judge scores the mesh numbered by position). The
frame pass is unchanged: six recordings processed afresh compare identical to the baseline of
2026-11-20 in 265 rows. Baseline of the app chain, GPU, 36 recordings:
`tools/scorecard_baselines/2026-11-21-gpu-core-app.json`.

**The app chain against the frame pass.** Identical in every scored number on 29 of 36
recordings and at every true-pose run: the sphere, the panels, all five brackets, all three
sheets, all 0.5 mm plates, the hole plate 10 mm up, the dark bracket, two of three seams. On
those Straighten and Refine with lasers change nothing. Where they act (frame pass → app chain):

| recording | what acted | what changed |
|---|---|---|
| standing walls, seed 0 | crossings (ratio 0.839, path moved 1.22 mm) | walls 0.5 / 1 / 2 mm +1.229 / +0.808 / +0.660 → +1.543 / +1.236 / +1.124; the 0.5 mm wall's face 0.483 → 0.267 covered; median / p95 0.125 / 0.720 → 0.144 / 0.973; path median 0.53 → 0.76 |
| standing walls, seed 2 | crossings (0.714, moved 1.94 mm) | walls +1.256 / +0.911 / +0.535 → +0.891 / +0.431 / -0.052 (the 2 mm wall reads 1.95 mm; at true poses it reads +0.156); median 0.145 → 0.106; path median 1.50 → 1.84 |
| speckled bracket 1 | crossings (0.758, moved 0.93), Refine | path median 1.02 → 0.73; one boss further off |
| speckled bracket 2 | crossings (0.625, moved 1.41) | path median / max 0.65 / 1.39 → 0.56 / 1.01; bosses 0.13 to 0.14 further off |
| hole plate 120 mm up, seed 0 | loops moved the poses, Refine | median 0.071 → 0.085; p95 1.99 → 1.67; Ø25 -6.90 → -6.61; path median 1.11 → 1.80 |
| hole plate 120 mm up, seed 2 | loops, Refine | p95 1.85 → 1.80; Ø25 -3.20 → -3.05; the bore -0.08 → -0.84 |
| one seam | Refine | p95 0.358 → 0.349 |

**The adjustment's own test** (the crossings a fit did not see must meet at 0.85 of their miss
or better):

| recordings | today: half the crossings held out | whole frames held out |
|---|---|---|
| standing walls, tracked, seeds 0 / 1 / 2 | 0.839 acts / 0.922 / 0.714 acts | 0.958 / 1.077 / 1.025: refused |
| standing walls at TRUE poses | 0.74 / 0.74 / 0.81: would act | 0.92 to 0.93: refused |
| a 640 px walls recording at true poses | 0.678: acts, and moves the true poses by up to 8.08 mm | 0.893: refused |
| the engine tests' sphere orbit with drift | 0.498: acts, mends the path | 0.572 / 0.612: acts, mends the path |
| the same orbit at true poses | refused | 1.22: refused |
| speckled brackets 1 / 2 | 0.758 acts / 0.625 acts | 1.051 refused / 0.763 acts |
| brackets, sheets, plates, panels, hole plates, seams | 0.97 to 1.27: refused | refused |

"Whole frames held out": every second frame of those used is held out, each half in turn; the
fit sees only crossings between two fitted frames, the test is the crossings between two
held-out frames; the worse of the two ratios must pass. The form was chosen on these same wall
recordings and the tests' drift case (every third frame acts at true poses, 0.73; blocks of ten
frames refuse the real drift, 0.88), so the walls' result is not an independent check of it.
What neither test catches: on the 640 px walls with drift added both pass (0.61), and the
adjustment then makes the path worse (4.8 to 22.6 mm rms).

**Against the app chain's baseline** (only these recordings differ; everything else identical):

| recording, measure | today | adjustment off | frames held out |
|---|---|---|---|
| walls seed 0, 0.5 / 1 / 2 mm | +1.543 / +1.236 / +1.124 | +1.229 / +0.808 / +0.660 | the same as off |
| walls seed 0, 0.5 mm face covered | 0.267 | 0.483 | 0.483 |
| walls seed 0, median / p95 | 0.144 / 0.973 | 0.125 / 0.720 | the same as off |
| walls seed 2, 0.5 / 1 / 2 mm | +0.891 / +0.431 / -0.052 | +1.256 / +0.911 / +0.535 | the same as off |
| walls seed 2, 0.5 mm face covered | 0.654 | 0.447 | 0.447 |
| walls seed 2, median; path median | 0.106; 1.84 | 0.145; 1.50 | the same as off |
| speckled bracket 1, path median | 0.725 | 1.019 | 1.019 |
| speckled bracket 2, path's last frame | 0.35 | 0.92 | 0.35 |

For reference, at true poses the walls read +0.79 / +0.53 / +0.35 (seed 0) and +0.76 / +0.62 /
+0.16 (seed 2). Both compares end "Worse on" the walls (seed 2 thicker, its 0.5 mm face less
covered) and the speckled bracket, so by the rule neither became the default; the choice is the
owner's (ENGINE_PLAN E-D2). Both are settings (`CurveParams.enabled`, `CurveParams.holdout`).

**What changed in the engine:** when the crossings change the poses, the adjustment's report
carries a sentence, Straighten's summary ends with it and the scan's warnings list it:
"Straighten moved the camera path by up to 1.2 mm to make the laser lines meet where they cross.
On thin walls seen from both sides this can make a wall read thinner or thicker than it is, so
check the thickness of thin walls another way before you rely on it." (Until the switch below
it also said "up to 0.6 mm in tests", a bound measured with the old test.)

**Switched (2026-11-21, the owner's choice, ENGINE_PLAN E-D2):** whole frames held out is the
default; half the crossings at random stays a setting (`CurveParams.holdout = "crossings"`).
The baseline of what Make mesh runs was made again, all 36 tracked runs computed afresh:
`tools/scorecard_baselines/2026-11-21-gpu-core-app-frames.json`. Against the baseline before
the switch only three recordings differ, as the "frames held out" column above says: walls
seed 0 (the walls +1.229 / +0.808 / +0.660, the 0.5 mm face 0.483 covered, median / p95 0.125 /
0.720, path median 0.531), walls seed 2 (+1.257 / +0.911 / +0.535, the 0.5 mm face 0.447,
median 0.145, path median 1.496) and speckled bracket 1 (path median 1.019; its far-off right
boss 4.85 → 2.22). The other 69 of 72 runs are identical in every scored number; the adjustment
still acts on speckled bracket 2. The fresh runs agree with the earlier candidate in every
scored number on 35 recordings; on speckled bracket 1 they differ by about 0.000001 mm, which a
repeat of the same code also shows (the GPU from run to run). Without a GPU neither test acts
on walls seed 0 (0.930 before, 1.172 now), bracket seed 0 (1.129, 1.263) or the sphere (too few
crossings), so those results are identical.

Limits: simulated, one run a recording, the GPU path only; the frames form was chosen on the
same wall recordings it is judged on.

## Tracking on dark, plain and thin parts (ENGINE_PLAN X4, 2026-11-21): measured, nothing adopted

GPU path; the matcher's depth fed from the cache; each run scored with the scorecard's judge (a
run without changes gives the scorecard's tracked numbers exactly). "Out" is how far the placed
camera stands back from where it really was, in mm. X3 found that this offset, shared by every
frame, is most of what tracked scans lose on thin parts; this section is about where it is made.

**Not by a model that is wrong at the truth.** With every tracker fusing at the true poses, each
frame was first registered by ICP from its true pose against that model. Mean move in the
camera's frame (x / y / z; "out" would be z about -0.2): walls -0.10 / -0.02 / +0.02; sheet
+0.28 / +0.03 / -0.03; the bad bracket seed +0.21 / -0.07 / +0.08. No outward pull.

**Not by the first frame.** Frame 1 is registered against a model made of one frame and lands 1
to 8 mm off on every scene with a table (walls 1.7, sheet 1.5, hole plates 3.9 to 8.0). But
giving every tracker the true pose for frame 1, or for frames 1 to 5, leaves the offset: walls
+0.233 / +0.246 (today +0.254), sheet +0.175 / +0.177 (+0.191), bracket +0.142 / +0.139 (+0.136).

**It is where the tracking loop settles, and the tracking model's band behind the surface sets
that.** Frames are registered against a model fused at slightly-out poses; the band keeps the
swollen surface; the error settles. One setting changed at a time; cells are out, then the
thicknesses (walls 0.5 / 1 / 2 mm; sheet flange / 1 mm rib / 3 mm rib; bracket median / share
over 0.3 mm):

| tracking model | walls seed 0 | sheet seed 0 | bracket seed 2 |
|---|---|---|---|
| today (band 1 voxel = 2 mm) | +0.254; 1.229 / 0.808 / 0.660 | +0.191; 0.604 / 0.969 / 0.535 | +0.136; 0.228 / 0.316 |
| at true poses, for reference | 0.786 / 0.534 / 0.354 | 0.180 / 0.270 / 0.030 | 0.058 / 0.102 |
| band off (2 voxels) | +0.280; 1.322 / 0.963 / 0.734 | +0.313; 0.897 / 1.159 / 0.813 | +0.230; 0.284 / 0.463 |
| band 0.75 voxel | +0.236; 1.132 / 0.742 / 0.579 | +0.068; 0.349 / 0.824 / 0.304 | +0.065; 0.176 / 0.177 |
| band 0.6 | +0.215; 1.074 / 0.657 / 0.514 | -0.051; 0.152 / 0.605 / -0.001 | +0.019; 0.151 / 0.144 |
| band 0.5 | +0.226; 1.029 / 0.689 / 0.545 | **-0.146; 0.075 / 0.364 / -0.421** | -0.025; 0.122 / 0.127 |
| truncation 1.5 / 3 voxels | +0.250 / +0.299 | +0.092 / +0.341 | +0.105 / +0.180 |
| tracking voxel 1.5 / 3 mm | +0.260 / +0.310 | +0.121 / +0.458 | +0.123 / +0.215 |
| border 4 / 12 mm | +0.249 / +0.232 | +0.225 / +0.194 | +0.163 / +0.141 |
| weight threshold 4 / 16 | +0.234 / +0.275 | +0.184 / +0.174 | +0.129 / +0.144 |
| normal window 3 / 9 | +0.251 / +0.239 | +0.220 / +0.189 | +0.130 / +0.114 |
| tracking filter 4 / 9 px | +0.246 / +0.246 | +0.189 / +0.147 | +0.146 / +0.107 |

The band, the truncation and the voxel size move it, the rest barely. But a shorter band moves
every recording inward together: the brackets that already sat in (seeds 0 and 3, out -0.05)
get worse (median 0.117 → 0.155 and 0.090 → 0.141), and at 0.5 the sheet's 3 mm rib reads
0.42 mm thin. On a wall with exact depth the band changes nothing (face offsets -0.14 / -0.07 /
+0.05 / +0.18 at 2 mm and at 1 mm), so it acts through noisy depth and poses that disagree.

**Judged: the band at 0.75 voxel, 36 recordings, on what Make mesh runs** (the change, by seed):

| rows | change | verdict |
|---|---|---|
| standing walls 0.5 / 1 / 2 mm | -0.05..-0.14 / -0.01..-0.20 / 0.00..-0.23 | nearer the truth |
| sheet flange / 1 mm rib / 3 mm rib | -0.25..-0.29 / -0.15..-0.33 / -0.23..-0.37 | nearer; but on seed 1 the flange reads 2.00 mm and the 3 mm rib 2.82 mm, under the truth |
| 0.5 mm plates | within 0.003 mm; top kept identical | same |
| brackets: bosses / shaft radius | -0.03..-0.10 / -0.02..-0.14 | further off on every seed: the swelling had hidden that they read small |
| bracket median, seeds 0 to 4 | +0.038 / +0.048 / -0.053 / +0.051 / -0.019 | mixed |
| sheet path max | +0.67 / +2.71 / +1.61 | worse |
| hole plate 120 mm up, seed 0 | path max 16.3 → 8.0; Ø25 3.5 mm nearer; one click fewer on the bore | mixed |
| dark, speckled, hard plates (last frame) | 3.79 → 3.47, 0.70 → 0.81, 1.13 → 1.19, 0.92 → 1.00, 1.38 → 1.96, 0.47 → 0.45 | mostly worse |
| one seam, path median | 0.32 → 0.99 | worse |
| car panel, sphere: path | within 0.04 mm | same |

Last line: worse on the brackets, the dark and speckled brackets, the hard plate, both hole
plates and the sheet (on the frame pass: the same but one). It misses the experiment's own stop
rule too (no hard scene 30 % better or under 1 mm). Off. Nothing changed by default.

**The hole plate 120 mm up that loses tracking (seed 0).** At frames 52 to 56 one frame lands
4.2 mm off from a start 1.7 mm off, and the constant-velocity prediction carries it on (8.2 and
22.8 mm off at the next two starts; the path is 13.8 mm off at frame 53, back by frame 57). The
tracker's own numbers stay healthy throughout (fitness 0.78 to 0.82, RMSE 0.35 to 0.38 mm, about
5,700 inliers), and ICP from the true pose against the same model moves at most 1.6 mm: the
model is sound, the translation is weakly held. Seeds 1 and 2 have step errors of about 2 mm
that are not carried that far. Frames are 0.1 s apart, so a guard on acceleration at 2 m/s²
allows a step to change by 20 mm and never acts.

**The hard recordings, by frame.** Dark bracket 1: its 3.9 mm are made at frames 1 and 2 (1.2 mm
off at frame 1 with fitness 0.53, then a jump of 5.2 mm) and held by the model to the end. Dark
bracket 2: about 1 mm in frames 2 to 5. Speckled: 1.3 and 1.7 mm off at frame 1 (fitness 0.10
and 0.20 against the one-frame model), then slowly along the motion. Hard 0.5 mm plates: single
pairs of frames slip about 2 mm along the plate and come back.

**Detail mode.** 31 of the 36 recordings switch to the tracker's detail mode at frame 1 or 2,
judged on a model made of one frame. It is needed: without it the walls slide (path max
15.9 mm), the bracket runs away (387 mm) and the sheet's 3 mm rib loses 2 mm. Leaving the raw
depth out near holes, near depth steps or where the matcher is unsure wrecks the path on walls,
sheet and bracket (5 to 20 mm).

**A velocity guard on the GPU model** (built as `FusionParams.gpu_velocity_guard`, off): at
2 m/s² it never acts on these recordings; at 0.6 m/s² the hard scenes get worse (dark last frame
3.97, speckled 1.35, hard plate 1.92 against 1.38).

**The true-pose reference carries its own error.** Fused at true poses in the tracker's world
(the first camera's frame, as every tracked scan is), walls seed 0 reads 0.975 / 0.531 / 0.387;
the scorecard's true-pose run, fused in the simulator's world, reads 0.786 / 0.534 / 0.354. The
1 mm grid's alignment to the walls accounts for about 43 % of that 0.5 mm wall's gap between
tracked and true.

Limits: simulated, one run a recording; tuned on walls seeds 0 and 1, sheets 0 and 2, bracket 2,
panel 0, plate 0; timings on a busy PC, not judged.

## The scan's start, and a weakly held step (ENGINE_PLAN X4b, 2026-11-22): measured, not adopted

X4 found that the first frame of a scan is registered against a model made of one frame, from a
prediction of zero motion, and lands 1 to 8 mm off; the frames after it keep that error (the dark
bracket's whole 3.9 mm). X4b built a start that holds the first frames back, and a rule for a
weakly held step. Everything is a setting that is off; no default changed.

**Built (off):**
- `PipelineParams.start_frames` (k; 0 is off), `start_sweeps` (2), `start_predict` ("pose" or
  "fit"), `start_first`, `start_detail_only`, `start_detail` ("frame" or "window"). The first k
  frames are tracked as before but held back: nothing of them goes into the mesh's model and
  their laser points wait. Then each is registered again against a tracking model of all the
  other held frames, from its own pose or ("fit") from a quadratic in time fitted to the others'
  positions, its rotation held as the gyro's; with `start_first` frame 0 too, the world then put
  back at its camera. The tracking model is made again and the frames are fused at their new
  poses. With `start_detail_only` this acts only on a scan that tracks in detail mode; a smooth
  part's frames are fused exactly as tracked. `report["start"]` says what the start did.
- `FusionParams.weak_step_strength`, `weak_step_mm`, `weak_step_mode` ("hold" or "undo"): a
  frame whose translation is weakly held and whose step along its weak direction differs from
  the last carried step by more than the limit does not carry that difference into the next
  prediction.
- The GPU tracking model only. On the no-GPU model the start took an 8-frame test recording
  108 mm off, so there the setting is not used (a log line says so) and results are as before.

**The start's variants on the dark bracket** (frame pass; last frame, today 3.79 mm; true poses
for frames 1 and 2 would give 1.48, for frames 1 to 5 0.95):

| variant | last frame (mm) |
|---|---:|
| k = 6, from its own pose | 3.24 |
| k = 6, fit | 2.72 |
| k = 6, own pose, frame 0 too | 2.23 |
| k = 6, fit, frame 0 too | 1.28 |
| k = 6, fit, frame 0 too, 4 sweeps | worse (path max 4.46) |
| k = 4, fit | 3.12 |
| k = 8, fit, frame 0 too | 1.82 |
| **k = 10, fit, frame 0 too** | **0.585** |

Frame 0 had to be registered too: frames 1 to 5 settled about 3 mm off frame 0 as a group.
Without `start_detail_only` the end-to-end sphere's path got worse (median 0.105 → 0.127, max
0.153 → 0.190 mm); with it the smooth scenes are identical.

**The other ideas:**
- Deciding for detail mode after 10 frames ("window"): the same decision as today on all 8
  recordings tried (median strength 2.8e-5 to 1.6e-4). No effect.
- The weak-step rule (strength 1e-3, 3 mm) alone: the lost hole plate's path max 16.3 → 8.0 mm
  (the aim was under 6), a hard plate's last frame 1.38 → 1.79, the dark bracket unchanged.
  Together with the start it adds nothing (hole plate seed 0 2.45 → 2.52).
- The accelerometer as a check on a step: not built.

**The judged candidate** (k = 10, fit, frame 0 too, detail mode only), on what Make mesh runs,
against `tools/scorecard_baselines/2026-11-21-gpu-core-app-frames.json`, 36 recordings, tracked
runs, before → after:

| | before | after |
|---|---|---|
| Dark brackets, last frame (mm) | 3.786 / 0.696 | 0.585 / 0.542 |
| Speckled brackets, last frame | 1.125 / 0.349 | 1.259 / 0.876 |
| Hard 0.5 mm plates, last frame | 1.379 / 0.471 | 1.043 / 0.431 |
| Hole plate 120 mm up, path max, seeds 0 / 1 / 2 | 16.30 / 7.09 / 3.89 | 2.82 / 3.96 / 2.71 |
| ... its Ø 25 hole (mm, minus is small) | -6.61 / -2.91 / -3.05 | -2.97 / -2.77 / -3.07 |
| Standing walls seed 0, 0.5 / 1 / 2 mm (too thick by) | 1.229 / 0.808 / 0.660 | 1.180 / 0.837 / 0.644 |
| Standing walls seed 1 | 1.182 / 0.701 / 0.579 | 1.192 / 0.701 / 0.569 |
| Standing walls seed 2 | 1.256 / 0.911 / 0.535 | 1.224 / 0.888 / 0.497 |
| ... the 0.5 mm wall's face covered, seeds 0 / 1 / 2 | 0.483 / 0.568 / 0.447 | 0.505 / 0.584 / 0.508 |
| Walls, last frame, mean | 1.358 | 0.548 |
| Sheet seed 0, flange / 1 mm rib / 3 mm rib | 0.604 / 0.969 / 0.535 | 0.575 / 1.002 / 0.549 |
| Sheet seed 1 | 0.254 / 0.658 / 0.171 | 0.247 / 0.628 / 0.117 |
| Sheet seed 2 | 0.596 / 1.211 / 0.719 | 0.634 / 1.223 / 0.759 |
| 0.5 mm plates, top kept, seeds 0 / 1 / 2 | 0.986 / 0.999 / 1.000 | 0.999 / 1.000 / 1.000 |
| Brackets seeds 0 to 4, part median | 0.117 / 0.075 / 0.228 / 0.090 / 0.090 | 0.107 / 0.076 / 0.205 / 0.079 / 0.081 |
| Brackets seeds 0 to 4, path max | 1.15 / 2.40 / 1.47 / 1.64 / 2.02 | 1.32 / 3.70 / 1.42 / 1.53 / 1.36 |

The end-to-end sphere and panel, the five 200-frame car panels and the flush-ramp seam are
identical in every number. Coverage is within 0.003 everywhere. The 0.5 mm plates' thickness
stays within 0.004 mm.

**The compare's verdict: worse on 7 scenes**, so not adopted (the owner's rule, E-D1):
- bracket seed 1's path max +1.30 mm, and seed 4's shaft 0.06 mm further off;
- the speckled brackets: last frame 0.349 → 0.876 on one, the right boss 3.0 mm further off on
  the other (a click known to be unreliable there);
- the hole plate 10 mm up: seed 1's Ø 25 0.51 mm and Ø 10 0.10 mm further off, seed 0's bore one
  answer fewer, seed 2's Ø 25 0.05 further;
- the hole plate 120 mm up: seeds 1 and 2 hole centres further off, seed 2's bore centre +0.30;
- sheet and wall readings up to 0.04 mm further from the truth on single seeds (under the 0.05
  alarm), beside others that come nearer;
- hairlines: the dark bracket's rotation median +0.01°, hard plate 2's path max +0.14 mm, a
  0.5 mm plate's median +0.002 mm.

X4b's own targets are met (dark bracket's last frame under 1.5 mm, the lost hole plate's path
max under 6 mm, smooth scenes unchanged); what fails is "no scene worse beyond its spread".

**The camera path's check** on the kept paths: today it warns on 2 of 36 (hole plate 120 mm up
seeds 0 and 1, jumps 7.46 and 5.65 mm); with the candidate on none (1.57 and 2.61).

**Time:** the start takes 2.6 to 3.9 s on a detail-mode scan, about 0.4 s on a smooth one
(rough, busy PC). In a live scan it would hold the first 10 frames (about 1 s) and then pause
1 to 3 s; the setting is off, so the live scan is as before.

**With the settings off** nothing moves: fresh runs at the defaults compare identical to the
baselines on both paths.

Limits: simulated, one run a recording; the variant was tuned on the dark bracket; one full
compare was spent, on the best variant.

### Why some scenes got worse, and the start registered together (2026-11-22)

A look at the rows that failed the compare, on a small set (the brackets, the dark and speckled
brackets, two hole plates, the walls' seed 0, the end-to-end sphere). Two separate causes.

**1. The start itself: a shared bias, turned into an offset.** Measured against the truth, every
registration of a start frame against the model of the other nine lands about 0.2 mm off them in
the same direction, along the table, where the translation is weakly held (speckled brackets
0.22 and 0.21 mm, dark bracket 0.28 mm). The rounds registered the frames one after the other,
each against the others' newest poses, so the later frames took the bias on top of the earlier
ones' and frames 1 to 9 ended up off frame 0 as a group (speckled bracket about 0.45 mm, then
held by the whole scan). This is also why 4 rounds were worse than 2.

**Built (off): `PipelineParams.start_together`.** In a round every frame is registered against
the others' poses from before the round, then all are moved together; a bias they share moves
them alike and is gone when the world is put back at frame 0's camera. Off, the code runs as
before.

**2. Single frames that slip in mid-scan, with the start off too.** Bracket seed 1's path max
is a slip at frames 151 to 157 (today 2.40 mm at frame 165 after 1.80 at 155); with starts of
6 / 8 / 10 / 12 / 14 frames it is 3.38 / 1.98 / 3.70 / 2.61 / 4.13 mm, so its height tips with
the start's poses and no start mends it. The hole plate 120 mm up, seed 0, is the same at
frames 52 to 56: today 16.3 mm; every one-after-the-other start happened to avoid it (2.0 to
2.8 mm); registered together it is 19.1 mm at 10 frames and 3.1 / 5.0 / 2.6 at 8 / 12 / 14. The
"rescue" in the table above was therefore luck, not the start mending anything. The weak-step
rule on top takes 19.1 to 8.3 mm and does nothing for the bracket.

The second speckled bracket's last frame (0.349 → 0.876) was not made in the frame pass (0.92
against 0.99): Straighten's crossings act today and did not with the candidate.

**The small set**, what Make mesh runs, tracked; last frame / path max / path median (mm):

| recording | today | start, one after the other | start, together |
|---|---|---|---|
| Speckled bracket 1 | 1.125 / 1.62 / 1.02 | 1.259 / 1.71 / 1.19 | 0.570 / 1.28 / 0.73 |
| Speckled bracket 2 | 0.349 / 1.01 / 0.555 | 0.876 / 1.39 / 0.664 | 0.437 / 1.04 / 0.467 |
| Bracket seed 0 | 0.417 / 1.15 / 0.489 | 0.519 / 1.32 / 0.526 | 0.343 / 1.20 / 0.413 |
| Bracket seed 1 | 0.597 / 2.40 / 0.674 | 0.527 / 3.70 / 0.519 | 0.778 / 2.55 / 0.663 |
| Bracket seed 2 | 0.554 / 1.47 / 0.699 | 0.555 / 1.42 / 0.679 | 0.700 / 1.46 / 0.726 |
| Bracket seed 3 | 0.641 / 1.64 / 0.757 | 0.609 / 1.53 / 0.735 | 0.540 / 1.34 / 0.611 |
| Bracket seed 4 | 0.768 / 2.02 / 0.763 | 0.705 / 1.36 / 0.597 | 0.389 / 1.58 / 0.461 |
| Dark bracket 1 | 3.786 / 6.29 / 3.88 | 0.585 / 2.02 / 0.675 | 0.673 / 1.96 / 0.802 |
| Dark bracket 2 | 0.696 / 2.28 / 1.03 | 0.542 / 1.77 / 0.709 | 0.483 / 1.85 / 0.570 |
| Hole plate 120 mm up, seed 0 | 2.10 / 16.3 / 1.80 | 2.11 / 2.82 / 1.68 | 2.14 / 19.1 / 2.01 |
| Hole plate 10 mm up, seed 1 | 0.723 / 2.26 / 1.01 | 0.629 / 1.83 / 0.922 | 0.692 / 2.30 / 1.03 |
| Standing walls, seed 0 | 1.067 / 1.21 / 0.531 | 0.580 / 1.06 / 0.391 | 0.386 / 0.99 / 0.332 |
| End-to-end sphere | | identical | identical |

Readings with the start together: the hole plate 120 mm up seed 0's Ø 25 -6.61 → -11.46 (the
slip); the walls' 0.5 / 1 / 2 mm 1.229 / 0.808 / 0.660 → 1.174 / 0.821 / 0.549 (all still too
thick; nothing thinned) and the 0.5 mm wall's face covered 0.483 → 0.523; on the speckled
brackets the boss and shaft clicks are 0.04 to 0.11 mm further off and one boss (known to be
unreliable there) is not answered; bracket seed 3's left boss -0.074 → -0.133. Part medians
within 0.004 mm.

**The small readings that failed the first compare** (hole plate 10 mm up, sheet, walls) change
with mixed signs across the seeds and inside the spread between the baseline's seeds (Ø 25
spread 0.67 mm against +0.00 / +0.52 / +0.05; 0.5 mm wall spread 0.075 against -0.05 / +0.01 /
-0.03): single seeds' noise, not a trend.

**Only at full resolution** changes nothing in these numbers: the scorecard already runs Make
mesh's frame pass at full resolution, and the live preview is never scored. It would only spare
the live view the pause.

**Verdict: still not adopted, and no full compare spent.** Registered together the start is
better than one after the other on 9 of the 12 recordings that moved, and better than today on
most; but the small set is not better or the same throughout (the hole plate's slip, bracket
seeds 1 and 2's last frame, the clicks on the speckled brackets). What decides the largest rows
is the slip in mid-scan, now ENGINE_PLAN X4c; the start is to be judged again once that holds.

Limits: simulated; one run a recording; the bias was measured on brackets only; the full set was
not run with `start_together`; why the starts one after the other avoid the hole plate's slip
more often is not explained; the clicks on the bosses were not looked into.

## A single frame that slips in mid-scan (ENGINE_PLAN X4c, 2026-11-22): one cause held, one open, not adopted

X4b left two slips: the hole plate 120 mm up, seed 0, at frames 52 to 56 (16.3 mm) and bracket
seed 1 at frames 151 to 166 (2.40 mm). They have different causes. A rule for the first is
built as a setting that is off; the second is open. No default changed, no compare spent.

**The probe.** In the frame pass of what Make mesh runs, each frame was registered before it
was fused from its prediction (what the engine does) and from the true motion since the last
frame, and each end pose scored by a truncated point-to-plane cost against the model ray cast
at that pose, a point of the frame without a model point counted at the cap.

**The hole plate: the registration slides into another fit.**

| frame | prediction off (mm) | result off | from the truth | cost, result / from the truth | step from the prediction |
|---|---:|---:|---:|---|---:|
| 50 | 0.95 | 2.28 | 1.73 | 3.153 / 3.125 | 2.98 |
| 51 | 1.68 | 1.01 | 1.02 | 3.018 / 3.018 | 2.68 |
| 52 | 1.62 | 5.08 | 0.35 | 3.256 / 2.968 | 6.68 |
| 53 | 4.49 | 11.30 | 5.65 | (frame 52 is in the model) | 6.87 |

At frame 52 the registration slides 6.7 mm along the weakly held direction (along the table)
and stops 5.1 mm off. From the truth it stays (0.35 mm), and so does every other start tried
(the last pose without the carried step, the prediction without the coarsest level, the
prediction mirrored along the axis: 0.2 to 0.9 mm off, cost 2.95 to 2.97). Against a model of
the last 30 frames at their true poses the prediction still slides to 5.3 mm, so the model is
not the cause. What the engine can see without the truth: the step along the weak direction
(6.7 mm; at most 2.9 in the 20 frames before) and the cost (0.29 mm² apart; within 0.03 on
healthy frames). The registration's own numbers show nothing (fitness 0.80 against 0.81): its
cost leaves out the points it loses, and the slide lost 2 % of them.

**Bracket seed 1: the model pulls.** Frame 152 lands 1.63 mm off, and from the truth still
1.24 mm: the truth is not a minimum for that model. Against a model of the same 30 frames at
their true poses it lands 0.12 mm off (frame 158: 1.13 against 0.14). The frames before were
fused 0.3 to 0.6 mm off, and the 2.40 mm is several such frames adding up. Nothing per frame
looks unusual (fitness 0.96, rmse 0.37), and no second start can mend it.

**The accelerometer** (in the recordings, not used by the engine): with gravity and its bias
unknown (a running mean over 3 s) it predicts the next position no better than constant
velocity (median 0.6 mm, 95th percentile 0.8 to 1.0). With both fitted over the whole true path
the median is 0.06 to 0.07 mm and the maximum 0.19: enough to tell a 2 mm jump from motion, if
both were estimated while scanning. Not built.

**Built (off): `FusionParams.slip_check_mm`** (None is off), `slip_starts` ("rest", "fine"),
`slip_margin` (0.05 mm²). A frame whose step along its weakest translation leaves the
prediction by more than the limit is registered again from the last pose without the carried
step ("rest") and from the prediction without the coarsest level ("fine"); the result the model
agrees with best is kept when its cost beats the first by more than the margin.
`info["slip_check"]` says what was compared. Frames within the limit are tracked exactly as
before.

**Does it hold, however the scan starts?** Frame pass, path max (mm); the start registered
together (`start_predict=fit, start_first, start_detail_only, start_together`), the rule at
3 mm:

| start | hole plate, rule off | rule on | bracket seed 1, rule off | rule on |
|---|---:|---:|---:|---:|
| off | 16.30 | 8.00 (frame 1; 2.67 after frame 10) | 2.40 | 2.40 |
| 6 frames | 2.71 | 2.71 | 1.90 | 1.90 |
| 8 | 3.09 | 3.09 | 3.30 | 3.30 |
| 10 | 19.1 | 2.75 | 2.55 | 2.55 |
| 12 | 4.99 | 2.32 | 2.61 | 2.61 |
| 14 | 2.64 | 2.64 | 1.81 | 1.81 |

The 8.00 mm at frame 1 is today's error of the first frame (X4), until now hidden behind the
slip; the start mends it. With the start off the rule looks at 7 frames of the hole plate and
changes only frame 52 (by 5.5 mm). On the bracket it looks at frame 1 only and changes nothing.

**Verdict: not adopted, no compare spent.** The hole plate's slip is held at every start (2.3
to 3.1 mm after the start, from 16.3 and up to 19.1). The bracket's is untouched and still tips
with the start's length (1.81 to 3.30 mm), which fails X4c's stop rule, so the small set and
the feature readings were not run with the rule on.

**With the settings off** the quick set on what Make mesh runs compares identical (GPU, fresh
runs), and the tracking tests pass on both paths.

Limits: simulated; one run a recording; the frame pass of two recordings only; the rule was not
run on other scenes (the smooth ones included), nor after the frame pass, nor on the no-GPU
path.

### The newest frame held out of the model (2026-11-22)

The second cause, the model that pulls (bracket seed 1).

**The error.** Bracket seed 1, frames 100 to 200, each step measured before fusion against the
truth: the error lies along the frame's weakest translation (across it mostly under 0.15 mm).
Its mean is 0.007 mm and its sign alternates from frame to frame (lag-one correlation -0.3 to
-0.5): no shared bias and no random walk, each frame scatters around the model. It is 0.45 mm
rms over frames 120 to 170, where the translation is held at 6e-4 to 9e-4; on bracket seed 0
0.19 mm, held at 1.2e-3 to 1.7e-3. It is largest after the weak direction turns (frame 139)
and where the model's visible pixels fall from 246k to 189k (new surface coming in). Frame 152
lands 1.63 mm off and the path stays about 1.3 mm off for the next 15 frames: they take its
error.

**Which memory.** Frames 110 to 172, each registered from its prediction against a model made
again from its own frames (mm):

| model | rms | largest |
|---|---:|---:|
| the real one (all frames) | 0.44 | 1.63 |
| the last 5 / 10 / 30 / 60 tracked frames | 0.69 / 0.61 / 0.55 / 0.46 | 2.50 / 1.60 / 1.83 / 1.32 |
| all but the newest / the newest two | 0.38 / 0.42 | 1.01 / 1.08 |
| the last 30 / all, at true poses | 0.45 / 0.46 | 1.20 / 1.20 |

A longer memory is better, and even a model at true poses does not make the scatter smaller:
about 0.4 mm a frame is the frame's own depth noise along the weak direction. The model only
adds the single events; leaving the newest frame out helps most.

**The accelerometer, with tracked poses.** Gravity and bias fitted on the tracked path equal
the truth's (9871 against 9870 mm/s², bias within 1.5). A filter on it, corrected by the
tracked positions, predicts along the weak direction to 0.22 / 0.80 mm (median / 95th
percentile) against the registration's own 0.22 / 0.71. At frame 152 it says -0.41 where the
registration found +1.27, then follows the plateau. Fits over a sliding window are worse than
constant velocity. Not built.

**Built (off): `FusionParams.fuse_lag`** (frames; 0 is off) and `fuse_lag_detail_only` (True).
A tracked frame is fused into the tracking model only once the next frames have registered;
its pose is what the registration found, and the mesh's model and the path are as tracked. Held
frames are fused when the model is read. Both tracking models; in detail mode only, so a smooth
part is tracked exactly as before. Two frames were worse than one (bracket seed 1 3.11 mm).

**Does it hold, however the scan starts?** Frame pass, path max (mm), `slip_check_mm=3`, the
start registered together:

| start | hole plate, lag off | lag 1 | bracket seed 1, lag off | lag 1 |
|---|---:|---:|---:|---:|
| off | 8.00 (frame 1; 2.67 after) | 8.00 (frame 1; 2.74 after) | 2.40 | 2.12 |
| 6 frames | 2.71 | 3.21 | 1.90 | 2.30 |
| 8 | 3.09 | 2.47 | 3.30 | 1.77 |
| 10 | 2.75 | 2.36 | 2.55 | 2.27 |
| 12 | 2.32 | 2.95 | 2.61 | 2.02 |
| 14 | 2.64 | 2.57 | 1.81 | 2.02 |

Both of X4c's targets hold at every start: the bracket under 2.40 mm (1.77 to 2.30, from 1.81
to 3.30), the hole plate under 6 mm after the start.

**The small set**, what Make mesh runs, tracked; last frame / path max / path median (mm). "All
three": the start of 10 frames registered together, the slip check at 3 mm, the lag of 1.

| recording | today | the start together alone | all three | lag and slip check, no start |
|---|---|---|---|---|
| Speckled bracket 1 | 1.125 / 1.62 / 1.02 | 0.570 / 1.28 / 0.73 | 1.456 / 1.90 / 1.32 | 0.908 / 1.70 / 0.85 |
| Speckled bracket 2 | 0.349 / 1.01 / 0.555 | 0.437 / 1.04 / 0.467 | 0.749 / 1.08 / 0.559 | 0.457 / 0.92 / 0.480 |
| Bracket seed 0 | 0.417 / 1.15 / 0.489 | 0.343 / 1.20 / 0.413 | 0.219 / 1.07 / 0.294 | 0.434 / 1.16 / 0.485 |
| Bracket seed 1 | 0.597 / 2.40 / 0.674 | 0.778 / 2.55 / 0.663 | 0.517 / 2.27 / 0.474 | 0.513 / 2.12 / 0.617 |
| Bracket seed 2 | 0.554 / 1.47 / 0.699 | 0.700 / 1.46 / 0.726 | 0.623 / 1.54 / 0.665 | 0.527 / 1.42 / 0.628 |
| Bracket seed 3 | 0.641 / 1.64 / 0.757 | 0.540 / 1.34 / 0.611 | 0.441 / 1.42 / 0.659 | 0.556 / 1.82 / 0.724 |
| Bracket seed 4 | 0.768 / 2.02 / 0.763 | 0.389 / 1.58 / 0.461 | 0.695 / 1.45 / 0.647 | 0.780 / 1.56 / 0.745 |
| Dark bracket 1 | 3.786 / 6.29 / 3.88 | 0.673 / 1.96 / 0.802 | 0.737 / 1.76 / 0.792 | 3.191 / 7.18 / 3.27 |
| Dark bracket 2 | 0.696 / 2.28 / 1.03 | 0.483 / 1.85 / 0.570 | 0.438 / 1.03 / 0.290 | 2.050 / 5.61 / 2.27 |
| Hard 0.5 mm plate 1 | 1.379 / 2.56 / 1.53 | | 0.789 / 2.21 / 0.900 | 1.849 / 3.17 / 1.66 |
| Hard 0.5 mm plate 2 | 0.471 / 2.58 / 0.852 | | 0.577 / 2.52 / 0.839 | 0.674 / 2.41 / 0.906 |
| Hole plate 120 mm up, seed 0 | 2.10 / 16.3 / 1.80 | 2.14 / 19.1 / 2.01 | 2.32 / 3.08 / 1.68 | 2.61 / 8.00 / 1.94 |
| Hole plate 120 mm up, seed 1 | 1.07 / 7.09 / 1.44 | | 1.16 / 4.15 / 1.39 | 0.98 / 5.78 / 1.39 |
| Hole plate 120 mm up, seed 2 | 2.13 / 3.89 / 1.87 | | 3.53 / 4.63 / 3.01 | 2.22 / 3.89 / 2.14 |
| Hole plate 10 mm up, seed 1 | 0.723 / 2.26 / 1.01 | 0.692 / 2.30 / 1.03 | 0.957 / 2.46 / 1.12 | 1.161 / 2.21 / 1.24 |
| Standing walls, seed 0 | 1.067 / 1.21 / 0.531 | 0.386 / 0.99 / 0.332 | 0.656 / 0.83 / 0.384 | 0.532 / 1.21 / 0.443 |
| Sheet, seed 0 | 0.866 / 2.38 / 1.10 | | 0.489 / 3.33 / 0.687 | 0.956 / 2.71 / 0.990 |
| End-to-end sphere, a 200-frame car panel | | identical | identical | identical |

Readings with all three on:
- **Hole plate 120 mm up:** seed 0's Ø 25 -6.61 → -3.35 and its centre 3.46 → 1.14, its bore
  -0.62 → -0.95 with one answer fewer; seed 1's bore -0.47 → -0.59; seed 2's bore centre 0.05 →
  0.21.
- **Hole plate 10 mm up, seed 1:** Ø 25 -0.20 → +0.39, Ø 10 -0.07 → -0.19.
- **Speckled brackets:** the first's right boss 2.22 → 3.59 and left boss -0.29 → -0.38; the
  second's left boss -0.32 → +0.13.
- **Brackets:** part medians 0.005 to 0.009 mm better; clicks within 0.03 mm, but seed 4's shaft
  -0.068 → -0.109.
- **Thin: nothing moved away from the truth.** Walls 0.5 / 1 / 2 mm (too thick by) 1.229 /
  0.808 / 0.660 → 1.132 / 0.787 / 0.588 and the 0.5 mm wall's face covered 0.483 → 0.523; the
  sheet's flange / 1 mm rib / 3 mm rib 0.604 / 0.969 / 0.535 → 0.557 / 0.956 / 0.520; the
  0.5 mm plates' thickness -0.010 / -0.005 → -0.008 / -0.004. (Lag and slip check without the
  start: a hard plate 0.003 mm thinner.)

**The compare on the small set, all three on: worse on** the hole plates 120 mm up (small
features, seed 2's last frame and median), the hole plate 10 mm up (Ø 10, Ø 25), the dark
brackets (hairline rows of the part's far points and the loops) and the speckled brackets (the
bosses, and their last frame against today: 1.125 / 0.349 → 1.456 / 0.749); the brackets the
same, the hard plates better, the sphere and panel identical. The lag and the slip check
without the start are clearly worse (dark bracket 2's last frame 0.70 → 2.05).

**Time:** the lag costs nothing measurable (bracket seed 1's frame pass 43.3 s without, 43.2 s
with); the start 2.6 to 3.9 s once.

**Verdict: not adopted, no full compare spent.** The three settings mend what was measured:
the first frame's error, the frame that slides into another fit, the young model that takes
its newest frame's error. Bracket seed 1 and the hole plate hold at every start, the dark
brackets and the hard plates are much better, nothing thin moves away from the truth. What
remains is the frame's own scatter along the weakly held direction (0.2 to 0.45 mm rms a
frame): every change rolls again where it adds up, and other detail-mode recordings move both
ways. That fails "no scene worse" (E-D1). A cure needs something that holds that direction: the
laser's points in the registration, or an accelerometer better than a filter on the tracked
path.

**With the settings off** the quick set on what Make mesh runs compares identical (GPU, fresh
runs), and the tracking and pipeline tests pass on both paths.

Limits: simulated; one run a recording; the start sweep on the frame pass of two recordings;
the full set was not run; each setting's share in each worse row is not separated (the slip
check alone and the lag alone on top of the start were not run); on the no-GPU path only the
tests ran with the lag.

## Refine from the images inside Make mesh, and a driver timeout (ENGINE_PLAN X6, 2026-11-22): not adopted; the timeout mended

X6 asked whether "Refine from the images" (the mesh edit) should run as part of Make mesh. It
is built as a setting that is off, with a guard; it misses the stop rule on time. On the way
its search on the graphics card turned out to run so long in one piece that the Radeon driver
timed out and the PC reset; that is mended for the mesh edit too.

**Setup.** `[process] refine_from_images` (off). What Make mesh runs, on the GPU (Radeon 680M),
against `tools/scorecard_baselines/2026-11-21-gpu-core-app-frames.json`; seven recordings, one
seed each (the end-to-end sphere and panel, a 200-frame car panel, bracket seed 0, the
standing walls, a 0.5 mm plate, the hole plate 10 mm up), tracked and true poses. First
unguarded (the mesh edit as it is), then with Make mesh's guard.

**Unguarded** it helps smooth surfaces and hurts edges, rims and a thin wall:
- sphere, part median / p95: 0.060 / 0.248 → 0.016 / 0.121 mm (radius error -0.089 → -0.042);
  end-to-end panel 0.038 → 0.014; car panel 0.021 → 0.013; 0.5 mm plate 0.033 → 0.017;
- bracket: its edges (where the normals turn by more than 30° within 2 mm) 0.15 → 0.24 mm, p95
  +0.15 to +0.17 mm, the surface within 1 mm 3 % less, the upright's height +0.13 to +0.15 mm,
  one boss answer lost;
- hole plate: the Ø 10 hole +0.19 to +0.24 mm, the bore lost (tracked: -0.52 mm and one answer
  fewer);
- walls: the median 0.01 mm worse, and **the 1 mm wall reads 0.15 mm thinner at true poses**.
- Moves above 0.2 mm were mostly wrong.

**The guard** (`SpacetimeParams.guard_smooth_deg / guard_edge_mm / guard_thin_mm /
guard_move_mm`, all off by default, so the mesh edit is unchanged; Make mesh uses 30°, 3 mm,
2 mm, 0.3 mm): a patch is used only where the mesh within its fit turns by at most 30°, no free
edge lies within 3 mm and no other side of a wall faces back within 2 mm; a patch whose
measured surface is more than 0.3 mm from the fused one is left out, and no point moves more
than 0.3 mm. The report says what was used and what was left, by reason. Measured limit on thin
parts (two-sided plates): walls of 0.5, 1.0 and 1.9 mm are left as they are, a 3 mm wall is
refined; a 0.5 mm plate whose images would pull its top 0.2 mm in keeps its thickness exactly.

| | today | unguarded | guarded |
|---|---|---|---|
| Sphere, median / p95, tracked | 0.0595 / 0.248 | 0.0160 / 0.121 | 0.0186 / 0.206 |
| Sphere, true poses | 0.0553 / 0.242 | 0.0138 / 0.101 | 0.0172 / 0.191 |
| Sphere, radius error, tracked | -0.089 | -0.042 | -0.071 |
| End-to-end panel, median, tracked / true | 0.038 / 0.039 | 0.014 / 0.014 | 0.013 / 0.013 |
| Car panel, median, tracked / true | 0.021 / 0.022 | 0.013 / 0.012 | 0.012 / 0.011 |
| Bracket, median, tracked / true | 0.117 / 0.062 | 0.094 / 0.052 | 0.086 / 0.035 |
| ... its p95 | | +0.17 / +0.15 | +0.001 / +0.000 |
| ... the upright's thickness, tracked / true | -0.295 / -0.059 | | -0.188 / -0.022 |
| Walls, median, tracked / true | 0.125 / 0.065 | +0.011 / +0.015 | 0.109 / 0.054 |
| ... the 1 mm wall, true poses | | -0.154 (thinner) | -0.001 |
| 0.5 mm plate, median, tracked / true | 0.033 / 0.027 | 0.017 / 0.015 | about the same |
| ... its thickness, true poses | -0.006 | +0.018 | +0.017 |
| Hole plate, Ø 10, tracked / true | | +0.24 / +0.19, bore lost | +0.008 / within 0.01 |

The guard removes every loss of the unguarded refine and keeps the median gains; it costs a
third to two thirds of the sphere's gain. One thin reading still moves: the 0.5 mm plate at
true poses reads 0.023 mm thicker (tracked +0.001).

**Time: the stop rule's miss.** With the refine Make mesh takes 41 to 196 % longer (sphere +105
to +200 %, car panel +45 to +110 %, bracket +49 to +66 %, hole plate +46 to +130 %), against
the 30 % allowed. The stage takes 11 to 50 s, mostly on the processor: placing the patches 7 to
10 s, finding the frames that see them 11 to 19 s, the search on the GPU 4 to 8 s, the guard
about 2 s.

**Verdict: not adopted; no full compare spent.** Beyond the time, only one seed a scene was
measured, the guard's limits were chosen on three of these same recordings, and the plate's
thickness moves at true poses. The setting stays, off. The way back would be faster patch
placing and ray casts (same results), then the other seeds.

### A driver timeout in Refine from the images, mended

**What happened.** The search ran each of its levels as one launch on the graphics card: up to
about 3 s on the hole plate (1.27 million work items, each sampling 15 x 15 points in two
images for 16 to 40 views). Windows gives a graphics driver 2 s. On 2026-11-22 the Radeon
driver (AMD Software 26.11.2, installed the day before) timed out during these runs; four
times the PC then switched off hard (Kernel-Power 41, no crash record), once Windows recovered
(LiveKernelEvent 141 at 15:18:21) and the search **silently returned nonsense**: of 127,497
patches none was kept (74,592 "edge": the cost's minimum at the end of the range; the same
recording normally keeps about 40,600), so the stage only said it skipped and the mesh stayed
as fused. Holding the job to half the processor's cores did not prevent it.

**The fix (on, no setting; the mesh edit too).** The texture test and the cost run in short
launches sized by their work (samples taken), each followed by a wait for the card; the size
follows the card's measured pace towards 0.12 s a launch, under a hard cap per kernel (the
texture test is about 16 times slower per sample than the cost: 2.5e7 and 4e8 samples). The
first cost launch is kept and run again at the end of the search; if its answer has changed,
the stage stops with "The graphics driver seems to have been reset during Refine from the
images (its results changed when checked), so the mesh was left unchanged..." and writes
nothing (the mesh edit answers 503; Make mesh marks the stage failed and carries on). The
report gets `launches` (count, longest, total).

**How it was checked.**
- The batched kernels equal the single launch bit for bit (a small synthetic scene, three
  seeds), and two forced batch sizes give equal meshes at a scene's scale.
- Car panel, walls (true poses) and the hole plate (true poses) searched as before the
  timeout: 45 / 62 / 74 launches, the longest 0.216 / 0.140 / 0.197 s; the hole plate keeps
  40,601 patches again, none "edge". No new timeout record and no reset during these runs, the
  tests on both paths, or the fresh quick-set run.
- The search takes about the same time as before (0.1 s less to 1 s more on a 30 to 50 s
  stage).

**With the settings off** the quick set on what Make mesh runs compares identical (87
readings; GPU, fresh runs), and the mesh edit's own tests pass unchanged on both paths.

Limits: simulated; one seed a scene; the self-check was not tried against a real driver reset
and reruns only the first launch (a lost device normally makes the GPU library raise, which
fails the stage anyway); runs since the fix were held to 8 of 16 cores, and against the earlier
runs a fifth to two fifths of the vertices differ by one float32 step (up to 1.5e-5 mm; the
patch counts are identical; the cause is on the processor side, not confirmed); timings since
the fix are throttled ones; the other GPU kernels (the matcher, the fusion) were read, not
measured: none loops over many frames in one launch, the whole-volume ones are those to watch.

## The thin-parts hint (ENGINE_PLAN X5 step 1, 2026-11-22)

After Make mesh the app looks at the finished mesh for what a finer mesh detail could help, and
suggests the preset "Thin sheet metal and small parts" where a gain was measured. It only
reads: no setting changes by itself and no result changes.

**What it looks for** (`ohs/mesh/thin_parts.py`; every limit is a multiple of the mesh detail v
the mesh was made at, 1 mm today):

- **A thin wall seen from both sides:** a ray from a vertex into the part meets the far side,
  facing back, within 2.7 v, over at least 100 v² of surface. Where 2.7 comes from: at 1 mm
  walls 0.5 to 2 mm thick read 1.66 to 2.66 mm (the standing walls 1.68 to 2.66, the sheet's
  1 mm rib 1.66 to 2.21, its 2 mm flange 2.25 to 2.61), and the sheet's 3 mm rib reads 3.03 to
  3.72; walls of 2 mm or less are where 0.5 mm was measured to help ("Presets: which settings
  matter"). Where 100 v² comes from: parts without a thin wall had at most 50 mm² of such
  readings; the speckled brackets 228 and 240 mm², in pieces each under 100.
- **Its thickness:** the median reading with its 10th to 90th percentile, and at 1 mm with the
  thin-wall rule on "probably reading - 1.3 to reading - 0.1 mm in truth" (walls of 0.5 to 2 mm
  read 0.1 to 1.3 mm too thick). At another mesh detail no estimate is given: none was measured.
- **A thin plate lying flat:** what Remove the table leaves is a plateau: 99 % of its area at
  most 2.7 v above the table, its top at least half of what is left and at least 400 v².
- **A small round hole or bore whose wall was seen:** wall points facing back within 3 to 16 v
  that lie round one axis on at least 60 % of a circle; a slot or a seam is rejected.
- A mesh made with the thin preset, or at 0.5 mm or finer, is not looked at.

**The rule: the preset is suggested only for thin walls**, the one case where 0.5 mm was
measured to help (walls 0.23 to 0.67 mm nearer the truth on all three recordings). A thin plate
or small holes alone are recorded in the scan's `report.json` and not suggested: a plate lying
flat reads its thickness within 0.01 mm at every mesh detail, and small holes were not shown
nearer the truth at 0.5 mm beyond the spread between scans. When walls are found together with
a plate or holes, the hint names those too and says no gain was measured for them.

**The hint's words** (the first sheet recording): "Thin walls found". "Thin walls: 7,013 mm² of
the surface (1.3 %) lies on walls seen from both sides that read 2.49 mm thick (most of it 2 to
2.64 mm), probably 1.19 to 2.39 mm in truth." "Walls seen from both sides read 0.23 to 0.67 mm
nearer their true thickness at mesh detail 0.5 mm: simulated walls 0.5 to 2 mm thick read 1 to
2.4 mm instead of 1.7 to 2.7 mm." The cost: 1.2 to 2.4 times the time (with this mesh's own
time), about 4 times the triangles, and the memory 0.5 mm scans took (7 to 11.9 GB, 2.2 to
3 GB of the graphics card's shared memory; the larger figures from "Launch times on the
graphics card at 0.5 mm"), "measured on simulated scans, on a mini PC with built-in graphics
and 20 GB of memory". One button, "Make the mesh again with this preset" (it sets the
scan's preset and runs Make mesh; the mesh you have stays until the new one is ready), and
"Not for this scan", which puts the hint away for good.

**On the scorecard's 36 recordings** (what Make mesh runs, tracked, the cached results; at true
poses every answer is the same):

| recordings | suggested | what is found |
|---|---|---|
| Standing walls, seeds 0 to 2 | yes | walls 1,794 / 3,679 / 4,109 mm² in 3 regions, reading 1.84 / 1.85 / 2.02 mm |
| Sheet, seeds 0 to 2 | yes | walls 7,013 / 8,082 / 7,179 mm² (flange and 1 mm rib as one region; the 3 mm rib is not flagged), reading 2.49 / 2.16 / 2.52 mm |
| 0.5 mm plates (3) and the hard 0.5 mm plates (2) | no: no measured gain | a plate 0.487 to 0.493 mm thick (truth 0.5), 72 to 80 mm across |
| Hole plates 10 mm and 120 mm up (6) | no: no measured gain | holes or bores Ø 7.8 to 9.2 mm (the Ø 10 bore, and the Ø 10 hole where its wall shows) |
| Sphere, panel, car panels (5), seams (3) | no | at most 13 mm² of wall readings, no hole walls |
| Brackets (5), dark (2) and speckled (2) brackets | no | no thin wall (the speckled ones 228 / 240 mm² of readings in pieces under 100 mm²); no round cluster |

**Measured limits** (synthetic, in the tests): walls of 0.5, 1, 2 and 2.6 mm are found and read
within 0.05 mm, a 2.8 mm wall is not (at mesh detail 2 mm a 2.8 mm wall is thin, with no
estimate); plates of 0.5 and 0.8 mm on a noisy table and a 2 mm plateau are found, their height
within 0.1 to 0.15 mm, a 4 mm plateau is not; a Ø 10 bore is found, a Ø 25 hole and a slot are
not.

**With it in** the quick set on what Make mesh runs compares identical to the baseline, and
the finder leaves the result mesh byte for byte as it was. The finding takes 2 to 8 s on the
processor at the end of Make mesh (0.3 to 1.5 million triangles); a finder that fails is logged
and costs the scan nothing.

**In the app on the real engine** (a standing-walls recording replayed into a scan, General preset,
Make mesh at Standard, on the GPU): with the whole recording (117 of 120 frames) the result page shows
"Thin walls found" at the top of the processing report (1,584 mm² of walls reading 1.84 mm, 3 regions,
82 bricks); "Not for this scan" puts it away and it stays away after a reload. With only the second
half of the same recording (63 frames) nothing is found (83 mm² of wall readings): the walls were not
seen from both sides, so there is no thin wall in that mesh to measure.

**The button, pressed later the same day** (after the graphics card's launches were bounded,
"Launch times on the graphics card at 0.5 mm"): on a fresh scan of the whole recording "Make
the mesh again with this preset" set the scan's preset to "Thin sheet metal and small parts"
and made the mesh at 0.5 mm: 3,776,921 triangles (0.9 million before), the hint gone
afterwards (`why_not: "made_fine"`), no driver timeout; the server's memory peaked at about
5.9 GB, sampled every 3 s.

Limits: simulated; bosses are not looked for (the bracket's Ø 14 bosses read right with true
depth, so their error is the depth's, and 0.5 mm did not help the bracket beyond its spread);
holes the cameras filled in are not found (over a far floor the Ø 6 and Ø 10 holes are 0.5 to
0.8 mm dents); the thickness estimate only at 1 mm with the thin-wall rule on; without a
graphics card walls read 0.3 to 0.75 mm thicker, so a 2 mm wall there may not be found; the
sheet's flange and rib merge into one region, so its reading mixes a 1 mm and a 2 mm wall; the
gain the hint quotes is P1's measurement.

## Launch times on the graphics card at 0.5 mm (ENGINE_PLAN X5 step 2's groundwork, 2026-11-22)

Windows resets a display driver whose command runs over 2 s, and on the mini PC the Radeon 680M
also draws the desktop ("A driver timeout in Refine from the images, mended"). Before the first
run at mesh detail 0.5 mm since the driver was reinstalled (AMD Software 26.11.2), every launch
of the matcher, the filters and the fusion was listed, recorded and, where its work is not one
frame's, bounded.

**The record** (`ohs.compute.launches`; off by default, then nothing changes): with
`OHS_GPU_TIMES=1` (or a file's path, which gets a JSON at the end) every launch's time is taken
from the card's own events, so nothing waits because of it; a launch over 0.25 s is warned of.
With the record on Make mesh's frame pass took +0 % (standing walls) and +2.3 % (bracket).

**At 1 mm** (Make mesh's frame pass; standing walls seed 0 / bracket seed 0), the longest
launches: the matcher's two aggregation passes 39 to 45 ms and its census 28 to 34 ms; the mesh
model's fusion 22 to 25 ms (at most 7,523 bricks a frame); the tracking model's fusion 18 ms
and ray cast 15 ms; everything else under 15 ms.

**What grows at 0.5 mm is the mesh model only.** The tracking model's voxel is the larger of
2 mm and the mesh detail, in a cube of at most 384 voxels a side, so its whole-volume launches
(the reset 7 ms; weight scaling and brick flags do not run in Make mesh) stay as they are. The
mesh model's fusion takes about 4 times the bricks a frame, and its pool's fill and copy (when
it grows) and its download grow with all the bricks.

**Bounded** (`ohs/track/recon_tsdf.py`): a frame's fusion is one launch as before unless it has
more bricks than the card's measured pace does in 0.1 s (16,384 before a pace is known, never
more than 32,768); then it goes in batches, each waited for. Each voxel is one work item
written once a frame, so the model is the same bit for bit. The pool's fill, copy and download
go in pieces of at most 64 MB. Without the bounds the largest pool the app allows on this card
(187,088 bricks, 365 MB a channel, mesh detail down to 0.25 mm) would have had a download of
about 220 ms in one command and a possible fusion launch of about 0.5 s; with them about 85 ms
and 40 ms at most (an estimate; that case was not run).

**A driver reset part way fails the job** instead of writing a mesh: each frame checks that the
previous fusion launch ended without an error, and before the mesh is read back a small buffer
filled with a pattern when the model was made must still hold it (a reset loses the card's
memory). The stage then fails in the server's usual words. Not tried against a real reset.

**Climbed in steps at 0.5 mm**, each its own run, the record of driver timeouts checked before
and after:

| step | longest launches (ms) | Make mesh | peak memory / the card's shared memory |
|---|---|---|---|
| synthetic, 12 frames | fusion 11.7 (1 mm: 7.6) | 0.73 s (0.36 s) | the pool 512 MB (128 MB) |
| standing walls, first 20 frames | census 48, fusion 40, download 24, ray cast 22 | 16.3 s | not sampled |
| standing walls, what Make mesh runs | fusion 70 (27,902 bricks), census 63, aggregation 57 | 76.8 s (1 mm: 40.5) | 9.88 GB (1.24) / 2.61 GB (0.05) |
| bracket, 240 frames | fusion 65, census 62, aggregation 48, download 38 | 133.4 s (1 mm: 80.0) | 11.92 GB (1.78) / 3.01 GB (0.08) |

No launch over 0.25 s, no error status, no driver timeout. **The memory is the finding:** 9.9
and 11.9 GB of the PC's 19.8 GB, mostly the finer mesh (6.4 against 1.55 million triangles on
the bracket) and its processing on the processor, not the card. The time grows in Follow the
camera (walls 46 s against 24.5 s) and in Refine with lasers (14 s against 1.4 s; bracket 25.6
against 2.4 s), which grows with the mesh. The thin-parts hint's memory line now gives 7 to
11.9 GB.

**At 1 mm nothing changed:** with the record off Make mesh's frame pass takes the same time
(walls 23.89 / 23.72 s against 23.71 / 23.89 s before; bracket 44.72 / 44.65 against 44.93 /
44.84 s), the meshes are identical as sets of vertices and triangles and the paths identical
(the vertex order differs from run to run on the old code too), and the quick set on what Make
mesh runs compares identical.

Limits: one card (the pace adapts, only the 680M was measured); the largest pool is an
estimate; the 1 mm times beside the 0.5 mm ones are the cached runs of an older engine; the
self-check was not tried against a real reset; the tests of the batching were seen failing only
in that their module did not exist before.

## Fine mesh detail on thin walls only (ENGINE_PLAN X5 step 2, 2026-11-22): built, off, not adopted

The whole scan at mesh detail 0.5 mm reads thin walls 0.23 to 0.67 mm nearer the truth, for
about twice the time and 10 to 12 GB of memory ("Launch times on the graphics card at 0.5 mm").
Step 2 makes only the thin walls finer. It is a setting, `ProcessParams.fine_walls`, off; the
scorecard runs it with `--set-param process.fine_walls=true --chain app`. Not adopted as a
default; in the app it is a choice per scan ("Make only the thin walls finer" in the app,
below).

**How it works** (`ohs/mesh/fine_walls.py`, `ohs/app/fine_walls.py`):

- **The depth is kept.** Follow the camera writes each fused frame's depth and its sigma to
  disk as it fuses them (about 7.8 MB a frame at 1280 x 800: 0.94 GB for 120 frames, 1.9 GB
  for 240; removed after the step; not capped). The matcher does not run again. On the
  graphics card's path only; without one nothing is kept and the report says so.
- **The regions are known after the 1 mm mesh:** the thin-parts hint's own wall finding
  (`find_thin_walls`, walls only) on the base mesh after Straighten; the flagged bricks of
  8 mm grown by one brick all round (97 to 168 flagged, 448 to 640 with the growth).
- **A second model at 0.5 mm, in those bricks only** (`ConfidenceTsdf.limit_bricks`), fed the
  kept depth at the base stage's poses. In its bricks it holds what the whole scan at 0.5 mm
  holds, bit for bit (a test compares every brick).
- **The stitch moves the 1 mm mesh; no second mesh is cut in.** The triangles in the region
  are split in four and their neighbours in two or three, so every split edge is split on
  both sides. Each vertex then moves along its normal, at most 1 mm, onto the finer model's
  nearest crossing from inside to outside in the direction it faces.
- **Nothing thin can be lost by it:** a wall's other face crosses the other way, so no vertex
  can jump onto it and no wall can close; a vertex without such a crossing keeps its 1 mm
  place; a move that would turn a triangle over is undone; no surface is removed. Tested on
  walls of 0.5, 1 and 2 mm, a 0.6 mm wall read from just inside its back face, and a 0.5 mm
  plate lying flat.
- A voxel of the finer model counts from 1 observation (marching cubes takes more than 3):
  with "more than 3" a third of the walls' vertices found no place (walls 0.97 / 0.60 / 0.61
  mm too thick against 0.77 / 0.52 / 0.47). A longer truncation (4 mm) was worse
  (0.90 / 0.58 / 0.50).
- It sits after Straighten and before Refine from the images and Refine with lasers.

**Standing walls** (error of the 0.5 / 1 / 2 mm wall's thickness in mm, + is too thick; what
Make mesh runs; the whole scan at 0.5 mm was run on seed 0 only):

| recording | 1 mm | step 2 | all 0.5 mm | Make mesh, 1 mm → step 2 (all 0.5 mm) |
|---|---|---|---|---|
| seed 0, tracked | 1.229 / 0.808 / 0.660 | 0.768 / 0.516 / 0.466 | 0.484 / 0.457 / 0.427 | 40.5 → 47.9 s (76.8) |
| seed 0, true poses | 0.786 / 0.534 / 0.354 | 0.530 / 0.248 / 0.010 | 0.293 / 0.032 / -0.094 | 22.6 → 26.7 s (49.9) |
| seed 1, tracked | 1.182 / 0.701 / 0.579 | 0.661 / 0.339 / 0.280 | | 42.3 → 49.7 s |
| seed 1, true poses | 0.835 / 0.400 / 0.272 | 0.537 / 0.110 / -0.064 | | 21.7 → 28.1 s |
| seed 2, tracked | 1.256 / 0.911 / 0.535 | 0.754 / 0.531 / 0.178 | | 42.5 → 49.5 s |
| seed 2, true poses | 0.758 / 0.621 / 0.156 | 0.542 / 0.290 / -0.112 | | 22.2 → 27.8 s |

**Sheet part** (flange 2 mm / rib 1 mm / rib 3 mm):

| recording | 1 mm | step 2 | all 0.5 mm | Make mesh, 1 mm → step 2 (all 0.5 mm) |
|---|---|---|---|---|
| seed 0, tracked | 0.604 / 0.969 / 0.535 | 0.377 / 0.631 / 0.367 | 0.349 / 0.617 / 0.455 | 76.1 → 89.7 s (134.9) |
| seed 0, true poses | 0.180 / 0.270 / 0.030 | 0.006 / 0.047 / -0.089 | 0.037 / 0.039 / -0.238 | 38.6 → 47.9 s (84.9) |
| seed 1, tracked | 0.254 / 0.658 / 0.171 | 0.004 / 0.343 / 0.120 | | 79.0 → 91.7 s |
| seed 1, true poses | 0.158 / 0.244 / 0.043 | -0.005 / -0.013 / -0.069 | | 40.4 → 48.6 s |
| seed 2, tracked | 0.596 / 1.211 / 0.719 | 0.349 / 0.914 / 0.699 | | 76.6 → 91.6 s |
| seed 2, true poses | 0.123 / 0.231 / 0.035 | -0.080 / -0.013 / -0.048 | | 38.6 → 48.3 s |

- **Memory:** 1.14 to 1.76 GB, the same as the 1 mm run on every recording (all 0.5 mm: 5.9
  to 11.7 GB). The card's shared memory 0.04 to 0.09 GB (all 0.5 mm: 2.5 to 2.9 GB).
- **The mesh:** 2.5 to 3.1 % of the surface is made finer, 7 to 11 % more triangles. Open
  loops the same on every run (1,248 and 1,248 on seed 0 tracked; the whole scan at 0.5 mm
  has 11,728).
- **The seam** (standing walls seed 0 tracked, sheet seed 0 true poses, sheet seed 2
  tracked; counted a second time by a separate script on the saved meshes): no edge with
  more than two triangles, no triangle doubled or turned against its neighbour, the same
  number of loops (1,248, 691, 1,573). Flat triangles only where the 1 mm mesh had them (the
  sheet at true poses has 5, two of them in the region and split in four: 11 after). Where
  the moved surface meets the one left alone the step is 0.05 to 0.09 mm at the 95th
  percentile, 0.17 to 0.41 mm at most. The moved vertices lie on the all-0.5 mm surface
  (median 0.000, 95th percentile 0.023 mm).
- **The part's median error:** tracked, better on all six (walls 0.125 → 0.097, 0.151 →
  0.107, 0.145 → 0.095; sheet 0.251 → 0.162, 0.105 → 0.068, 0.255 → 0.155). At true poses
  the same on four (0.065 → 0.066, 0.058 → 0.059 twice, 0.073 → 0.058) and worse on the
  standing walls seeds 1 and 2 (0.056 → 0.074, 0.064 → 0.074).
- **Recordings without a thin wall** (0.5 mm plate, hole plate, bracket, sphere, car panel;
  tracked and true poses): the mesh is the same in every vertex and triangle. The cost is the
  finding (0.5 to 1.6 s) and keeping the depth: bracket 80.0 → 81.1 s, panel 68.6 → 71.1 s,
  sphere 13.3 → 14.1 s, hole plate 72.3 → 77.1 s (timing scatter; its finding takes 1.0 s).
- **The step's own time:** 5.8 to 6.8 s on the standing walls and 10.0 to 10.9 s on the sheet
  (finding 0.9 to 1.5 s, fusing the kept depth 2.7 to 6.0 s at about 23 ms a frame, the
  stitch 1.9 to 3.1 s).
- **On the graphics card:** the longest launch in 29 runs 64 ms (the matcher's census), no
  driver timeout.

**Against X5's stop rule:**

| the rule | result |
|---|---|
| walls within 0.05 mm of the all-0.5 mm result | **missed on the standing walls**: seed 0 tracked 0.28 / 0.06 / 0.04 mm away, true poses 0.24 / 0.22 / 0.10. The sheet is within 0.03 (tracked) and 0.03 / 0.01 (true poses; its 3 mm rib is nearer the truth than all 0.5 mm) |
| the hole within its range | no hole is flagged: the hole plate's mesh is the 1 mm mesh |
| time at most 1.25 times | tracked 1.16 to 1.20 times: met. True poses 1.18 to 1.29 times: missed on standing walls seed 1 (1.29), at the limit on seed 2 and sheet seed 2 (1.25) |
| memory at most 1.3 times | met (1.00) |
| open loops at most 1.2 times | met (1.00) |
| smooth scenes within their range | identical |

**Not adopted, and the full compare was not spent.** By E-D1 it also misses on its own
against the 1 mm mesh: the part's median error is worse on two true-pose runs, and the sheet's
3 mm rib, which sits in the grown region without being a thin wall, goes from 0.03 to 0.04 mm
too thick to 0.05 to 0.09 mm too thin at true poses (tracked it is nearer on all three).

**What it does give:** against the 1 mm mesh every wall of 0.5 to 2 mm reads nearer the truth
on all twelve runs (the 0.5 mm wall by 0.22 to 0.52 mm, the 1 mm wall by 0.29 to 0.38, the
2 mm wall by 0.04 to 0.36, the sheet's flange by 0.04 to 0.25 and its 1 mm rib by 0.22 to
0.34), for 16 to 29 % more time on parts with thin walls, no more memory and no new open
loops. Of the whole-scan 0.5 mm gain on seed 0 that is 62 / 83 / 83 % tracked and 52 / 57 %
on the 0.5 and 1 mm walls at true poses.

**Why the walls fall short of all 0.5 mm:** where the all-0.5 mm mesh has surface, step 2
reads the same as it (0.586 against 0.583 mm, 0.461 against 0.452, under one placement of the
mesh on the truth). The rest is each wall's less-seen side: there the 0.5 mm voxels have 1 to
3 observations and the all-0.5 mm mesh has holes (hence its 11,728 loops); step 2 keeps the
1 mm surface there, which reads thicker. The placement alone moves the 0.5 mm wall's reading
by about 0.1 mm between runs.

**With the setting off** the quick set on what Make mesh runs compares identical to the
baseline, and a pass with the setting on writes the same mesh as one without (a test).

Limits: simulated; the whole scan at 0.5 mm was run on three recordings (standing walls seed 0
both ways, sheet seed 0 both ways), so the 0.05 mm rule is judged on those; the 1 mm times
beside are the cached runs of an older engine; the spread between two runs of one recording
was not measured, so "worse" on the two median errors is against one run each; the kept depth
is not capped in the engine (7.8 MB a frame on disk; the app checks the disk first); not in the server's Make mesh at the time of these runs, so not seen in the
app; without a graphics card the step does nothing; not run on the NVIDIA laptop.

**If taken up again:** the time is in fusing every kept frame (only the frames that see a
region are needed) and in writing the depth as 32-bit floats; the accuracy limit is the
walls' less-seen side, not the stitch.

## "Make only the thin walls finer" in the app (ENGINE_PLAN X5 step 2, 2026-11-22): a choice per scan

The owner's decision, with the numbers of "Fine mesh detail on thin walls only" in hand: the
step goes into the app as a second, lighter choice in the thin-parts hint, chosen per scan. It
is not a default: a scan nobody chose it for is made exactly as before.

**What the app does:**

- **The hint offers two choices** when thin walls are found: first "Make only the thin walls
  finer", then the preset "Thin sheet metal and small parts" as before, and "Not for this
  scan". The first makes the mesh again with the choice on for this scan
  (`POST .../process {quality, fine_walls: true}`); the scan's entry keeps the choice for
  every later Make mesh and recipe on everything. `[process] fine_walls` (off) is the default
  for scans without a choice of their own.
- **Its words** give the measured gain and cost: walls of 0.5 to 2 mm 0.04 to 0.52 mm nearer,
  about half to four fifths of what the whole scan at 0.5 mm gains; that each wall's
  less-seen side keeps its surface, so the walls still read thicker than with the preset;
  1.2 to 1.3 times this mesh's time, in seconds; no more memory; 7 to 11 % more triangles;
  the disk space while it runs, from this scan's frames.
- **It is offered and runs only** with the mesh model on the graphics card, at mesh detail
  exactly 1 mm (the one measured), not at Preview quality, and when the scan's drive has the
  kept depth's estimate (the frames Follow the camera will process times 8 bytes a pixel:
  7.8 MB a frame at 1280 x 800) and 2 GB more free. Otherwise Make mesh goes on without it and
  the page says why in one line, with the space needed when it is the disk. Decided before
  Follow the camera, so nothing is kept in vain.
- **In Make mesh** only Follow the camera keeps the depth; the step runs after Straighten, on
  the base stage's mesh; when it acted its mesh is the new base and Refine with lasers, the
  edits, the Check and the report work on it. It has its own part of the progress bar (0.15
  against the others' 1.0, only when it runs) and can be cancelled between two frames.
- **It never costs the scan:** a failure is logged, the stage says so, the mesh stays as
  fused. The kept depth is removed in every case (done, skipped, failed, cancelled), and what
  a run stopped by a power cut left is removed at the next Follow the camera.
- **After such a mesh** the hint does not suggest again (`why_not: "walls_made_finer"`) and
  gives no thickness estimate (it was measured on plain 1 mm meshes); in its place a note
  says what was done, with one action, "Make the mesh again without this" (`fine_walls:
  false`).

**In the real app** (the reviewer, in the browser, the server from a frozen copy; the first
standing-walls recording replayed into a scan, 116 frames, General preset, Standard, on the
Radeon 680M):

| | Make mesh | triangles before the edits | the walls read |
|---|---|---|---|
| the choice off | 46.2 s | 1,005,624 | 1.83 mm (most of it 1.28 to 2.46), "probably 0.5 to 1.7 mm in truth" |
| "Make only the thin walls finer" pressed | 55.9 s (1.21 times) | 1,089,638 | 1.54 mm (0.93 to 2.43), no estimate |
| "Make the mesh again without this" pressed | 46.3 s | as off | 1.83 mm (1.28 to 2.46), the estimate back |

- The hint before: both choices, the first with "This mesh took 46 s; with it about 55 s to
  60 s" and "about 0.9 GB of free space while the mesh is made (7.8 MB for each of the 116
  frames)".
- The step: 6.8 s (finding 0.8, fusing the kept frames 1.9, the stitch 1.9 s); 2.6 % of the
  surface on 3 walls; moves 0.36 mm at the 95th percentile, the seam 0.05 mm; 87 bricks
  flagged, 505 with the growth.
- The kept depth: 907 MB at its peak (sampled every 0.6 s), no `fused_depth` folder left
  after any of the three runs.
- After it: the hint gone, the note "The thin walls were made finer: 2.6 % of the surface on
  3 walls, at mesh detail 0.5 mm, in 7 s. The rest of the mesh is as Make mesh made it." with
  its button. After the button: the hint back with both choices, the stages and the plan as
  before, `fine_walls: false` in the scan's entry.
- No driver timeout in any run.
- The agent's own run of the same on its server: 45.4 → 56.1 → 45.6 s, the walls 1.84 → 1.52
  mm, 914 MB kept; and the sphere with the choice on: "No thin walls were found in the mesh,
  so it was left as it is", the base unchanged, 258 MB kept while it ran and removed.

**With the choice off nothing changed:** no pass keeps depth and the stages, the plan and
`made` are as before (tests); the quick set on what Make mesh runs, run afresh (`--force`),
compares identical to the baseline on 4 of 4 scenes.

**Found on the way:** two Make mesh of one scan at mesh detail 1 mm give the same vertices
but not always the same triangles, before this change too: Fill holes breaks ties by the
order of the vertices, which differs from run to run on the graphics card. With `[process]
repeatable_edits` on they are the same; the server tests of this item turn it on to compare
exactly.

Limits: simulated recordings; the gain and the cost in the hint's words are the engine
section's ("Fine mesh detail on thin walls only"), with its limits (the part's median error
worse on two true-pose runs, the sheet's 3 mm rib 0.05 to 0.09 mm too thin there); the disk
check is tested by putting a free-space reading in its place, not on a full drive; only
mesh detail 1 mm; the step's share of the progress bar is measured on two parts; not tried
with a real scanner or on the NVIDIA laptop; fusing only the kept frames that see a region
was not done (fusing is 2 to 3 s of the step on the standing walls).

## The merge on today's engine (ENGINE_PLAN X7, 2026-11-22): measured, not adopted, nothing built

"Aligning and merging two scans" (2026-11-15) found that a merge adds surface and not accuracy.
That was before the mesh model's weights by confidence and its rule for surfaces seen from
opposite sides, so the merge was measured again, and the plan's idea (register both scans'
frames onto the merged model) judged against it.

**Fixed before any merge was scored** (the study's notes): five pairs, the metrics, what "beats"
means, the stop rule.

- **The scans:** stretches of 90 frames (9 s, a sweep of 140 to 160 degrees) of the scorecard's
  400-degree orbits at 1280 x 800, the two scans of a pair from different seeds (another
  texture, hand path and noise), the part not moved between them. Each as Make mesh makes it
  (Follow the camera, Straighten, Refine with lasers), on the graphics card.
- **The merges** as the app makes them (`ohs.align.merge_scans` with the server's settings,
  same setup; the app's merge does not run Refine with lasers): at the pose the alignment
  accepts, or, where it is not sure and the app asks for points, at the pose from four points
  picked 1.5 mm off. Beside it the merge with the tracked scans at their true motion, and with
  both scans at their true poses (the ceiling).
- **Scored on the part only**, each mesh with the Default recipe and placed by the part alone:
  the median error, the share more than 0.3 mm off, and "covered": the share of the true
  surface either scan saw that the mesh holds within 0.5 mm. "Either scan" is that share for
  the two single meshes together (a place counts when either has it within 0.5 mm).
- **Beats** the better single scan (the one with the smaller share more than 0.3 mm off):
  median and share both no worse than it beyond the spread, one of them better beyond it. Two
  runs of the same job gave the same numbers to the last digit, so 0.003 mm and 0.5 points are floors, not a spread across seeds (one pair per case; corrected 2026-11-23).

| pair | the better single scan | the app's merge | covered: either scan / the merge | the merge at true poses |
|---|---|---|---|---|
| bracket, 97 degrees apart | 0.088 mm / 14.8 % | 0.085 mm / 14.0 %: beats | 0.933 / 0.920 | 0.075 mm / 13.5 %, covered 0.909 |
| bracket, 56 degrees apart | 0.096 mm / 13.9 % | 0.097 mm / 14.5 % | 0.942 / 0.929 | 0.082 mm / 12.8 %, 0.920 |
| bracket, opposite sides (171 degrees), from picked points | 0.098 mm / 15.7 % | 0.308 mm / 51.2 % | 0.944 / 0.880 | 0.062 mm / 10.5 %, 0.927 |
| car panel, 99 degrees apart, from picked points | 0.028 mm / 4.5 % | 0.022 mm / 8.1 % | 1.000 / 1.000 | 0.022 mm / 9.2 %, 1.000 |
| sheet part, 123 degrees apart, from picked points | 0.163 mm / 25.7 % | 0.371 mm / 57.9 % | 0.905 / 0.651 | 0.074 mm / 14.4 %, 0.886 |

**Against the stop rule** (the merge beats the better single scan on at least 4 of 5 pairs, and
its good surface is at least that of either scan together): it beats on 1 of 5 and reaches the
covered share on 1 of 5 (the car panel); no pair does both. **Not adopted, nothing built.**

**What explains it:**

- **A fused merge averages.** Of the good surface the merge loses against either scan, 94 to
  98 % lies where only one of the two scans had it within 0.5 mm: it is still there, 0.5 to
  1 mm off, pulled towards the other scan's poorer view. Nothing thin went missing.
- **Even true poses do not reach "either scan"** on 4 of 5 pairs (0.909 against 0.922, 0.920
  against 0.933, 0.927 against 0.944, 0.886 against 0.915). So nothing that only mends poses
  can pass the rule, which is why registering the frames onto the merged model was not built.
  ("Poses refined against the finished scan" adds that a scan cannot correct the slow error
  it shares with the model.)
- **The pose decides a pair that shares little.** Opposite sides of the bracket share 46 % of
  the part. From the picked points the alignment accepted a pose about 1 mm from the true
  motion, and the merge is ruined (the upright reads 0.8 mm too thick, half the part more than
  0.3 mm off). With the same two tracked scans at their true motion the merge is better than
  either scan (0.075 mm / 12.0 %, covered 0.926).
- **The sheet:** its two scans share 40 % and the accepted pose is about 3 mm off ("the shapes
  alone fit about as well another way: your points decided"). Even at the true motion the
  merge of the tracked scans is worse than scan A (0.226 mm / 35.9 % against 0.163 / 25.7 %):
  each tracked sheet scan is bent by its own slow tracking error, so no one motion fits both.
  At true poses the merge is far better than either (0.074 mm / 14.4 %).
- **The car panel's larger share off** is fill past the panel's free edge (7.4 of the 8.1
  points): the merge carries both scans' border fill. On the panel's face it is more accurate
  (0.022 against 0.028 mm).
- **A tracked scan's first frame can sit millimetres off the rest of its scan** (the bracket's
  scan B: 4.5 mm). Merging at the motion between the first cameras gave 0.134 mm / 24.4 %
  where the shape alignment's pose gave 0.085 / 14.0 %. The app uses the alignment.
- **Thin walls:** only a merge sees both faces of the sheet's 2 mm flange (at true poses it
  reads 0.11 mm too thick; a single scan sees one face and has no reading). The 1 mm rib
  reads thicker merged (+1.16 mm at true poses) than in one scan (+0.81).
- **Taking each place from one scan instead of averaging** (each 2 mm cell's triangles from
  the scan with the lower sigma, or with the lower true error as an upper bound no rule can
  reach; a trial outside the engine) covers 0.2 to 1 point more than the merge and still 0.6
  to 2.1 points less than either scan, even with the truth's choice.

**What would help, in this order:** a surer pose, or a warning, for pairs that share little of
the part; each scan's own tracking (X3, X4, X4c); only then a choice per place.

Repeat with `python tools/merge_accuracy_study.py` (its head gives the order of the steps).

Limits: simulated; one pair per case; the graphics card's path only; scans of 9 s, shorter
than a real one; the picked points are simulated (true matches moved 1.5 mm); the part was not
moved between the scans (the merge of a moved part, with the table maps, was not measured);
the trial per place is a mesh of loose triangles, not a model.

## Two scans that share little (ENGINE_PLAN small things, 2026-11-23): a caution at the alignment and in the merge

X7 found two merges far worse than either scan while the alignment said only "Aligned": the bracket's opposite
sides (46 % shared) and the sheet part (40 %). Where little is shared the scans fit where they meet and can be off
elsewhere. Since 2026-11-23 an accepted alignment whose `overlap` (the share of the smaller scan's part within 1 mm
of the other, normals agreeing) is under `CAUTION_OVERLAP` = 0.70 (`ohs.align.pairwise`) carries a caution, and a
merge measures the same share itself at the poses it is given and says so in its warnings. Nothing is refused.

**How it was measured** (`tools/merge_overlap_sweep.py`, `tools/merge_accuracy_study.py`; data and notes in
`D:\OpenHandScan-work\scratch\mergewarn`). Scans: 90-frame stretches of the scorecard's orbit recordings, made as
Make mesh makes them (graphics card). 39 pairs fixed beforehand: every two scans of one scene from different seeds
(bracket 14, sheet 16, plate with holes 8, car panel 1), 3°-175° apart, each aligned from the shape and from four
picked points (true matches, each 1.5 mm off): 78 alignments, about 3 s each. The pose was compared with two
truths: the motion between the scans' camera paths fitted to the true poses, and the motion between the two meshes
as each fits the true part best. Two sheet scans were bad by themselves (their own mesh 0.29 and 0.37 mm median off
the part, 49 % and 62 % of it more than 0.3 mm off): every pair with them was 3-18 mm "off" whatever it shared.

Accepted alignments (overlap: median pose error against the placed meshes):

| scene | overlap of the accepted alignments | pose off the truth (median) |
|---|---|---|
| bracket, 146-174° apart | 0.446, 0.462, 0.467, 0.474, 0.492 | 0.63-1.06 mm |
| bracket, 3-120° apart | 0.717, 0.829, 0.835, 0.847, 0.861, 0.944, 0.956, 0.963, 0.969 | 0.12-0.51 mm |
| plate with holes, 15-172° | 0.908-0.971 | 0.18-0.58 mm |
| car panel, 99° | 0.977 | 0.31 mm |
| sheet (sound scans) | 0.424, 0.555, 0.660, 0.689, 0.795, 0.864, 0.894, 0.934 | 0.31-1.38 mm (fitted worlds); the placed truth does not hold for the sheet's bent scans |

On the bracket the overlap has a wide gap (0.49 to 0.72); the sheet's pose errors could not be judged by either
truth, so 12 merges (7 pairs) decided: the app's merge at the alignment's pose (a) and, for 5 of them, at the true
motion (b2), judged as X7 judges (median distance / share more than 0.3 mm off the part):

| merge | overlap | better single | merge at the pose | merge at the true motion | ruined by the pose |
|---|---|---|---|---|---|
| X7 P5 sheet 123° | 0.397 | 0.163 / 25.7 % | 0.371 / 57.9 % | 0.226 / 35.9 % | yes |
| X7 P3 bracket 171° | 0.463 | 0.098 / 15.7 % | 0.308 / 51.2 % | 0.075 / 12.0 % | yes |
| M1 bracket 174° | 0.492 | 0.088 / 14.8 % | 0.270 / 45.5 % | 0.076 / 12.0 % | yes |
| M3 sheet 91° | 0.689 | 0.127 / 21.5 % | 0.286 / 46.4 % | 0.173 / 23.6 % | yes |
| M2 bracket 64° | 0.717 | 0.085 / 14.7 % | 0.083 / 11.6 % | - | no (better than either) |
| M6 sheet 76° | 0.795 | 0.123 / 15.1 % | 0.315 / 53.1 % | 0.275 / 44.2 % | no: ruined at the true motion too |
| X7 P1 bracket 97° | 0.861 | 0.088 / 14.8 % | 0.085 / 14.0 % | 0.082 / 14.5 % | no |
| M7 sheet 84° | 0.864 | 0.127 / 21.5 % | 0.178 / 24.7 % | 0.172 / 22.1 % | no |
| M4 sheet 41° | 0.894 | 0.171 / 24.5 % | 0.220 / 32.5 % | 0.216 / 31.5 % | no (a sheet merge is worse by itself) |
| M5 holes 107° | 0.938 | 0.063 / 19.4 % | 0.077 / 23.0 % | - | no |
| X7 P2 bracket 56° | 0.944 | 0.096 / 13.9 % | 0.097 / 14.5 % | 0.097 / 14.5 % | no |
| X7 P4 panel 99° | 0.977 | 0.028 / 4.5 % | 0.022 / 8.1 % | 0.022 / 8.0 % | no |

**The limit: 0.70.** Merges the pose ruined had overlap 0.397-0.689; merges whose pose was sound 0.717 and up. The
margin is thin on one side: 0.689 (M3, sheet, ruined) and 0.717 (M2, bracket, better than either), one pair each.
On the bracket alone the gap is 0.49-0.72. Open3D's RANSAC varies between runs, but here the shape and the
picked-point starts gave the same overlap to three decimals on every pair both accepted.

**Other signals** (same merges): none separates. The smaller of the two shares: ruined up to 0.610 (M3), sound from
0.590 (P1). `rmse_mm`: ruined from 0.221 (M3), sound up to 0.223 (P1). `fit_mm`: ruined from 0.175, sound up to 0.183.
How far the parts reach beyond the shared region (95th percentile, mm) and that over the shared region's size:
ruined from 5.3 mm / 0.18, sound up to 13.1 mm / 0.32. Over all 72 accepted alignments against either truth no
signal separates (the bad sheet scans); overlap gets the fewest wrong (6 of 72 against the placed meshes).

**A check after the merge** (each scan's own part, at its merge pose, against the merged mesh, along its normal; no
truth): does not separate. Share more than 0.5 mm off, the worse of the two scans: ruined merges P3 0.034, M1 0.026,
M3 0.028, P5 0.197; sound P1 0.030, P2 0.010, P4 0.005, M2 0.024, M5 0.018, M7 0.038, and at the true motion P3 0.064,
P5 0.385. A merge at a pose that is off follows each scan where only that scan saw, so each scan's own surface is
in it; what is wrong is where the two parts sit against each other, which needs the truth to see. Not built.

**Cost:** the merge reads both meshes into clouds and scores the pair: 1.6-2.7 s for two 0.5-0.6 M-vertex scans
(the scoring itself 0.01 s), against 27-51 s for the merge; it gives the alignment's own overlap to four decimals.

**Found on the way:** an alignment from the shape ACCEPTED a pose 506 mm off (sheet s1@140 + s2@0, 0.649 overlap,
109° apart: an F11 miss; it gets the caution, being under 0.70). Two of the sheet's 90-frame scans were bad by
themselves (above). The sheet merges are worse than the better single even at the true motion (M4 +7 points, X7 P5
b2 +10): averaging two bent scans of thin walls.

**Thin parts:** a 4 mm plate scanned from above and turned over from below shares only its edges (25 % in the
test): from picked points the engine says "not sure" (a turned pose fits better: its own words, as before); from
tags it is accepted with the caution, whose words hold there (they fit at the edges; a wall seen from both sides can
come out too thick or too thin; one pass with the part standing on edge sees both faces). Nothing is removed.

**Also from the shape.** Two of the four merges the pose ruined were aligned from the shape alone, with 8 of 9
starting guesses agreeing (M1, M3; X7's P3 and P5 were from picked points): the agreement of the guesses does not
show it.

**Shown:** on the Align page as "The scans share little" under the verdict (a caution; Merge stays possible, nothing
more is asked); for several scans the verdict names the links and each pair's line ends "shares little"; a merged
scan lists it under its warnings ("How the scan was made"); a second pass's message ends with it. Checked in the
app's demo data in the browser (light and dark).

Not verified: real recordings; pairs aligned from tags (the caution goes by the overlap alone there); scans longer
than 9 s; the limit rests on one sheet pair each side of it.

## The overall size of a large part (ENGINE_PLAN X8 step 1, 2026-11-23): measured, fails

Gap item 7: a large part's overall size within about 0.05 % (0.65 mm on 1.3 m). Never measured before.
`tools/large_part_study.py`; the set-up and the rule below were fixed before any result (data and notes in
`D:\OpenHandScan-work\scratch\x8`). A measurement: nothing in the engine or its defaults is changed.

**Set-up.**
- A plain painted panel, 1300 x 400 mm, one surface, gently doubly curved and convex up (radii 6 m along, 2 m
  across), built from the simulator's existing pieces. It lies on a dark table on its four corners, 1 mm above it.
  Two Ø10 holes on the centre line 30 mm from each end, 14 mm above the table. Paint and table as the car panel's.
  Nothing along its length to hold on to but its ends.
- One recording a seed (seeds 0, 1, 2): 3 passes along the panel (out, back, out), 130 mm apart, the door's hand
  path settings (distance 240-290 mm, elevation 60-75°, the same hand noise), about 100 mm/s, 56.5 s, 565 frames at
  10 fps, 1280 x 800, realistic. About 22 min to render a seed.
- Processed as Make mesh does on the graphics card at Standard quality, 1 mm mesh detail, Default recipe
  (`scorecard.app_chain`: Follow the camera, Straighten, Refine with lasers), tracked, and fused at the true poses
  (the ceiling, and each reading's own error).
- End to end is read three ways:
  - **Hole tool:** the app's Hole tool on both holes (clicked 1.5 mm outside each rim at 0/120/240°), the distance
    between the two centres in the mesh's own frame.
  - **Edges:** the middles of the two short edges on the mesh, read against a local fit of the panel; judged against
    the same seed's reading at true poses.
  - **Path:** the camera path placed on the true one by the frames at one end (in position; the turn is the whole
    path's), read at the other end. For finding the cause, not judged.
- Bow: the mesh placed on the truth by its two ends; the middle's offset along the normal, and sideways from the long
  edges.
- Slide: at points two passes both saw (found from the truth), how far the two passes' tracked poses put the same
  point apart.
- **Fails:** over 0.05 % end to end on any seed, by the Hole tool where it answers or by the edges against true
  poses.

| | Seed 0 | Seed 1 | Seed 2 |
|---|---|---|---|
| Hole tool, tracked | both refused | A refused, B read as Ø4.1 | both refused |
| Hole tool at true poses | both refused | one click of three answered at each, Ø4.9 and Ø4.5 | A read as Ø5.6, B refused |
| Edges against true poses | -1.75 mm, -0.135 % | +6.53 mm, +0.502 % | +1.58 mm, +0.121 % |
| Edges at true poses (the reading's own error) | +0.09 mm | +0.10 mm | +1.08 mm |
| Camera path, end to end (after Straighten) | -5.5 mm, -0.49 % | +4.5 mm, +0.40 % | -1.3 mm, -0.11 % |
| Path, pass 1 / 2 / 3 alone | -0.5 / -8.1 / -1.4 mm | -0.2 / +7.8 / -2.8 mm | +1.5 / -2.4 / -0.8 mm |
| Passes slide along the panel, RMS (max) | 4.0-6.8 (7.9) mm | 4.8-9.5 (11.6) mm | 1.2-2.7 (4.7) mm |
| Passes slide across, RMS | 0.9-2.0 mm | 1.0-3.5 mm | 1.9-4.1 mm |
| Passes' gap along the normal, RMS | 0.01-0.03 mm | 0.06-0.12 mm | 0.04-0.06 mm |
| Bow along the normal / sideways | -0.014 / -0.13 mm | +0.008 / +0.22 mm | -0.008 / -0.38 mm |
| Mesh against the true panel, median / over 0.3 mm | 0.025 mm / 2.6 % | 0.032 mm / 4.2 % | 0.023 mm / 2.5 % |
| Frames tracked; frames where the surface let the scanner slide | 565 of 565; 39 % | 565 of 565; 40 % | 565 of 565; 38 % |
| Make mesh, memory peak (at true poses) | 142 s, 6.1 GB (83 s, 5.3 GB) | 210 s, 5.9 GB (83 s, 5.2 GB) | 139 s, 6.0 GB (84 s, 5.2 GB) |
| Straighten's loops | 48 found, "not clearly better", unchanged | 48 closed, the end moved 4.0 mm | 48 closed, "already line up", unchanged |
| Straighten's laser crossings | 2,988, unchanged | 3,056, unchanged | 3,139, unchanged |
| The app's warnings | none (path check 1.3 mm, warns at 4) | none (1.3 mm) | none (1.1 mm) |

- **It fails on every seed: 0.12 to 0.50 % end to end from the mesh's edges, 2.4 to 10 times the need.** Seeds 0
  and 1 are clear. Seed 2's 1.58 mm is only 1.5 times that seed's own reading error (1.08 mm at true poses), but its
  passes also sit 1.2 to 2.7 mm apart along the panel, 2 to 4 times the 0.65 mm the need allows.
- **The cause is drift along the length within a pass**, most on the return pass (up to 8 mm over 1.1 m, a steady
  ramp of about 0.7 % of its travel on seeds 0 and 1). The passes then sit 2 to 11 mm apart along the panel, and the
  fused mesh averages them. On seed 1 Straighten's loops moved the end 4 mm and the slide between passes 1 and 3
  went from 11.1 to 9.5 mm: not mended.
- **No scale error.** At true poses the edges read within 0.1 mm on two seeds, and the sign of the tracked error
  changes from seed to seed.
- **No bow.** Along the normal it is under 0.015 mm; sideways it is within the reading's own scatter (-0.13 to
  -0.27 mm at true poses).
- **Straighten does not see it.** The passes' gap along the normal is only 0.01 to 0.12 mm: the passes slide along
  the surface, which the loops and the laser crossings do not measure.
- **The app says nothing after the scan.** The camera-path check reads 1.1 to 1.3 mm (it warns at 4), and the mesh
  against the truth looks excellent (median 0.023 to 0.032 mm). Nothing tells the user the part can be 6.5 mm long.
  The tracker does know while it runs: on 38 to 40 % of the frames it notes that the surface let the scanner slide
  (the track report's `weak.slide`). No warning is built on that figure yet; whether it separates this panel from
  the door is not measured.
- **The Hole tool cannot measure these holes.** Ø10 holes 14 mm above a table on this panel come out closed or
  half-filled, even at true poses: tracked, five of six refused ("no hole edge within 10 mm of the click", once
  "the surface dips only 1.3 mm"); at true poses the few answers are Ø4.5 to 5.6 ("the surface rises from the flat
  face at this edge" for the rest). "Near a floor: Ø10 -0.3 to +0.6" (the plate, seen from above) does not carry
  over to this panel seen at 60-75°. Not compared with that plate on this exact scene.
- **The 768 mm tracking cube** moves 40 or 41 times a scan, so a return pass meets a model that holds nothing of the
  earlier pass beyond about 770 mm. But a 1536 mm cube (`fusion.gpu_volume_voxels=768`, seed 0 only, 20 moves) gave
  the same result: pass 2 -7.85 mm (was -8.07), edges -1.57 mm against the truth (was -1.66), slide 3.9 to 6.9 mm.
  So the cube is not the cause.
- **The mesh model's brick pool** reached 48k of 65k bricks (the most it may take is 187k): not a limit.
- **Time:** Make mesh took 2.5 to 3.7 times the scan's length here (Straighten 32 s, 103 s where it closed loops).

**Limits of this study.** One scene, one hand-path pattern, three seeds, the simulator's hand. The edge reading has
up to 1 mm of its own error at one end (seed 2 at true poses). The cube was tried on seed 0 only. No run was
repeated. Not tried: other speeds or frame rates, four lasers, a part with features along its length, tags, the
path without a GPU, the exe.

**Next (not built):** the plan's step 2, tags as anchors in Straighten (`ENGINE_PLAN.md` X8).

## Tags as anchors on a large part (ENGINE_PLAN X8 step 2, 2026-11-23): built, a setting, off

Step 1 found a 1.3 m plain panel 0.12 to 0.50 % wrong in length: tracking drifts along the plain length within a
pass and the passes then sit 2 to 11 mm apart along the panel, which Straighten's loops cannot see. Step 2 lets
AprilTags hold the scan. Built in `ohs/track/tag_anchors.py` and Straighten (`ohs/track/posegraph.py`); setting
`[process] straighten_tags` (`StraightenParams.tags`), **off**. `tools/large_part_study.py`; data and notes in
`D:\OpenHandScan-work\scratch\x8`.

**How it works.**
- A quick look first: the left image of every fourth keyframe. With no tag there, nothing more is read and the scan
  comes out bit for bit as before.
- Each tag (tag36h11) is found in Straighten's keyframes and its corners are placed by the stereo pair, so the
  printed size need not be known.
- One fit over the keyframes' poses and the tags' poses: each detection adds its four corners in its own camera
  (never a median over the drifted world), the tracked motion between keyframes and Straighten's verified loops stay
  in as terms.
- Weights are measured, on seed 3 only: a corner's scatter 0.39 px across the image and 0.11 px along the view, the
  gyro's turn drift 0.008 degrees a frame. Nothing was tuned after seed 3.
- A tag is left out, and named in the report and the verdict, when it was seen in fewer than 3 keyframes, when its
  views do not agree with the fit (over 1.5 mm, more than a fifth of them), or when it alone bends the scan by more
  than 1 mm with views that disagree with themselves.
- Never worse: the tags act only if, on whole keyframes held out of the fit (every second one, both ways round),
  the corners meet at 0.85 of the distance before or less, and the tracked motion is still believable.

**Set-up, fixed before any tagged render.** The panel, the sweep (3 passes, 565 frames) and the seeds of step 1,
with 20 tags of 25 mm on the table: five along each long side, 300 mm apart and 30 mm off the edge, five across
each short end. The same recording with the setting off and on; fused at true poses twice (the simulator's world,
and a world turned 20 degrees and moved a fraction of a voxel) to show how well the length reading repeats.
Step 1's edge reading jumped by whole voxels at an end lying on the model's grid, so the length is read from the
mesh cut every 2.5 mm across (`edges_slice`); it repeats within 0.12 mm on all three seeds.

**The rule, fixed beforehand:** on all three seeds (a) the mesh's length within 0.05 % (0.65 mm) of the same seed's
true-pose reading, (b) the camera path end to end within 0.05 %, (c) a scan without tags unchanged, (d) the passes'
slide along the panel under 0.65 mm RMS for every pair of passes.

| | Seed 0 off | Seed 0 on | Seed 1 off | Seed 1 on | Seed 2 off | Seed 2 on |
|---|---|---|---|---|---|---|
| (a) Mesh length against true poses | -2.13 mm, -0.164 % | -0.55 mm, -0.042 % | +2.56 mm, +0.197 % | +0.62 mm, +0.048 % | +0.70 mm, +0.054 % | +0.24 mm, +0.018 % |
| The reading at true poses, twice | 0.10 mm apart | | 0.08 mm apart | | 0.12 mm apart | |
| (b) Camera path, end to end | -4.94 mm, -0.439 % | **-0.58 mm, -0.052 %** | +5.08 mm, +0.453 % | **+0.67 mm, +0.060 %** | -1.97 mm, -0.175 % | +0.03 mm, +0.003 % |
| Path, pass 1 / 2 / 3 alone, mm | -0.4 / -7.3 / -1.4 | -0.6 / -0.6 / -0.6 | -0.9 / +8.7 / -2.8 | +0.8 / +0.7 / +0.5 | +1.5 / -3.4 / -0.7 | 0.0 / +0.2 / -0.1 |
| (d) Slide along, RMS, pairs 1-2 / 2-3 / 1-3, mm | 3.50 / 3.99 / 6.15 | **0.72** / 0.57 / 0.39 | 6.03 / 7.69 / 11.4 | 0.45 / 0.54 / 0.58 | 3.05 / 1.43 / 3.33 | **0.77 / 1.14** / 0.34 |
| Slide along, largest, mm | 7.2 | 1.9 | 13.2 | 1.8 | 5.3 | 2.7 |
| Gap along the normal, RMS, mm | 0.01-0.02 | 0.03-0.08 | 0.03-0.05 | 0.06-0.08 | 0.04-0.07 | 0.04-0.07 |
| Path error after a fit, median / largest, mm | 3.17 / 5.46 | 0.42 / 1.87 | 5.37 / 7.60 | 0.46 / 1.75 | 2.48 / 4.95 | 0.34 / 1.52 |
| Mesh against the true panel, median / over 0.3 mm | 0.024 mm / 2.5 % | 0.032 mm / 2.3 % | 0.035 mm / 3.5 % | 0.041 mm / 2.5 % | 0.023 mm / 2.6 % | 0.025 mm / 2.5 % |
| Tags that held the scan / left out | | 17 / none | | 16 / one, seen in 2 keyframes | | 18 / none |
| Keyframes that saw one tag / two | | 70 / 38 of 119 | | 71 / 38 of 117 | | 74 / 39 of 117 |
| Straighten, s | 6.9 | 13.5 | 6.4 | 13.5 | 6.3 | 13.1 |
| Make mesh, s; memory peak | 141; 5.9 GB | 217; 6.1 GB | 142; 6.2 GB | 218; 6.0 GB | 142; 6.1 GB | 217; 5.8 GB |

- **The tags mend most of it: about a tenth of the error is left.** The passes' slide along the panel falls from
  1.4-11.4 mm RMS to 0.34-1.14 mm, the camera path's end-to-end error from 0.18-0.45 % to 0.003-0.060 %, the mesh's
  length error from 0.05-0.20 % to 0.018-0.048 %.
- **The rule is missed, so the setting stays off.** (a) is met on all three seeds, seed 1 only just (0.048 %, with
  a reading that repeats to 0.008 %). (b) misses on seeds 0 and 1 by 0.002 and 0.010 %. (d) misses on seeds 0 and 2
  (0.72, and 0.77 and 1.14 mm RMS against 0.65). (c) holds: on step 1's recordings every field of Straighten's
  report matched the code before and the poses agreed to 5e-6 mm (graphics card); without a graphics card the unit
  test covers it.
- **What is left is the length between the ends.** On seeds 0 and 1 every pass is off by the same 0.6 to 0.8 mm:
  the passes are joined, and all of them are a little short or long together. A diagnostic on seed 0 shows why.
  With the side tags 600 mm apart no frame ever sees two of them, and 0.2 % stays (-2.23 mm, every pass alike):
  the tags join the passes, but the length is held only through frames that see two tags spaced along the part. At
  300 mm that happens in a third of the keyframes.
- **One side is enough.** Seed 0 with tags along the near side only (and the ends): path -0.02 mm, slide 0.35-0.63
  mm RMS. Not judged; one seed.
- **It costs** about 7 s in Straighten and about 76 s in Make mesh here (a second fusion at the new poses); memory
  is the same.
- **Straighten's words** when the tags act end with how many tags held the scan, how far the scan's end or middle
  moved, and any tag left out with the reason.
- **A tag that moves during the scan** by about 1 mm (2.5 times a view's scatter) is not recognised and can bend
  the scan by up to 2 mm (synthetic views with the measured scatter). A larger move is recognised and the tag left
  out.

**Limits and not verified.** One scene, three seeds, the simulator's hand, 25 mm tags on the table (the app prints
20 mm tags: the size itself does not matter, but a smaller tag is read from less far and pins less; not measured).
Not run: tags on the part itself, a tag really moved in a render, a scan in Markers tracking mode (Straighten runs
there too; the tags would act the same way), the path without a graphics card on these recordings, tags closer
than 300 mm. The Hole tool is not judged (step 1: the holes close up). The diagnostics are one seed each.

**Next:** tags close enough along the part that two are in view in most frames (ENGINE_PLAN X8).

### Tags 150 mm apart along one side (X8 step 3, 2026-11-24): fails the rule

The same panel, sweep, seeds and rule as step 2. Layout, fixed before any render: nine 25 mm tags along the near
long side only, 150 mm apart, and step 2's five across each short end (every pass sees the ends at its turns),
19 in all; none on the far side. Runs in `D:\OpenHandScan-work\scratch\x8\runs\s0-row150` to `s2-row150`
(`judge.json`, `spacing.json`). Nothing was tuned.

| | seed 0 | seed 1 | seed 2 |
|---|---|---|---|
| the tags | acted, 18 of 19 used | acted, 18 of 19 used | **refused by the never-worse check** |
| the mesh's length against true poses, with tags (without) | -0.014 % (-0.198) | +0.007 % (+0.309) | +0.006 % (+0.006) |
| the camera path end to end, with tags (without) | -0.021 % (-0.472) | +0.007 % (+0.398) | **-0.126 %** (the same) |
| the passes' slide, worst pair, RMS, with tags (without) | 0.59 mm (7.09) | **0.88 mm** (9.18) | **2.35 mm** (the same) |
| the slide of pairs 1-2 / 2-3 / 1-3, with tags | 0.53 / 0.59 / 0.34 | 0.64 / 0.88 / 0.47 | 2.35 / 1.33 / 1.88 |
| keyframes that see two tags at least 100 mm apart along the part | 35 of 118 | 33 of 117 | 35 of 117 |
| the check on frames set aside, after / before (limit 0.85) | 0.70, 0.67 | 0.57, 0.44 | 0.869, 0.826 |
| Make mesh, without and with tags | 166 and 249 s | 227 and 231 s | 164 and 168 s |
| the true-pose reading repeated | -0.10 mm | +0.07 mm | +0.12 mm |

- **Seed 0 meets the rule.** Seed 1 misses on the slide alone: 0.88 mm RMS against 0.65. **Seed 2 fails:** the
  check on frames set aside came out 0.869 on one of its two splits, above the limit of 0.85, so the tags changed
  nothing and the scan stayed as tracked. That scan started nearly right (the tags' own error on the frames set
  aside 0.51 to 0.56 mm before), so the tags could not show a clear enough gain; its path is still 0.126 % short
  and its passes 2.35 mm apart.
- **Where the tags act, the length holds:** the path within 0.021 % (0.27 mm over 1.3 m) on both seeds, against
  0.003 to 0.060 % with step 2's tags 300 mm apart on both sides.
- A row on one side does not put two spaced tags into most frames: 28 to 30 % of the keyframes, as in step 2,
  because only the near pass sees the row.
- The setting `[process] straighten_tags` stays off. Without tags nothing changes (Straighten's code is step 2's).

Not run: the fit given the known spacing of the tags (it needs a distance term in the engine's fit); rows on both
sides 150 mm apart; tags on the panel; 20 mm tags; the check's limit on scans that start nearly right.

### The never-worse check's limit on a scan that starts nearly right (X8 follow-up, 2026-11-24)

On step 3's seed 2 the check refused the tags at 0.869 against 0.85. Measured: would the tags have helped, and
can the limit move without letting harm in. Straighten's tag block was run again alone, on the processor, on the
saved keyframes, detections and loops of the six tagged scans of steps 2 and 3 (`tools/large_part_study.py`,
commands `limitprep`, `limit`, `limitverdict`; results in `D:\OpenHandScan-work\scratch\x8\limit`). No engine
code and no default was changed. Camera path end to end in % / the passes' worst slide in mm RMS:

| scan | without tags | limit 0.85 (today) | limit 0.90 to 1.00 | no check | the check's ratios |
|---|---|---|---|---|---|
| step 2, seed 0 | -0.439 / 6.15 | -0.051 / 0.72 | the same | the same | 0.54, 0.66 |
| step 2, seed 1 | +0.422 / 10.11 | +0.066 / 0.59 | the same | the same | 0.20, 0.41 |
| step 2, seed 2 | -0.189 / 3.77 | +0.004 / 1.15 | the same | the same | 0.80, 0.65 |
| step 3, seed 0 | -0.489 / 7.48 | -0.018 / 0.58 | the same | the same | 0.67, 0.66 |
| step 3, seed 1 | +0.403 / 9.40 | +0.008 / 0.89 | the same | the same | 0.56, 0.45 |
| step 3, seed 2 | -0.126 / 2.35 | refused | **-0.009 / 1.37** | -0.009 / 1.37 | 0.87, 0.83 |

- **The tags would have helped seed 2:** from a limit of 0.90 they act and the path comes from -0.126 % to
  -0.009 %. The slide only falls from 2.35 to 1.37 mm RMS, so the rule's slide part is still missed.
- **Four harm cases, emulated on the saved detections** (the most-seen tag moved 5 mm halfway through; three
  times the detections' scatter; the side row slipped 3 mm halfway through; half the detections dropped). The
  rule, fixed beforehand: a limit is safe if on every case and scan the result is no worse than without tags
  (the path not more than 0.01 % further off, the slide not more than 0.1 mm RMS larger), or the tags are
  refused, or the bad tag is left out. Limits of 0.85 to 1.00: safe on all 24. No check: not safe; with three
  times the scatter step 3's seed 2 goes to -0.161 % against -0.126 % without tags.
- **A tag moved 5 mm is not always left out.** It was left out on two scans and refused on one; on two it
  stayed in (slide 3.23 mm against 1.15 unharmed, 3.13 against 0.58): still better than without tags, most of
  the gain lost.
- **What the ratio measures:** how much better the tag detections set aside fit after the fit. Across the six
  scans it follows the slide mended (r = -0.96) more than the length mended (r = -0.68), and it cannot fall far
  below 1 when the detections set aside start near the tags' own scatter (about 0.45 mm), though the length is
  still off.

Limits: the limit of 0.90 was found on the one scan it mends, so this is no test of it: fresh seeds are needed.
Every harm case is emulated, one draw each. The camera path and the slide only; no mesh made. The processor's
loops straightened four of the six scans where the graphics card's did not (by up to 2.6 mm), so those four
start from other poses than the judged runs; step 3's seed 2 and step 2's seed 0 match them exactly.

### Fresh seeds for the limit (X8, 2026-11-24): the limit stays; the length holds, the slide does not

Seeds 3, 4 and 5 of the same panel with step 3's layout (nine tags along the near side 150 mm apart, five across
each end), not looked at before, each processed without tags, with the tags at the check's limit of 0.85 and at
0.90 (`D:\OpenHandScan-work\scratch\x8\runs\s3-row150` to `s5-row150`). The rule, fixed beforehand: 0.90 is safe if
its run is no worse than 0.85's and than no tags on every measure; worth it if the tags act at 0.90 where 0.85
refused them and the path is then under 0.05 %.

| | seed 3 | seed 4 | seed 5 |
|---|---|---|---|
| the mesh's length against true poses, with tags (without) | -0.001 % (-0.017) | -0.005 % (-0.551) | -0.003 % (-0.197) |
| the camera path end to end, with tags (without) | +0.001 % (-0.243) | -0.014 % (-0.725) | -0.010 % (-0.456) |
| the passes' slide, worst pair, RMS, with tags (without) | **0.77 mm** (5.95) | **1.02 mm** (10.21) | **1.41 mm** (7.85) |
| the check's ratios (limit 0.85) | 0.62, 0.58 | 0.45, 0.53 | 0.72, 0.54 |
| Make mesh, without and with tags | 149 and 229 s | 218 and 226 s | 146 and 225 s |
| the true-pose reading repeated | +0.04 mm | +0.11 mm | +0.13 mm |

- **0.85 refused the tags on none of the three, so 0.90 gave the same runs.** Safe, and neither supported nor
  refuted: the limit stays at 0.85.
- **The length is mended on every seed** (the path from 0.24 to 0.73 % off to within 0.014 %).
- **The slide between passes misses on every seed** (0.77 to 1.41 mm RMS against 0.65), though it falls from 6 to
  10 mm.

**All nine tagged scans** (steps 2 and 3 and these): the tags acted on eight; the mesh's length is within 0.05 %
on all nine; the path on six of the eight where they acted (the other two miss by 0.002 and 0.010 %); the slide
within 0.65 mm RMS on two; the whole rule on one. A scan without tags is the same bit for bit.

Not measured: what a slide of 0.6 to 1.4 mm between passes costs the surface and features along the part (the
panel is plain, and the length read from its edges is right); a fresh seed on which 0.85 refuses.

### Short scans with tags in view, the setting off and on (X8, 2026-11-24): a thin wall is lost; the setting stays off

Before `straighten_tags` could go on by default (decision E-D5), every short recording with tags in view was
processed with the app's default chain with the setting off and on (limit 0.85), on the graphics card: the
standing walls and the sheet with six table tags (seeds 0 to 2), one of each in Markers mode, and four 90-frame
bracket and sheet stretches with eight table tags (`D:\OpenHandScan-work\scratch\x8\short`, `table.json`). The
rule, fixed beforehand: on every scan the run with the setting on is no worse than the run with it off (path
median within 0.05 mm and largest within 0.2 mm, mesh median within 0.01 mm and the share over 0.3 mm within 1
point, no wall more than 0.05 mm further from the truth, no frame lost, nothing new warned). One scan processed
twice with the setting off gave the same numbers and the same path, so no tolerance was widened.

| | scans |
|---|---|
| the tags did not act (too few, not better, would bend the scan): every number and the path the same | 8 |
| the tags acted, better or the same | 4 |
| the tags acted, **worse** | 1 |

- **The one worse scan loses a thin part.** The standing walls, seed 2, markerless: five tags held the scan and
  nearly everything got better (path median 1.39 to 0.66 mm, mesh median 0.139 to 0.098 mm, the 1 mm wall's
  error +0.88 to +0.30 mm, the 2 mm wall's +0.51 to -0.06 mm), but **the 0.5 mm wall all but disappeared from
  the mesh** (its coverage from 49 % to 2.5 %, its thickness no longer readable; the share of the part within
  0.5 mm from 0.902 to 0.874). Nothing warned. The wall is there with the setting off and at true poses (85 %).
- Not separated: whether the corrected poses put the wall's two sides too close for a 1 mm mesh to keep it, or
  the second fusion that Straighten makes when it acts removed it. (With the setting off the loops changed
  nothing on that scan and nothing was fused again.) Straighten fuses again whenever its loops act, tags or no
  tags, so the question is wider than the setting.
- Where the tags acted and helped: the sheet, seed 0 (path median 1.16 to 0.21 mm; flange +0.62 to +0.27, 1 mm
  rib +0.94 to +0.48, 3 mm rib +0.52 to -0.04 mm), the same in Markers mode (0.69 to 0.25 mm), and two bracket
  stretches (0.70 to 0.16 and 0.98 to 0.18 mm; on one of them a tag moved 2 mm was not left out).
- The setting reaches a Markers-mode scan too: Make mesh runs Straighten in either mode.
- Make mesh takes 13 to 40 s longer when the tags act (a second fusion), nothing when they do not.

**So the setting stays off**: a thin part must not go missing in silence. Not done: the run that tells the two
causes apart; more than one seed in Markers mode; a second run of the scan that lost its wall.

## Thin walls: the raw reading, and where the error is made (ENGINE_PLAN X16 and X17, 2026-11-23)

Thin standing walls read 0.5 to 1.3 mm too thick (decision E-D3, item 2). Two questions were measured: can a
wall's thickness be read without the mesh, and why does tracking place a wall's two sides apart.
`tools/wall_thickness_study.py`; notes and every run in `D:\OpenHandScan-work\scratch\ed3\walls_looks`. Scenes:
the scorecard's standing walls (0.5, 1 and 2 mm) and sheet part (a 1 mm rib and a 2 mm flange), 3 seeds each, on
the graphics card.

### The thickness read from the raw frames (X16)

At a click, each face of the wall is a plane fitted to the raw depth points of the frames that see it (the faces
told apart by their normals); the thickness is the distance between the planes. 15 walls (5 per seed).

| poses | the raw reading minus the truth | the mesh's reading minus the truth | twice the reading's own ± covers the truth |
|---|---|---|---|
| true | -0.04 to +0.07 mm | +0.12 to +0.84 mm | 9 of 14 (one wall refused) |
| tracked | +0.29 to +0.99 mm (one wall -0.01) | +0.25 to +1.26 mm | 1 of 15 |

- The raw reading removes the mesh's share, 0.1 to 0.7 mm a wall. What tracking adds stays.
- Its own ± (0.03 to 0.09 mm, from resampling the frames) holds the depth's and the frames' scatter. It cannot
  hold an error that all frames of one side share against the other side's, so tracked it under-covers.
- Where the wall is found from the mesh by a ray through it, the 3 mm rib is read too (the thin-walls finding
  refuses it); the reading with the region taken from the mesh agrees with the one taken from the true part
  within 0.08 mm.
- Not in the app. A study tool only.

### A tie seen from both sides: dropped

A matte ball beside the walls, two balls, and AprilTags on the table as Straighten's anchors (the walls' scan
is 4 s, 40 frames). Separation of the two sides (0.5 / 1 / 2 mm walls; today +0.57 / +0.55 / +0.51 on seed 0):

| | seed 0 | seed 1 | seed 2 |
|---|---|---|---|
| today | +0.57 / +0.55 / +0.51 | +0.47 / +0.43 / +0.35 | +0.62 / +0.53 / +0.31 |
| one ball | +0.23 / +0.26 / +0.33 | +0.45 / +0.31 / +0.19 | +0.22 / +0.08 / -0.17 |
| two balls | +0.80 / +0.58 / +0.40 | | |
| tags | +0.58 / +0.57 / +0.54 | | |

- One ball about halves it on some walls and pulls the 2 mm wall 0.17 mm too thin on seed 2; the mesh barely
  gains. Two balls are worse. The tags do not act: too few keyframes see each tag on so short a scan, and with
  every frame looked at the held-out test refuses.
- **A defect found on the way, in Straighten's laser crossings** (ENGINE_PLAN, small things): with a ball in view
  on seed 0 the frames test let the adjustment act (ratios 0.83 with one ball, 0.77 with two, limit 0.85), the
  crossings' median miss halved (0.127 to 0.061 mm), and against the truth the path got worse (median / largest
  0.26 / 1.26 to 0.32 / 1.40 mm with one ball, 0.18 / 0.52 to 0.29 / 1.03 with two) and so did the walls
  (two balls: +0.52 / +0.34 / +0.15 to +0.80 / +0.58 / +0.40). On so short a scan a held-out frame shares its
  geometry and its errors with the fitted frames on either side of it.

### Where the error is made: a ladder of references (X17)

The same recording tracked against four models. "Exact depth": the simulator's true depth in place of the
matcher's. Separation of the two sides, 0.5 / 1 / 2 mm walls:

| what each frame is tracked against | exact depth, seed 0 | seed 1 | seed 2 | the matcher's depth, seed 0 | seed 1 | seed 2 |
|---|---|---|---|---|---|---|
| a frozen 2 mm map, filled from exact depth at the true poses | -0.06 / -0.03 / -0.00 | +0.14 / +0.11 / +0.09 | +0.00 / -0.03 / -0.06 | +0.47 / +0.36 / +0.18 | | |
| the engine's evolving map, integrated at the true poses | -0.09 / -0.07 / -0.06 | +0.10 / +0.04 / +0.02 | -0.10 / -0.11 / -0.18 | +0.09 / +0.07 / +0.08 | | |
| the loop, as the product runs | -0.72 / -0.67 / -0.58 | -0.10 / -0.12 / -0.11 (slipped, path 4.1 mm) | +0.62 / +0.61 / +0.58 | +0.60 / +0.49 / +0.52 | +0.50 / +0.43 / +0.37 | +0.60 / +0.43 / +0.24 |

- **A right map holds the sides** within 0.14 mm (frozen) and 0.18 mm (evolving, at true poses) on every wall and
  seed, with a one-sided 2 mm map. So the map's representation is not the cause.
- **Only the loop separates them,** by about 0.6 mm: each tracked frame goes into the map the next frames are
  tracked against. With exact depth the sign follows the seed (too close on seed 0, too far on seed 2); with the
  matcher's depth it is too far, "too thick", on all three.
- **The matcher's depth against a map made from exact depth** gives +0.36 mm (1 mm wall), against a map made from
  its own depth at true poses +0.07: the depth and the map must carry the same edge errors.
- **The exact scene as the model** (no TSDF) was not measured: that harness failed (the path ran 8 to 18 mm off).

**A bias sweep:** a disparity offset added to exact depth (0.1 px is about 0.135 mm of depth at 240 mm), seed 0,
the 1 mm wall:

| offset, px | -0.10 | -0.05 | -0.02 | 0 | +0.02 | +0.05 | +0.10 |
|---|---|---|---|---|---|---|---|
| the loop | -0.59 | -0.65 | -0.65 | -0.67 | -0.71 | -0.70 | -0.76 |
| the evolving map at true poses | -0.01 | +0.49 (slipped) | -0.04 | -0.07 | -0.07 | -0.06 | -0.07 |

The loop's line has a slope of about -0.85 mm a pixel and does not pass through zero: -0.67 mm at no bias. It is
not a shared bias multiplied by a weak hold. Without the feedback the offsets leave the separation where it was.

**What the tracker sees.** Each map ray cast at the true poses against the exact depth (every third frame): a
2 mm map puts its surface 0.10 to 0.17 mm in front of the true one in the median, also when it was filled from
exact depth at true poses (p90 of the difference 0.29 to 0.46 mm). That bias is the map's own and every frame
shares it.

**A refinement on the raw frames would return its start.** Point to plane between the raw frames of the scan
(seed 0: 40 frames, about 445,000 point pairs between 704 frame pairs, the first frame fixed, every other pose
optimised again at each point), profiled along the separation. With the pairs found again at every point the
cost is flat within its noise over ±0.8 mm and follows the number of pairs. With the pairs fixed at the start
poses the minimum is at the start: at 0 from the true poses and at +0.04 mm from the tracked poses, where the
truth lies at about -0.55 mm and costs more. The standard error of 0.035 to 0.07 mm given earlier for this
relative pose was computed with pairs fixed at the truth and measured their stiffness (corrected 2026-11-23).

**Not indicated by this:** a tracking map that keeps a wall's two faces apart (designed, 3 to 5 days; not built).

Limits: simulated; one scene (three standing walls in a box, near the worst case); the sweep, the profile and
the matcher's depth on the two true-pose rungs are seed 0 only; offsets other than disparity not run; one run
slipped on each of two rungs; the graphics card's path only.

### Where the offset enters, and six remedies on the loop (X17, 2026-11-24)

Each frame's pose error across the 1 mm wall, from the saved runs (`walls_looks\timecourse.txt`): with the
matcher's depth the first side holds within about 0.1 mm (seeds 0 and 1); the frames over the walls' ends scatter
by 0 to 1.1 mm from one frame to the next; the second side then settles 0.4 to 0.5 mm away. The second side is
first seen by those weakly held frames, and later frames register to what they put into the map. The frames over
the ends are weakly held on every rung of the ladder, right maps included (+0.4 to +0.85 mm); with a right map
their error does not carry on. Seed 2 also drifts on its first side; with exact depth the offset is there from
the first frames.

Six remedies on the loop, the matcher's depth, the standing walls, seeds 0 to 2 (`walls_looks\round3.txt`). The
rule, fixed beforehand: the 1 mm wall's separation at most 0.2 mm on all three seeds, the camera path no worse, no
frame lost. Separation of the two sides, 0.5 / 1 / 2 mm walls; the path's error, median / largest in mm:

| | seed 0 | seed 1 | seed 2 | path, seed 0 | seed 1 | seed 2 |
|---|---|---|---|---|---|---|
| today | +0.60 / +0.49 / +0.52 | +0.50 / +0.43 / +0.37 | +0.60 / +0.43 / +0.24 | 0.53 / 1.21 | 1.52 / 3.93 | 1.50 / 2.50 |
| `start_together` (X4b) | +0.56 / +0.52 / +0.49 | +0.48 / +0.43 / +0.37 | +0.57 / +0.42 / +0.24 | 0.33 / 0.99 | 0.49 / 0.94 | 1.09 / 1.88 |
| `slip_check_mm` (X4c) | as today: it never acts | | | | | |
| `fuse_lag` (X4c) | +0.53 / +0.52 / +0.53 | +0.40 / +0.36 / +0.35 | +0.40 / +0.31 / +0.16 | 0.44 / 1.21 | 3.19 / 8.47 | 0.42 / 3.94 |
| weakly held frames not fused | refused | refused | +1.00 / +0.89 / +0.87 | 7.4 / 128 (105 frames lost) | 53 / 167 (52 lost) | 1.19 / 2.50 |
| the map's ray cast 0.15 mm deeper | +1.11 / +0.89 / +0.76 | -0.03 / -0.11 / -0.18 | +0.19 / +0.06 / -0.08 | 0.79 / 1.89 | 1.41 / 3.90 | 1.14 / 2.21 |
| the engine's hold of a weak step | +0.57 / +0.45 / +0.49 | +0.49 / +0.43 / +0.38 | +0.58 / +0.38 / +0.19 | 0.54 / 1.21 | 1.91 / 3.93 | 1.55 / 2.53 |

- **None alive.** `start_together` makes the path better on all three seeds and leaves the walls where they
  were. `fuse_lag` helps two seeds a little and makes seed 1's path worse. Not fusing the weakly held frames
  loses tracking: nearly every frame of this scene counts as weakly held. Taking the map's measured ray-cast
  bias out is a strong lever whose effect follows the seed (worse on seed 0, cured on seeds 1 and 2).
- The walls themselves cannot hold the pose across the turn, and nothing that only changes how frames enter the
  map makes them. What is left is a pose that does not come from the walls.

### Thin walls in Markers mode (X17, 2026-11-24): not a remedy

The standing walls and the sheet part, each with six 25 mm tags on the table round the part, seeds 0 to 2,
processed as Make mesh does with the scan's mode "markers" (`markers.tag_mm` 25) and markerless on the same
recordings, each also at true poses (`walls_looks\summary4.md`). The rule, fixed beforehand: the frames' raw
separation within 0.2 mm on every wall of 2 mm or less on all six recordings, no frame lost, the mesh's error no
more than at its true poses + 0.15 mm. Raw: the frames' reading minus the truth; walls 0.5 / 1 / 2 mm, sheet
flange 2 / rib 1 / rib 3 mm; the path's error median / largest in mm:

| recording | run | raw | mesh | path | frames lost | tags in view (median, least) |
|---|---|---|---|---|---|---|
| walls, seed 0 | Markers | -0.26 / -0.23 / -0.13 | +1.08 / +0.80 / +0.81 | 0.39 / 8.68 | 8 of 120 | 2, 1 |
| | markerless | +0.58 / +0.57 / +0.54 | +1.17 / +0.85 / +0.69 | 0.84 / 1.93 | 0 | |
| | true poses, Markers' mesh and markerless mesh | -0.02 / 0.00 / -0.02 | +1.15 / +0.98 / +0.79 and +0.78 / +0.53 / +0.39 | | | |
| walls, seed 1 | Markers | -0.47 / -0.26 / -0.11 | +1.01 / +0.95 / +1.24 | 0.91 / 3.71 | 4 | 2, 1 |
| | markerless | +0.51 / +0.46 / +0.38 | +1.24 / +0.75 / +0.60 | 2.92 / 6.51 | 0 | |
| | true poses, both meshes | 0.00 / -0.01 / -0.03 | +1.44 / +1.25 / +1.11 and +0.81 / +0.40 / +0.27 | | | |
| walls, seed 2 | Markers | -0.14 / -0.17 / -0.25 | +1.16 / +0.82 / +1.11 | 1.51 / 13.91 | 29 | 2, 1 |
| | markerless | +0.58 / +0.49 / +0.25 | +1.23 / +0.88 / +0.51 | 1.39 / 3.67 | 0 | |
| | true poses, both meshes | -0.03 / +0.01 / -0.04 | +1.43 / +1.40 / +1.18 and +0.77 / +0.63 / +0.17 | | | |
| sheet, seed 0 | Markers | +0.01 / 0.00 / +0.07 | +0.76 / +1.35 / +0.09 | 0.69 / 2.91 | 11 of 240 | 3, 1 |
| | markerless | +0.41 / +0.63 / +0.52 | +0.62 / +0.94 / +0.52 | 1.16 / 2.31 | 0 | |
| | true poses, both meshes | +0.01 / +0.02 / 0.00 | +0.54 / +1.33 / +0.19 and +0.20 / +0.27 / +0.03 | | | |
| sheet, seed 1 | Markers | -0.12 / -0.04 / -0.03 | +0.45 / +1.58 / +0.18 | 0.79 / 2.07 | 4 | 3, 1 |
| | markerless | 0.00 / +0.20 / +0.09 | +0.27 / +0.59 / +0.06 | 0.57 / 2.83 | 0 | |
| | true poses, both meshes | 0.00 / +0.02 / -0.02 | +0.43 / +1.53 / +0.30 and +0.16 / +0.24 / +0.04 | | | |
| sheet, seed 2 | Markers | -0.45 / -0.10 / -0.06 | +0.27 / +1.37 / +0.11 | 0.32 / 10.70 | 7 | 3, 1 |
| | markerless | +0.34 / +0.98 / +0.80 | +0.58 / +1.24 / +0.70 | 0.67 / 3.51 | 0 | |
| | true poses, both meshes | 0.00 / +0.05 / -0.01 | +0.41 / +1.45 / +0.22 and +0.13 / +0.23 / +0.03 | | | |

- **All three parts of the rule are missed.** Frames are lost on all six recordings (4 to 29: no tags, too few
  tags, a high residual). The raw separation is beyond 0.2 mm on five of six, and now inward: the walls read 0.10
  to 0.47 mm thinner than the truth, which a reading must never do in silence. The mesh is thicker than
  markerless on most walls.
- **Why:** six tags on the table leave 2 in view on the walls and 3 on the sheet in the median, sometimes 1; a
  frame's tag corners scatter 0.28 to 0.37 mm RMS and the path has spikes of 2.1 to 13.9 mm; the tags lie in one
  plane. The inward sign on five of six suggests a bias of the tag pose, not scatter alone.
- **Found on the way:** in Markers mode Make mesh keeps the opposite-sides rule off (`[process] thin_walls`
  "auto" means on for scans without markers only), so a thin wall's mesh reads +0.8 to +1.6 mm at true poses
  there, against +0.03 to +0.84 markerless (ENGINE_PLAN, small things).
- What holds a thin part today is laying it flat: a 0.5 mm plate lying on the table reads within 0.01 mm.

Not run: Markers mode with `thin_walls` "on"; more or larger tags; tags on the part; the path without a graphics
card. The raw readings in Markers mode use that run's own depth over the region chosen from the truth.

## A merge from one model per scan (ENGINE_PLAN X18, 2026-11-23): measured, dropped

X7 found that today's merge (every frame of both scans fused into one model) is often worse than the better single
scan. Decision E-D4 restated what a good merge must do: (1) on each scan's own seen surface it is no worse than that
scan, within the spread; (2) nothing thin or small gets worse; (3) it holds more of the part than the larger scan;
(4) a pair that shares under 0.70 is aligned from tags. X18 asked whether fusing each scan alone and combining the two
models does better. `tools/merge_combine_study.py`; data and notes in `D:\OpenHandScan-work\scratch\ed3`
(`merge_looks`, `gpu1`).

**The cases:** X7's five pairs (P1 to P5) and the seven merges of the overlap sweep (M1 to M7): bracket, panel, sheet
and holes plate, scans of 90 frames (9 s) as in X7. Poses: the app's (from the shape, or from picked points where the
shape is not sure), and the true motion (the rigid fit of each scan's tracked frames to the truth).

### First tests, on the saved meshes (processor only)

Each mesh is read along the true normal on the surface the two scans saw (the nearest vertex within 1.5 mm whose
normal agrees), and the two readings are combined by a rule. 27 cases (pair and pose).

| | E-D4 (1) and (3) pass, of 27 |
|---|---|
| today's merge | 10 |
| the two readings with an equal say | 16 |
| weighted by 1 / sigma squared | 17 |

- **Weighting by the sigma was dropped by its own test:** it failed on 7 of the 27 (a thin rib thicker, or a scan's
  own surface worse than that scan on a sound pair).
- **What the sigma is worth as a weight** (per vertex, single scans): its rank correlation with the true error is
  0.29 to 0.46. It is a loose upper bound: on the bracket, where it says 0.3 to 0.5 mm, the error's median is 0.08 to
  0.09 mm. On the tracked sheet the low-sigma vertices are under-covered (twice the sigma covers 0.79 to 0.80 where
  it says 0.05 to 0.2 mm), because an error made by tracking is invisible to it.

### The experiment on the graphics card

Fixed before any pair was judged (`gpu1\NOTES.md`): 35 pairs (the 12 cases and 2 spread pairs for each, looking from
the case's own sides on other seeds; the holes plate has 1), each at both poses. For each: today's merge
(`ohs.align.merge_scans`), one model per scan combined with the faces kept apart (the mean where both scans count and
their gradients agree; the larger value where they point opposite ways, so each face stays where its own scan put
it), and as a control the plain average of the same two models. The spread of a metric is its range over a case's
pairs. **Kill rule:** at either pose, the new merge fails E-D4 (1) on a sound pair where today's passes, or a wall of
2 mm or less reads 0.05 mm or more thicker than today's, or coverage falls below today's by more than the spread.

**Killed.** E-D4 (1) and (3) both pass on:

| pose | pairs | today's merge | one model per scan, faces kept apart | plain average | surface emulation |
|---|---|---|---|---|---|
| the app's pose, sound pairs | 23 | 11 | 10 | 10 | 20 |
| the true motion, sound pairs | 31 | 17 | 15 | 14 | 30 |
| pairs whose pose is ruined (both poses) | 16 | 4 | 2 | 2 | 6 |

- **Fails E-D4 (1) where today passes:** P1s1 at both poses, M3s2 at the true motion.
- **Thin walls thicker than today's:** the 2 mm flange +0.10 and +0.13 mm (M3s1, M3s2, true motion); the 1 mm rib
  +0.06 mm (P5, picked points).
- **Coverage below today's by more than the spread** (true motion): the bracket's opposite sides -0.7 to -1.0 points
  (six pairs); sheet pairs -6.2 to -9.3 points (M3 and its spread pairs); -22.3 points on the two pairs that hold a
  poor scan (sheet seed 2 from 50 degrees, 0.565 covered alone): with an equal say it pulls the better scan's surface
  towards it.
- **Elsewhere, kept apart against today,** on each scan's own surface: the panel unchanged; the bracket slightly worse
  (cover -0.1 to -0.5 points, median +0.005 to +0.018 mm); the holes plate -0.1 points and +0.002 to +0.005 mm; sheets
  from unchanged to clearly worse (M6: -1 to -5 points, +0.02 to +0.05 mm).
- **Thin walls on sound sheet pairs** mostly read nearer the truth kept apart: the 1 mm rib +1.38 to +1.11 mm (M4),
  +1.09 to +0.84 (M6; +0.77 to +0.38 at the true motion), +0.58 to +0.44 (M7); the 2 mm flange +0.71 to +0.52 (M6).
- **The two halves of the change:** on the bracket the plain average and kept apart are about equal and both worse
  than today's one model: an equal say per scan is what costs. On the sheets the plain average is far worse (10 to 42
  points of cover lost) and keeping the faces apart recovers most of it. Neither beats today's merge.
- **Twice the merged sigma** covers 0.98 to 1.00 of the surface kept apart (the holes plate 0.85 to 0.92, about the
  single scans' own). Today's merged sigma under-covers on the holes plate (0.69 to 0.72 against the singles' 0.85 to
  0.905) and on several sheet pairs (0.86 to 0.92).
- **The emulation's gain did not survive fusing.** Its reading rejects outliers and picks a face by its orientation; a
  fused volume cannot. Its 20 of 23 should not be quoted as a result.
- **Time and memory:** about 29 s a merge (2 x 11.7 s to fuse each scan alone, 5.2 s to combine and mesh) against
  today's 27.2 s; the same swing of system memory (+4.6 GB).

**Not built.** What may be worth carrying on is keeping a wall's two faces apart where two scans saw opposite faces,
inside today's one model (ENGINE_PLAN X18).

### A pose from tags, where two scans share little (ENGINE_PLAN X19: the first real test passes; not built)

Tag maps emulated for both scans (8 tags, the corner noise measured in "Tags as anchors on a large part", once
independent and once shared between tags; 3 runs each):

| pair | shared | the app's pose from the true motion | the tag pose from the true motion | refined from the tags |
|---|---|---|---|---|
| P3 (bracket, opposite sides) | 46 % | 0.97 mm | 0.11 to 0.29 mm | 0.97 mm |
| M1 (bracket) | 49 % | 0.79 mm | 0.30 to 0.46 mm | 0.79 mm |
| M3 (sheet) | 69 % | 0.51 mm | 0.48 to 0.69 mm | 0.51 mm |
| P5 (sheet) | 40 % | 3.19 mm | 4.6 mm | 3.19 mm |

- **What the pose is worth, with real fusing** (the experiment above): on the 12 pairs that share under 0.70 today's
  merge passes E-D4 (1) and (3) on none at the app's pose and on 10 at the true motion (the two misses are sheet
  pairs).
- The refinement from the shapes walks a tag pose back to the shapes' own minimum every time, so a tag pose has to be
  held.
- **Today's merge at the held tag pose, real fusing** (2026-11-24; `gpu1\tag_merges.txt`; 4 cases with 3
  independent-noise and 3 shared-noise tag maps each, 7 spread pairs with one each; the test fixed beforehand: E-D4
  (1) and (3) pass on every run of the pairs whose single scans are sound, the P3 and M1 families). **It passes.**

  | pair | the tag pose from the true motion | E-D4 (1) and (3) at the tag pose | at the tag pose | at the app's pose | at the true motion |
  |---|---|---|---|---|---|
  | P3, 6 runs | 0.11 to 0.29 mm | all pass | 0.952 to 0.954 / 0.067 to 0.074 / 11.5 to 11.7 | 0.897 / 0.199 / 41.1, fails | 0.954 / 0.064 / 11.5 |
  | M1, 6 runs | 0.30 to 0.46 mm | all pass | 0.952 to 0.953 / 0.063 to 0.066 / 11.0 to 11.2 | 0.910 / 0.166 / 37.0, fails | 0.953 / 0.063 / 11.0 |
  | P3s1 | 0.51 mm | pass | 0.957 / 0.067 / 10.4 | 0.846 / 0.203 / 44.2, fails | 0.955 / 0.058 / 10.7 |
  | P3s2 | 0.18 mm | pass | 0.953 / 0.058 / 10.0 | 0.936 / 0.149 / 31.2, fails | 0.953 / 0.056 / 10.0 |
  | M1s2 | 0.79 mm | pass | 0.954 / 0.066 / 10.8 | 0.880 / 0.211 / 43.4, fails | 0.953 / 0.060 / 10.9 |
  | M3 (sheet), 6 runs | 0.48 to 0.69 mm | all pass | 0.924 to 0.925 / 0.163 to 0.167 / 22.1 to 22.4 | 0.902 / 0.250 / 38.6, fails | 0.924 / 0.154 / 20.9 |
  | M3s1 (sheet) | 1.42 mm | fails (1) | 0.920 / 0.217 / 30.2 | 0.675 / 0.382 / 58.0, fails | 0.925 / 0.177 / 23.1, fails (1) |
  | M3s2 (sheet) | 0.49 mm | pass | 0.912 / 0.198 / 32.1 | 0.691 / 0.404 / 62.4, fails | 0.919 / 0.155 / 25.4 |
  | P5 (sheet), 6 runs | 4.59 to 4.67 mm | all pass | 0.903 to 0.905 / 0.165 to 0.171 / 23.6 to 24.4 | 0.677 / 0.327 / 52.8, fails | 0.778 / 0.234 / 39.2 |
  | P5s1 (sheet) | 3.97 mm | pass | 0.905 / 0.187 / 25.7 | 0.766 / 0.299 / 49.8, fails | 0.757 / 0.290 / 48.3, fails (1) |

  (Columns 4 to 6: the share within 0.5 mm / the median in mm / the percentage more than 0.3 mm off, over all the
  surface either scan saw. The sheet pairs are reported, not part of the test. P5s2 passes too.)
- On the bracket a held tag pose gives the true motion's merge. On the bent sheet scans of P5 it gives a better one
  than the true motion: the fit of all tracked frames to the truth is not the best rigid pose for two scans bent
  differently, and the tags, seen where the part is, are a better compromise.
- Twice the merged sigma covers 0.983 to 0.995 at the tag poses, as at the true motion (0.86 to 0.99 at the app's
  pose).
- **The overlap is no evidence against the tags:** at the shapes' own minimum the overlap is higher than at the tag
  pose on every pair, by 0.002 to 0.13, and that minimum lies 0.4 to 1.2 mm from the tag pose on the bracket and M3
  pairs and 1.6 to 4.5 mm on the P5 pairs. A rule that lets the shapes overrule the tags after a move of 3 mm with
  a better fit would have overruled P5's tags.
- P3's cause, read per region: at the true motion the two scans agree in the median everywhere (within 0.03 mm, no
  trend along the part) and disagree in the tails (0.4 to 0.7 mm at the 90th percentile, most on the upright, which
  one scan saw at 45 degrees). The 0.9 mm is a slide along the part's length, 0.02 to 0.09 mm of it along the
  normals: a weakly held direction pulled by local disagreements at edges.
- P5 fails at every pose, the true motion included: its two sheet scans are slid and bowed differently by tracking.
  The held tag pose still covers more (0.83) than the app's (0.71).

Repeat with `python tools/merge_combine_study.py` (its head gives the commands).

Limits: simulated; scans of 9 s, shorter than a real one; the spread pairs of the sheet and the holes plate share a
seed or a scan with their case, so those spreads are half independent; no part was moved between the scans; fine
walls and Refine with lasers are absent from both merges, as in the app; the tag maps are emulated, not rendered;
one model with the opposite-sides rule off was not run (the rule is tied to the thin-walls setting).

## A hole's rim read from the camera images (ENGINE_PLAN X15, 2026-11-23 and 24): alive; not in the app

A Ø25 hole over open space reads 1 to 7 mm small, and Ø6 and Ø10 there cannot be read at all (decision E-D3,
item 1). Measured: can a hole be read from the raw camera images, where its rim is a sharp step, without the mesh.
`tools/hole_rim_study.py` (a study tool; nothing in the engine uses it); notes, rows and summaries in
`D:\OpenHandScan-work\scratch\ed3\holes_looks` (`NOTES.md`, `summary3_main.txt`, `summary3_drop.txt`,
`summary3_abl.txt`). Everything ran on the processor.

**The reading.** At a click: about 20 recorded frames that saw the hole; in each, the face's plane from that
frame's depth, the rim's step along radial lines in both raw cameras, and one model of the hole (the entrance
circle and the exit circle a wall's height below it, the height free or given). The answer is the median over the
frames that are not refused. The diameter is measured in each frame's own coordinates and needs no camera path;
the position is taken to the world with the path. The ± (1 sigma) is the frames' scatter with 0.1 mm added that
all frames share.

**Scenes.** The scorecard's hole plates (3 mm, Ø25, Ø10, Ø6; 120 mm and 10 mm above the floor, 3 seeds each) and
car panel (Ø25, 5 seeds), at true poses and with the app's tracking on the graphics card. Study plates without
the block: 1 and 5 mm thick at 10 and 120 mm above a dark floor, and 3 mm at 120 mm above a bright textured
floor, 3 seeds each, at true poses and with tracking on the processor's path. "Dots off": every fifth frame
rendered with the projector off (the scanner cannot do this today). Three clicks a hole, the scorecard's own.

**The rule, fixed beforehand.** Dropped if at true poses any Ø25 or Ø10 (the median of its answered clicks) is
off by more than 0.3 mm, or tracked by more than 0.5 mm on a scan whose path check does not warn; a scan that
lost tracking must warn or refuse. A refusal is no failure. Each arm (dots on, dots off) is judged by itself,
with the thickness free.

**Round 3** (rounds 1 and 2 below). Diameter error in mm, thickness free; R = refused:

| scene | dots on, true poses | dots on, tracked | dots off, true poses | dots off, tracked | today's hole tool (Ø25) |
|---|---|---|---|---|---|
| plates 120 mm up, 3 seeds (one lost tracking) | -0.06 to +0.00 | -0.06 to -0.01 | -0.12 to -0.02 | -0.09 to -0.02 | -1.4 to -1.6 true, -2.9 to -6.6 tracked; nothing for Ø6, Ø10 |
| plates 10 mm up, 3 seeds | Ø25 -0.04 to -0.01, Ø6 -0.08 to -0.01, Ø10 R | Ø25, Ø6 within 0.04, Ø10 R | -0.12 to -0.01 | -0.08 to -0.01 | +0.58 to +0.75 true, -0.20 to +0.47 tracked |
| car panel, 5 seeds | -0.02 to +0.00 | -0.02 to +0.00 | -0.03 to -0.02 | -0.03 to -0.00 | -1.1 to -1.7 true, -1.0 to -1.4 tracked |
| 1 mm plate, 10 mm up | Ø25 -0.11 to 0.00, **Ø10 R / R / +0.49**, Ø6 +0.05 / R / -0.07 | Ø25 within 0.07, Ø6 +0.05 / -0.09 / R, Ø10 R | -0.13 to -0.05 | -0.13 to -0.04, one R | |
| 1 mm plate, 120 mm up | -0.13 to +0.01 | R; seed 2 Ø6 **+18.88** | -0.12 to -0.04 | R; seed 2 Ø6 **+18.93** | |
| 5 mm plate, 10 mm up | within 0.07, seed 2 Ø10 +0.21, one R | within 0.10, five R | -0.08 to +0.00 | -0.06 to +0.01 | |
| 5 mm plate, 120 mm up | -0.11 to +0.07, one R | seed 2 within 0.12, the rest R | -0.10 to -0.01 | seed 2 within 0.09, the rest R | |
| 3 mm plate over a bright floor | R on all 9 holes | R | R on all 9 holes | R | |

| | holes answered | within 0.2 mm | within 0.1 mm | twice the ± covers (clicks) |
|---|---|---|---|---|
| dots off, true poses | 59 of 68 | 59 | 53 | 177 of 177 |
| dots off, tracked | 44 | 43 | 41 | 125 of 128 |
| dots off, true poses, thickness given | 59 | 59 | 57 | 177 of 177 |
| dots on, true poses | 51 | 49 | 44 | 145 of 145 |
| dots on, tracked | 33 | 32 | 30 | 88 of 91 |
| dots on, true poses, thickness given | 51 | 49 | 49 | 148 of 149 |

- **Dots off: alive.** Every answered hole at true poses is within 0.2 mm, the bright floor is refused, and no
  rule is broken.
- **Dots on: dropped, by one case.** The 1 mm plate 10 mm above its floor, seed 2: Ø10 +0.49 mm on two answered
  clicks (their own ± 0.41 and 0.50). Every other Ø25 and Ø10 it answers is within 0.21 mm.
- **Dots on with a far background is E-D3's case and holds.** On the plates 120 mm up and the panel, every hole
  answered is within 0.13 mm at true poses and within 0.06 mm with the graphics card's tracking, Ø6 and Ø10
  included, where today's tool reads the Ø25 1.0 to 6.6 mm small.
- **A wrong answer the rule does not count, in both arms.** The 1 mm plate 120 mm up, seed 2, tracked on the
  processor's path (the path had slid; its check warns): the Ø6 click answers +18.9 mm on all three clicks, its
  centre 8.6 mm off: it read the Ø25. It carried the path warning and "position unsure", nothing about the
  diameter. That the slid path carried the click onto the Ø25 is inferred, not traced.
- The processor's path slides on most study plates (jumps of 7 to 23 mm), so their tracked readings are mostly
  refused. The study plates were not tracked on the graphics card.

**Position** (the hole's centre): 0.01 to 0.16 mm at true poses; 0.13 to 0.96 mm with the graphics card's
tracking; 0.2 to 1.7 mm with the processor's. The reading says "position unsure" when the frames put the centre
more than 1 mm apart while their diameters agree.

**The wall's height found** (dots off; true 3 / 1 / 5 mm): 2.8 to 3.1; 0.9 to 2.4, but 2.3 to 9 for the Ø10 near
a floor; 4.3 to 5.1.

**What each change of round 3 did** (the same frames, true poses):
- A seed brighter or darker than the face: no effect by itself; the bright floor is refused either way.
- The rule on the frames' centres (refuse above 3 mm apart, or above 1 mm apart when the diameters also scatter
  by more than 0.3 mm): 21 refusals, all on scans tracked on the processor's path. Among them wrong readings of
  +0.47, +0.48, -2.81 and +0.46, and some good ones (-0.04, -0.06). Round 2's quiet -4.65 on the bright floor is
  now a refusal.
- The two cameras held to agree on the top rim within 0.15 mm (dots on): mended nothing. The near-floor Ø10 that
  drops the arm reads +0.33 without it and +0.49 with it; two readings of +0.08 and -0.14 are refused with it.
- The thickness given: 57 of 59 within 0.1 mm instead of 53 (dots off); no help for the near-floor Ø10 with the
  dots on (+0.46, +0.53).

**Dots-off frames left out of tracking** (the processor's path): camera-path error, median / largest, with all
frames and without the dots-off ones: 6.5 / 10.9 and 4.0 / 11.1 mm (plate 120 mm up, seed 2); 3.1 / 5.4 and
4.3 / 7.0 mm (plate 10 mm up, seed 0). No clear change beside that path's own errors; the diameters agree within
0.02 mm either way. Not run on the graphics card.

**Rounds 1 and 2.** One edge per radial line (round 1): over open space every Ø25 and Ø10 within 0.2 mm, but the
plate 10 mm above a lit floor read -0.57 to -0.63 in silence: dropped. A model of the hole per frame (round 2):
dots on dropped by a near-floor Ø10 (+0.53); with dots-off frames every hole of the recordings and of the 1 and
5 mm plates within 0.2 mm, dropped by one bright-floor click (-4.65). Motion blur of 0 / 0.3 / 1 / 3 px (dots
off): Ø25 -0.03 to -0.05, Ø10 -0.04 to -0.07, Ø6 -0.07 to -0.14.

Limits: simulated images (no gloss, chamfer, burr, MJPEG; the blur and the light are the simulator's); bores
square to the face only; a bright background is refused, never read; the reading is not checked against the mesh
(the wrong-hole case); near and far backgrounds are not told apart by the tool; about 20 s a click on this
processor. Dots-off frames need hardware the scanner does not have: the board switches the projector by a
serial command, not in step with the cameras.

### Rounds 4 and 5: a check against the mesh and a gate for near backgrounds (2026-11-24): neither holds yet

Two guards were added so that the dots-on reading could be offered where it is good: a check that the rim read
is the mesh's own hole or dent at the click, and a gate that answers with the dots on only when nothing lies
close below the face inside the hole. Rules as before, and two more, fixed beforehand: no answer anywhere that is
a hole other than the clicked one; and, so that a guard refusing everything cannot pass, at least 90 % of the
Ø25 and Ø10 holes over a far background answered at true poses ("useful"). The click is now made where the scan's
own poses put the rim, on the mesh, as a user's is. Rows and summaries: `holes_looks\summary4_cpu.txt`,
`summary5_cpu.txt`, `summary5_gpu.txt`, `table5.txt`.

**The wrong-hole answer of round 3, traced** (`trace_wrong_hole.txt`): that scan's tracking on the processor's
path had slid by 95 mm in the median; from frame 92 to 172 the Ø6's true position, put into each frame with the
tracked pose, landed 82 to 87 mm away, on the Ø25's rim (the holes are 75 mm apart), and those frames read the
Ø25 cleanly and agreed. The study had clicked at the true position on a scan that was elsewhere; a user clicks
on the mesh.

| | round 4 | round 5 (the mesh's triangles drawn into the finder's cells; every depth point counted in the gate) |
|---|---|---|
| dots on, gated: verdict | alive, not useful | **dropped**: two Ø10 holes 10 mm above a floor pass the gate and read +0.35 and +0.32 mm |
| dots on: far-background Ø25 and Ø10 answered at true poses | 10 of 23 | the panel answers now; 34 holes answered in all, 31 within 0.2 mm |
| dots off with the mesh check: verdict | alive, not useful | alive, not useful |
| dots off: far-background Ø25 and Ø10 answered at true poses | 19 of 23 | 19 of 23 |
| dots off: holes answered at true poses, all within 0.2 mm | 44 (43 within 0.1) | 43 (42 within 0.1) |
| a hole other than the clicked one named | none | none |

- **Every answer the dots-off arm gives is right,** and twice the ± covers every answered click in both arms.
- **The mesh check refuses what it should pass.** Round 5, dots off, the processor's part: 102 refusals, none of
  a reading that was wrong. A small hole that the mesh closed is only a shallow dent in it (Ø6 on a true-pose
  mesh: 0.5 mm deep at the centre), which the finder does not take for a hole. On the round-3 kind of click it
  refuses all three wrong-hole readings.
- **The gate does not keep the near floors out.** Where it refused (36 times in round 5) the reading would have
  been within 0.3 mm every time, and the two readings that drop the arm passed it.
- **The two readings that drop the arm carry a wide ± of their own** (0.26 to 0.27 mm and 0.21 mm), as round 3's
  did (0.41 and 0.50), where the good readings carry 0.10 to 0.18. Not a rule yet: seen on the rows, to be tested
  on scans not looked at.
- **Study plates tracked on the graphics card** (15 scans, the app's chain): the plates 10 mm above the floor
  track within 0.46 to 0.67 mm in the median; the plates 120 mm up and over the bright floor mostly lose
  tracking (1.1 to 636 mm), as one of the scorecard's did. Both arms alive there, few holes answered: dots off 18
  (all within 0.2 mm), dots on 9 (all within 0.2).

Not built or run: a scene with a second hole a few millimetres beside the clicked one, the case the check
against the mesh exists for; a rule on the reading's own ±; the blurred-step edge model.

### Round 6: the reading refuses on its own ± (2026-11-24): passes on scans not looked at before

The gate and the mesh finder are out of the judged reading. In their place, fixed before any round-6 result:

- **A refusal on the reading's own ±, above 0.20 mm.** The limit comes from every reading of rounds 3 to 5 that
  the earlier rules answered (1,774 readings; `holes_looks\NOTES.md` has both distributions): of the 1,758 right
  ones 1,733 carry 0.20 mm or less; of the 16 wrong ones 10 carry more, and the other 6 are the wrong-hole
  readings of the slid scan, which the next test is for.
- **The read circle against the click:** the click must lie inside the read circle or within 3.0 mm of its rim.
- The seed takes the dark or bright patch nearest the click, not the largest within reach (decided from geometry:
  beside a larger neighbour the largest would be the wrong hole).

**Fresh data, judged by the same rules** (true poses, seeds 3 to 5, none looked at before; only the frames that
are read were rendered): plates of 1, 3 and 5 mm at 10 mm above the floor, plates of 1 and 5 mm at 120 mm, and
a new plate with neighbours (a Ø10 with a Ø6 beside it and a Ø25 with a Ø10 beside it, 3 mm rim to rim) at both
heights, clicked on the side facing the neighbour, on the far side and on the bridge between the two.

| | dots on | dots off |
|---|---|---|
| verdict | **alive and useful** | **alive and useful** |
| far-background Ø25 and Ø10 answered | 21 of 21 | 21 of 21 |
| holes near a floor answered, none more than 0.3 mm off | 36 of 39 | 39 of 39 |
| all holes answered / within 0.2 mm / within 0.1 mm | 66 of 69 / 66 / 63 | 69 of 69 / 69 / 68 |
| twice the ± covers the error (answered clicks) | 173 of 173 | 195 of 195 |
| a hole other than the clicked one read | none | none |

Median diameter error in mm per family, over the answered clicks (in brackets, clicks answered of 9 where not
all):

| family | dots on: Ø25 / Ø10 / Ø6 | dots off: Ø25 / Ø10 / Ø6 |
|---|---|---|
| 1 mm plate, 10 mm up | -0.07 / -0.04 (4) / -0.03 (7) | -0.08 / -0.07 / -0.08 |
| 1 mm plate, 120 mm up | -0.07 / -0.07 / -0.05 | -0.08 / -0.06 / -0.09 |
| 3 mm plate, 10 mm up | -0.02 / +0.02 (3) / -0.02 | -0.03 / -0.02 / -0.05 |
| 5 mm plate, 10 mm up | -0.01 / +0.00 (7) / -0.03 (7) | -0.01 / -0.01 / -0.05 |
| 5 mm plate, 120 mm up | -0.02 / -0.02 / -0.03 | -0.01 / -0.04 / -0.06 |
| neighbours, 120 mm up (Ø25 / its Ø10 / Ø10 / its Ø6) | -0.02 / -0.07 / -0.05 / -0.05 | -0.03 / -0.06 / -0.04 / -0.06 |
| neighbours, 10 mm up | -0.04 / -0.04 (7 of 9) / -0.08 / +0.05 (3 of 6) | -0.03 / -0.06 / -0.05 / -0.04 |

- **Every answered click is within 0.14 mm** (the worst: -0.136, -0.133, +0.121).
- **Beside a neighbour 3 mm away every answered click read its own hole** (dots off 60 of 60 clicks; dots on 55,
  5 refused); the test of the circle against the click never had to act.
- **Near a floor with the dots on the Ø10 is answered by fewer clicks** (3 to 7 of 9): the refusals are where
  rounds 3 and 5 gave their wrong answers.
- Refusals on the fresh data, dots on: the ± rule 5 (1 of a reading more than 0.3 mm off, 4 of readings that
  were right), the earlier rules 17 (6 and 11). Dots off: none.

**The 11 scorecard recordings again** (reported apart: they informed the limit; clicks on the scan's mesh):

| | dots on, true poses | dots on, tracked | dots off, true poses | dots off, tracked |
|---|---|---|---|---|
| holes answered of 23 | 21 | 21 | 23 | 23 |
| within 0.2 mm / within 0.1 mm | 21 / 21 | 21 / 20 | 23 / 23 | 23 / 23 |

The two refused with the dots on are near-floor Ø10 holes. The plates 120 mm up and the car panel read -0.08 to
+0.00 mm at true and at tracked poses alike, the scan that lost tracking included (its position is marked
unsure).

Limits: simulated images throughout; no real hole has been measured (gloss, chamfers, burrs, MJPEG, real light
and blur). The fresh plates are at true poses only; tracked poses are tested on the recordings, which informed
the limit. A scan that lost its path still loses the hole's position. A bright background is refused, never
read. Holes closer than 3 mm rim to rim, and bores not square to the face, were not tried. About 12 s a click on
this processor.

## The tag pose held, built and tested with tags rendered into both scans (ENGINE_PLAN X19, 2026-11-24): a setting, off; its stop rule passed

`AlignParams.hold_tags`, set by `[process] align_hold_tags` (off). An alignment that started from tags and whose
refined pose shares under 0.70 keeps the tags' pose when 3 or more shared tags agree within 0.5 mm; the shapes
overrule it only beyond the larger of 8 mm and 4 sigma of the tag pose. Added in review: the hold weighs only a
pose that the refinement from the tags accepted, so a refusal never becomes an acceptance; tags lying on the
table (within 3 mm of the target scan's table plane) do not hold a part that was moved between the scans; a held
link's weight among several scans is built in the same frame as a shape link's; a merge at a held pose records it
as information, not among its warnings.

**The test** (`tools/merge_combine_study.py`, commands `x19-render`, `x19-single`, `x19-pair`, `x19-judge`; data in
`D:\OpenHandScan-work\scratch\ed3\x19run`). Each pair is two 90-frame stretches of the scorecard's own orbits,
rendered with eight 25 mm tags on the table (the part shadows and hides them), meshed on the graphics card, the
tags found as the server finds them, aligned through `align_with_tags` with the part not moved, and merged with
`merge_scans`. The stop rule, fixed beforehand: the held merge passes E-D4 (1) and (3) on the three bracket pairs
that share under 0.70 (three seed pairs); the pair that shares 96 % is the same with the hold on and off, bit for
bit; where a tag was moved 2 mm between the scans it is left out or the hold overruled, never followed. **Passed.**

Columns: how far the merge's pose lies from the true motion (median / 95th percentile, mm); then, over all the
surface either scan saw, the share within 0.5 mm / the median in mm / the percentage more than 0.3 mm off.

| pair | merge at | pose from the true motion | all seen surface | E-D4 (1) / (3) |
|---|---|---|---|---|
| T3, bracket, opposite sides (shares 0.45) | the pose without the hold | 0.81 / 1.29 | 0.930 / 0.188 / 35.8 | fails / passes |
| | **the held tag pose** | 0.17 / 0.34 | 0.955 / 0.062 / 11.4 | **passes / passes** |
| | the true motion | 0 | 0.955 / 0.063 / 11.4 | passes / passes |
| T1, bracket, other seeds | without the hold | 0.87 / 1.42 | 0.940 / 0.155 / 28.0 | fails / passes |
| | **held** | 0.43 / 0.72 | 0.953 / 0.064 / 11.1 | **passes / passes** |
| | the true motion | 0 | 0.952 / 0.063 / 11.3 | passes / passes |
| T3b, bracket, other seeds | without the hold | 0.72 / 1.00 | 0.945 / 0.132 / 20.4 | fails / passes |
| | **held** | 0.19 / 0.37 | 0.952 / 0.057 / 10.3 | **passes / passes** |
| | the true motion | 0 | 0.952 / 0.057 / 10.2 | passes / passes |
| TS, sheet (reported, not in the rule) | without the hold | 0.86 / 1.41 | 0.914 / 0.229 / 33.4 | fails / passes |
| | held | 0.41 / 0.63 | 0.925 / 0.163 / 21.8 | passes / passes |
| | the true motion | 0 | 0.925 / 0.151 / 20.9 | passes / passes |
| TM, T3 with tag 2 moved 2 mm in one scan | without the hold (all 8 tags, agreeing within 0.68 mm) | 0.79 / 1.10 | 0.928 / 0.191 / 37.0 | fails / passes |
| | held from the other 7 tags | 0.18 / 0.36 | 0.954 / 0.061 / 11.3 | passes / passes |
| TH, one side scanned twice (shares 0.96) | hold on and off: the same result | 0.29 / 0.47 | 0.855 / 0.087 / 19.5 | passes / passes |

- **Without the hold the alignment already starts from the tags, and still ends 0.7 to 0.9 mm from the true
  motion:** the refinement from the shapes walks there. Held, it stays 0.17 to 0.43 mm away and the merge is the
  true motion's.
- The tags' corners agree within 0.12 to 0.22 mm; the tag pose's own sigma is 0.06 to 0.12 mm. Twice the merged
  sigma covers 0.985 to 0.996 at the held poses.
- **The sheet's thin features** (mm too thick; single scans +0.39 and +0.64 on the 2 mm flange, +0.52 and +0.61
  on the 1 mm rib): flange +0.56 without the hold, +0.36 held, +0.32 at the true motion; rib +0.75, +0.79, +0.76.
  The rib is thicker in every merge than in either scan: the merge's known thickening, at the true motion too.
  The bracket has no feature of 2 mm or less.
- What a user would read, held: "Aligned from the 8 tags both scans saw (their corners agree within 0.21 mm). The
  scans share little of the part (42 %), so the tags decide the position; the shapes alone would put it 0.8 mm
  away."
- Tests: 12 for the hold (the engine's 10 and the server's 2), among them a setting-off result identical to
  before, a refusal that stays a refusal, a plate slid on its table whose table tags do not hold it, a tag map
  9 mm wrong overruled.

Not tested with rendered scans: a part turned over with tags on the part itself; a tag map wrong as a whole
(made-up clouds only: refused at 20 mm, overruled at 9 mm). Three seed pairs of one part and one sheet pair, all
with the part not moved and the tags on the table, 9 s scans. The spread used by E-D4 is taken from last night's
cases. Printed-tag error, and the calibration error that tags and surface share, are not in the simulation. The
Align page shows nothing of the hold, and the setting was not tried in the running app.

### The part turned over, tags on the part (X19, 2026-11-24): a miss on one of three pairs; the setting stays off

The case the owner asked for before the hold goes on by default (E-D5). The bracket cannot rest upside down, so
it was propped level on a small block, as a user would; scan A upright, scan B turned, `same_setup` false, three
seed pairs; tags found as the server finds them; data in `D:\OpenHandScan-work\scratch\ed3\x19run` (`RUN_T2.md`,
`work\pairs`). The stop rule, fixed beforehand: with the hold on the merge passes E-D4 (1) and (3) on all three
pairs; with the tags left on the table the hold does not act; with the tag map shifted as a whole it is never
followed.

**Where tags can go on a turned part.** A first go with tags on both faces of the upright stopped before any
merge: only 2 tags were seen by both scans. A survey with the scene's own occlusion showed why: turned over, the
upright's inner face lies under the base and is never seen; no other surface is seen both ways up; and the
orbit sees the upright's outer face only at a slant, where a tag is found in about half the frames. The second
go used five 20 mm tags in two rows on the outer face; 3 or 4 of them were shared in each pair.

| pair | hold off | hold on |
|---|---|---|
| R1 | passes (1) and (3) | **held** (4 tags, sigma 0.13 mm), passes; the pose's error at the tags 1.21 to 0.49 mm |
| R2 | passes | **held** (3 tags, sigma 0.15 mm), passes; 1.04 to 0.31 mm |
| R3 | passes | **overruled, fails both**: the shapes' own search took a pose with the part turned round, 75.5 mm away ("3 of 9 starting guesses agree"), and the hold gave way to it |

- **The miss is the overrule's.** To decide whether the tags are wrong as a whole, the hold compares them with a
  search from the shapes alone. That search can accept the very pose tags exist to rule out (a part that fits
  about as well turned round), and it then overrules a tag start that was right and accepted. With the setting
  off the same pair passes: the hold made it worse.
- **Tags left on the table, the part moved** (three pairs): the hold did not act; hold on and off give the same
  result (two refused as "not sure which way it goes", one accepted at a wrong pose both ways, as today).
- **The tag map of one scan shifted as a whole:** by 20 mm, refused with the hold on and off, never followed.
  By 9 mm: the hold did not hold the shifted pose (the shapes moved it 8.7 mm, over the limit), and what came
  back is the setting-off result, 4.4 mm RMS from the truth, but under the words "Aligned from the shapes",
  which is not true of a refinement that started at the shifted tags.
- Two rows of tags show no weakness about the rows' axis (the held pose turns -0.18 and -0.13 degrees about it,
  as without the hold). The reference itself is good to about 0.4 degrees here, so smaller turns are not
  resolved.

**So the setting stays off.** Next: the tags are judged against the refinement that starts from them, not
against a search from the shapes alone; then these alignments are run again.

Limits: one seed pair a case, three pairs; 20 mm tags, found under the simulator's light and dots; the bracket
only.

## The wall-thickness reading in the app (ENGINE_PLAN X16, 2026-11-24 and 26): in, with one-sided words

The owner's decision E-D5: a thin wall's thickness read from the scan's camera frames goes into the app now,
worded one-sided, as a stopgap. `ohs/measure/wall.py` (the reading) and `ohs/measure/frame_depth.py` (matcher depth
near a click for a handful of recorded frames, which a hole reading can share); `GET` and `POST
.../scans/{sid}/wall`; the viewer's Fit tool has a fourth choice, "Wall" (T). The stop rule, fixed in X16: through
the server's route, on the scorecard's standing walls and sheet (3 seeds each, the app's default Make mesh,
tracked), the reading is nearer the truth than the mesh's own thickness on every wall of 2 mm or less, refuses
where one side is not seen, and never reads thinner than the truth by more than 0.1 mm. **Met on the graphics
card's path.** Data: `D:\OpenHandScan-work\scratch\ed3\walls_app` (`readings_gpu.json`, `readings_cpu.json`).

Error in mm (the value minus the truth), the mesh's thickness read at the same click:

| scan | wall (truth) | the mesh | "at most" | the raw reading | a click takes |
|---|---|---|---|---|---|
| walls, seed 0 | 0.5 / 1 / 2 | +1.51 / +0.73 / +0.95 | +0.68 / +0.63 / +0.61 | +0.58 / +0.52 / +0.49 | 11.5 to 12.2 s |
| walls, seed 1 | 0.5 / 1 / 2 | +1.34 / +0.78 / +0.54 | +0.58 / +0.54 / +0.42 | +0.50 / +0.47 / +0.33 | 12.8 to 15.9 s |
| walls, seed 2 | 0.5 / 1 / 2 | +1.67 / +0.90 / +0.49 | +0.75 / +0.62 / +0.38 | +0.63 / +0.52 / +0.27 | 14.5 to 15.6 s |
| sheet, seed 0 | flange 2 / rib 1 / rib 3 | +0.51 / +0.96 / +0.53 | +0.43 / +0.88 / +0.78 | +0.36 / +0.71 / +0.64 | 16.5 s |
| sheet, seed 1 | flange 2 / rib 1 / rib 3 | +0.17 / +0.68 / +0.19 | +0.07 / +0.40 / +0.25 | -0.02 / +0.32 / +0.16 | 15.3 to 17.2 s |
| sheet, seed 2 | flange 2 / rib 1 / rib 3 | +0.52 / +1.24 / +0.73 | +0.40 / +1.14 / +1.00 | +0.32 / +1.00 / +0.84 | 15.9 to 17.4 s |

- **On all 15 walls of 2 mm or less the "at most" value is nearer the truth than the mesh** (+0.07 to +1.14 mm
  against +0.17 to +1.67), and it is never under the truth (the raw reading at its lowest -0.02 mm; the shown value
  adds twice the reading's own scatter, at least 0.05 mm).
- **One side not seen: refused in words** on the sheet's foot on all three seeds ("No wall seen from both sides at
  the click").
- The 3 mm rib (outside the rule) reads worse than the mesh on all three seeds; the reading is made for walls up
  to about 3 mm.
- **Without a graphics card it is not offered:** with the processor's matcher on the same meshes the "at most"
  value was no nearer than the mesh on 5 walls (0.01 to 0.05 mm above it).
- **Not offered** either for a scan tracked by its markers (there the two sides came out 0.10 to 0.47 mm too
  close, so "at most" would be false), for a merged scan, or when the recording, the calibration or the camera
  path is gone.
- What the user reads (the 1 mm wall of seed 0): "At most 1.63 mm thick here (read from 23 camera frames: 14 on
  one side, 9 on the other)." "The scan cannot rule out that the wall is up to about 1 mm thinner: scans place a
  thin wall's two sides slightly too far apart (on simulated walls by 0.3 to 1.0 mm)." "For the real thickness, lay
  the part flat and scan it from above, or measure it with calipers." "The mesh reads 1.73 mm here; the mesh holds
  thin walls thicker than the frames do." Never a ±.
- A click takes 11.5 to 17.4 s, about 10 s of it the matcher on 18 to 26 frames. The matcher runs again on those
  frames; keeping Make mesh's depth instead would cost 7.8 MB a frame for as long as the scan exists.
- Seen in the running app (2026-11-26, the working tree's server on these projects): the Wall choice shows, a
  click on the 2 mm wall of seed 0 gave "At most 2.64 mm" after 10.7 s, a click beside a wall was refused.

Limits: simulated scans; the tracking error stays in the reading (X17), which is why it says "at most"; a scan made
wholly without a graphics card was not tried; a one-sided refusal was seen on the sheet only.

## The tag hold after the overrule was mended (ENGINE_PLAN X19, 2026-11-26): the turned-over case passes; one weakness is known

The hold now judges the tags by the refinement that starts at them, never by a search from the shapes alone:
held when that refinement was accepted and moved the scan no more than the larger of 8 mm and 4 sigma of the
tag pose; otherwise the answer is exactly the one without the hold, pose and numbers, with a sentence that says
the tags did not hold. An alignment with the hold on now costs the same time as with it off (the extra search,
4.5 to 7 s on these scans, is gone). Every alignment was run again on the saved scans on the processor
(`x19run\rerun_fix.json`); the one new held pose was merged and judged on the graphics card.

| pair | hold off | hold on, before | hold on, now |
|---|---|---|---|
| R1, R2 (turned over, tags on the part) | accepted | held | held, the same poses (the refinement moved 0.76 and 0.80 mm) |
| R3 (turned over) | accepted, passes | gave way to the part turned round, 75.5 mm away; fails | **held** (3 tags, moved 0.67 mm); the merge passes E-D4 (1) and (3) |
| Q1 to Q3 (tags on the table, the part moved) | refused, refused, accepted | not weighed | not weighed |
| R1, tag map shifted 20 mm | refused | refused | refused |
| R1, tag map shifted 9 mm | accepted | not held, under words that were not true | not held (moved 8.7 mm): the hold-off pose bit for bit, in true words |
| the first test's T3, T1, T3b, TS, TM | accepted | held | held, the same poses (TM again without its moved tag) |
| TH (shares 0.96) | accepted | untouched | untouched |

R3 merged at the held pose (the share within 0.5 mm / the median in mm / the percentage more than 0.3 mm off):
scan A's own surface 0.940 / 0.079 / 11.1, scan B's 0.987 / 0.068 / 5.1, all seen 0.960 / 0.072 / 8.3; with
the hold off 0.939 / 0.077 / 10.9, 0.979 / 0.098 / 6.9, 0.955 / 0.096 / 9.5; at the true pose 0.960 / 0.056 /
8.3 over all. So the stop rule's three parts hold: the turned pairs pass with the hold on, table tags never hold
a moved part, and a shifted tag map is never the held pose where it was tried.

- **On the turned-over pairs the hold is no nearer the truth than the setting off** (R3: 0.71 mm RMS at the tags
  against 0.69, 1.23 mm at scan B's middle against 0.60): tags and shapes agree within 0.7 mm there and the
  reference itself is good to about 0.4 degrees. The hold's gain is on pairs that share little with the part
  not moved (the first test: median 0.057 to 0.064 mm against 0.132 to 0.188).
- **A known weakness, kept as a test that is expected to fail** (`test_a_tag_map_wrong_as_a_whole_is_overruled_in_words`):
  on a made-up part a tag map wrong by 9 mm is held, because the refinement from the wrong tags moved the scan
  back only 7.9 mm, under the 8 mm limit. With the hold off that pair is not right either (the refinement starts
  at the same wrong tags), but the hold then fixes the pose at the tags. In the rendered case the same shift was
  not held, by 0.7 mm. A candidate guard, measured and not built: the overlap at the tags' pose against the
  overlap where the refinement ended is 0.94 to 0.99 on the held pairs here and 0.41 and 0.002 on the two wrong
  maps.
- With tags on the table of a moved part, two of the three pairs gave different poses from run to run with the
  hold off and on alike: the search from the shapes alone is not repeatable there (it draws at random). The hold
  is not involved.

Not run: the app with the mended hold; more parts than the bracket and the sheet.

## The tag hold: a guard on overlap measured and not built; the limit lowered from 8 to 2 mm (ENGINE_PLAN X19, 2026-11-26)

The one known weakness of the hold (a tag map wrong by 9 mm held, because the refinement from it moved the scan
only 7.9 mm) was to be closed by a guard: do not hold when the overlap at the tags' pose is small against the
overlap where the refinement from them ended. Before building it, 153 alignments were run again on the processor
from saved scans with the hold on and off (`D:\OpenHandScan-work\scratch\ed3\x19guard`, `table_step1.json`): the
three turned-over pairs, the three table-tag pairs with the part moved, the first test's six pairs, the 31
emulated-tag merges, and tag maps shifted as a whole by 3 to 15 mm along and across the tags' face on five
rendered pairs and on made-up parts. In 122 of them the hold was weighed. The rule for the guard's limit was fixed
beforehand: the lowest ratio of a right map and the highest of a map wrong by more than 8 mm must lie a factor 1.3
apart.

**The overlap ratio does not separate; no guard was built.** Right maps: 0.64 (the bent sheet scans) to 1.00. Maps
wrong by 9 to 15 mm, slid along the face the tags lie on: 1.00, held at the full shift. The setting off is as
wrong there, within 0.01 mm: the faces the two scans share do not pin a slide along them, so the refinement
keeps the tags' start whatever the setting. No test on the shapes can see such a slide.

**What the numbers did show: the limit of 8 mm was far too wide.**

| the limit | right maps held (of 45) | wrong maps (shifted 3 mm or more) held worse than the setting off (of 77) | the worst of them |
|---|---|---|---|
| 8 mm (until now) | 45 | 33 | 6.4 mm worse |
| 3 mm | 45 | 3 | 2.4 mm worse |
| **2 mm (now)** | 45 | 0 | none |
| 1.5 mm | 38 (the bent sheets lose the hold) | 0 | none |
| 1 mm | 35 | 0 | none |

The refinement from right tags moved a scan 1.76 mm at most (the bent sheets; 0.8 mm at most on the rendered
pairs); every hold that came out worse than the setting off had moved it 2.25 mm or more. Those two lie a factor
1.28 apart, a hair under the 1.3 that had been fixed for the overlap guard; the limit was lowered all the same,
to the side where a mistake is harmless: a right map that is not held gets exactly today's answer. With 8 mm, tags on the
table and the part nudged 3 to 7 mm along the table were held at 3.0 to 7.1 mm where the setting off gives 0.7 to
0.8 mm. `AlignParams.overrule_mm` is 2.0 now; not held is still exactly the setting-off answer, in the words "The
tags did not hold this position ...". The test that was expected to fail passes (its one line that named the old
limit names the limit); a new test holds 3, 5 and 7 mm. The three turned-over pairs (moved 0.67 to 0.80 mm) and
every pair held before stay held at the same poses. An alignment with the hold on costs what it costs off
(240 s against 238 s over 117 alignments).

**What stays, measured with the 2 mm limit on maps wrong by 1 to 2.5 mm** (`small_shifts.json`, 68 alignments):

- **A tag map wrong by less than about 2.5 mm is held at its error.** Tags on the table, the map shifted along
  the table: held at 1.0 / 1.5 / 2.0 / 2.5 mm where the setting off gives 0.78 (T3); 1.1 / 1.6 / 2.1 mm against
  0.66, and not held at 2.5 mm (T1). Shifted across the face: not held from 1.5 or 2 mm on. The cause is plain in
  the rows: on pairs that share little the shapes' own minimum lies 0.7 to 1.2 mm from the truth (that is why the
  hold exists), the move is the distance from the tags' pose to that minimum, and so a right map (moved 0.76 mm)
  and one wrong by 1.5 mm the other way (moved 0.75 mm) look alike. No limit on the move can do better than about
  the limit plus that distance.
- **A map slid along a direction the shared faces do not pin is held at any shift**, and the setting off is
  equally wrong (the turned-over pairs, tags on the part's one shared face: 3 to 15 mm). For tags on the part this
  is not a case a user can produce (the tags move with the part); it would take every tag stuck on again
  elsewhere.
- **The gain the hold is for, on right maps:** the pose 0.06 to 0.11 mm RMS from the truth against 0.38 to 0.79
  with the setting off (the first test's five pairs), 0.3 to 0.4 against 0.9 to 1.1 on two turned-over
  pairs, 0.88 against 0.68 on the third (the reference there is good to about 0.4 degrees).

So the trade for the default is now known: with the hold on, a pair that shares little comes out 0.3 to 0.7 mm
nearer when nothing moved, and up to about 2 mm worse than today when the part was nudged by 1 to 2.5 mm against
tags on the table without the user saying so. The setting stays off; the choice is the owner's.

Limits: the processor path only (the alignment is the same code on both); simulated scans; the bent sheets'
"truth" is the fit to all tracked frames, itself 2.5 to 2.7 mm from the tags' pose; the direction "along" for
table tags is the tag ring's first principal axis; shifts stand in for a nudged part or a moved table, turns of
the tag map were not tried.

## Holes from the camera images in the app (ENGINE_PLAN X15 step 2, 2026-11-26): in the Hole tool, as a second answer

The owner's decision E-D5: the hole reading from the camera images goes into the app's Hole tool beside the mesh's
answer. `ohs/measure/rim.py` (the reading, step 1) through `GET` and `POST .../scans/{sid}/hole-images`; the shared
frame provider (`ohs/measure/frame_depth.py`) gained the raw images and the matcher's depth with its sigma; the
viewer's Hole card shows the mesh's answer, then "From the camera images". The stop rule, fixed beforehand: through
the server's route, on the scorecard's hole recordings made into app projects with the app's default Make mesh
(Standard, tracked, graphics card), three clicks a hole on the face beside it: (a) no answer names a wrong hole;
(b) every answered Ø 25 and Ø 10 within 0.3 mm of the truth, 0.5 mm when the path check warns; (c) at least 90 % of
the far-background Ø 25 and Ø 10 answered; (d) near the floor refused or within 0.3 mm; (e) every answer nearer the
truth than the mesh's own hole fit, or the mesh gives none. **Met**: 81 clicks, 50 answered, the worst answer
0.08 mm off. Data: `D:\OpenHandScan-work\scratch\ed3\holes_app` (`rows_gpu.jsonl`, `rows_cpu.jsonl`).

The three clicks of each hole, in mm (the mesh's hole fit / the camera images with their ± / clicks refused):

| recording | hole | truth | the mesh | the camera images | refused | a click takes |
|---|---|---|---|---|---|---|
| holes-high-s0 (120 mm above the floor) | Ø 25 | 25.00 | 18.39 | 24.99 ± 0.10 | 0 of 3 | 8.3 s |
| | Ø 10 | 10.00 | none | 10.00 to 10.01 ± 0.10 | 0 of 3 | 7.8 s |
| | Ø 6 | 6.00 | none | 5.97 to 5.98 ± 0.11 | 0 of 3 | 6.8 s |
| | blind bore Ø 10 | 10.00 | 9.67, none, none | refused | 3 of 3 | 5.9 s |
| holes-high-s1 | Ø 25 | 25.00 | 22.09 | 24.96 ± 0.10 | 0 of 3 | 8.0 s |
| | Ø 10 | 10.00 | none | 9.94 to 9.96 | 0 of 3 | 8.1 s |
| | Ø 6 | 6.00 | none | 5.92 to 5.95 | 0 of 3 | 7.0 s |
| | blind bore | 10.00 | 9.71, none, 9.64 | refused | 3 of 3 | 5.0 s |
| holes-high-s2 | Ø 25 | 25.00 | 21.95 | 24.95 to 24.96 | 0 of 3 | 8.2 s |
| | Ø 10 | 10.00 | none | 9.98 to 10.03 | 0 of 3 | 8.0 s |
| | Ø 6 | 6.00 | none | 5.93 to 5.98 | 0 of 3 | 6.8 s |
| | blind bore | 10.00 | none, 9.10, 9.23 | refused | 3 of 3 | 5.6 s |
| holes-low-s0 (10 mm above the floor) | Ø 25 | 25.00 | 25.32 to 25.47 | 24.98 to 25.01 | 0 of 3 | 8.0 s |
| | Ø 10 | 10.00 | 10.07 | refused | 3 of 3 | 7.7 s |
| | Ø 6 | 6.00 | none | refused, 5.94, 5.94 | 1 of 3 | 6.9 s |
| | blind bore | 10.00 | 9.52 to 9.68 | refused | 3 of 3 | 5.9 s |
| holes-low-s1 | Ø 25 | 25.00 | 24.52 to 25.33 | 24.92 to 24.94 ± 0.11 | 0 of 3 | 7.9 s |
| | Ø 10 | 10.00 | 9.93 | refused | 3 of 3 | 7.0 s |
| | Ø 6 | 6.00 | none | refused | 3 of 3 | 7.0 s |
| | blind bore | 10.00 | 9.21 to 9.57 | refused | 3 of 3 | 5.4 s |
| holes-low-s2 | Ø 25 | 25.00 | 24.90 to 25.38 | 24.95 to 24.96 ± 0.11 | 0 of 3 | 7.9 s |
| | Ø 10 | 10.00 | 9.80 | refused | 3 of 3 | 7.8 s |
| | Ø 6 | 6.00 | none | 6.01 to 6.07 ± 0.11 to 0.13 | 0 of 3 | 6.7 s |
| | blind bore | 10.00 | 8.77 to 9.59 | refused | 3 of 3 | 5.7 s |
| panel200-s0 (the car panel) | Ø 25 | 25.20 | 24.10 | 25.18 to 25.19 ± 0.10 | 0 of 3 | 8.2 s |
| panel200-s1 | Ø 25 | 25.20 | 24.10 | 25.18 to 25.19 | 0 of 3 | 7.9 s |
| panel200-s2 | Ø 25 | 25.20 | 23.97 | 25.15 to 25.17 | 0 of 3 | 8.3 s |

- **(a)** all 50 answers name the clicked hole. **(b)** every answered Ø 25 and Ø 10 is within 0.08 mm. **(c)** the
  far-background Ø 25 and Ø 10 are answered on 27 of 27 clicks, where the mesh reads the Ø 25 1.1 to 6.6 mm small and
  gives no circle for the Ø 10. **(d)** near the floor every answer is within 0.08 mm; the three Ø 10 there are refused
  on all nine clicks (where the mesh's own fit is within 0.2 mm: near a floor the mesh is the answer). **(e)** no
  answer is further from the truth than the mesh's fit; the mesh gave none on 31 of the 81 clicks.
- Twice the ± covers the error on all 50 answers; the worst ± is 0.13 mm; the worst centre is 0.42 mm from the truth.
- **The blind bore is refused on every click** (too few frames see its rim, or the frames disagree by 1.3 to 6 mm):
  never a wrong number. The study did not read it either.
- A click takes 4.6 to 10.5 s here; the frames are kept, so a second click on the same hole is quicker.
- **With the processor's matcher on the same meshes and paths the rule is met too** (52 of 81 answered, the worst
  0.09 mm). It is still **not offered without a graphics card**, because a scan tracked and meshed wholly on the
  processor was not run. **Not offered** either, in words, for a merged scan, a scan tracked by markers, a scan made
  at Preview quality, a recording under 1280 pixels wide (each not measured), or when the recording, the
  calibration or the camera path is gone.
- What the user reads: "Ø 24.99 mm ± 0.10 from the camera images (the rim where the face ends, read in 18 frames)."
  "This is the hole's edge in the clicked face, where the face ends: not the bore further down, not the outer edge
  of a chamfer or countersink, not the smallest gap through the hole." And, when the path check warns or the frames
  place the centre apart: "The hole's position may be off (...); its diameter does not depend on the camera path."
  A refusal shows no number: "No diameter from the camera images: the frames disagree on the diameter by 1.9 mm."
  with what to do.
- Seen in the running app (2026-11-26, the working tree's server on these projects, holes-high-s0): a click beside the Ø 25 hole gave "Ø 18.39 mm ± 0.22 from the mesh" and, 7.8 s later, "Ø 24.99 mm ± 0.10 from the camera images (... read in 19 frames)"; a click right beside the Ø 6 hole gave "The mesh gives no circle here" and "Ø 5.95 mm ± 0.10 from the camera images"; a click several millimetres out from that hole's edge was refused quietly ("No diameter from the camera images: the frames disagree on the diameter by 2.0 mm."). The card can cover the hole that was clicked.

Limits: simulated scans (no gloss, chamfer, burr, MJPEG artefacts or real light); the projector's dots on; clicks
1.5 mm outside the rim; one part per scene. The reading is not kept with the scan.

## The tag hold offered per alignment (ENGINE_PLAN X19, 2026-11-27): off by default, a button on the Align page

The owner's choice after the weakness was measured (the section before last): `[process] align_hold_tags` stays off,
and the Align page offers the hold for one alignment at a time. No scan result changes by default; this is a route
field, a block of words and two buttons.

- **Routes.** `hold_tags` in the bodies of `align` and `align_all` overrides the setting both ways for that
  alignment. Without it and with the setting off both responses are as before, field for field, apart from the new
  `hold` block (12 tests in `tests/test_server_align_hold_offer.py`, 7 through the real server and 5 fast; the guard
  tests were seen failing first against a stub). A pair aligned again without the hold forgets its earlier held pose.
- **Tests run in review:** the alignment, merge-preset, settings and command-line test files: 139 passed on the
  processor (without the merge-preset file), 142 with the graphics card. UI: typecheck, the 87 tests, build.
- **The 1.0 mm caution on a held pose, counted on the guard study's alignments at the 2 mm limit (no new runs):**

| held alignments | number | the caution shows | the shapes' move, mm |
|---|---|---|---|
| right tag maps | 45 | 10 (emulated: the bent sheet pair P5 and its spread pairs 1.27 to 1.75, P3s1 1.11, M1s2 1.02) | 0.32 to 1.75 |
| the part nudged 1 to 2.5 mm against table tags | 38 | 17 (across the tags' face 7 of 7, along it 10 of 29) | 0.23 to 1.79 |
| of those, held worse than off by more than 0.05 mm | 23 | 14; the 9 without it are nudges of 1 and 1.5 mm along the face | 0.23 to 0.81 without it |
| slid 3 to 15 mm along a direction the shared faces do not pin | 33 | 0 (the setting off is as wrong there) | 0.00 to 0.63 |

  So the caution advises and does not separate: it shows on 22 % of right holds and misses 9 of 23 harmful small
  nudges. The limit was left at 1.0 mm; the words claim no more ("A small disagreement is normal where two scans
  share little, but it is also what a part nudged against tags on the table looks like."), the offer's sentence
  carries the condition (no tag moved against the part), and the guide says not to rely on the caution alone.
- **Seen in the running app (2026-11-27, the working tree's server on the rendered bracket pair of the earlier trial,
  "Front (tags)" and "Back (tags)", the part not moved, the setting off):** Align gave "Aligned: 45 % of the smaller
  scan's part lies within 1 mm of the other scan, typically 0.29 mm; from the tags both scans saw." and, inside "The
  scans share little", "Both scans saw tags 0, 1, 2, 3, 4, 5, 6 and 7. Where two scans share little, these tags can
  place the scan more exactly than the shapes, ..." with the button. Pressing it gave "Aligned from the 8 tags both
  scans saw (their corners agree within 0.21 mm). The scans share little of the part (42 %), so the tags decide the
  position; the shapes, starting at the tags, would move it 0.8 mm.", "The tags hold "Back (tags)" on "Front (tags)",
  as asked for this alignment.", no caution (0.8 mm), Start "The tags hold the position". "Let the shapes decide"
  gave the first answer again, with the offer. About 25 s an alignment. Found there and mended: the Shared table row
  of a held result read "Not used: the two scans' tables did not line up"; it reads "Not used: the tags decide the
  position".
- **Whole suite on the commit (0747663, 2026-11-27, from a frozen copy):** with the graphics card 2449 passed,
  7 skipped (49 min); on the processor 2364 passed, 92 skipped (46 min). No graphics driver timeout.
- **The exe, rebuilt from 0747663 (527 MB):** starts in 2 s and serves the page; aligning the two bracket scans
  gives 99 % shared and 0.12 mm as before, and the answer carries the `hold` block (null: no tags there). The
  wall-thickness readings (4 spots) and the hole readings from the images (12 clicks) are the same as the Python
  run's, value for value. The hold itself was not pressed in the exe: it was tried in the working tree's server
  (above).
- **Not done:** not-held and the caution were seen in the demo and in tests, not on real projects in the app; several
  scans with the hold were tried in the demo and one server test (two scans through `align_all`), not with three real
  scans; simulated scans only.

## Every stage says what its own mesh model left out (ENGINE_PLAN R2-5, 2026-11-27)

The last open part of "nothing goes missing in silence": when a mesh model's pool is full, new surface is not added.
Follow the camera said so on the Process page; Straighten and Build the surface, which fuse again, did not, and
"Make only the thin walls finer" ignored its finer model's status. No scan result changes: this adds words.

- **What it does.** `stages.straighten.warnings`, `stages.fuse.warnings` and `stages.fine_walls.warnings` (and
  `made.fine_walls.warnings`) hold the stage's own sentences; `[]` when there are none, and a run starts with `[]`. The
  Process page shows them under the stage, in the quiet style the pool's sentence already has under Follow the
  camera. The thin walls' step has a sentence of its own ("The finer model for the thin walls ran out of room after
  frame N: ... The thin walls it did not reach keep the surface Make mesh made, at the mesh's own detail; nothing was
  removed."), shown in its note on the result page and carried into the base's report and the scan's summary,
  whether or not its mesh became the base.
- **Own, by where it came from.** The camera path's warning, which every later stage's report carries, shows under
  Follow the camera only; Straighten's laser-crossings sentence stays at the end of its summary. The first version
  told a stage's own warnings from the track's by their words, which hid a fuse's warning when its pool filled
  exactly as the track's had (the same frame and counts: Build the surface at the same poses and mesh detail). They
  are now read from the fuse's report before the track's are carried in.
- **Tests:** 10 in `tests/test_server_stage_warnings.py`, through the real server with the pool's status, the path's
  check, the laser crossings and the thin walls' step replaced by ones that warn (a small recording cannot fill a
  pool). 8 were seen failing against the code before the change (`KeyError: 'warnings'`); the one for the identical
  sentence was seen failing against the first version; the sentence test passes either way (the mesh model's
  sentence is unchanged, word for word). With the neighbouring files (fine walls, path warning, pool full, merge
  capacity, straighten tags, the stages, Make mesh from the images, presets): 83 passed, 1 skipped with the graphics
  card; 179 passed, 5 skipped on the processor (with the error-words file, 100 tests). No graphics driver timeout.
  Lint clean. UI: typecheck, the 87 tests, build.
- **Seen in the running app (2026-11-27, the working tree's server on the processor path, the same replacements as
  the tests):** scan "Stage warnings": under Follow the camera the path's warning and "The mesh model ran out of room
  after frame 6: surface seen after that was not added (40 of 1,040 blocks of surface left out; the model holds 1,000
  blocks, 12 MB). The mesh is incomplete there. ..."; under Straighten that sentence once, then its summary ending
  with the crossings' sentence; under Build the surface that sentence. Scan "Thin walls full": the note "The thin
  walls were made finer: 3.0 % of the surface on 2 walls ..." with "The finer model for the thin walls ran out of room
  after frame 7: ... (1,200 of 6,200 blocks of surface left out; the model holds 5,000 blocks, 60 MB). ..." under it.
  Found there and mended: the result page said that sentence a second time under "Notes on making the mesh"; it says
  it once.
- **Whole suite on the commit (b709d03, a frozen copy, 2026-11-27):** 2459 passed, 7 skipped with the graphics card
  (51 min 50 s); 2374 passed, 92 skipped on the processor (47 min 55 s): the 10 new tests on each path, nothing else
  changed in the counts. No graphics driver timeout.
- **The exe, rebuilt from b709d03 (527 MB):** it starts in 2 s and serves the page; the alignment of the two bracket
  scans is as before (99 % within 1 mm, typically 0.12 mm, 9 of 9 guesses agree; the hold block is there); the
  process state of a scan saved before reads `warnings: []` under Follow the camera, Straighten and Build the
  surface; the wall-thickness route gives the same four readings as Python and the hole-images route the same
  twelve (nine read, three refused with the same words). No stage warning was raised in the exe: nothing there
  fills a pool.
- **Shown as a warning (later on 2026-11-27).** The mesh model's sentence says surface is missing, so the page no
  longer shows it as a quiet grey line: it has the amber warning style and the title "The mesh is incomplete"
  under its stage, on the scan's row of the project page (the sentence on hover) and in the viewer's report, beside
  "Tracking may have slid" and apart from "Notes on making the mesh". The thin walls' own sentence stays a quiet
  note (nothing is missing from the mesh there). The page tells the sentence by its first words, as it does the
  camera path's (`ui/src/lib/pathWarning.ts`); 9 checks added to `pathWarning.test.ts` (29), 87 UI tests,
  typecheck, build. Seen in the running app (`warn-check`, the status replaced): the row's warning, the amber note
  under each of the three stages, and the viewer's block with no "Notes on making the mesh" beside it. No engine or
  server code changed (one docstring). The exe was rebuilt from this commit (31e8deb, 527 MB): it starts in 2 s,
  aligns the two bracket scans as before and its page holds the new title; the wall and hole routes were not
  checked again (they were on b709d03, and nothing they use changed).
- **Not done:** no real pool was filled (in the tests and in the app the status is replaced); while a mesh is being
  made again the progress panel shows the
  stages' summaries, not their warnings; when Straighten fuses twice (loops, then crossings) only the second fuse's
  warnings are shown, since its mesh replaces the first; a skipped or failed run still keeps other fields of the run
  before (only `warnings` is cleared at the start).

## The thin wall lost with the tags on: the laser crossings, not the tags (ENGINE_PLAN X8, 2026-11-27)

The question left open on 2026-11-24 ("Short tagged scans"): with `[process] straighten_tags` on, one short scan of
standing walls (`tags-s2`) came out with a better camera path and without its 0.5 mm wall, with no warning. Was it the
tags' poses, or Straighten's second fuse? Neither: it was the laser crossings, which run after the tags.

- **The same scan, three ways** (Make mesh's chain on the graphics card, Standard, 1 mm; judged by the scorecard):

  | | path off the truth, median | 0.5 mm wall covered | wall 0.5 / 1 / 2 mm error | whole part covered within 0.5 mm |
  |---|---|---|---|---|
  | tags off | 1.39 mm | 49 % | +1.23 / +0.88 / +0.51 mm | 90.2 % |
  | tags on, then the laser crossings (as it was) | 0.66 mm | 2.5 % | no reading / +0.30 / -0.06 mm | 87.4 % |
  | tags on, laser crossings off | 0.28 mm | 74 % | +0.82 / +0.60 / +0.22 mm | 91.3 % |

  With the tags alone every number is better than without them, and the wall is there. The last row is also a fuse
  by Straighten at changed poses, so the second fuse is cleared too.
- **What the crossings did.** Before the tags their own check refused them (held-out ratio 1.07 against the limit of
  0.85). After the tags it passed at 0.847, by 0.003. They then moved the camera by up to 2.0 mm, and by the
  recording's truth took the path from 0.28 to 0.66 mm (median) and from 0.04 to 0.08 degrees. Read from the raw
  frames (2026-11-24), the frames of the two sides of each wall came about 1.1 mm closer to each other: from 0.5 mm
  too far apart to 0.6 mm too close, and the 0.5 mm wall's sides passed through each other.
- **Every saved run where the crossings acted** (the scorecard's cache and the X8 runs, the crossings' own
  before / after against the truth): on the two speckled brackets the path got nearer the truth in 9 of 9 runs
  (for example 1.02 to 0.73 mm); on standing walls it got farther in 5 of 5 (0.47 to 0.69, 1.50 to 1.84, 0.28 to
  0.66 mm). The crossings' own numbers do not tell the two apart: held-out ratios 0.63 to 0.81 on the brackets and
  0.71 to 0.85 on the walls, the median miss falling from about 0.2 to 0.08 mm on both.
- **On scans the tags hold** (16 saved scans: 11 long panels, 5 short scans): the crossings refused on 15 (ratios
  1.04 to 1.23 on the panels, 1.12 to 1.41 on the short scans) and acted on `tags-s2`. On one of the panels an
  earlier version of the tags' step had left a path on which they acted too (0.825). Both times the path moved away
  from the truth (0.28 to 0.66 mm, 0.45 to 0.94 mm). They helped on none.
- **Looked for and ruled out** (processor only, `x8\crosscheck.py`, `crosswhere.py`, `lasertruth.py`,
  `tangentcheck.py` in the work folder):
  - *Crossings that join a wall's two faces.* None: of 244 to 325 crossings on `tags-s2`, no pair has its two
    cameras on opposite sides of the surface, at the true, the tracked or the moved poses.
  - *Laser points off the surface near the walls.* No: at the true poses the curves' points are 0.05 mm (median)
    from the true surface on `fins-s2` (4 % over 0.25 mm, none over 1 mm), better than on the speckled bracket
    (0.08 mm, 9 %).
  - *Found:* the crossings themselves miss at the TRUE poses. On `fins-s2` 43 % of the 402 crossings miss by more
    than 0.25 mm, on the speckled bracket 27 % of 280. Part of it is the same on both: crossings between frames
    under 10 apart (median miss 0.6 to 1.7 mm at the truth) and at the table's edge, and tangents taken from two
    neighbours 0.36 mm apart; with a line fitted over 2 mm the bracket's share falls to 7 %, the walls' only to
    33 %. Why the walls keep a third of their crossings off by 0.25 mm and more at the true poses is **not
    explained** (not the points, not the two faces, not the tangents alone).
- **The fix (adopted):** on a scan the tags hold (`straighten.json` `tags.acted`), Straighten does not run the laser
  crossings after them; `laser_curves` then reads `{changed: false, reason: "tags_held"}` and the stage's mesh is
  the fuse at the tags' poses. With `straighten_tags` off (the default), or tags that did not act, nothing changes:
  the crossings run as before. `tools/scorecard.py`'s chain asks the server's rule (`crossings_after_tags`).
- **The scan again, through the changed code** (tags on, nothing else set): the path 0.28 mm, the 0.5 mm wall covered
  74 %, walls +0.82 / +0.60 / +0.22 mm, the part covered 91.3 %: the third row above, number for number.
- **Tests:** 4 in `tests/test_server_tags_then_crossings.py` (the rule; through the server with the loops' and the
  crossings' steps replaced: tags that held keep the crossings out, tags that did not act leave them running; the
  scorecard's chain asks the same rule). Against the code before: 3 failed, the "as before" one passed. With the
  files around it (Straighten's tags, the stage warnings, the stages, the scorecard, scan health, the crossings, the
  tag anchors): 98 passed with the graphics card; 98 passed on the processor. No graphics driver timeout. Lint
  clean.
- **Not done:** one scan shows the loss and its cure; the other four short scans where the tags acted are unchanged
  by the fix (the crossings had refused there), and no new scan was rendered for it. The crossings still act on
  standing walls WITHOUT tags when their check passes (it refuses the scorecard's three walls scenes today; a walls
  recording with drift added is known to pass). What makes them miss at the true poses is the next section.
- **Whole suite on the commit (947b41b, a frozen copy, 2026-11-27):** 2463 passed, 7 skipped with the graphics card
  (48 min); 2378 passed, 92 skipped on the processor (42 min): the 4 new tests on each path. No graphics driver
  timeout. The exe was not rebuilt from it.

## Why the laser crossings miss at the true poses: two causes (ENGINE_PLAN X8, 2026-11-27)

The question left open above: at the TRUE camera poses the laser lines of different frames should meet where they
cross, yet on the standing walls 43 % of the crossings miss by more than 0.25 mm (speckled bracket 2: 27 %). Two
causes, both measured against the true surface; the second is what fools the adjustment's own test on thin walls.

**Setup.** The scorecard's recordings `fins-s2` (standing walls, 120 frames) and `hard-speck2` (speckled bracket,
90 frames), 1280 px, true poses, the crossings as Straighten finds them (402 and 280); the 640 px walls of the
engine tests for the last part. Processor only.

**Where the misses are.** A crossing whose two points lie on the true surface (within 0.15 mm) hardly ever misses:

| crossings at the true poses | walls: n, median miss, over 0.25 mm | bracket: n, median, over 0.25 mm |
|---|---|---|
| all | 402, 0.208 mm, 43.3 % | 280, 0.110 mm, 27.1 % |
| both points on the true surface | 180, 0.074 mm, 2.8 % | 159, 0.056 mm, 6.9 % |
| a point off the surface, one face | 208, 0.325 mm, 75.5 % | 111, 0.294 mm, 53.2 % |
| the two points on different faces | 14, 1.467 mm, 85.7 % | 10, 0.363 mm, 60.0 % |
| between frames under 10 apart | 45, 0.889 mm, 84 % | 67, 0.6 to 1.05 mm, 80 to 87 % |

Ruled out again here: the frame's own other laser line (no crossing sample lies within 8 mm of it).

**First cause: crossings that are none.** All 45 crossings between neighbouring frames on the walls join the SAME
laser line of two frames. Their directions differ by 32 degrees at the median, just over the 30 the rule asks, and
the normal of the two directions points 68 degrees off the level face they lie on. A curve's direction is taken
from a sample's two neighbours, a third of a millimetre apart: where the line bends over an edge its samples cut
the corner, and one sample 0.4 mm off tilts its neighbours by over 30 degrees. Two nearly parallel lines then pass
for a crossing, and its "miss" is the distance between the lines (1 to 3 mm). With the direction taken from a
straight line fitted over a short stretch, and a sample used only where its curve is straight on both sides (a
setting, `CurveParams.tangent_fit_mm` = 1.5 mm, 0.75 mm each side, within 0.1 mm):

| at the true poses | two neighbours (today) | fitted line |
|---|---|---|
| walls: crossings, between near frames, over 0.25 mm | 402, 45, 43.3 % | 120, 0, 29.2 % |
| bracket: crossings, between near frames, over 0.25 mm | 280, 67, 27.1 % | 101, 1, 5.9 % |
| 640 px walls: crossings, between near frames missing over 1 mm | 1821, 182 | 505, 4 |

**Second cause: the projector's dots pull the laser line's centre.** With the fitted line every crossing left on the
walls lies on the base's top (120 of 120), and 29 % still miss. There a laser line reads up to 0.36 mm low over
a few millimetres, in the same place along the line frame after frame. The height error of the laser samples on
level faces, taken by place along the line in the image and compared between frames (walls, 1280 px):

| | scatter | alike 1 / 2 / 5 / 10 / 30 frames apart (correlation) |
|---|---|---|
| horizontal line | 0.132 mm | 0.80 / 0.76 / 0.63 / 0.38 / 0.07 |
| vertical line | 0.069 mm | 0.64 / 0.43 / 0.08 / -0.06 / 0.04 |

The dots and the laser lines are in the same image and both are fixed to the scanner, so a dot sits on the same
piece of a line until the depth changes. The proof, the 640 px walls rendered twice, the same scene, path and seed:

| 640 px walls at the true poses | with the dots | without the dots |
|---|---|---|
| laser samples' height error on level faces | 0.212 mm | 0.086 mm |
| alike 1 / 2 / 5 / 10 frames apart | 0.86 / 0.82 / 0.73 / 0.60 | 0.47 / 0.29 / 0.15 / 0.20 |
| crossings, fitted line: median miss, over 0.25 mm | 0.309 mm, 59 % | 0.054 mm, 9 % |
| the adjustment, two neighbours (today) | refused (0.893; one half 0.704) | refused (0.917) |
| the adjustment, fitted line | ACTS (0.574), moves the true poses 2.85 mm | refused (0.980) |

An error shared by neighbouring frames is exactly what the test with whole frames held out cannot see through: a
held-out frame carries the error of the fitted frames beside it, so fitting the error "helps" it, as drift would.

**What the fitted line does to Straighten's decisions** (processor; the poses and the mesh each saved app-chain run
fed the crossings; 34 tracked scans, the two whose loops moved the poses left out, and all 36 at the true poses
with the true-pose mesh):

- Tracked: today the crossings act on speckled bracket 2 alone (ratio 0.743; path median 0.652 to 0.51 mm, the
  largest error 1.39 to 1.15 mm). With the fitted line it keeps 97 of its 295 crossings, too few for the check
  (fewer than 20 join held-out frames, also when 60 crossings are asked instead of 150), so that gain is lost;
  nothing acts. Every other scan is refused either way.
- At the true poses nothing acts either way, but the walls come nearer the limit of 0.85: seed 0 at 0.852 with
  the fitted line (0.909 today), refused by 0.002. Seed 2: 0.87 today; with the fitted line too few crossings
  (120), or 1.408 when 60 are asked.
- Without a reference mesh the 640 px walls of the engine tests pass at the true poses with the fitted line
  (the table above: 0.574, moved 2.85 mm).
- Tried and not kept: no fit when the crossings' median miss is at most 0.15 mm (with the fitted line it is 0.04
  to 0.13 mm at the true poses on all 32 scans with enough crossings). It would not stop the 640 px walls
  (0.31 mm).

**Outcome.** The fitted line is in the engine as a setting and OFF: on its own it loses the one gain the
crossings give today and brings the walls nearer to being moved, because the crossings it leaves show the dots'
error more clearly to a test that takes it for drift. Nothing changes by
default; the scorecard can try it (`curve.tangent_fit_mm=1.5`). Where samples are farther apart than 0.4 mm (a
smaller image, a far or slanted surface) its three lengths grow in proportion.

**Also seen, the adjustment without a reference mesh** (the app always gives one; the engine tests do not): drift
added to the true poses of five recordings (three walls, speckled bracket 2, a 240-frame bracket), four drifts
each. Today it acts on 16 of the 20 and leaves the camera path worse on 10, better on 5; at the true poses it
moves 2 of the 5 (the walls, by 1.1 and 2.1 mm rms). With the fitted line (60 crossings asked): acts on 12, worse
on 6, better on 4; moves 1 of 5 at the true poses (walls seed 0, 0.8 mm rms). The 240-frame bracket with 2,500 to
4,000 crossings is among the ones made worse.

**Tests.** 10 in `tests/test_track_curve_tangents.py` on made-up curves (the setting is off; a straight curve, a
bend over an edge, one sample off the surface, a gap, the false crossing itself, a real crossing kept, coarser
samples, the default rule, too short a curve); with the setting on by default 8 of the first 9 failed against the
old rule and 1 held. The standing walls' test in `tests/test_track_curve_ba.py` now also holds both findings (182
false crossings to 4; with the setting the true poses are moved, which is why it is off). With the files around
it 87 passed on the processor and 87 with the graphics card; no graphics driver timeout; lint clean. The whole
suite was not run (the default path gives the numbers the older tests pin).

**Open.** (1) Laser frames without the dots: whether the scanner can switch the dot projector off for the frames
its lasers are read from is a hardware and firmware question, not looked at. (2) A held-out test that an error
repeated in neighbouring frames cannot pass, for instance holding out by place along the line instead of by
frame: not built. (3) The adjustment without a reference mesh. (4) Not measured: what the dots cost "Refine with
lasers", which uses the same laser points. Work files: `x8\crossedge.py`, `crossbias.py`, `crossdump.py`,
`crossdump2.py`, `crossrobust.py`, `crosstry.py`, `walldrift.py`, `lowline.py`, `biasfixed.py`, `nodots.py`.

## Dot frames and laser frames in turn, measured in the simulator (ENGINE_PLAN X8, 2026-11-27)

The section above found that the projector's dots pull the laser lines. The scanner as built cannot take its
lasers in frames without the dots (ENGINE_PLAN X8, "Laser frames without the dots"). This measures what it would
gain and cost if it could: every second frame dots only, the frames between lasers only. Nothing was changed in
the engine and nothing was tried on hardware.

**Setup.** Three of the scorecard's recordings (standing walls seeds 2 and 0, 120 frames at 30 a second; speckled
bracket 2, 90 frames), 1280 px, each rendered twice more with the same scene, hand path and seed: lasers only and
dots only. The laser side at the TRUE poses on the processor; the dot side through the scorecard's frame pass
with the graphics card. A laser frame has no depth of its own, so its pose is taken from the dot frames beside it:
straight between the two neighbours, or a cubic through four. Those neighbours' poses are the true ones here.

**The lasers.** Distance of the laser samples from the true surface (median / nine in ten within / share over
0.25 mm):

| laser samples | walls seed 2 | walls seed 0 | speckled bracket 2 |
|---|---|---|---|
| today: dots and lasers in every frame | 0.047 / 0.186 mm / 4.5 % | 0.040 / 0.163 mm / 2.6 % | 0.077 / 0.242 mm / 9.3 % |
| lasers only, the frame's own pose | 0.027 / 0.079 mm / 0.4 % | 0.023 / 0.068 mm / 0.3 % | 0.069 / 0.192 mm / 4.4 % |
| lasers only, placed between two dot frames (30 a second) | 0.039 / 0.104 mm / 0.5 % | 0.046 / 0.116 mm / 0.4 % | 0.085 / 0.222 mm / 6.8 % |

On the walls nine in ten samples lie within 0.07 to 0.08 mm without the dots, against 0.16 to 0.19 mm today. On
the speckled bracket the gain is small: its own speckle is the larger error there.

**What placing a laser frame costs.** The error of a pose taken from its neighbours, on each scan's own hand path
(70 to 95 mm/s), by frame rate (position median; turn median):

| frames a second | straight between two | cubic through four |
|---|---|---|
| 30 (these recordings) | 0.071 to 0.084 mm; 0.022 to 0.028 deg | 0.053 to 0.073 mm; 0.025 to 0.027 deg |
| 50 (the camera on Windows: 46 to 55) | 0.029 to 0.031 mm; 0.010 to 0.012 deg | 0.015 to 0.018 mm; 0.008 deg |
| 60 | 0.021 to 0.022 mm; 0.008 deg | 0.009 to 0.010 mm; 0.005 deg |

The third row of the first table carries the 30-a-second error; at the camera's real rate it is under half of that.

**The crossings and the adjustment at the true poses** (fitted line; crossings, median miss, share over 0.25 mm;
then the held-out ratio, which must stay above 0.85 for true poses to be left alone; with the two neighbours in
brackets):

| | walls seed 2 | walls seed 0 | speckled bracket 2 |
|---|---|---|---|
| today, every frame | 120, 0.135 mm, 29 %; 1.408 (0.870) | 189, 0.112 mm, 10 %; 0.852 (0.909) | 101, 0.090 mm, 6 %; 1.044 (1.091) |
| lasers only, every frame | 565, 0.030 mm, 4 %; 1.390 (1.312) | 854, 0.027 mm, 11 %; 1.646 (1.828) | 1371, 0.048 mm, 5 %; 1.511 (1.446) |
| in turn: lasers in every second frame, placed | 125, 0.041 mm, 2 %; 1.784 (1.254) | 198, 0.067 mm, 15 %; 1.322 (1.783) | 329, 0.083 mm, 7 %; 1.692 (1.767) |

With half the frames there are as many usable crossings as today with all of them, and at the true poses the
adjustment's check is nowhere near its limit (lowest 1.13 over all the laser-only rows; today 0.852 and 0.870).

**The dot side: Follow the camera on the same number of frames, and on half** (tracked poses; path median, part
median, part covered within 0.5 mm, the 0.5 / 1 / 2 mm walls' faces covered):

| | today | dots only, the same frames | today, every second frame | dots only, every second frame |
|---|---|---|---|---|
| walls seed 2 | 1.50 mm, 0.145, 0.899, 0.45 / 0.60 / 0.96 | 1.20, 0.122, 0.900, 0.59 / 0.59 / 0.96 | 0.50, 0.126, 0.893, 0.54 / 0.58 / 0.92 | 0.45, 0.112, 0.887, 0.55 / 0.49 / 0.90 |
| walls seed 0 | 0.53 mm, 0.125, 0.884, 0.48 / 0.60 / 0.68 | 0.59, 0.117, 0.874, 0.50 / 0.46 / 0.69 | 0.67, 0.110, 0.847, 0.30 / 0.39 / 0.41 | 0.59, 0.100, 0.837, 0.33 / 0.26 / 0.41 |
| speckled bracket 2 | 0.65 mm, 0.093, 0.896 | 0.63, 0.091, 0.890 | 0.48, 0.100, 0.887 | 0.36, 0.097, 0.881 |

- Pictures without the laser lines track and mesh about as well as today's on the same frames (part median 0.091
  to 0.122 mm against 0.093 to 0.145; covered within 0.01). One number goes the other way: the 1 mm wall's face is
  covered less on walls seed 0 (0.46 against 0.60; at the true poses 0.55 against 0.68), not explained.
- Half the frames costs coverage on thin walls: on walls seed 0 the three faces fall to 0.30 to 0.41 from 0.48 to
  0.68, at the true poses too, so it is the number of looks, not the tracking. Half of these recordings is 15
  frames a second.
- **What that means for the scanner:** Standard quality already uses every second frame of a camera at 45 frames
  a second or more (30 a second asked, `effective_frame_step`), and the camera gives 46 to 55 on Windows. The dot
  frames would be the frames Standard uses today, and the laser frames the ones it skips: no fewer frames for
  depth or for lasers. Best quality, which uses every frame, would have half.

**Not measured.** Placing from TRACKED poses (here the neighbours' true poses) and with the motion sensor; "Refine
with lasers" with the cleaner points; a whole Make mesh with frames in turn (the engine expects dots and lasers in
every frame); the live view at half the rate; anything on the real camera and Pico. A hand path rougher than the
simulator's would raise the placing error. Work files: `D:\OpenHandScan-work\scratch\alt` (`render.py`,
`lasergain.py`, `lasergain_*.json`, `framepass.json`, the scorecard tables `table_*.md`); no graphics driver
timeout in the four runs.

## The real camera on its own: frame timing (ENGINE_PLAN X8, 2026-11-27)

The owner plugged the stereo camera into this PC, alone: no Pico, no emitters, not in the housing. Windows names it
"3D Global Shutter Camera" (USB 32E4:9282; the board is marked `3D-GS800P`). Measured what frames in turn need to
know: is the frame period steady, does it depend on the exposure, and can the PC tell which frame is which. All at
2560 x 800 MJPG; processor only. Windows offers two ways to read a camera: DirectShow (the app's first choice) and
Media Foundation.

| asked | read through | frames a second read | frames lost | time between frames |
|---|---|---|---|---|
| 60 | DirectShow | 60.005 (1500 frames in 24.98 s) | none | arrival: 16.67 ms on average, spread 3.4 ms; 62 of 1499 waits were near two periods, each followed by a short one (frames arrive in bursts) |
| 60 | Media Foundation | 59.99 (1200 frames) | none | the driver's own time stamps step by 16.6667 ms every time (spread 0.00005 ms); arrival against the stamp spreads 5.7 ms |
| 120 | DirectShow | 68 | about four in ten | the reading call itself takes 14.5 ms |
| 120 | Media Foundation | 119.7 (1500 frames) | 6, in two groups of three | stamps step by 8.3333 ms, twice by 33.33 ms |

- **The frame period is steady:** 16.6662 to 16.6669 ms on the PC's clock over four runs of 15 to 25 s (the spread
  is the arrival jitter, about 20 parts in a million; the camera's own drift is not resolved by this).
- **It does not depend on the exposure:** with the exposure set by hand from -4 to -13 (DirectShow's steps, each
  half the one before) the camera stayed at 59.96 to 60.22 frames a second over 240 frames each, while the picture
  of the room fell from 73 to 18 of 255. Setting it works through Media Foundation too (77 to 19), though the
  value read back there stays at -6. The exposure time in milliseconds for each step is NOT measured: that needs
  a timed light.
- **Which frame is which:** arrival times are off a straight line by 4.7 ms (spread; 9.6 ms at worst in a hundred)
  with a 16.7 ms period, so arrival alone cannot count frames safely. Media Foundation's stamps count them
  exactly, lost frames included (a lost group of three shows as a 33.33 ms step).
- **120 frames a second is real** at the full picture size through Media Foundation. The app asks for 60.
- **The app's own camera class** (`ohs.devices.camera.StereoCamera`, default settings, 900 frames): through
  DirectShow it opens in 2.5 s and loses no frame, but its frame time stamps are off a straight line by 7.7 ms
  (spread), because the stamp is taken before the wait for the frame (16.0 ms before the frame is in hand; the
  constant part is absorbed by the motion sensor's calibrated time offset, the jitter is not). Through Media
  Foundation the stamps (taken on arrival) are off by 5.5 ms, and opening took 82 s. The 82 s are OpenCV's
  "hardware transforms" for Media Foundation: opening and each of the three size and rate settings take 20 s
  with them (80 to 82 s, twice), and 0.6 s in all with `OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0` set before
  OpenCV is loaded; the frames, the rate and the exact stamps are the same either way (`msmfopen.py`).
  The simulator's recordings jitter their frame times by 0.2 ms. Mended 2026-11-28: see "Frame times from the
  camera's own clock" below.

**What it means for frames in turn.** A Pico switching on its own clock can stay in step with this camera: the
period is steady and the exposure does not move it. At 60 a second with a few milliseconds of exposure the dark
gap between two exposures is 10 ms or more. The PC should count frames by Media Foundation's stamps, not by
arrival. At 120 a second there would be 60 dot frames and 60 laser frames a second, so Best quality would keep
every frame it has today and a laser frame would be placed between dot frames 16.7 ms apart (0.02 mm in the
table of the section before this one); the costs are a shorter gap (8.3 ms period), Media Foundation only, and frames lost in
threes now and then.

**Not learned.** The real exposure time and where in the period it sits; that left and right are exposed
together; how well the Pico's clock and the camera's keep step over minutes; the PC's load when it processes
120 frames a second. All need the Pico with a light in front of the camera. Work files:
`D:\OpenHandScan-work\scratch\cam` (`probe.py`, `appstamp.py`, `probe_*.json`, `appstamp.json`).

## The frame pass on recordings with frames in turn (ENGINE_PLAN X8, 2026-11-27)

The frame pass (`ohs.app.process.process_recording`, the engine under "Follow the camera") now reads what light
each frame holds. Simulator only; the app cannot make such a recording yet, and the later steps (the crossings,
Refine with lasers) do not read the laser frames yet.

**What it does with a recording whose frames come in turn.** Dot frames are the pass's frames (depth, tracking, the
mesh; the frame step, Standard's rate cap and the pick by motion count them alone), and no laser line is looked
for in them. Each laser frame has its lines found as always and is placed between the dot frames beside it in time
once the pass is done: a cubic through the four tracked dot frames around it where they follow one another evenly,
else straight between two. A laser frame is left out, and counted with the reason, at the scan's ends, beside an
untracked dot frame, across a cut, or across a gap over 2.5 times the usual. Mixed and dark frames are left out and
counted. `trajectory.csv` holds the dot frames as before; `laser_trajectory.csv` the placed laser frames;
`report.json` gains `frames_in_turn`.

**A recording without kinds gives the same result as before.** Processor path: digests of the trajectory, the laser
points, the meshes and the report taken on the unchanged code first (`tests/test_app_process_unchanged.py`).
Graphics card: the same 14-frame scan through the code before and after, two settings, every one of those items
equal (`D:\OpenHandScan-work\scratch\turnE\gpu_same.py`).

**Placing alone** (the simulator's hand paths, true dot poses, no pictures; walls, bracket, sphere, thin plate, three
seeds each; camera position median / nine in ten within / turn median):

| dot frames a second | straight between two | cubic through four |
|---|---|---|
| 30 (60 a second in turn) | 0.017 to 0.027 mm / 0.026 to 0.041 mm / 0.008 deg | 0.008 to 0.012 mm / 0.014 to 0.018 mm / 0.0045 deg |
| 50 | 0.006 to 0.010 mm | 0.0013 to 0.0019 mm |

These are three to four times smaller than the study's 0.07 to 0.08 mm at 30 a second (the section "Dot frames and
laser frames in turn"); those were the scorecard recordings' hand paths, these the simulator's default ones over
4 s. The difference is not explained.

**Laser points off the true surface** (640 x 400, 48 frames at 60 a second, processor path; median / nine in ten
within, mm). "In turn" has 24 dot frames and 24 laser frames (23 used: the last has no dot frame after it); "today"
is the same scan with both lights in every frame.

| at the TRUE dot poses | in turn | in turn, straight only | today, all 48 frames | today, every second frame |
|---|---|---|---|---|
| standing walls | 0.040 / 0.128 | 0.041 / 0.131 | 0.100 / 0.341 | 0.099 / 0.338 |
| sphere | 0.050 / 0.180 | 0.051 / 0.182 | 0.139 / 0.483 | 0.137 / 0.482 |
| bracket | 0.046 / 0.148 | 0.046 / 0.151 | 0.105 / 0.389 | 0.106 / 0.387 |

| at the TRACKED poses (path median) | in turn | today, all 48 frames | today, every second frame |
|---|---|---|---|
| sphere (0.27 mm) | 0.070 / 0.195 | 0.151 / 0.481 | 0.149 / 0.480 |
| bracket (2.6 mm; today 3.0 to 3.2) | 0.101 / 0.245 | 0.160 / 0.461 | 0.159 / 0.476 |

- The laser points lie 2 to 2.5 times closer to the true surface in turn, on every scene, tracked or not. A laser
  frame also finds more points (about 615 against 485 on the walls).
- **The walls, tracked, are not in the table:** at 640 px on the processor path the tracking slid 20 to 30 mm on
  this short scan, in turn (31 mm) and today (22 to 28 mm) alike. There the median was 0.092 mm in turn against
  0.138 today, but nine in ten lay within 0.70 mm in turn against 0.52 to 0.63 today: worse in turn, on a path
  too wrong to read much from. To be measured at 1280 px on the graphics card.
- The mesh from the dot frames is level with today's on as many frames (median 0.075 to 0.076 against 0.076 to
  0.077 mm at the true poses; tracked bracket 0.405 against 0.403).

**The check against the depth.** In a frame of both lights the laser stage drops points that the frame's own stereo
depth disagrees with. A laser frame has no depth, so its points are checked against the depth of the dot frames
before and after it instead. That is counted (`depth_check.would_drop`) and NOT applied by default
(`ProcessParams.laser_frame_depth_check`, off): at the true poses it dropped about 4 % of the points, all on the
walls' vertical line, and made the cloud slightly worse on all three scenes, so those were good points; on the
tracked bracket it helped a little (0.101 to 0.097 mm). A check that takes good points off a thin wall stays off.

**Not done.** The crossings and Refine with lasers read the laser lines from the frames in `trajectory.csv`, which
are dot frames now: they must learn to read the laser frames at their placed poses. The calibration's health check
gets no laser-against-depth samples from such a scan. Thin walls, holes from the pictures and freeing disk space
are not tried on one. Live scanning does not know frames in turn. Nothing at 1280 px or on the graphics card beyond
the tests (43 pass there), nothing on real pictures. Work files: `D:\OpenHandScan-work\scratch\turnE`.

## Frame times from the camera's own clock (ENGINE_PLAN X8, 2026-11-28)

The fault found in the section "The real camera on its own" is mended: the app's camera class
(`ohs.devices.camera.StereoCamera`) now times each frame by the camera's own frame clock, and reads the camera
through Media Foundation first on Windows. Checked on the real camera (alone on this PC, 2560 x 800 MJPG).

| the app's camera class, default settings | before | after |
|---|---|---|
| read through | DirectShow | Media Foundation (DirectShow second) |
| time to open | 2.5 s | 1.0 to 1.1 s |
| a frame's time is | the PC's clock before the wait for the frame | the camera's stamp for that frame |
| frame-to-frame time off the period: spread / worst | 7.0 ms / about 16 ms | 0.00005 ms / 0.0001 ms |
| frame times off a straight line: spread | 7.7 ms | under 0.0001 ms |
| frames lost at 60 a second | none | none (3602 in 60 s; 2702 in 45 s) |
| asked for 120 a second | 68 a second read | 116.6 read; 69 lost in 23 groups of three, each a gap in the frame numbers |

**How.** Media Foundation gives every frame a stamp that steps by exactly one frame period. On this camera those
stamps are times on the PC's own clock (`perf_counter`): over ten minutes (35 982 frames, none lost) the earliest
arrivals of each minute came 25.6 to 28.5 ms after their stamps, with no trend. So the stamp is used as it is.
The frame number counts the camera's frames by the same stamps, so a frame lost before the PC got it leaves a gap.

- **Watched, not trusted blindly.** If the earliest arrivals move against the stamps by more than 8 ms (twice the
  wander seen; a clock drifting by 20 parts in a million is caught in about 7 minutes), or a frame arrives more
  than 1 ms before its stamp, the stamps are not on the PC's clock after all: the times then follow a learned
  offset (the lower edge of arrival minus stamp, with its slow drift), taken up without a step.
- **Stamps on another clock** go to that learned offset from the start; a camera that gives no usable stamps
  (DirectShow gives none) is timed on arrival, after the call that waited for the frame. Through DirectShow that
  leaves 3.2 ms of spread (30 s on the real camera) where it was 7.0 ms.
- **Opening.** OpenCV's "hardware transforms" made Media Foundation take 80 s to open; `ohs` now switches them off
  before OpenCV loads (`OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0`, unless the user set it). It must be set
  before OpenCV is first imported: set afterwards it did nothing (20 s for the open). A script that imports
  `cv2` before `ohs` still gets the slow open.
- **Exposure** works through Media Foundation: the picture's mean gray was 78.8 at -5 and 32.2 at -9.
- `StereoCamera.info` says where the times come from: `frame_times` ("camera" or "arrival"), `frame_clock` ("host"
  or "learned") and `frame_period_ms`.
- A frame's time is now about 26 ms BEFORE its earliest arrival (nearer the exposure), where it used to be about
  one frame before. The motion sensor's calibrated time offset absorbs a constant, but a calibration made with
  the old timing would not fit; none exists yet (the scanner is not assembled).

**Tests.** 24 new ones on fake captures (`tests/test_devices_camera_times.py`): bursty and late arrivals, lost
frames, clocks 50 and 100 parts in a million apart over ten minutes, six kinds of bad stamps, stamps that break or
drift later, DirectShow-like and Media-Foundation-like captures, the fallback to DirectShow, two cameras, the
order of the interfaces. With the camera, settings, device and setup tests: 135 pass.

**Not tested.** Linux (V4L2 gives stamps too; irregular ones fall back to arrival times), two real cameras, a PC
with several cameras (their numbers could differ between the two interfaces). The packaged exe was tested on
2026-11-28, below. Work files: `D:\OpenHandScan-work\scratch\camT` (`realcam3.txt`, `raw_60.npz`,
`raw_120.npz`) and `D:\OpenHandScan-work\scratch\cam` (`after.py`, `longedge.json`).

**The whole suite after the four pieces of frames in turn (commit af02982, 2026-11-28).** The whole test suite on
a frozen copy of af02982, which holds the firmware's timed switching, the recording's frame kinds, the frame pass
on in-turn recordings and the camera's frame times: 2582 passed, 9 skipped with the graphics card (57 min) and
2500 passed, 91 skipped on the processor (49 min); no failure, no driver timeout (the count of the driver's fault
reports was 5 before, between and after). A first run the evening before was cut 30 minutes in, at 52 % of the
graphics pass with no failure up to there, when Windows Update restarted the PC (a planned restart in the Windows
log, not a crash); this is the second run, from the start. The exe was still the one built from ca5a97e
then; it is rebuilt below ("The exe after the mend").

**One connection in 31 was timed on arrival, and the mend (2026-11-28).** The exe rebuilt from a0eb137 was connected
to the real camera through the app's own route (`POST /api/device/connect`, camera only). It opened through Media
Foundation in 1.2 s, but that first connection said `frame_times` "arrival": the camera's clock was not used, for the
whole connection, and nothing in the log said why (the reason was logged below the default level). It was the first
time the camera was opened after the PC had restarted. It did not happen again:

| connections to the real camera after that | timed by the camera's clock |
|---|---|
| the same exe, twice | 2 of 2 |
| a fresh copy of the exe, its first start | 1 of 1 |
| the app from Python, same route | 1 of 1 |
| the camera class from Python, 12 opens, PC idle | 12 of 12 |
| the same, 14 opens with 16 processes keeping every core busy | 14 of 14 (the first open took 7.8 s, the rest 0.9 to 1.1 s) |

So the cause of that one connection is NOT known. What is known is how the decision was made: the first 20 frames
(a third of a second) were judged once, and for good. Two kinds of start fail that judgement although the camera's
clock is fine, both shown on simulated clocks: frames held up while the PC is busy and then handed over together
(the stamps then seem to run 2.35 times as fast as the arrivals), and a stream whose first stamps are not on its
frame clock yet. The mend:

- A stretch that does not look like a frame clock is looked at again: up to four stretches, and no new one after
  two seconds. Only then are the frames timed on arrival. A capture that gives no stamps at all (DirectShow) is
  still decided at its first frame.
- When the stamps are given up, the log says so as a warning with the reason, and `StereoCamera.info` carries it
  (`frame_times_reason`; the app's device state passes `info` on).
- Tests: five new cases on simulated clocks (four kinds of slow start keep the camera's clock; stamps that stay
  unusable are given up within the bound), seen failing first. With the camera, device, setup and command-line
  tests: 158 pass.
- On the real camera after the mend: 6 of 6 opens by the camera's clock (0.8 to 1.2 s); 30 s at 60 a second, 1802
  frames, none lost, frame-to-frame time off the period 0.0001 ms at worst, as before.

Open: the one connection could not be made to happen again, so the mend is not proven against it; if it returns,
the reason is now in the log and in the device state. Work files: `D:\OpenHandScan-work\scratch\cam` (`exe_cam.py`,
`openmany.py`, `openmany_idle.json`, `openmany_load.json`, `openmany_after.json`).

**The exe after the mend (commit 1725546, 2026-11-28).** Rebuilt from 1725546: 527 MB, starts in 2 s and serves the
page. Its three checks are as before: it aligns the two simulated bracket scans the same (99 % of the smaller scan
within 1 mm, typically 0.12 mm, 9 of 9 starting guesses agree), its wall-thickness readings are the same as
Python's on 4 of 4 and its hole readings on 12 of 12; no driver timeout (the count stayed 5). Connected to the real
camera through the app's route three times: Media Foundation, connected in 1.0 to 1.2 s, frames timed by the
camera's clock used as it is (`frame_times` "camera", `frame_clock` "host", `frame_times_reason` none) each time,
1205 frames in 20 s. So the exe does switch OpenCV's hardware transforms off before OpenCV loads, which was only
read from its start-up code before. This exe is the current one. Not tested in the exe: a scan with the camera
(the scanner is not assembled, no control board on this PC).

## The laser crossings on recordings with frames in turn (ENGINE_PLAN X8, 2026-11-28)

The step that lines a scan up on the crossings of its laser lines (`ohs.track.curve_ba.laser_curve_adjustment`, run
by Straighten) read the laser lines from the frames of the trajectory it was given. On a recording whose frames come
in turn those are dot frames, which hold no laser line. It now reads such a recording. Simulator only, the engine
function only: the app's Straighten step does not pass it an in-turn scan yet. **The result is mixed, and the
crossings are not yet to be relied on for in-turn scans** (below).

**What it does with frames in turn.** The laser curves come from the laser frames (and from frames holding both
lights, at their own poses); no line is looked for in a dot frame, and mixed and dark frames are never read. Each
laser frame is placed between the given dot frames beside it by the frame pass's own rule (`ohs.track.pose_between`)
and is left out, and counted with the reason, at the scan's ends, beside an untracked dot frame, across a cut or
across a long gap. In the solve a laser frame has no pose of its own: it stays placed between its dot frames, so its
crossings move those dot frames, and it is placed again after every step. The dot frames carry the motion prior and
the depth term (a laser frame has no depth). The held-out check holds out every second laser frame. Every tracked dot
frame gets a pose, as before. Afterwards the laser frames are to be placed again between the new dot poses, which is
exactly where the solve had them (0.0000 mm apart; the frame pass does this already when it is given poses).
`report["frames_in_turn"]` says how many laser frames were placed, used, and left out and why.

**Why tied and not free.** A first design gave each laser frame its own pose, joined to its neighbours by the
motion prior. On the drifted orbit with a reference surface it ended at 3.06 mm against 2.68 mm tied, and its
laser poses lay 0.08 to 0.10 mm (median) from where the frame pass would place them afterwards.

**A recording without kinds is adjusted exactly as before.** Processor path: digests of the poses (bit for bit) and
of the report without its timings, five cases on two recordings, taken on the unchanged code first; checked again
here by running the code before the change (they are its digests). Graphics card: the same five cases through the
code before and after, every digest equal (`D:\OpenHandScan-work\scratch\turnX\same_curve.py`).

**At the TRUE dot poses** (640 x 400, processor path; median miss of the crossings in mm, their number in brackets).
The short scans are 48 frames at 60 a second (0.8 s; in turn 24 dot and 24 laser frames); "today" has both lights
in every frame; the orbit is 400 degrees around the sphere in 12 s (60 frames today, 60 dot and 60 laser in turn).

| scene | in turn | today, all 48 frames | today, every second frame |
|---|---|---|---|
| standing walls | 0.355 (56) | 0.464 (765) | 0.492 (206) |
| bracket | 0.934 (160) | 0.663 (951) | 0.733 (213) |
| sphere | 1.715 (25) | 0.877 (640) | 0.910 (168) |
| orbit | **0.034** (1623) | 0.150 (1421) | |

- **Real crossings meet far better in turn.** Counting only a horizontal line meeting a vertical one: walls 0.066 mm
  (28 crossings) against 0.143 (65) today; orbit 0.043 (695) against 0.145 (704). On the orbit the lines meet about
  four times better without the projector's dots, as the study of 2026-11-27 expected.
- **The short bracket and sphere scans have no real crossing at all**, in turn or today: every crossing there is one
  line met by the same line of another frame. Today most of those are neighbouring frames whose lines the dots make
  jagged (0.4 to 0.6 mm); in turn those vanish, and what is left are false crossings at bends (1.4 to 1.7 mm). That
  is why the plain medians of bracket and sphere are worse in turn: fewer, and only false ones.
- Placing costs nothing measurable: with true laser poses instead of placed ones the walls' miss is 0.349.

**Does the held-out check leave the true poses alone?** (the answer wanted is yes; ratio = held-out miss after /
before, the poses are changed below 0.75)

| scene | in turn | today, all 48 frames | today, every second frame |
|---|---|---|---|
| orbit | yes (1.49; 1.545 with a reference) | yes (1.221) | |
| standing walls | yes, too few crossings (56 of 150) | **no**: 0.551, true poses moved 10.3 mm | no: 0.814, moved 4.7 mm |
| bracket | **no**: 0.095, moved 4.1 mm | no: 0.255, moved 7.6 mm | no: 0.193, moved 5.3 mm |
| sphere | yes, too few crossings (25) | no: 0.206, moved 13.7 mm | no: 0.467, moved 3.3 mm |

**At the TRACKED poses** (the frame pass's own path, its mesh as the reference; path error rms mm before -> after):

| scene | in turn | today, all 48 frames |
|---|---|---|
| standing walls | too few crossings (78): unchanged, 29.9 (the path slides at 640 px on the processor) | not better (0.853): unchanged, 26.4 |
| bracket | adjusted (0.493): **2.92 -> 5.42** | adjusted (0.705): **3.49 -> 10.65** |
| sphere | too few crossings (25): unchanged, 0.264 | adjusted (0.337): **0.29 -> 6.20** |

**The orbit with drift put on the dot frames** (rms mm before -> after; turn error median after):

| | in turn | today |
|---|---|---|
| no reference | 5.95 -> 3.28, 0.96 deg | 5.95 -> 3.27, 0.40 deg |
| true surface as the reference | 5.95 -> **2.68**, 0.78 deg | 5.95 -> **1.62**, 0.34 deg |
| the same with `tangent_fit_mm` 1.5 | 2.59 | 1.91 |

**What this says.**

- On short scans the adjustment makes the path WORSE wherever it acts, today and in turn, by false crossings. In
  turn it acts less often (walls and sphere stop at "too few crossings", which is safe) and does less harm on the
  bracket (5.4 against 10.7 mm), but it still harms. This is the fault of "Why the laser crossings miss at the true
  poses" (2026-11-27), not a new one; in the app Straighten runs the crossings on every scan with lasers.
- **On the long orbit with the tests' drift (seed 3) and a reference, in turn ends worse than today. Over nine
  drifts it does not: see "Nine drifts" below.** (Seed 3: 2.68 against 1.62 mm; the turn
  error grows from 0.72 to 0.78 degrees where today's falls to 0.34). More rounds or iterations make it worse, and
  the crossings end at 0.022 mm, below the 0.034 mm of the true poses: it over-fits. The depth term alone gives about
  1.05 mm in both. So the exact crossings pull the path away from the better answer. Likely, not proven: the choice
  of the closest pair per pair of curves, made again each round, picks pairs that already meet.
- **A lead, not followed yet.** In a trial of the first design (laser frames free) a motion prior ten times stiffer
  ended at 1.14 mm with the reference and 1.90 without (turn error 0.28 and 0.46 degrees): better than today's 1.62
  and 3.27. A prior four times looser was worse in turn and today. So the weighting of the prior against the now
  exact crossings is the likely fault, not the laser frames. Not tried in the design that was built, nor on today's
  way (`meas\coupling.json`, `meas\drift_ref_settings.txt`). Followed the same day ("Nine drifts" below): it
  holds for that one drift only.
- `tangent_fit_mm` removes nearly all crossings on the short scans (0 to 41 left), in both.

**Nine drifts, and the motion prior's stiffness (added the same day).** The drifted orbit above is one drift, the
tests' (seed 3). The lead was followed in the design that was built, over nine drifts (seeds 1, 2, 3, 5, 7, 11, 13,
17, 19; path error 3.2 to 6.0 mm rms before) and with the prior made 2 to 100 times stiffer; no code was changed.

| the orbit after the adjustment, rms mm, nine drifts | mean | median | better than today's way | path made worse than before |
|---|---|---|---|---|
| in turn, true surface as the reference | 1.82 | 1.11 | on 7 of 9 | on 1 (seed 1: 5.69 -> 6.60) |
| today, the same | 2.11 | 1.62 | | on none |
| in turn, no reference | 2.29 | 1.75 | on 7 of 9 | on 1 (seed 1: 5.69 -> 6.66) |
| today, the same | 4.13 | 3.46 | | on 3 (seeds 2, 7, 17: up to 4.30 -> 7.88) |

- **Seed 3 was an unlucky drift for frames in turn, not a typical one.** With the settings as built, in turn ends
  below today's way on 7 of the 9 drifts, with the reference and without; the exceptions are seeds 3 and 1.
- **On seed 1 the in-turn adjustment makes the path worse** (5.69 to 6.6 mm, the turn error 1.41 to 1.77 degrees)
  and the held-out check lets it pass (0.12 with the reference, 0.18 without). Today's way does the same on three
  drifts when it has no reference.
- **A stiffer prior is not the remedy in turn.** Ten times stiffer it is better on 3 of 9 drifts with the reference
  and on 2 of 9 without, and worse on the rest (median 1.15 against 1.11 mm with the reference, 2.34 against 1.75
  without; seed 11: 0.78 to 1.15). It lowers the mean only through the two unlucky drifts. The crossings' weight,
  the robust floor and the number of rounds change nothing for the better either. **The default stays.**
- The orbit's true poses are left alone at every stiffness, in turn (ratio 1.55 as built, 1.15 at 100 times) and
  today (1.18 to 1.07): the margin shrinks as the prior stiffens.
- The short bracket in turn passes the check at its true poses at every stiffness; stiffer, its poses are moved
  less (rms 2.98 mm as built, 2.30 at 10 times, 1.17 at 100 times), and its tracked path ends less bad (5.42 as
  built, 3.60 at 10 times) but still worse than before (2.92).
- **For today's way a stiffer prior looked better on most of these cases** (numbers only; nothing changed): ten
  times stiffer it beats the default on 8 of 9 drifts with the reference (mean 2.11 to 1.78) and 7 of 9 without
  (4.13 to 2.84), and it leaves the short scans' true poses far less wrong (path rms after: walls 7.1 to 2.2 mm,
  sphere 8.6 to 1.6, bracket 5.0 to 3.9). The tracked sphere and bracket still end worse than before (sphere 0.29 to 1.86 where the
  default gives 6.20). A change of today's default is a scan-result change and needs the scorecard: a task of its
  own.

Processor path, 640 x 400, the recordings above; the short scans' tracked paths and meshes were taken from the
earlier runs. Not measured: meshes and wall thickness, the graphics card, 1280 px, drift on the short scans.
Work files: `D:\OpenHandScan-work\scratch\turnW` (`sweep.py`, `show.py`, `meas\*.jsonl`).

**Open, as tasks of their own.** Refusing a line's crossing with the same line of a near frame; a held-out check
that false crossings at bends cannot pass, and that a drift like seed 1's cannot pass; the choice of the pair per
pair of curves. The weighting is not the remedy in turn ("Nine drifts" above). For today's way: the stiffer
motion prior, on the scorecard. Until then the crossings are not to be relied on to straighten an in-turn
scan, and the server's Straighten step, when it learns frames in turn, should say so or leave them off.

**Tests.** `tests/test_track_curve_ba_in_turn.py`, 13: the guard of six (five digests, and a kinds column of
"both" only changes nothing), and seven for frames in turn (curves from the laser frames only and the true poses
left alone; drift reduced and the laser frames placed again where the solve had them; left out beside an untracked
or cut dot frame; no laser frame placed changes nothing; frames of both lights read at their own poses, mixed and
dark never; a tied frame moves the frames it lies between; `max_frames` counts the laser frames). With the other crossings tests, the placing, the frame pass in turn and the calibration's health scans: 64 pass on the processor path; 55 pass and 9 are skipped on the graphics card (the digests are the processor path's).

**Not measured.** Meshes and wall thickness after the adjustment; seeds other than 0; scans longer than the orbit
in turn; frames picked by motion (the default gap limit is 2.5 times the median interval of the dot frames given,
stricter than the frame pass's for those); four lasers in turn; the graphics card beyond the unchanged-results
check and the tests; real pictures. Work files: `D:\OpenHandScan-work\scratch\turnX` (`NOTES.md`, `measure.py`,
`meas`).

## Make mesh on a scan with frames in turn (ENGINE_PLAN X8, 2026-11-28)

The app's Make mesh was run through the server on a scan whose recording has frames in turn, for the stages Follow
the camera, Straighten, Fuse and Refine with lasers. Simulator only: the app cannot record such a scan yet. Most of
it worked already, because the stages call the frame pass and the laser crossings, which know frames in turn. Three
things were wrong in the server and are mended (`ohs/server/processing.py`):

| found | mended |
|---|---|
| the scan's result folder got no `laser_trajectory.csv` (a new base copies a fixed list of files) | copied with the others, when the stage has one |
| Refine with lasers' stage folder got none either | the same |
| the laser crossings were not given the gap limit Follow the camera placed its laser frames with | passed on from Follow the camera's report (`frames_in_turn.placing.max_gap_s`) |

**What already worked.** Follow the camera's stage folder holds `laser_trajectory.csv` and its report
`frames_in_turn`. Straighten's loops read frames by the trajectory's rows, which are dot frames, and so does the tag
hold. The laser crossings were given the dot frames' trajectory. Every fuse at new poses (after the loops, after
the crossings, and the Fuse stage) places the laser frames again between the new dot poses. Refine with lasers pulls
the mesh onto the stage's laser cloud, which on such a scan holds the laser frames' points only ("Pulled 6.7 % of
the surface onto 2,935 laser points" in the test's Make mesh). Also new: what the crossings used goes into
Straighten's report as `frames_in_turn.laser_crossings`. No switch and no wording for the user was added.

**A scan without frames in turn runs exactly as before:** each change does nothing without a
`laser_trajectory.csv` or a `frames_in_turn` block, and the existing server tests pass unchanged.

**Tests.** `tests/test_server_process_in_turn.py`, 4 (three failed on the unchanged server): a 24-frame scan in
turn (12 dot and 12 laser frames, 640 x 400, Preview quality) through Make mesh. The scan is too short for loops and
has too few crossings, so both steps run for real and their poses are then moved by a known shift each, which makes
Straighten's own fuse and the fuse after the crossings run. Checked: the trajectory holds dot frames only; the
laser frames are placed, and placed again after each change of the poses, exactly by the frame pass's rule; the
crossings get the dot trajectory and the gap limit; the result folder holds the base stage's files; Refine with
lasers runs inside Make mesh and after a Fuse. With the server's processing, tag, cut and keep tests and the in-turn frame pass and crossings tests: 75 pass on the processor path; 69 pass and 6 are skipped on the graphics card (the crossings' digests are the processor path's).

**Found, not mended here (outside this task).**

- **The fuse at given poses uses a stricter gap limit than Follow the camera when frames were picked by motion**
  (`frame_motion_deg`, off by default): 24-frame sphere, picked at 30 degrees with a largest gap of 8: Follow the
  camera used 8 laser frames (gap limit 0.667 s), the fuse at the same poses used none (0.083 s). On such a scan
  the laser cloud would be empty after Straighten or Fuse. The next task.
- At given poses the fuse does not apply Follow the camera's rate cap to the laser frames, so under a cap it would
  use more laser frames than Follow the camera did (read from the code, not run).
- The calibration's health check gets no laser-against-depth samples from such a scan (its laser-against-mesh
  sign works).
- The thin-walls step and Refine from the images do not copy `laser_trajectory.csv` into their stage folders (the
  result folder keeps the previous base's, which has the same poses). `laser_trajectory.csv` is not offered as a
  download.

**Not tested.** Standard and Best quality; real loops or real crossings changing an in-turn scan (both are known
shifts here); the tag hold, cuts and frames picked by motion through the server; the thin-walls step, merging and
freeing disk space on such a scan. Work files: `D:\OpenHandScan-work\scratch\turnM`.

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

Rewritten 2026-11-21 from the sections named in each point; the numbers are today's.

- **Without a GPU the camera path is weak** ("The no-GPU plate slide, and a check of the camera
  path", 2026-11-20). Tracked path max over the 21 recordings of the no-GPU baseline: sphere 0.19
  mm, brackets 4.5 to 7.1 (part median 0.48 to 0.69 mm, against 0.08 to 0.23 on the GPU path),
  sheets 6.1 to 9.4, walls 5.9 to 17.9, 0.5 mm plates 14.0 to 39.1, dark and speckled brackets 8.3
  to 43.5. The 0.5 mm plates slide in their first frames and come out thinned (to 0.11 mm on seed
  0, with a third of the top kept; 0.27 mm with 67 to 76 % on seeds 1 and 2). The cause is the
  tracking model this path uses (Open3D's): table pixels beside its holes and borders pull the
  camera 3.4 to 6.6 mm against 0.4 to 1.2 mm with the GPU's model. Five settings against that
  pull were tried and none is on: each is worse on several scenes ("The no-GPU tracking model's
  pull: settings tried", 2026-11-21). The app warns when no GPU is used.
- **The camera path's check misses slow drift** ("The no-GPU plate slide, and a check of the
  camera path"). It warns above a 4 mm jump within 0.8 s: on the GPU path no false warning on 46
  recordings that held and a warning on both that slid; without a GPU it warns on 12 of the 18
  recordings that did not hold. The 6 it misses drift slowly at a steady wrong speed (the sheets
  6.1 to 9.4 mm off, a bracket 7.1 mm, the two 90-frame plates 8.0 and 8.3 mm, thinned without a
  warning). A real hand's jerk may read as a slide; the simulator's hand is smooth, so that is not
  measured.
- **Processing time.** Without a GPU a frame at full resolution takes 0.60 to 0.73 s on this CPU
  (the bracket on a quiet PC; 0.45 to 0.53 s before the CPU defaults of 2026-11-20 doubled the
  matcher's work), so it is offline only ("SGBM's paths from above, and the CPU path's slides").
  On the Radeon 680M a frame takes 158 to 162 ms at full quality: 9.5 to 9.7 minutes of waiting
  per minute of scanning (about 11 with reading the frames), against a target of 3; live scanning
  runs at 15 fps ("The waits a user feels", 2026-11-10).
- **Without a GPU the calibration status judges only the cameras** ("The CPU path's ray cast",
  2026-11-19; "SGBM's paths from above"): the laser lines and the gyro would be judged rightly at
  1280x800 since the defaults of 2026-11-20, but the status does not use them on this path yet.
- Without an IMU, symmetric or featureless scenes drift: the yaw about a sphere's axis and sliding
  on a plane are unobservable. The tracker flags this as `degenerate` but cannot fix it.
- The laser margin needs the board printed by `render_charuco_page` on the paper given to
  `lasers_from_recording(paper=...)`, with its margins intact. A trimmed page falls back to the
  board region, which is less accurate.
- **The mesh's p95** ("The scorecard's baseline", 2026-11-20, GPU path, the part alone): 0.13 to
  0.25 mm on smooth parts (the sphere, the 0.5 mm plate, the end-to-end panel), 0.45 to 0.49 mm on
  the bracket tracked (0.41 to 0.43 at true poses), 0.36 to 0.44 mm on the door, 0.51 to 0.80 mm
  on the sheet part and the standing walls, 1.2 to 2.0 mm on the hole plates. On the 200-frame car
  panel it is 1.7 to 2.3 mm: 7 to 9 % of its vertices are fused fill hanging up to 15 mm past its
  free edge. The laser lines are used in the mesh where they disagree with it ("Refine with
  lasers", 2026-11-10): the stage moves the mesh only where at least 1 % of it would move, and
  only within about 15 mm of where the lines swept.
- **Small holes are filled in** ("Holes filled in", 2026-11-12; "The scorecard's baseline"). Where
  the background behind a hole is far away, the stereo depth carries the surface into the hole;
  the gap guard keeps most of it out. Today, GPU path: the Ø 25 hole 120 mm above its floor reads
  1.40 to 1.57 mm small at true poses and 2.9 to 6.9 mm small tracked (seed 0 lost tracking), and
  its Ø 10 and Ø 6 holes give no fit; 10 mm above the floor the Ø 25 reads -0.20 to +0.47 tracked (+0.58 to +0.75 at true poses), the Ø 10 -0.20 to +0.07 tracked (+0.28 to +0.60 at true poses), the Ø 6 no fit; the 200-frame car panel's Ø 25 reads 0.97 to 1.37 mm
  small tracked. The guard's price on a small part with many edges, tracked: 0.7 to 2.0 points of
  coverage ("The gap guard in the engine", the note of 2026-11-16). Measured and not adopted: a
  wider guard by the matcher's doubt (holes better, walls, ribs, bosses and coverage worse; "The
  gap guard by the matcher's doubt", 2026-11-21) and hole rims from the laser lines' ends (no fit
  within 0.2 mm; "Hole rims from the laser lines' ends", 2026-11-21).
- **Thin parts read too thick, and bosses read small, with tracking** ("The scorecard's
  baseline"; the walls as Make mesh runs them since 2026-11-21). Tracked, GPU path: the 0.5 / 1 /
  2 mm standing walls read 1.18 to 1.26 / 0.70 to 0.91 / 0.53 to 0.66 mm too thick (at true poses
  0.76 to 0.84 / 0.40 to 0.62 / 0.16 to 0.35); on the sheet part the 1 mm rib reads 0.66 to 1.21
  mm too thick, the 3 mm rib 0.17 to 0.72, the flange 0.25 to 0.60; the bracket's bosses read 0.07
  to 0.27 mm small in radius (0.22 to 0.30 at true poses) and its shaft within 0.07 mm. A 0.5 mm
  plate lying flat reads within 0.01 mm. The cause of the tracked excess ("Poses refined against
  the finished scan", 2026-11-21): (this reading of the cause is in question since 2026-11-23: see "Poses refined against the finished scan") one camera offset shared by every frame, 0.19 to 0.25 mm
  outward on every sheet and wall recording, which leaves the scan consistent with itself, so
  nothing that refines against the scan can find it. The tracking model's band behind the surface
  sets where it settles; a shorter band moves every recording inward together and was not adopted
  ("Tracking on dark, plain and thin parts", 2026-11-21).
- **Straighten's laser-crossing adjustment can move the path wrongly on thin walls**
  ("Straighten's laser crossings on what Make mesh runs", 2026-11-21). Since 2026-11-21 its test
  holds out whole frames, which refuses the standing walls and keeps the real drift case; when it
  does act the scan's warnings say so. Neither its old test nor the new one catches drift added to
  a 640 px walls recording: both pass and the path gets worse (4.8 to 22.6 mm rms).
- **Dark, speckled and hard scenes slip in their first frames** ("The scorecard's baseline";
  "Tracking on dark, plain and thin parts"): last frame 0.35 to 3.79 mm off on the GPU path, made
  at frames 1 and 2 against a model of one frame and then held. The hole plate 120 mm up loses
  tracking on seed 0 at frames 52 to 56 (13.8 mm off at frame 53) while the tracker's own numbers
  stay healthy.
- On the CPU path two runs of the same recording were found to differ a little (2026-11-10); the
  cause was found and mended ("The CPU path's ray cast: a crash, a hang and runs that differed",
  2026-11-19).
