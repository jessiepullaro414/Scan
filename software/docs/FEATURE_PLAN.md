# Feature plan: the six "Later" items

Written 2026-11-17 from the code as it is at `main` = 274239b. It covers the six larger items of
[`ROADMAP.md`](ROADMAP.md), section 4, that do not wait for anything else:

| | Item | What the user gets |
|---|---|---|
| **K** | Kept features and a coordinate system | Fits and measurements stay with the scan, have names, and the part can be set on its own axes. Not one of the six: it is what Inspection and Scan to CAD both stand on. |
| **I** | Inspection | Sections, comparison with a reference model, gap and flush, a report. |
| **C** | Scan to CAD | The fitted shapes as a STEP file and a FreeCAD macro, with suggested nominal sizes to approve. |
| **H** | Calibration health | Valid / Check recommended / Recalibration required, with the step to redo. |
| **N** | Next-best-view guidance | Where to scan again, and from which side. |
| **R** | Data retention | How much raw data a project keeps, what it costs on disk, and a safe way to free space. |
| **P** | Presets by object type | "What are you scanning?" sets sensible values; every value stays open. |

**This is a proposal.** As the roadmap says, each item still needs the owner's go-ahead. Nothing
here is started.

## Rules

The rules of [`MAINTENANCE_PLAN.md`](MAINTENANCE_PLAN.md) hold: one task, one commit; the test is
written first and seen to fail; tests are never loosened; engine work is tested on the GPU path
and with `OHS_COMPUTE=cpu`. On top of them:

1. **Defaults do not move.** A task may add an option that is off, or a file next to the existing
   ones. Anything that changes a scan's result, a mesh edit's output or a file the app already
   writes waits for the owner's yes. Such tasks are marked **(owner)**.
2. **No new dependency without a yes.** Marked **(dep)**. The plan is written so that the first
   version of every item needs none.
3. **Measured before claimed.** Each item has a measuring task with a stop rule. What the
   simulator cannot show waits for the real scanner and is marked **(hw)**.
4. **The app warns, it does not block** (roadmap, principle 4). Health states, weak areas and
   retention warnings are shown and written into the report; none of them stops a scan.
5. Written up in `VALIDATION.md` (numbers) and `APP_API.md` (routes) in the same commit.

Sizes: **S** up to half a day, **M** one to two days, **L** several days. They are guesses.

## Suggested order

1. **K1–K3** (kept features, extents and relations in the engine, coordinate system).
2. **C1–C2** (STEP and FreeCAD export): small once K is there, and the first thing a
   reverse-engineering user asks for.
3. **I1–I4** (sections, reference comparison, report, then gap and flush).
4. **H1–H4** (calibration health), best before the first real scans, because that is when a
   calibration will first go stale.
5. **N1–N2** (where to scan again, a second pass).
6. **R1–R3** (disk use, freeing space, modes).
7. **P1–P3** (presets), last on purpose: the roadmap asks for presets "where measurements show
   the settings matter", and real scans will say more than the simulator.

K comes first whatever else is chosen for I or C. H, N, R and P do not depend on K or on each
other, so any of them can be taken first.

## Decisions for the owner

| # | Question | Suggestion |
|---|---|---|
| D1 | How is the report made into a PDF? | First a self-contained HTML report that prints cleanly (the system's "Save as PDF"), no dependency. A PDF library (fpdf2 or reportlab, a few MB) only if a one-click PDF file is wanted. |
| D2 | STEP: our own small writer, or OpenCascade (OCP / CadQuery)? | Our own writer for planes, cylinders, circles, points and axes. It is a text format and these shapes are simple. OpenCascade is LGPL and would add hundreds of MB to the exe; it is only needed to *read* STEP or to write free-form surfaces, and neither is planned. |
| D3 | How do we know a STEP file opens in real CAD? | Tests read the file back with a small reader of our own. Beyond that, either the owner opens the test files in their CAD program, or FreeCAD is installed on this PC for a round-trip test (an install: needs a yes). |
| D4 | Retention: which mode is the default? | Keep everything, as today, until real recordings show the sizes. The roadmap's three names stay (Standard / Engineering / Full); the default is the owner's choice. |
| D5 | Presets: which ones, and what are they called? | Decided after P1's measurements. "General" is today's behaviour, unchanged. |
| D6 | May `mesh.ply` carry one more value per vertex (how many independent looks it had)? | Needed by N1 to say *why* an area is weak. It adds a property and changes no existing one. |

---

## K. Kept features and a coordinate system

**Today:** a fit lives in the viewer's memory (`useState` in `ui/src/pages/ViewerPage.tsx`) and
is gone when the page closes. Fits are numbered, not named. The engine gives a plane no outline
and a cylinder no length; the UI works those out for drawing (`summarizeRegion`,
`ui/src/lib/fits.ts`). Relations between two fits (distance, angle) are computed in the UI only,
and only the distance between parallel planes has a ±. There is no coordinate system: every
number is in the scan's own frame.

### [x] K1. Fits and measurements are kept with the scan (M)
- **What:** `scans/<sid>/features.json` (with a `format_version`): each feature has an id, a name
  the user can change, the kind, the fitted shape with its ± values, the click it was grown from
  (point, kind, tolerances), its warnings, and the mesh version it was fitted on. Two-point
  measurements and relations between two features are kept in the same file.
- **Where:** new `ohs/measure/features.py` (the records, load, save, validation); routes
  `GET/POST/PATCH/DELETE /api/projects/{pid}/scans/{sid}/features` beside the fit route
  (`ohs/server/app.py:537`, `ProcessManager.fit`); the viewer's Fits and Measurements panels
  read and write through a store instead of `useState`.
- **When the mesh changes** (an edit, Make mesh again): the features stay, marked "fitted on an
  earlier mesh", with one button to fit them again from their saved clicks. Nothing is refitted
  silently.
- **Test first:** save, reload and rename over the API; a feature saved on mesh version 3 is
  marked after an edit and comes back within its ± after "fit again".
- **Done when:** closing and reopening the viewer shows the same named fits.
- **Done 2026-11-17.** `ohs/measure/features.py` (the file, checked on every read and write,
  written through a temporary file), `ohs/server/features.py` and eight routes under
  `.../features` (`APP_API.md`, "Kept features"). A fit click in the viewer now keeps the fit;
  fits, distances and relations can be renamed (double-click, or the pencil); each fit's region
  is kept in its own file (`features/<id>.npy`) for the mesh it was fitted on. After an edit a
  fit shows "Fitted on an earlier mesh" and one "Fit again" button refits all such fits on the
  server from their saved clicks; one that cannot be refitted stays, marked, with the reason.
  - *Found in review, test first:* a fit whose whole region had been cut away was fitted again
    on the nearest other surface, under the same name. Now refused ("the surface under this fit
    is gone") when the nearest measured face is more than 2 mm and 1.5 of its own longest edge
    from the saved click.
  - Checked in the app on a real scan of 804,382 triangles: a plane kept, renamed, there again
    after a reload; marked after an edit; fitted again with its name.
  - Tests: 37 on both paths (the file, the routes), 51 UI checks (the mock routes, the store).
  - `extent`, null at first, is filled since K2 reached the app (below).

### [x] K2. Extents and relations move into the engine (M)
- **What:** `RegionFit` gains the extent of its region: a plane's outline (the region's boundary
  projected onto the plane, plus its oriented rectangle), a cylinder's span along its axis and
  the arc it covers, a hole's edge loop (`RegionFit.edge` exists; `processing.py:1808` does not
  send it). New `ohs/measure/relate.py` with the relations the UI has today (`relate` in
  `fits.ts`) plus the distance between two hole centres, each with a ± from both fits.
- **Why in the engine:** the report (I3) and the CAD export (C1) need the same numbers the viewer
  shows. One source, and the UI displays it.
- **Test first:** on the synthetic meshes of `tests/test_measure_*` the engine's relations equal
  the UI's numbers to 1e-6; ±2σ covers the true relation about 95 % of the time over seeded
  trials, as for the fits themselves.
- **Engine done 2026-11-17.** `RegionFit.extent` (`region_extent` in `ohs/measure/region.py`;
  in `to_dict(with_extent=True)`, so today's answers keep their keys) and
  `ohs/measure/relate.py` (`relate`, with a 1σ on every relation that can have one). The values
  equal the viewer's on a shared set of cases; ±2σ covers the truth 94.5–98 % of the time; the
  fits are unchanged (`VALIDATION.md`, "Relations between two fits, and where a fit's region
  lies").
  - *Found by trying it in the app:* the first version's plane outline took 23 s on a real-size
    scan (every loop tested against every other; the synthetic meshes have a handful of loops,
    the scan's table 2,810). Now 0.12–0.14 s there, with a test on a plate of 529 holes.
  - **Known limits:** a hole's direction has no ±, so angles with it have none; the ± of skew
    axes is up to 1.4 times off on part-round cylinders (the fits keep no full covariance).
- **In the app, done 2026-11-17.** A kept fit carries its extent: the small part in
  `features.json` (a plane's rectangle, area and loop counts; a cylinder's span and arc; a
  hole's edge up to 128 points), a plane's outline in its own file
  (`features/<id>.outline.npz`, served by `GET .../features/{id}/outline`). `POST .../fit`
  answers exactly what it did. Kept relations and the viewer's two-fit card show the engine's
  record with its ± (`GET .../features/relate?a=&b=`); where the engine has no ±, the card says
  why in the engine's words. Each fit's card shows its size (a plane's two sides and area, a
  cylinder's length and arc), and the 3D outline is the engine's rectangle or span and arc.
  - A fit kept before this has no extent: a note with "Fit again" measures it on its own mesh
    (the shape stays identical, so the part's frame stays current).
  - On a real-size scan (804,372 triangles; the table's outline 60,627 points in 720 loops):
    the kept fit's record is about 1.3 kB and `GET .../features` answers in 18 ms; the outline
    is 2.4 MB as JSON, in its own file; keeping the fit takes 1.5 s, the fit's own time.
  - Checked in the app on that scan: the note and "Fit again", the size and area, the rectangle
    drawn, the relation card with the engine's ±.
  - Tests: a new file for the routes (`test_server_extent.py`), 383 pass on both paths over the
    measure and kept-features files; 28 UI test files.
  - `test_server_features.py` asserted a kept fit's extent was None (K1's state); that line
    now asserts the plane's extent.
  - **Left, seen on the way:** writing a 1.27M-face region's file takes about 0.8 s on the
    simulated door scan (K1's `save_faces`): worth a look. Not seen by eye: a cylinder's arc
    and span drawn in 3D (no cylinder on the scans at hand).

### [x] K3. A coordinate system for the part (M)
- **What:** the 3-2-1 rule from kept features: a plane gives the first axis, a second plane or an
  axis gives the direction of the second, a hole centre or a point gives the origin. Stored in
  `features.json` as the part's frame. The viewer shows the axes and can show every number in
  that frame. Downloads stay in the scan's frame unless the user picks "in the part's frame".
- **Where:** `ohs/measure/frame.py`; a "Coordinate system" panel in the viewer.
- **Test first:** on a box with a hole, the frame built from (top face, side face, hole) puts the
  hole's centre at the origin and the faces on the axes to within their ±; a mesh exported in the
  frame and loaded again has the hole at the origin.
- **Engine done 2026-11-17** (`ohs/measure/frame.py`, `part_frame` and `PartFrame`; 151 tests on
  both paths). The primary is a plane; the secondary a side face, an axis lying along the
  primary, or a second hole across it ("face, hole, hole"); the origin a hole, a cylinder, a
  point or a third face (a corner). Signs come from where the features lie, never from a
  normal's sign, so the same features always give the same frame; two switches flip an axis.
  - *A choice made:* with a hole as the origin, the hole's centre **is** the origin (the test
    above asks for it), which is "level, turn, origin" as measuring machines do it: the primary
    sets three degrees of freedom, the secondary one, the hole two. The strict 3-2-1 count (the
    side face is X = 0 and the hole sets only Y) is the option `secondary_zero`; three faces are
    strict 3-2-1 by themselves.
  - The frame's ± is carried over from the fits'; ±2σ covers the true frame as it should
    (`VALIDATION.md`, "The part's coordinate system").
- **Server and viewer done 2026-11-17.** `POST / DELETE .../features/frame` build the frame from
  three kept fits and keep it under `frame` in `features.json`; a refusal is answered in the
  engine's words, and a fit made on an earlier mesh may not be used. `GET
  .../features/frame/mesh.ply` and `mesh.stl` give the current mesh in the part's frame; the
  ordinary downloads are byte-identical with or without a frame (`APP_API.md`, "The part's
  frame").
  - The viewer's "Coordinate system" panel: three slots filled from the kept fits, the axis
    names, two flips and "Strict 3-2-1", each with a card on hover; the frame's ± and warnings;
    the part's axes drawn at its origin; one switch, "Numbers in the part's frame", for the
    positions and directions in the fits and measurements panels (sizes, distances, angles and
    ± do not change with the frame); "STL / PLY in the part's frame" in the Download panel.
  - The frame is **out of date** when a fit it was built from was deleted, fitted again or
    changed; it is then shown dashed with the reasons and a "Build again" button. Nothing is
    rebuilt on its own. A rename does not count.
  - Each fit's card now also shows its position and direction (it showed none before, so the
    switch would have had nothing to change).
  - `ProcessManager.mesh_arrays` is the public way to the current mesh; the kept features no
    longer read private members (K1's note).
  - Checked in the app on a simulated bracket scan (715,226 triangles) with one real fit and two
    made up for the check: built, the axes drawn, the hole's centre reads 0, 0, 0 with the
    switch on, the STL in the part's frame downloads with all its triangles.
  - Tests: 14 for the routes and 3 for the file on both paths, 60 UI checks.
  - **Left:** picking a point (not a kept fit) as the origin in the viewer (the route takes
    one); the frame's ± on meshes of simulated scans.

---

## I. Inspection

**Today:** fits (plane, cylinder, hole), a two-point ruler, the deviation map of an edited mesh
against the measured one (`ohs/mesh/offset.py`, `surface_offset`), the Check answer, the Lasers
shading. No section or clipping plane, no reference model, no report other than the processing
`report.json`. Mesh readers for PLY, STL and OBJ exist (`load_mesh`, `ohs/track/export.py:405`)
but nothing can be uploaded.

### I1. Sections (M)
- **What:** cut the mesh with a plane: a kept plane moved along its normal, a plane of the part's
  frame (K3), or three clicked points. The engine returns the cut as ordered lines in the plane's
  own 2D coordinates, each point with its sigma. The viewer hides one side of the cut and shows
  the 2D profile in a panel, where two clicks measure a distance. Download as **DXF** and **SVG**
  at 1:1 in mm (both are text formats: our own writers, no dependency).
- **Where:** new `ohs/mesh/section.py`; route `POST .../section`; `viewer/SectionPanel.tsx`.
- **Test first:** a box cut square gives a rectangle of the true size, closed; a cylinder cut at
  an angle gives an ellipse with the right axes; the DXF read back gives the same points; a cut
  through a filled (not measured) area marks that stretch as not measured.

### I2. Comparison with a reference model (L)
- **What:** load an STL, OBJ or PLY as the reference, align the scan to it, and show the
  Deviation shading against it with a tolerance: "93 % within ± 0.5 mm", and the areas outside.
- **Where:** an upload route that stores the file under `<project>/references/`; alignment
  through `scan_cloud_from_mesh` and `align_pair` (`ohs/align/pairwise.py`), started from the
  shape, from picked point pairs (the Align page has both) or from the part's frame (K3); the
  map through `surface_offset`, which already takes any reference mesh.
- **Two ways to align, and the report says which:** a best fit hides an overall shift or bend; an
  alignment on datums (K3) shows it. Both are offered.
- **Units:** a reference whose size is 25.4 or 1,000 times off is asked about, not guessed.
- **Not in this task:** a STEP file as the reference (it needs OpenCascade to read, D2).
- **Test first:** a simulated scan against its scene's own mesh reads the known scan error (the
  numbers of `VALIDATION.md`); the same scan against a reference with one face moved 1 mm shows
  that face 1 mm off and the rest unchanged.

### I3. The report (M) **(D1)**
- **What:** one page per scan: project and scan, date, calibration and its health (H3), how the
  scan was made (mode, quality, preset), the Check answer at the user's tolerance, the kept
  features with their ± and names, relations, sections, the comparison with the reference, and
  every warning that was not resolved (principle 4). Pictures of the model with the features
  marked. Also the features as CSV and JSON.
- **Where:** `ohs/report/` builds one self-contained HTML file (pictures inside it); the viewer's
  Download panel gets "Report". Pictures come from the viewer's canvas, rendered to an image on
  request (nothing does that today).
- **Stated in the report:** a ± is the spread from scan to scan, not the accuracy; no accuracy
  figure until the reference parts have been measured (roadmap, "On accuracy claims").
- **Test first:** the report of a simulated scan with three kept features holds exactly those
  names and numbers; an unresolved warning appears; the file opens with no network access.

### I4. Gap and flush (L, an experiment)
- **What:** between two panels: the user clicks either side of the seam; the engine follows the
  seam, takes a section across it every few millimetres, and reads the gap (edge to edge, in the
  panels' surface) and the flush (the step between the two surfaces). Result: lowest, mean and
  highest along the seam, and a small chart.
- **The risk:** a scanned edge is rounded and can be 0.2–0.5 mm off (`VALIDATION.md`, the gap
  guard and the holes), and a gap is two edges. Flush reads surfaces, which the scanner does
  well.
- **Measure first:** a new simulator scene of two panels with a known gap (2–6 mm) and flush
  (0–2 mm); scan it tracked and at true poses.
- **Stop rule:** flush is shipped if it reads within 0.2 mm. Gap is shipped only if its bias is
  steady enough to state (the same within 0.2 mm across the scene's gaps); otherwise the tool
  shows flush alone and says why.

---

## C. Scan to CAD

**Today:** nothing. The roadmap's rule stands: fitted shapes, not "any mesh becomes CAD".

### C1. A STEP writer for fitted shapes (M) **(D2, D3)**
- **What:** `ohs/cad/step.py` writes the kept features as STEP (AP214): each plane as a flat face
  with its outline, each cylinder as a cylindrical face over its measured span and arc with its
  axis, each hole as a circle on its plane, points, and the part's coordinate system (K3). Names
  carry over. In the part's frame when there is one.
- **Test first:** the file read back by a small reader in the tests gives every shape's
  parameters to 1e-6 mm; the entity references are all resolved; a fixed set of test files is
  kept for the check in real CAD (D3).

### C2. Export from the app, and a FreeCAD macro (S)
- **What:** "For CAD" in the viewer's Download panel: the STEP file, and a FreeCAD macro
  (`.FCMacro`, a short Python text) that builds the same planes, cylinders, circles and axes as
  named objects. A new allowed file in `RESULT_FILES` (`ohs/server/projects.py`).
- **Test first:** the route returns both files for a scan with kept features and a clear message
  for a scan with none; the macro's numbers equal the features'.

### C3. Suggested nominal sizes, for an engineer to approve (M)
- **What:** beside each measured value a suggestion: Ø 14.02 ± 0.03 → "14?", 89.7° → "90?", two
  planes 0.3° apart → "parallel?". A suggestion is made only where the round value lies inside
  the measurement's ±2σ *and* the known bias for that kind of feature (small bosses read
  0.15–0.25 mm small in radius; holes depend on the level they are read at). The engineer
  accepts, changes or ignores each one. The export uses accepted values and the report lists
  measured and nominal side by side. Nothing is rounded silently.
- **Test first:** a Ø 14.02 ± 0.03 boss suggests 14; a Ø 14.3 ± 0.03 one suggests nothing; an
  accepted nominal appears in the STEP file and the measured value stays in `features.json`.

**Not planned here:** sphere and cone fits, free-form surfaces, automatic CAD from a mesh
(`RESEARCH_PLAN.md` 4.8.7–4.8.8).

---

## H. Calibration health

**Today:** a row-error check on three idle frames at each connect (`ohs/calib/drift.py`,
`drift_check`, limit 0.6 px), shown as a drift alert; the 2-minute board check; each wizard step
can be redone alone, and redoing stereo drops lasers and IMU (`merge_calibration`). The
calibration file has no date; dates and the last check are in `<data_dir>/setup.json`. Each
recording carries a copy of its calibration. During a scan nothing watches the calibration:
laser points that disagree with the depth are counted (`n_rejected_by_depth`), not measured; the
rotation the tracker finds against the gyro's (`jump_deg`) is not saved.

### H1. Each scan writes down what it saw of the calibration (M)
- **What:** a `health` block in the scan's `report.json`:
  - the row error on every 30th frame (`row_error`; done while processing, so the live frame
    pays nothing);
  - per laser line, the signed median distance between its points and the stereo depth, before
    the drop (today only the count of dropped points);
  - the laser points against the finished mesh (`laser_agreement`, `ohs/mesh/anchor.py:172`);
  - the tracker's median residual, and its rotation against the gyro's (`jump_deg`, added to
    `_frame_row` in `ohs/app/process.py`);
  - what Straighten had to correct (`straighten.json`);
  - the temperature at the scan and at the calibration.
- **Also:** the calibration file gains a creation time, an id and the temperature, as optional
  keys (old files load as before; `ScannerCalibration` must carry them through a save).
- **Test first:** the block is there with every key on both paths; the mesh and the poses are
  bit-identical with and without it.

### H2. Which signs show a stale calibration, measured (M)
- **What:** process the same simulated recordings with a calibration that is slightly wrong:
  the cameras' angle (0.01–0.1°), the baseline (0.1–1 %), a laser plane's tilt and offset, the
  IMU's rotation and time offset. For each: how far each sign of H1 moves, and how far the mesh
  and its measurements move.
- **Stop rule:** a sign is used only if it moves clearly before the mesh is 0.1 mm off, and does
  not move as much on a hard but well-calibrated scene (dark, shiny, thin walls). The rest are
  dropped from the status and stay in the report.
- **Result:** a table in `VALIDATION.md` and two limits per kept sign ("check", "recalibrate").

### H3. The status (M)
- **What:** `ohs/calib/health.py` joins the launch check, the age of the last board check and the
  last scans' health blocks into **Valid**, **Check recommended** or **Recalibration required**,
  with the part concerned (cameras, lasers, IMU) and the reason in words. It is part of the
  setup state the app already reads, and is written into each scan's report.
- **Test first:** with H2's perturbed calibrations the status names the right part; with the true
  calibration it stays Valid on every simulated scene, including the hard ones.

### H4. In the app (S–M)
- **What:** the state on the device chip and the setup overview; a line on the Scan page when it
  is not Valid; on a scan's card and in its report, "scanned with a calibration that needed a
  check". The button goes to the one step to redo (the wizard already has "Redo" per step and
  the `recalibrate_stereo` action). Scanning is never blocked.

### H5. Limits from the real scanner **(hw)**
The simulator has no warm-up, no knocks and no loose screws. The limits of H2 are provisional
until scans on the real hardware over days and temperatures say otherwise.

**Not planned here:** correcting a calibration by itself during scans (`RESEARCH_PLAN.md`
4.9.4–4.9.5).

---

## N. Next-best-view guidance

**Today:** the Check answer lists the areas that miss a tolerance, each with a centre, a size
and a kind ("uncertain" or "not measured") (`ohs/mesh/check.py`, `weak_areas`). The viewer works
out a direction to *look* at an area, not one to *scan* it from. While scanning, the live model
is coloured by how many frames saw each 8 mm cell (`CoverageGrid`, `ohs/server/meshing.py`). A
finished scan cannot be continued; a second scan can be aligned and merged with the first.

### N1. Why an area is weak, and where to scan it from (L) **(D6)**
- **What:** each area gets a reason and a suggested pose.
  - *Reason:* not seen at all (a hole or a shadow); seen only at a steep angle; seen too few
    times; seen from too far. For "too few times" the mesh must carry each vertex's count of
    independent looks (`nlook`, already in the model, dropped at `extract_mesh`): D6.
  - *Pose:* a few candidate directions around the area's normal at the working distance; each is
    tested against the mesh for being blocked by the part itself and for looking into the part;
    the best one is kept, with how much of the area it would see.
- **Where:** `ohs/mesh/check.py` (reason, normal) and new `ohs/guide/views.py` (the poses); the
  Check panel shows, per area, a small picture of where to hold the scanner.
- **Test first, on the whole loop:** scan the simulated bracket from one side only; take the
  suggested poses; let the simulator scan from them; merge. The share of the surface that meets
  the tolerance must rise, and by more than with the same number of random poses.

### N2. A second pass that joins the first (M)
- **What:** "Scan these areas" from the Check panel starts a new scan that knows which scan it
  belongs to, with the first model shown faintly and the areas lit. After Stop the two are
  aligned and merged (the engine of the Align page), and the Check answer is shown before and
  after.
- **Said plainly in the app:** a merge adds coverage; it does not yet make the well-seen surface
  more accurate (`VALIDATION.md`, "Aligning and merging two scans").
- **Test first:** two simulated passes of the bracket through this route give one merged scan
  whose Check share is higher than the first pass's.

### N3. Hints while scanning (L, an experiment)
- **What:** during the scan, areas the model has seen too little or too steeply are lit, with one
  hint at a time ("come round to the left", "tilt down"), through the filter the slide and turn
  hints already use (`WeakHintFilter`, `ohs/server/session.py`).
- **Stop rule:** kept only if, on simulated scans that follow the hints, the Check share after a
  fixed scan time is clearly higher than without, and the frame time does not grow.

---

## R. Data retention

**Today:** a scan keeps everything: one PNG per frame with both eyes (`recording/frames/`), the
IMU and events, its calibration; a folder for each stage (`process/track`, `straighten`,
`fuse`), each with its own mesh; the undo cache (the base and 10 steps, about 21 MB a step at a
million triangles); the result. A deleted project goes to `.trash`. There is no way to remove a
recording and keep the mesh, and the app shows no disk use. A simulated frame is about 1.3 MB;
if real frames are that size, a 2-minute scan at 60 frames a second is about 9 GB. Real sizes
have not been measured (roadmap question 9).

### R1. Show what is on disk (S)
- **What:** per scan and per project, the size of the recording, the stages, the undo cache and
  the result; shown on the project page. A table in `VALIDATION.md` for the simulated scans:
  bytes per frame and per second, and each part's share.
- **Test first:** the sizes the route reports equal the files' sizes.

### R2. Free space, on purpose (M) **(D4)**
- **What:** the three modes, as what a scan keeps:
  - **Full:** everything, as today.
  - **Engineering:** the recording, and the result. Stage folders and the undo cache are removed
    (they can be rebuilt from the recording). Every number can still be checked and the mesh
    remade.
  - **Standard:** the result with its sigma, the laser points, the path, the report, the
    calibration and the kept features. No recording: the mesh can still be edited, measured and
    exported, but not remade, merged or refined from the images.
- A scan is moved to a lighter mode by the user, per scan or for a project. The dialog says how
  much is freed and what can no longer be done, and asks once more for Standard, because that
  cannot be undone. New scans are kept in full unless the owner decides otherwise (D4). Nothing
  is removed silently (roadmap).
- **Where:** `ohs/server/projects.py` (the mode in `project.json`), `ohs/server/processing.py`
  (`_need_recording` already answers "this scan has no recording").
- **Found on the way, its own small task:** "Refine with lasers" is refused without a recording
  although it only reads the stage's laser points.
- **Test first:** after Engineering, Make mesh gives the bit-identical mesh; after Standard,
  edits, fits, the Check answer and downloads work and Make mesh answers with the clear refusal.

### R3. Keep only the frames the mesh used (M, an experiment)
- **What:** Standard quality uses 30 of the 60 frames a second. A recording thinned to those
  frames (still lossless) would halve Engineering.
- **Stop rule:** adopted only if the Standard mesh from the thinned recording is bit-identical,
  and Best quality's loss is stated in the dialog.

### R4. Smaller frames **(hw)**
Stronger lossless compression, or a lossy one for the frames between key frames, is decided on
real recordings (question 9). PNG's level is 1 today; the trade of size against saving speed is
measured in R1.

### R5. Projects larger than memory (L, measure first)
- **What:** build a large project (eight scans at Best quality, the most the Align page takes at
  once) and record the peak memory of each page and job. Only what fails or swaps gets fixed. The likely places: the
  cache files are read whole (`_load_npz`, no memory mapping), `load_ply` reads the whole file,
  and the viewer holds every mesh it shows.
- **Stop rule:** if that project works on this PC without swapping, the task ends with the
  numbers written down.

---

## P. Presets by object type

**Today:** the choices are the quality (Preview / Standard / Best), the tracking mode
(markerless / markers), the voxel size at the Fuse stage, thin walls (auto / on / off), and four
built-in recipes for the edits after meshing ("Default", "Body panel", "Bracket / mechanical",
"Quick preview"; `BUILTIN_RECIPES`, `ohs/server/processing.py:137`). There is no object type and
no object size anywhere in the engine or the app.

### P1. Which settings matter for which object (M)
- **What:** on the simulated scenes (the car panel, the bracket, the sphere, and the large and
  detailed parts of `VALIDATION.md`, "Large and detailed parts") vary the handful of settings a preset could hold: voxel size, quality, thin walls, the gap
  guard, the depth range, the recipe (fill size, smoothing strength and where). For each: error,
  coverage, the fits' errors, time.
- **Stop rule:** a preset is made only where a setting changes a result by more than the spread
  from scan to scan. If two object types end with the same values, they are one preset.
- **Result:** a table in `VALIDATION.md` and the list of presets for the owner (D5).

### P2. A preset is a named bundle, and "General" is today (M)
- **What:** `ohs/app/presets.py`: a preset names values for the scan (mode, depth range), for
  Make mesh (quality, voxel, thin walls), a recipe, and a tolerance for the Check answer. Kept
  like recipes: built-in ones in the code, the user's own in `<data_dir>/presets/`. The scan
  stores the preset's name and every value it resolved to, in its recording and its report, so a
  result can be traced.
- **Test first:** with "General" the mesh is bit-identical to today's; a preset's values reach
  the pipeline; a value changed by hand wins over the preset and is recorded as changed.

### P3. In the app (M)
- **What:** "What are you scanning?" in the Scan page's setup, with a card on hover for each
  preset that says what it changes and why (as for "Where to smooth"). Every value it sets stays
  visible and can be changed (roadmap, principle 2).

### P4. Later
- **Object size** (small / medium / large), kept apart from the type: it sets the depth range
  and the voxel, and a large object meets the tracking model's 768 mm cube.
- **"Auto" suggests a preset** and says why, from the first frames (the detail probe exists).
- **Surface presets** (dark, shiny): exposure and emitter levels. **(hw)**

---

## What this plan leaves out

- Anything the roadmap marks as waiting for the real scanner, apart from the tasks marked
  **(hw)** above, which only say what will be measured then.
- A public plugin API, phase shift, Gray code and photogrammetry (roadmap, sections 4 and 5).
- A merged model more accurate than its best scan (M4): that waits on each scan's own surface
  error, not on these features.
