# OpenHandScan: roadmap

The long-term direction, written on 2026-11-12 from the owner's vision notes. It says what is
built, what comes next and what waits. It is not a schedule, and each item still needs a go-ahead.

The details live elsewhere:
- [`DESKTOP_APP_PLAN.md`](DESKTOP_APP_PLAN.md): the app's milestones (M4 multi-scan, M5 measure and
  reverse engineering, M6 ship it).
- [`RESEARCH_PLAN.md`](RESEARCH_PLAN.md): the algorithms, ranked, and what will not work.
- [`MAINTENANCE_PLAN.md`](MAINTENANCE_PLAN.md): fixes and speed-ups to the code as it is.
- [`../VALIDATION.md`](../VALIDATION.md): what has been measured, and how.

---

## 1. Vision

An affordable, open-source scanner whose geometry you can trust in millimetres. The first use is
reverse engineering car parts. The same tool should serve engineering, fabrication, robotics and
other fields.

**Principles:**
1. **Accuracy over appearance.** A smooth mesh is not necessarily a correct one.
2. **One engine, sensible presets.** Presets and defaults for common jobs, with every setting still
   open to change.
3. **Keep the original evidence.** Never overwrite the raw recording or the measured geometry.
   Processing makes new results next to them. Raw data is kept so a result can be checked and
   remade, not because raw data is always the most accurate.
4. **The user stays in control.** The app guides, warns and recommends; it does not block without
   need. The operator decides when a scan is finished. Warnings that are not resolved stay in the
   project and its report.
5. **Open.** MIT licence, open formats, and room for other people's methods.

**Guiding rule:** keep the evidence, automate sensible defaults, show the uncertainty, and let the
operator decide when to finish.

**On accuracy claims:** no accuracy figure is published until it has been measured against
independently measured reference parts, for that setup and those conditions. A preset does not
change what the hardware can physically measure.

---

## 2. Where we are (2026-11-12)

| Area | Status | What exists | What is missing |
|---|---|---|---|
| Raw evidence and reprocessing | **Built** | Every scan keeps its recording (frames, IMU, events, calibration). Make mesh can be rerun at another quality. Edits are stored as selections and replayed, with undo / redo. Recordings survive a crash mid-scan (B3, B14). | Retention modes (below). |
| Live preview, slower offline result | **Built** | A light live model while scanning; Make mesh offline at Standard or Best. | |
| Measurement methods | **Built** | Passive and active stereo with a dot projector: the LED projector (option B) is the build route for now, the laser + DOE projector (option A) stays available. Two calibrated laser lines. | Phase shift / Gray code, photogrammetry: only with fitting hardware. |
| Agreement between methods | **Partly** | Laser points that disagree with the frame's stereo depth are dropped. "Refine with lasers" pulls the surface onto the laser points (on the simulator, a 0.3 mm bias went from 0.24 to 0.05 mm median error). | Show the user where the methods disagreed, instead of only dropping points. |
| Confidence | **Partly** | Every depth pixel and every mesh vertex has a sigma. On simulated scans, 2 sigma covers the true error for about 96 % of pixels, and for 96-99 % of the vertices on flat surface and about 90 % at edges. The viewer's Confidence shading and the sigma under the cursor. Filled holes are "not measured". | Checked on real scans; trusted / provisional / rejected geometry; telling the user what to rescan. |
| Tracking and drift | **Partly** | Tracking state, hints when a surface cannot fix the position ("slide", "turn"), rewind, marker mode, "Straighten" (pose graph) after the scan. | Recovering after a real loss; a rescan queue; next-best-view guidance. |
| Calibration and checks | **Partly** | Guided calibration wizard, self-test, scale check of the printed board. | Valid / Check / Recalibrate status from signs of drift. |
| Validation | **Simulator only** | Accuracy, speed and confidence measured on simulated scans with known truth ([`VALIDATION.md`](../VALIDATION.md)). | Reference parts on the real hardware (waits for the hardware). |
| Presets | **Partly** | Preview / Standard / Best quality; markerless or marker tracking. | Presets by object type and size. |
| Editing and smoothing | **Partly** | Clean, fill, smooth, simplify, select, with undo / redo. | A deviation map of processed against raw; a limit on how far processing may move the surface. |
| Measuring | **Started** | Point-to-point distance in the viewer. | Fitted planes, cylinders and holes, angles, sections, gap and flush, comparison with CAD, report (M5). |
| Several sessions | **Not yet** | | Align and merge scans, also from another day (M4). |
| Scan to CAD | **Not yet** | | Fitted shapes exported as STEP or a FreeCAD package (M5). |
| Data retention | **Not yet** | | Standard / Engineering / Full modes, large projects read from disk. |
| Plugins | **Not now** | Swappable parts inside the engine (GPU or CPU, two reconstruction methods). | A public plugin API (see section 5). |

---

## 3. Next

In this order. Each one uses what is already built.

**Accuracy findings to act on first** (2026-11-12, from checking the fitted shapes against the
simulator's truth; [`VALIDATION.md`](../VALIDATION.md), "Fitted shapes against the truth"). These are
about the scan itself, not the Fit tool, and they decide whether a measurement can be trusted:
- **Holes are filled in.** Where the background behind a hole is far, the stereo depth carries
  the surface 1–3 mm into it; a Ø 25 mm hole fuses to an opening of Ø 12–14 mm. Holes are what
  car parts are mounted by. **Investigated 2026-11-12** ([`VALIDATION.md`](../VALIDATION.md), "Holes
  filled in"): the fill is the stereo matcher's windows spreading into a region with nothing to
  match, on every side of the hole, so a left-right check does not help. A guard that drops fused
  depth within 8 px of a patch without depth opens the Ø 25 mm hole to Ø 24.4–25.2, at the price
  of thinner side-wall coverage and convex features 0.2–0.5 mm smaller in diameter. **Built into
  the engine 2026-11-13 and on by default** (`PipelineParams.gap_guard_px`; `VALIDATION.md`, "The gap
  guard in the engine"): on 16 recordings fused with and without it at the same poses, the Ø 25
  hole can be measured every time (Ø 22.0–24.3 with tracking), and flat surface and the floor get
  better; but on the bracket with tracking 1–7 points of coverage are lost and the worst errors
  grow by 0.1–0.2 mm. The user chose to turn it on as it is. To do: the per-voxel variant that
  should keep the bracket's coverage. Small holes (Ø 6–10 mm) over a far, dark background stay
  closed with every fix tried: they need another cue, such as the laser lines' end points at a
  rim. To do after the decision: the Hole tool fitting the edge of the flat region instead of the
  mesh's boundary loop, for holes with a wall and a near background.
- **Convex features are fattened** (a Ø 16 mm shaft +0.2 mm at true poses, +1.1 mm with tracking)
  and tracking errors of 0.7–1 mm distort faces by up to 1.2 mm. To do: find the share of the
  stereo depth, of the fusion and of the tracking.
- **Small cylinders fit worse since the new sigma** (measured again 2026-11-13,
  [`VALIDATION.md`](../VALIDATION.md), "Fitted shapes against the truth"): a Ø 14 mm boss reads
  0.39 mm off in radius with tracking (before 0.18 mm) and its axis 12.5° off (before 8.0°),
  because the fit weighs each point by `1 / sigma²` and the new sigma is very uneven on a small
  feature. Planes, the shaft at true poses and the hole are better. A candidate, not adopted:
  equal weights for cylinders (0.22 mm and 5.4° with tracking, at the price of the axis position
  at true poses). **Waiting for a decision.**
- **The vertex sigma is 2–6 times too large**, and a fit's ± is several times too small. To do:
  an honest sigma, an error model for fits that knows the biases, and until then no ± shown as
  an accuracy. Measured since: on flat surface sigma is about 2.5 times too large, at edges and
  small features it is too small (bias), and no single factor repairs it. This blocks item 3
  below, and makes smoothing's "within 2σ" limit do nothing.
  **Investigated 2026-11-13** ([`VALIDATION.md`](../VALIDATION.md), "The vertex sigma"): the pixel
  sigma is about right; a voxel's mean of the variances is dominated by a few hugely inflated
  observations next to gaps. A candidate model is 3.4 times tighter on flat surface and larger
  at edges; with it the Check answer passes 56–58 % of a scan at ± 0.3 mm instead of 10.6 %, and
  99.8 % of that is truly within. **Adopted 2026-11-13**: built into the engine, with the same
  numbers as the prototype (58–60 % passed at ± 0.3 mm on the held-out scans, 99.8 % of it truly
  within). It costs 192 MB more per scan, 2 ms per frame and 1–2 s when the mesh is made. Item 3
  below is no longer blocked. Still to do: it cannot see a tracking error that shifts a whole
  region (that needs a per-scan pose-quality term); smoothing's "within 2σ" limit can bind now and
  a fit's ± follows the smaller sigma, and neither has been measured again; real scans.

1. **Fit simple shapes in the viewer.** Pick a region; fit a plane, a cylinder or a circle. Show hole
   diameters, plane-to-plane distances and angles, with the fit's residual and the region's
   confidence next to each number. This is the start of M5, and where "accuracy over appearance"
   becomes visible. **Built 2026-11-12** (the Fit tool, key G). Its check against the truth gave
   the findings above; open: how the ± is shown, the region's tolerance, the flatness label.
2. **A deviation map.** Show how far a processed mesh moved from the measured one (after fill or
   smooth), with a limit the user can set. Later, the same map against a reference STL.
   **Built and checked 2026-11-12** (the Deviation shading, key 5): the map agrees with the real
   change; smoothing rounds edges by up to 0.47 mm, shown undersized.
3. **Confidence the user can act on.** After Make mesh, list the regions that miss a tolerance the
   user sets ("these 3 areas need another pass"). This gives most of a rescan queue without
   next-best-view research. **Engine and API built 2026-11-12; the panel is on hold:** checked
   against the truth, the answer is safe but passes about 10 % of a scan at ± 0.3 mm where over
   90 % is truly within. It needed the honest sigma above. **The panel was built 2026-11-13**, once
   the new sigma model was in the engine: in the viewer and on the Process page, type the
   tolerance, read "58 % of the surface meets ± 0.3 mm; 12 areas need another pass", click an
   area to see it lit on the mesh. Open holes are counted, not listed. Scans made before
   2026-11-13 keep their earlier, larger sigma until their mesh is made again.
4. **Show disagreement.** Mark on the model where the laser points and the stereo depth disagreed.
5. **Small ones:**
   - start Make mesh by itself after Stop (the operator still decides when the scan ends).
     **Done 2026-11-12:** Standard quality starts right after Stop when the scan has frames; a
     switch under Settings, "After a scan", turns it off (a preference kept in the browser, on by
     default); the job can be cancelled as before;
   - show the recording backlog (`record_backlog`, B4) and a GPU fallback (`gpu_fallback`, B7) in
     the app. **Done 2026-11-12:** two warning chips next to the recording chip on the Scan page
     ("Saving is behind: N frames waiting", shown above 60 frames and hidden below 20;
     "Graphics card stopped: running slower"), and one notification when a GPU stage fails.
6. **With the real hardware:** measure against reference parts (gauge blocks, a machined part, a
   ball bar): bias, repeatability and coverage, per working distance and object size. Publish
   accuracy only from these numbers.

---

## 4. Later

- **Presets by object type** (car panel, precision part, fine detail, large object), kept separate
  from object size. Start with two or three, chosen where measurements show the settings matter.
  "Auto" may suggest one and say why; the user can always change it.
- **Calibration health:** Valid / Check recommended / Recalibration required, from tracking
  residuals, laser-stereo agreement and drift, with a guided recalibration. Calibration stays
  separate from accuracy checks.
- **Trusted, provisional and rejected geometry**, once confidence has been checked against real
  errors. Accepting a provisional area does not remove its uncertainty.
- **Several sessions** (M4): align and merge scans, continue on another day.
- **Inspection** (M5): angles, sections, gap and flush, comparison with CAD, PDF report.
- **Scan to CAD** (M5): fitted shapes to STEP or a FreeCAD package; suggested nominal dimensions
  that an engineer approves. Not "any mesh becomes CAD".
- **Data retention modes** (Standard / Engineering / Full), sized from real recordings; projects
  larger than memory read from disk; raw data removed only when the user chooses, never silently.
- **Next-best-view guidance:** where and from which angle to scan again.
- **Scanning spray** as an optional workflow, with a note that the coating adds thickness.
- **More measurement methods** (phase shift, Gray code, photogrammetry) if fitting hardware exists.
  Methods are combined only where they share a calibration and their uncertainties agree.

---

## 5. Not now, on purpose

- **A public plugin API.** A public API is a promise to keep it stable, and the engine still
  changes. The engine already swaps parts inside. Revisit when someone outside wants to plug
  something in; then add version checks, test datasets and benchmarks.
- **Many presets times many sizes.** Each combination needs its own validation.
- **Turning a mesh into STEP.** Fitted shapes are the realistic route.
- **Any accuracy claim before section 3, item 6.**

---

## 6. Open questions

From the vision notes, with what is known today.

| # | Question | Known today |
|---|---|---|
| 1 | How do we measure absolute accuracy, not only repeatability? | Reference parts on the real hardware (Next 6). |
| 2 | How do we check confidence against real errors? | On the simulator it holds (2 sigma covers about 95-97 %). Real scans: not yet. |
| 3 | Which thresholds separate accepted, provisional and rejected? | Open; depends on 2. |
| 4 | How do we resolve disagreeing measurements without smoothing away real features? | Lasers anchor the surface where they disagree. Other cases open. |
| 5 | How do we notice tracking loss and recover without spoiling the model? | Hints and rewind exist; recovery after a real loss is open. |
| 6 | How do we tell real detail from noise? | Open. |
| 7 | How should quality limits change with size, preset and task? | Open; see presets in section 4. |
| 8 | When is a measurement not good enough for the tolerance asked? | Per-vertex sigma is the basis (Next 3). |
| 9 | Which capture and compression rules keep the evidence and limit the size? | Open; needs real recordings. |
| 10 | Is offline processing reproducible across versions? | `tools/snapshot.py` compares results (A2); the CPU path varies a little between runs (A6). |
| 11 | How much does smoothing change the dimensions? | The deviation map (Next 2) will show it. |
| 12 | How does a user verify a model before making a part? | Open: a validation report plus measuring a known feature with calipers. |
