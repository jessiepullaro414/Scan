# UX plan: a review of the whole app, and what to mend

Written 2026-11-20 from a review of OpenHandScan Studio as its user meets it: a maker scanning car
parts in a garage, not a metrology expert, perhaps without a graphics card. An Opus agent walked
every flow in the in-browser demo (`?mock=1`) at 1280x800, 1024x700 and 800x700, read the page
sources for the states the demo cannot reach, and went through every error text of the server
(about 370 places). Nothing was changed for it. The real engine was not used; what must be checked
there is in the last section.

Each finding is a **defect** (plainly wrong, or against the project's own rules: mend it) or a
**choice** (the owner decides; the reviewer's pick is given). Sizes: S, M, L. The numbering is
U1 to U50, by how much it hurts.

**Decided by the owner (2026-11-20): fix all of them.** Every finding marked "choice" goes ahead
with the reviewer's pick as written here. The defects are mended first; then the choices, in
three waves: the names and words (U2, U3, U4, U17, U37, U47 and the table "One name for each
thing"), the structure of the pages (U11, U12, U15, U25, U26, U44, U45, U49), and what needs
new work in the server too (U5, U7 with U50, U14, U21, U34).

**Done so far (2026-11-20), the defects in the pages.** Ticked below: U1, U6, U16, U18, U19, U20,
U22, U23, U24, U27, U28, U29, U30, U32, U33, U35, U36, U38, U40, U41, U42, U43, U46. Each was
looked at in the demo except where it says otherwise here. Checked in the code and by its test
only, because the demo does not reach the state: the laser warning before a real scanner's
self-test (U18), the amber "Calibration loaded" under "Recalibration required" (U46), the focus
on Cancel in danger dialogs (U6), "Making the mesh" after Stop (U30), "1–7" with a CAD model
loaded (U25). Not ticked yet, with what is left:

- **U8, U48:** the pages' half is done (the words on the pages, "Calibration valid"). Left in
  the server, because the pages use the same names and both sides change together in the first
  wave of choices: "Your calibration still holds", "2-minute check", "σ" in the Smooth labels,
  "Remove loose pieces", "the part's frame" in `frame_label`, the stereo and "laser planes" words
  in the calibration's metric rows.
- **U25:** the key ranges are right ("1–7" from the modes that work); the shortcut sheet is the
  choice half.
- **U31:** the recipes and the stereo and laser verdict sentences are now compared with the
  server's by a test (`api/mock/recipes.test.ts`, `lib/calibWords.test.ts`); the check headline
  and the motion sensor's sentence follow once the server has its new words.
- **U39:** the folder paths are gone and the size is a visible link ("1.15 GB · Free space…");
  "Open the project folder" needs a route in the server (today only the printables folder opens),
  so it goes with the third wave.
- **U32** is complete for every number the pages write; texts that come whole from the server
  still have a plain space before the unit.

**Done (2026-11-20), the server's words: U9, U10, U13.** About 530 texts in 23 files of
`ohs/server`. How it holds from now on:

- Every error's detail and hint pass through `protocol.sentence()` (a capital, a full stop), so
  the pages no longer have to repair them.
- A caught exception reaches the user only through `protocol.plain_reason()`: a sentence written
  for a person passes, anything that reads like a program's goes to the log and a fixed plain
  sentence is shown instead.
- A job that fails on a program error says fixed words for its kind (`jobs.FAILURE_TEXT`: what
  failed, that the recording is safe, what to try); the exception is in the log.
- No ids, routes, command lines, Python quotes or parameter names in what the user reads. The
  guard is `tests/test_server_error_words.py` (27 checks); it cannot see a text built in a
  variable first, or the Free space refusals (returned as tuples): those were read by hand.
- U10: "This calibration is as good as the board allows." / "Good: no step needs redoing." /
  "… laser lines are calibrated well."; U13: "Fill holes up to 15 mm around (about Ø 5 mm)".

The pages matched on the old words in four places, mended with it: the Free space toast listed
"nothing to free" as a failure (`lib/keepMode.ts`), Setup's "Go to Connect / Go to Stereo" links
(`pages/setup/common.tsx`), a wrong margin shown as "no table" (`TableSection.tsx`), and the
mesh-changed note under Compare. Checked on the real engine: six refusals read through the API
(no scanner, a task that is gone, an unknown address, a project that is gone, a calibration
without a scanner, a scan without a scanner).

Left from this, small:

- A length typed with a comma ("100,2") in the print check, which U9 guessed was refused: the
  page already turns the comma into a point before it sends the number
  (`pages/setup/PrintStep.tsx`), so the user is not refused. The server still takes a number
  only, as before; nothing to change.
- The demo's copies of server texts (`api/mock/*.ts`) still have the old words in about twenty
  places ("Reading the reference", "Emitters off", "No Pico", lower-case refusals): they follow
  in the first wave, with the names.
- Two engine messages for the part's axes ("secondary_zero…", "Axes must be … got 'XX'") now
  show the generic "These shapes cannot set the part's axes." They need words of their own in
  the engine (`ohs/measure`), not in the server.
- "no pose: …" in a Scan check area's reason comes from the engine (`ohs/guide/views.py`).

**Done (2026-11-20), the first wave of the choices: the names and the words.** U2, U3, U17, U37
and the table "One name for each thing", in the pages, the demo, the server and the report
together; with it the halves that were waiting: U8, U31, U48 and the demo's copies of server
texts. What the status lines above call "left for the first wave" is done. Guards against the
old names coming back: `tests/test_server_error_words.py` (the server's and the report's texts)
and `ui/src/lib/words.test.ts` (the pages' texts).

How the picks were read, where the plan left room:

- **Scatter** is the 2σ the Scan check tests with ("Typical scatter", "Largest scatter"). A
  single point's value on the confidence map stays 1σ and is written "±", as the report's
  "Every ± is 1σ" says. No "σ" or "sigma" on a page; the report keeps "1σ" where it explains ±.
- **"Check recommended"** stays the state's name (the table); the button is "Check the
  calibration".
- **"Live preview"** (the table) wins over U8's "Live model".
- **"Strict 3-2-1"** stays: it names a measuring method and has its own help text.
- **"close every opening"** stays in the watertight label: it closes the outer edge too.
- The firmware flashing steps keep "Pico": they are about that board.

**Done (2026-11-20), the second wave: the structure of the pages.** U11, U12, U15, U25, U26,
U44, U45, U49, in the pages alone. Looked at on the real engine too: the viewer's groups, the
shortcut sheet, Settings, and the question on the Align page.

- **U11, the viewer's side panel:** the scan's size stays on top; below it four folding groups,
  only the first open: **Is it good?** (Scan check, processing report), **Measure**
  (measurements, fitted shapes, the part's axes once a plane is saved, sections, flush),
  **Compare with CAD**, **Export** (downloads, for CAD, report, Edit mesh, Make mesh again).
  Turning on a tool opens its group; a folded group says what it holds ("1 fitted shape").
- **U12, Settings:** Appearance, After a scan, While scanning, Meshing, Line lasers, About; then
  a closed "Advanced: what the settings file says" with the file's path and every section read
  only, in words a person reads. No command line at the top.
- **U15:** "Did you move or turn the part between the two scans?" has no default: Yes and No
  cards, each saying what the merge does with the table; Align (and a second pass's Start)
  waits for the answer, with an "Answer it" button. "Make an ordinary scan instead" stays, so
  nothing stops a scan.
- **U25:** "?" opens a sheet of the page's shortcuts on the Scan page, the viewer, Edit mesh and
  the two calibration screens; a test reads each page's key map and fails when a key is missing
  from its sheet or listed twice. On Edit mesh the hole outlines moved from H to O.
- **U26:** during the first run only the setup card has buttons; the three-steps card points
  to it.
- **U44:** "New scan" on Home opens the Scan page with the project picker focused and asked
  for; the first project is no longer taken silently.
- **U45:** Esc twice within a second stops a scan; one Esc shows "Press Esc again to stop".
- **U49:** one "Mesh detail" control (the preset's); "Build the surface" says it is optional
  and when it is needed; "Refine from the images" is marked a trial.

Left from the second wave, for the third (they need the server):

- "Open the settings folder" (U12): one route could serve it, U34's log folder and U39's
  project folder, for example `POST /api/open-folder` with `{what: "settings" | "logs" |
  "project", project_id?}`. Until then Settings has "Copy the settings folder's path".
- A second pass started from the scanner's own trigger does not go through the page, so the
  moved-part question (U15) cannot hold it. The engine should not start a pass whose answer is
  missing: the page would send the answer ahead, and the trigger would be refused in a plain
  sentence while there is none.
- Seen on the way, not mended: at 800 px the shading toolbar of the viewer and of Edit mesh
  is cut off on the right; the second pass panel's title is squeezed by its button at 1280;
  "Needs a GPU" in Settings and two tooltips (U5 says "graphics card"); "Planes you calibrated
  are kept" under Line lasers.

**Done (2026-11-21), the third wave: what needed the server too.** U5, U7 with U50, U14, U21,
U34, U39, the second wave's two leftovers, and one thing the engine brought: the camera path's
warning. Routes and fields are in `APP_API.md` ("The camera path's warning", "Rename a scan,
the trash", "Folders and the log"). Tried on the real engine: rename, move to the trash, the
trash's list, restore, the refusals; the project page and Home at 1024, 1280 and 1600 px.

- **The camera path's warning** (ENGINE_PLAN X0a): when tracking may have slid, the scan's row
  on the project page has an amber chip "Tracking may have slid" (the sentences on hover), the
  viewer's processing report opens with the warning, the "Follow the camera" stage on Edit mesh
  carries it, and the HTML report has a "Camera path" row. Older scans and scans whose path
  held show nothing.
- **U7, U50:** each scan row has a "More" menu with "Rename…" and "Move to trash…". A scan
  that is recording or busy is refused in a sentence; a scan that others were merged from or
  continue is named before it goes. Home has "Trash": deleted projects and scans, each with
  Restore (a taken name becomes "… (restored)", nothing is overwritten), and "Empty trash…",
  which asks a second time and deletes only the items the page listed. A new scan's default
  name takes the next free number.
- **U39, U12, U34:** "Open the project folder", "Open the settings folder", "Open the log
  folder" and "Save a log for a bug report…". The app now writes its log to a file
  (`logs/studio.log` in its data folder, 2 MB, three older ones kept). The page never sends a
  path: only the three folders the app owns open.
- **U5:** one line in Settings and on Home's setup card says what this computer can do. With a
  graphics card: "Every feature works." Without: no confidence map (so the Scan check cannot
  test the mesh), "Thin walls and edges" has no effect, tracking holds flat, plain parts less
  well, making the mesh takes longer. "GPU" is "graphics card" everywhere a user reads.
- **U14:** "Removed 3 loose bits, the largest 4.2 mm across (1,234 triangles)", and "Show what
  was removed" puts the mesh before the edit beside it as a ghost.
- **U21:** checked on the real server first: with the simulator's demo connected, the scan
  uses the demo's own true calibration, and Setup's own target has nothing yet; the chip said
  "Calibration loaded". It now says "Demo calibration" and why (this also answers V2 below).
- **U15's leftover:** nothing to change. The scanner's trigger always starts an ordinary scan,
  never a second pass.

Found in review and mended: on the project page the row's buttons ran over the details at
1280 px once "More" was always shown. "Make mesh again" is now in the row's More menu under
about 1500 px, and beside the others above it.

Left, small:

- "Show what was removed" (U14) is offered right after the edit, or after Undo then Redo;
  from any other step of the history it would need a jump of two steps.
- A trigger press while the Scan page is set up for a second pass starts an ordinary scan,
  without a word about the pass.
- One hint still shows a file's path when a project file cannot be read ("Restore … from a
  backup"): `projects._load`.
- A scan put back from the trash is listed last in its project, not in its old place.
- Scan check: "Seen too few times" is said as a title and again as the sentence's start.

### [ ] U51. The old names in texts the engine itself makes (found in the first wave, S)
The server and the pages are done; these sentences are made deeper in the engine and reach
the user through them, and the demo mirrors them (so `api/mock` is outside the pages' guard).
Words only. U4 and U47 are ticked with this.

- `ohs/calib/health.py`, `ohs/calib/drift.py`: "2-minute check", "launch check", "the IMU",
  "the calibration still holds"; and U4's reason for the scans' limit ("Scans measure this more
  finely than the board check, so their limit is lower") beside "want under 0.15 px".
- `ohs/app/presets.py`: U47's card ("Finer mesh detail (0.5 mm instead of 1 mm): thin walls come
  out closer to their real thickness. Takes about twice as long to make the mesh. No gain on
  large panels."); the numbers move to a "Why" line.
- `ohs/mesh/section.py`, `ohs/cad/*.py`: "the part's frame", "the scan's own frame".
- `ohs/inspect/reference.py`: "the reference", "mm RMS"; `ohs/align/pairwise.py` "mm RMS";
  `ohs/align/merge.py` "loose pieces"; `ohs/mesh/support.py` "floaters", "RMS";
  `ohs/mesh/check.py` "the mesh's own uncertainty"; `ohs/guide/views.py` "no pose: …";
  `ohs/inspect/gapflush.py` "the stereo depth"; `ohs/measure` "(RMS)" and the two messages for
  the part's axes named above; the self-test's details ("imu=…").
- The command line (`ohs/app/cli.py`, `capture.py`) says "still holds": a different audience;
  leave unless it is one edit.

## The ten that matter most

1. **U1:** an open calibration result silently blocks the first scan.
2. **U2, U3:** "±" and "Check" each mean several things, and Check's verdict reads like a
   promise of accuracy.
3. **U4:** the same camera number is judged against three limits under three names.
4. **U5:** without a graphics card the app loses its best features and says so only afterwards.
5. **U6:** "Delete for good?" opens with the delete button focused.
6. **U7:** a scan cannot be renamed or deleted, and the trash cannot be opened from the app.
7. **U8, U9:** jargon on the main screens, and server errors that show Python text, job ids and
   command lines.
8. **U10:** calibration results promise accuracy, against the roadmap's own rule.
9. **U11:** the viewer's side panel is about 17 sections in an order nobody would choose.
10. **U15, U13, U14:** silent defaults that can lose geometry: "the part stayed where it was",
    loose bits under 5 mm removed, holes "≤ 5 mm" filled (5 mm round, about Ø 1.6 mm).

## Findings

### [x] U1. An open calibration result blocks scanning, and the Scan page does not say why (defect, S to M)
- **Where:** Setup, Check, "Finish & check", then leave by the step rail, the sidebar or "Scan
  your first object"; Scan page, Start scan. `ui/src/pages/setup/actions.ts` (`startFirstScan`
  does not close the session), `ui/src/pages/ScanPage.tsx` (`reason` ignores the calibration),
  server `studio.py` (409).
- **Sees:** Start looks ready; then a toast "Couldn't start the scan / A calibration session is
  running". The sidebar still says a calibration is in progress after the check finished.
- **Mend:** leaving a result that cannot be saved closes it; the Scan page says "A calibration
  result is still open in Setup." with "Close it" (a check) or "Review it" (a result that can
  be saved); the sidebar follows the same state.

### [x] U2. "±", "σ", "sigma" and "tolerance" are mixed, and Check's verdict reads like accuracy (choice, M)
- **Sees:** "97 % of the surface meets ± 0.3 mm"; per area "The scan's own 2σ here is larger
  than ± 0.3 mm", "Median sigma ±0.17 mm"; the report says every ± is 1σ.
- **Pick:** verdict "97 % of the surface was measured finely enough for ± 0.3 mm; 13 areas need
  another pass."; per area "Measured too loosely for ± 0.3 mm here."; "Typical scatter /
  Largest scatter" shown as the same 2σ the test uses; ± only for tolerances and 1σ fit values,
  said once at the top; "What this does not know" directly under the verdict.

### [x] U3. "Check" names two unrelated things (choice, S)
- Setup step 7 and the chip "Check recommended" against the viewer's "Check".
- **Pick:** "Calibration check" in Setup (chip: "Check the calibration"); "Scan check" in the
  viewer (button: "Check the scan").

### [ ] U4. Three limits and three names for the left/right row mismatch (choice, S)
- "Rectified row error 0.11 px (want < 0.3 px)", "Row error after rectification 0.16 px (want
  < 0.6 px)", "disagreed by 0.34 px in height (want under 0.15 px)".
- **Pick:** one name, "Left/right height mismatch"; where the limits must differ say why
  ("Scans measure this more finely than the board check, so their limit is lower"). The limits
  themselves are provisional (FEATURE_PLAN D10).

### [x] U5. What the computer can do is invisible until something is missing (choice, M)
- "This mesh has no confidence data: it was made without a GPU ... Make it again to get it."
  (which does not help on the same PC); "GPU" and "graphics card" used in turns.
- **Pick:** one line in Settings, About and on the Home setup card: "This computer: graphics
  card found (name): every feature works" or what is not available without one; "graphics
  card" only; "make it again" only where it would help. (Since D7 and the status change of
  2026-11-20 the list of what is missing without one is shorter: check it first.)

### [x] U6. Danger confirms open with the destructive button focused (defect, S)
- `ui/src/components/ui/Dialog.tsx`: `data-autofocus` on the confirm button whatever the tone.
- **Mend:** with tone "danger" the focus starts on Cancel. Also keep the dialog open when its
  action failed.

### [x] U7. A scan cannot be renamed or deleted; the trash cannot be opened (choice, M to L)
- **Pick:** a menu on each scan's row with "Rename…" and "Move to trash…" (the folder goes to
  `.trash` as a project does); a "Trash" link on Home with "Restore" and "Empty trash…".
  Rename first: cheapest and most wanted.

### [x] U8. Jargon on screens used every time (defect against the rules, M as many small edits)
- **Scan:** the badge "0.90 · 0.55 mm" ("ICP fitness · inlier RMSE"), "Fitness / RMSE",
  "PIPELINE 15.4fps". Hide the numbers unless asked for in Settings; "Live model".
- **Setup:** "Lens and baseline calibration" → "Calibrates the two cameras"; "a baseline with
  the wrong sign" → "the calibration says left and right are swapped"; drop "rectified"; "laser
  planes" → "laser lines"; drop "fit residual"; "the board's pose is what the gyro is compared
  with" → "the app compares the motion sensor with it"; "Emitters" → "Lasers and dots"; "IMU" →
  "Motion sensor (IMU)".
- **Process:** "Checked 5 revisits with ICP and kept 2; the pose graph spread the correction" →
  "Found 5 places the scan came back to and used 2 to straighten it."; "camera poses" → "camera
  path"; "Smaller voxels keep finer detail" → "Finer mesh detail keeps smaller features."; chips
  "1 mm voxels" → "Mesh detail 1 mm"; "Floaters" → "Loose bits"; "Base mesh" → "Starting mesh";
  "too few features" → "too little shape to follow".
- **Viewer:** "Meshing: TSDF · 1.00 mm voxels" → "Mesh detail 1.0 mm"; "Matcap clay", "Normal
  shading" → "Plain grey", "Colour by direction"; "Plane · RMS 0.04 mm" → "Plane · flat to
  0.04 mm (typical)"; "Bounding box along the scan's axes (the first camera frame)" → "Size
  along the scan's own directions."
- **Align:** "0.25 mm RMS" → "typically 0.25 mm apart"; "ACCEPTED" → "Aligned"; "fused again" →
  "rebuilt together".

### [x] U9. Server error texts leak exceptions, ids, routes and command lines (defect, M)
- About 54 errors put `str(e)` into what the user reads, about 30 job and stage messages too;
  about 30 hints name API routes; about 10 name command lines; about 35 show internal ids.
- **Worst, with the rewrite:**
  - `app.py`, the catch-all "internal error: TypeName: ..." with "see the log (ohs -v app)" →
    "Something went wrong inside the app." / "Your recordings are safe. Try again; if it
    happens again, save the log (Settings, About) and report it."
  - `jobs.py`: a job's error is `str(e)` with the hint in parentheses → keep the detail and
    the hint apart; a fixed sentence for each kind of job for other exceptions.
  - `passes.py`: "...this scan is busy (processing) (wait for job j-3fa9c01b2e ...)" → use the
    error's own detail and hint.
  - `processing.py` stage cards "Fusing failed: {e}" → "Building the surface failed. Try again,
    or Make mesh at Standard quality. Your recording is safe."
  - `session.py`: "(ohs calibrate ...) and that Open3D is installed (pip install -e .[fusion])"
    → "The live preview could not start. Check the calibration in Setup. You can still record
    and make the mesh afterwards."
  - `studio.py`: "no calibration is loaded / (ohs calibrate stereo, ...)" → "The scanner is not
    calibrated yet. / Calibrate it in Setup, or try the app with the simulator."
  - `devices.py`: "run `ohs devices` and set camera.index" → "The camera could not be opened. /
    Check the USB cable and close other programs that use a camera, then press Connect again.";
    "No Pico ({e})" → "The scanner's control board (Pico) was not found. You can still scan,
    but the lasers, the motion sensor and the trigger will not work."
  - `projects.py`: "(POST .../process)" → "Press Make mesh first."; `processing.py`: "wait for
    job {id}" → "Wait for it to finish, or press Cancel."
  - `setup.py`: a measured length shown with Python quotes; a comma decimal ("100,2") is
    probably refused: accept it.
  - `keep.py`: "a scan keeps full, engineering or standard" matches no label in the app.
- Also: lowercase fragments and whole sentences are mixed; capitalise in one place.

### [x] U10. Accuracy promised from a calibration grade (defect, S)
- Demo: "Depth will be accurate to about 0.1 mm at 250 mm." Server (`calib_results.py`): "Depth
  from this calibration will be accurate.", "Laser points will be accurate across the scanning
  range."; Process: "...are more accurate."
- **Mend:** "This calibration is as good as the board allows." / "Good: no step needs redoing.";
  "Both laser lines are calibrated well."; "...The lines are measured on their own, so they
  correct the surface." The demo says what the server says (U31).

### [x] U11. The viewer's side panel: order and grouping (choice, M)
- Today: Size; Measurements; Fitted shapes; Coordinate system (an empty form); Sections; Kept
  sections; Flush; Kept seams; Check; Compare; References; Fine-tune; Download; For CAD; Report;
  Remake; Processing report.
- **Pick:** four groups, each folding, the first open: (1) "Is it good?": Scan check, the
  processing report with the calibration status; (2) "Measure": Measurements, Fitted shapes,
  Sections, Flush, each with its kept list inside; the coordinate system only after a first
  plane is kept, as "Set the part's axes…"; (3) "Compare with CAD"; (4) "Export": Download, For
  CAD, Report, Fine-tune, Remake.

### [x] U12. The Settings page is a dump of the settings file (choice, L; smallest step S)
- "Edit ~/.ohs/config.toml (ohs config --init ...), then restart the engine."; rows such as
  "FourCC MJPG", "Baud 115200", "Poisson Off", "Dictionary DICT_5X5_100"; two folders; "(M3)".
- **Pick:** the four real settings on top (Theme, After a scan, Thin walls, Line lasers); the
  rest under a closed "Advanced: what the settings file says" with "Open the settings folder";
  explain or drop "Scans folder"; drop "(M3)".

### [x] U13. What a hole's size means; the demo's recipes differ from the engine's (defect, S)
- Chip "Holes ≤ 5 mm" and history "Fill holes up to 10 mm" mean the length round the hole, so
  15 mm is about Ø 4.8 mm. Car parts are mounted by holes.
- **Mend:** "Holes up to 15 mm around (Ø 5)"; the history the same. The demo's built-in recipes
  (10 / 10 / 40 mm, "Largest piece only") follow `BUILTIN_RECIPES` (5 / 5 / 15, no "largest
  only").

### [x] U14. Removing loose bits can take a small or thin part without saying what it took (choice, S to M)
- The Default recipe removes pieces under 5 mm (and under 1 % of the triangles); the history
  gives a triangle count only.
- **Pick:** "Removed 3 loose bits, the largest 4.2 mm across", and "Show what was removed".
  Measure on the real engine whether a 0.5 mm tab cut off by noise is caught (V5).

### [x] U15. The moved-part question has a silent default (choice, S)
- Align preselects "The part stayed where it was"; the pass leaves "The part was moved"
  unticked. Turning the part over is the usual reason for a second scan.
- **Pick:** no default: "Did you move or turn the part between the two scans? Yes / No" must be
  answered before aligning, and before a pass starts.

### [x] U16. The project table at narrow widths loses the scan names (defect, S)
- At 800 px the name column is 43 px; at 1024 px the details wrap into a tall cell
  (`ProjectPage.tsx`, the actions column's `minmax(370px,400px)`).
- **Mend:** under about 1100 px the secondary actions go into a menu; under about 860 px the
  details go under the name.

### [x] U17. One action has six names (choice, S)
- "Process", "Remake", "Process…", "Fine-tune in Process", "Step by step…", "Rebuild the mesh",
  "Re-run"; the server says "process the scan", "run Track".
- **Pick:** two verbs: "Make mesh" (and "Make mesh again") and "Edit mesh…" for the Process
  page; the stages inside it "Follow the camera / Straighten / Build the surface".

### [x] U18. Self-test: the laser warning comes too late, and texts contradict (defect, S)
- "The Pico, the emitters and the trigger are skipped" then "Checking the simulated emitters…";
  the Class 3R warning only while the test runs; "Run it again after any fix" after a pass.
- **Mend:** the warning before "Run self-test" on the real scanner; "Skipping the lasers
  (simulator)"; "Every check passed".

### [x] U19. cm and mm for the same distances (defect, S)
- "20–50 cm" against "200–500 mm"; "about 25 cm" against the gauge's "200–350 mm".
- **Mend:** mm everywhere.

### [x] U20. "Views" or "poses"; deleting a view needs a mouse (defect, S)
- **Mend:** "views" everywhere; each thumbnail gets a visible × button labelled "Delete view
  7", reachable by keyboard.

### [x] U21. The chip says "Calibrated" before any calibration (defect, or check first, S)
- With the simulator the chip reads "Calibrated" while the rail says "Not calibrated yet", and
  the Device page names another calibration file than the wizard saved.
- **Mend:** "Demo calibration" when the demo's own is in use; check which file a simulator
  scan uses (V2).

### [x] U22. Check's area cards are long and repeat themselves (defect, S to M)
- Each card repeats "Uncertain / The scan's own 2σ here is larger than ± 0.3 mm."; "even the
  squarest was 64° off square on"; "about 254 mm"; cm² beside mm².
- **Mend:** say the shared sentence once above the list; "Seen only at a slant (the best look
  was 64° from straight on). Hold the scanner straighter to it."; "about 250 mm"; one area
  unit; one button, "Scan these areas again…", the places shown on the Scan page.

### [x] U23. The places list mixes two sentence forms and two numberings (defect, S)
- Places 1 to 3 in one form (`lib/checkWhy.ts`), 4 to 6 in another (`lib/pass.ts`); places
  numbered 1 to 6 for areas 1, 2, 3, 5, 6, 7.
- **Mend:** one form; places lettered A, B, C.

### [x] U24. A button called "Not a pass" (defect, S)
- **Mend:** "Make an ordinary scan instead".

### [x] U25. Shortcuts (defect and choice, S)
- The viewer says "1–6 shading" for 7 modes. H, P, C mean other things on the Process page.
- **Mend:** "1–7"; **pick:** a "?" sheet for each page, and "O" for openings on Process.

### [x] U26. Two first-run cards compete (choice, S to M)
- The setup card above "From part to mesh in three steps", whose steps leave out calibration
  and name only the Pico.
- **Pick:** while it is the first run, step 1 reads "Set up the scanner (about 16 minutes,
  once)" and the lower card has no buttons; "the control board (Pico or ESP32)"; "Try the
  simulator" as the one name.

### [x] U27. "6/7" beside "All set" (defect, S)
- The optional Print step counts in the total. **Mend:** count the required steps.

### [x] U28. A toast covers the wizard's Next button (defect, S)
- **Mend:** toasts at the top right or above the footer; "The simulator is connected." (not
  "Start a scan!").

### [x] U29. Connect: "Selected" twice, and real cameras listed while the simulator is on (defect, S)

### [x] U30. After Stop, and small wording (defect, S)
- The distance gauge keeps saying "Move closer" when nothing records; the lost-tracking banner
  is cut off at narrow widths; "keep scanned area in view" → "keep the scanned area in view";
  after Stop: "Making the mesh. You can turn the model while you wait, or leave this page: the
  job keeps running (see Jobs)."

### [x] U31. The demo says things the real engine does not (defect, S)
- The calibration verdicts (U10) and the recipes (U13). **Mend:** the demo uses the server's
  words, or a test compares them.

### [x] U32. Number formatting (defect, S)
- "29.7fps", "7,754tris", "453mm"; "1280×800" against "2560 × 800"; "1289 mm" against "1,568";
  a hyphen for minus beside a true minus. **Mend:** one formatter with a narrow space before
  the unit.

### [x] U33. Small grey text is under the contrast it needs (defect, S)
- `text-subtle` on the panel is 3.85 to 1 at 10 to 11 px (help lines, hints, the gauge's
  scale); 4.5 to 1 is the usual floor. **Mend:** lighten it to about 5 to 1.

### [x] U34. No log inside the app (choice, M)
- Every hint says to run `ohs -v app`. **Pick:** Settings, About: "Open the log folder" and
  "Save a log for a bug report…".

### [x] U35. "Align two scans" for a page that takes two to eight (defect, S)
- **Mend:** "Align and merge scans" everywhere.

### [x] U36. The Align verdict's words (defect, S)
- "ACCEPTED", "0.25 mm RMS where the two overlap", "8 of 9 agree each one starts from a
  different rough fit" (label and help run together). **Mend:** "Aligned"; "Typically 0.25 mm
  apart where they overlap"; "8 of 9 starting guesses agree (each starts from a different
  rough fit)".

### [x] U37. Free space: the names (choice, S)
- The three titles, a row label, and "full / engineering / standard" from the server;
  "Standard" is also a quality. **Pick:** the three titles as the only names; the middle one
  "Keep half the frames and the result"; drop "events"; "You can no longer re-run single
  steps on the Process page until you make the mesh again." Keep the two-step confirm.

### [x] U38. Why a scan failed is hidden in a hover (defect, S)
- **Mend:** the first line under the status: "Failed: too few frames tracked. Make mesh again,
  or at Preview quality."

### [x] U39. Developer text on the project page (defect, S)
- "Raw recordings stay in scans/<id>/recording…" → a button "Open the project folder"; the
  size as a visible link "1.15 GB · Free space…" instead of a hover.

### [x] U40. Two Start buttons on the IMU step, and a garbled sentence (defect, S)
- **Mend:** one Start; "The motion sensor keeps tracking steady on fast sweeps and plain
  surfaces."

### [x] U41. Verdicts said twice (defect, S)
- The badge "Excellent" and the headline "Excellent stereo calibration"; "Nothing to do: scan
  away." twice.

### [x] U42. Downloads differ from page to page (defect, S)
- **Mend:** one list everywhere, OBJ included.

### [x] U43. "Check answer at ± mm" (defect, S)
- **Mend:** "Tolerance for the report's Scan check, ± mm".

### [x] U44. "New scan" on Home silently picks the first project (choice, S)
- **Pick:** open the Scan page with the project picker focused: "Choose the project for this
  scan".

### [x] U45. Esc stops a scan (choice, S)
- Esc is also "dismiss". **Pick:** Esc twice within a second, with "Press Esc again to stop".

### [x] U46. "Calibration loaded" right under "Recalibration required" (defect, S)
- **Mend:** "Calibration loaded (recalibration required for the cameras)", in amber.

### [ ] U47. Presets: General shows nothing; the help card is a wall of numbers (choice, S)
- **Pick:** show General's values too, greyed; help: "Finer mesh detail (0.5 mm instead of
  1 mm): thin walls come out closer to their real thickness. Takes about twice as long to make
  the mesh. No gain on large panels."

### [x] U48. The calibration status has five wordings (defect, S)
- "Valid", "Calibrated", "Calibration valid", "still holds", "Calibration loaded" (a different
  fact). **Mend:** "Calibration valid" for the state; "Calibration loaded" only for "a file is
  in use".

### [x] U49. The Process page's stage list (choice, M)
- "Track ... fuses the depth into a mesh" then "Fuse: Not run"; qualities in the order
  Standard, Best, Preview; mesh detail in three places on one page.
- **Pick:** Preview, Standard, Best; one "Mesh detail" control in the Preset box, which Fuse
  uses; trial stages marked "Trial" in their title.

### [x] U50. Scans are called "Scan 1, Scan 2…" and can never be renamed (choice, S; with U7)

## One name for each thing

| Concept | Names in the app today | Proposed |
|---|---|---|
| Making the mesh | Make mesh, Remake, Rebuild the mesh, Process, Fine-tune in Process, Step by step, Re-run | **Make mesh** / **Make mesh again**; the page is **Edit mesh** |
| The surface | mesh, model, result, tracked mesh, base mesh | **mesh**; **live preview** while scanning; **starting mesh** in the history |
| Mesh detail | Mesh detail, voxel size, voxels, "TSDF · 1.00 mm voxels" | **Mesh detail** |
| Quality | Preview / Standard / Best, and "the frames Standard quality uses" | for processing only; Free space says "half the frames" |
| The scan's scatter | Confidence, sigma, σ, 2σ, uncertain, uncertainty, ± | **confidence** for the map; **scatter** for the number; never "sigma" on screen |
| Tolerance | Tolerance your part needs, Check tolerance, "Check answer at ±" | **Tolerance** |
| The scan's quality check | Check | **Scan check** |
| The calibration's check | Check, 2-minute check, launch check | **Calibration check**; at start-up the **start-up check** |
| The calibration's state | Valid, Calibrated, Calibration valid, still holds, Calibration loaded | **Calibration valid / Check recommended / Recalibration required** |
| The calibration steps | Stereo, Lens and baseline, the cameras; laser planes, line lasers; IMU, gyro, motion sensor | **Cameras**, **Lasers**, **Motion sensor (IMU)** |
| Calibration captures | views, poses, captures | **views** |
| The hardware | scanner, device, camera, Pico, controller, emitters, dot projector | **scanner**, **cameras**, **control board (Pico or ESP32)**, **lasers and dots** |
| Several scans | pass, second pass, Scan these areas, Scan from these places | **second pass**; button **Scan these areas again** |
| Combining scans | Align two scans, Align scans, merge, fused again | **Align and merge** |
| Coordinates | Coordinate system, part's frame, scan's frame, first camera frame, Strict 3-2-1 | **part's axes** / **scan's axes** |
| Fits | Fit, fitted shape, kept fit, feature, measurement | **Fitted shape** (kept = saved); **Measurement** for two points |
| Loose bits | Floaters, loose bits, pieces | **Loose bits** |
| Holes | "Holes ≤ 5 mm", "mm around", openings | **holes, X mm around (about Ø Y)** |
| The reference | reference, reference model, CAD | **CAD model** |
| The simulator | simulator, simulator demo, sandbox, demo | **simulator** |
| Keep modes | full / engineering / standard, three titles, a row label | the three Free space titles only |

## What is good and must be kept

- The setup wizard: each step says why it matters and how long it takes; live guidance ("Move
  the board to the top-right corner"); automatic capture; the simulator's truth beside the
  result; "Drop and recalibrate" for one bad view; the saved file and its backup shown.
- "The app warns, it does not block": the health callout with one "Redo the cameras" button,
  "The limits are provisional…", no scan ever blocked by a status.
- The calibration status never uses colour alone; the distance gauge has words with its colours.
- Free space: three clear choices, a size for each scan, "Afterwards you can no longer…", and a
  second explicit question.
- Deleted projects go to a trash; nothing is deleted for good without being asked.
- Check's reasons and "where to hold the scanner", honest about their limits.
- The report's "What the numbers mean" and "Every ± is 1σ…, not the accuracy".
- Align: an honest verdict, picked points as a way out, "The two scans stay as they are"; the
  pass panel's "A merge adds coverage…".
- After Stop: a mesh by itself with progress, and the rest of the app keeps working.
- Dialogs trap focus and give it back; hotkeys are off while typing; status chips are announced.
- Preset rows: "From the preset / Changed by hand from …" with a way back.

## To check on the real engine

- [ ] **V1.** U1 on the real server: the exact toast, also with an unsaved stereo result.
- [ ] **V2.** Which calibration file a simulator scan uses, against the chip and the Device page.
- [ ] **V3.** What this PC reports about its graphics card, and every message met with
  `OHS_COMPUTE=cpu` (Check, Confidence, the health note, thin walls) since D7.
- [ ] **V4.** The preset reaches the mesh and the viewer's report says 0.5 mm (checked once in
  the app on 2026-11-20: it did; the demo showed "1.00 mm voxels").
- [ ] **V5.** The default recipe on a scene with a small detached part or a thin tab: what goes,
  and what the history says.
- [ ] **V6.** "Body panel" (15 mm around) on a scan with Ø 6 to 12 mm holes: which close.
- [ ] **V7.** Real failure texts: a second Make mesh on a busy scan, a replay folder gone
  mid-scan, a scan with no calibration, a pass while its first scan is busy.
- [ ] **V8.** The HTML report as a document, printed to PDF: order, length, repeated ±
  explanations, page breaks.
- [ ] **V9.** A long job: does the percentage move evenly, does "a few seconds left" hold, can
  the user leave and come back, does Cancel leave the scan usable.
- [ ] **V10.** Keyboard only: deleting a calibration view, the Free space dialog, the Check list.
- [ ] **V11.** The Connect step's message with no camera, and with the camera held by another
  program.
- [ ] **V12.** Align and merge with a part turned over, with each answer to the moved-part
  question: the result, and whether the merge says what it left out.
