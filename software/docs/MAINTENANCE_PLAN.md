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
| F6 | Read the recording one frame ahead | speed | S-M | Sonnet | ✔ |
| F7 | The tracker's time on the CPU side | speed | M-L | Opus | ✔ |
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

### [x] F5. The stereo matcher holds Python's lock while the GPU works (Opus, S) ✔
- **Done 2026-11-11.** `_run` keeps the events of its non-blocking copies until the blocking copy
  has returned. Radeon 680M, GPU path, Standard, 90 frames, old and new runs alternated: the mean
  frame 160.8 / 161.4 ms → 151.4 / 151.9 / 152.7 ms (−5 to −6 %), the wait for the laser threads
  16.3 → 0.9–1.4 ms, the depth stage 81 → 86–87 ms. The A2 snapshot is identical. The two other
  places were looked at and left alone: their dropped events are uploads at the head of an idle
  queue (`_upload_images` is 1.9 ms per frame in all), and no thread of the pipeline waits
  beside them. Not measured: the RTX laptop, preview quality, live scanning.
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

### [x] F6. Read the recording one frame ahead (Sonnet, S-M) ✔
- **Done 2026-11-11.** `RecordingReader.iter_ahead(positions, ahead)` reads the frames in a thread,
  at most `ahead` of them before they are asked for; the frame pass of `process_recording`
  (`ohs/app/process.py`, the pass behind Make mesh) uses it with two frames ahead and closes it
  when the pass ends, fails or is cancelled. Radeon 680M, GPU path, default settings, the first
  150 frames of a 355-frame recording on disk, old and new runs alternated three times: time per
  frame 167.5 / 169.0 / 172.9 ms → 149.7 / 151.2 / 152.1 ms (−17 to −21 ms, about 11 %), the
  pass with its mesh 28.3–29.0 → 25.7–26.0 s; the mesh has the same vertex and face counts in
  all six runs. The A2 snapshot is identical. Three tests in `tests/test_core.py`: same frames
  in the same order (also out of order, repeated, one, none, no read-ahead), a missing image
  fails the pass at its frame, closing early ends the thread. Not changed: the later passes
  (`ohs/track/posegraph.py`, `spacetime.py`, `curve_ba.py`) and the bench, which read single
  frames. Not measured: the RTX laptop, a recording on a slow disk, the CPU path.
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

### [x] F7. The tracker's time on the CPU side (Opus, M-L) ✔
- **Done 2026-11-11:** profiled line by line on a 355-frame recording (Radeon 680M, GPU path,
  default settings; figures in VALIDATION.md), then the two host costs that can go without
  changing a bit were removed. (1) The GPU model view no longer builds vertices and normals for
  all 1280x800 pixels (8.2 ms per frame): ICP reads them at the few percent of pixels it matches,
  so `_ModelView.at(pix)` in `ohs/track/markerless.py` now makes them there, with the same
  arithmetic per pixel; `.V` and `.N` still give the whole maps. (2) Three counts of the valid
  depth pixels use `np.count_nonzero(x != 0)` in place of `np.count_nonzero(x)` (3.6 → 0.26 ms
  each on a float32 depth map, the same integer). Old and new runs alternated three times, 150
  frames: time per frame 149.4 / 150.3 / 151.3 ms → 140.9 / 141.2 / 143.7 ms (−8 to −9 ms,
  about 6 %), the pass with its mesh 25.5–25.9 → 24.2–24.7 s, the same mesh in all six runs.
  The A2 snapshot is identical. New test `test_model_view_lookup_equals_the_whole_maps` (the
  lookup against the whole maps, bit for bit, on both backends).
- **Left, and why:** of the tracker's 53 ms per frame about 17 are waits for the device, not
  host work (the ray cast 5.5, the fusion's `finish` 5.4, the tracking filter's copy back 4.6,
  the depth upload 1.7); they can only be overlapped with other work, which belongs with E5.
  The ICP's own arithmetic (8.5 ms over 7 iterations) and the source points (2.7 ms) have no
  single costly line. The range check in `_clean_depth` (1.5 ms) was left: its cheaper form
  saves 0.5 ms and differs when the depth limit is infinite. So the "30 ms in reach" below was
  too high; about 9 were. Not measured: the RTX laptop, the CPU path (its model view is
  unchanged, only the counts apply).
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

**First look at the two SGM kernels (2026-11-11, Radeon 680M, 1280x800, 208 disparities):** per
frame `sgm_vertical` takes 28.2 ms and `sgm_horizontal` 28.0 ms of about 75 ms for all the
matcher's kernels (census 9.7, support 2.2, the rest under 1.2 each). Three changes that keep
the depth map identical were tried; each gave the same hash over the matcher's outputs, and
none is in the code:

| Tried | `sgm_vertical` | Verdict |
|---|---|---|
| Store the matching cost in the top-down pass and load it in the bottom-up pass (213 MB more device memory) | 28.2 -> 33.7 ms | slower |
| Top-down path costs stored column by column | 28.2 -> 28.9 ms | no gain |
| The vertical pass writes its sums in place, one volume less to touch | 28.2 -> 27.4 ms, the horizontal pass 0.3 ms slower (3 runs each, alternated) | inside the noise |

So the kernels gain nothing from less arithmetic paid for with more memory traffic, and the
memory layout is not what holds them back. What is left changes the depth map: a coarser first
pass that narrows the search per pixel, or fewer disparities. At 640x400 the kernels take about
11.5 ms against about 62 ms, with the median error 0.41 mm against 0.32 mm; F3 showed that a
narrowed range can give false depth on surfaces outside it. Any such change is the owner's
decision first, with accuracy measured before it is adopted.

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

### [x] A3. UI tests for the PLY reader, binary frames and downloads (Sonnet, M) ✔
- **Where:** `ui/package.json` (`"test"` runs one file), `ui/src/three/ply.ts`,
  `ui/src/api/binary.ts`, `ui/src/api/http.ts` (`fetchBinary`).
- **Change:** make `npm test` run every `*.test.ts`. Move `parsePly` into a module without
  imports (Node cannot resolve the extensionless `./coords`), then add: `ply.test.ts` (all
  triangles, quads, a degenerate face, a `filled` property, big-endian, the ASCII fallback),
  `binary.test.ts` (encode/decode round trip, an unaligned header), a `fetchBinary` test with a
  fake `Response` (known and unknown length).
- **Guard:** the type check; the 21 existing selection checks still pass. These tests are the
  guard for B9, U3 and U7.
- **Done 2026-11-12.** `npm test` = `node --test` over `src/**/*.test.ts` (each file still prints
  its count). `parsePly` and its types moved unchanged (checked line by line) to
  `src/three/plyParse.ts`, which has no imports; `ply.ts` imports and re-exports them. New:
  `ply.test.ts` 36 checks (triangles, quad/pentagon fans, `filled`, big-endian, ASCII and bad
  files give null, empty faces, degenerate faces; two checks marked "B9 (current, wrong)"),
  `binary.test.ts` 49, `http.test.ts` 11 (`fetchBinary` with a fake `Response`). Type check,
  `npm test` (6 files: 49 + 11 + 35 + 21 + 16 + 36 checks) and the build pass. Noticed: a PLY
  whose face element has 0 faces gives an empty mesh, not null (so no fallback to `PLYLoader`).

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
- **Seen again 2026-11-10:** `test_server_process_studio.py::test_refine_with_lasers` failed once
  in the full CPU-path run after F4 (the mean laser residual before refining was −0.25 mm where
  the test expects −0.5 ± 0.15) and passed in two runs of the module alone on the same code. F4
  changed the progress reporting only, and the GPU-path run passed (573 tests). A difference
  this large is more than "a few vertices": the preview track behind it is worth a look here.
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

### [x] B2. A failed frame write is lost without an error (Sonnet, S) ✔
- **Where:** `recording.py:93-99` and `:145-148`.
- **Problem:** the pool runs `cv2.imwrite`, which returns False on failure (disk full) instead of
  raising; nothing reads the return value. Pruning keeps only `not f.done()`, so even a future
  that raised is dropped before `close()` looks. The scan reports no error while frames are
  missing, and processing later stops at the first missing file.
- **Change:** submit a wrapper that raises when `imwrite` returns False; keep the first error when
  pruning; report it from `close()` and pass it on through `Recorder.error`.
- **Guard:** `test_recording_roundtrip`, `test_latest_slot_and_recorder`. Add first: `cv2.imwrite`
  monkeypatched to fail once, the error must be reported.
- **Done 2026-11-12.** Frames go through `_imwrite`, which raises `OSError` when `cv2.imwrite`
  returns False; `RecordingWriter.error` keeps the first failure (also across pruning);
  `close()` finishes the folder (CSV files, `meta.json`) and then raises it; `Recorder.close()`
  catches it into `Recorder.error` and the summary. New tests `test_recording_failed_frame_write_is_reported`
  (returns False / raises) and `test_recorder_reports_failed_frame_write` failed on the old code
  (nothing raised, `error` None) and pass now. A clean recording makes the same `cv2.imwrite`
  call with the same arguments. `test_core.py` and `test_app_live.py`: 58 passed.

### [x] B3. A crash mid-scan can leave an unreadable recording (Opus, M) ✔
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
- **Done 2026-11-12.** Pending writes are a deque of (future, row) in frame order; a row is written
  once its PNG write has returned (`_write_done_rows`), and the CSV files are flushed about once a
  second from `write_frame`/`write_imu`/`write_event` (new public `flush()`, no fsync). No `.tmp`
  + rename: a listed PNG is now always complete, and neither the reader nor `recover_stale`
  opens an unlisted one. New test `test_recording_lists_only_written_frames_before_close`
  (frames 5-7 left half written): on the old code all 8 rows were listed (`8 == 5` failed). A
  clean recording is byte-identical: 150 frames with IMU, events, poses and depth written with
  the old and the new module, 159 files each, no difference except `meta.json`'s `created` time.
  `test_core.py`, `test_sim_datasets.py`, `test_app_pipeline.py`, `test_app_live.py`: 82 passed.
  Known and left: a frame whose write failed still gets its row (so `n_frames` counts it; `close()`
  raises, B2); and the last CSV line can be torn by a crash, see B14.

### [x] B4. The recording backlog has no limit and is not shown (Sonnet, M) ✔
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
- **Done 2026-11-12.** `RecordingWriter` defaults to `min(8, os.cpu_count())` workers and waits on
  the oldest PNG write while twice the workers' frames are unwritten (only the `ohs-recorder`
  thread waits; the camera thread only puts into the Recorder's queue). `Recorder.backlog` =
  frames in its queue + the writer's unwritten PNGs; a warning once past 120 frames (~0.5 GB, ~2 s
  at 60 fps). The status message has a new field `record_backlog` (0 when idle); the app does not
  show it yet. New tests: `test_recording_caps_frames_in_flight` (old code: the writing thread
  never waited), `test_recorder_backlog_counts_waiting_frames_and_warns_once` (old: backlog 0
  instead of 30), and `record_backlog` checks in two `test_server_live.py` tests (old: KeyError).
  A clean recording is byte-identical (same 150-frame comparison as B3). `test_core.py`,
  `test_app_live.py`, `test_server_live.py`, `test_server_devices.py`: 71 passed. The Recorder's
  own queue is still unbounded (dropping camera frames is a product decision); `studio.py` reads
  the session's private `_recorder` (a public accessor would be cleaner).

### [x] B5. Rewind may not restore the reconstruction's weights (Opus, S-M) ✔
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
- **Done 2026-11-12, the small version.** Confirmed not a bug in practice: the live session takes
  its parameters from `PipelineProcessor.params()` (`session.py:223` -> `live.py:1144`, `recon="open3d"`),
  and both models a rewind rescales have `scale_weights` (`markerless.py:533`, `volume_gpu.py:829`),
  so no `ConfidenceTsdf` is met and nothing to measure. Added the pin
  `test_the_live_session_uses_a_model_rewind_can_rescale` (passes) and made the failure a warning
  (`test_a_failed_weight_rescaling_is_a_warning` failed on the old code, debug only, and passes
  now). `test_server_rewind.py` 4 passed on both compute paths.
- **Guard:** `test_server_rewind.py`. Add first: a rewind with more keyframes than the budget,
  then assert the reconstruction's weights were scaled.

### [x] B6. Undo/redo writes files while holding the app's main lock (Opus, M) ✔
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
- **Done 2026-11-12.** `_move` checks the 409 rules under `Studio._lock`, takes the scan lock before
  letting it go, and loads/writes the step under the scan lock only; the lock order is unchanged
  and the scan-lock holder never takes `Studio._lock`. A project delete now waits for such a write
  (`ProcessManager.wait_moves`, one line in `delete_project`); without it the delete raced the
  undo, which wrote the folder again. New test `test_undo_writes_the_files_without_the_main_lock`
  (own project; a held `_write_result`): on the old code the main lock was held during the write;
  it also checks a second undo, an edit and a delete sent meanwhile. Messages and 409 rules
  unchanged. `test_server_process_studio.py` 19 passed and five other server test files 53
  passed, on both compute paths. Still waiting under `Studio._lock`: a second request on the same
  scan during its write (as already for edit jobs); `holes()` and a first `scan()` can still
  write into a scan folder without `Studio._lock` (unchanged).

### [x] B7. A GPU failure mid-scan falls back to the CPU silently (Sonnet, M) ✔
- **Where:** `ohs/vision/depth.py:394-400`, `:491-497`, `:535-538`; `ohs/app/pipeline.py:480-486`.
- **Problem:** a runtime failure of the GPU matcher or of the tracking filter logs a warning and
  switches to the CPU for the rest of the scan. The frame rate drops about four times and the
  depth's noise model changes, and the user is told nothing.
- **Change:** record it in the pipeline (stage → reason) and put it into the frame result's
  `extra` on the frame that switches. Showing it in the app is a second, separate task.
- **Guard:** `test_runtime_failure_falls_back_to_the_cpu`. Add first: a pipeline test that makes
  the matcher or the filter raise and checks the flag.
- **Done 2026-11-12.** `FramePipeline.gpu_fallbacks` (stage -> reason, `"depth"` and
  `"track_filter"`), filled from the new `DepthEstimator.gpu_failure` (not set for the quiet switch
  on small images) and from the filter's fallback; the frame that switches carries
  `extra["gpu_fallback"]`. Two new tests in `test_app_pipeline.py` failed on the old code (nothing
  recorded) and pass now; the matcher one is skipped on the CPU path. `test_app_pipeline.py` and
  `test_vision_depth_gpu.py` pass (GPU path 40 passed; CPU path 16 passed, 1 skipped). Showing it
  in the app is not done (separate task).

### [x] B8. The Viewer can leak a mesh when you leave during loading (Sonnet, S) ✔
- **Where:** `ui/src/pages/ViewerPage.tsx:228-239`; compare `ui/src/pages/process/mesh.ts:93-95`.
- **Problem:** the 30 ms parse timer does not check `ctrl.signal.aborted` again. Leave the page in
  that window and the geometry is created after the clean-up ran and is never disposed.
- **Change:** add the check. Optional second step (M): one shared download-and-parse hook for the
  Viewer and the Process page, which are near copies.
- **Guard:** the type check; on the Viewer, `renderer.info.memory.geometries` returns to its
  earlier value after leaving.
- **Done 2026-11-12.** Check added. On a closer look the late geometry never reaches the GPU and
  is freed by the garbage collector, so the renderer count does not show it; the real costs were a
  whole parse after leaving and, on a scan change in that window, the old mesh replacing the new
  download's progress. Checked in the mock app with the 30 ms timer stretched to 1.5 s: leaving
  during it, the callback takes 0 ms (no parse); staying, 44.8 ms and the mesh shows. Type check,
  `npm test` and the build pass. The shared hook (second step) is not done.

### [x] B9. A degenerate face in a PLY shifts the face indices (Sonnet, S-M) ✔
- **Where:** `ui/src/three/ply.ts:214-251`.
- **Problem:** a face with fewer than three corners writes nothing, but while the face map is
  still null the triangle counter falls behind, so a selection can send the wrong face indices to
  delete/keep. Real files with such faces are rare.
- **Change:** build the map when such a face is met. **Guard:** the PLY tests from A3 (do A3
  first).
- **Done 2026-11-12.** `parsePly` (now in `plyParse.ts`) starts the face map at the first face
  with fewer than 3 corners (identity up to there). A3's two "B9 (current, wrong)" checks became
  the right ones (triangle, degenerate, triangle: `faceOfTri` [0, 2] and `filled` [0, 0]; old:
  null and the per-face [0, 1, 0]) and a case with a quad after the degenerate face was added
  (`faceOfTri` [0, 2, 3, 3]; old: [0, 1, 3, 3]); they failed on the old code and pass now. Type
  check, `npm test` (6 files, ply.test 39 checks) and the build pass.

### [x] B10. A failed Pico open leaves the serial port open (Sonnet, S) ✔
- **Where:** `ohs/devices/pico.py:214-219`: `reset_input_buffer()` runs outside the `try` that
  shuts the transport down.
- **Change:** move it inside. **Guard:** `test_devices_pico.py`; add a fake transport whose
  `reset_input_buffer` raises and assert the port was closed.
- **Done 2026-11-12.** The clearing got its own `try` that shuts down the same way (kept before
  the reader starts, so stale bytes are still dropped first). New test
  `test_open_closes_its_port_when_clearing_the_input_fails` failed on the old code (port not
  closed) and passes now; `test_devices_pico.py`, `test_server_devices.py` and
  `test_server_setup.py` pass.

### [x] B11. The two self-tests have drifted apart (Sonnet, S) ✔
- **Where:** `ohs/app/selftest.py:189-199`, `:214` and `ohs/server/selftest.py:306-316`, `:330`;
  also `app/live.py:192` (`_Rate`) against `server/util.py:16` (`RateMeter`).
- **Problem:** the image checks are copies with the same thresholds; the accelerometer check
  already differs (norm of the mean against mean of the norms).
- **Change:** one shared check function; each caller keeps its own hint text. Pick one definition
  for the accelerometer check and say which in the commit.
- **Guard:** the self-test tests in `test_server_setup.py` and `test_app_cli.py`; messages stay
  identical.
- **Done 2026-11-12, both definitions kept.** The two tests are not "app against command line":
  with the real scanner the app runs `ohs.app.selftest.run_selftest` too (norm of the mean); only
  the simulator / replay stream check uses the mean of the norms. Each fits its source (the
  scanner is held still for the hardware test, so a turn should warn; a replayed scan moves), so
  no result changes. Shared now: `image_check` (thresholds and messages, each caller's hints) and
  `gravity_ok` (the 9.3-10.3 m/s^2 range) in `ohs/app/selftest.py`, the server imports them in
  the function as before. New test `test_selftest_image_and_gravity_checks_keep_their_messages`
  pins every message to its old wording (it needs the new functions, so it cannot run on the old
  code). Self-test tests in `test_app_cli.py` 4 passed, `test_server_setup.py` 10 passed. The two
  rate meters (`_Rate`, `RateMeter`) are left as they are.

### [x] B12. A hole with a perimeter of zero reaches the hole list (Sonnet, S) ✔
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
- **Done 2026-11-12.** Confirmed with a sphere missing one triangle whose three corners were moved
  to one spot: `hole_outlines` listed a hole with `perimeter_mm` 0.0. Fixed by leaving such loops
  out of the list (one condition in `hole_outlines`; merging vertices in the clean-up would change
  every mesh). New test `test_a_hole_whose_vertices_coincide_is_not_listed` failed on the old code
  for that reason and passes now; holes with a real perimeter are listed as before.
  `test_mesh_holes.py` 10 passed, `test_server_process_studio.py` 18 passed. Whether this was the
  cause of the one `test_overlays` failure is not known (never reproduced).

### [-] B13. Fill holes also takes a loop with a perimeter of zero as a hole (Sonnet, S) ✔
- **Found 2026-11-12** while doing B12: `fill_holes` (`ohs/mesh/holes.py`, the `cand` list) keeps
  every loop whose perimeter is at most `max_hole_mm`, so also a collapsed one (perimeter 0). Its
  patch would be faces of zero area.
- **Change:** confirm first with the B12 test mesh; if so, leave such loops out of `cand` too.
- **Guard:** `test_mesh_holes.py`; the fill tests in `test_server_process_studio.py`.
- **Not needed (checked 2026-11-12, rule 2).** On the B12 test mesh `fill_holes(V, F, 10)` adds
  one face of area 0, and that face is what closes the mesh: `mesh_stats` goes from watertight
  False, 1 hole, to watertight True, 0 holes. Leaving the loop out would leave the mesh open,
  which is worse for an STL. The stats count such a loop as a hole while the outline list (B12)
  does not show it; Fill holes then closes it, so the two agree after a fill.

### [x] B14. A crash can leave a torn last line in a recording's CSV files (Sonnet, S) ✔
- **Found 2026-11-12** while doing B3: Python flushes a file's buffer when it fills (8 KB), which
  can be in the middle of a line, so after a crash the last line of `frames.csv` (or `imu.csv`)
  can be cut short. `RecordingReader.__init__` does not skip such a row (it can raise, or keep a
  row without a file name), and `_count_rows` in `server/projects.py` (`recover_stale`) counts it.
- **Change:** confirm first with a cut `frames.csv`; then let the reader and `_count_rows` skip an
  incomplete last row (only the last one).
- **Guard:** `test_core.py` recording tests; `recover_stale` tests; a new test with a cut file.
- **Done 2026-11-12.** Every writer ends its rows with a line end (checked: `RecordingWriter`,
  `server/calibration.py`, the one test that appends rows), so a last line without one is the
  torn one. `_complete_lines` in `recording.py` drops it for `frames.csv`, `imu.csv` and
  `events.csv`; `_count_rows` counts only lines with a line end. New test
  `test_recording_skips_a_torn_last_line` (each file cut 8 bytes into its last row) failed on the
  old code (4 frames listed instead of 3) and passes now. `test_core.py`, `test_sim_datasets.py`,
  `test_server_process_cuts.py`, `test_server_basic.py`: 76 passed. The ground-truth poses file
  (simulator only) is read as before.

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

### [x] E4. Let the TSDF integration run without blocking (Opus, M) ✔
- **Done 2026-11-11:** `integrate` no longer waits for the device after queueing the fusion. On
  the Radeon the pass takes 136.5 to 138.7 ms per frame where it took 140.9 to 142.8 (old and
  new alternated three times, 150 frames, default settings): about 4 ms, 3 %. Same mesh, and the
  A2 snapshot is identical. The two brick lists are kept in `_lists` until the next frame;
  `download`, `reset`, `close` and every buffer release wait first (`_wait`).
- **What the wait was:** not this kernel. Waiting only for the two small list uploads still took
  5.4 ms and gained nothing (140.0 to 141.5 ms per frame): the device was busy with the tracking
  volume's fusion, queued just before on the same GPU and not waited for. The depth and sigma
  uploads are still waited for (1.5 ms, now without holding the GIL), so the caller's arrays are
  its own again when `integrate` returns. Not measured: the RTX laptop.
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
- **From the F7 profile (2026-11-11, Radeon, per frame):** the pass also stands still while the
  device works: the fusion's `finish` 5.4 ms, the tracking filter's blocking copy back 4.6 ms,
  the reconstruction's uploads 1.75 ms, the tracking volume's depth upload 1.7 ms. Two ways to
  use that time with the same results: leave the fusion unfinished until its result is needed,
  so it runs beside the laser points (3.9 ms on the host) and the next frame's rectification;
  and start the tracking filter without waiting and do the laser points meanwhile. To be
  measured before building. The first of the two is done (E4, about 4 ms).
- **Part done 2026-11-11 (laser points beside the tracking):** `FramePipeline.process` hands
  `laser_points` to a laser thread as soon as the depth and the lines are there and takes the
  result after tracking and fusion; the rejected counts and the cloud are still updated on the
  main thread, in the same order. 137.0 to 137.4 ms per frame -> 133.4 to 134.3 ms (about
  3.3 ms, 2.4 %), same mesh, snapshot identical. `timings_ms["laser_triangulate"]` is now the
  wait that is left, `laser_triangulate_cpu` the work's own time. Guard, passing on the old code
  too: `test_laser_points_equal_the_inline_computation`.
- **Part done 2026-11-11 (the tracking filter reads the matcher's depth on the device and
  starts early):** `GpuStereoMatcher.device_depth` / `DepthEstimator.device_depth` give the
  buffer that holds the depth map just returned; `TrackingFilterGpu` has `start` (a map or
  such a buffer, no wait) and `finish`; `FramePipeline.process` starts the filter as soon as
  the depth is there and `tracking_input` collects it. No upload for the filter (4 MB), and
  the device filters during the gyro prior. 134.1 to 135.6 ms per frame -> 131.2 to 132.3 ms
  (about 3.2 ms, 2.4 %), same mesh, snapshot identical. Guards:
  `test_start_and_finish_give_the_same_map`, and
  `test_the_pipeline_tracks_on_the_filter_of_its_depth` (passes on the old code too).
  Still open: the reconstruction and the tracking volume still upload their maps (about
  1.5 and 1.1 ms). Since E4 the reconstruction's fusion is still running when the next frame's
  matcher writes its depth buffer, so sharing that buffer needs events between the queues.
- **Left for later (owner, 2026-11-11: move on, and note what could still be better):** three
  things, none started, together at most about 3 to 4 ms of the 132 ms per frame on the Radeon.
  (1) The reconstruction reads the depth and its sigma from the matcher's buffers in place of
  two uploads (about 1.5 ms). (2) The tracking volume does the same for its depth (about
  1.1 ms). Both need the matcher's next run to wait for the event of the fusion that still
  reads the buffer, or a second pair of buffers that the matcher swaps each frame. (3) The
  tracking filter is queued behind the matcher's last kernel, so it runs while the depth and
  the sigma are downloaded; worth it only if a measurement shows wait left in
  `timings_ms["track_filter"]`. Same rules as above: results identical, measured old against
  new, and not built if the gain is inside the noise.
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

### [x] P2. Remove floaters searches for an index it already has (Sonnet, S) ✔
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
- **Done 2026-11-13:** `remove_floaters(..., return_kept=True)` also returns the faces it kept;
  `carry_sigma` and `carry_filled` follow the mesh by them, as they do for deleted faces. On two
  real scans (857 k faces, 3,700 removed) the carry-over took 730 ms before and 25 ms after;
  the edit itself 220 ms. Vertices, faces and sigma are identical, except 2 vertices that share
  a place with another (6e-8 mm apart in sigma) and 5 faces whose filled flag the search took
  from a twin face; the index is the correct one.
- **Found on the way (not fixed):** Fill holes can add a triangle back to back with a measured
  one (the same three vertices, reversed): 4 such pairs on one real scan, 1 on another. The old
  search then marked the measured twin as filled too.

### [x] P3. Mesh refinement peaks at 350 MB per chunk (Sonnet, S) ✔
- **Done 2026-11-10:** `project_onto_patches` takes `chunk` (default 25,000 vertices, was a fixed
  200,000). Peak memory with 200,000 vertices and 10,000 patches: 606 MB before, 145 MB after;
  time unchanged at 2.6 s (measured by the builder, not repeated in review).
- **Where:** `ohs/track/spacetime.py:883-905` (`project_onto_patches`).
- **Problem:** chunks of 200,000 vertices with 12 neighbours of 18 float64 values each.
- **Change:** chunks of about 25,000; rows are independent, so the output is bit-identical.
- **Guard:** `test_vertices_move_onto_the_patches_and_nowhere_else`,
  `test_the_refined_mesh_is_closer_to_the_truth`. Add first: two chunk sizes give equal output.

### [x] P4. The laser cloud is averaged twice per processing run (Sonnet, S) ✔
- **Where:** `ohs/app/process.py:390-398`, `ohs/track/fusion.py:85-107`.
- **Problem:** `save_ply` computes the voxel-averaged points, the report computes them again, and
  the raw count concatenates every chunk just to take its length.
- **Change:** compute the points once, write them, count raw points from the chunk lengths.
- **Guard:** `test_laser_cloud_accumulates_in_world_frame`,
  `test_process_recording_fuses_at_given_poses`; `report["lasers"]` and the bytes of
  `laser_points.ply` unchanged.
- **Done 2026-11-12.** `process_recording` averages once, writes and counts that; raw counts come
  from the chunk lengths (`LaserCloud.raw_counts`). On a cloud as large as the largest real scan
  (602,000 raw points, the door at Best has 605,000): 220 -> 110 ms, three runs each, same file
  bytes. A 16-frame simulated recording processed with the old and the new code: the same
  `report["lasers"]` and a byte-identical `laser_points.ply`. `test_track_export.py`,
  `test_app_pipeline.py`, `test_server_process_studio.py`: 45 passed (GPU), 44 + 1 skipped (CPU).

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

### [-] P7. Straighten can fuse the whole scan twice (Opus, L) ✔
- **Where:** `ohs/server/processing.py:1277-1315`.
- **Problem:** with loops it fuses once as the reference for the laser crossings, and again if
  those change the poses; the first mesh is thrown away.
- **Change:** investigate a half-resolution reference fuse. This changes a quality trade-off:
  measure the mesh error against the simulator's truth before and after, and stop if it is worse.
- **Guard:** `test_straighten_closes_loops_and_fuses_again`; add the error benchmark first.
- **Measured 2026-11-12, not adopted.** The second fuse runs only when the loops changed the poses
  and then the laser crossings change them too. It never happened with real tracking: 0 of 11
  runs (6 simulated orbits, 5 studio recordings; 4 fused once). It happened only with synthetic
  drift (13 of 22 runs). When it does, the thrown-away fuse is 18-27 % of Straighten and 12-14 %
  of Make mesh (GPU, 3 runs each, the GPU path repeats exactly). A coarse reference fuse (preview
  quality, or twice the voxel) saved up to 4.9 s at 1280x800 but made the mesh measurably worse in
  several cases (bracket +7 % object and +16 % floor error, sphere 640 +10 %), and it adds a fuse
  to the one looped case real tracking produced (Straighten 26-41 % slower). Untested idea if this
  wait ever shows on real scans: keep each frame's depth from the first fuse and integrate it again
  at the new poses (no repeated stereo, same result). Scripts and results:
  scratchpad `p7\` of the session of 2026-11-12.

---

## U. Desktop app UI

Every task here: the type check, `npm test`, and for bundle tasks the chunk sizes before and after.

### [x] U1. The status message re-renders the 3D scene 10 times a second (Sonnet, S-M) ✔
- **Done 2026-11-11.** The store keeps the old `session` object when the new one is equal
  (`store/sameSession.ts`, with a node test in `npm test`); the Scan page reads only the phase
  and the Project page only "scanning here"; `LiveCanvas` is memoised. Renders counted in
  `npm run dev:mock` over 10 s (development mode, which renders twice): Scan page, `LiveCanvas`
  and its scene 152 -> 0, idle and while scanning; Project page 150 -> 0 idle, and 8 while a scan
  runs in that project (its own refresh of the scan list). The Viewer and Process canvases are
  left as they are: their pages do not read the session, so they were never part of this.
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

### [x] U2. Measurement lines rebuild their geometry on every render (Sonnet, S) ✔
- **Where:** `LiveCanvas.tsx:293`; `pages/viewer/ViewerCanvas.tsx:294`, `:315`. Same session as U1.
- **Problem:** drei's `<Line>` rebuilds when `points` is a new array, and these are written
  inline.
- **Change:** a module constant for the fixed line; a memoised component per measurement.
- **Done 2026-11-11.** Counted in `npm run dev:mock` (development mode renders twice): with two
  measurements on the model, 100 moves of the cursor rebuilt the finished lines 400 times, now 0.
  Adding a third measurement renders only the new one. The distances shown are the same. The
  dashed line from the first point to the cursor (`:315`) still follows the cursor, as it must.
  The aim line in the live view rendered rarely already (only when the distance zone changes), so
  its constant is for tidiness.

### [x] U3. Every mesh download is held twice in memory (Sonnet, S) ✔
- **Where:** `ui/src/api/http.ts`, `fetchBinary`.
- **Problem:** the chunks are collected and then copied into one array.
- **Change:** allocate once when `content-length` is known; fall back to chunks otherwise.
- **Guard:** the `fetchBinary` test from A3.
- **Done 2026-11-12:** with the size known the chunks go straight into one buffer. A 256 MB
  download peaked at 512 MB of buffers before and 333 MB after (Node, 64 KB chunks; the rest is
  chunks not yet collected). A size that is wrong either way still gives every byte (3 new checks).

### [x] U4. Switching the shading rebuilds the lighting environment (Sonnet, S) ○
- **Where:** `ui/src/three/Studio.tsx:15-31`.
- **Change:** build the environment once per renderer; set the intensity in a second effect.
- **Done 2026-11-12:** two effects. Counted in the viewer on a 650 k-triangle scan: a switch
  between Clay and Studio created 16 textures and 87 framebuffers before (2 and 42 on the way
  back) and none after; opening the viewer still builds the environment once (14 and 45).

### [-] U5. The Scan page downloads a library only the Viewer uses (Sonnet, S) ○
- **Where:** `ui/vite.config.ts:45` puts `three-mesh-bvh` in the shared `r3f` chunk.
- **Gain (estimate):** about 40 KB off the Scan and Process pages. **Guard:** chunk sizes.
- **Tried 2026-11-12, not adopted:** taking `three-mesh-bvh` out of the `r3f` group changes
  nothing (the chunk is byte for byte the same: drei's `Bvh` module stays in the group and brings
  the library with it). A group of its own for the library and drei's `Bvh` module grew to
  216 kB (it takes their dependencies along) and the Scan page still loaded it. The Process page
  uses `Bvh` too now, so only the Scan page could gain, about 40 KB of a 1.3 MB first 3D load
  from localhost. Not worth a bundler work-around.

### [x] U6. Unused exports and a duplicated normals helper (Sonnet, S) ○
- **Where:** never referenced: `realUrl` (`api/backend.ts`), `getSocketFactory` (`api/socket.ts`),
  `QUALITY_TEXT` (`components/ScanBits.tsx`), `fmtMm`, `fmtDate` (`lib/format.ts`), `usePrevious`
  (`lib/util.ts`), `threeToCv`, `sphereToThree` (`three/coords.ts`). `LiveCanvas.tsx:106-141`
  duplicates the normals code of `three/ply.ts`.
- **Change:** search for each name again, then delete. **Guard:** the bundle is the same size or
  smaller.
- **Done 2026-11-12:** seven of the eight names deleted (`QUALITY_TEXT` is used now, from
  `lib/quality.ts`). The normals code is one function in `three/normals.ts`, its own module so
  the Scan page still does not load the PLY reader; on random data it gives the live mesh the
  same values as the old copy (0 of 24 000 differ). All JS together is 256 bytes smaller; the
  Scan page loads 263 bytes more (the 446-byte `normals` chunk).

### [-] U7. Mesh parsing freezes the UI on large meshes (Sonnet M, then Opus L) -
- **Where:** `pages/process/mesh.ts:93-109`, `ViewerPage.tsx:228`, `three/ply.ts:188-249`.
- **Change:** (a) Sonnet: a fast path for the usual face layout (one count byte, 32-bit indices).
  (b) Opus: parse in a Web Worker and transfer the arrays.
- **Guard:** the PLY tests from A3 give the same arrays; the timing log in `mesh.ts:98`.
- **Not needed (measured 2026-11-11, rule 2).** The parse is not where the time goes. In Chrome on
  this PC, medians of 5 runs of `readPly` and `plyGeometry` on real engine meshes:

  | Mesh | Read | Geometry |
  |---|---|---|
  | a usual scan, 24 MB, 852,847 triangles | 65 ms | 12 ms |
  | the door at Best, 99 MB, 3,627,310 triangles | 84 ms | 8 ms |

  The slowest single run was 266 ms (the first one). In Node the 99 MB mesh reads in 159 ms, and a
  144 MB Open3D file with 64-bit coordinates and 5.76 million triangles in 179 ms plus 157 ms for
  its normals. A fast path could halve a tenth of a second, and a worker is not worth its code for
  that. The freeze on large meshes is somewhere else: see U8.
- For A3: `parsePly` imports nothing, so moving it and its types to a file of its own makes it
  testable with plain `node`, like the other UI tests.

### [x] U8. The first use of Measure or Confidence freezes the viewer for seconds (Sonnet, S) ✔
- **Where:** `pages/viewer/ViewerCanvas.tsx`, `<Bvh firstHitOnly>`. Found while measuring U7.
- **Problem:** the tree for picking points on the mesh is built on the first use of Measure or of
  the confidence view, on the main thread, with drei's default split (SAH). Measured in Chrome on
  this PC with the options drei uses: 1.7 s for a usual scan (852,847 triangles), 7.2 s for
  3,627,310 triangles. Nothing in the app moves meanwhile.
- **Change:** (a) the CENTER split: 316 ms and 1.6 s on the same meshes. (b) Later, if the rest
  matters: build the tree in a worker (three-mesh-bvh has one), so nothing freezes at all.
- **Guard:** the same hits: 3000 rays through each mesh give the same distances with both splits,
  to the last digit, and the time per ray stays the same (about 4 µs).
- **Done 2026-11-11 (a).** 1724 → 316 ms and 7173 → 1587 ms on the two meshes. In the app
  (`npm run dev:mock`, 235,674 triangles, development mode) the long task after a click on Measure
  went from 765 ms to 119 ms. The confidence view could not be tried in the mock (no confidence
  values there). Left for later: (b), the worker, for the 0.3 to 1.6 s that remain.

---

## S. Start-up and server

### [x] S1. `ohs --help` loads OpenCV (Sonnet, S) ✔
- **Where:** `ohs/app/config.py:39` imports `CameraParams` from `ohs/devices/camera.py`, which
  imports `cv2`. Measured: `import ohs.app.cli` takes 540-600 ms, 300-480 ms of it OpenCV.
- **Change:** move `CameraParams` to a module without `cv2` and re-export it from `camera.py`.
- **Guard:** `test_app_cli.py`, `test_app_config.py`, `test_devices_camera.py`. Add first: a
  subprocess test that `cv2` is not in `sys.modules` after `import ohs.app.cli`.
- **Done 2026-11-12:** `CameraParams` and `PicoParams` live in `ohs/devices/params.py` and are
  re-exported. `PicoParams` moved too: `pico.py` imports numpy, which is most of the time (numpy
  about 300 ms, OpenCV itself about 45 ms). `import ohs.app.cli` took 578 ms before and
  69-214 ms after (`-X importtime`, on a machine busy with a study). The new test checks that
  neither `cv2` nor `numpy` is loaded.

### [ ] S2. New app connections read files on the event loop (Sonnet, S) ✔
- **Where:** `ohs/server/app.py:588`, `:610` call `seed_client` (`studio.py:230-238`), which reads
  the calibration and setup files, on the event loop; every client's sender pauses meanwhile.
- **Change:** `await asyncio.to_thread(...)`, keeping the order of messages.
- **Guard:** `test_websocket_hello_device_session_and_status`,
  `test_websocket_calibration_messages`, `test_sim_device_previews_and_status`.

---

## F. Found on the way

### [x] F1. The Fit tool accepts a cylinder on a flat, noisy patch of a very short scan
- **Found 2026-11-13**, when the gap guard was turned on: `test_fit_a_plane_on_the_table`
  (`tests/test_server_process_studio.py`) asked for a cylinder at a click on the table of its
  10-frame, 640 px scan and expected a refusal. On the CPU path that answer depends on the noise.
- **Measured** (the same recording through `process_recording` at Preview quality, CPU path, three
  runs; the CPU path's meshes differ a little from run to run): without the guard the click was
  accepted as a cylinder of radius 22.6 m on 77,000 points in two runs of three (a plane), and
  refused in the third ("its curvature is not clear"). With the guard, it was accepted on the far
  rim of the scan, which curls there: radius 60–62 mm, RMS 0.5 mm, a strip 700 x 44 mm.
- **Where:** `ohs/measure/region.py` (`fit_at`, the seed's roundness test, `SEED_ROUNDNESS`) and
  `ohs/measure/fit.py` (`MIN_ROUNDNESS`).
- **To do:** a cylinder whose radius is many times the size of its region is a plane and should be
  refused; and look at whether a curled rim should pass. Tests on controlled meshes first
  (`tests/test_measure_region.py`).
- **Done 2026-11-13:** in both cases the region had drifted away from its seed. The seed saw a
  radius of 247 mm in a bump of the noisy table and the region then ran over the whole table
  (22.8 m, 1° of arc); on the rim the seed saw 26 mm and the region 52–54 mm, because the curl
  tightens towards the edge. `fit_at` now refuses a cylinder whose final radius is more than
  `SEED_DRIFT` (1.5) times its seed's, or less than the seed's divided by it, and the message gives
  both radii. Measured before choosing 1.5: on 90 real cylinders of the simulated scans (the
  bracket's bosses and shaft, 5 seeds, true and tracked poses) the final radius is 0.85 to 1.08
  times the seed's; the wrong fits were 2 and 92 times. After the change the 8 saved meshes of
  the test scan (CPU path three runs and GPU path one, guard off and on) are all refused, and the
  90 real cylinders all still fit. Tests first, on two controlled meshes that the old code
  accepted (a bumped, bowed, noisy plate: 4.9 m; a rim that curls ever tighter: 25 → 50 mm). The
  server test asks for the cylinder again, and for a hole.
- **Not covered:** a rim that curls with one radius is a cylinder to the Fit tool, as a rolled
  edge is. Nothing in its shape tells the two apart.

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
