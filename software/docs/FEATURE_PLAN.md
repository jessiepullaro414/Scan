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

**Decided by the owner (2026-11-17):**
- **D1:** go ahead with Inspection; the report is a self-contained HTML page that prints to PDF.
- **D2:** our own small STEP writer. **D3:** FreeCAD is on this PC for the round-trip check (a
  portable copy of 1.1.4 under `C:\Users\Garage\Tools\`; the installer needs an administrator
  prompt).
- **An approved relation and the CAD file** (asked after C3): the user gets the option. When a
  relation is approved, they choose whether one of its two shapes moves to suit it in the
  export, and which one, or whether it stays listed only.
- **Offers that only a known bias allows** (asked after C3): not assumed. A Ø 14.3 boss may
  well be a Ø 14.3 boss. By default only the ± makes an offer; the user can choose to see the
  bias-only ones as well, marked as such.
- **K3:** with a hole as the origin its centre is the origin; strict 3-2-1 stays a switch (no
  objection raised).

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
- **Engine done 2026-11-17.** `ohs/mesh/section.py`: the cutting plane from a point and a
  normal, a fitted plane moved along its normal, a plane of the part's frame ("Z = 12") or
  three points; `section` gives ordered lines (closed where the surface is closed) in the
  plane's 2D axes and in 3D, each point with its sigma, each stretch measured or filled, with
  lengths, signed areas and the bounding box. The drawing is seen from the side the normal
  points to, "up" is Z (Y for a nearly level cut), in the part's axes when there is a frame,
  and never mirrored. `ohs/cad/dxf.py` (R12, mm, two layers) and `ohs/cad/svg.py` (1:1, a
  scale bar) write it.
  - A cut of a real-size scan takes about 30 ms; the numbers are in `VALIDATION.md`,
    "Sections". FreeCAD 1.1.4 reads the DXF back to 1e-6 mm.
  - Tests: 42 on both paths, with a DXF reader of our own (`tests/dxf_reader.py`).
  - **Left:** the route, the viewer's panel (hide one side of the cut, the 2D profile, two
    clicks for a distance, the downloads). `section` is not exported from `ohs/mesh/__init__.py`
    because a function of that name would hide the module: import `ohs.mesh.section`.

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
- **Engine done 2026-11-17.** `ohs/inspect/reference.py`: `read_reference` (STL, OBJ, PLY;
  refusals in words; the winding made consistent, an inside-out model turned round; a
  reference about an inch, cm, m or µm factor off is a finding to confirm, never guessed), three
  alignments (`align_by_shape`, `align_by_points` with or without a refinement,
  `align_by_datums` from the part's frame with no fit), the deviation per vertex with the
  scan's sigma beside it, and the answer for a tolerance (shares by area, worst values, robust
  statistics, the areas outside as regions, each saying whether it exceeds the scan's own
  uncertainty). A best fit given the datums also says how far it is from the datum pose.
  - *Not on the reference:* scan surface more than 3 mm from the reference, outside its box
    or facing the other way (the table, clamps) counts in no share; reference area the scan
    did not cover is reported.
  - At real size (the bracket on its table, 715,226 triangles): 0.5 s to read, 4.7 s to align
    from the shapes, 0.3 s for the deviation; the pose is found with the table in the scan;
    87 % within ± 0.3 mm. Numbers in `VALIDATION.md`, "A scan compared with a reference model".
  - Tests: 12 on both paths (one marked slow, 11 s, on a cached recording).
  - *Found on the way:* the alignment engine accepted a pose turned 180° when stray points
    were left on the table; worked round here, and written up as `MAINTENANCE_PLAN.md`, F11,
    because the Align page uses the same engine.
  - **Left:** the upload route and the viewer (pick the file, confirm the units, choose the
    alignment, the deviation shading with a tolerance, the areas outside); the simulator has
    no true mesh for its scenes built from primitives (the test builds the bracket's itself).

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

### [x] C1. A STEP writer for fitted shapes (M) **(D2, D3)**
- **What:** `ohs/cad/step.py` writes the kept features as STEP (AP214): each plane as a flat face
  with its outline, each cylinder as a cylindrical face over its measured span and arc with its
  axis, each hole as a circle on its plane, points, and the part's coordinate system (K3). Names
  carry over. In the part's frame when there is one.
- **Test first:** the file read back by a small reader in the tests gives every shape's
  parameters to 1e-6 mm; the entity references are all resolved; a fixed set of test files is
  kept for the check in real CAD (D3).
- **Done 2026-11-17** (the owner: our own writer; FreeCAD on this PC for the check).
  `ohs/cad/shapes.py` (`cad_shapes`: the kept fits, their outlines and the part's frame as
  shapes to export) and `ohs/cad/step.py` (`write_step`: AP214 text, millimetres, the same
  input gives the same bytes). One named thing per shape: each plane and cylinder face, each
  hole's circle, an axis line for every cylinder and hole, and with a frame the part's X, Y and
  Z lines and its origin.
  - *A plane's edge:* by default the fit's rectangle, four clean edges. As an option
    (`boundary="outline"`) the real outline: its largest outer loop and its real holes,
    simplified to 0.5 mm (never more than 2 mm), at most 5,000 points.
  - *Found on the real scan, test added:* the first outline option gave a face FreeCAD called
    not valid on the table of a real-size scan (684 pits of scan noise kept as holes, the
    tolerance pushed to 64 mm, loops crossing; the outer loop also touches itself at 95
    points). Now holes under 3 % of the outline's size are left out as pits (at most the 50
    largest stay), the writer checks every loop itself (`face_problems`: simple, holes
    strictly inside, none touching) and never writes a face it has not checked; one that
    cannot be mended becomes the rectangle, with a note. That table: 1 mm, 2,039 points and 48
    holes, 2.0 MB, 1 s, valid in FreeCAD.
  - *In FreeCAD 1.1.4* (`tools/freecad_check.py`, run by the tests when FreeCAD is found):
    every sample opens with all shapes valid, planes as planes and cylinders as cylinders
    (a full round and a part round), the numbers equal to 6e-14 mm, and each shape under its
    fit's name, odd characters included. FreeCAD's STEP import does not show lone points, so
    the origin shows as where the three axis lines meet. Other CAD programs: not tried.
  - Sample files for a CAD program: `python tools/step_samples.py <folder>` (five files).
  - Tests: 21 on both paths (our own reader, `tests/step_reader.py`; the FreeCAD round trips
    are skipped where FreeCAD is not installed).
  - **Left:** the export in the app (C2); the hole rule looks at size only, so on a table with
    gaps in the measured surface some kept "holes" are gaps (the notes say what was left out).

### [x] C2. Export from the app, and a FreeCAD macro (S)
- **What:** "For CAD" in the viewer's Download panel: the STEP file, and a FreeCAD macro
  (`.FCMacro`, a short Python text) that builds the same planes, cylinders, circles and axes as
  named objects. A new allowed file in `RESULT_FILES` (`ohs/server/projects.py`).
- **Test first:** the route returns both files for a scan with kept features and a clear message
  for a scan with none; the macro's numbers equal the features'.
- **Done 2026-11-17.** `ohs/cad/fcmacro.py` writes the same shapes as a FreeCAD macro: a new
  document named after the part, each fit an object under its name, in groups (Planes,
  Cylinders, Holes, Axes, Points, Part frame), the notes as comments on top. A name is written
  as an escaped text constant, so it cannot run as code. `GET .../features/cad` says what
  would be exported (counts, the frame, notes, warnings, sizes), `.../features/cad/fits.step`
  and `.../fits.FCMacro` are the downloads (`?boundary=rectangle|outline`), built in memory
  from the kept fits; nothing on disk changes (`APP_API.md`, "For CAD"). They are served under
  the kept features, not as result files, so `RESULT_FILES` is unchanged.
  - The viewer's Download panel has "For CAD": STEP, FreeCAD macro, a switch "Planes with
    their real outline" with its cost on hover, and a line such as "1 fit (1 plane), in the
    scan's own frame" with the notes. Fits made on an earlier mesh are exported and named in
    a warning; an out-of-date frame is used and warned about. With no kept fit the buttons say
    why they are off.
  - The real outline's summary costs the server about a second for a large plane, so the
    panel reads it only when the switch is turned on or its help is opened (changed in
    review; it was read every time the kept fits changed).
  - *In FreeCAD 1.1.4:* the macro of every sample builds valid objects of the right kind in
    the right group, equal to the same shapes' STEP round trip to 6e-14 mm; a name full of
    quotes and code-like text comes back as the label. From the app on a real-size scan: both
    files, with the rectangle and with the real outline (4,607 edges), download and build a
    valid face.
  - Tests: 18 new (macro, routes) on both paths, the FreeCAD runs skipped where it is not
    installed; 39 UI checks.
  - **Left:** the approved sizes in the export (C3); seen only in FreeCAD's command-line
    program, not its window; a fit named like a group ("Planes") is "Planes (2)" in the macro.

### [x] C3. Suggested nominal sizes, for an engineer to approve (M)
- **What:** beside each measured value a suggestion: Ø 14.02 ± 0.03 → "14?", 89.7° → "90?", two
  planes 0.3° apart → "parallel?". A suggestion is made only where the round value lies inside
  the measurement's ±2σ *and* the known bias for that kind of feature (small bosses read
  0.15–0.25 mm small in radius; holes depend on the level they are read at). The engineer
  accepts, changes or ignores each one. The export uses accepted values and the report lists
  measured and nominal side by side. Nothing is rounded silently.
- **Test first:** a Ø 14.02 ± 0.03 boss suggests 14; a Ø 14.3 ± 0.03 one suggests nothing; an
  accepted nominal appears in the STEP file and the measured value stays in `features.json`.
- **Engine done 2026-11-17** (`ohs/cad/nominal.py`: `suggest_fit`, `suggest_relation`,
  `apply_nominal`; 25 tests on both paths). An offer names the value, the measured number and
  its ±, the round value, the difference, what allows it (±2σ alone, or a known bias, with the
  `VALIDATION.md` section it rests on) and a sentence the app can show. No offer comes with its
  reason too (already round, nothing fits, too many fit, no ±, a warning on the fit).
  - *Round values, coarsest first:* whole mm, then halves, then tenths; angles 0° and 90°, then
    30°, 45°, 60°, then whole degrees; "parallel", "perpendicular", "in one plane", "coaxial"
    for relations. Two that fit equally are both given.
  - *Known biases* are one table in the module (bosses Ø 13–17, the Ø 25 and Ø 10 holes with
    and without a wall, wall thickness 1–3 mm; true poses and tracked apart), each row quoting
    the document; a test reads `VALIDATION.md` and fails when a quote is gone. They are
    simulator numbers; where the document has none (bores, other sizes, every angle), no bias
    is used and the offer says so.
  - `apply_nominal` gives a copy of a fit's shape with the approved sizes; the measured shape
    is untouched.
  - *A choice to confirm:* the example above, Ø 14.3 ± 0.03, gets no offer as a plain cylinder
    (it is already round to a tenth). Told that it is a boss scanned with tracking, 14.5 is
    offered, marked "only because of a known bias" (bosses read 0.30–0.38 mm small in
    diameter there). That follows the rule; say if a bias alone should never make an offer.
  - **A cylinder says whether it is a boss or a bore (2026-11-17).** The offers need it: the
    two are biased differently. Each face of the fitted region votes with its area, away from
    the axis ("boss") or towards it ("bore"); 80 % must agree, else the form is unknown and a
    warning says so. It is in `RegionFit.form` (`to_dict(with_form=True)`), in the kept record
    (`form` in `features.json`; `POST .../fit` answers what it did), on the cylinder's card
    ("Form: Boss / Bore / Not known"), and `suggest_fit` reads it from a kept record. The fits
    are bit-identical; the vote costs 6 ms on a million-triangle tube. On the simulated bracket
    scan 44 of 45 cylinders clicked at random got a form. A cylinder kept earlier gets its
    form when it is next fitted again.
- **In the app, done 2026-11-17.** Approved values are kept with the scan: a `nominals` list on
  a kept fit or relation in `features.json` (which value, the nominal, suggested or entered,
  when, and the measurement it was approved against); the measured shape is never changed.
  Routes under `.../features/{id}/nominal` give the offers, approve one (the suggested value,
  or an entered one that makes sense for that kind of value) and remove it (`APP_API.md`,
  "Nominal values").
  - *In the viewer:* beside a cylinder's or a hole's diameter, and beside a kept relation's
    values, a chip such as "Ø 14?" (in the warning colour when a known bias alone allows it)
    with the engine's sentence on hover, and "Accept", "Other value…", "Ignore". An approved
    value shows as "nominal Ø 14" next to the measured one, with remove and edit. Ignoring
    hides the chip in this browser until the value is measured again.
  - *When the measurement changes:* approvals stay. One is marked **check again**, with the
    old and the new measured value, only when the new measurement no longer allows it by the
    engine's rule. Nothing is dropped or applied on its own.
  - *Tracked or true poses* (the biases differ): "true" only when the scan's report says the
    poses were given and equal to the true ones; everything else counts as tracked.
  - *The CAD export* uses approved sizes (a diameter or a radius) in the STEP file and the
    macro, lists each as "nominal 14 mm (measured 14.02 +/- 0.03 mm)" in the notes, and has a
    "Measured values only" switch that gives exactly the files without approvals. Approved
    relations (parallel, an angle, a distance) are listed in the notes and **not applied to
    the geometry**: turning one shape to suit another needs a rule for which one moves.
  - Checked in the app on the simulated bracket scan with a boss written in for the check
    (Ø 14.02 ± 0.03): the chip, Accept, "nominal Ø 14" on the card, the STEP cylinder at
    radius 7 with the approval and 7.01 with "measured only", the measured shape unchanged.
  - Tests: 14 new (routes, the file, the check-again rule) on both paths; 189 pass over the
    CAD, nominal and kept-features files; 33 UI test files.
  - **The owner's two decisions, done 2026-11-17** (see "Decided by the owner"):
    - *An approved relation can move a shape in the CAD files.* In a kept relation's card,
      "In the CAD file": Listed only (the default, as before) / Move the first fit / Move the
      second. `ohs/cad/moves.py` turns the chosen shape about its own point and slides it, by
      the smallest change, so the approved values hold on the exported shapes (checked to
      1e-9, or nothing moves); the other shape and the measured fits stay as they are. The
      notes say how far each shape was turned and slid. A shape is moved by one relation only,
      in the order the moves were chosen; one that an applied relation was measured against
      may not be moved afterwards. What cannot be applied stays listed, with the reason.
      "Listed only" gives byte-identical files to before.
    - *Offers that only a known bias allows are a choice, off by default.* The suggest
      functions take `with_bias` (off: the search runs as if no bias were known); the viewer
      has a switch "Offers a known bias allows" with what a known bias is on hover, kept as a
      preference. An approval remembers the rule it was made under, so "check again" judges
      it by that rule. The plan's Ø 14.3 boss gets no offer by default and "14.5?" (marked)
      with the switch on.
    - Checked in the app against the real engine: two planes 0.3° apart, "parallel" offered
      and approved with the second to move: the export turned it 0.3000° and reads 0° to
      1e-14; the card's three-way choice and the panel's line. (In review: long fit names
      pushed that choice past the card's edge; the labels are shorter now.)
    - Tests: 22 new for the moves (engine and routes); 211 pass on both paths over the CAD,
      nominal and kept-features files. Nine existing bias tests now ask for the bias
      (`with_bias=True`) and assert the same offers.
  - **Left:** not seen by eye: "check again", a bias chip and the bias switch against the real
    engine (the switch shows once a cylinder or a hole is kept); a moved file opened in
    FreeCAD's window. Wall-thickness biases are never offered in the app (it cannot tell that
    two planes are one wall's faces).
  - Seen on the way: `VALIDATION.md`, "Known limitations", still says small bosses read 0.1 mm
    small in radius; the newer sections say 0.15–0.28 mm. And
    `test_an_edit_marks_a_fit_and_fit_again_brings_it_back` failed once for the agent in a
    CPU-path run (the list of refitted fits differed); it passed 6 of 6 times straight after
    and in every run since. Noted, not chased.

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
