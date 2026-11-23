# Engine plan: where the scanning engine falls short, and the experiments to mend it

Written 2026-11-20 from a review of every measurement in `VALIDATION.md` and of both plans'
"Left" notes (an Opus agent read; nothing was run for it). It ranks where the engine is furthest
from what a user needs and lists the experiments most likely to help, each with a stop rule
written before its result is known.

Everything here is measured on the simulator: no shine, no motion blur, no MJPEG, ideal lasers
and IMU. Every gain below is an upper bound until the real scanner is measured.

Section names are `VALIDATION.md`'s unless marked MP (`MAINTENANCE_PLAN.md`) or FP
(`FEATURE_PLAN.md`).

## Rules

The rules of `FEATURE_PLAN.md` and `MAINTENANCE_PLAN.md` hold. In short:

- One task, one commit. Guard test first, seen failing. Never loosen a test. Both compute paths.
- **A change to what a scan produces is adopted when it is better** (the owner, 2026-11-20,
  E-D1): it meets its stop rule, it is better on every test scene of the scorecard or the same
  within that scene's spread, nothing thin gets worse, and the report says what was switched
  on, with the numbers. It stays a setting that can be turned off. A change that misses its
  stop rule on any scene is recorded as measured and stays off; a near miss goes to the owner
  with the numbers, as F14's did.
- **A change to what a mesh edit produces** is still built as an option that is off
  (`FEATURE_PLAN.md`, the standing rule; `MAINTENANCE_PLAN.md` F15 is the example).
- Thin parts matter: every rule with a size in mm is tested on a thin plate and its limit
  stated; nothing thin is removed or thickened silently.
- A stop rule is written before the experiment. A result that misses it is recorded as
  measured and not adopted; it is not tuned until it passes.
- **How experiments are judged.** A change to fusing alone is judged by a paired replay: each
  recording's matcher depth is cached and fused with and without the change at the same true
  and tracked poses, and the yardstick is each recording's own difference (all with the same
  sign), with today's range across seeds beside it. A change to tracking is judged across
  seeds (the GPU path repeats bit for bit). Timings: three alternating rounds; on the Radeon
  PC only after AMD Software has been restarted (MP F8).

## Decisions

| # | Question | Suggestion |
|---|---|---|
| E-D1 | An engine change that alters GPU-path scan results and meets its stop rule: switch it on, or keep it off until the owner has looked? Asked 2026-11-20. | The rule the owner gave for the CPU path: adopt when better on every test scene, with a report of what was switched on. |
| E-D2 | Straighten's laser-crossing adjustment inside Make mesh: keep it as it is, use the test that holds out whole frames, or switch it off? Asked 2026-11-21. Neither change is better on every scene (X0a), so the rule does not decide. | The frames test. Today's test passes at true poses and then moves them, so what it does on thin walls is chance: one wall recording 0.3 to 0.5 mm thicker, another pulled past the truth. The frames test never acts on a wall, still mends real drift, and what it costs is known: one wall recording stays as thick as the frame pass reads it (the 2 mm wall +0.54 mm instead of -0.05), and one of two speckled brackets keeps a path 0.3 mm worse. |

**Decided by the owner (2026-11-20):**
- **E-D1:** adopt a change when it is better (see the rules above).

**Decided by the owner (2026-11-21):**
- **E-D2:** the test that holds out whole frames ("Stricter test"). Straighten's
  laser-crossing adjustment acts only when the crossings between frames the fit did not see
  meet clearly better. To do, as one change: `CurveParams.holdout` becomes `"frames"` by
  default (the old test stays a setting); the app chain's baseline is made again with it
  (three recordings move: the standing walls seeds 0 and 2 and one speckled bracket), saved
  under a new name; the tests that pin the old default are updated; the report of what was
  switched is written here and in `VALIDATION.md`. It waits until X4's runs are finished, so
  that X4 is judged against one baseline and not against a default that changed under it.

  **Switched 2026-11-21.** `CurveParams.holdout` is `"frames"` by default; `"crossings"` stays
  a setting. What moved, on what Make mesh runs, against the baseline before the switch: three
  recordings of 36 and nothing else.
  - Standing walls seed 0: the walls 0.5 / 1 / 2 mm read +1.543 / +1.236 / +1.124 before and
    +1.229 / +0.808 / +0.660 now (0.31 to 0.46 mm nearer the truth); the 0.5 mm wall's face
    0.27 → 0.48 covered; median / p95 0.144 / 0.973 → 0.125 / 0.720.
  - Standing walls seed 2: +0.891 / +0.431 / -0.052 → +1.257 / +0.911 / +0.535, the thickness
    the frame pass reads; its 2 mm wall is no longer pulled past the truth (it read 1.95 mm);
    the 0.5 mm face 0.65 → 0.45 covered.
  - Speckled bracket 1: the adjustment is now refused there; path median 0.73 → 1.02 mm.
  - The other 69 of 72 runs are identical in every scored number, the second speckled bracket
    among them, where the adjustment still acts. It acts on one recording of 36 now, on four
    before.
  - Without a GPU neither test acts on the recordings checked (a wall recording, a bracket, the
    sphere), so nothing changes there.
  - The sentence shown when it acts no longer says "up to 0.6 mm in tests": that bound was
    measured with the old test.
  - The baseline to judge against from now on: `tools/scorecard_baselines/2026-11-21-gpu-core-app-frames.json`.

## The error budget today

GPU path, Standard quality, 1 mm voxels, tracked unless said.

| Part | Result today | Depth | Tracking | Fusing (band, voxel, guard) | Edits and fits |
|---|---|---|---|---|---|
| Smooth panel (car panel, sphere) | Median 0.083, p95 0.39 mm (36-frame panel; sphere 0.061 / 0.257) ["Thin walls seen from opposite sides"]. 60-frame panels 0.026 to 0.037 / 0.16 to 0.20 ["Presets"]. | GPU matcher median 0.32, p95 1.41 mm, bias -0.002 mm ["GPU processing"]. | 0.11 / 0.26 mm (median / max, 36 frames). Tracking doubles the mesh's median: 0.037 at true poses, 0.075 tracked ["Deviation map"]. | Every second frame: +18 to 22 % median. 1.5 to 2 mm voxels: p95 beyond the spread. | Taubin: median -10 %. Refine with lasers changes nothing unbiased. Flat-within reads 0.14 to 0.48 mm on truly flat faces (stale, 2026-11-12). Flush: mean within 0.04 mm. A gap: not measurable. |
| Bracket (edges, Ø14 bosses, Ø16 shaft) | Median about 0.14 mm; 11.6 to 31.3 % more than 0.3 mm off (median 12.6 %); mean offset +0.008 mm; covered 94.5 to 95.6 %. | True depth and true poses: 6.5 % over 0.3 mm. Matcher depth and true poses: about 10 %. So the depth is about 8 of the 12.6 points ["Where a tracked scan's shape errors come from"]. | Adds 2 to 3 points in the median and makes the bad seed (31 %). Best quality is worse than Standard here (0.100 against 0.089 mm). | The gap guard costs 0.7 to 2.0 points of coverage and removes bias. | Bosses read Ø 0.3 to 0.5 mm small on every seed; with true depth they are right (-0.02): the depth and the guard. Shaft right with tracking only because two errors cancel. Upright face at worst +0.33 mm. |
| Thin sheet lying flat | A 0.5 mm plate reads 0.489 to 0.504 mm with every setting ["Presets"]. | not the limit | GPU path: 1.1 to 3.8 mm at the last frame (MP F14). | Remove table keeps 0.3 mm and up; 0.1 mm goes silently ["A thin part lying flat"]. | A merge with the plate turned over reads 1.0 mm as 1.42 and 0.5 mm as 1.28. |
| Thin sheet standing, both sides seen | 2 mm flange 2.26 to 2.61, 1 mm rib 1.66 to 2.20 mm. Walls 0.5 / 1 / 2 mm read 1.70 / 1.71 to 1.90 / 2.53 to 2.66 at 1 mm voxels and 1.03 to 1.08 / 1.32 to 1.45 / 2.18 to 2.43 at 0.5 mm. | small | +0.3 to 0.6 mm (flange), +0.7 to 1.2 mm (1 mm rib) over true poses: 0.77 times how far out the cameras stand, r = 0.98. | Mesh's floor at true poses: 2.10 to 2.18 / 1.24 to 1.28 mm. | |
| Holes | Ø25 over a far dark background: 1.1 mm small tracked, 1.3 at true poses. A plate 120 mm up: 22.0 to 22.1 tracked. Near a floor: Ø25 +0.15 to 0.5, Ø10 -0.3 to +0.6. Ø6 everywhere and Ø10 over a far floor: closed, refused. | All the fill is the matcher's: 1.3 mm median reach ["Holes filled in"]. | On the plate tracking takes another 1.7 to 3.1 mm. | The rim fuses as a shoulder: the reading goes 26.7 to 24.3 mm between 0.15 and 2 mm below the face. Coarser voxels close Ø25 by 1.4 to 2.4 mm. | The ± does not cover the bias. A gap of 2 to 6 mm is bridged, not offered. |
| Large part (door, 946 mm) | Stale (2026-11-10): median 0.07, p95 0.79 mm. Today's 141-frame stretches: 0.039 to 0.052 / 0.35 to 0.43. | as the panel | Loops do not mend sliding (up to 98 mm) ["Straighten for large drift"]. Overall length over a metre: not measured. | The 768 mm tracking cube has never been shown to bind. | not measured |
| Detailed casting (carburettor) | Stale (2026-11-10): mesh median 0.80, p95 2.7 mm. Today's stretches: 0.157 to 0.230 / 0.69 to 1.04, coverage 0.68 to 0.70. | Detail mode tracks raw depth, so the camera-fixed dot error comes back. | The largest of any scene. | 0.5 mm voxels: p95 -0.11 to -0.38. | not measured |
| Dark or speckled surface | GPU path, last frame: 1.1 to 3.8 mm, against 0.61 mm on the plain bracket (MP F14). The mesh against the truth: not measured. | Dots about a fifth as bright. | ICP from the true pose against a one-frame model drifts 2.6 to 2.7 mm. | not measured | CPU path after D7: 42 mm and 0.9 mm (two seeds), plates 5.7 mm. |

## Where it falls shortest

Ranked by the gap between what a user needs and what the engine delivers.

1. **Mounting holes.** Needed: diameter and position within 0.1 to 0.2 mm. Today: 1.1 to 3.1 mm
   small over open space; +0.2 to 0.5 mm near a floor; Ø6 to 10 over open space not measurable;
   gaps between panels not measurable. Short by 5 to 15 times.
2. **Wall thickness with both sides seen.** Needed: 0.1 to 0.2 mm. Today +0.3 to 1.2 mm
   tracked; a 0.5 mm wall reads 1.7 mm. Short by 3 to 10 times. Lying flat is fine.
3. **Tracking on dark, plain and thin parts (GPU path).** Needed: under about 0.3 mm
   everywhere, because a whole region shifted is invisible to the sigma. Today 1.1 to 3.8 mm,
   and one bracket seed in five reads 2.5 times worse.
4. **Small round features.** Needed: Ø within 0.05 to 0.1 mm. The Ø14 bosses read 0.3 to
   0.5 mm small on every scan, outside any ±.
5. **Edges and detailed parts.** 10 to 13 % of the bracket is more than 0.3 mm off; the
   carburettor's p95 is 0.7 to 1.0 mm at 70 % coverage.
6. **Flatness of a flange.** Needed: 0.05 to 0.1 mm. The label reads 0.14 to 0.48 mm on truly
   flat faces (stale).
7. **Overall size of large parts.** Needed: about 0.05 %. Not measured.
8. **A merge** adds coverage; it adds accuracy only where the pose is right and the scans
   saw different sides (X7, measured 2026-11-22).
9. **Waits.** After a scan, Standard takes about 4.8 times the scan's length on the Radeon PC
   (target 3) and 1.5 times on the RTX laptop (target 1). Live with four lasers: about 14
   frames a second at 640x400 on the Radeon PC.
10. **Without a GPU:** no thin-wall rule, about 0.3 mm too fat, no sigma (so no Check and no
    guidance), one dark seed still 42 mm off.

## The experiments

The first five are the ones to run first, after X0.

### [x] X0. The scorecard (M; done 2026-11-20)
One command that runs a fixed set of recordings and prints one table, with a difference against
a saved baseline and a verdict for each row against its yardstick. Every experiment below is
judged with it.
- **Scenes:** sphere and car panel end to end; car panel 200 frames, seeds 0 to 4 (the hole);
  bracket 240 frames, seeds 0 to 4; the sheet part, the standing walls and the 0.5 mm plate,
  seeds 0 to 2; plates with holes 10 and 120 mm above their background; the six dark and
  speckled recordings; one door and one carburettor stretch; the three panel-seam recordings.
- **Columns:** camera path (median, max, last frame); the part alone, best fit (median, p95,
  share over 0.3 mm, signed mean); coverage; fits (hole Ø and centre, boss and shaft, upright
  face, wall and plate thickness, flush); laser against mesh for each line; open loops; frame
  time, wall time, peak memory; the range across seeds; a paired-replay mode for changes to
  fusing alone; the CPU path.

**Done (2026-11-20): `tools/scorecard.py`**, run from `software/`. It renders a fixed set of
simulated recordings once into a cache, processes each as the app's Make mesh (Standard, default
settings, markerless) at tracked and at true poses, applies the Default recipe and scores
against the simulator's truth. Numbers and tables: `VALIDATION.md`, "The scorecard's baseline".

- **Commands:** `run`, `baseline --save NAME`, `compare NAME`, `table`, `render`;
  `replay --set-param fusion.X=V` (a paired replay of a change to fusing alone, on the cached
  matcher depth); `repeat` (the same recording twice: determinism); `selfcheck` (the placement on
  the truth itself); `holes` (the Ø25 hole read every way); `rescore` (a change to the scoring
  alone). Filters: `--set quick|core|full`, `--scenes`, `--seeds`, `--compute gpu|cpu`,
  `--set-param SECTION.FIELD=V`, `--params FILE`, `--quiet-pc` (judge timings too),
  `--render-lanes N`.
- **Scene sets:** `quick` is the end-to-end sphere and the 0.5 mm plate, seed 0. `core`
  (36 recordings, what an experiment is judged on) adds the end-to-end car panel, the car panel
  at 200 frames (seeds 0 to 4), the bracket at 240 frames (seeds 0 to 4), the sheet part, the
  standing walls and the 0.5 mm plate (seeds 0 to 2), the hole plates 10 and 120 mm above their
  background (seeds 0 to 2), the six dark and speckled recordings and the three seams. `full`
  adds the 60-frame panels and three stretches each of the door and the carburettor.
- **The part alone:** the scan is placed by a best fit of the part's own points onto the part's
  own surface, never the table's. The bracket's spread at true poses is 0.004 mm this way
  (0.52 mm with the whole scene). Points past a sheet's free edge do not place it, and a
  direction the part's shape does not fix (a flat or gently curved sheet, along itself) stays
  where the first frame put it.
- **The verdict:** each recording is paired with itself in the baseline; a scene's spread is its
  range over seeds in the baseline. A row is better or worse when the mean change is beyond the
  spread and every seed moved that way, and also worse when one recording alone is worse by more
  than the spread. Thin parts and small features (plate and wall thickness, the holes, bosses,
  the shaft) are shown first and judged for each recording: 0.05 mm thicker or further off, a
  face lost or a click that no longer answers is an alarm and makes its scene worse. A scene
  with one recording says "no spread to judge by". The last line is "Better or the same on
  every scene", or the scenes that are worse.
- **Restartable:** a recording counts only when complete (a marker written last, every planned
  frame there and whole), results are written whole, and the same command carries on after a
  kill. A baseline is saved only when every job finished.
- **The baselines of 2026-11-20** are in `tools/scorecard_baselines/` (`compare` takes a file):
  `2026-11-20-gpu-core.json` and `2026-11-20-cpu-quick.json`. The cache with the recordings
  (12.7 GB) is not in the repository; rendering it again takes about 3 hours on this PC:
  `python tools/scorecard.py baseline --set core --save NAME --render-lanes 3 --cache DIR`.
- **Today's spread** (GPU, tracked): bracket median 0.153 mm (0.075 to 0.228; at true poses
  0.004 mm); car panel median 0.001 mm and its Ø25 hole 0.40 mm; sheet flange 0.35 mm; the
  walls 0.07 / 0.21 / 0.12 mm; plate thickness 0.010 mm. Both paths repeat exactly on the quick
  set (the same recording processed twice gives the same numbers).
- **The Ø25 hole's two signs, settled:** the sign follows the recording, not the click, the
  placement or the tool. A 60-frame pan from one side leaves the rim wide (+1.07 / +0.54 / -0.23
  against 25.0, as in "Presets"); a 200-frame orbit that looks into the hole from every side
  fills it inwards (0.8 to 1.2 mm small tracked, 0.9 to 1.5 at true poses). A further 0.2 mm
  was only the reference (25.0 in one study, 25.20 in the other).
- **Limits:** simulation only. Timings are rough unless the PC is quiet. The seams, the
  end-to-end scans and the CPU quick set have one recording each, the hard scenes two. A
  sheet's drift along itself is not scored. At true poses the placement still moves 0.1 to
  0.3 mm on the walls, the hole plates and the carburettor, and 0.8 mm on one seam. Replay is
  for fusing settings only. Determinism was checked on the quick set only, and whether a
  recording rendered again matches the cached one bit for bit was not checked.
- **Still to run with it:** the merge study, flat-within and the Check shares (the door and the
  carburettor are done: see `VALIDATION.md`).
- **Since 2026-11-21: `--chain app`** runs what the app's Make mesh runs (Follow the camera,
  Straighten, Refine with lasers, then the recipe) with the server's own functions and
  parameters, checked against a real Make mesh through the server: the same calls, the same
  meshes up to the numbering of the vertices. The default stays the frame pass, and the
  earlier baselines still compare identical. The app chain's baseline is
  `tools/scorecard_baselines/2026-11-21-gpu-core-app.json`. It differs from the frame pass on
  7 of 36 recordings (walls seeds 0 and 2, both speckled brackets, the hole plate 120 mm up
  seeds 0 and 2, one seam); on the other 29, and at true poses, Straighten and Refine with
  lasers change nothing. **An experiment that touches tracking or what follows it is judged on
  the app chain from now on**, against `2026-11-21-gpu-core-app-frames.json` (made after
  Straighten's test was switched, E-D2; the file without "frames" is the state before).
- A wall that read too thick and moved towards the truth is flagged "THINNER" in the table;
  that flag is a note, not an alarm, and does not make its scene worse.

### [ ] X0a. What the first baseline found (do these first)
Three things in the engine, none known before. Each is a defect to understand, not an
experiment: measure, find the cause, mend, and judge the mend with the scorecard.
- [ ] **No GPU, 0.5 mm plate (seed 0): tracking slides 39 mm, and the plate nearly vanishes.**
  At true poses the plate reads 0.49 mm with 99.9 % of its top; tracked it reads 0.11 mm with
  35 % of its top. A thin part thinned and partly removed without a word: against the
  thin-parts rule. First question: is it the defaults switched on for the no-GPU path on
  2026-11-20 (D7), or older?

  **Found and warned about (2026-11-20); the mend itself is the next item.** Numbers:
  `VALIDATION.md`, "The no-GPU plate slide, and a check of the camera path".
  - **Older than D7, and D7 made it smaller.** Before D7 the five plate recordings slid 29 to
    76 mm without a GPU; with D7 they slide 8 to 39 mm. Only seed 0 is a little worse with it
    (37 against 29 mm at the last frame).
  - **Every plate is damaged without a GPU, not only seed 0:** seeds 1 and 2 read 0.27 mm with
    67 to 76 % of the top, the two 90-frame plates 0.07 and 0.14 mm thin with 90 %.
  - **The cause is the tracking model the no-GPU path uses (Open3D's).** The whole slide
    happens in the first 8 frames, all of it along the table. Started at the true pose of
    frame 1, ICP against that model moves the camera 3.4 to 6.6 mm (against the GPU's model:
    0.4 to 1.2 mm), whichever path made the depth; even frame 0 against its own model moves
    0.65 mm (GPU 0.05). The pull comes from table pixels beside the model's holes and
    borders; a 0.5 mm step at the model's 2 mm voxels holds almost nothing along the table,
    and the constant-velocity prediction carries the first jump on (a first velocity of up to
    300 mm/s is let through; the acceleration guard then sees a steady speed).
  - **The warning (built, on, changes no result):** `ohs/track/path_check.py`. For every 0.8 s
    of tracked frames a quadratic in time is fitted to the camera's position; a frame more
    than 4 mm off it is a jump no hand makes. The scan's report gets `path_check` (`jump_mm`,
    `frame`, `state`) and a sentence in `warnings`. Without a GPU it warns on 12 of the 18
    recordings that went more than 5.9 mm off (all three 0.5 mm plates, the walls, two
    brackets, the dark and speckled ones) and on none of the three that held; on the GPU path
    on none of the 46 scans that held within 6.3 mm, and on two of the hole plates that slid.
    It does not see a slow drift at a steady wrong speed (the sheets, one bracket, and the two
    90-frame plates, which are thinned 0.07 and 0.14 mm without a warning). Both paths'
    scorecards are identical with it.
  - **Shown in the app since 2026-11-21:** a chip on the scan's row, the top of the viewer's
    processing report, the "Follow the camera" stage and the HTML report. This item stays
    open until the plates themselves come out right (the next item).
- [ ] **No GPU: mend the tracking model's pull.** Without a GPU the whole camera path is
  weak, not only on plates: the bracket's median is 0.48 to 0.69 mm (GPU 0.08 to 0.23), the
  sheets' path is 6 to 9 mm off, the walls' 6 to 18 mm. Two leads, measured on the five plates
  only (path max, mm; today 39.1 / 15.3 / 14.0 / 8.0 / 8.3): the model's normals taken from its
  vertex map (- / 9.2 / 14.6 / 3.2 / 7.0), a first velocity that starts from zero (11.6 / 15.3
  / 12.1 / 6.0 / 8.9), and both (6.0 / 9.2 / 10.2 / 4.1 / 7.0). Both together are better on
  every plate and still 4 to 10 mm off. To do: build them as settings, judge on the no-GPU
  baseline `2026-11-20-cpu-thin.json` (21 recordings: sphere, brackets, sheets, walls, plates,
  dark and speckled). **Stop rule: adopted only if** better or the same on every scene of that
  baseline and no plate or wall reads thinner. Also worth one try: the GPU model's rule of
  dropping hits near where its observations end. A second way to catch what the warning
  misses: check the path against the accelerometer (the recordings carry it).

  **Measured 2026-11-21: nothing adopted; the settings are built and off.** Numbers:
  `VALIDATION.md`, "The no-GPU tracking model's pull: settings tried".
  - **Built** (no-GPU tracking model only; the GPU's code path is untouched):
    `FusionParams.model_normals = "vertex_map"` (with `vertex_map_window`),
    `raycast_border_mm` (the GPU model's rule: no ray-cast hit within 8 mm of where the
    model's observations end), `velocity_from_rest`; `PipelineParams.track_trunc_mult`,
    `detail_border_px`.
  - **Judged on the 21 recordings:** vertex-map normals, the border rule, and both. Each is
    worse on several scenes, so by the stop rule none is on.
  - **What they mend:** the plates. With both, the plates' path max is 3.7 to 6.7 mm
    (today 8 to 39), their thickness 0.01 to 0.06 mm thin (today 0.07 to 0.39) and 90 to 100 %
    of the top is kept (today 34 to 90 %). The border rule alone lowers the bracket's median
    on all five seeds (0.31 to 0.59 mm, today 0.48 to 0.69).
  - **What they break:** the dark bracket runs away (177 to 430 mm off, today 43.5), one
    bracket seed slides 10 to 44 mm, the walls lose a face on one or two seeds, the sheet's
    flange and ribs jump by up to 3 mm on one seed. Not a near miss.
  - **Worse or no better on the plates and one bracket, not judged further:** a first
    velocity from rest, vertex-map normals without smoothing (bracket 32 mm off), a 2-voxel
    truncation for the tracking model (a plate 509 mm off), leaving the image's border band
    out of the probe's raw depth.
  - **Reading:** on this path the dark scenes are chaotic whatever the model does (as F14
    found), so a change to the model trades plates against them. What could still work: a
    tracking model as faithful as the GPU's on this path (X11's subject), a choice of the
    rule by the scene (plain and flat against dark and detailed: the engine already probes
    for detail at the start of a scan), or the accelerometer as a check. Until then the app
    warns (12 of the 21 today).
  - **Found on the way, mended:** the scorecard's "THICKER" alarm also fired when a plate
    read too thin moved towards its true thickness. It now alarms only when thicker is
    further from the truth ("LESS THIN" otherwise, no alarm).
- [ ] **Fused surface hangs past a sheet's free edge,** up to 15 mm out, where the drop behind
  the edge is 60 to 120 mm: 7 to 9 % of the 200-frame car panel's vertices, which is what puts
  its p95 at 1.7 to 2.3 mm while its median is 0.02 mm. The scorecard reports it as
  `part_beyond_edge`. Likely the same fill that closes holes (X1's subject).

  **2026-11-21, stays open.** X1's gate lowers the share past the edge from 7.0 / 7.9 % to
  5.8 / 6.5 % (tracked / true poses) and the panel's p95 from 1.78 / 2.13 to 1.14 / 1.47 mm,
  but at the cost of the standing walls, so it is off. What is needed is a cue for "the
  surface ends here" that does not take steep wall faces with it; the laser lines' ends (X2)
  are the next candidate.
  The laser lines' ends were then measured (X2), not built: they almost never run past an
  edge, but stop 0.3 to 1.7 mm inside it in the median and 1 to 4 mm inside it for 40 % of
  them. Too loose for a hole's reading. As a bound on fill that hangs up to 15 mm past an edge
  ("no surface further out than the furthest line end") it is untried, and would have to pass
  the thin-wall tests first.
- [ ] **Straighten's laser-crossing adjustment acts wrongly on standing walls, and the
  scorecard does not see it** (found by X3, 2026-11-21). On two of three wall recordings the
  adjustment passes its own held-out test (0.84, 0.72 against a limit of 0.85), but that test
  also passes at the true poses (0.61, 0.47): the crossings of one frame share that frame's
  errors, so a random split of crossings is not an independent check. Fused at its poses the
  walls read 0.31 to 0.46 mm thicker on one recording and up to 0.58 mm thinner on the other,
  whose 2 mm wall then reads 1.95 mm, 0.2 mm under its true-pose value: a thin part thinned
  without a word, in the app today, since Make mesh runs Straighten. The scorecard runs the
  frame pass only, so none of its numbers include Straighten. To do: (1) the scorecard gets a
  mode that runs what the app's Make mesh runs (Follow the camera, Straighten, Refine with
  lasers, the Default recipe), with a baseline of its own; (2) on it, judge the adjustment
  off against on, and a test that holds out frames (blocks of 10 refuse the walls, 1.41 to
  1.49, but also the genuine drift case the tests keep, 0.88 against 0.50). **Stop rule:** by
  the owner's rule, what is better or the same on every scene and thins no thin part becomes
  the default.

  **Measured 2026-11-21: nothing switched by the rule; the engine now says when it acts; the
  choice is with the owner (E-D2).** Numbers: `VALIDATION.md`, "Straighten's laser crossings on
  what Make mesh runs".
  - **The scorecard measures what the app makes** (`--chain app`, see X0). On its baseline the
    adjustment acts on 4 of 36 recordings: the standing walls seeds 0 and 2 and both speckled
    brackets.
  - **Its test is unsound.** On a wall recording at the TRUE poses the test passes (0.68) and the
    adjustment then moves the true poses by up to 8 mm.
  - **Two candidates, built as settings (both off when this was measured):** the adjustment switched off
    (`CurveParams.enabled = False`), and a test that holds out whole frames
    (`CurveParams.holdout = "frames"`: every second frame, each half in turn, the test on the
    crossings between two held-out frames). The frames test refuses every wall, tracked and at
    true poses (0.89 to 1.08 against a limit of 0.85), and still mends the engine tests' real
    drift (0.57 to 0.61).
  - **Neither is better or the same on every scene.** Walls seed 0 gets better with either (the
    walls 0.31 to 0.46 mm nearer the truth, the 0.5 mm wall's face 0.27 to 0.48 covered). Walls
    seed 2 reads 0.37 / 0.48 / 0.59 mm thicker than with today's adjustment and its 0.5 mm face
    is less covered; today's adjustment happens to pull that recording towards the truth, and
    past it (its 2 mm wall reads 1.95 mm). One speckled bracket's path median goes from 0.73 to
    1.02 mm. Every other recording is identical.
  - **Built and on:** when the crossings change the poses, Straighten's summary and the scan's
    warnings say so ("…On thin walls seen from both sides this can make a wall read thinner or
    thicker than it is…"). So it is no longer silent.
  - **Switched 2026-11-21 by the owner's choice (E-D2):** the frames test is the default. It
    refuses every wall recording, tracked and at true poses, and still mends the tests' real
    drift. See the report under the decisions at the top. The item stays open for the point
    below.
  - **Still open whatever is chosen:** no test yet tells thin walls from real drift when both
    are present: on a wall recording with drift added every test passes and acting makes the
    path five times worse.
- [ ] **GPU, hole plate 120 mm up, seed 0: tracking is lost** (path max 16.3 mm; the Ø25 hole
  then reads 6.9 mm small). Seeds 1 and 2 hold (3.9 mm).

  **Found 2026-11-21 (X4); stays open (X4b).** At frames 52 to 56 one frame lands 4.2 mm off
  from a start 1.7 mm off, and the constant-velocity prediction carries it to 8 and 23 mm. The
  tracker's own numbers stay healthy (fitness 0.78 to 0.82, RMSE 0.35 to 0.38 mm), and ICP
  from the true pose against the same model moves at most 1.6 mm: the model is sound, the
  translation is weakly held. Seeds 1 and 2 have step errors of about 2 mm that are not
  carried that far. The camera path's check warns on this recording.

  **2026-11-22 (X4b):** with the scan's start (a setting, off) the lost seed holds: path max
  16.3 → 2.8 mm, Ø25 -6.6 → -3.0, and the path check no longer warns. Not adopted (X4b), so
  this stays open.

### [x] X1. A gap guard that goes by the matcher's own doubt (measured 2026-11-21: not adopted) (S to M)
- **Idea:** drop fused depth whose matcher sigma says "fill" (above about 3 mm) within 12 to
  16 px of a patch without depth, instead of, or on top of, dropping everything within 8 px.
  Fill pixels have a median sigma of 34 mm; real surface 1 to 4 mm outside the edge has 0.48 mm.
- **Evidence** ("Holes filled in", true poses): dropping sigma above 3 mm was the best fix on
  every measure. Ø25 read 25.34 to 25.37 (today's guard: 24.38 to 24.54); the bracket 0.053 /
  0.39 mm (0.066 / 0.49); boss and shaft radii -0.07 / -0.10 / -0.06 (-0.14 / -0.16 / -0.12);
  it alone read the Ø10 hole near the floor. Applied everywhere it cost 24 % of the pixels
  and side-wall coverage 95.3 to 85.5 %; limited to the surroundings of patches it should
  keep most of that.
- **Changes:** GPU-path results (the fused depth only). `ohs/track/recon_tsdf.py`.
- **Risk:** steep side walls carry a sigma raised by the angle (try a variant without that
  term); thin walls are sensitive to the guard (a guard of 0 deletes a face of a 0.5 mm wall).
- **Experiment:** thresholds 2, 3, 5 mm; radii 8, 12, 16 px; with and without the angle term.
  Car panel 200 frames seeds 0 to 4; plates at 120 and 10 mm seeds 0 to 2 (Ø25, 10, 6 and
  the bore); bracket 240 frames seeds 0 to 4; standing walls and the sheet part seeds 0 to 2;
  the end-to-end sphere and panel.
- **Stop rule: adopted only if** Ø25 reads at least 0.3 mm nearer the truth on every panel
  and plate-120 recording at both pose sources and none reads further; the bracket's p95 and
  boss radius are no worse than +0.02 mm on any seed; coverage drops by at most 1 point (side
  walls at most 2); no wall gets more than 0.05 mm thicker and none loses a face; sphere and
  panel stay within their range across seeds.

**Measured 2026-11-21: not adopted; the settings are built and off.** Numbers: `VALIDATION.md`,
"The gap guard by the matcher's doubt".
- **Built:** `PipelineParams.gap_guard_sigma_mm`, `gap_guard_sigma_px`, `gap_guard_sigma_angle`
  (the GPU's surface model and its numpy twin; the model the no-GPU path uses fuses no sigma
  and ignores them: its results are identical with them on).
- **Tried:** paired replays at 2, 3 and 5 mm with 8, 10, 12 and 16 px, with and without the
  matcher's steep-view factor, on top of today's 8 px or instead of it; then three full
  compares on the 36 recordings (3 mm with 10, 12 and 16 px).
- **What it mends:** the orbit's Ø25 hole opens by 0.7 to 1.2 mm (car panel -1.32 to -0.38 mm
  at 12 px; hole plate 120 mm up at true poses -1.51 to -0.80), the car panel's p95 falls by
  0.6 to 0.9 mm and the share of surface past its free edge from 7.0 to 5.8 %.
- **What it breaks, at every setting:** the standing walls read 0.15 to 0.55 mm thicker and
  the 2 mm wall loses a face from 12 px; the sheet's ribs read 0.05 to 0.10 mm thicker;
  coverage falls 1 to 8 points on the walls, the brackets and the sphere (19 to 21 on one
  speckled bracket); a boss answers one click fewer; the one-sided pan's Ø25, which reads
  wide already, reads 0.4 to 0.5 mm wider. It misses its stop rule on four counts and the
  owner's rule on eight scenes: a clear miss, on thin parts.
- **Why:** within about 8 px of a patch without depth the matcher's own hole factor (up to 121
  times) puts nearly every pixel's sigma above 3 mm. So the gate does not tell fill from
  surface there: it acts as a wider distance guard, opening holes and eating the faces of
  standing walls alike. With today's guard off, a 3 mm gate at 8 px gives today's result.
- **Thin parts:** the 0.5 mm plate lying flat is untouched at every reach up to 48 px; the
  standing walls get worse from 10 px, the first reach at which the gate does anything.
- **Not tried:** a gate on the sigma with the hole factor taken out (the matcher's doubt from
  the image alone). It is outside this experiment's grid.

### [x] X2. Hole rims and edges from the laser lines (measured 2026-11-21: not built) (S, then M)
- **Idea:** a laser line ends exactly where the surface ends. Collect the curves' end points
  at a rim over many frames and fit the circle (or the edge) in the face's plane. Keep laser
  points that the depth check drops only because the dense depth there is fill.
- **Evidence:** laser points are 0.04 to 0.07 mm accurate and free of the matcher's window.
  At the panel seam the laser cloud stopped 1.5 to 2.6 mm short of the edges ["Gap and
  flush"], probably the depth check (not verified).
- **Changes:** a new reading for the Hole tool (no scan changes). Relaxing the depth check
  changes the laser cloud: an option.
- **Risk:** the real line's width and speckle at a rim; too few rim points on a short scan.
- **Experiment:** first a diagnostic: where the line ends lie against the true rims, with the
  depth check on and off. Then plates Ø25, 10, 6 at both distances, seeds 0 to 2; panel Ø25
  seeds 0 to 4; the three seam recordings; 3 clicks a hole, true and tracked poses.
- **Stop rule: offered only if** Ø is within 0.2 mm on at least 8 of 9 clicks a hole at both
  pose sources, the centre within 0.2 mm, with at least 40 rim points. A gap is offered only
  if its bias is equal within 0.2 mm across gap sizes.

**Measured 2026-11-21: stopped at the first measurement; nothing built.** Numbers:
`VALIDATION.md`, "Hole rims from the laser lines' ends".
- **Today's ends are short.** The extraction trims 2 points off each end of a segment, so the
  ends sit 0.4 to 0.5 mm inside the rim; 1 to 6 % are within 0.2 mm of it.
- **Without the trim** 16 to 34 % are within 0.2 mm, but about 40 % stop 1 to 4 mm short: a
  projector dot or one outlier row within a line's last 20 rows breaks it, and the piece
  beyond is shorter than the shortest segment kept. Medians +0.3 to +1.5 mm.
- **No circle fit to the ends** of a 200-frame scan reads the Ø25 hole nearer than 0.2 to
  1.4 mm large, the Ø10 up to 3 mm: far from the stop rule's 0.2 mm on 8 of 9 clicks.
- **Part two has nothing to win:** the depth check drops 0 to 0.06 % of the laser points, almost
  all on the true surface and none at a rim or a free edge.
- **What holds:** a line's end almost never runs past a rim (1 to 6 % by more than 0.5 mm). It
  is a one-sided cue: "the surface reaches at least this far".
- **What could still work:** an extraction that finds a line's end itself (where the
  intensity along the line drops), without the trim and without losing short end pieces. It
  needs the real line's width first ("Only the real scanner can settle").

### [x] X3. Poses refined against the finished scan (measured 2026-11-21: not adopted) (M to L)
- **Idea A:** after the first fuse, register every frame again with the tracker's ICP against
  the finished 1 mm model (thin-wall rules on), the gyro holding the relative rotation and
  weak directions staying at the tracked pose; fuse again. One or two rounds.
- **Idea B:** find out why Straighten's laser-curve adjustment never acts on tracked scans (0
  of 11 times, MP P7), then let it act where its held-out test allows.
- **Evidence:** tracked walls are too thick by 0.77 times how far out the cameras stand; one
  bracket seed reads 31 % against 12 %; Best is worse than Standard on the bracket. Laser
  crossings are 0.06 mm accurate and halved the sphere's error under drift. Against: an
  online refine on a young model overshot one sheet at +19 % a frame; offline the model is
  complete, so that weakness does not apply.
- **Changes:** results, and about +15 to 25 % on Make mesh.
- **Risk:** frames pulled onto a model fused from their own biased poses; sliding on smooth
  scenes.
- **Experiment:** the sheet part seeds 0 to 2, bracket 240 frames seeds 0 to 4, panel 200
  frames seeds 0 to 4, the end-to-end scans, 3 door stretches; idea B also with four lasers.
- **Stop rule: adopted only if** the flange and the 1 mm rib get at least 0.15 mm thinner on
  all three sheets and stay no more than 0.05 mm below their true-pose values; the bracket is
  no more than 1 point worse on any seed and better on the bad one; smooth scenes' camera
  paths are no more than 10 % worse; the time stays within +25 %.

**Measured 2026-11-21: neither idea can work; nothing built.** Numbers: `VALIDATION.md`,
"Poses refined against the finished scan".
- **What the tracked error is.** Fused at true poses plus one part of the tracked error, the
  slow part (over about 2 s) brings back 85 to 132 % of the mesh's extra error on all eleven
  recordings (brackets, sheets, standing walls); the frame-to-frame jitter and the jumps mostly
  under 30 %; the translation far more than the rotation. Most of the slow part is one
  translation in the camera's own frame, shared by every frame: on every sheet and wall
  recording the camera stands 0.19 to 0.25 mm out. With the truth's value of that offset taken
  out, the 1 mm standing wall reads its true-pose thickness and the sheet's flange +0.21 mm
  (today +0.60).
- **The scan cannot find it.** It leaves the scan consistent with itself: the vertex sigma does
  not move across it, and the laser crossings meet better with it than without it.
- **Idea A** (frames registered again against the finished model; one to three rounds; raw or
  filtered input; the model's borders in or out; weak directions held) misses the stop rule:
  the flange and the walls get thicker each round, since the model carries its own thin-wall
  excess; with the borders out the frames slide and a 0.5 mm wall loses a face; the car panel's
  path is 60 to 100 % worse. Only the bad bracket seed improves. About +30 % time a round.
- **Idea B:** the crossings already meet at the lasers' own scatter at the tracked poses (13 of
  16 recordings), so the adjustment has nothing to do, and forced it worsens 12 of 15 paths.
- **Next:** the offset is made in tracking, so X4 and the tracking model's handling of thin
  features are where it can be mended. And see X0a: where Straighten's adjustment does act
  today, on standing walls, it acts wrongly.

### [x] X4. Tracking on dark, plain and thin parts, GPU path (measured 2026-11-21: not adopted) (S, S, M)
- **Steps:** (a) diagnose by axis and frame on the six hard recordings: where the drift
  starts, how well the translation is held, detail mode, the carried velocity. (b) allow the
  velocity guard on the GPU model. (c) add laser points as ICP points with their own weight
  (they stay bright where the dots fade, and they are not fused into the tracking model, so
  they cannot lock in a camera-fixed pattern). (d) hold the velocity at zero for the first
  frames.
- **Evidence:** 1.1 to 3.8 mm against 0.61 mm on the plain bracket. On the CPU path the guard
  with the depth fix took the dark seeds from 167 to 42 mm and from 249 to 0.9 mm.
- **Risk:** a laser plane's calibration bias entering the poses; the guard holding back a real
  fast start; real dark parts may be mended in hardware (exposure, emitter level).
- **Experiment:** the six hard recordings, bracket seeds 0 to 4, sphere, panel, the door and
  carburettor stretches.
- **Stop rule: adopted only if** every hard scene improves by at least 30 % and ends under
  1 mm; no other scene gets worse beyond its range across seeds; no frame is lost.

**Measured 2026-11-21: nothing adopted.** Numbers: `VALIDATION.md`, "Tracking on dark, plain
and thin parts".
- **Where the camera's offset is made (X3's finding).** Not by a model that is wrong at the
  truth: from the true pose against a tracking model fused at the true poses, ICP pulls the
  camera 0 to 0.08 mm in, not out. Not by the first frame: true poses for the first one or
  five frames leave the offset as it is. It is where the tracking loop settles, and the
  tracking model's band behind the surface (`gpu_back_voxels`) sets that; truncation and voxel
  size act the same way; the border, the weight threshold, the normals and the tracking filter
  barely move it. Band off: the sheet's camera +0.31 mm out; 1 voxel (today) +0.19; 0.75:
  +0.07; 0.5: -0.15, and the sheet's 3 mm rib then reads 0.42 mm thin.
- **Judged at 0.75 voxel on what Make mesh runs: a miss.** Walls 0.1 to 0.23 mm and the
  sheet's walls 0.15 to 0.37 mm nearer the truth, the hole plate's lost seed 16.3 → 8.0 mm.
  But bosses and the shaft read 0.03 to 0.14 mm smaller on every bracket, two brackets are
  0.04 to 0.05 mm worse in the median, one sheet is thinned under its true size (flange 2.00,
  3 mm rib 2.82), and a seam and the hard scenes get worse. A shorter band moves every
  recording inward together: it trades the ones that stood out against the ones that stood in.
- **Step (b), a velocity guard on the GPU model:** built (`FusionParams.gpu_velocity_guard`,
  off). At 2 m/s² it never acts (frames are 0.1 s apart, so a step may change by 20 mm); at
  0.6 m/s² the hard scenes get worse.
- **The hard recordings:** the dark bracket's 3.9 mm are made at frames 1 and 2 and then held
  by the model; the speckled ones start 1.3 to 1.7 mm off at frame 1; the hard plates slip
  about 2 mm for single pairs of frames. None came under 1 mm with anything tried.
- **Found on the way:** frame 1 is registered against a model made of one frame and lands 1 to
  8 mm off on every scene with a table; 31 of 36 recordings decide for the tracker's detail
  mode at frame 1 or 2, on that one-frame model. And the true-pose reference itself carries
  the voxel grid's alignment (about 43 % of one 0.5 mm wall's gap).
- **Not tried:** step (c), laser points in the ICP. **Next:** X4b below.

### [x] X4b. The scan's start, and holding a weak translation (found by X4; S to M): not adopted
- **Measured 2026-11-22: not adopted; the settings are built and off.** Built:
  `PipelineParams.start_frames`, `start_sweeps`, `start_predict`, `start_first`,
  `start_detail_only`, `start_detail` (the GPU tracking model only), and
  `FusionParams.weak_step_strength / weak_step_mm / weak_step_mode`.
  - **(a) the scan's start** (10 frames held back, each registered again against the other
    nine from a quadratic fit in time, frame 0 too, only in detail mode) meets this item's own
    targets: the dark bracket's last frame 3.79 → 0.59 mm (the second 0.70 → 0.54); hole plate
    120 mm up, path max 16.3 / 7.1 / 3.9 → 2.8 / 4.0 / 2.7 and seed 0's Ø25 -6.6 → -3.0; the
    walls' last frame 1.36 → 0.55 mm; the smooth scenes identical; the camera path's check
    warns on none of 36 (today 2).
  - **But on what Make mesh runs it ends worse on 7 scenes:** bracket seed 1's path max
    +1.3 mm, the speckled brackets (last frame 0.35 → 0.88 on one), the 10 mm hole plate's
    small features (Ø25 0.5 mm further off on one seed), hairline rows on the dark bracket and
    the plates, and sheet and wall readings up to 0.04 mm further from the truth on single
    seeds. A miss by E-D1; most rows are small, so the numbers are with the owner
    (`VALIDATION.md`, "The scan's start, and a weakly held step").
  - **(b)** deciding detail mode after 10 frames decides the same on every recording tried.
  - **(c)** alone takes the lost hole plate to 8 mm only and makes a hard plate worse; with
    (a) it adds nothing.
  - **(d)** was not built.
  - Without a GPU the start took a test recording 108 mm off, so it is not used there.
  - **If taken up again:** the start only at full resolution (the live preview stays as it
    is), and a look at why the speckled brackets and bracket seed 1 get worse.
- **Looked into, 2026-11-22 (`VALIDATION.md`, "Why some scenes got worse"): still not
  adopted, no full compare spent.**
  - Every registration of the start lands about 0.2 mm off the others in the same direction
    (along the table); one after the other, that became a group offset from frame 0 (the
    speckled bracket 0.45 mm). Built `PipelineParams.start_together` (off): a round registers
    against the poses from before it. Speckled brackets' last frame 1.125 / 0.349 → 0.570 /
    0.437 (one after the other 1.259 / 0.876); brackets 0, 3, 4 and the walls better too.
  - Bracket seed 1's path max and the hole plate's loss are single frames slipping in
    mid-scan, there with the start off; whether they slip tips with the start's poses
    (together the hole plate is at 19.1 mm again; the "rescue" above was luck). That is X4c.
  - The start only at full resolution changes no number (the scorecard runs that already).
    The small hole, sheet and wall rows are single seeds' noise inside the baseline's spread.
  - **Judge again after X4c** with `start_frames=10, start_predict=fit, start_first=true,
    start_detail_only=true, start_together=true`.
- **What X4 measured:** the first frame is registered against a model of one frame from a
  prediction of zero and lands 1 to 8 mm off; the dark bracket's whole 3.9 mm error is made in
  frames 1 and 2 and never mended; the hole plate's loss at frames 52 to 56 and the hard
  plates' slips are single frames landing a few mm off while the tracker's own numbers look
  healthy, then carried by the constant-velocity prediction.
- **Ideas, each a setting:** (a) build the first model from several frames before trusting it
  (hold the camera's first frames together with the motion sensor's rotation and a short
  window of frames, or register frames 1 to k against each other and fuse once); (b) decide
  for detail mode after k frames, not on one; (c) where the translation is weakly held
  (`trans_strength` low), do not carry a jump into the prediction: fall back to the last
  velocity that was confirmed by two frames; (d) the motion sensor's accelerometer as a check
  on a step (the recordings carry it; the engine does not use it).
- **Judge on** the app chain's baseline, the hard recordings and the hole plate 120 mm up.
- **Stop rule: adopted only if** the dark bracket's last frame is under 1.5 mm and the hole
  plate's seed 0 path max under 6 mm, no scene is worse beyond its spread, nothing thin moves
  away from the truth, and the smooth scenes' paths stay within 10 %.

### [x] X4c. A single frame that slips in mid-scan (found by X4 and X4b; M): not adopted
- **Evidence:** the hole plate 120 mm up, seed 0, at frames 52 to 56 (16.3 mm) and bracket
  seed 1 at frames 151 to 157 (1.8 to 3.7 mm): one frame lands a few mm off along the weakly
  held direction while the tracker's own numbers look healthy, and the constant-velocity
  prediction carries it. Whether it happens tips with tiny changes of the poses before it
  (X4b: starts of 6 to 14 frames give 2.0 to 4.1 mm on the bracket). The weak-step rule
  (`FusionParams.weak_step_*`, off) only halves the hole plate's and does nothing for the
  bracket.
- **Ideas, each a setting:** hold a frame whose step along the weak direction leaves the
  prediction by more than the limit out of the tracking model, and register it again once
  the next frames are in (a short window, as the start does); register it from two
  predictions and keep the one the model agrees with; the motion sensor's accelerometer as a
  check on the step (X4b's (d), never built).
- **Judge on** the app chain's baseline; then judge X4b's start again with `start_together`.
- **Stop rule: adopted only if** the hole plate's seed 0 path max is under 6 mm and bracket
  seed 1's under today's 2.40 mm, the result does not tip when the start's length changes
  (6 to 14 frames), no scene is worse beyond its spread, nothing thin moves away from the
  truth, and the smooth scenes are identical.
- **Measured 2026-11-22 (`VALIDATION.md`, "A single frame that slips in mid-scan"): one cause
  held, one open; not adopted, no compare spent.**
  - **The two slips have different causes.** The hole plate's frame 52 slides 6.7 mm along
    the weak direction into another fit, 5.1 mm off; from the truth and from every other
    start it stays within 0.2 to 0.9 mm at a clearly lower cost, even against a model at
    true poses. Bracket seed 1's frames land 1 to 1.6 mm off because the model, fused a few
    tenths of a mm off, pulls them (0.1 mm against a model of the same frames at true poses).
  - **Built, off:** `FusionParams.slip_check_mm`, `slip_starts`, `slip_margin`: a frame whose
    step along its weakest translation leaves the prediction by more than the limit is
    registered again from two other starts, and the result the model agrees with best is
    kept. It holds the hole plate at every start (path max 2.3 to 3.1 mm after the start,
    from 16.3 and up to 19.1) and never acts on the bracket, which still tips (1.81 to
    3.30 mm).
  - **The accelerometer** is no better than constant velocity with gravity and bias unknown
    (median 0.6 mm); with both known it is good to 0.1 mm. An estimator while scanning is not
    built.
  - **Still open, the bracket's cause:** a tracking model that agrees better. Ideas: fuse a
    frame into the tracking model only once the next frame confirms it; register a short
    window of frames again together, as the start does; the accelerometer with gravity and
    bias estimated while scanning. Then judge together: `fusion.slip_check_mm=3` and the
    start (`start_frames=10, start_predict=fit, start_first=true, start_detail_only=true,
    start_together=true`).
- **Round 2, the model that pulls (2026-11-22; `VALIDATION.md`, "The newest frame held out of
  the model"): not adopted, no full compare spent; the numbers are with the owner.**
  - **Where the error comes from:** each frame lands off along its weakest translation only,
    no shared bias, the sign alternating: 0.45 mm rms where the translation is held at 6e-4
    to 9e-4 (bracket seed 0: 0.19 mm at 1.2e-3 to 1.7e-3). A model at true poses does not
    make it smaller, a shorter memory makes it larger; the model without its newest frame
    makes it 0.38 (largest 1.01 against 1.63): where new surface comes into view the next
    frames take the newest frame's error.
  - **The accelerometer with tracked poses** predicts no better than the registration itself
    (0.22 / 0.80 against 0.22 / 0.71 mm). Not built.
  - **Built, off:** `FusionParams.fuse_lag` (0) and `fuse_lag_detail_only` (True): a frame
    goes into the tracking model only once the next has registered. With the slip check, at
    every start (off, 6 to 14 frames): bracket seed 1 1.77 to 2.30 mm (today 2.40; 1.81 to
    3.30 without the lag), the hole plate 2.4 to 3.2 after the start. Both targets met.
  - **Small set, all three on** (start 10 together, slip check 3 mm, lag 1): the dark
    brackets 3.79 / 0.70 → 0.74 / 0.44 last frame, the hard plates and brackets better,
    hole plate seed 0 16.3 → 3.1, nothing thin away from the truth, smooth scenes identical.
    But worse on the speckled brackets (last frame 1.125 / 0.349 → 1.456 / 0.749), hole
    plate 120 mm up seed 2 (median 1.87 → 3.01), the sheet's path max (2.38 → 3.33) and the
    hole plates' small features. A miss by E-D1.
  - **What remains** is the frame's own scatter along the weak direction; every change rolls
    again where it adds up. A cure needs something that holds that direction: the laser's
    points in the registration, or a better accelerometer. To judge the three anyway:
    `pipeline.start_frames=10, start_predict=fit, start_first=true, start_detail_only=true,
    start_together=true, fusion.slip_check_mm=3, fusion.fuse_lag=1`.

### [x] X5. Fine voxels where the part needs them (S to M, then L): the hint on; step 2 not a default, in the app as a choice per scan
- **Step 1:** after Make mesh, find thin walls (where the opposite-sides rule fired) and
  small round features, and suggest the "Thin sheet metal and small parts" preset with its
  measured gain (FP P4). A hint only.
  - **Step 1 done 2026-11-22: the hint, on; it changes no result** (`VALIDATION.md`, "The
    thin-parts hint"). `ohs/mesh/thin_parts.py` looks at the mesh after Make mesh for thin
    walls seen from both sides (a reading of at most 2.7 mesh detail over at least 100 square
    mesh detail), a thin plate lying flat, and small round holes or bores whose wall was seen.
  - **The preset is suggested only for thin walls**, the one case where 0.5 mm was measured to
    help (0.23 to 0.67 mm nearer the truth, P1). A plate or small holes alone are recorded in
    `report.json` and not suggested: a plate lying flat reads right at every mesh detail, and
    small holes were not shown nearer the truth beyond the spread.
  - The hint says what was found, what the preset changes as measured, and what it costs; one
    button sets the preset and makes the mesh again; it can be put away for the scan.
  - On the 36 cached recordings it suggests on the standing walls and the sheet (6 of 6),
    records without suggesting on the 0.5 mm plates (5) and the hole plates (6), and is
    silent on the rest. Bosses are not looked for (their error is the depth's); holes the
    cameras filled in are not found.
- **Step 2:** fuse at 0.5 mm only in the flagged bricks (replaying cached depth) and stitch
  them into the 1 mm mesh.
  - **From step 1:** the finding carries the bricks (8 mesh detail on a side) holding the
    flagged wall and hole-wall vertices: 92 to 169 on the walls and sheets, 4 to 15 on the
    hole plates, well under 5 % of the volume. A wall's two faces sit 1.7 to 2.7 mm apart, so
    a brick must take both (dilate by one); hole bricks hold the bore's wall, not its rim
    (dilate too); on the sheet the flange and rib are one region, so stitch lines would run
    along rib feet and flange edges; the finding runs on the edited mesh, a replay needs the
    base stage's mesh.
  - **Groundwork done 2026-11-22** (`VALIDATION.md`, "Launch times on the graphics card at
    0.5 mm"): every launch timed at 0.5 mm on the Radeon 680M and the mesh model's bounded;
    the longest 70 ms, no driver timeout. **The reference step 2 has to beat**, all at 0.5 mm,
    what Make mesh runs, tracked: standing walls 76.8 s and 9.9 GB (1 mm: 40.5 s, 1.24 GB);
    bracket 133.4 s and 11.9 GB (1 mm: 80.0 s, 1.78 GB; 6.4 against 1.55 million triangles).
    Step 2's limits are therefore 50.6 s / 1.61 GB and 100 s / 2.31 GB. The memory is mostly
    the finer mesh on the processor (the card's shared memory 2.6 to 3 GB), so the memory rule
    is decided by how much 0.5 mm mesh step 2 builds and stitches; a finer model only in the
    flagged bricks needs its own brick search limited to them.
  - **Step 2 measured 2026-11-22: not adopted; the setting is built and off**
    (`ProcessParams.fine_walls`; `VALIDATION.md`, "Fine mesh detail on thin walls only").
    Follow the camera keeps each fused frame's depth on disk; after Straighten the kept depth
    is fused at 0.5 mm only in the walls' bricks (grown by one), and the 1 mm mesh is split
    there and moved onto that model (at most 1 mm, only onto the face a vertex faces, so no
    wall can close; no surface is removed).
  - **Against the stop rule** (12 runs with thin walls, 10 without): the standing walls stay
    0.28 / 0.06 / 0.04 mm (tracked) and 0.24 / 0.22 / 0.10 mm (true poses) from the all-0.5 mm
    result: missed. The sheet is within 0.03. Time 1.16 to 1.20 times tracked (met), 1.18 to
    1.29 times at true poses (missed on one run). Memory 1.00 times, open loops 1.00 times,
    recordings without thin walls identical. The part's median error is worse on two
    true-pose runs. No full compare was spent.
  - **What it gives against the 1 mm mesh:** every wall of 0.5 to 2 mm nearer the truth on all
    twelve runs (by 0.04 to 0.52 mm), for 16 to 29 % more time, no more memory, no new open
    loops: about 50 to 85 % of the whole-scan 0.5 mm gain at an eighth of its memory. The
    numbers went to the owner 2026-11-22.
  - **The miss** is each wall's less-seen side, where the 0.5 mm model has 1 to 3 observations
    a voxel and the all-0.5 mm mesh has holes; the stitch itself is clean (no doubled or flat
    triangle, the step at the seam 0.05 to 0.09 mm at the 95th percentile).
  - **To take it up again:** fuse only the kept frames that see a region and keep the depth
    smaller (time); the less-seen side (accuracy).
  - **In the app 2026-11-22, a choice per scan, off unless chosen** (the owner's decision;
    `VALIDATION.md`, the section on "Make only the thin walls finer" in the app). The hint offers
    "Make only the thin walls finer" first and the preset second; the choice is kept in the
    scan's entry (`POST .../process {fine_walls}`; `[process] fine_walls`, off, for scans
    without one). It runs only on the graphics card, at mesh detail 1 mm, not at Preview, and
    when the drive has the kept depth's size and 2 GB more free; else Make mesh goes on
    without it and says why. A mesh made finer is not suggested again; a note in the hint's
    place offers "Make the mesh again without this".
  - In the real app on the standing walls: Make mesh 46.2 → 55.9 s, the walls read 1.83 →
    1.54 mm, 907 MB kept while it runs and removed; without it again 46.3 s and the mesh as
    before. With the choice off the quick set compares identical.
  - Not done: fusing only the kept frames that see a region; a mesh detail other than 1 mm.
- **Evidence:** 0.5 mm is the only setting that passed P1's stop rule (walls 0.23 to 0.67 mm
  nearer), for 1.2 to 2.4 times the time and 7 GB at 3.3 million triangles (R5).
- **Risk:** pinholes and cracks at the boundary, memory, holes reading smaller.
- **Stop rule (step 2): adopted only if** walls are within 0.05 mm of the all-0.5 mm result
  and the hole within that result's range; time at most 1.25 times the 1 mm run, memory at
  most 1.3 times, open loops at most 1.2 times; smooth scenes within their range.

### [x] X6. "Refine from the images" inside Make mesh (S): not adopted, on time
- **Measured 2026-11-22: not adopted; the setting is built and off** (`VALIDATION.md`, "Refine
  from the images inside Make mesh, and a driver timeout"). Seven recordings, one seed each,
  what Make mesh runs, tracked and true poses.
  - `[process] refine_from_images` (off) runs the stage between Straighten and Refine with
    lasers; on the GPU only, not at Preview quality.
  - **Unguarded** it helps smooth surfaces (sphere median 0.060 → 0.016 mm, panels about
    halved) and hurts edges and rims: bracket edges 0.15 → 0.24 mm and p95 +0.15; the Ø 10
    hole 0.2 mm larger, the bore lost; a 1 mm wall 0.15 mm thinner at true poses.
  - **A guard** (off in the mesh edit; Make mesh: 30°, 3 mm from a free edge, 2 mm thin wall,
    0.3 mm move) removes every loss and keeps the median gains; it costs part of the sphere's
    gain, and the 0.5 mm plate reads 0.023 mm thicker at true poses. Walls up to 1.9 mm are
    left as they are.
  - **The miss:** Make mesh +41 to +196 % (30 % allowed). The time is mostly on the processor
    (placing patches 7 to 10 s, ray casts 11 to 19 s); the GPU search is 4 to 8 s.
  - **Found on the way, mended:** the search ran each level as one GPU launch of up to about
    3 s; the Radeon driver timed out, the PC reset four times, and once the search silently
    returned nothing. The launches are now short (longest measured 0.22 s) and checked; on for
    the mesh edit too.
  - **If taken up again:** faster patch placing and ray casts (same results), then the other
    seeds and the full set. If adopted the app needs: the mesh edit run after it starts from
    the unrefined mesh without the guard (say so, or give it the guard); the history entry to
    record the setting and limits; the setting's change to mark the mesh as made with other
    settings; the toggle with "needs a graphics card, not at Preview"; the stage's share of
    the progress bar (guessed 15 %, measured 25 to 50 %).
- **Evidence:** sphere 0.081 / 0.276 to 0.044 / 0.129 mm; panel 0.043 / 0.148 to 0.023 /
  0.093. Never tried on the bracket's edges; round the panel's edge it was worse than a plain
  local fit.
- **Stop rule: adopted only if** flat regions and flatness improve beyond the range across
  seeds; edges, rims and walls get no more than 0.02 mm worse on any seed; Make mesh grows by
  at most 30 %.

### [x] X7. A merge that improves accuracy (S, then M): measured, not adopted, nothing built
- First measure the merge again on today's engine (the study predates the opposite-sides
  rule). Then register both scans' frames onto the merged model, the part only, table masked
  (X3 across scans).
- **Stop rule: adopted only if** the merged part beats the better single scan on at least 4
  of 5 pairs, and its good surface is at least the union's.
- **Measured 2026-11-22** (`VALIDATION.md`, "The merge on today's engine"; five pairs fixed
  beforehand; `tools/merge_accuracy_study.py`): the app's merge beats the better single scan
  on 1 of 5 pairs and reaches the two scans' good surface on 1 of 5; no pair does both.
  - With both scans at their true poses the merge still misses the two scans' good surface on
    4 of 5: a fused merge averages, and what it loses lies 0.5 to 1 mm off where only one scan
    was good. So nothing that only mends poses can pass; registering the frames onto the
    merged model was not built.
  - Where a merge does add accuracy: opposite sides at the right pose (bracket 0.075 mm /
    12.0 % against 0.098 / 15.7 % for the better scan), and thin walls seen from both faces.
  - Where it loses most: pairs that share little of the part. The alignment accepted poses
    about 1 mm (bracket, opposite sides, 46 % shared) and 3 mm (sheet, 40 % shared) off from
    picked points, and those merges are far worse than either scan.
  - The rule's "at least the union" is close to what no single surface can reach; if the
    item is taken up again the owner may want to restate it.

### [ ] X8. Large parts: measure the overall size first (M)
- A 1.2 to 1.5 m plain panel with Ø10 holes at both ends, scanned in passes: the end-to-end
  distance, the bow, the slide between passes. Only if it fails: tags on the table as anchors
  in Straighten (`ohs/align/tags.py` exists).
- **Stop rule (anchors): adopted only if** under 0.05 % end to end on all 3 seeds with the
  door no worse than its spread.

### [ ] X9. The matcher for the next frame while this one is tracked (M to L)
- Results identical; another 450 MB of graphics memory. Little gain expected on the Radeon PC
  (MP F8).
- **Stop rule: adopted only if** the snapshot is bit for bit the same and Make mesh is at
  least 15 % faster on the RTX laptop, the Radeon PC not slower.

### [ ] X10. Laser-line filtering on the GPU (M)
- An extraction takes 13.6 ms alone and 73.6 ms beside the matcher on the Radeon PC; four
  lasers cost 70 ms a frame at 640x400.
- **Stop rule: adopted only if** the laser points are within 0.005 mm median of today's and a
  frame with four lasers is at least 10 ms faster on the Radeon PC, the RTX laptop not slower.

### [ ] X11. Without a GPU: a compiled confidence model (M to L)
- The numpy model takes 140 ms a frame against Open3D's 8 ms and is 2 points better even
  without thin walls. Compiled (numba is already an optional dependency) it would give the
  CPU path thin walls, the sigma, the Check and the guidance.
- **Stop rule: adopted only if** at most 25 ms a frame, equal to the numpy model within
  1e-5 mm, and better on every CPU-path scene (the owner's rule for that path).

### [ ] X12. Guidance towards sides never scanned (M)
- Guided passes gain +13 points against +8 on the first scan's own surface but are equal to
  random on the whole part (+24.8 against +23.1) ["Why an area is weak"].
- **Stop rule: adopted only if** better than random on the whole part on at least 4 of 5
  seeds, beyond random's spread, while keeping the +13.

### [ ] X13. A term for the pose in the vertex sigma (M)
- A region shifted by tracking is invisible to the sigma; a tracked plane's offset is covered
  only 44 %.
- **Stop rule: adopted only if** 2 sigma covers at least 95 % on tracked faces and edges and
  the Check still passes at least 50 % at 0.3 mm.

### [ ] X14. Hole tool: read the wall below the shoulder (S to M)
- The reading depends on the level (26.7 to 24.3 mm); the truth is crossed 0.8 to 0.9 mm
  down. Weaker than X2.
- **Stop rule: adopted only if** within 0.2 mm on 8 of 9 clicks.

## Small things found by the review

- [x] "Known limitations of the pipeline" in `VALIDATION.md` was out of date: it said the
  lasers are not used in the mesh (Refine with lasers exists), bosses read 0.1 mm small, walls
  0.4 to 0.7 mm too thick, 0.35 s a frame without a GPU, the guard's cost 1 to 7 points, and
  the mesh's p95 0.7 to 1.2 mm. **Rewritten 2026-11-21** from the sections that hold today's
  numbers, each point naming its section; the p95 is given by part from the scorecard's
  baseline (the "0.39 to 0.51" noted here matched no current table).
- [ ] `tools/preset_study.py` places the bracket with the whole scene, so the table rules its
  error (0.17 / 0.49 / 0.69 mm median): not comparable with the part alone (0.14 mm).
- [ ] `ohs/track/volume_gpu.py`'s docstring still says the mesh's model is Open3D's.
- [ ] Every timing on the Radeon PC since 2026-11-10 ran beside `RadeonSoftware.exe` using a
  whole core (MP F8).
- [x] GPU launches that could run long (found by X6, 2026-11-22). **Measured and bounded
  2026-11-22** (`VALIDATION.md`, "Launch times on the graphics card at 0.5 mm"). The tracking
  model's whole-volume launches do not grow with the mesh detail (its voxel is at least 2 mm,
  its cube at most 384 a side: the reset 7 ms). What grows is the mesh model: its fusion a
  frame (about 4 times the bricks at 0.5 mm) and its pool's fill, copy and download. These now
  go in batches of at most 32,768 bricks (or what the measured pace does in 0.1 s) and pieces
  of 64 MB, the model the same bit for bit. The longest launch of Make mesh at 0.5 mm is 70 ms
  (1 mm: 45 ms, the matcher). `OHS_GPU_TIMES=1` records every launch
  (`ohs.compute.launches`). A driver reset part way now fails the job instead of writing a
  mesh.
- [ ] A merge of two scans that share little of the part (found by X7, 2026-11-22): from
  picked points the alignment accepts a pose about 1 mm off (bracket, opposite sides, 46 %
  shared) or 3 mm off (sheet, 40 % shared) and says "Aligned"; the merged part is then far
  worse than either scan (0.31 and 0.37 mm median against 0.10 and 0.16) and nothing tells the
  user. To do: a warning before the merge when little is shared, and a check after it (the
  merged walls against each scan's own); a surer pose for such pairs.
- [ ] The merged model does not get Refine with lasers, a single scan does (found by X7).
  Measure whether it helps a merge.
- [ ] `tests/test_track_spacetime.py::test_the_refined_mesh_is_closer_to_the_truth` failed once
  without a GPU (`OHS_COMPUTE=cpu`) on 2026-11-22, in a run of several test files, and passed in
  the eight runs after it (alone, its file six times, the same set again). **It failed again
  the same day** in another run without a GPU, and this time its words were kept:
  `assert rep["moved"]["p95"] < 0.3` with 0.3011. So without a GPU the refine's largest
  moves sit right at the test's limit and the run varies a little from one time to the next.
  Not loosened. To do: find what varies between two runs without a GPU (the scan the test
  builds, or the refine's numpy twin), and whether it did before 2026-11-22.

## Not worth trying again (measured)

- **The matcher's sigma as a gate on the gap guard** while that sigma carries the matcher's
  hole factor (X1): it is a wider distance guard in disguise, and thin standing walls pay.
- **Hole rims from the laser cloud's ends with today's extraction** (X2): the trim and the
  20-row shortest segment put the ends 0.4 to 4 mm short of the rim.
- **Poses refined against the scan itself** (X3): the error that matters is an offset of the
  camera the scan cannot see (its sigma and its laser crossings do not move with it);
  registering frames against the finished model adds the model's own thin-wall excess each
  round, and leaving out its borders lets the frames slide.
- **Letting the laser-crossing adjustment act more** (X3): the crossings already meet at the
  tracked poses; where its test lets it act, the test is fooled.
- **One shorter or longer band, truncation or voxel in the GPU tracking model for every scan**
  (X4): it moves every recording's camera inward or outward together.
- **Leaving raw depth out near holes, depth steps or where the matcher is unsure, in the
  tracker's detail mode** (X4): the paths slide 5 to 20 mm.
- **A guard on acceleration on the GPU model** at frames 0.1 s apart (X4): it never acts, or
  with a tighter limit makes the hard scenes worse.
- **Fusing weights:** 1/sigma² (+15 to 25 % median); flat up to a knee (no gain).
- **Weak directions:** remapping them; geometrically stable sampling.
- **A narrower disparity search:** one range a frame (2 to 7 % faster; the bar was 10 %).
- **Fill in holes:** the guard per voxel (the bias comes back); the full left-right check
  (+66 ms, removes 2 %); carving free space; stricter matcher settings.
- **Merging:** taking each place from the best scan (even the upper bound loses); cross-scan
  Straighten on whole-frame clouds.
- **A finer tracking model:** a second 1 mm model as a switch slips 7.6 to 9.9 mm; as a
  refine it was no win at +19 % time.
- **The camera path's scale:** symmetric source normals; integrating only within the band in
  the tracking model.
- **The mesh's band:** 2 mm on both sides (7 times the pinholes); fading weights; a cut along
  the view; view directions for opposite sides.
- **Detail mode** switched frame by frame, or after weak frames.
- **Voxels and quality:** coarser voxels for large panels (opens holes); Preview for anything
  measured; Best on the bracket.
- **Hints while scanning** (FP N3): the gain was from moving more.
- **Smoothing "within 2 sigma":** protects no edge.
- **Small engine items:** a constant correction for SGBM; a float32 laser cloud; a coarse
  reference fuse in Straighten.
- **Alignment:** more first guesses.

## Only the real scanner can settle

- The real stereo error: dot density, and how fast errors stop repeating between frames.
  Everything tuned on the simulator rests on it (the look spacing, the tracking filter, the
  gyro prior, the sigma's constants, the 8 px guard).
- Whether holes fill at all with ambient light and a lit background; real holes and chamfers
  against calipers.
- Shiny and dark surfaces: reflections, saturation, laser speckle on paint, exposure and
  emitter levels.
- Motion: blur at hand speed, jerky motion, MJPEG, real 60 frames a second.
- The camera path's check (X0a): its limit of 4 mm off a quadratic over 0.8 s was set on the
  simulator's smooth hand. A real hand that stops or turns sharply may read as a slide and
  warn for nothing; the limit is provisional until real scans are looked at.
- The real IMU: bias, scale, time sync.
- Real lasers: sheets that are not flat, how four lines separate, the line's width at a rim
  (this bounds X2).
- Calibration over time and absolute accuracy on reference parts.
- Real speed with the camera stream.
