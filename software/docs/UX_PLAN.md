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

Mended 2026-11-21 (the small leftovers):

- A trigger press while the Scan page is set up for a second pass still starts an ordinary
  scan (the trigger never starts a pass), and the page now says so while it records: "An
  ordinary scan was started, not the second pass of “…”: the pass is still waiting, and Start
  on this page begins it once this scan is stopped." (`lib/pass.ts` `passWaitingNote`).
- No hint shows a file's path any more: the project file's (`projects._load`), and three more
  the guard found once it was taught to see a path in a text (a recipe's file, the list of CAD
  models, a printable). The paths go to the log. Two are shown on purpose and named in the
  guard: the saved calibration file, and the Replay folder the user typed.
- A scan put back from the trash takes its old place, by its own creation time. (Two scans
  made in the same second cannot be told apart: the time is stored to the second.)
- Scan check: a reason's title is said once. Three sentences now start with their title
  ("Seen too few times: …", "Seen from too far: …", "On an edge or a tight curve, …"); a test
  holds every reason to it.
- The second pass panel's "Make an ordinary scan instead" is under the title, which has the
  whole width; the four recipe buttons are named "Apply “Default”" and so on; the Replay
  field is named "Recording folder" on the field itself; the Simulator chip on the motion
  sensor screen is no longer focusable (its whole text is read by a screen reader; the
  tooltip's file path is reachable by mouse only); the Device page's "Control board not
  found" names Setup's Connect step and has a button to it, not `firmware/README.md`.
- The shading toolbar of the viewer and of Edit mesh wraps inside the 3D view at 800 px (it
  was cut off under the side panel).

Still left: Setup's Connect step itself says "The full guide is in `firmware/README.md`"
inside the flashing steps.

### [x] U51. The old names in texts the engine itself makes (found in the first wave, S)
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

**Done (2026-11-21).** Words only: `tools/snapshot.py` before and after reads identical, so a
scan's result is bit for bit what it was. With it every finding of this plan, U1 to U51, is
ticked; what is left are the small items in the "Left" lists above and the checks on the real
engine at the end.

- Every sentence in the list above, and more that reach the user through the calibration's
  results: the camera, laser and motion sensor calibration's errors and the calibration
  check's verdicts (`ohs/calib/stereo.py`, `imu.py`, `laser_planes.py`, `ohs/app/calib_flow.py`).
- U4: one name, "left/right height mismatch", in the scans' status, the start-up check and
  the calibration check; each limit that differs says why ("scans measure this more finely
  than the calibration check, so their limit is lower"; "the start-up check looks at three
  pictures only, so its limit is wider").
- U47: the thin-parts preset's card reads What it changes, What it costs, Why, with the
  numbers on the last line.
- The two refusals for the part's axes now reach the user in the engine's own words ("Strict
  3-2-1 needs a plane, …"; "The axes must be two different letters of X, Y and Z, …").
- The demo's calibration status is built from the engine's own sentences, and a test reads
  `ohs/calib/health.py` and fails when the demo says something the engine does not
  (`api/mock/health.test.ts`).
- Guard: `tests/test_server_error_words.py` now also reads 25 engine modules that make
  user-read sentences, with seven named exemptions (a parameter's own name, STEP syntax, and
  the like).
- Found in review and mended: the viewer said "Calibration: Calibration valid"; one sentence
  under Compare still said "uncertainty (2σ)".
- Left on purpose: texts for the command line and for developers (settings descriptions, the
  bench, the Pico driver), and the rewording of "GPU" in an older scan's stored warning (its
  report file still says it).

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

### [x] U4. Three limits and three names for the left/right row mismatch (choice, S)
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

### [x] U47. Presets: General shows nothing; the help card is a wall of numbers (choice, S)
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

- [x] **V1.** U1 on the real server: the exact toast, also with an unsaved stereo result.
- [x] **V2.** Which calibration file a simulator scan uses, against the chip and the Device page.
  Checked 2026-11-21 (U21): with the simulator's demo connected the scan uses the demo
  recording's own true calibration; Setup's own target for the simulator is a separate file
  with nothing in it yet. The chip and the Device page now say "Demo calibration".
- [x] **V3.** What this PC reports about its graphics card, and every message met with
  `OHS_COMPUTE=cpu` (Check, Confidence, the health note, thin walls) since D7.
- [x] **V4.** The preset reaches the mesh and the viewer's report says 0.5 mm (checked once in
  the app on 2026-11-20: it did; the demo showed "1.00 mm voxels").
- [x] **V5.** The default recipe on a scene with a small detached part or a thin tab: what goes,
  and what the history says.
- [x] **V6.** "Body panel" (15 mm around) on a scan with Ø 6 to 12 mm holes: which close.
- [x] **V7.** Real failure texts: a second Make mesh on a busy scan, a replay folder gone
  mid-scan, a scan with no calibration, a pass while its first scan is busy.
- [x] **V8.** The HTML report as a document, printed to PDF: order, length, repeated ±
  explanations, page breaks.
- [x] **V9.** A long job: does the percentage move evenly, does "a few seconds left" hold, can
  the user leave and come back, does Cancel leave the scan usable.
- [x] **V10.** Keyboard only: deleting a calibration view, the Free space dialog, the Check list.
- [x] **V11.** The Connect step's message with no camera, and with the camera held by another
  program.
- [x] **V12.** Align and merge with a part turned over, with each answer to the moved-part
  question: the result, and whether the merge says what it left out.

**Walked on 2026-11-21** on a clean copy of the app with its own folders (a simulator scan of
630 frames, one of 1,321 frames with the thin-parts preset, one made without a graphics card,
and a copy of the two-sided bracket project). V1 and V3 to V12 are ticked as walked; what they
found is the list below. Not possible here: a camera held by another program (no camera on this
PC), the report's page breaks on paper (no print view in the tool), and a part really turned
over (the two bracket scans align either way).

What held, and stays as it is: leaving a calibration check closes it; Esc once only says
"Press Esc again to stop"; the trash from end to end (the scans that depend on one are named,
restore, the second question before emptying); the busy refusals; the list of what a merge left
out when the part was moved; the moved-part question without a default; the Scan check's "What
this does not know" and its reasons; the line about this computer; "297 loose bits removed, the
largest 40.8 mm across"; the log saved for a bug report; V4 (the preset reaches the mesh:
"Mesh detail 0.5 mm") and V6 ("Body panel" closed every hole under 15 mm around and left the
Ø 6 to 12 mm ones open, as "about Ø 5 mm" says).

## Found on the real engine (2026-11-21)

Defects first, by harm; each with its mend. "Pages" and "server" say where the mend is.

**Mended in the pages (2026-11-21):** R1, R3, R4, R10, R11, R13, R15, R16 are done and ticked;
the pages' half of R9, R12, R19, R20 and R22 is done and they are ticked with the server's
half. Seen on the real engine: the Free space dialog takes the focus on its first choice that
can apply, and a merged scan's row says "What the merge left out".

- **R3, how the keys behave now** (`lib/hotkeys.ts`): in a text field no shortcut fires. On a
  button, switch, link or menu item reached with Tab only Esc and "?" fire, so Enter and Space
  do what the control does. After a click on a button its own keys (Enter, Space, the arrows)
  stay the control's, and letters still work as shortcuts (W after clicking Wireframe). With
  nothing focused every shortcut fires. Walked by keyboard on the camera calibration, the Scan
  page, the viewer and the motion sensor screen.
- **R4:** the cause was the dialog focusing a button that was still disabled; every dialog
  goes through the same code, and a menu item now hands the focus back to its menu's button.
- **R1:** a scan's other notes have a title of their own: "What the merge left out" on a
  merged scan, "Notes on making the mesh" otherwise.
- **R15:** during "Make mesh again" the viewer keeps the mesh, its downloads and its report,
  and shows the job with Cancel; this is true to what happens once R2 is in the server.
- The pages' guard missed the old words because most were not on its list, and because it
  skipped lines of text with an apostrophe or a semicolon and text after an expression; it
  reads those now. It also read a trailing comment as text on a checkout with CRLF line
  ends; mended.
- Noticed, not mended: after "Delete view N" and after Pause the focus falls to the page (the
  button is gone), so a keyboard user loses the place; the Device page shows the path
  `firmware/README.md` in a hint; "What the merge left out" also stands over a note that says
  a raised area is kept.

**Mended in the server (2026-11-21):** R2, R5, R6, R7, R8, R9, R12, R14, R19 and R22 are done
and ticked. The server's half of R17, R18, R20, R21 and R23 is done; they are ticked when the
pages read the new fields.

- **R2, how a Make mesh again works now:** it builds in a folder of its own beside the scan's
  result (`.remake-<job>` in the scan's folder) and takes the result's place only when it has
  finished, by three renames. Until then nothing of the scan is touched: the mesh, the stages,
  the edits and their history, the report, the laser points. A cancel or a failure removes the
  half-made folder and puts the scan's status back. An app closed mid-run is put right at the
  next start: an unfinished run is removed, a swap stopped half way is undone, a swap that had
  moved everything in is finished. A folder that still holds a part of the earlier result is
  never removed. While a run builds, the scan holds two results on disk; Free space counts the
  second. Tried on the real engine: cancelled at 42 %, every one of the scan's 13 files was
  byte for byte what it was, its entry too, and no folder was left behind. A single stage run
  alone (Follow the camera, Straighten…) still works on the scan itself, as before.
- **R5:** Straighten and Build the surface fuse at known camera positions, so their own report
  did not say which path made the scan; they now carry the path of the stage that tracked it.
  A scan made without a graphics card at full size with that path's own settings has no note,
  rightly: its lasers and motion sensor are judged too.
- **R6, R8:** the report describes the calibration kept inside the scan's own recording, with
  a "Made with" row (the simulator's own, the replay's own, the scanner's as it was). A merged
  scan's report says what it was merged from and what the merge left out.
- **R7:** a merge always says what its cleaning took ("No loose bit was left out as too
  small…" or "12 small loose bits (3,400 mm²) were left out…").
- **R23, the server's half:** the line names the rule ("the largest 40.8 mm across but under
  1 % of the mesh"), and the mesh before and after any edit of the history can be fetched, so
  what an edit removed can be shown at any later time.
- **R18, the server's half:** the stages' shares of the percentage were measured on three
  scans (Follow the camera 42 to 57 % of the time, Straighten 29 to 42 %, the rest 11 to 19 %)
  and set to 47 / 37 / 3 / 13 (they were 72 / 12); the job carries the time left for the whole.
- **R21, the server's half:** a rename to a name another scan of the project has is refused
  with a suggestion ("Bracket (2)"); a merge's default name takes the next free one.
- The server's guard now also refuses program words in progress lines (a device's code name,
  "trajectory", "fusing", "DN") and frame counts without grouping.

**The pages read the server's new fields (2026-11-21):** R17, R18, R20, R21 and R23 are done and
ticked; with them every finding of the walk, R1 to R23. Seen on the real engine during a real
"Make mesh again" of a 630-frame scan and its cancel.

- **During a Make mesh again** the Edit mesh page shows "The new mesh being made" above the
  stages: the run's parts with one of them Running, the job's progress and Cancel, and "About
  4 min left for the whole mesh (now: Follow the camera)". The stages below keep the current
  mesh's states. After a cancel: "Making the mesh: cancelled. Stopped before it finished. The
  mesh stays as it was, and so does the recording." The same words in the toasts, the Jobs
  menu, the project row and the Scan page.
- **R17:** the viewer says "Making the mesh took …" for the whole run.
- **R23:** every Remove loose bits line of the history has "Show what was removed", at any
  time; the two meshes are loaded when asked and freed when closed (over 6,000,000 extra
  triangles the page says they are too big to show together).
- **R21:** a refused rename shows the server's sentence with a button "Use “Bracket (2)”"; on
  Align and merge a typed name another scan has gets the same warning, and the server refuses
  it too (found by the page pass: it took a typed name as given).
- **R20:** the line about this computer says why no graphics card is in use: turned off in
  the app's settings, turned off for this run by a setting made outside the app, or none found.
- **R14:** an engine's notice is its own title; no "Engine error" above it.
- The keyboard focus stays on a sensible control after "Delete view N" and after Pause.
- Found in review on the real engine and mended: the job's card lay over Edit mesh's toolbar
  while the new block already showed the same job; and a scan stayed "Making the mesh" on
  screen after a cancel of a job this window had not started.

Mended 2026-11-21: the page's own "Remove the table" text says mm² as the server does
(`lib/format.ts` `fmtMm2`); at 800 px a row that is making its mesh puts the progress under the
name, which keeps its width. Left: an edit made before 2026-11-21 keeps its stored words
("Remove floaters", cm²).

### [x] R1. Every warning of a scan is titled "Tracking may have slid" (pages)
A merged scan's notes about its table ("70 raised areas up to 0.59 mm high…") appear under that
title on the row's chip and in the viewer. Only the camera path's own warning gets that title;
the merge's notes get their own ("What the merge left out").

### [x] R2. Cancel on "Make mesh again" replaces a finished mesh with a half-made one, without a word (server)
Cancelled at 75 %: the scan went from 804,197 cleaned triangles with 193,001 laser points,
Straighten and Refine with lasers done, to 807,184 triangles in 221 pieces with 82,089 laser
points and those stages "Not run". The toast says only "Meshing cancelled / The raw recording
is kept". The finished mesh, its stages and its edits must stay until the new run has
finished; a cancelled or failed run leaves the scan exactly as it was.

### [x] R3. On the calibration screens the keyboard's Space, Enter and Backspace act even on a focused button (pages)
Enter on "Delete view 5" finishes the calibration instead; Space on the Auto-capture switch
takes a view. The page's shortcuts must not fire when a button, switch, link, menu item or
field has the focus (`useHotkeys`).

### [x] R4. The Free space dialog does not take the focus (pages)
Opened by keyboard, Tab walks the page behind it. Focus goes into the dialog when it opens (the
first choice that can apply, else Cancel) and stays inside.

### [x] R5. A scan made with the graphics card says it was "processed without a graphics card" (server, engine)
Once Straighten or Refine with lasers has written the scan's final report, the calibration
status carries the no-graphics-card note ("…so only the cameras could be judged"), in the
viewer and the report. And the scan really made without one has no such note. Each stage's
reading must carry which path made it.

### [x] R6. The report's Calibration section describes the wrong file (server, report)
For a simulator scan it shows Setup's own target for the simulator ("Laser lines Not
calibrated", and a top warning "No calibration for the vertical, horizontal laser lines this
build has: calibrate the lasers again") although the scan used the demo's full calibration and
has 193,001 laser points; without a graphics card it says "Cameras Not calibrated" beside
"Calibration valid". The section describes the calibration the scan was made with.

### [x] R7. A merge with "No" (the part was not moved) removes triangles without saying so (engine, server)
1,110,025 triangles before cleaning, 1,108,916 after, and no line about it. The merge always
says what its cleaning took, as it does for "Yes".

### [x] R8. A merged scan's report leaves out the merge (server, report)
None of the merge's notes, not which scans it was made from, and its calibration status says
"Not assessed … (it was processed before the app did that)", which is untrue for a merge made
today. The report gets "Merged from …", the merge's notes, and a sentence of its own for the
status.

### [x] R9. An unsaved calibration result: the Scan page says "No calibration yet" (pages, server)
U1's sentence ("A calibration result is still open in Setup.", Review it / Close it) is hidden
because the page tests "no calibration" first; with the simulator an open session makes that
true. The server's refusal says "A calibration is running." while it is waiting to be saved.
Test the open session first; the server says "A calibration result is waiting in Setup: save
it or close it first."

### [x] R10. Scan check: "Show where to hold the scanner" is cut off at 1280 px (pages)

### [x] R11. Free space dialog: doubled full stops, a choice preselected that cannot apply, and a footer that contradicts the choice's own line (pages)

### [x] R12. Program words on the screens (server, pages)
"on the GPU (gfx1035)", "on the CPU", "graphics card found (gfx1035)"; "Straightening the
trajectory", "left for fusing", "The rest keeps the fused surface"; "Smooth (bilateral, 50 %,
edges kept, away from edges)"; "Refining: fitting the correction field"; "The live pipeline is
slow on this computer"; the self-test's "Mean 28 DN", "|accel| 9.79 m/s^2", "2560x800 (2 x
1280x800)"; the Replay field's "e.g. one made by ohs record or ohs sim" and "meta.json and
frames.csv"; "DICT_5X5_100" on Setup's board line; "Reprojection error", "Baseline vs truth",
"Principal point vs truth" in the cameras' result; "The mesh model without the thin-wall band";
"Planes you calibrated are kept."; "Meshing" and "Meshing cancelled" beside "Making the mesh";
"base" in the recipe dialog; in the report "Last board check", "baseline", "Built from a given
camera path (straightened, or rebuilt from the tracked path)", "Missing: warning no
calibration…". Each in the table's names and plain words; the device's code name goes to
Settings' About only. Extend the two guards (`words.test.ts`, `test_server_error_words.py`)
with what they missed.

### [x] R13. Connecting a camera shows the simulator's text (pages or server)
"This happens once; next time the simulator starts right away." while a camera connects (the
Device page: "…the demo starts right away").

### [x] R14. A recording that vanishes mid-scan: "Engine error", and advice about a USB cable (server)
"The recording could not be read any more (its folder was moved or deleted). The scan was
stopped and saved." And "Scanner problem" / "Engine error" as titles go.

### [x] R15. The viewer during "Make mesh again" (pages)
It shows the current mesh but says "Make the mesh first." under Export and "Shown after
meshing." under the report, and has no progress or Cancel. The current mesh's downloads and
report stay; the job's progress and Cancel show on the page. (With R2 the current mesh is safe
until the new one is done.)

### [x] R16. Without a graphics card the Scan check panel says "Check can't be used…" and offers "Make the mesh again" (pages)
The old name, and a button that cannot help on this computer (the Confidence tooltip already
knows). Also: the Confidence button's reason and "Thin walls and edges has no effect here"
are on hover only or missing in the Edit mesh preset box; say them on the page.

### [x] R17. "Processing time" is not the time the mesh took (server)
7 min 27 s shown for about 26 minutes. Show the whole Make mesh, or name what it is.

### [x] R18. The time left misleads (pages, server)
"A few seconds left for tracking" at 68 % with 16 minutes to go, then nothing from 83 %; the
percentage crawls from 80 to 83 % for nine minutes. Estimate the whole job, or say what comes
after ("then about N min for the other stages"); give the late stages their true share.

### [x] R19. Units and numbers (server, pages)
cm² beside mm² in one paragraph of a merge and on Edit mesh; no thousands separators ("frame 23
of 1321", "5206 mm around", "Surface 278857 mm²"); "100% of the image"; "392mm" and "70common"
in the board gauge; "0.50 mm" beside "0.5 mm".

### [x] R20. Small wording (server, pages)
"The check calibration needs the cameras calibrated first."; "at each pose" (view);
"Calibration loaded" on the Scan page while the chip says "Demo calibration"; "goes back to the
base" (starting mesh); the self-test's "it has no lasers" for the simulator; "Seven short
steps" beside "of 6"; the motion sensor step's "1 min" beside "about 2 minutes"; setup "about
18 minutes" on Home and "about 10 minutes" on Device and Scan; "Last board check: passed: The
check passed: …"; "Not measured: less than 0.1 %: filled in, nothing measured it"; the line
about this computer says the graphics card "is turned off in the settings" when the
environment turned it off, and "Scans work; without one:" reads badly; advice to "Calibrate it
in Setup" for a replay without a calibration, which Setup refuses to calibrate.

### [x] R21. Names that repeat (server, pages)
Rename accepts a name another scan has; a merge's default name repeats an earlier merge's
("Bracket from the front + Bracket from the side" three times); the trash then lists two
alike. Warn on rename and offer " (2)"; a default name takes the next free one.

### [x] R22. Edit mesh stage states (pages, server)
While a job ran, "Follow the camera" (done) and "Straighten" both said "Running"; on a scan
made without a graphics card Straighten says "Skipped" above "Closed 48 loops".

### [x] R23. A big piece can go under "Remove loose bits smaller than 5 mm" (pages, server)
"297 loose bits removed, the largest 40.8 mm across": the label's second half ("or under 1 % of
the mesh") is what took it, and "Show what was removed" is no longer offered once the recipe's
later steps have run. The line names the rule that took the largest piece, and the removed
bits can be shown from the history at any later time (the thin-parts rule: nothing goes
without the user being able to see what).

Matters of taste, noted and not planned: the Align verdict's numbers said twice; "Too loose"
twice on an area card; four "Apply" buttons with the same name for a screen reader on Recipes;
the Replay field named only by its placeholder; "AprilTags" beside "markers"; Settings'
Advanced shows the settings' raw names ("Data dir") beside words, the port from the file while
the app runs on another, and jargon of the settings file itself ("ChArUco", "Poisson").

## Found while writing the user guide (2026-11-23)

The guide (`docs/guide`) was written from the pages' source and then checked against the running
demo. That turned up 45 small things in the app. From now on a change to a word, a button or a
flow also changes the guide's page and picture.

### [x] G1. Words that disagreed or were unclear (33 fixed, 2026-11-23)
- One name for one thing: "dashed scanner" (never "ghost"), "Moved onto it", the four lasers'
  names the same on chips and sliders, "Build the surface" in the Jobs panel (was "Fuse"),
  "Follow the camera" in a failure's advice (was "Track"), "Swap left and right" in Setup,
  Connect (the advice named a camera setting that does not exist).
- True to what happens: the trigger (press to start, pause or resume; hold for 1 s to stop), the
  self-test (about 25 s, three prompts), the print's scale bar (between its outer edges), the
  boss figures (0.15 to 0.19 mm small in radius as tracked, 0.23 to 0.28 with the true camera
  path), "Typical scatter" says it is twice each point's ±.
- The demo says what the engine says: print verdicts, a failed scan's reason (no code word), the
  firmware version, the setup's seven steps and 19 minutes, a finished job's line.
- Small display: one number when a time range's ends read the same ("about 11 s"), the time left
  wraps, the Download menu's hints sit under their labels, the Preset list has its own line, the
  "what this step removed" box no longer covers the breadcrumb, fit labels stay under the toolbar
  and the Section profile, the Jobs panel closes when a result is opened, a viewer whose project
  is gone shows no side panel, a screen reader hears the Ctrl+Z again.
- Compare, From points: quick clicks were dropped and the pairs went out of step (each view
  listened only on its turn). One state now holds both sides and a click counts only on its turn.
- Seen in the running demo and mended after: the picker's prompt wrapped at 1040 px, the two
  views jumped between turns and quick clicks missed (its top bar is two fixed rows now); a
  failed scan's row gave its advice twice; while "Show what was removed" is on, the selection
  tools looked active but a drag turned the view (they show as unavailable now).
- Left as they are: "Attention" in the narrow step list (the longer words do not fit), the
  device status without its calibration words under 1280 px (by design).

### [ ] G2. Proposals from the guide's writers (design questions, not decided)
- One noun for what a scan is compared with: "the reference" ("Compare with a reference"), with
  "a CAD model or a scan of a good part" said once.
- Colour modes "Deviation" (moved by your edits) and "CAD model" (off the reference) are easy
  to mix up: "Moved by edits" and "From the reference".
- "Make mesh again" is on a scan's row only above 1500 px; below it hides in More.
- "Make the mesh again" on Edit mesh shows only inside the Scan check panel for a mesh without
  confidence: also in the Stages panel's heading, for every mesh.
- "Align again from the shape alone" also shows after a points alignment: offer "Refine on the
  shapes" there.
- "Finish & calibrate" looks ready from 4 views while 15 to 25 are wanted: plain style until
  15, with the count beside it.
- "The simulator is ready" leaves no lasting sign on the Device page.
- Free space with "Keep the recording and the result" loses the undo steps for good without a
  second question.
- The demo's live model is all green: one band of "few frames" would show the yellow.
- After "Use my measurement" the engine keeps `ok: false`, so "Using your measurement" shows
  only in the demo; on the engine the card still reads as off.
- Small things seen and left: after Esc the Section button can stay highlighted (not found in
  the code; perhaps keyboard focus); the "Mesh ready" notice sits on top of an open Jobs panel;
  once a job's step line read "Building the surface" while its time-left line said "(now:
  Straighten)".
- From the page on STL and STEP (2026-11-23): there is no single way to close the outer edge
  for a print ("All holes" leaves it out; one has to type a size at least the edge's length);
  "Open Edit mesh" in the viewer's print line lands at the top of the page, not on Holes; with
  the top face as Primary the part's Z points into the part, so the STL along the part's axes
  comes in upside down (Flip Z exists); the demo's "Make it watertight" never closes the outer
  edge, and its Simplify step does not say how far the surface moved.
- Settings, Advanced: "Read the file again" does not read the file (the engine holds what it
  read at start; a hand edit needs a restart): "Show the settings again", with that said. The
  row "Source" is the raw key and shows "auto" without a file; "The app's engine" is a title
  for a section the engine never sends.

### [x] G3. The Export group says what its files are (2026-11-23)
The owner missed the way from a mesh to STL and STEP in the guide; the app did not say it
either. Three lines in the viewer's Export group: under "For CAD", that the files are the
shapes fitted and saved, as exact geometry, and not the scanned surface (for that, STL); with
the mesh downloads, that STL and PLY are along the scan's axes and how to get the part upright
at its own origin; and, when the mesh is open, that a 3D print needs a closed mesh and where
"Make it watertight" is. The guide has the page `docs/guide/to-cad.md`.

### [x] G3. The Fit tool's "Wall" choice (done 2026-11-26)
A fourth choice in the Fit tool's shape picker, "Wall" (T), shown only when the scan offers the
reading (ENGINE_PLAN X16). A click shows "Reading the wall from the camera frames…" and then a card
with the engine's one-sided sentences ("At most X mm thick here…", never a ±); a refusal is a toast
with the engine's words. Checked in the running app. Open: whether the reading should be keepable
with the scan as a fitted shape is.

### [x] G4. The Hole tool's second answer, "From the camera images" (done 2026-11-26)
After a Hole click a card under the view shows the mesh's answer, then a block "From the camera
images" with the engine's words (ENGINE_PLAN X15). It waits with "Reading the rim from the camera
frames…" and drops an answer that comes after a newer click. A refusal shows quietly in the block,
not as a toast: the mesh's answer still stands. When the mesh gives no circle (small holes over
open space) the card says "The mesh gives no circle here" and still reads the images. No new key.
Open: the reading is not kept with the scan as the fitted circle is.

### [x] G5. The tag hold offered per alignment (done 2026-11-27)
ENGINE_PLAN X19; the owner keeps `[process] align_hold_tags` off. When an alignment from tags
shares little, the "The scans share little" callout carries the server's words naming the tags
and a button "Hold the tags' position", which aligns the same scans again with `hold_tags: true`
(the same answer to "was the part moved", no picked points for two scans; with several scans the
picked points of the last result are sent again). Held: the Start figure reads "The tags hold
the position", the Shared table row "Not used: the tags decide the position", the caution (the
shapes would move it more than 1 mm) shows as a warning line, and a quiet "Let the shapes
decide" aligns again with `hold_tags: false`. Not held: the words only. The choice is per
alignment: other scans or a plain Align send nothing. Several scans: one block and one button.
Demo: the three scans of "Fender flare, left" (offer; held; held with the caution when "from
behind" stays in place; not held with "from below").

