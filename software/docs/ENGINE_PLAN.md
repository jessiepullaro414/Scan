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

**Decided by the owner (2026-11-20):**
- **E-D1:** adopt a change when it is better (see the rules above).

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
8. **A merge** adds coverage, never accuracy.
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
  - **Not shown in the app yet:** the server and the pages do not read `warnings` (next).
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
- [ ] **Fused surface hangs past a sheet's free edge,** up to 15 mm out, where the drop behind
  the edge is 60 to 120 mm: 7 to 9 % of the 200-frame car panel's vertices, which is what puts
  its p95 at 1.7 to 2.3 mm while its median is 0.02 mm. The scorecard reports it as
  `part_beyond_edge`. Likely the same fill that closes holes (X1's subject).
- [ ] **GPU, hole plate 120 mm up, seed 0: tracking is lost** (path max 16.3 mm; the Ø25 hole
  then reads 6.9 mm small). Seeds 1 and 2 hold (3.9 mm).

### [ ] X1. A gap guard that goes by the matcher's own doubt (S to M)
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

### [ ] X2. Hole rims and edges from the laser lines (S, then M)
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

### [ ] X3. Poses refined against the finished scan (M to L)
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

### [ ] X4. Tracking on dark, plain and thin parts, GPU path (S, S, M)
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

### [ ] X5. Fine voxels where the part needs them (S to M, then L)
- **Step 1:** after Make mesh, find thin walls (where the opposite-sides rule fired) and
  small round features, and suggest the "Thin sheet metal and small parts" preset with its
  measured gain (FP P4). A hint only.
- **Step 2:** fuse at 0.5 mm only in the flagged bricks (replaying cached depth) and stitch
  them into the 1 mm mesh.
- **Evidence:** 0.5 mm is the only setting that passed P1's stop rule (walls 0.23 to 0.67 mm
  nearer), for 1.2 to 2.4 times the time and 7 GB at 3.3 million triangles (R5).
- **Risk:** pinholes and cracks at the boundary, memory, holes reading smaller.
- **Stop rule (step 2): adopted only if** walls are within 0.05 mm of the all-0.5 mm result
  and the hole within that result's range; time at most 1.25 times the 1 mm run, memory at
  most 1.3 times, open loops at most 1.2 times; smooth scenes within their range.

### [ ] X6. "Refine from the images" inside Make mesh (S)
- **Evidence:** sphere 0.081 / 0.276 to 0.044 / 0.129 mm; panel 0.043 / 0.148 to 0.023 /
  0.093. Never tried on the bracket's edges; round the panel's edge it was worse than a plain
  local fit.
- **Stop rule: adopted only if** flat regions and flatness improve beyond the range across
  seeds; edges, rims and walls get no more than 0.02 mm worse on any seed; Make mesh grows by
  at most 30 %.

### [ ] X7. A merge that improves accuracy (S, then M)
- First measure the merge again on today's engine (the study predates the opposite-sides
  rule). Then register both scans' frames onto the merged model, the part only, table masked
  (X3 across scans).
- **Stop rule: adopted only if** the merged part beats the better single scan on at least 4
  of 5 pairs, and its good surface is at least the union's.

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

- [ ] "Known limitations of the pipeline" in `VALIDATION.md` is out of date: it says the
  lasers are not used in the mesh (Refine with lasers exists), bosses read 0.1 mm small (now
  0.15 to 0.26), walls 0.4 to 0.7 mm too thick (the 1 mm rib is 0.7 to 1.2), 0.35 s a frame
  without a GPU (now 0.6 to 0.73 s), the guard's cost 1 to 7 points (now 0.7 to 2.0), and the
  mesh's p95 0.7 to 1.2 mm (now 0.39 to 0.51).
- [ ] `tools/preset_study.py` places the bracket with the whole scene, so the table rules its
  error (0.17 / 0.49 / 0.69 mm median): not comparable with the part alone (0.14 mm).
- [ ] `ohs/track/volume_gpu.py`'s docstring still says the mesh's model is Open3D's.
- [ ] Every timing on the Radeon PC since 2026-11-10 ran beside `RadeonSoftware.exe` using a
  whole core (MP F8).

## Not worth trying again (measured)

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
