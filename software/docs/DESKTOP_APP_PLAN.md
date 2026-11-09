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
| GPU lock-in | NVIDIA RTX 30/40 for GPU mode (MetroX) | – | GTX 1060-class recommended | GPU recommended | – | – | **CPU works everywhere; GPU optional** |

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
   - **Our fix:** a CPU path everywhere (the engine already runs on CPU), with optional CUDA.
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
6. **Runs on ordinary laptops:** CPU-only works, no GPU required, and it ships as one installer.

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
| M2 | Setup and calibration wizard | first-run flow, live coverage heatmaps, auto-capture, plain-language results, launch-time check |
| M3 | Processing studio | step-by-step pipeline with preview and undo, recipes, selection tools, clean / fill / smooth / decimate, global pose-graph optimisation |
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

### Quality bar (every milestone)
- **Performance:** the live view holds 30 fps or more in the UI and shows the scanner's state within 150 ms. A 2M-triangle mesh orbits at 60 fps on an integrated GPU.
- **Reliability:** no data loss on a crash, and an emitters-off guarantee.
- **Tests:** the backend has unit and API tests; the UI must pass type-check and build; there's a browser smoke test of every page; the simulator drives the end-to-end checks.
- **UX:** every error says what happened and what to do. No dead ends.
