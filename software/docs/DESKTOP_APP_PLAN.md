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
- **Afterwards:** "Make mesh" runs one-click full-quality processing with a progress bar. A cancel button keeps the raw data.

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

### Quality bar (every milestone)
- **Performance:** the live view holds 30 fps or more in the UI and shows the scanner's state within 150 ms. A 2M-triangle mesh orbits at 60 fps on an integrated GPU.
- **Reliability:** no data loss on a crash, and an emitters-off guarantee.
- **Tests:** the backend has unit and API tests; the UI must pass type-check and build; there's a browser smoke test of every page; the simulator drives the end-to-end checks.
- **UX:** every error says what happened and what to do. No dead ends.
