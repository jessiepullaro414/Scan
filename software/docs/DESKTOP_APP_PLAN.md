# OpenHandScan Studio: desktop app plan

**Goal:** the best scanning app a hobbyist or small shop can get. It should feel as polished as the commercial apps, but be **reliable, open and offline**, and **built for car work**: brackets, intakes, body panels, interiors, reverse engineering. "Studio" runs the scanner, turns scans into accurate meshes, and gets you from scan to part.

---

## 1. What competitors do, and what users say

The research was done in November 2026 from vendor pages, forums and reviews. Reddit was mostly unreachable. Claims marked "unverified" came from background knowledge, not a source.

| | Revo Scan (Revopoint) | Creality Scan | EXStar / StarVision (Shining 3D) | Artec Studio | VXelements (Creaform) | peel.OS / peel.CAD | **OpenHandScan Studio** |
|---|---|---|---|---|---|---|---|
| Price | free with scanner | free with scanner | free with scanner | $1,700/yr Pro, ~$4.3k lifetime (per major version) | quote only | $5,990 with OS / $8,990 with CAD | **free, MIT** |
| Offline, no account | unverified | unverified | ✓ (Vega standalone) | unverified | cloud licensing (v10+) | cloud licence (reseller claim) | **✓ always** |
| Tracking modes | feature / marker | per device | geometry / marker / texture | geometry / texture / hybrid / targets | targets (≥3 visible) | geometry / texture / targets | **dots + laser + IMU + AprilTags, automatic fallback** |
| Live quality feedback | distance bar (unverified) | preview | rewind and recovery | yes | distance gauge, target count | yes | **distance, tracking confidence, coverage, audio cues** |
| Scan rewind | unverified | unverified | ✓ "Scanning Rewind" | unverified | – | – | **✓** |
| One-click processing | ✓ One-click Edit | unverified | partial | ✓ Autopilot | – | – | **✓ plus full manual control** |
| Multi-scan merge | **2 models at a time**, 3+ marker pairs | unverified | manual alignment is flaky | ✓ global registration | target best-fit | ✓ | **N-way automatic, markers or 3-point manual, plus global optimisation** |
| Measurement | unverified | unverified | unverified | ✓ | ✓ (VXinspect) | partial | **✓ distances, angles, radii, gap & flush** |
| Primitive fitting / reverse engineering | – | – | – | Ultimate edition | VXmodel (paid) | peel.CAD (paid) | **✓ built in** |
| Deviation map vs CAD | – | – | – | ✓ (+ZEISS) | ✓ | partial | **✓ (M5)** |
| GPU lock-in | NVIDIA RTX 30/40 for GPU mode (MetroX) | – | GTX 1060-class recommended | GPU recommended | – | – | **any GPU used automatically (NVIDIA, AMD, Intel); CPU works everywhere** |

### What users hate (and what we do about it)
1. **Crashes mid-scan that lose the data.**
   - Revo Scan / MetroX exits to the desktop "at around 20K frames, data lost" ([forum](https://forum.revopoint3d.com/t/revo-scan-5-metrox-v5-6-8-crashes-during-scanning/40178)).
   - Creality Scan crashes right after "End scan" ([forum](https://forum.creality.com/t/crealityscan-software-usb3-issues/18725)).
   - **Our fix:** the raw stream is always recorded to disk as it's captured; the live model is only a preview. Scans recover after a crash, and any scan can be reprocessed later with better settings.
2. **Version regressions.**
   - Revo Scan 6 users went back to v5 "because it works reliably" ([forum](https://forum.revopoint3d.com/t/revoscan-6-shortcomiings/43597)).
   - Revopoint has no real changelog.
   - **Our fix:** public changelog, project files tagged with the engine version, side-by-side installs, and deterministic reprocessing.
3. **Calibration that hangs or fails silently.**
   - Creality's Otter freezes at 85 % ([forum](https://forum.creality.com/t/otter-creality-scan-4-calibration-fails-each-time-at-85/42419)).
   - Unreadable QR boards turn a Raptor into a "useless brick" ([forum](https://forum.creality.com/t/raptor-calibration-useless-brick/48164)).
   - **Our fix:** a guided wizard with live coverage, plain-language errors, retry without starting over, and a quick check at launch that stays silent unless calibration has drifted (an idea borrowed from Creality's 3.3.13).
4. **Merging is limited or flaky.**
   - Revo Scan merges two models at a time and needs 3+ marker pairs ([tutorial](https://forum.revopoint3d.com/t/how-do-i-use-the-merge-function-in-revo-scan-5/21039)).
   - EXStar Hub's manual alignment sometimes sets no points ([forum](https://forum.einstar.com/t/exstar-hub-1-0-4-3-manual-alignment-issues/1983)).
   - **Our fix:** N-way automatic registration with a global pose graph; fall back to markers, then to 3-point manual alignment. You always see the residual error.
5. **GPU and hardware lock-in.**
   - Revopoint's MetroX GPU mode requires NVIDIA RTX 30/40 ([release note](https://global.revopoint3d.com/blogs/news/revo-scan-5-metrox-v5-6-4-released-with-gpu-acceleration-for-windows)).
   - **Our fix:**
     - Use whatever GPU is in the machine, automatically: NVIDIA, AMD or Intel, discrete or integrated.
     - The CPU path stays as the fallback, so the app works everywhere. The engine runs on the CPU today.
     - A GPU is a speed-up, never a requirement.
6. **Flat, shiny and black car surfaces.**
   - A Revo POP user couldn't scan tyres, black body parts or glass without markers ([forum](https://forum.revopoint3d.com/t/scanning-vehicles-with-revopoint-pop/317)).
   - **Our fix:**
     - The dot projector gives texture to plain panels.
     - The laser lines measure black and glossy paint better than structured light does.
     - AprilTag stickers keep large panels drift-free.
     - The app tells you which of these to use and when.
7. **Cost, subscriptions and cloud licence checks** on pro tools. **Our fix:** free, open source, no account, no activation.

### What users like, which we copy
- Shining 3D's **Scanning Rewind**, plus easy recovery by returning to the last good spot. We go further and show a "ghost" of the last good scanner pose so you know where to point.
- Revopoint's **One-click Edit** next to a fully manual pipeline.
- **All pre-scan options in one panel:** mode, markers, plane removal, point spacing (Shining 3D).
- **Artec's automation:** queued workflows, Autopilot, scripting. We provide a Python API and saved "recipes".
- **SpaceMouse support.** Users noticed when Revo Scan 6 dropped it.
- **Standalone and offline** operation (Einstar Vega).

---

## 2. Where we beat everyone (the differentiators)
1. **Built for automotive:**
   - presets for flat panels, glossy or black paint, interiors and engine bays
   - a tag layout planner for big parts, with drift warnings
   - gap & flush measurement
   - a symmetry plane for mirroring left and right parts
2. **Scan-to-part in one app.** Fit planes, cylinders, holes and spheres, align to a coordinate system, take cross-sections (DXF/SVG), and export them to CAD: a STEP of the fitted primitives, plus a FreeCAD/Fusion-ready package. Competitors sell this as add-ons.
3. **Accuracy you can see.** Every result carries its numbers: tracking residuals, laser-plane fit, per-region confidence. The laser lines give 0.07 mm reference points, overlaid on the dense mesh.
4. **Never lose a scan.** Raw recording, crash recovery, reprocessing.
5. **Open:**
   - MIT licence, open formats (PLY/STL/OBJ/3MF and JSON project files)
   - a Python API
   - the same UI works in a browser, so a phone or tablet can be the scan display
6. **Fast on any machine:** it uses the GPU when there is one, from any vendor (an RTX laptop, or the Radeon or Intel graphics built into most laptops). It still works on the CPU alone, and it ships as one installer.

---

## 3. Who it's for and the main jobs
- **The DIY fabricator:** "Scan the engine bay around the intake, and design a bracket that fits."
- **The body-kit maker:** "Scan the fender, mirror it, design a flare."
- **The restorer:** "Measure this part against the original drawing and see where it's bent."
- **The small shop:** "Scan a part, send the customer a measured report."

---

## 4. Information architecture

```
Home ── Projects (grid, recent, "New scan" button)       Device status chip (always visible)
 ├─ Setup     first-run wizard: connect → self-test → print targets → calibrate (stereo, lasers, IMU)
 ├─ Scan      live view: 3D model + cameras + gauges + controls     (presets, markers, rewind)
 ├─ Process   one-click or step-by-step: optimise → fuse → clean → fill → smooth → decimate
 ├─ Align     multi-scan: auto / markers / 3-point manual → merge
 ├─ Measure   distance, angle, radius, gap & flush, sections, primitives, coordinate systems, deviation
 └─ Export    STL/OBJ/PLY/3MF, DXF/SVG sections, STEP primitives, PDF report
```

A project is a folder: `project.json`, `scans/<id>/recording/` (raw), `scans/<id>/result/`, `measure/`, `exports/`. Projects are portable and readable without the app.

---

## 5. Key UX flows

### 5.1 First run (under 10 minutes to the first scan)
1. **Connect.** The app finds the camera and the Pico automatically. If the firmware is missing, one click shows the flashing steps with pictures.
2. **Self-test.** Checks the camera frame rate, IMU, clock sync, each emitter (camera-confirmed) and the trigger. Results are green / amber / red with a fix-it link for each.
3. **Print targets.** One click makes the board, the tags and the projector mask. Then the app checks your print's 100 mm scale bar with the calipers.
4. **Calibrate:**
   - **Stereo:** 15–25 board views. A live coverage heatmap, auto-capture when the board is steady and well placed, and a pass/fail verdict with the reason.
   - **Lasers:** 6–10 poses. Shows whether both lines hit the white margin.
   - **IMU:** "hold still, now wave".
5. **First scan:** scan a demo object, such as a mug or a coffee can, with an animated hint.

### 5.2 Live scan (the screen people judge us by)
- A **big 3D view** shows the model growing in real time. Colours mean: green = good data, yellow = thin data, unscanned = transparent.
- **Distance gauge:** a vertical bar from 150 to 450 mm, with the sweet spot marked. There's a sound when you're out of range (optional).
- **Tracking state:** OK, weak or lost, with the reason ("too few features: add tags or move closer"). If tracking is lost, a **ghost of the last good pose** appears and you're told to "return here". It recovers automatically.
- **Camera thumbnails:** left image with the laser overlay, plus a depth inset.
- **Controls:**
  - the scanner trigger: press to start or pause, long press to finish
  - **Rewind 2 s** (cut the last bad stretch)
  - mode and preset
  - emitter levels
- **Always recording:** a red dot and the elapsed time. A crash costs nothing.
- **Afterwards:** "Make mesh" runs one-click processing with a progress bar, at Standard quality (30 frames a second of scanning) unless Best (every frame) or Preview is picked. A cancel button keeps the raw data.
  - While it runs, the last live model stays on the Scan page and can be turned. Under the progress bar the app shows the time left in the current pass over the frames ("About 3 min left for tracking"), from this job's own rate, once the rate is steady. After the last frame the job's message says what is being done.
  - When the job is done, the finished mesh replaces the live model on the Scan page (and the tracked mesh in the viewer), with the camera left where it is. A failed or cancelled job leaves the live model on screen, with the reason.

### 5.3 Processing
One-click by default. The advanced panel shows each step with live preview and undo:
1. global optimisation (loop closure / pose graph)
2. laser refinement
3. fusion at a chosen resolution
4. remove floating bits
5. fill holes (by size)
6. smooth (feature-preserving)
7. decimate (to a target size)

Settings are saved as a **recipe** you can reuse or script.

### 5.4 Multi-scan alignment
- Pick the scans, then **Auto align**: feature matching, global registration and ICP, with a pose graph across all N scans.
- If tags are shared, they're used first.
- Manual fallback: click 3 point pairs.
- The result shows per-scan RMS and a residual heat-map before merging.

### 5.5 Measure and reverse engineering
- **Select:** brush, lasso, connected-region and plane-region selection.
- **Fit:** plane, cylinder (with the axis shown), sphere, cone, and **hole finder** (circles on planes, shown with diameters).
- **Coordinate systems:** 3-2-1 / plane-line-point. Snap the part to the "car axes" or to a fitted mounting face.
- **Measure:** point-to-point, point-to-plane, angle between planes and axes, radius, **gap & flush** between panels.
- **Sections:** slice with a plane, view the 2D profile, export DXF/SVG.
- **Mirror:** fit a symmetry plane and mirror the mesh (for left/right parts).
- **Deviation:** compare against a reference STL/STEP and show a colour map with a pass/fail tolerance and a PDF report (M5).

### 5.6 Export
- STL/OBJ/PLY/3MF meshes (mm), laser point clouds, DXF/SVG sections.
- A **STEP of the fitted primitives and coordinate system**, so the CAD model starts in the right place.
- A **FreeCAD project/macro** with those primitives.
- A PDF measurement report.

---

## 6. Technology

| Layer | Choice | Why |
|---|---|---|
| Engine | the existing `ohs` Python package (OpenCV, Open3D, numpy) | Already validated (294 tests, end-to-end accuracy in `VALIDATION.md`) |
| Backend | **FastAPI + uvicorn**, REST + **WebSocket** | Async streaming of previews and meshes to the UI; typed, documented API; easy to test |
| UI | **React + TypeScript + Vite**, **three.js** via **@react-three/fiber / drei**, **zustand**, **Tailwind**, **lucide** icons | The best 3D-in-a-web-view stack; a fast, modern, consistent UI; widely known, so open-source contributors can help |
| Desktop shell | **pywebview** (Windows WebView2, macOS WKWebView, Linux WebKitGTK) | A native window with one runtime (Python) and no Electron |
| Remote use | the same UI in any browser on the LAN (opt-in) | Use a phone or tablet as the scan display; a scanner on a mini-PC later |
| GPU compute | chosen by a spike at the start of M3; candidates are **wgpu** (WebGPU compute shaders on Vulkan / DX12 / Metal, so any vendor), **CUDA** through CuPy (NVIDIA), and **OpenCL** through OpenCV's T-API | One engine, auto-detected at start. Every GPU kernel has a CPU twin, and tests check that the results agree. The settings show which device is in use. |
| Packaging | PyInstaller and an installer (M6) | One download, no Python needed |

Rejected alternatives:
- **Qt/PySide6:** native, but a dated look, LGPL packaging chores, and far fewer UI building blocks.
- **Open3D's GUI:** no extra downloads, but too limited for a polished app.
- **Electron/Tauri:** a second runtime, or a Rust toolchain, on top of Python.

---

## 7. Architecture

```
┌──────────── pywebview window (or any browser) ────────────┐
│ React app: pages, 3D viewport (r3f), stores (zustand)      │
│   REST /api/*  ◄──────►  WebSocket /api/ws/live            │
└───────────────────────────┬────────────────────────────────┘
                            │ localhost:8765 (LAN optional)
┌───────────────────────────▼────────────────────────────────┐
│ FastAPI app (ohs.server)                                     │
│  DeviceManager   camera | Pico | replay | sim sources        │
│  ScanSession     capture thread → FramePipeline (0.5 scale)  │
│                  → Hub (latest status / preview / mesh)      │
│                  → RecordingWriter (every frame, raw)        │
│  JobRunner       process_recording, calibration flows,       │
│                  alignment …  (thread pool, progress, cancel)│
│  ProjectStore    folders + project.json                      │
└──────────────────────────────────────────────────────────────┘
```

- **Streaming protocol** (see [`APP_API.md`](APP_API.md)):
  - JSON status at 10 Hz
  - JPEG previews at 10–15 Hz
  - mesh snapshots, binary float32/uint32 buffers, every 1–2 s
  - Clients keep only the latest of each.
- **Threads:** the camera and Pico threads exist already. One processing thread owns `FramePipeline`, which isn't thread-safe. The asyncio loop only fans out the latest messages, so a slow UI never stalls the scanner.
- **Safety:** emitters always turn off on disconnect, crash or exit, as `live.py` already does.

---

## 8. Milestones

| # | Milestone | Done when |
|---|---|---|
| **M1** | **App skeleton plus live scanning** (done, see status below) | `ohs app` opens a window. Projects page; device panel (camera / replay / **simulator**); live Scan page with the growing 3D model, camera previews, distance gauge, tracking state, start / stop / **rewind**; scans saved to projects; one-click "Make mesh" job with progress; result viewer (orbit, lighting, wireframe, measure distance). Backend API tests and UI type-check and build pass, plus a browser smoke test. |
| M2 | Setup and calibration wizard (done, see the M2 plan and status below) | first-run flow, live coverage heatmaps, auto-capture, plain-language results, launch-time check |
| M3 | Processing studio, GPU first | It starts with **GPU acceleration**: profile the pipeline, then move the hot spots to the GPU (stereo matching, TSDF fusion, ICP tracking), auto-detected on any vendor, with the CPU fallback. Then the step-by-step pipeline with preview and undo, recipes, selection tools, clean / fill / smooth / decimate, and global pose-graph optimisation. An `ohs bench` command measures speed on any machine. Test beds: this PC's Radeon 680M (integrated) and the owner's GPU laptop. Target: a 2-minute scan meshed at full quality in under 2 minutes on a laptop GPU. |
| M4 | Multi-scan alignment | N-way automatic, tags, manual pairs, residual maps, merge |
| M5 | Measure and reverse engineering | primitives, hole finder, coordinate systems, gap & flush, sections, mirror, deviation and PDF report, STEP / FreeCAD export |
| M6 | Ship it | installer, updater check (offline-friendly), crash-safe logs and diagnostics bundle, docs and tutorials, SpaceMouse, localisation scaffolding |

### M1 status (2026-11-08)
Built and checked end to end in a browser against the simulator:
- create a project, connect the simulator, then scan with pause, resume and rewind;
- stop, then Make mesh (full quality) and open the viewer;
- shading modes, measuring, downloads, rename, move to trash, and both themes.

Also done: 41 backend API tests and a clean UI type-check and build.

Known gaps, carried into later milestones:
- **Not yet verified:** 60 fps in the UI (the test browser was throttled), and the real pywebview window. That window is tested only with a stub, and downloads are now allowed in it.
- **Processing speed:** full-quality processing of the 27 s demo scan (696 frames) took about 5.5 minutes on the CPU. Faster paths (frame subsampling for "preview", GPU depth) belong in M3.
- **After a rewind:** the live model is coarser until new frames refine it.
- **Not built yet:** marker-mode live tags, automated UI tests, and a "follow the scanner" camera.

### M2 plan: setup and calibration wizard
The API is in [`APP_API.md`](APP_API.md#m2-setup-and-calibration). What this milestone builds:

**The wizard** (`#/setup`, the sidebar's "Setup" item) has seven steps. Each step shows done / to do / warning, and any step can be redone on its own:
1. **Connect.** Lists the cameras found, marks the one that looks like the side-by-side stereo camera (2560x800), and shows the Pico's port and firmware. You can pick the camera; it's saved to the config. If the Pico doesn't answer, the flashing steps from `firmware/README.md` are shown. "No hardware yet?" connects the simulator.
2. **Self-test.** Camera frame rate and image, Pico, IMU, clock sync, each emitter (camera-confirmed) and the trigger. Green / amber / red, each with a plain "how to fix". The trigger and emitter prompts appear live.
3. **Print targets.** One click writes the board, the AprilTag sheet and the LED projector mask (Letter or A4). The app shows previews, download buttons and "Open folder", plus the printing rules (100 % / actual size, matte paper, glue the board flat to glass). **Scale check:** type in what your calipers read for the 100 mm bar.
   - Within ±0.3 %: OK.
   - Up to ±4 %: the app explains that the printer scaled the page, and offers "reprint at 100 %" or "use my measurement". The latter scales the board for calibration only, never for printing.
   - Beyond ±4 %: re-measure and reprint.
4. **Stereo**, 5. **Lasers**, 6. **IMU**, 7. **Check**: the capture screens below. When the setup is done, a "Scan your first object" card starts a project and opens the Scan page with a short coach mark.

**Capture screens** (stereo, lasers, check):
- **Live view:**
  - Stereo shows both cameras side by side; the others show the rectified left image.
  - Overlays: the detected corners, the board outline, and a live **coverage heatmap** (8 x 8 cells, views per cell, the same grid as the engine's coverage report).
  - A dashed **target box with an arrow** shows where to put the board next, and a ring fills while you **hold still**.
- **Auto-capture:** a view is taken by itself when all three hold:
  - **Usable:** stereo needs at least 8 corners seen by both cameras; lasers / check need a board pose and both laser lines on the page with at least 15 points each.
  - **Steady:** the corners moved less than 1 px between processed frames for 0.5 s.
  - **New:** compared with every captured view, the board moved more than 12 % of the image width, or its distance changed more than 10 %, or its tilt changed more than 8°.
  
  After a capture there's a 1 s cooldown. You can always capture by hand (Space, or the trigger) when the view is usable. Each capture plays a short click, and the sound can be switched off.
- **Guidance** comes from the backend. It names the next region to fill (3 x 3 image regions, the emptiest first), then the missing tilt directions (left / right / up / down, 20–45°), then the distances:
  - stereo: 200–500 mm;
  - lasers: near < 220 mm, mid 220–330 mm, far > 330 mm, over 150–450 mm.
  
  For lasers, it also says which laser line misses the white margin.
- **Views strip:** thumbnails with corner counts and distances. Undo the last view (Backspace) or delete any. After a solve, the worst views are flagged with their error, and one click drops them and recalibrates.
- **Finish & calibrate** (Enter) unlocks at the minimum view count: 4 for stereo (aim for 15–25), 3 for lasers (6–10), 1 for check (3–5).

**IMU screen:** a big instruction card that walks you through it:
- "Hold still" counts down 2 s.
- "Rotate slowly about all three axes, ±20–40°" runs a 20–60 s timer, with a live gyro-rate gauge and how often the board was seen (keep it above 80 %).
- It stops automatically at 60 s.

**Results** are in plain language:
- a grade (excellent / good / usable / poor / failed) and a one-line verdict;
- metric rows, each with its target and a "what this means" note;
- advice with buttons: add views, drop the bad views, start over, redo stereo, check the print;
- "Save calibration", which backs up the previous file, and a downloadable full report;
- on the simulator, a "versus the true calibration" table.

A failed solve never dead-ends: you go back to capturing with the views kept, and the reason is shown. **Swapped cameras** are caught: a stereo baseline with the wrong sign means left and right are swapped, and the app says to turn on "swap left/right".

**Launch-time check:**
- After every connect, the app matches features between the rectified left and right images of a few idle frames and measures the row error.
- It stays silent unless that is above 0.6 px; then it suggests the 2-minute Check step.
- It needs no board, and it is also silent when there are too few features.

**Simulator:**
- A live virtual scanner looks at the printed ChArUco page (`ohs.sim.board_camera`, a fast homography renderer at about 20–35 ms per stereo frame).
- It runs a scripted tour per step:
  - stereo: board poses all over the image;
  - lasers / check: poses with both laser lines painted on the page;
  - IMU: hold still, then rotate, with synthesized gyro data.
- It uses a distorted lens model, so the whole wizard runs end to end without hardware, and every result can be compared with the truth.
- Its results go to a sandbox calibration.

**Done when:**
- The wizard runs end to end on the simulator: stereo, lasers, IMU and check all save, and every result is within the truth thresholds in the tests.
- The self-test, printables and print check work.
- The launch check flags a deliberately perturbed calibration.
- Backend tests pass, the UI type-checks and builds, and a browser pass covers every step.

### M2 status (2026-11-09)
Built and walked through in a browser against the real backend, on the simulator:
- connect, self-test, printables and the print check;
- stereo, lasers and IMU, each auto-captured, solved and saved to the sandbox;
- the check, the "Done" page and "Scan your first object";
- both themes.

Every result in that run, against the virtual scanner's truth:

| Step | Result |
|---|---|
| Stereo, 16 views | 0.06 px; baseline +0.009 mm, focal length +0.13 px, row error 0.05 px (p95) |
| Laser planes | within 0.006° and 0.011°; laser points 0.05 mm and 0.08 mm off (median) |
| IMU | camera–IMU rotation 0.006°, clock offset +0.01 ms |
| Check | passed: rows to 0.11 px, laser points within 0.01 mm |

The launch check passes on the simulator (0.07 px). In the tests it flags a camera turned by 0.3° (3.8 px).

Tests:
- 59 server tests (41 from M1), 20 for the virtual board and the drift check, and 19 for the config;
- the UI type-checks and builds.

Fixed during the walk-through:
- the laser lines were drawn black;
- a hard-coded config path, in two places;
- self-test and firmware copy that didn't fit the simulator;
- hint labels cut short;
- "Finish & calibrate" said on the check step;
- the IMU timer limits: 22 s and 62 s count from the start of the recording;
- the "first start builds the demo" note that stayed after the demo existed. The engine now reports `sim_demo_ready`.

Known gaps:
- **Real hardware:** the camera self-test, emitters, trigger and devices probe ran only against fakes. Everything else needs the real scanner too.
- **IMU never re-checked:** the check doesn't re-check the IMU, because check recordings hold no IMU samples (the CLI is the same).
- **Launch check after a scene swap:** after a simulator calibration, the launch check isn't re-run on the demo.
- **Still not built:** automated UI tests.

### M3 plan: processing studio, GPU first
M3 has two phases: first speed, using the GPU, then the processing studio UI.

**Where the time goes today** (full quality, 1280x800, CPU only, 433 ms per frame):

| Stage | ms / frame | Share |
|---|---|---|
| Stereo depth (SGBM) | 156 | 36 % |
| Ray casting the tracking model (Open3D, CPU) | 145 | 33 % |
| Tracking depth filter (at full resolution, although tracking uses half) | 40 | 9 % |
| Laser line extraction | 28 | 7 % |
| ICP | 20 | 5 % |
| Fusion | 19 | 4 % |

**GPU layer (`ohs.compute`)**:
- OpenCL through PyOpenCL, so one kernel source runs on NVIDIA, AMD and Intel.
- It chooses a GPU automatically: a discrete one first, then an integrated one.
- Config `[compute] device` (`auto` / `cpu` / part of a GPU's name), overridden by the `OHS_COMPUTE` environment variable.
- Without a GPU, everything stays on the CPU, and the app says why in plain words.
- OpenCV's own OpenCL path doesn't pay off for small image operations: on the Radeon 680M a blur took 11.8 ms on the GPU and 7.0 ms on the CPU. So the GPU is used only for the big kernels.

**Phase 1, speed:**
1. **GPU stereo depth.** Census SGM in OpenCL, with the same `DepthEstimator` contract (depth in mm, 0 = invalid).
   - It must be at least as accurate as SGBM against the simulator's ground-truth depth.
   - Targets: ≤ 15 ms on the RTX 3070 Ti laptop, ≤ 70 ms on the Radeon 680M.
2. **GPU tracking model.** A dense 2 mm TSDF tracking volume on the GPU, integrated and ray cast with trilinear interpolation. The ray-cast maps feed the existing ICP.
   - Open3D stays the CPU twin.
   - Drift on the validation scans must stay within the Open3D numbers.
3. **CPU win for everyone.** Filter the tracking depth at the tracking resolution, not the full one.
4. **Overlap.** The GPU computes the next frame's depth while the CPU tracks the current one.
5. **`ohs bench`.** A standard simulated recording, processed on the CPU and on the GPU:
   - per-stage milliseconds, the speed-up, and accuracy against the ground truth;
   - JSON output, so the laptop and this PC can be compared.
6. The app shows which device processes ("GPU: NVIDIA GeForce RTX 3070 Ti").

Phase 1 is done when full-quality processing is at least 3x faster on the RTX laptop and at least 1.5x faster on the Radeon 680M, with tracking and depth accuracy unchanged on the simulator. Every test must pass both with the GPU and with `OHS_COMPUTE=cpu`.

**Phase 1 status (2026-11-09):** steps 1, 2, 3, 5 and 6 are done; step 4 (overlap) is still open.
- **Speed on the Radeon 680M:** full quality runs 1.81x faster, at 5.34 instead of 2.96 frames per second. Preview runs 1.42x faster.
- **Accuracy:** better than the CPU path on depth (1.8 % of pixels off by > 2 mm instead of 5.6 %) and on tracking. The car-panel worst error dropped from 0.97 mm to 0.26 mm.
- **Robustness:** across 15 simulated scans, the GPU tracking model doesn't slide where Open3D's slid 13-42 mm on 3 of them. The numbers are in `software/VALIDATION.md`, "GPU processing".
- **RTX 3070 Ti laptop:** 2.58x faster at full quality (8.4 instead of 3.2 frames per second) and 1.60x at preview. All GPU tests pass on NVIDIA.
- **GPU tuning still open**, which mainly helps discrete GPUs:
  - more parallel work in the SGM kernels (depth takes 37 ms on the RTX; the aim is about 10 ms, and preview is no faster than the CPU yet);
  - subsample ICP's source points (GPU depth gives 45 % more);
  - move laser extraction and the tracking filter to the GPU;
  - step 4 (overlap).
  
  The aim is about 15 frames per second at full quality on the RTX.

**Phase 2, processing studio:** step-by-step processing with preview and undo, recipes, selection tools, clean / fill / smooth / simplify, and global pose-graph optimisation ("straighten"). The API is in [`APP_API.md`](APP_API.md#m3-phase-2-processing-studio).

- **Where:** a **Process** page per scan (`#/projects/:pid/scans/:sid/process`). The viewer and the project page link to it. "Make mesh" stays the one-click path: it runs the Default recipe.
- **Stages** (each a job, with progress and cancel):
  1. **Track:** today's processing (tracking + fusion), at standard, best or preview quality.
  2. **Straighten:** loop closure.
     - It picks keyframes along the trajectory and finds revisits: keyframes close in space but far apart in time.
     - It checks each candidate with ICP between keyframe clouds, then optimises a pose graph (Open3D's Levenberg-Marquardt with a line process that rejects bad loops).
     - Finally it fuses again at the corrected poses.
     - The result says how many loops it closed and how far the scan moved ("closed 3 loops, moved the end by 1.8 mm"). With no loops it changes nothing.
  3. **Fuse:** build the mesh again from the current poses at a chosen voxel size (0.5 / 1 / 2 mm).
- **Edits on the mesh:** fast, each a job with progress. They apply in order to the fused mesh and show at once in the viewer.
  - Remove floaters (by size);
  - fill holes (up to a size in mm, with a smooth fill);
  - smooth (Taubin, or feature-preserving bilateral normal filtering that keeps the edges of brackets);
  - simplify (quadric, to a triangle count);
  - delete or keep a selection made in the viewer.
  
  **Undo / redo** (Ctrl+Z / Ctrl+Shift+Z) over the whole list must be instant for the last 10 steps. Re-running a stage asks first, because it starts the edits over.
- **Selection tools in the viewer:**
  - box, lasso and brush;
  - add or subtract with Shift and Ctrl;
  - invert, clear, and "select connected".
  
  A highlight shows what is selected and how many faces.
- **Inspection overlays:**
  - **holes:** red outlines, each with its size;
  - **trajectory:** before and after straightening, with the loop links;
  - **before / after:** for the last edit.
- **Stats panel:**
  - triangles, vertices;
  - pieces, holes (count and largest in mm), watertight or not;
  - size, area, and volume when watertight.
- **Recipes:** named step lists (stage settings + edits) that apply in one click, saved as JSON in `data_dir/recipes`.
  - Built in: **Default**, **Body panel** (smoother, fills larger holes), **Bracket / mechanical** (keeps edges, light smoothing), **Quick preview** (preview quality, simplified).
  - "Save as recipe" stores the current scan's steps.
- **Validation:**
  - Straightening must never make a scan worse:
    - simulated scans with a loop and real drift (e.g. a 360° orbit), plus synthetic drift added to good trajectories;
    - measured against the ground truth before and after;
    - no false loops on short scans.
  - Mesh edits are tested on known shapes: hole sizes, sharp-edge angles kept, triangle counts.

**Done when:** the Process page runs every stage and edit with undo / redo, selection, overlays and recipes against the real backend on the simulator. Straightening measurably reduces drift on a looped simulated scan and never worsens a short one. Tests and the browser pass are green.

**Phase 2 status (2026-11-09):** done. It was checked in the browser against the real backend, on a simulated scan of about 644,000 triangles, on the Radeon 680M PC:
- **Default recipe** (what "Make mesh" runs):
  - track, then straighten when it finds loops;
  - remove floaters;
  - fill holes up to 5 mm.
  
  Open holes went from 218 to 36. It doesn't smooth or simplify, so measurements stay true to the scan.
- **Edits:** each shows in the viewer as soon as it finishes, and undo / redo steps through all of them.
  - Feature-preserving smoothing took 3 s and moved the surface 0.023 mm on average.
  - Simplify took 643,968 triangles to 322,057 in 4.4 s.
  - A selection of 208,251 faces was deleted.
- **Selection:** box, lasso, brush and "select connected", with add / subtract, invert and clear.
- **Overlays:**
  - hole outlines with their sizes;
  - the trajectory before and after straightening, with its loop links;
  - before / after for the last edit.
- **Straighten:**
  - On this scan it closed 21 loops, found the passes already lined up (within 0.02 mm), and kept the scan and its edits as they were.
  - Under synthetic drift it cuts the doubled-surface gap by about 10x.
  - It finds no false loops.
  
  The numbers are in `software/VALIDATION.md`, "Straighten".
- **Tests:** 459 pass with the GPU.

**Phase 3, accuracy** (agreed 2026-11-09). These are M3 items of [`RESEARCH_PLAN.md`](RESEARCH_PLAN.md), in this order. The numbers are from its revised top 10.
1. **Degeneracy-aware tracking** (#4):
   - stable (covariance-based) sampling for ICP;
   - per-direction handling of what the surface can't constrain;
   - telling the user when to add tags on flat or gently curved panels.

   **Status (2026-11-09): done. The hints shipped; the two ICP changes didn't.**
   - The tracker reports what the surface can't pin down ("slide", "turn"). The live view turns that into a hint that doesn't flicker: sliding even with the gyro, turning only without it. The processing report gives the share of frames with each.
   - Stable sampling and remapping both made tracking worse on 18 simulated scans, so they were dropped (`VALIDATION.md`, "Degeneracy-aware tracking"). Stable sampling may come back once points carry a depth confidence (item 2).
2. **Depth confidence and uncertainty-weighted fusion on the GPU** (#1):
   - a per-pixel confidence from the matcher;
   - fusion weighted by it, with error bars that don't over-trust consecutive frames, which see nearly the same thing ("honest averaging");
   - the 1 mm reconstruction TSDF moves to the GPU;
   - a per-vertex confidence map shown in the viewer.

   **Status (2026-11-09): done, except the weighting. Fusion averages; the sigmas measure.**
   - Every depth pixel has a calibrated sigma: 2σ covers the true depth error for 96.7 % of the pixels on the GPU and 95.9 % on the CPU (simulated).
   - The 1 mm reconstruction TSDF runs on the GPU (about 20–28 ms a frame at full resolution) and averages like Open3D. Weighting by the sigma made the mesh worse, so it was dropped (`VALIDATION.md`, "Depth confidence").
   - Each vertex has a sigma that counts only independent looks, so holding the scanner still doesn't claim more precision. 2σ covers the true error for 95–100 % of the vertices.
   - The viewer and the Process page have a "Confidence" shading: a legend, the share of the surface within 0.1 / 0.3 mm, and the sigma under the cursor in the viewer. The sigma follows every mesh edit (filled holes are "not measured") and is in `mesh.ply` as `quality`.
   - Without a GPU, Open3D makes the mesh as before, with no confidence map.
3. **"Refine with lasers"** (#3): a Process stage.
   - It rejects laser outliers against the dense surface.
   - It pulls the mesh onto the laser points with a smooth correction.
   - It reports how far it moved.

   **Status (2026-11-10): done.**
   - A fourth stage in the Process page, after Fuse, also run by Make mesh and the built-in recipes ("Quick preview" leaves it off). It refines the mesh of the stage that made the base, with that stage's laser cloud.
   - It drops laser points off the dense surface twice: per frame against the dense depth, and against the fused mesh with a robust fit. It reports how much of the surface it moved, how far, and how far the laser lines were from the surface before and after.
   - On the simulator, a smooth 0.25 mm depth bias near the lines drops to about 0.05 mm. Without a bias nothing changes: the stage says the mesh already agrees and keeps the edits. Corrections under 0.1 mm are the lasers' own error and are not applied (`VALIDATION.md`, "Refine with lasers").
   - It corrects only where the lasers swept; the rest of the surface keeps any bias. The per-scan bias model of research plan 4.2.4 (and 4.9.1) would correct the whole scan.
4. **Measurement-safe mesh edits** (the plan's quick wins):
   - simplify to a tolerance in mm;
   - flag filled faces, so measuring tools skip them;
   - smoothing that does less where the data is confident;
   - an optional watertight fill with a distance limit.

   **Status (2026-11-10): done.**
   - **Simplify by accuracy** (the default): within 0.01–0.1 mm. The result says how far the surface actually moved: on simulated scans 99.9 % of it within the tolerance, all within about twice it.
   - **Filled faces are flagged:** stored in the PLY, counted in the stats, drawn striped grey, and the viewer's distance tool won't put a point on them.
   - **Smoothing within 2σ** (the default when the mesh has confidence): no point moves more than twice its own uncertainty.
   - **Make watertight:** the hole fill can close the outer edge too, within its size limit, for parts scanned all round. It is a size limit, not Poisson with an envelope (research plan 4.6.4).
5. **World-space spacetime stereo, as a trial build** (#2, research plan 7.2): refine the fused surface patch by patch against the raw images of every frame that saw it, instead of averaging per-frame depth.
   - Each frame lights a patch with a different dot arrangement, so the camera-fixed errors of per-frame matching never arise.
   - On the simulator, the prototype cut the median error 4–6 times where the scan concentrates.
   - It comes after item 2, whose error estimates it uses, and after Straighten in the processing order.
   - The trial build decides whether it ships as a GPU stage in M4.

   **Status (2026-11-10): trial build done; worth shipping in M4, after a test on real recordings.**
   - "Refine from the images" is a stage in the Process page, between Fuse and Refine with lasers (`ohs/track/spacetime.py`). It is not in Make mesh or the recipes yet.
   - It reads up to 120 frames, places a patch about 7 px across every 2.6 mm of the surface, and searches each along its normal on the GPU. It keeps a patch with dots in at least 16 frames and a clear match, and moves the vertices around it onto the measured surface; the rest stays as fused.
   - On four simulated scans with tracked poses, the median error where it measured fell 1.8–2.8 times on the sphere (0.062–0.081 → 0.021–0.044 mm) and about 2 times on the car panel (0.043–0.046 → 0.022–0.023 mm). On the panel most of that gain comes from fitting a smooth patch to the mesh, not from the images (`VALIDATION.md`, "Refine from the images").
   - It measured 19–60 % of the surface: the rest has too few frames with dots on it.
   - About 10–20 s for 36 frames and 25 s for a 449-frame scan (120 frames read) on the Radeon 680M. The CPU twin takes about 20 minutes for a short scan.
   - At the true poses the sphere improves 4.7 times, as the prototype did: the tracking error is now the limit.
   - A first try at refining each frame's pose against the images (the second half of research plan 7.2) made the poses more consistent but not the shape: per frame, the camera-fixed matching error comes back (`VALIDATION.md`). A joint fit with a motion prior, or anchoring to the laser lines (research plan 7.3), is the next thing to try.
   - Not yet: it doesn't use or update the sigmas of item 2 (each vertex keeps its own), and nothing on real images has run.
6. **Global verification for large drift** (the first half of #5, Straighten v2; research plan 4.4.1). Today Straighten finds loops from the drifted trajectory and starts ICP there, so it can't close drift beyond about 20 mm.
   - Feature matching between keyframe clouds (FPFH and FGR, in Open3D) proposes loops that are too far off for ICP.
   - The existing checks then verify them, keeping the "never worse" rule.
   - The same code serves multi-scan alignment in M4.

   **Status (2026-11-10): done.** Straighten has no new settings or buttons; it closes much larger drift.
   - A candidate that ICP can't verify from the tracked pose is checked again with wider drift bounds, and if needed from a global FPFH + FGR guess. A large correction counts only when a second loop agrees.
   - The drift model now scales to what the loops measure. That was the first wall: at 10 times the usual drift, the loops verified but the solver threw them all out.
   - On simulated bracket orbits with 5–20 times the usual drift, doubled surfaces close 3–18 times (for example 50 → 2.7 mm at 78 mm of drift, where nothing verified before). Sliding along the surfaces is not fixed (`VALIDATION.md`, "Straighten for large drift").
   - Not yet: proposing loops by shape alone (beyond the widened gate) and the fragments of research plan 4.4.2, which multi-scan alignment in M4 will share. Nothing has run on real recordings.

7. **Laser-curve bundle adjustment, poses only** (#5's second half, research plan 7.3; added 2026-11-10). Straighten also lines the scan up where the laser lines of different frames cross.

   **Status (2026-11-10): done, poses only.**
   - It runs after the loops, inside Straighten (and on scans without loops), with no new settings. It fuses again only when it changes the poses.
   - Never worse: fitted on half the crossings, the other half must meet clearly better, else the poses stay.
   - On a full-resolution 400° sphere orbit with drift, after Straighten, the sphere's mesh error fell 0.17 → 0.12 mm and 0.97 → 0.48 mm (true poses: 0.09); without drift nothing changes. Short scans have too few crossings and are left alone (`VALIDATION.md`, "Laser-curve adjustment").
   - About 8 s for 60 frames at full resolution, plus the second fuse when it changes the poses.
   - Next: the laser planes and the time offset as unknowns (self-calibration), and the IMU.

**Also queued:**
- **Temperature logging** (part of #8, before the hardware arrives): the firmware reads the IMU chip's temperature, and every recording stores it, so drift with heat can be modelled later.
  - **Done 2026-11-09.** Firmware 1.1.0 sends it about once a second while the IMU streams (`C` lines; `STATUS` adds `temp=`).
  - Recordings store each reading as a `temperature` event, with a summary in `meta.json`, and a scan starts with the latest reading.
  - The Device page shows it, and simulated recordings get a slow warm-up.
  - Still open, with #8: the temperature at calibration time, which needs a calibration-format change.
- **GPU tuning for discrete GPUs:**
  - more parallel work in the SGM kernels;
  - ICP point subsampling;
  - laser extraction and the tracking filter on the GPU;
  - the CPU/GPU overlap.

  **Status (2026-11-10): first round done.** Measured with `ohs bench` on the RTX 3070 Ti laptop and the Radeon 680M (`VALIDATION.md`, "GPU tuning"):
  - The laser lines are extracted in two CPU threads while the depth runs, so they are now hidden behind it on the laptop.
  - ICP samples every 8th pixel at full resolution, 3–4 times faster and no less accurate; preview is unchanged.
  - The confidence TSDF weighs pixels on the GPU and samples bricks per voxel size.
  - Laptop: full quality 124 → 67 ms a frame (8.0 → 14.9 fps), preview 53 → 28 ms (18.8 → 35.2 fps). Radeon: 217 → 172 ms full. Accuracy unchanged.
  - The SGM work-group settings were already the fastest on both GPUs; their output doesn't depend on them.

  **Second round done (2026-11-10):** the tracking filter and the confidence TSDF's brick search run on the GPU, with the same results as on the CPU.
  - Laptop: full quality 67 → 49 ms a frame (20.4 fps), preview 28 → 25.5 ms (39.2 fps). Radeon: 172 → 156 ms full.
  - Left, if speed matters again: keep the depth on the GPU between the matcher, the filter and both models (it crosses PCIe several times a frame), and the laser threads once the depth step gets below about 16 ms.

### M5 plan, first slice: fitted shapes in the viewer (agreed 2026-11-12)
The first item of [`ROADMAP.md`](ROADMAP.md), section 3, and [`RESEARCH_PLAN.md`](RESEARCH_PLAN.md) 4.8.1–4.8.4.
It turns "a nice mesh" into numbers a part can be made from.

**What the user does:**
- In the viewer, the **Fit** tool (next to Measure) has three kinds: **Plane**, **Cylinder**, **Hole**.
- One click on the surface: the engine grows a region from that point that matches the kind,
  fits it, and the region lights up. A label shows the result with its uncertainty:
  "Plane · flat within 0.04 mm", "Cylinder Ø 14.02 ± 0.03 mm", "Hole Ø 25.01 ± 0.05 mm".
- Each fit is listed in the side panel with its numbers: points used, residual RMS, the region's
  mean sigma, and a warning when the region is small or touches a filled (unmeasured) area.
- Pick two fits to get their relation: the distance between parallel planes, the angle between
  planes or axes, the distance between two axes, a hole's centre to a plane.

**How it works:**
- `ohs/measure/fit.py`: plane (weighted total least squares), cylinder (start from the normals,
  then Gauss-Newton on the true distances), circle in a plane (holes). Weights 1/σ² from each
  vertex's sigma; filled vertices are left out. The covariance gives the ± values (1σ shown; the
  label says so). These are statistical errors only: calibration and scale come on top.
- `ohs/measure/region.py`: grow over the mesh from the clicked face, keeping faces within a
  tolerance of the current fit and with agreeing normals, refitting as the region grows. Holes
  use the boundary loop nearest the click (`hole_outlines`) and the plane of the faces around it;
  a scanned hole wall is fitted as a cylinder instead when there is one.
- API: `POST /api/projects/{pid}/scans/{sid}/fit` with the kind and the clicked point and face;
  the answer has the shape, its ± values, the residual, the counts, the region's faces (for the
  highlight) and warnings. Documented in `APP_API.md`.

**Steps** (one commit each, tests first):
1. Fits with uncertainty, tested on synthetic points with known truth: the error is small and
   ±2σ covers the truth about 95 % of the time. **Done 2026-11-12:** `ohs/measure/fit.py`
   (`fit_plane`, `fit_cylinder`, `fit_circle`), 50 tests; over 200 seeded trials per case ±2σ
   covers the truth 93–100 % (larger runs 94–99 %), also on 90° arcs and 4 mm short cylinders.
   Open for later steps: the full covariance (for relations between fits), and mesh vertices'
   correlated errors, which may make these ± values too small on real meshes (step 5 measures it).
2. Region growing and the hole finder, tested on simulated meshes (the bracket's bosses and
   shaft, the car panel's hole).
3. The API, its tests, and `APP_API.md`.
4. The viewer's Fit tool, the list, relations, and the mock engine.
5. Simulator validation in `VALIDATION.md`: diameter, axis and position errors against the
   truth on the bracket (bosses r = 7 mm, shaft r = 8 mm) and the car panel (hole Ø 25 mm),
   whether ±2σ covers the truth, and the bias of small holes (fusion rounds them).

**Done when:** on simulated scans in the app, one click gives each shape with its ± value, the
errors against the truth are measured and written down (whatever they are), and the tests, type
check, build and a browser check pass.

**Not in this slice:** saving fits with the scan, coordinate systems, deviation against CAD,
sections, gap and flush, STEP export.

### Quality bar (every milestone)
- **Performance:** the live view holds 30 fps or more in the UI and shows the scanner's state within 150 ms. A 2M-triangle mesh orbits at 60 fps on an integrated GPU.
- **Reliability:** no data loss on a crash, and an emitters-off guarantee.
- **Tests:** the backend has unit and API tests; the UI must pass type-check and build; there's a browser smoke test of every page; the simulator drives the end-to-end checks.
- **UX:** every error says what happened and what to do. No dead ends.
