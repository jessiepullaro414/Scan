# Maintenance plan: fixes and updates from the baseline review

Baseline: `main` = db7b328 (2026-11-10). This is a list of fix and update tasks found by reviewing
the whole of `software/` (engine, server, desktop app UI) for **efficiency** and **improvement**,
written so that a mid-tier model can do each one cold: Sonnet for local, mechanical jobs, Opus for
jobs that need design judgement. The rule for every task is **no regressions**.

How it was made: four reviewers each read one area (per-frame engine, offline processing, server
and devices, app UI) and the test suite was timed test by test. The findings marked **✔** were then
checked against the code by a second read; the ones marked **○** are as the reviewer reported them
and the task starts by confirming the problem is there. Tasks marked **-** are not findings in the
code: they are new tools, or (section F) experiments that follow from the measured times.

What to expect: **performance is the owner's first priority** (people will not adopt a slow
scanner), so section F comes first. The per-frame engine has already had two tuning rounds, and the
small engine tasks in section E together are an estimated 5-15 % of the frame time: worth doing,
but they do not change what a user feels. Section F holds the few changes that could, and each is
an experiment with a stop rule, not a sure win. Sections B (bugs), P (memory peaks) and the test
timings are safe, certain improvements.

## Rules for every task

1. **One task, one commit.** No drive-by changes; a second problem found on the way is added to
   this file, not fixed in the same commit.
2. **Confirm first.** Read the lines named under *Where*. If the problem is not there (the code
   moved or was fixed), mark the task "not needed" here and stop.
3. **Guard first.** Where a task says *add first*, write that test against the old code, see it
   pass (or, for a bug, see it fail for the stated reason), and only then change the code.
4. **Bit-identical means identical.** Where the guard says so, the output arrays and files are
   compared with `np.array_equal` or byte for byte on the same machine, before and after.
5. **Speed claims are measured.** Run the benchmark before and after on the same machine and give
   both medians in the commit message. If the gain is inside the noise, do not commit the change:
   simpler code wins.
6. **Never loosen a test to make a task pass.** If a guard cannot be met, stop and report.
7. **Both compute paths.** Engine tasks run the tests on the GPU path and again with
   `OHS_COMPUTE=cpu`.
8. Tick the task off in this file in the same commit (replace `[ ]` by `[x]` and add the measured
   result in a few words).

### Commands

| What | Command (from `software/`, in the project's venv) |
|---|---|
| Fast tests | `python -m pytest -q -m "not slow"` |
| All tests | `python -m pytest -q` |
| CPU path | set `OHS_COMPUTE=cpu`, then the same |
| Frame-time benchmark | `ohs bench --frames 90 --quality both --device gpu` |
| UI type check | `npm run typecheck` (in `software/ui`) |
| UI tests | `npm test` (in `software/ui`) |
| UI bundle sizes | `npm run build`, then the sizes in `software/ohs/server/static/assets` |

Baseline numbers (2026-11-10): 510 tests pass on the GPU path in 18 min 10 s; the CPU path runs 473
and skips 37 in 19 min (one of them needs watching, see Test timings). The 90-frame bench on the Radeon 680M: full 155 ms, preview 66 ms per
frame (medians); on the RTX 3070 Ti laptop: full 49 ms. UI first paint loads about 478 KB.

## Overview

| ID | Task | Kind | Size | Model | Checked |
|---|---|---|---|---|---|
| F1 | Measure the waits a user feels, and set targets | measure | M | Sonnet | - |
| F2 | Process fewer frames after a scan | speed, large | L | Opus | - |
| F3 | Narrow the stereo search with the depth already known | speed, large | L | Opus | - |
| F4 | Something useful on screen from Stop until the mesh is ready | felt speed | M | Opus | - |
| F5 | The stereo matcher holds Python's lock while the GPU works | speed | S | Opus | ✔ |
| F6 | Read the recording one frame ahead | speed | S-M | Sonnet | ○ |
| F7 | The tracker's time on the CPU side | speed | M-L | Opus | ○ |
| A1 | Faster tests: a fast run of about 4 minutes, a full run of about 12 | test time | M | Sonnet | ✔ |
| A2 | Before/after snapshot tool for the engine | safety net | M | Sonnet | - |
| A3 | UI tests for the PLY reader, binary frames and downloads | safety net | M | Sonnet | ○ |
| A4 | Lint for unused code (ruff, pyflakes rules only) | safety net | S | Sonnet | ✔ |
| A5 | CI on GitHub (CPU path, fast tests, UI checks) | safety net | M | Sonnet | ✔ |
| A6 | The CPU path gives slightly different results on every run | repeatability | M | Opus | ✔ |
| B1 | A job cancelled while queued never settles | bug | S | Sonnet | ✔ |
| B2 | A failed frame write is lost without an error | bug | S | Sonnet | ✔ |
| B3 | A crash mid-scan can leave an unreadable recording | bug | M | Opus | ✔ |
| B4 | The recording backlog has no limit and is not shown | robustness | M | Sonnet | ✔ |
| B5 | Rewind may not restore the reconstruction's weights (confirm first) | bug? | S-M | Opus | ○ |
| B6 | Undo/redo writes files while holding the app's main lock | robustness | M | Opus | ○ |
| B7 | A GPU failure mid-scan falls back to the CPU silently | robustness | M | Sonnet | ○ |
| B8 | The Viewer can leak a mesh when you leave during loading | bug | S | Sonnet | ✔ |
| B9 | A degenerate face in a PLY shifts the face indices | bug | S-M | Sonnet | ✔ |
| B10 | A failed Pico open leaves the serial port open | bug | S | Sonnet | ✔ |
| B11 | The two self-tests have drifted apart | duplication | S | Sonnet | ○ |
| B12 | A hole with a perimeter of zero reaches the hole list (confirm first) | bug? | S | Sonnet | ○ |
| E1 | Skip the disparity download nobody reads | speed | S | Sonnet | ✔ |
| E2 | The tracker copies the depth map on every call | speed | S | Sonnet | ✔ |
| E3 | The finest ICP level is computed twice at full quality | speed | S | Sonnet | ✔ |
| E4 | Let the TSDF integration run without blocking | speed | M | Opus | ✔ |
| E5 | Keep the depth on the GPU between stages | speed | L | Opus | ○ |
| E6 | One copy of the constants shared by CPU and GPU depth | drift | S | Sonnet | ○ |
| E7 | The laser cloud grows without limit in float64 | memory | M | Opus | ○ |
| P1 | Marker map refinement peaks at 0.5 GB | memory | S | Sonnet | ✔ |
| P2 | Remove floaters searches for an index it already has | speed | S | Sonnet | ✔ |
| P3 | Mesh refinement peaks at 350 MB per chunk | memory | S | Sonnet | ✔ |
| P4 | The laser cloud is averaged twice per processing run | speed | S | Sonnet | ✔ |
| P5 | Curve adjustment builds clouds it barely uses | speed | M | Sonnet | ○ |
| P6 | Two trajectory readers and two STL writers | duplication | S | Sonnet | ○ |
| P7 | Straighten can fuse the whole scan twice | speed | L | Opus | ○ |
| U1 | The status message re-renders the 3D scene 10 times a second | speed | S-M | Sonnet | ✔ |
| U2 | Measurement lines rebuild their geometry on every render | speed | S | Sonnet | ○ |
| U3 | Every mesh download is held twice in memory | memory | S | Sonnet | ✔ |
| U4 | Switching the shading rebuilds the lighting environment | speed | S | Sonnet | ○ |
| U5 | The Scan page downloads a library only the Viewer uses | bundle | S | Sonnet | ○ |
| U6 | Unused exports and a duplicated normals helper | dead code | S | Sonnet | ○ |
| U7 | Mesh parsing freezes the UI on large meshes | speed | M / L | Sonnet / Opus | ○ |
| S1 | `ohs --help` loads OpenCV | start-up | S | Sonnet | ✔ |
| S2 | New app connections read files on the event loop | speed | S | Sonnet | ✔ |

Order: F1 and section A first (the rest leans on them). Then F2 to F4 and the speed tasks that
matter most on integrated graphics (E4, E5), with section B alongside. E, P, U and S touch
different files and can be done in any order, or side by side in separate worktrees. Inside a
section, tasks that share a file are listed together and go to one session, in the order given.

---

## F. Performance a user feels (the priority)

A user feels three waits. Where they stand today (bench of 2026-11-10; the two machines are an
integrated GPU and a gaming laptop):

| What the user feels | Radeon 680M | RTX 3070 Ti | Source |
|---|---|---|---|
| Live scanning (half resolution) | 15.9 fps | 39.2 fps | measured (`ohs bench`, preview) |
| Processing after the scan, full quality | 6.4 fps | 20.4 fps | measured (`ohs bench`, full) |
| → wait per minute of scanning, full quality | about 9 min | about 3 min | computed, see below |
| → the same at preview quality | about 4 min | about 1.5 min | computed |
| Engine start | 0.8 s (1.9–2.0 s as F1 measures it, from a new process) | not measured | measured once |

The wait per minute of scanning is computed, not measured: the camera records 60 frames a second
(`CameraParams.fps`), the recording keeps every frame, and processing uses every one
(`process.frame_step = 1`), so a minute is up to 3,600 frames. It has never been timed on a real
scan, because the hardware is not built yet. F1 measures it.

How to read it: live scanning is in good shape. **The wait after the scan is the weak spot**, and
it is the one the small tasks in section E do not move. Two things could move it by a factor
rather than by percent: processing fewer frames (F2) and making the stereo matcher search less
(F3). Neither is bit-identical, so both are guarded by the accuracy tables in VALIDATION.md, not
by the snapshot tool.

### [x] F1. Measure the waits a user feels, and set targets (Sonnet, M)
- **Done 2026-11-10:** `ohs bench` prints, per run, the wait per minute of scanning at the
  camera's frame rate (60 fps), from the mean frame time, and stores it as `wait_per_minute_min`.
  It also times the fixed tail after the last frame (mesh extraction, cleaning, each export file,
  the laser points file; `tail_ms`) and, once per bench, the engine start: a new `ohs app` process
  until its port accepts a connection and until its first status message (`engine_start`). This
  start figure includes Python's own start and the imports, which the 0.8 s in the table above
  probably did not. Not in the tail: the app's own step after processing (reading the mesh back
  into the project and showing it, `server/processing.py` `_new_base`).
  The table is in VALIDATION.md, "The waits a user feels", with the targets. Measured on the
  Radeon: wait per minute 9.5–9.7 min at full quality by the bench and about 11 min with frame
  reading included; tail 1.8–1.9 s after a 3 s scan; engine start 1.9–2.0 s, of which about 0.8 s
  passes between the app's "hello" and its first status message (not looked into). Still to
  measure on the RTX laptop: the tail and the engine start.
- **Change:** `ohs bench` also reports, per quality, the wait per minute of scanning at the
  camera's frame rate, and the fixed tail after the last frame (meshing, cleaning, export). Add a
  timing of engine start to first status message. Put the table above into VALIDATION.md with the
  measured values, and the targets next to them.
- **Targets (agreed by the owner, 2026-11-10):** live at least 15 fps on integrated graphics and 30 fps
  on a gaming GPU (both met today). After a scan, the full-quality mesh within the time the scan
  took on a gaming GPU, and within three times that on integrated graphics (today about 3 times
  and 9 times).
- **Guard:** the existing bench output and `test_app_cli.py` unchanged; no engine behaviour
  changes.

### [x] F2. Process fewer frames after a scan (Opus, L)
- **Where:** `ohs/app/process.py:70`, `:258` (`frame_step`); `ohs/app/config.py:172`.
- **Why:** at 60 frames a second, neighbouring frames show almost the same view. The live view
  already tracks the same scan on about a quarter of the frames (it drops what it cannot keep up
  with, `frames_skipped_by_processing` in `app/live.py`). Offline, every frame pays for stereo
  matching, tracking and fusion.
- **Change, in three steps, each its own commit:**
  1. Measure only. Run the validation scans and the bench with `frame_step` 1, 2, 3 and 4, and
     table the time, the trajectory error, the mesh error and the surface noise. Note how far the
     simulated scanner moves per frame, so the result can be carried over to a real hand.
  2. If a step holds the accuracy, pick frames by motion instead of by count: the gyro in the
     recording gives the rotation between frames at no cost, with a cap on the gap.
  3. Only then decide the default. If accuracy falls, leave the default at 1 and offer the faster
     setting in the app as a choice.
- **Step 1 done 2026-11-10** (table in VALIDATION.md, "Processing fewer frames after a scan"; no
  code change). The wait falls as 1 / step, and the trajectory and the large-part error hold up
  to step 8. On smooth surfaces the mesh median rises 18–22 % at step 2 and 47–53 % at step 4,
  far beyond the spread, so by the stop rule the default stays at 1 on this evidence. One finding
  for step 3: the validation scans are 30 fps recordings, which equal step 2 on the 60 fps
  device. Step 2 gives the accuracy validated so far at half the wait; step 1 is 15–18 % better
  than that. Not measured: fast hand motion, Straighten with fewer laser crossings, marker mode,
  the CPU path, a real IMU (the gyro prior is fixed per processed frame, `pipeline.py:148`).
- **Step 2 done 2026-11-10** (code b890475, off by default: `process.frame_motion_deg`,
  `process.frame_max_gap`, `ohs process --motion DEG --max-gap N`; table in VALIDATION.md,
  "Picking frames by motion against a fixed step"). At the same number of frames it is neither
  better nor worse than a fixed step on a steady hand: it mostly picks the same frames. On a hand
  that changes speed it puts 1.7–2 times as many frames into the fast part and the trajectory
  error is a little lower, but nothing lost tracking with a fixed step either, so the advantage
  is small and not proven. It does not bring back the noise that fewer frames cost. A sliding
  hand is caught by the cap alone. An angle does not give a predictable wait. Step 3 (the
  default, and what the app offers) is the owner's decision. Not measured: a real IMU, motion
  blur, Straighten and the laser stages with picked frames, marker mode, the CPU path.
- **Step 3 done 2026-11-10** (the owner's decision: two quality levels; VALIDATION.md, "Standard
  and Best quality"). **Standard**, the default of Make mesh, the Process page, the recipes and
  `ohs process`, processes at most `process.standard_frame_rate` (30) frames per second of
  scanning: every 2nd frame of the 60 fps camera, every frame of a 30 fps recording, so the tests
  and the validation figures do not move (A2 snapshot identical). **Best** processes every frame.
  The preview is unchanged and picking by motion stays a setting, off. The quality values are
  `standard | best | preview`; the old `full` is accepted everywhere and read as `standard`. The
  later stages fuse the frames the track processed. Computed wait at Standard: about 4.8 times
  the scan's duration on the Radeon, 1.5 times on the RTX laptop; the F1 targets are not met
  yet (F3 is next). Not checked: a recording from the real camera.
- **Gain:** in proportion: every second frame halves the wait.
- **Stop rule:** more frames also average noise away. If the mesh error or noise rises beyond the
  run-to-run spread in VALIDATION.md, the default does not change.
- **Guard:** with `frame_step = 1` the A2 snapshot is identical; the accuracy tests in
  `test_e2e_scan.py` keep their thresholds.

### [x] F3. Narrow the stereo search with the depth already known (Opus, L): tried, not adopted
- **Result 2026-11-10: the stop rule hit.** The simple form (one range per frame) was built and
  measured: the mean frame time on the bench fell by 2 to 7 % in five pairs of runs (Radeon
  680M), under the 10 % below. The depth in view spans nearly the whole range in every frame (the
  floor behind the part), so one range drops only 17 % of the disparities. The code is not in
  the engine; it is kept on the branch `experiment/f3-narrow-depth-search`. Table and findings in
  VALIDATION.md, "A narrower stereo search per frame". A range per pixel stays untried: it would
  need a matcher that does not put false depth on surfaces outside the range (found here: 11 to
  33 % of their pixels).
- **Where:** `ohs/vision/depth_gpu.py` (the matcher), `ohs/vision/depth.py` (the CPU reference).
- **Why:** the matcher is nearly half of the frame on both machines (22.5 of 49 ms on the RTX,
  72.8 of 156 ms on the Radeon), and a sweep of its work-group sizes found nothing more. Its work
  grows with the number of disparities it tries. After the first frames the model already tells
  roughly how far away the surface is.
- **Confirm first:** read how the disparity range is set today, and whether it is fixed when the
  kernels are compiled.
- **Change:** start with the simple form: one range per frame, from the nearest and farthest
  depth the model predicts in view, plus a margin, with a full-range frame whenever tracking is
  weak or lost and every so often to catch new surfaces. Try it on the CPU reference first, where
  it is one parameter. A range per pixel is a second, harder step and only if the first pays.
- **Gain:** unknown until tried. Halving the range would be roughly a fifth of the frame.
- **Stop rule:** drop it if the gain on the bench is under 10 %, or if any accuracy number in
  VALIDATION.md gets worse beyond the run-to-run spread.
- **Guard:** `test_gpu_matches_cpu_on_planes` and the depth tests at full range unchanged; add
  first: a test that a surface entering from outside the range is picked up within N frames.

### [x] F4. Something useful on screen from Stop until the mesh is ready (Opus, M)
- **Done 2026-11-10.** Already there: after Stop the live model stays on screen and can be
  orbited. Added: a job reports the frame pass it is in (`Job.frames`: stage, done, total, measured
  frames per second), and the app turns that into a time left under the progress bar and in the
  Jobs menu ("About 1 min left for tracking"); on the Scan page the finished mesh takes the live
  model's place when Make mesh is done, with the camera where it was; the Viewer swaps a remade
  mesh in without a loading screen. Checked on the real server with a simulator scan (355 frames):
  the estimate through the track pass, the swap on the Scan page, cancel, and a remake in the
  Viewer. Limits: the estimate covers the pass that runs (a fuse pass can follow in Straighten,
  and the stages after the passes have no estimate); a job started from another window is not
  linked to its scan in this window (as before). Open for the owner: start Make mesh right after
  Stop without a click; show the tracked mesh on the Scan page as soon as the track pass ends.
- **Confirm first:** what the app shows between Stop and the processed mesh, and whether the live
  mesh stays on screen and can be orbited.
- **Change (as far as it is missing):** keep the live mesh visible and usable while the
  full-quality processing runs; show progress with a time estimate from the measured frames per
  second; swap the mesh in when it is ready. This does not shorten the wait, it makes it usable.
- **Guard:** `test_server_process_studio.py`, the UI type check, and a walk through the flow in
  `npm run dev:mock`.

### [ ] F5. The stereo matcher holds Python's lock while the GPU works (Opus, S) ✔
- **Found by the profile of 2026-11-10** (VALIDATION.md, "Where a frame's time goes").
- **Where:** `ohs/vision/depth_gpu.py`, `GpuStereoMatcher._run`.
- **Why:** the matcher's first output copy is non-blocking and its event is dropped at once.
  pyopencl waits for a dropped copy event without releasing Python's lock, so the main thread
  holds the lock for about 78 of the matcher's 82 ms. The two laser threads, started to run
  beside the depth, wait for it: each takes 92 ms where its own work is 21 ms, and the frame then
  waits 16 ms for them. In the app the server's threads are held up the same way.
- **Change:** keep the events of the non-blocking copies in a list until the blocking copy has
  returned, then let them go. Look at `ohs/track/filter_gpu.py` and `ohs/track/recon_tsdf.py`,
  which drop upload events the same way, and measure before changing them.
- **Gain (measured with the change patched in from outside, Radeon 680M, two pairs of runs):**
  the mean frame 159.5 → 153.3 ms and 160.4 → 152.9 ms (−4 to −5 %); the wait for the laser
  threads 16 → 1 ms; the depth stage itself rises by 8 to 9 ms. Not measured: the RTX laptop,
  live scanning.
- **Guard:** the A2 snapshot identical before and after (the bench's accuracy figures already
  were, to the last digit); the full suite on both compute paths.

### [ ] F6. Read the recording one frame ahead (Sonnet, S-M) ○
- **Where:** `ohs/io/recording.py` (the frame iterator), used by the frame passes in
  `ohs/server/processing.py`.
- **Why:** each frame's two images are read with `cv2.imread` when the pipeline asks for them:
  16 to 17 ms per frame in which nothing else runs. The bench does not count it; in Make mesh it
  is about a tenth of the wait per frame on the Radeon 680M.
- **Confirm first:** time a frame pass over a recording on disk with and without the reads (the
  profile timed `imread` inside the bench, not inside a job).
- **Change:** a reader thread that keeps one or two frames decoded ahead of the pipeline, in
  order, with a fixed small queue so memory stays flat; errors in the thread surface in the
  pass as they do today. Do F5 first: until then a thread like this is held up by the same lock.
- **Gain:** up to the 16 ms per frame, to be measured on a real job.
- **Guard:** the A2 snapshot identical; a test that the iterator yields the same frames in the
  same order, and that a read error is raised in the pass, not lost in the thread.

### [ ] F7. The tracker's time on the CPU side (Opus, M-L) ○
- **Why:** tracking and fusion take 56 to 58 ms per frame, of which the GPU works about 8. The
  rest is Python and NumPy on the host: finishing the ray cast 17.6 ms (7.0 of it waiting for
  the device), ICP 9.6, the tracking filter 6.8, fusion 10.7, source points 2.8.
- **Confirm first:** a profile inside `_raycast_gpu_finish`, `_icp` and `_fuse`, on a scan longer
  than the bench (the bench's first 13 frames run a start-up probe that a long scan does not
  feel). E5 (keep the depth on the GPU between stages) overlaps: decide which of the two carries
  what before starting.
- **Change:** unknown until the profile is read. No change that is not bit-identical without
  the owner's word.
- **Gain:** unknown; the host time in reach is about 30 ms of the 160.
- **Guard:** the A2 snapshot identical; the full suite on both compute paths.

After these, what is left of the frame is the matcher's own device time: 70 ms, 56 of them in
the two SGM kernels. That is the next large item, and a different kind of work (the kernels'
algorithm, or a coarser first pass), with accuracy to guard.

Also for felt speed, from the sections below: E4 and E5 (largest on integrated graphics, an
estimated 4-10 ms of 156), U1 and U7 (the app stays smooth), P1 and P3 (no memory spikes while
processing), S1 (start-up).

---

## A. Safety net (do these first)

### [ ] A1. Faster tests: a fast run of about 4 minutes, a full run of about 12 (Sonnet, M) ✔
- **Why:** every task in this plan ends with a test run. Today the full run is 18 minutes and the
  "fast" run (`-m "not slow"`) is still about 9. The numbers are in **Test timings** at the end.
- **Change, each its own commit:**
  1. Mark as `slow` the tests whose setup plus call is over about 4 s (the table at the end).
     No test is deleted or weakened, and every feature keeps at least one fast test.
  2. Keep the simulator's rendered recordings on disk between runs. About a third of the full run
     is fixture setup, most of it rendering. The bench already has a versioned cache
     (`test_recording_is_cached_and_versioned` in `test_app_bench.py`): use the same scheme, keyed
     on the scene's parameters and a renderer version, outside the repo, with an environment
     variable to turn it off.
  3. Add `tests/conftest.py` (there is none) for the recordings that several modules render
     separately, so one run renders each once.
- **Guard:** the number of collected tests stays 512; the full run passes on both compute paths; a
  cached recording is byte-identical to a fresh one; `--durations` before and after in the commit.

### [x] A2. Before/after snapshot tool for the engine (Sonnet, M)
- **Done 2026-11-10:** `python tools/snapshot.py snapshot <dir>` and `compare <a> <b> [--tol X]`
  (from `software/`). Both qualities at 90 frames take about 35 s on the Radeon 680M. To compare
  with an older commit, check it out in a second worktree and run the tool from a neutral folder
  with `PYTHONPATH=<worktree>/software`. Found while building it: the mesh comes out in a
  different vertex order on every run, so the snapshot stores it in a canonical order; and the
  CPU path gives slightly different poses and meshes from run to run (confirmed, see A6),
  so bit-identical checks are done on the GPU path and the tool's own real-snapshot test is
  strict only there.
- **Why:** most engine tasks below promise bit-identical output. Today that takes hand-written
  scripts.
- **Change:** a small script (for example `software/tools/snapshot.py`) with two commands.
  `snapshot <dir>` processes the simulated recording that `ohs bench` uses (see how
  `ohs/app/bench.py` gets it) at preview and full quality and saves the trajectory, the mesh's
  vertices and faces and the laser points as `.npz`. `compare <a> <b>` prints the largest
  difference per array and exits non-zero unless everything is identical (`--tol` for the tasks
  that are allowed a tolerance).
- **Guard:** a test that two snapshots of the same code compare identical, and that a perturbed
  pose is reported.
- Snapshots are per machine and per compute path (a Radeon and an RTX differ in the last bits);
  never commit them.

### [ ] A3. UI tests for the PLY reader, binary frames and downloads (Sonnet, M) ○
- **Where:** `ui/package.json` (`"test"` runs one file), `ui/src/three/ply.ts`,
  `ui/src/api/binary.ts`, `ui/src/api/http.ts` (`fetchBinary`).
- **Change:** make `npm test` run every `*.test.ts`. Move `parsePly` into a module without
  imports (Node cannot resolve the extensionless `./coords`), then add: `ply.test.ts` (all
  triangles, quads, a degenerate face, a `filled` property, big-endian, the ASCII fallback),
  `binary.test.ts` (encode/decode round trip, an unaligned header), a `fetchBinary` test with a
  fake `Response` (known and unknown length).
- **Guard:** the type check; the 21 existing selection checks still pass. These tests are the
  guard for B9, U3 and U7.

### [ ] A4. Lint for unused code (Sonnet, S) ✔
- **Where:** `software/pyproject.toml` has no linter section, although the code already carries
  `# noqa: PLC0415` comments.
- **Change:** add `[tool.ruff]` with only the pyflakes rules (`F`) plus the rule those comments
  refer to; fix what it reports (unused imports and variables) and nothing else. No reformatting.
- **Guard:** all tests; `git diff --stat` shows deletions only.
- Needs `ruff` installed in the venv: ask the owner before installing.

### [ ] A5. CI on GitHub (Sonnet, M) ✔
- **Where:** the repo has no `.github/workflows`.
- **Change:** one workflow on push and pull request: Python fast tests on the CPU path
  (`OHS_COMPUTE=cpu`, `-m "not slow"`), and in `software/ui`: `npm ci`, type check, tests, build.
- **Guard:** the workflow is green on `main` before anything else is merged through it.
- Do it after A1 (the fast tier). Ask the owner first: it turns on GitHub Actions for the repo.

### [ ] A6. The CPU path gives slightly different results on every run (Opus, M) ✔
- **Found 2026-11-10** with the snapshot tool (A2): three snapshots of the same code with
  `OHS_COMPUTE=cpu`, 20 frames. Laser points are identical. Poses differ by up to 0.008 (preview)
  and 0.002 (full) in the largest matrix entry, and the mesh by a few vertices (356,205 against
  356,186 and 356,198 at full quality). The GPU path is bit-identical from run to run.
- **Why it matters:** the mesh-dependent tests can pass or fail by chance on the CPU path (see
  B12), and a before/after check on the CPU path needs a tolerance.
- **Where:** not found yet. The differences start in the poses, so look at the CPU tracker first
  (Open3D's ray cast and ICP run on several threads); `OMP_NUM_THREADS=1` makes them smaller but
  does not remove them.
- **Change:** find the stage (compare depth, then pose, then fused volume between two runs of the
  same frames). If it can be made repeatable at no cost in speed, do that; if not, write down the
  size of the differences in VALIDATION.md and give `tools/snapshot.py compare` a default
  tolerance for CPU snapshots.
- **Guard:** no change to the GPU path (snapshot identical); the CPU bench not slower.

---

## B. Bugs and robustness

### [x] B1. A job cancelled while queued never settles (Sonnet, S) ✔
- **Done 2026-11-10:** `cancel()` runs `on_done` itself for a job that was still queued (once,
  outside the lock) and then sets `finished`, so the scan leaves "processing" at once.
  `shutdown()` settles queued jobs the same way. Two new tests.
- **Where:** `ohs/server/jobs.py:128-141` (`cancel`) and `:181-185` (`_run`).
- **Problem:** `cancel()` marks a queued job cancelled. When the pool reaches it, `_run` returns at
  `if job.state != "queued": return`, before the `finally` that calls `on_done`. The pool has two
  workers shared by processing, the demo, calibration, the self-test and the printables, so a job
  can be queued. Three handlers depend on `on_done`: a scan stays "processing"
  (`processing.py:1533`, `:1676`), a calibration session stays "solving" and answers 409
  (`calibration.py:1083`), and the device manager stays busy until the app restarts
  (`devices.py:715`).
- **Change:** run `on_done` exactly once for a job cancelled while queued (from `cancel()` outside
  the lock, or from the early return in `_run`).
- **Guard:** extend `test_job_runner_progress_failure_and_cooperative_cancel`
  (`test_server_basic.py`): block both workers, queue a third job, cancel it, assert `on_done` ran
  once with state `cancelled`. Add one test that a cancelled queued stage job leaves the scan's
  status settled. Keep `test_cancel_a_track_and_409_while_recording` passing.

### Recording (`ohs/io/recording.py`): B2, B3, B4 in one session, in this order

### [ ] B2. A failed frame write is lost without an error (Sonnet, S) ✔
- **Where:** `recording.py:93-99` and `:145-148`.
- **Problem:** the pool runs `cv2.imwrite`, which returns False on failure (disk full) instead of
  raising; nothing reads the return value. Pruning keeps only `not f.done()`, so even a future
  that raised is dropped before `close()` looks. The scan reports no error while frames are
  missing, and processing later stops at the first missing file.
- **Change:** submit a wrapper that raises when `imwrite` returns False; keep the first error when
  pruning; report it from `close()` and pass it on through `Recorder.error`.
- **Guard:** `test_recording_roundtrip`, `test_latest_slot_and_recorder`. Add first: `cv2.imwrite`
  monkeypatched to fail once, the error must be reported.

### [ ] B3. A crash mid-scan can leave an unreadable recording (Opus, M) ✔
- **Where:** `recording.py:67-70`, `:96`, `:138-158`; `server/projects.py:375` (`recover_stale`).
- **Problem:** the `frames.csv` row is written when the PNG is queued, before the file exists, and
  the three CSV files are only flushed at `close()`. After a crash the index can be missing rows
  or point at missing or half-written PNGs, and the reader raises at that frame. The app plan says
  "never lose a scan".
- **Change:** write a frame's row only once its PNG is written (in order); flush the CSV files
  about once a second (no fsync); consider writing PNGs as `.tmp` and renaming.
- **Guard:** `test_recording_roundtrip`, `test_scan_recording_roundtrip`,
  `test_process_recording_writes_everything`: a clean recording stays byte-identical. Add first:
  write N frames, do not call `close()`, flush, then every listed PNG must be readable.

### [ ] B4. The recording backlog has no limit and is not shown (Sonnet, M) ✔
- **Where:** `app/live.py:336` (unbounded queue), `recording.py:64/93` (unbounded pool queue),
  `live.py:361` (`backlog` has no caller).
- **Problem:** if PNG encoding falls behind the camera, each waiting frame holds about 4 MB and
  nothing shows it. Measured on this PC while the tests were running: 69 ms per 2560x800 frame at
  PNG level 1, about 55 fps with 4 threads.
- **Change:** cap the in-flight writes (wait on the oldest beyond twice the workers); publish the
  backlog in the status message; warn past a threshold; workers = `min(8, os.cpu_count())`.
- **Guard:** `test_latest_slot_and_recorder`, `test_sim_device_previews_and_status`. Add first: a
  slow fake writer, then check the cap and the backlog field.
- The real throughput against the real camera needs the hardware; the cap and the display do not.

### [ ] B5. Rewind may not restore the reconstruction's weights (Opus, S-M) ○
- **Where:** `ohs/server/session.py:809-831` (`_scale_weights`), `ohs/track/recon_tsdf.py`
  (`ConfidenceTsdf` has no `scale_weights`).
- **Problem:** after a rewind that rebuilds from a subsample of the keyframes, the weights are
  multiplied back up so the model is as firm as before. On the default GPU path the reconstruction
  model is a `ConfidenceTsdf`, which has no `scale_weights`; the helper falls through to
  `tracker.voxel_grid`, fails, and only logs at debug level. The rebuilt part of the model is then
  lighter than it should be, so later frames outweigh it.
- **Confirm first, this may not be a bug:** the live preview builds its pipeline with
  `recon="open3d"` (`ohs/app/live.py:1139`), and its comment says rewind is the reason. If every
  live session goes through that line, the reconstruction model is never a `ConfidenceTsdf` during
  a rewind, and the task shrinks to the warning below plus a test that pins the choice.
- **Change:** first measure whether it matters (rewind a simulated scan with and without scaled
  weights, compare the mesh error). If it does, give `ConfidenceTsdf` a `scale_weights` and decide
  what scaling means for confidence weights. Either way, log the failure as a warning.
- **Guard:** `test_server_rewind.py`. Add first: a rewind with more keyframes than the budget,
  then assert the reconstruction's weights were scaled.

### [ ] B6. Undo/redo writes files while holding the app's main lock (Opus, M) ○
- **Where:** `ohs/server/processing.py:1079-1092` (`_move`) → `_show_step` (`:899-914`) →
  `_write_result` (`:873-897`).
- **Problem:** `_move` keeps `Studio._lock` while it loads the step and writes the PLY, the STL
  and an optional OBJ, and for a step outside the cache replays the edits. Start scan, stop scan,
  the trigger and connect all wait on that lock.
- **Change:** hold `Studio._lock` only for `_check_free`; do the work under the scan's own lock
  (or as a short job). The lock order and the 409 rules need care.
- **Guard:** `test_track_edits_undo_redo_and_the_process_messages`,
  `test_a_fresh_scan_and_the_409_rules`. Add first: a slow `_write_result`, then assert the main
  lock is free during an undo.

### [ ] B7. A GPU failure mid-scan falls back to the CPU silently (Sonnet, M) ○
- **Where:** `ohs/vision/depth.py:394-400`, `:491-497`, `:535-538`; `ohs/app/pipeline.py:480-486`.
- **Problem:** a runtime failure of the GPU matcher or of the tracking filter logs a warning and
  switches to the CPU for the rest of the scan. The frame rate drops about four times and the
  depth's noise model changes, and the user is told nothing.
- **Change:** record it in the pipeline (stage → reason) and put it into the frame result's
  `extra` on the frame that switches. Showing it in the app is a second, separate task.
- **Guard:** `test_runtime_failure_falls_back_to_the_cpu`. Add first: a pipeline test that makes
  the matcher or the filter raise and checks the flag.

### [ ] B8. The Viewer can leak a mesh when you leave during loading (Sonnet, S) ✔
- **Where:** `ui/src/pages/ViewerPage.tsx:228-239`; compare `ui/src/pages/process/mesh.ts:93-95`.
- **Problem:** the 30 ms parse timer does not check `ctrl.signal.aborted` again. Leave the page in
  that window and the geometry is created after the clean-up ran and is never disposed.
- **Change:** add the check. Optional second step (M): one shared download-and-parse hook for the
  Viewer and the Process page, which are near copies.
- **Guard:** the type check; on the Viewer, `renderer.info.memory.geometries` returns to its
  earlier value after leaving.

### [ ] B9. A degenerate face in a PLY shifts the face indices (Sonnet, S-M) ✔
- **Where:** `ui/src/three/ply.ts:214-251`.
- **Problem:** a face with fewer than three corners writes nothing, but while the face map is
  still null the triangle counter falls behind, so a selection can send the wrong face indices to
  delete/keep. Real files with such faces are rare.
- **Change:** build the map when such a face is met. **Guard:** the PLY tests from A3 (do A3
  first).

### [ ] B10. A failed Pico open leaves the serial port open (Sonnet, S) ✔
- **Where:** `ohs/devices/pico.py:214-219`: `reset_input_buffer()` runs outside the `try` that
  shuts the transport down.
- **Change:** move it inside. **Guard:** `test_devices_pico.py`; add a fake transport whose
  `reset_input_buffer` raises and assert the port was closed.

### [ ] B11. The two self-tests have drifted apart (Sonnet, S) ○
- **Where:** `ohs/app/selftest.py:189-199`, `:214` and `ohs/server/selftest.py:306-316`, `:330`;
  also `app/live.py:192` (`_Rate`) against `server/util.py:16` (`RateMeter`).
- **Problem:** the image checks are copies with the same thresholds; the accelerometer check
  already differs (norm of the mean against mean of the norms).
- **Change:** one shared check function; each caller keeps its own hint text. Pick one definition
  for the accelerometer check and say which in the commit.
- **Guard:** the self-test tests in `test_server_setup.py` and `test_app_cli.py`; messages stay
  identical.

### [ ] B12. A hole with a perimeter of zero reaches the hole list (Sonnet, S) ○
- **Found 2026-11-10:** `test_server_process_studio.py::test_overlays` failed once in the full
  CPU-path run (`perimeter_mm` 0.0 for one hole) and passed in two runs of the module alone, on
  the same code. The old code passed twice too, so nothing says the first wave caused it; the
  mesh differs a little from run to run on the CPU path (A6).
- **Where:** `ohs/mesh/holes.py:74-94` (`hole_outlines`) lists every loop that is not the outer
  border, also one whose vertices sit on the same spot.
- **Change:** confirm first with a built mesh (a boundary loop whose vertices coincide). Then
  decide where it belongs: leave such loops out of the list, or merge the coincident vertices
  when the mesh is cleaned. Say which in the commit.
- **Guard:** `test_mesh_holes.py`; a new test with the built mesh; `test_overlays` unchanged.

---

## E. Per-frame engine (bit-identical unless stated)

Every task here: tests on both compute paths, the A2 snapshot identical before and after, and the
bench medians before and after in the commit message.

### [x] E1. Skip the disparity download nobody reads (Sonnet, S) ✔
- **Done 2026-11-10:** with exact division the GPU matcher downloads only depth and sigma. Output
  identical (snapshot before/after on the GPU path, both qualities). Bench on the Radeon 680M,
  90 frames, three runs before and three after, alternating: the depth stage went from 74.8 to
  73.2 ms at full quality and from 20.0 to 18.4 ms at preview (medians; the runs do not
  overlap). The whole frame, E1 to E3 together: 161.7 to 160.1 ms at full and 66.4 to 65.2 ms at
  preview (means), which is about the spread between runs (2 to 3 ms).
- **Where:** `ohs/vision/depth.py:554-561`, `ohs/vision/depth_gpu.py:1187-1193`.
- **Problem:** `depth_sigma` downloads disparity, depth and sigma every frame; the disparity is
  only read when the GPU cannot divide exactly.
- **Change:** ask for the disparity only when `exact_division` is false.
- **Gain (estimate):** one 4 MB download per frame: about 0.3 ms on the laptop, 1-2 ms on the
  Radeon.
- **Guard:** `test_depth_without_exact_division_is_converted_on_the_cpu`,
  `test_gpu_api_contract`, `test_depth_sigma_is_calibrated`.

### [x] E2. The tracker copies the depth map on every call (Sonnet, S) ✔
- **Done 2026-11-10:** `_clean_depth` returns its input when no pixel
  is bad (zeros no longer count as bad). One difference: a pixel holding -0.0 is no longer
  rewritten to +0.0. Output identical (snapshot before/after on the GPU path). Measured together
  with E3: see there.
- **Where:** `ohs/track/markerless.py:517-527` (`_clean_depth`), called one to three times a
  frame.
- **Problem:** `d < depth_min` is true for every 0 pixel, every depth map has zeros, so
  `bad.any()` is always true and the map is copied and rewritten although nothing changes.
- **Change:** flag only non-zero bad pixels; return the input when there are none.
- **Gain (estimate):** 1-2 ms per call at 1280x800.
- **Guard:** `test_garbage_depth_fails_and_relocalises`, `test_tracking_accuracy_along_arc`,
  `test_alt_depth_holds_the_pose_the_main_input_cannot`. Check that no caller writes into the
  returned array (it may now be the caller's own).

### [x] E3. The finest ICP level is computed twice at full quality (Sonnet, S) ✔
- **Done 2026-11-10:** `_source_points` computes each (offset, stride)
  pair once per call and shares the result between levels. Output identical (snapshot
  before/after on the GPU path).
- **Measured, E2 and E3 together (Radeon 680M, same runs as E1):** the tracking stage at full
  quality went from 53.3 to 52.0 ms (medians), with the runs overlapping; at preview there is no
  change. In the same runs the `raycast` line reads about 1.3 ms higher after than before (14.9
  to 16.2 ms); two runs of the commit with E1 alone gave 14.3 and 15.5.
- **Timed directly** (old and new function side by side, 1280x800 depth with 12 % empty pixels,
  200 calls, three rounds): `_clean_depth` 3.6 to 1.5 ms a call, `_source_points` at full quality
  4.9 to 2.9 ms a call. So the two save about 4 ms of CPU time a frame, and only about 1.3 ms of
  it shows in the frame time on the Radeon. Likely reason, not proven: the ray cast waits for
  GPU work that used to finish during that CPU time. To check on the RTX laptop, where the GPU
  waits are shorter.
- **Where:** `ohs/track/markerless.py:378-390` and `:529-552`. Same session as E2.
- **Problem:** strides (16, 8, 4) with spacing 8 become [16, 8, 8], so `_source_points`
  back-projects and computes normals for the same grid twice on every call.
- **Change:** compute each distinct (offset, stride) once and share the arrays.
- **Gain (estimate):** 0.3-1 ms per call at full quality; preview is unaffected.
- **Guard:** `test_tracking_accuracy_along_arc`,
  `test_weak_directions_say_what_the_surface_cannot_pin_down`,
  `test_translation_strength_says_whether_the_input_holds_the_slide`.

### [ ] E4. Let the TSDF integration run without blocking (Opus, M) ✔
- **Where:** `ohs/track/recon_tsdf.py:513-521` and `:586-613`.
- **Problem:** `integrate` waits for the GPU kernel (`finish()` twice) only to keep two host
  arrays alive. VALIDATION.md measures that wait at 2.4 ms (laptop) and 9.7 ms (Radeon).
- **Change:** keep the arrays in `self` until the next `integrate`, `download` or `close`, and
  drop the waits; `download()` and `close()` must wait first.
- **Gain (estimate):** 1-4 ms per frame; the bench decides, since the GPU is shared.
- **Guard:** `test_gpu_and_numpy_agree`, `test_without_sigmas_it_meshes_like_open3d`,
  `test_gpu_brick_search_finds_the_cpu_bricks`. Add first: N frames with and without the waits
  give an identical `download()`, and `close()` straight after `integrate` works.

### [ ] E5. Keep the depth on the GPU between stages (Opus, L) ○
- **Where:** `ohs/track/filter_gpu.py:217-232`, `ohs/track/recon_tsdf.py:571-584`,
  `ohs/track/volume_gpu.py:714-735`.
- **Problem:** the depth is downloaded, then uploaded again by the filter (4 MB), by the
  reconstruction with its sigma (8 MB) and by the tracking volume (4 MB). All three share one
  OpenCL context. VALIDATION.md already names this as the next step.
- **Change:** hand device buffers and events between the stages; keep the downloads the CPU needs.
- **Gain (estimate):** 1-1.5 ms on the laptop, 3-6 ms on the Radeon. After E4.
- **Guard:** `test_gpu_matches_cpu_filter`, `test_gpu_and_numpy_agree`,
  `test_integration_values_and_weights`, the A2 snapshot.

### [x] E6. One copy of the constants shared by CPU and GPU depth (Sonnet, S) ○
- **Done 2026-11-10:** `GPU_MIN_SIZE`, `LK_EPS2` and `LK_MIN_TEXTURE` are defined once in
  `depth.py` and imported by `depth_gpu.py` (`depth_gpu.MIN_SIZE` is now `depth.GPU_MIN_SIZE`);
  one helper `_drop_gpu_if_too_small` replaces the three copies of the size check. A test holds
  the two modules to the same objects.
- **Where:** `ohs/vision/depth_gpu.py:106-109` against `ohs/vision/depth.py:89`, `:211-212`; the
  "too small for the GPU" block is repeated at `depth.py:489`, `:529`, `:547`. Same session as E1.
- **Change:** import the constants from one place; one helper for the size check.
- **Guard:** `test_gpu_matches_cpu_on_planes`, `test_tiny_images_go_to_the_cpu`.

### [ ] E7. The laser cloud grows without limit in float64 (Opus, M) ○ (not bit-identical)
- **Where:** `ohs/track/fusion.py:71-89`, `ohs/app/pipeline.py:604-606`.
- **Problem:** every tracked frame appends a float64 chunk and `raw_points()` concatenates them
  all. Estimated 250-450 MB after 10,000 frames.
- **Change:** measure first. Then choose between float32 storage and periodic consolidation.
- **Guard:** add first: `points()` within 1e-4 mm of today's output, and a memory check over a
  5,000-frame synthetic feed.

---

## P. Offline processing

### [x] P1. Marker map refinement peaks at 0.5 GB (Sonnet, S) ✔
- **Done 2026-11-10:** `_consensus` builds the "close" table in blocks of 64 rows. Peak memory at
  1,410 frames: 511 MB before, 28 MB after; time unchanged at about 0.3 s (measured by the
  builder, not repeated in review).
- **Where:** `ohs/track/markers.py:534-545` (`_consensus`), called from `refine_map`
  (`:571-595`); the frame list it works on is never capped (`:614`, `:638`).
- **Problem:** `A[:,None]-A[None,:]` builds an n×n×4×3 float64 tensor per tag and per iteration.
  Measured for n = 1,410: 0.31 s and a 509 MB peak for one call.
- **Change:** compute the same comparison in blocks of about 64 rows; counts and members are
  unchanged, so the result is bit-identical.
- **Guard:** `test_map_accuracy_and_refinement`. Add first: old against new `_consensus` on random
  observations, with ties.

### [ ] P2. Remove floaters searches for an index it already has (Sonnet, S) ✔
- **Where:** `ohs/mesh/confidence.py:85-90` (and `carry_filled`, `:120-131`), `ohs/mesh/clean.py:64`,
  `ohs/server/processing.py:1022`.
- **Problem:** after `remove_floaters`, each vertex's sigma is carried over by building a KD-tree
  of the old vertices and querying every new one, although the edit knows which vertices it kept.
  The default recipe runs this on every scan. Measured on 1 M synthetic points: 1.43 s against
  0.34 s for the edit itself.
- **Change:** return the kept index from the edit and index with it.
- **Guard:** `test_carry_sigma_follows_the_vertices_through_each_edit`,
  `test_remove_floaters_by_size_count_fraction_and_largest`,
  `test_make_mesh_runs_the_default_recipe`. Add first: the indexed result equals the KD result.
  Meshes with two vertices at the same position may differ there; the exact index is the correct
  one, say so in the commit.

### [x] P3. Mesh refinement peaks at 350 MB per chunk (Sonnet, S) ✔
- **Done 2026-11-10:** `project_onto_patches` takes `chunk` (default 25,000 vertices, was a fixed
  200,000). Peak memory with 200,000 vertices and 10,000 patches: 606 MB before, 145 MB after;
  time unchanged at 2.6 s (measured by the builder, not repeated in review).
- **Where:** `ohs/track/spacetime.py:883-905` (`project_onto_patches`).
- **Problem:** chunks of 200,000 vertices with 12 neighbours of 18 float64 values each.
- **Change:** chunks of about 25,000; rows are independent, so the output is bit-identical.
- **Guard:** `test_vertices_move_onto_the_patches_and_nowhere_else`,
  `test_the_refined_mesh_is_closer_to_the_truth`. Add first: two chunk sizes give equal output.

### [ ] P4. The laser cloud is averaged twice per processing run (Sonnet, S) ✔
- **Where:** `ohs/app/process.py:390-398`, `ohs/track/fusion.py:85-107`.
- **Problem:** `save_ply` computes the voxel-averaged points, the report computes them again, and
  the raw count concatenates every chunk just to take its length.
- **Change:** compute the points once, write them, count raw points from the chunk lengths.
- **Guard:** `test_laser_cloud_accumulates_in_world_frame`,
  `test_process_recording_fuses_at_given_poses`; `report["lasers"]` and the bytes of
  `laser_points.ply` unchanged.

### [ ] P5. Curve adjustment builds clouds it barely uses (Sonnet, M) ○
- **Where:** `ohs/track/curve_ba.py:414-431`, `ohs/track/posegraph.py:514-543`.
- **Problem:** for up to 200 frames it builds fine and coarse clouds with two KD-trees each, then
  uses about 400 points of the fine cloud.
- **Change:** a points-only path in `KeyframeClouds`. Measure before and after.
- **Guard:** `test_drift_is_reduced`, `test_true_poses_are_left_alone`; the point arrays identical.

### [ ] P6. Two trajectory readers and two STL writers (Sonnet, S) ○
- **Where:** `ohs/server/processing.py:492-499` against `ohs/track/posegraph.py:269-286` and
  `ohs/app/process.py:165-170`; `processing.py:455-468` against `ohs/track/export.py:143-151`.
- **Problem:** the two readers parse the `ok` column differently; the two STL writers differ in
  speed.
- **Change:** one reader, one writer each; files stay byte-identical.
- **Guard:** `test_trajectory_csv_round_trip_matches_process_recording`, `test_stl_roundtrip`,
  plus a byte comparison of an STL written before and after.

### [ ] P7. Straighten can fuse the whole scan twice (Opus, L) ○
- **Where:** `ohs/server/processing.py:1277-1315`.
- **Problem:** with loops it fuses once as the reference for the laser crossings, and again if
  those change the poses; the first mesh is thrown away.
- **Change:** investigate a half-resolution reference fuse. This changes a quality trade-off:
  measure the mesh error against the simulator's truth before and after, and stop if it is worse.
- **Guard:** `test_straighten_closes_loops_and_fuses_again`; add the error benchmark first.

---

## U. Desktop app UI

Every task here: the type check, `npm test`, and for bundle tasks the chunk sizes before and after.

### [ ] U1. The status message re-renders the 3D scene 10 times a second (Sonnet, S-M) ✔
- **Where:** `ui/src/store/session.ts:54-67`; `ScanPage.tsx:40`; `ProjectPage.tsx:148`, `:168`;
  `pages/scan/LiveCanvas.tsx`. The server sends `status` at 10 Hz whenever a window is connected,
  scanning or not (`ohs/server/studio.py:76`, `:251-255`).
- **Problem:** every status message stores a new `session` object; the Scan and Project pages
  subscribe to the whole object and the canvases are not memoised, so the 3D tree is reconciled
  ten times a second, also while idle.
- **Change:** keep the old object when its fields are equal (a pure `sameSession(a, b)` in its own
  module); select primitives on the two pages; wrap the three canvases in `React.memo`.
- **Guard:** a node test of `sameSession`; a React Profiler count before and after in
  `npm run dev:mock`.

### [ ] U2. Measurement lines rebuild their geometry on every render (Sonnet, S) ○
- **Where:** `LiveCanvas.tsx:293`; `pages/viewer/ViewerCanvas.tsx:294`, `:315`. Same session as U1.
- **Problem:** drei's `<Line>` rebuilds when `points` is a new array, and these are written
  inline.
- **Change:** a module constant for the fixed line; a memoised component per measurement.

### [ ] U3. Every mesh download is held twice in memory (Sonnet, S) ✔
- **Where:** `ui/src/api/http.ts`, `fetchBinary`.
- **Problem:** the chunks are collected and then copied into one array.
- **Change:** allocate once when `content-length` is known; fall back to chunks otherwise.
- **Guard:** the `fetchBinary` test from A3.

### [ ] U4. Switching the shading rebuilds the lighting environment (Sonnet, S) ○
- **Where:** `ui/src/three/Studio.tsx:15-31`.
- **Change:** build the environment once per renderer; set the intensity in a second effect.

### [ ] U5. The Scan page downloads a library only the Viewer uses (Sonnet, S) ○
- **Where:** `ui/vite.config.ts:45` puts `three-mesh-bvh` in the shared `r3f` chunk.
- **Gain (estimate):** about 40 KB off the Scan and Process pages. **Guard:** chunk sizes.

### [ ] U6. Unused exports and a duplicated normals helper (Sonnet, S) ○
- **Where:** never referenced: `realUrl` (`api/backend.ts`), `getSocketFactory` (`api/socket.ts`),
  `QUALITY_TEXT` (`components/ScanBits.tsx`), `fmtMm`, `fmtDate` (`lib/format.ts`), `usePrevious`
  (`lib/util.ts`), `threeToCv`, `sphereToThree` (`three/coords.ts`). `LiveCanvas.tsx:106-141`
  duplicates the normals code of `three/ply.ts`.
- **Change:** search for each name again, then delete. **Guard:** the bundle is the same size or
  smaller.

### [ ] U7. Mesh parsing freezes the UI on large meshes (Sonnet M, then Opus L) ○
- **Where:** `pages/process/mesh.ts:93-109`, `ViewerPage.tsx:228`, `three/ply.ts:188-249`.
- **Change:** (a) Sonnet: a fast path for the usual face layout (one count byte, 32-bit indices).
  (b) Opus: parse in a Web Worker and transfer the arrays.
- **Guard:** the PLY tests from A3 give the same arrays; the timing log in `mesh.ts:98`.

---

## S. Start-up and server

### [ ] S1. `ohs --help` loads OpenCV (Sonnet, S) ✔
- **Where:** `ohs/app/config.py:39` imports `CameraParams` from `ohs/devices/camera.py`, which
  imports `cv2`. Measured: `import ohs.app.cli` takes 540-600 ms, 300-480 ms of it OpenCV.
- **Change:** move `CameraParams` to a module without `cv2` and re-export it from `camera.py`.
- **Guard:** `test_app_cli.py`, `test_app_config.py`, `test_devices_camera.py`. Add first: a
  subprocess test that `cv2` is not in `sys.modules` after `import ohs.app.cli`.

### [ ] S2. New app connections read files on the event loop (Sonnet, S) ✔
- **Where:** `ohs/server/app.py:588`, `:610` call `seed_client` (`studio.py:230-238`), which reads
  the calibration and setup files, on the event loop; every client's sender pauses meanwhile.
- **Change:** `await asyncio.to_thread(...)`, keeping the order of messages.
- **Guard:** `test_websocket_hello_device_session_and_status`,
  `test_websocket_calibration_messages`, `test_sim_device_previews_and_status`.

---

## Reviewed and fine: leave alone

So nobody spends a session on these again.

- **Engine:** device and program caching; precomputed rectify maps; preallocated matcher and
  filter buffers; the tracking volume's staging buffer and `release()`; the reconstruction's pool
  growth; the gyro buffer (10 s); `pipeline.close()` and `reset()`.
- **Offline:** job cancel and clean-up once running; the routes run in the thread pool, not on the
  event loop; the undo history is on disk and pruned; `topology`, `simplify`, `smoothing`,
  `anchor`, `holes`, `stats`; the pose graph (300 keyframes at most).
- **Server:** the websocket hub keeps only the latest message per kind, so a slow window cannot
  grow memory; binary framing; preview JPEGs (about 1 ms); the local-only guard, CORS and bind
  address; no path traversal found; device loss turns the emitters off; the firmware's watchdog and
  fault paths; one calibration solver, not two; `APP_API.md` matches every route.
- **UI:** the mock backend is a flag-gated dynamic import and stays out of the first paint; the 3D
  pages and the setup wizard load lazily; three.js resources are disposed; per-frame callbacks
  reuse their objects; binary decoding is zero-copy; the three.js chunk (771 KB) is what
  react-three-fiber needs.

## Not reviewed

- `ohs/calib/` (board, stereo, IMU, laser planes): skimmed only.
- `ohs/sim/`: only as far as it drives the test time.
- `app/cli.py` and `app/config.py` bodies, `app/live.py` past line 410.
- The UI's setup wizard, Home and Device pages, and most of `components/ui`.
- Whether the mock backend's replies still match `api/types.ts` (they are untyped, so the type
  check cannot tell).
- Anything that needs the real scanner: camera and Pico unplug timing, real PNG throughput.

## Not in this plan

Larger than a maintenance task, tracked elsewhere: the three detailed-part limits in
VALIDATION.md (no detection on the CPU-only path, no switch mid-scan, rotation error), and the
work that waits for the real hardware (hardware v2, the DOE demo).

## Test timings

Measured on 2026-11-10 on the GPU path (Radeon 680M): 510 tests in 1,091 s, with
`pytest --durations=90`.

- The 90 slowest entries are about 985 s, 90 % of the run. The other 420 or so tests take about
  105 s together.
- Fixture setup is about 370 s, a third of the run. Most of it is the simulator rendering
  recordings (about 1 s per full-resolution stereo frame).
- The 13 tests marked `slow` are about 560 s, so the fast run is about 530 s (estimated from the
  sums, not run separately). Marking the entries below over 4 s that are not yet `slow` takes
  about another 270 s off, which leaves about 4 minutes.

The slowest entries (s = already marked `slow`):

| Seconds | Phase | Test | |
|---|---|---|---|
| 160 | setup | `test_e2e_calibration.py::test_full_stereo` | s |
| 86 | call | `test_e2e_scan.py::test_full_markerless_car_panel` | s |
| 80 + 18 | setup + call | `test_track_posegraph.py::test_real_drift_panel_sweep_gpu` | s |
| 66 | call | `test_server_calibration.py::test_wizard_end_to_end_on_the_simulator` | s |
| 44 | call | `test_e2e_calibration.py::test_full_chain_scan` | s |
| 35 + 9 | setup + call | `test_e2e_scan.py::test_full_markerless_sphere` | s |
| 26 | call | `test_server_live.py::test_scan_rewind_stop_and_process` | |
| 23 + 16 | call + setup | `test_server_process_studio.py::test_straighten_closes_loops_and_fuses_again` | |
| 23 | call | `test_sim_board_camera.py::test_imu_calibration_from_one_pass` | |
| 21 | call | `test_server_calibration.py::test_device_target_stereo_is_saved_and_the_camera_reloads_it` | |
| 19 | call | `test_e2e_calibration.py::test_full_imu` | s |
| 19 | setup | `test_track_spacetime.py::test_frames_from_recording_spreads_and_rectifies` | |
| 18 | call | `test_track_posegraph.py::test_real_drift_panel_sweep_cpu` | s |
| 17 | setup | `test_track_posegraph.py::test_true_poses_come_back_unchanged` | |
| 13 | call | `test_e2e_calibration.py::test_imu_from_recording_fast` | |
| 13 | call | `test_server_calibration.py::test_calibration_with_the_camera` | |
| 13 | setup | `test_track_curve_ba.py::test_the_curves_meet_at_the_true_poses` | |
| 10 | call | `test_e2e_scan.py::test_marker_mode_full_resolution_short` | |
| 9 | call | `test_server_process_studio.py::test_refine_from_the_images` | |
| 8 | setup | `test_e2e_calibration.py::test_stereo_from_recording` | |
| 7.5 | call | `test_track_spacetime.py::test_the_refined_mesh_is_closer_to_the_truth` | |
| 7 | call | `test_server_process_studio.py::test_track_edits_undo_redo_and_the_process_messages` | |

A setup time belongs to a shared fixture, charged to the first test that uses it: when that test
is deselected, the next user of the fixture pays. Check with `--durations` after marking.

One thing to watch: a full run on the CPU path earlier on 2026-11-10 showed
`test_track_markerless.py::test_alt_depth_holds_the_pose_the_main_input_cannot` failing. Run alone
at db7b328 on the CPU path it passes. It is not established whether that full run was at this
commit or just before it. The first full CPU run under this plan settles it; if the test fails
there, it is a flaky test to fix, not to loosen.
