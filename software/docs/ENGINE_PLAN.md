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

**Decided by the owner (2026-11-23):**
- **E-D3: four results have to be fixed.** After reading the comparison with the market
  (`SCANNER_COMPARISON.md`, updated 2026-11-23) the owner said: "I think we will have to fix"
  that "a Ø25 hole over open space reads 1 to 7 mm small, thin standing walls read 0.5 to
  1.3 mm too thick, and the 1.3 m panel came out 1.6 to 6.5 mm wrong in length. A merge beat
  the better single scan on only 1 of 5 pairs". These four go ahead of X9 to X14:
  1. Holes over open space (gap item 1). Tried and not adopted: X1 (the guard by the matcher's
     doubt), X2 (rims from the laser lines' ends). X15 (the rim read from the camera images) reads them within 0.14 mm in simulation, on
     scans not looked at before too; not in the app yet.
  2. Thin standing walls (gap item 2). Tried: X3, X4, X4b, X4c (not adopted), X5 (fine walls,
     a choice per scan).
  3. The length of long plain parts (gap item 7): X8 steps 2 and 3 (tags as anchors): the
     length is held within 0.06 % where the tags act (five of six scans), the rule is missed;
     a setting, off.
  4. A merge that beats the better single scan (gap item 8): X7 measured it; nothing built.
     X18 (one model per scan) measured and dropped; X19 (a pose from tags, held) built as a
     setting, off, its stop rule passed.
  E-D1 still holds for each: a change goes on by default only when it is better on every
  scorecard scene and nothing thin gets worse; a miss stays a setting; a near miss comes to
  the owner with numbers. New experiments for 1, 2 and 4: X15 (holes: the rim read from the
  camera images, alive), X16 and X17 (thin walls), X18 and X19 (merge).
- **E-D4: what a good merge must do is restated** (asked and decided 2026-11-23, "Yes, restate
  it"). X7's rule asked the merged part to beat the better single scan and to hold at least the
  good surface of the two scans together. The second part takes, at every place, whichever scan
  the truth says was right: no real method can (the best rule tried stays 1.2 to 1.3 points
  under it on four of five pairs), and the first compares different surfaces. The rule from now
  on, for every merge study:
  1. On each scan's own seen surface the merge is no worse than that scan (median and the share
     more than 0.3 mm off, within the spread across seeds).
  2. Nothing thin or small gets worse, feature by feature (walls, ribs, bosses, holes).
  3. The merge holds more of the part than the larger scan.
  4. A pair that shares under 0.70 of the part is aligned from an outside reference (tags); a
     merge of such a pair from the shape alone is not promised to be good, and the caution says
     so.
  The old "union" is still reported, as a ceiling, and is no gate. Given up: the promise for
  every single place (it fails today on 2 to 7.5 % of the good surface) and the stronger
  headline. Today's merge passes the new rule on 2 of X7's 5 pairs.
- **E-D5: three choices after the night of 2026-11-23** (asked and decided 2026-11-24; the
  owner took the reviewer's recommendation on each).
  1. Long parts (X8): the never-worse check's limit is tested on fresh seeds first (0.85
     against 0.90, seeds 3 to 5), then the owner decides on switching `straighten_tags` on.
  2. Thin walls (X16): the thickness read from the raw frames goes into the app now, worded
     one-sided ("at most this thick; up to about 1 mm thinner cannot be ruled out"). The
     owner: "We do need it to be correct but we can do your recommendations for now": a
     stopgap; a thin wall's true thickness from a scan (X17) stays a need.
  3. Merge (X19): `align_hold_tags` goes on by default after one more rendered case passes,
     a part turned over with tags on the part itself; with it the Align page's words, the
     guide and a trial in the running app. The case missed on one of three pairs (the
     overrule): the setting stays off until that is mended and the case passes.
     **Decided 2026-11-27, after the weakness was measured (the owner: "Keep it off"):**
     the setting stays off by default and the Align page offers the hold for one
     alignment at a time. Built the same day (X19).
  4. Holes (X15), decided the same day after round 6: the reading from the camera images is
     built into the app's hole tool, as a second answer beside the mesh's.
  5. Long parts (X8), decided the same day after the fresh seeds: `straighten_tags` goes on
     by default at the limit of 0.85, after a check that short scans with tags in view are
     not made worse by it. The check failed (a 0.5 mm wall lost on one scan): the
     setting stays off.

## The error budget today

GPU path, Standard quality, 1 mm voxels, tracked unless said.

| Part | Result today | Depth | Tracking | Fusing (band, voxel, guard) | Edits and fits |
|---|---|---|---|---|---|
| Smooth panel (car panel, sphere) | Median 0.083, p95 0.39 mm (36-frame panel; sphere 0.061 / 0.257) ["Thin walls seen from opposite sides"]. 60-frame panels 0.026 to 0.037 / 0.16 to 0.20 ["Presets"]. | GPU matcher median 0.32, p95 1.41 mm, bias -0.002 mm ["GPU processing"]. | 0.11 / 0.26 mm (median / max, 36 frames). Tracking doubles the mesh's median: 0.037 at true poses, 0.075 tracked ["Deviation map"]. | Every second frame: +18 to 22 % median. 1.5 to 2 mm voxels: p95 beyond the spread. | Taubin: median -10 %. Refine with lasers changes nothing unbiased. Flat-within reads 0.14 to 0.48 mm on truly flat faces (stale, 2026-11-12). Flush: mean within 0.04 mm. A gap: not measurable. |
| Bracket (edges, Ø14 bosses, Ø16 shaft) | Median about 0.14 mm; 11.6 to 31.3 % more than 0.3 mm off (median 12.6 %); mean offset +0.008 mm; covered 94.5 to 95.6 %. | True depth and true poses: 6.5 % over 0.3 mm. Matcher depth and true poses: about 10 %. So the depth is about 8 of the 12.6 points ["Where a tracked scan's shape errors come from"]. | Adds 2 to 3 points in the median and makes the bad seed (31 %). Best quality is worse than Standard here (0.100 against 0.089 mm). | The gap guard costs 0.7 to 2.0 points of coverage and removes bias. | Bosses read Ø 0.3 to 0.5 mm small on every seed; with true depth they are right (-0.02): the depth and the guard. Shaft right with tracking only because two errors cancel. Upright face at worst +0.33 mm. |
| Thin sheet lying flat | A 0.5 mm plate reads 0.489 to 0.504 mm with every setting ["Presets"]. | not the limit | GPU path: 1.1 to 3.8 mm at the last frame (MP F14). | Remove table keeps 0.3 mm and up; 0.1 mm goes silently ["A thin part lying flat"]. | A merge with the plate turned over reads 1.0 mm as 1.42 and 0.5 mm as 1.28. |
| Thin sheet standing, both sides seen | 2 mm flange 2.26 to 2.61, 1 mm rib 1.66 to 2.20 mm. Walls 0.5 / 1 / 2 mm read 1.70 / 1.71 to 1.90 / 2.53 to 2.66 at 1 mm voxels and 1.03 to 1.08 / 1.32 to 1.45 / 2.18 to 2.43 at 0.5 mm. | small | +0.3 to 0.6 mm (flange), +0.7 to 1.2 mm (1 mm rib) over true poses: 0.77 times how far out the cameras stand, r = 0.98. | Mesh's floor at true poses: 2.10 to 2.18 / 1.24 to 1.28 mm. | |
| Holes | Ø25 over a far dark background: 1.1 mm small tracked, 1.3 at true poses. A plate 120 mm up: 22.0 to 22.1 tracked in the first study, 18.1 / 22.1 / 21.8 on the scorecard's three seeds (seed 0 lost tracking). Near a floor (the scorecard's baseline): Ø25 +0.58 to +0.75 at true poses and -0.20 to +0.47 tracked, Ø10 +0.28 to +0.60 at true poses and -0.20 to +0.07 tracked. Ø6 everywhere and Ø10 over a far floor: closed, refused. | All the fill is the matcher's: 1.3 mm median reach ["Holes filled in"]. | On the plate tracking takes another 1.7 to 3.1 mm. | The rim fuses as a shoulder: the reading goes 26.7 to 24.3 mm between 0.15 and 2 mm below the face. Coarser voxels close Ø25 by 1.4 to 2.4 mm. | The ± does not cover the bias. A gap of 2 to 6 mm is bridged, not offered. |
| Large part (door, 946 mm) | Stale (2026-11-10): median 0.07, p95 0.79 mm. Today's 141-frame stretches: 0.039 to 0.052 / 0.35 to 0.43. | as the panel | Loops do not mend sliding (up to 98 mm) ["Straighten for large drift"]. Overall length of a 1.3 m plain panel: 0.12 to 0.50 % off, the passes 2 to 11 mm apart along it ["The overall size of a large part"]. | The 768 mm tracking cube moves about 40 times on a 1.3 m scan; a cube twice the size gives the same result, so it is not what limits the length. | not measured |
| Detailed casting (carburettor) | Stale (2026-11-10): mesh median 0.80, p95 2.7 mm. Today's stretches: 0.157 to 0.230 / 0.69 to 1.04, coverage 0.68 to 0.70. | Detail mode tracks raw depth, so the camera-fixed dot error comes back. | The largest of any scene. | 0.5 mm voxels: p95 -0.11 to -0.38. | not measured |
| Dark or speckled surface | GPU path, last frame: 1.1 to 3.8 mm, against 0.61 mm on the plain bracket (MP F14). The mesh against the truth: not measured. | Dots about a fifth as bright. | ICP from the true pose against a one-frame model drifts 2.6 to 2.7 mm. | not measured | CPU path after D7: 42 mm and 0.9 mm (two seeds), plates 5.7 mm. |

## Where it falls shortest

Ranked by the gap between what a user needs and what the engine delivers.

1. **Mounting holes.** Needed: diameter and position within 0.1 to 0.2 mm. Today: 1.0 to 6.9 mm
   small over open space (the car panel 0.97 to 1.37; the plate 120 mm up 2.9 to 6.9 tracked,
   one seed having lost tracking); +0.58 to +0.75 mm near a floor at true poses (-0.20 to +0.47 tracked); Ø6 to 10 over open space not measurable;
   gaps between panels not measurable. Short by 5 to 15 times.
2. **Wall thickness with both sides seen.** Needed: 0.1 to 0.2 mm. Today walls read +0.54 to 1.26 mm too thick tracked (+0.27 to 0.50 over true poses; the sheet's 1 mm rib +0.66 to 1.21); a 0.5 mm wall reads 1.7 mm. Raw frame points read every wall within 0.07 mm at true poses (E-D3, 2026-11-23), so what remains is how tracking places the two sides' frames. Short by 3 to 10 times. Lying flat is fine.
3. **Tracking on dark, plain and thin parts (GPU path).** Needed: under about 0.3 mm
   everywhere, because a whole region shifted is invisible to the sigma. Today 1.1 to 3.8 mm,
   and one bracket seed in five reads 2.5 times worse.
4. **Small round features.** Needed: Ø within 0.05 to 0.1 mm. The Ø14 bosses read 0.3 to
   0.5 mm small on every scan, outside any ±.
5. **Edges and detailed parts.** 10 to 13 % of the bracket is more than 0.3 mm off; the
   carburettor's p95 is 0.7 to 1.0 mm at 70 % coverage.
6. **Flatness of a flange.** Needed: 0.05 to 0.1 mm. The label reads 0.14 to 0.48 mm on truly
   flat faces (stale).
7. **Overall size of large parts.** Needed: about 0.05 %. Today 0.12 to 0.50 % on a 1.3 m
   plain panel in three passes (1.6 to 6.5 mm), with no warning (X8 step 1, 2026-11-23). Short
   by 2.4 to 10 times. With tags 300 mm apart along the part and the setting `straighten_tags`
   on (off by default): 0.018 to 0.048 % (X8 step 2).
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
- **Evidence:** tracked walls are too thick by 0.77 times how far out the cameras stand (about twice the cosine of the faces' incidence: the geometry of the hand path, not a constant to calibrate; E-D3, 2026-11-23); one
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
  recording the camera stands 0.19 to 0.25 mm out. (In question since 2026-11-23: the offset depends on how the path is placed, 0.00 to 0.07 mm when placed by the camera centres, and sheet seed 1 has 0.07. What does not depend on that is the relative pose of the frames of a wall's two sides: the raw frames read every wall within 0.07 mm at true poses and 0.3 to 1.0 mm too thick tracked. Why tracking places the sides apart is being tested; `docs/ENGINE_PLAN.md`, "An outside review", third round.) With the truth's value of that offset taken
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

### [ ] X8. Large parts: measure the overall size first (M): step 1 measured, fails; step 2 (anchors) built, a setting, off: a near miss; step 3 (tags closer) fails
- A 1.2 to 1.5 m plain panel with Ø10 holes at both ends, scanned in passes: the end-to-end
  distance, the bow, the slide between passes. Only if it fails: tags on the table as anchors
  in Straighten (`ohs/align/tags.py` exists).
- **Stop rule (anchors): adopted only if** under 0.05 % end to end on all 3 seeds with the
  door no worse than its spread.
- **Step 1 measured 2026-11-23: fails** (`VALIDATION.md`, "The overall size of a large part";
  `tools/large_part_study.py`; set-up and rule fixed beforehand).
  - A 1.3 m plain panel, 3 passes, 3 seeds, Make mesh on the graphics card at Standard: end to
    end -0.135 / +0.502 / +0.121 % from the mesh's edges (-1.75 / +6.53 / +1.58 mm; the need
    is 0.65 mm). The camera path is 0.11 to 0.49 % off. Seed 2's edge reading has 1.08 mm of
    its own error.
  - Cause: drift along the plain length within a pass, most on the return pass (up to 8 mm
    over 1.1 m); the passes then sit 2 to 11 mm apart along the panel and the mesh averages
    them.
  - Not the cause: scale (true poses read within 0.1 mm on two seeds), bow (under 0.015 mm
    along the normal), the 768 mm tracking cube (it moves 40 times a scan, but a 1536 mm cube
    gave the same on seed 0), the mesh model's brick pool (48k of 65k).
  - Straighten finds 48 loops but no gap to close (0.01 to 0.12 mm along the normal), and the
    laser crossings already meet. Nothing warns after the scan: the path check reads 1.1 to
    1.3 mm, the mesh's median is 0.023 to 0.032 mm.
  - The Hole tool cannot read the Ø10 holes 14 mm above the table, even at true poses (small
    things below), so end to end is judged from the edges.
- **Step 2 built and measured 2026-11-23: a near miss, not adopted, a setting** (`[process]
  straighten_tags`, off; `VALIDATION.md`, "Tags as anchors on a large part").
  - **The wall question, answered and mended 2026-11-27** (`VALIDATION.md`, "The thin wall lost
    with the tags on: the laser crossings, not the tags"). The 0.5 mm wall that vanished on a
    short tagged scan was removed by the laser crossings that ran after the tags, not by the
    tags and not by the second fuse: tags alone give a path 0.28 mm off the truth with the
    wall there (74 % covered; 49 % without tags); the crossings then passed their check by
    0.003, moved the path to 0.66 mm and pulled the wall's sides through each other. On
    scans the tags hold the crossings never helped (16 saved scans: refused on 15, worse on 1).
    Adopted: Straighten does not run the laser crossings on a scan the tags hold. Nothing
    changes with the setting off. They still act on walls without tags when their check
    passes.
  - **Why the crossings miss at the true poses, found 2026-11-27** (`VALIDATION.md`, "Why the
    laser crossings miss at the true poses: two causes"). (1) Crossings that are none: a
    curve's direction comes from two neighbouring samples and tilts by 30 degrees and more
    where a line bends over an edge, so nearly parallel lines of neighbouring frames pass for
    a crossing that "misses" by the distance between them (45 of 402 on a walls recording).
    A line fitted over a short stretch removes them (`CurveParams.tangent_fit_mm`). (2) The
    projector's dots pull the laser line's centre, alike in neighbouring frames (correlation
    0.8 one frame apart); rendered without dots the laser points are 2.5 times as exact
    (0.086 against 0.212 mm) and the crossings meet (9 % miss by over 0.25 mm against 59 %).
    The held-out test takes (2) for drift. The fitted line makes that worse, not better: with
    it a walls recording at its TRUE poses is moved by 2.85 mm, so it is a setting and OFF;
    nothing changes by default. Open: laser frames without the dots (hardware and firmware),
    a held-out test that a repeated error cannot pass, the adjustment without a reference
    mesh (it leaves drifted paths worse more often than better).
  - **Laser frames without the dots: not as built (read from the code and the parts list,
    2026-11-27; nothing tried on hardware).** The Pico holds each emitter at a steady
    brightness (PWM at 10 kHz) that the PC sets once when a scan starts (`EMIT v h d`); a
    recording stores one emitter state, not one per frame. No wire joins the camera and the
    Pico: the camera runs by itself over USB (46 to 55 frames a second on Windows) and the
    Pico does not know when a frame is taken. The engine expects dots and lines in every
    frame. The cameras' shutter is global, so a frame taken between two switches would be
    all dots or all lasers. Three routes, none started:
    1. A sync wire. The OV9281 sensor has a strobe output and a frame-sync input; with
       either on a pad the Pico could switch between exposures. **The board shows no such
       pad (the owner's photos of both sides, 2026-11-27; board marked `3D-GS800P`,
       `2022.1.30 A`).** The only labelled pads are the four USB ones (5V, DM, DP, GND).
       Some part places are empty and unlabelled: an LED place and a two-pad place beside
       the bridge chip, two six-pad places by the regulators, a few two-pad places on the
       back. What they carry cannot be told from a photo; the sensors' own pins are under
       the lens holders; no data sheet for the board was found. Finding a frame signal
       would mean probing unlabelled pads with a scope while the camera streams. Not
       pursued.
    2. No wire: the Pico alternates on its own clock at the camera's frame period, the PC
       tells from each picture what it holds (dots, lasers, or a mix) and steers the
       Pico's timing. Needs a timed command in the firmware, the frame's kind stored per
       frame, and the real camera to show that USB timing allows it.
    3. With the dots on: no mend found. Stereo cannot check the horizontal line (it runs
       along the baseline), and that is the line the dots pull most.
    What alternating costs either way: half the frames give depth and tracking, half give
    laser points (about 25 each a second), and a laser frame has no depth of its own, so
    its camera position must come from the frames beside it.
  - **Frames in turn, measured in the simulator 2026-11-27** (`VALIDATION.md`, "Dot frames and
    laser frames in turn"; nothing built). Gain: on the walls nine in ten laser samples lie
    within 0.07 to 0.08 mm of the true surface without the dots (0.16 to 0.19 today); the
    speckled bracket gains little. With lasers in every second frame only there are as many
    usable crossings as today, and at the true poses the adjustment's check stays far from
    its limit (lowest 1.13; today 0.852 and 0.870 against 0.85). Cost: a laser frame placed
    between two dot frames is off by 0.07 to 0.08 mm at 30 frames a second and 0.03 mm at
    the camera's 50; with that the walls' laser samples are still better than today's (nine
    in ten within 0.10 to 0.12 mm at 30 a second). Dot-only pictures track and mesh as
    today's do. Standard quality already uses every second frame of the real camera, so
    the laser frames would be the ones it skips; Best quality would have half its frames,
    which costs coverage on thin walls (faces covered 0.30 to 0.41 from 0.48 to 0.68 on
    one walls scan at 15 frames a second). Not measured: tracked poses for the placing,
    Refine with lasers, a whole Make mesh, the real camera. The camera board shows no sync
    pad (route 1 above), so what is left is the route without a wire (route 2); whether USB
    timing allows it can only be shown on the real scanner.
  - With 20 tags on the table 300 mm apart: the mesh's length -0.042 / +0.048 / +0.018 %
    (without: -0.164 / +0.197 / +0.054), the camera path -0.052 / +0.060 / +0.003 % (without:
    0.18 to 0.45), the passes' slide 0.34 to 1.14 mm RMS (without: 1.4 to 11.4).
  - The rule as put before the runs: mesh met on all three, the path misses on two by 0.002
    and 0.010 %, the slide misses 0.65 mm RMS on two seeds (0.72; 0.77 and 1.14).
  - What is left is the length between the ends: it is held only through frames that see two
    tags spaced along the part (a third of the keyframes at 300 mm; none at 600 mm, where
    0.2 % stays).
  - A scan without tags is unchanged, bit for bit. Make mesh takes about 76 s longer when the
    tags act.
  - **Step 3 measured 2026-11-24: fails the rule; the setting stays off** (`VALIDATION.md`, "Tags
  150 mm apart along one side"). Nine tags along the near side 150 mm apart and five across
  each end, 19 in all; the same seeds and rule; nothing tuned.
  - Seed 0 meets it (mesh -0.014 %, path -0.021 %, slide 0.59 mm RMS). Seed 1 misses on the
    slide alone (+0.007 %, +0.007 %, 0.88 mm against 0.65).
  - On seed 2 the never-worse check refused the tags (0.869 on one split against the limit
    of 0.85), so the scan stayed as tracked: path -0.126 %, slide 2.35 mm. That scan started
    nearly right, and the tags could not show a clear enough gain on the frames set aside.
  - Where the tags act the path is within 0.021 % (0.27 mm over 1.3 m), better than step 2's
    0.003 to 0.060 %. A row on one side still puts two spaced tags into only 28 to 30 % of
    the keyframes: only the near pass sees it.
  - **The check's limit, measured 2026-11-24** (the tag block alone, on the processor, the six
    tagged scans; `VALIDATION.md`, "The never-worse check's limit"): from a limit of 0.90 the
    tags act on seed 2 and its path comes from -0.126 % to -0.009 % (the slide to 1.37 mm
    RMS, still a miss). Limits up to 1.00 were never worse than no tags on four emulated
    harm cases (a tag moved 5 mm, three times the scatter, a slipped row, half the
    detections); no check at all was. The check's ratio follows the slide mended (r = -0.96)
    more than the length (r = -0.68). Not adopted: 0.90 was found on the scan it mends.
  - **Open, in this order:** (1) the limit at 0.90 on fresh seeds (3 to 5) of both layouts,
    judged by the same rule, with the harm cases rendered; a tag moved 5 mm is not always
    left out (two scans kept it and lost most of the gain). (2) The slide of 0.7 to 1.1 mm RMS that stays
    on three of the five scans where the tags act. (3) The fit given the tags' known spacing
    (a printed strip; X22): a distance term in `ohs/track/tag_anchors.py`, about 40 lines
    and tests; it would also give the scale an outside reference.
  - **Fresh seeds measured 2026-11-24** (3 to 5, step 3's layout, the limit at 0.85 and at
    0.90; `VALIDATION.md`, "Fresh seeds for the limit"): 0.85 refused the tags on none, so
    both limits gave the same runs: 0.90 is neither supported nor refuted and **the limit
    stays 0.85**. The length is mended on all three (mesh within 0.005 %, path within
    0.014 %, from 0.24 to 0.73 % off); the passes' slide misses on all three (0.77, 1.02,
    1.41 mm RMS against 0.65). Over the nine tagged scans: tags acted on eight, mesh length
    met on nine, path on six of eight, slide on two, the whole rule on one.
  - **Decided 2026-11-24 (E-D5): on by default at 0.85, after a check** that short scans
    with tags in view (the walls and sheet with table tags, the tag-hold test's bracket
    scans) are not made worse by it. **The check failed the same day: the setting stays
    off** (`VALIDATION.md`, "Short scans with tags in view"). Of 13 scans the tags left 8
    as they were and made 4 better or the same; on the standing walls, seed 2, they made
    the path and the thicker walls better and the 0.5 mm wall all but disappear from the
    mesh (coverage 49 % to 2.5 %), with no warning. **Open, first:** which step lost the
    wall, the corrected poses (two sides placed too close for a 1 mm mesh) or the second
    fusion Straighten makes whenever it acts, loops included; then a warning when a thin
    wall found before Straighten's fusion is gone after it. The length is right wherever the tags act
    and nothing gets worse; the passes still sit 0.6 to 1.4 mm RMS
    apart along the part, which the rule's part (d) does not allow. Not measured: what
    that slide costs features along a part. On five of six tagged
    scans it brings the path from 0.18 to 0.47 % down to 0.003 to 0.060 %, on the sixth it
    changes nothing, and a scan without tags is the same bit for bit; by the rule as
    written it is a miss.
- **Step 2 as it was planned: tags as anchors in Straighten.** A few AprilTags (20 to 30 mm) on the
  table along the part, or on the part, at most about 300 mm apart so that one frame (about
  470 mm along the panel) sees two neighbours.
  - Each tag is a node in Straighten's pose graph; each keyframe that sees it adds an edge
    from that one frame's own detection (`MarkerTracker`, the corner triangulation behind
    `ohs/align/tags.py`'s `tag_map`, per frame: a median over the drifted world would bake the
    drift in). The never-worse rule stays.
  - Expected: the slide between passes from 2 to 11 mm to well under 1 mm (the same tag seen on
    every pass), and the drift within a pass held by neighbours linked within single frames
    (near 0.1 mm over 1.3 m if a 300 mm link is good to about 0.05 mm; not measured).
  - It will not mend: the holes that come out closed, the edge reading's own error, a scan
    without tags.
  - A scan without tags is unchanged by construction; the door has none.
  - Size M to L: tags in the scene from existing decals (S), observations per keyframe (S),
    nodes, edges, weights, the never-worse checks and the report (M), the study on 3 seeds and
    the door's spread (M).

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

### [x] X15. A hole's rim read from the camera images (M): in the app's Hole tool since 2026-11-26, as a second answer (simulation only; real images open)
E-D3 item 1. `tools/hole_rim_study.py` (a study tool; nothing in the engine uses it); notes and
every run in `D:\OpenHandScan-work\scratch\ed3\holes_looks`; `VALIDATION.md`, "A hole's rim read
from the camera images".
- **Why the images:** depth cannot open a small hole over open space (the two cameras see
  different things through it), and the mesh closes over what depth leaves. The rim itself is
  a sharp step in both raw images.
- **The reading:** at a click, in about 20 recorded frames that saw the hole: the face's
  plane from that frame's depth, the rim's step along radial lines in both cameras, one model
  of the hole per frame (the entrance circle, and the exit circle a wall's height below it),
  the median over the frames. The diameter is measured in each frame's own coordinates, so
  it does not need the camera path; the position does.
- **Kill rule, fixed beforehand:** dropped if at true poses any Ø25 or Ø10 is off by more
  than 0.3 mm, or tracked by more than 0.5 mm on a scan whose path check does not warn; a
  scan that lost tracking must warn or refuse. A refusal is no failure, a quiet wrong answer
  is. Scenes: the scorecard's hole plates and car panel (11 recordings), and study plates of
  1 and 5 mm at 10 and 120 mm above the floor and one above a bright floor, 3 seeds each.
- **Three rounds.** One edge per line: dropped (the plate near a lit floor read 0.6 mm
  small). A model of the hole per frame: within 0.2 mm everywhere but one bright-floor click
  (-4.65 in silence). Round 3 (a seed of either sign, a rule on the frames' centres, the
  two cameras held to agree, the thickness free or given):
  - **Dots off: alive.** At true poses 59 holes answered, all within 0.2 mm, 53 within
    0.1 (57 with the thickness given); twice the ± covers 177 of 177 clicks. With the
    graphics card's tracking every hole of the 11 recordings is within 0.13 mm. The bright
    floor is refused on all 9 holes.
  - **Dots on: dropped by one case,** the 1 mm plate 10 mm above its floor, seed 2: Ø10
    +0.49 mm (its own ± said 0.4 to 0.5). Every other Ø25 and Ø10 it answers is within
    0.21 mm; the Ø10 near a floor is mostly refused.
  - **Dots on, the background far** (the plates 120 mm up, the panel): every hole answered
    is within 0.13 mm at true poses and within 0.06 mm with the graphics card's tracking,
    Ø6 and Ø10 included. Today's hole tool reads these Ø25 1.0 to 6.6 mm small and gives
    nothing for Ø6 and Ø10. This is E-D3's case, and it needs no new hardware.
- **One wrong answer outside the rule, in both arms:** on a study scan whose tracking (on the
  processor) had slid by millimetres, the click for the Ø6 landed on the Ø25 and read it:
  +18.9 mm, with the path warning and "position unsure" and nothing about the diameter. The
  reading has to be checked against the mesh's own hole or dent at the click. Not built.
- **What the changes did:** the centre rule turned the bright floor's quiet -4.65 into a
  refusal and caught four more wrong readings on slid scans (it also refused a few good
  ones). Holding the two cameras to agree mended nothing and cost answers. A given
  thickness tightens the dots-off reading a little. Leaving the dots-off frames out of
  tracking: no clear change on the processor's path (not run on the graphics card).
- **Position:** 0.01 to 0.16 mm at true poses, 0.13 to 0.96 mm with the graphics card's
  tracking (it says "position unsure" when the frames put the centre more than 1 mm apart).
- **Rounds 4 and 5 (2026-11-24): two guards built, neither holds yet.** A check that the rim
  read is the mesh's own hole or dent at the click, and a gate that answers with the dots on
  only over a far background; judged by the rule above plus "no hole other than the clicked
  one" and "at least 90 % of the far-background Ø25 and Ø10 answered".
  - The wrong-hole answer is traced: the study clicked at the true position on a scan whose
    path had slid 85 mm, so the click lay on the other hole's rim. With the click made on
    the scan's own mesh, as a user's is, no reading names another hole.
  - The check against the mesh refuses what it should pass (round 5, dots off: 102
    refusals, none of a wrong reading): a small hole the mesh closed is a dent 0.5 mm deep.
    Dots off stays right on every answer but answers 19 of 23 far-background holes.
  - The gate does not keep near floors out: in round 5 two Ø10 holes 10 mm above a floor
    pass it and read +0.35 and +0.32 mm, which drops the gated dots-on arm.
  - Seen on the rows, not yet a rule: every reading that broke the rule carried a ± of its
    own of 0.21 mm or more (0.21, 0.26, 0.41, 0.50); the good ones carry 0.10 to 0.18.
- **Round 6 (2026-11-24): passes, with the dots on too.** The gate and the mesh finder are
  out. The reading refuses when its own ± is above 0.20 mm (the limit fixed beforehand from
  1,774 readings of rounds 3 to 5) and when the click lies more than 3 mm outside the read
  circle. Judged on scans not looked at before (seeds 3 to 5 of plates near and far from
  the floor, and a new plate with a second hole 3 mm beside the clicked one):
  - dots on: every far-background Ø25 and Ø10 answered (21 of 21), 36 of 39 near-floor
    holes answered and none more than 0.3 mm off, every answered click within 0.14 mm,
    twice the ± covering all 173; dots off: 69 of 69 holes, all within 0.2 mm;
  - beside a neighbour no click read the other hole;
  - the 11 scorecard recordings (they informed the limit): dots on 21 of 23 holes at true
    and at tracked poses, all within 0.2 mm; the open-space plates and the panel -0.08 to
    +0.00 mm, where today's tool reads 1.0 to 6.6 mm small.
  - Left: the fresh plates are at true poses only; the near-floor Ø10 with the dots on is
    answered by 3 to 7 clicks of 9; real images.
- **Decided 2026-11-24 (E-D5): built into the app's hole tool.** **Step 1 done 2026-11-26:**
  the engine module `ohs/measure/rim.py` (`read_hole`: round 6's reading, no switches; it
  reports the entrance rim in the face's plane) and the study tool calling it. All 666
  clicks of round 6 run again through the module are identical to the study's in every
  field (diameter, ±, refusal, hole named, frames used); its tests pass on both paths. About
  0.6 s a frame on this processor, 12 s for 20 frames. Step 2 (the fit route, the hole
  tool, the guide) followed.
- **Step 2 done 2026-11-26** (`VALIDATION.md`, "Holes from the camera images in the app"):
  the Hole tool's second answer "From the camera images", through a route of its own
  (`GET` and `POST .../hole-images`; a fit answers in under a second, this takes 5 to
  15 s), `[process] hole_from_images` on. The stop rule is met through the route on 81
  clicks of nine app-made scans: every answer names its own hole and is within 0.08 mm; 27
  of 27 far-background Ø 25 and Ø 10 clicks answered, where the mesh reads the Ø 25 1.1 to
  6.6 mm small and gives nothing for Ø 10 and Ø 6; near the floor the Ø 10 is refused (the
  mesh is right there); the blind bore is refused. Offered only with a graphics card (the
  processor's matcher passes on the same scans, but a scan made wholly on the processor
  was not run), not for merged scans, Markers mode or Preview quality (not measured).
- **Left:** real images (gloss, chamfers, burrs); scans made wholly on the processor;
  Markers mode; Preview quality; the reading is not kept with the scan as a fitted shape
  is; the known-bias table of Compare with CAD still speaks of the mesh's hole only.
- **Waits for hardware:** the dots-off reading. The board switches the projector by a serial
  command, not in step with the cameras; it needs frames without dots, a short exposure
  (under 0.5 ms; today automatic, about 7.8 ms) and perhaps a light.
- Not verified: real images (gloss, chamfers, burrs, MJPEG, real blur and light); oblique
  bores; a bright background that is read rather than refused; tracking of the study scenes
  on the graphics card; the cause of the +18.9 case is inferred, not traced.

### [x] X16. A thin wall's thickness read from the raw frames (S to M): in the app since 2026-11-26, with one-sided words
E-D3 item 2. `tools/wall_thickness_study.py` (a study tool; nothing in the engine uses it);
`VALIDATION.md`, "Thin walls: the raw reading, and where the error is made".
- **The reading:** at a click on a wall seen from both sides, each face is a plane fitted to
  the raw depth points of the frames that see it; the thickness is the distance between the
  two planes. The faces are told apart by their normals, never by a cut at the middle.
- **At true poses** it reads every wall of 0.5 to 2 mm within 0.07 mm (walls and sheet, 3
  seeds each), where the 1 mm mesh reads +0.12 to +0.84 mm.
- **Tracked** it reads +0.29 to +0.99 mm too thick (one wall -0.01), where the mesh reads
  +0.25 to +1.26: the mesh's share (0.1 to 0.7 mm) goes, what tracking adds stays.
- **Its own ±** (0.03 to 0.09 mm, from resampling the frames) does not hold tracking's error:
  twice the ± covers the truth on 9 of 14 walls at true poses and on 1 of 15 tracked. So the
  reading cannot be shown as "t ± s"; honest wording is one-sided: "at most this thick; the
  scan cannot rule out up to about 1 mm thinner".
- **Decided 2026-11-24 (E-D5): into the app's measuring now,** with the one-sided statement.
  **Done 2026-11-26** (`VALIDATION.md`, "The wall-thickness reading in the app"):
  `ohs/measure/wall.py` and `ohs/measure/frame_depth.py` (matcher depth near a click, to be
  shared with the hole reading of X15); `GET` and `POST .../wall`; the viewer's Fit tool has
  "Wall" (T). `[process] wall_reading` is on. Offered only with a graphics card (the
  processor's matcher was no nearer than the mesh on 5 walls), never for a scan tracked by
  markers or a merged scan. The stop rule below is met on the graphics card's path: nearer
  than the mesh on all 15 walls of 2 mm or less, never under the truth, one side unseen
  refused. The tracking cause stays open in X17: the reading can only say "at most".
- **Stop rule for the app's version: adopted only if** on the scorecard's walls and sheet it
  is nearer the truth than the mesh's reading on every wall, refuses where one side is not
  seen, and never reads a wall thinner than the truth by more than 0.1 mm.

### [ ] X17. Thin walls: why tracking places the two sides apart (M): the cause located 2026-11-24, six remedies and Markers mode tried, none alive
E-D3 item 2. Notes and every run in `D:\OpenHandScan-work\scratch\ed3\walls_looks`
(`NOTES.md`, `ladder.txt`, `profile_fins-s0.md`, `candidate-D.md`); `VALIDATION.md`, "Thin
walls: the raw reading, and where the error is made".
- **A ladder of references** (the standing walls, graphics card; "separation": how far apart
  tracking places the frames of a wall's two sides, 1 mm wall, exact depth, seeds 0 / 1 / 2):
  - tracked against a frozen 2 mm map filled from exact depth at the true poses: -0.03 /
    +0.11 / -0.03 mm;
  - tracked against the engine's own evolving map, integrated at the true poses: -0.07 /
    +0.04 / -0.11 mm;
  - the loop as the product runs it (each tracked frame goes into the map it is tracked
    against): -0.67 / -0.12 (that run slipped) / +0.61 mm. With the matcher's depth: +0.49 /
    +0.43 / +0.43 mm.
- **So:** the map's representation is not the cause (a one-sided 2 mm map holds the sides
  within about 0.1 mm when the poses that fill it are right), and neither is a lack of depth
  accuracy. The error is made in the loop, where a frame's pose error goes into the map the
  next frames are tracked against. With exact depth its sign follows the seed; with the
  matcher's depth it is "too thick" every time.
- **Not a shared bias times a weak gain:** a disparity offset of -0.10 to +0.10 px added to
  exact depth moves the loop's separation from -0.59 to -0.76 mm (about -0.85 mm a pixel)
  round an intercept of -0.67 at no bias; without the feedback the same offsets leave it at
  about -0.07.
- **The matcher's depth against a right map** separates the sides by +0.36 mm (frozen map
  from exact depth), and by +0.07 against a map made from its own depth at true poses: depth
  and map have to carry the same edge errors.
- **Every 2 mm map ray-casts its surface 0.10 to 0.17 mm in front of the true one,** also
  when filled from exact depth at true poses: a bias of the map's own, shared by all frames.
- **A refinement on the raw frames after the scan would return its start.** Point to plane
  between the raw frames (40 frames, 445,000 pairs), profiled along the separation: with the
  pairs recomputed the cost is flat within its noise over ±0.8 mm; with the pairs fixed at
  the start poses the minimum is at the start, from the truth and from the tracked poses
  alike. The standard error of 0.035 to 0.07 mm reported earlier measured the pairing's
  stiffness, not information about the truth (corrected 2026-11-23).
- **Dropped:** a tie seen from both sides. A matte ball halves the separation on the nearest
  wall and over-corrects another (a 2 mm wall 0.17 mm too thin on one seed); two balls are
  worse; tags on the table do nothing on a 4 s scan (too few keyframes see each tag).
- **Not indicated:** a two-sided tracking map (candidate D, designed, L: 3 to 5 days). The
  ladder argues against it.
- **Where the offset enters** (2026-11-24, each frame's pose error across the 1 mm wall, from the
  saved runs): with the matcher's depth the first side holds within about 0.1 mm (seeds 0 and
  1); the frames over the walls' ends scatter by 0 to 1.1 mm from one frame to the next; the
  second side then settles 0.4 to 0.5 mm away. It is first seen by those weakly held frames,
  and later frames register to what they put into the map. The frames over the ends are
  weakly held on every rung, right maps included (+0.4 to +0.85 mm); with a right map their
  error does not carry on. (Seed 2 also drifts on its first side; with exact depth the
  offset is there from the first frames.)
- **Six remedies tried on the loop, matcher's depth, seeds 0 to 2; rule fixed beforehand (the
  1 mm wall's separation at most 0.2 mm on all three, the path no worse, no frame lost):
  none alive.** Today: +0.49 / +0.43 / +0.43 mm.
  - `start_together` (X4b): +0.52 / +0.43 / +0.42; the camera path is better on all three
    seeds (median 0.53 / 1.52 / 1.50 to 0.33 / 0.49 / 1.09 mm), the walls are not.
  - `slip_check_mm` (X4c): never acts here. `fuse_lag` (X4c): +0.52 / +0.36 / +0.31, and seed
    1's path gets worse (largest error 3.9 to 8.5 mm).
  - Weakly held frames not fused: nearly every frame of this scene counts as weakly held;
    tracking is lost on two seeds.
  - The map's ray cast made 0.15 mm deeper (its measured bias taken out): +0.89 / -0.11 /
    +0.06. A strong lever with the seed's sign, not a cure.
  - The engine's hold of a weak step: +0.45 / +0.43 / +0.38.
- **What this says:** the walls themselves cannot hold the pose across the turn, and nothing
  that only changes how frames enter the map makes them. What is left is a pose that does not
  come from the walls.
- **Markers mode measured 2026-11-24: not a remedy** (six 25 mm tags on the table, walls and
  sheet, 3 seeds each; rule fixed beforehand: raw separation within 0.2 mm on every wall of
  2 mm or less, no frame lost, the mesh no worse than at its true poses + 0.15 mm). All three
  parts missed: frames lost on all six recordings (4 to 29); the two sides come out 0.10 to
  0.47 mm too CLOSE on five of six (thinner than the truth, where markerless reads +0.25 to
  +0.98); about 2 to 3 tags in view, their corners scattering 0.3 mm a frame, all in one
  plane. And in Markers mode the mesh has no opposite-sides rule (small things below).
- **Where this leaves thin standing walls:** no remedy alive in tracking. What works today:
  the part laid flat (a 0.5 mm plate reads within 0.01 mm); the raw-frame reading (X16) as an
  upper bound; "Make only the thin walls finer" (X5) for the mesh's share. **Open, each with
  its own rule before it is run:** (1) the two sides tied through the part's own far ends in
  a longer scan (round the part twice, Straighten's loops joining the rounds; the study
  scans are 4 to 8 s); (2) tags on the part beside the wall, seen from both sides; (3) the
  map's ray-cast bias taken out together with `start_together` (each moved the result;
  never tried together); (4) Markers mode with `thin_walls` on and 12 or more tags.
- Not verified: the exact scene as the tracking model (the harness failed); offsets other
  than disparity; the sweep and the profile on seeds 1 and 2; the path without a graphics
  card. One run slipped on each of two rungs. A box scene with three standing walls is near
  the worst case for this.

### [x] X18. A merge from one model per scan (S, then M): measured 2026-11-23, dropped
E-D3 item 4, judged by E-D4. `tools/merge_combine_study.py`; `VALIDATION.md`, "A merge from one
model per scan"; data in `D:\OpenHandScan-work\scratch\ed3` (`merge.md`, `merge_looks`, `gpu1`).
- **The idea:** today's merge fuses every frame of both scans into one model, so a place seen
  poorly by one scan drags the same place seen well by the other. Fuse each scan alone and
  combine the two models, by each scan's sigma or with an equal say, keeping a wall's two faces
  apart where the scans saw opposite faces.
- **Weighting by the sigma: dropped** (first test, on the saved meshes, processor only). It
  failed its own test on 7 of 27 cases. The sigma is a loose upper bound and a weak ranking:
  where it says 0.3 to 0.5 mm the error's median is 0.08 to 0.09 mm, its rank correlation with
  the true error is 0.29 to 0.46, and on a tracked sheet twice the sigma covers only 0.79 to
  0.80 of the low-sigma vertices (tracking error is invisible to it).
- **The surface emulation misled.** Reading the two meshes along the true normal and combining
  the readings with an equal say passed E-D4 on 16 of 27 cases against today's 10. That reading
  takes the nearest vertex within 1.5 mm whose normal agrees: it rejects outliers and picks a
  face by its orientation, which a fused volume cannot.
- **The real experiment** (graphics card, 35 pairs: the 12 cases and 2 spread pairs each, at the
  app's pose and at the true motion; the kill rule fixed beforehand): **killed.** E-D4 passes on
  sound pairs: today's merge 11 of 23 at the app's pose and 17 of 31 at the true motion; one
  model per scan with faces kept apart 10 and 15; the plain average of the two models 10 and 14.
  It fails E-D4 (1) where today passes (P1s1, M3s2), reads a 2 mm flange 0.10 to 0.13 mm thicker
  than today on two sheet pairs, and covers less than today: 0.7 to 1.0 points on the bracket's
  opposite sides, 6 to 9 points on sheet pairs, 22 points where one of the two scans is poor.
- **Which half does what:** keeping the faces apart is the useful half (on sheets the plain
  average loses 10 to 42 points of cover and kept apart recovers most; the 1 mm rib reads
  nearer the truth on most sound sheet pairs, e.g. +1.38 to +1.11, +1.09 to +0.84, +0.58 to
  +0.44 mm). An equal say per scan is the harmful half: a poor scan pulls the better one's
  surface towards it, where today's single model gives it only its share of the observations.
- **Not built, and not to be built as two models.** What is worth carrying on: keeping a wall's
  two faces apart where two scans saw opposite faces, inside today's single model. Today that
  rule is tied to the thin-walls setting (the band and the second mean together), so it needs
  an engine change first; then the same 35 pairs and the same rule. Not started.
- **Cost, for the record:** about 29 s a merge against today's 27 s, the same memory swing
  (+4.6 GB of system memory).
- **Also found:**
  - The merged sigma of today's merge under-covers on the holes plate (twice the sigma covers
    0.69 to 0.72, each scan's own 0.85 to 0.905) and on several sheet pairs (0.86 to 0.92).
  - P3 (the bracket's opposite sides, 46 % shared): at the true motion the two scans agree in
    the median everywhere (within 0.03 mm, no trend along the part); the tails disagree by 0.4
    to 0.7 mm at the 90th percentile. The alignment's 0.9 mm error is a slide along the part's
    length, only 0.02 to 0.09 mm of it along the normals: a weakly held direction pulled by
    local disagreements at edges, not one offset between the scans. Its lower residual comes
    from two biased surfaces, not from a better pose.
  - Sheet scans are slid and bowed by tracking; two of them then disagree by more than any
    pose can mend (P5: no pose passes, the true motion included).
- Limits: simulated; scans of 9 s; the spread pairs of the sheet and the holes plate share one
  seed or one scan with their case (half independent); no moved part; fine walls and Refine
  with lasers absent from both merges, as in the app; the fourth arm (one model with the
  opposite-sides rule off) left out.

### [x] X19. A pose from tags, held, where two scans share little (S to M): off by default by the owner's choice (2026-11-27); offered per alignment on the Align page
E-D4 (4). Design in `D:\OpenHandScan-work\scratch\ed3\tag_hold_design.md`; first test in
`tools/merge_combine_study.py` (`tags`).
- **What the pose is worth** (X18's experiment, real fusing): on the 12 pairs that share under
  0.70 (P3, M1, M3, P5 and their spread pairs) today's merge passes E-D4 (1) and (3) on none
  at the app's pose and on 10 at the true motion (the two misses are sheet pairs).
- **Found** (tag maps emulated with the corner noise measured in X8, independent and shared
  between tags, 3 runs each): a pose taken from the tags both scans saw lies 0.11 to 0.29 mm
  (P3) and 0.30 to 0.46 mm (M1) from the true motion, against the app's 0.97 and 0.79 mm; on
  the sheet pair M3 it lies 0.48 to 0.69 mm off, no nearer than the app's 0.51. Refining a tag
  pose from the shapes, as the app does today, walks back to the shapes' own minimum every
  time, the same pose as without tags. So a tag pose must be held, not refined. A merge at the held tag pose passed E-D4 (1) on every run of P3, M1 and M3 in the surface
  emulation, which X18 showed to be too kind; so it was run for real:
- **Today's merge at the held tag pose, real fusing** (2026-11-24, 31 merges; the test fixed
  beforehand: E-D4 (1) and (3) pass on every run of the pairs whose single scans are sound):
  **passes.** P3 and M1, 6 runs each, and their spread pairs P3s1, P3s2, M1s2 all pass, where
  the app's pose fails every one. The merged part is the true motion's: 0.952 to 0.957 within
  0.5 mm, median 0.058 to 0.074 mm, 10.0 to 11.7 % more than 0.3 mm off (the app's pose: 0.846
  to 0.936, 0.149 to 0.211 mm, 31 to 44 %). The tag poses lie 0.11 to 0.79 mm from the true
  motion; that is near enough.
  - Sheet pairs (reported, not gated): M3 passes on all 6 runs and M3s2 passes; M3s1 fails as
    it does at the true motion. P5 and its spread pairs pass on all 8 runs, with 0.90 to 0.93
    covered against 0.47 to 0.77 at the app's pose and 0.76 to 0.93 at the true motion: for two
    scans bent differently by tracking the tags' pose is a better rigid pose than the fit to
    all tracked frames (it lies 4.0 to 4.7 mm from that fit on P5 and P5s1).
  - Thin walls at the tag pose read as at the true motion or nearer the truth (M3's flange
    +0.37 mm against +0.64 at the app's pose).
- **The rule designed:** an alignment that started from tags, with under 0.70 shared and at
  least 3 shared tags whose corners agree within 0.5 mm, keeps the tag pose; the shapes'
  refinement is still run, as a check only. The shapes overrule the tags only when they move the part by more than the larger of 8 mm
  and 4 sigma of the tag pose (a tag map wrong as a whole: a table moved with its tags moves
  things by tens of millimetres). Changed 2026-11-24 from "3 mm and a clearly better fit": the
  shapes' own minimum lies 0.4 to 1.2 mm from the tag pose on the bracket and 1.6 to 4.5 mm on
  the bent sheet pairs, and the overlap is higher there on every pair (by 0.002 to 0.13), so a
  better fit is no evidence against the tags; the old rule would have overruled P5's tags and
  lost its best merge. At 0.70 shared or more nothing changes, bit for bit. The
  verdict says who decided and how well the tags agree ("the tags put it within about 0.2 mm").
- **Built 2026-11-24 as a setting, off** (`[process] align_hold_tags`; `ohs/align/pairwise.py`,
  `tags.py`, `many.py`, `ohs/server/aligning.py`; 12 tests; `APP_API.md`, Rules, "The tag pose
  held"). With it off every alignment and merge is as before. Added in review: a refusal
  never becomes an acceptance (the hold weighs only a pose the refinement from the tags
  accepted); tags on the table do not hold a part that was moved between the scans; a held
  link's weight among several scans is in the shape links' frame; a held merge is recorded
  as information, not as a warning.
- **The stop rule below, with tags rendered into both scans: passed** (`VALIDATION.md`, "The
  tag pose held, built and tested"). On three bracket seed pairs that share under 0.70 the
  held merge passes E-D4 (1) and (3) and is the true motion's merge (median over the seen
  surface 0.062 / 0.064 / 0.057 mm against 0.188 / 0.155 / 0.132 without the hold, which
  fails (1) on all three); the pair sharing 96 % is the same with the hold on and off; a
  tag moved 2 mm was left out and the pose held from the other seven. A sheet pair
  (reported) passes too.
- **Decided 2026-11-24 (E-D5): on by default after the turned-over case passes.** Run the same
  day (`VALIDATION.md`, "The part turned over, tags on the part"): **a miss, the setting
  stays off.** Two of three pairs are held and pass; on the third the overrule gave way to
  the shapes' own search, which had taken the part turned round (75.5 mm away), where the
  setting off passes. Table tags on a moved part never held. A tag map shifted 9 mm was not
  held, but the result was worded "Aligned from the shapes" though it started at the tags.
- **The overrule mended 2026-11-26** (`VALIDATION.md`, "The tag hold after the overrule was mended"): the
  tags are judged against the refinement that starts from them, never against a search
  from the shapes alone; not held means exactly the setting-off answer, in words that are
  true. The third turned pair is held now and its merge passes E-D4 (1) and (3); every
  earlier held pair keeps its pose; an alignment with the hold on costs what it costs off.
  So the turned-over case passes. The setting is still off.
- **The weakness measured 2026-11-26** (`VALIDATION.md`, "The tag hold: a guard on overlap
  measured and not built"): 153 alignments. The overlap ratio does not separate (right maps
  0.64 to 1.00, maps slid 9 to 15 mm along the tags' face 1.00), so that guard was not
  built. The limit of 8 mm was far too wide: 33 of 77 wrong maps were held worse than the
  setting off, by up to 6.4 mm. **`overrule_mm` is 2.0 now:** all 45 right maps stay held
  (they moved 1.76 mm at most), none of the 77 is held worse than off, the test expected
  to fail passes. What stays: a tag map wrong by less than about 2.5 mm is held at its
  error (tags on the table and the part nudged 1 to 2.5 mm: 1.0 to 2.5 mm where off gives
  0.7 to 0.8), because on pairs that share little the shapes' own minimum is 0.7 to 1.2 mm
  off and no limit on the move can tell the two apart; and a slide along a direction the
  shared faces do not pin is held, where off is equally wrong.
- **The owner's choice on that trade (2026-11-27): "Keep it off".** On right maps the hold
  is 0.3 to 0.7 mm nearer, on a part nudged 1 to 2.5 mm against table tags up to about
  2 mm worse than today; so `[process] align_hold_tags` stays off and the person who
  knows where the tags are decides, one alignment at a time.
- **Offered per alignment, built 2026-11-27** (`VALIDATION.md`, "The tag hold offered per
  alignment"; `APP_API.md`, Rules, "The tag pose held"; UX_PLAN G5): `hold_tags` in the
  bodies of `align` and `align_all` overrides the setting both ways for that alignment;
  both results carry a `hold` block the server writes (offered, held with a caution when
  the shapes would move the pose more than 1.0 mm, not held and why). The page shows the
  words and one button: "Hold the tags' position", or "Let the shapes decide". Guide
  section and two pictures; tried in the running app on the rendered bracket pair.
  - **The caution advises, it does not separate** (counted on the guard data, limit
    2 mm): it shows on 10 of 45 right holds (the bent sheet pairs and two spread pairs,
    1.02 to 1.75 mm), on 17 of 38 held nudges of 1 to 2.5 mm, and not on 9 of the 23
    nudges held worse than off (they moved 0.23 to 0.81 mm); on 0 of 33 slides along an
    unpinned direction (off is as wrong there). The offer's own sentence carries the
    condition: no tag moved against the part between the scans.
  - **Left:** real tags and real scans; a part with tags on the part itself, rendered,
    beyond the bracket; a measure that tells a nudged part from a right map (none
    found: overlap ratio and the refinement's move both fail under about 2.5 mm).
- **For the guide (added):** on a part turned over, tags go on a face seen both ways up.
  By E-D1 it is better on every pair it acts on and the same on every pair it does not (no
  tags, or 0.70 shared or more). Open before that: a part turned over with tags on the part itself, rendered (made-up clouds only so
  far); more parts than one bracket and one sheet; the Align page's words for a held pose
  (UX_PLAN) and the guide; the setting tried in the running app.
- **Stop rule: adopted only if**, with tags rendered into both scans (not emulated), the held
  merge passes E-D4 (1) and (3) on every pair that shares under 0.70 and whose single scans are
  sound, on 3 seeds; pairs at 0.70 or more are unchanged bit for bit; a tag moved by 2 mm
  between the scans is left out or overruled, never followed.

### [ ] X21. Errors the whole scan shares: a slightly wrong calibration (M)
Raised by an outside review on 2026-11-23 (below). Every number in `VALIDATION.md` is made with
the true calibration, or with one the simulator's perfect board gives (baseline +0.005 mm, depth
scale 0.001 %; `VALIDATION.md`, "Results (full runs)"). A real calibration is off by more, and
its error is the same in every frame, so no averaging removes it and no surface score shows it.
- The scale of every length rests on the baseline, and that on the printed board's scale. The
  app checks the print with calipers on a 100 mm bar: 0.05 mm there is 0.05 %, the whole of the
  long part's need (gap item 7), before tracking adds anything.
- **To measure:** perturb the calibration once per trial and hold it for the whole scan
  (baseline, focal length, principal point, the cameras' relative turn, the board's scale, the
  laser planes), each within what the calibration's own spread and the print check allow, over
  independent trials. Report what each does to the scorecard's dimensions (the panel's length,
  hole diameters and positions, wall thickness, flatness), signed, as mm per unit of error, and
  which the calibration status already catches (`VALIDATION.md`, "Which signs show a stale
  calibration").
- **What follows for X8:** tags as anchors place the tags with the stereo pair's own scale. They
  hold the scan's shape, not its scale. For an absolute length a known length has to be in the
  scene: tags of known pitch (a printed strip, checked like the board), or a scale bar. To
  measure with the same perturbations: tag positions estimated in the scan against tag positions
  known beforehand.
- No stop rule yet: this is a measurement that sets what the other items can promise.

### [ ] X22. A known length in the scene, and the print's scale in both directions (S, then M)
From the outside review's second round (2026-11-23), on what a home builder can use as a length
reference. Nothing here is built.
- **What the calipers can give.** 0.02 to 0.05 mm on the board's 100 mm bar is 0.02 to 0.05 % of
  scale: 0.26 to 0.65 mm over 1.3 m, the whole long-part need. Repeating the measurement does
  not remove the calipers' own error. So with today's check alone the 0.05 % need is at the
  limit and 0.02 % is out of reach.
- **The print check measures one direction.** The board's page has one 100 mm bar, the check
  takes one number (`measured_mm`) and scales the board alike in both directions. Printers scale
  the two directions differently. Step 1 (S): a second bar across the first, two numbers, a
  board model with its own scale each way; the check says so when the two differ.
- **A long reference.** The reviewer's choice for a home builder: a steel rule or flat steel
  scale of 1 to 1.5 m, lying straight on a rigid support, with marks the cameras can find tied
  to its graduations (tags beside chosen graduations, their real positions measured after they
  are stuck on, never taken as nominal). A printed strip gives no length to rely on beyond the
  span that was measured. Steel grows 0.075 mm over 1.3 m for 5 degrees, aluminium twice that:
  the reference's temperature is noted.
- **How a known length enters (step 2, M):** as an uncertain constraint in the tags' fit (the
  distance between two tags, with its own ±, counted once however many frames see it), never as
  an exact one: a wrong reference taken as exact bends the scan to fit it. One scale for the
  stereo pair may be estimated too, but from a short, well-seen standing look at the reference,
  not from a long scan, so that a drifting path is not mended by changing the scale. A scale
  applied afterwards mends a uniform error only.
- **Stop rule, to fix before step 2:** with a calibration off in scale by what X21 allows, the
  long panel's length comes within 0.05 % with the reference in view, and a scan without one is
  unchanged.
- **For the real scanner** (see also "Only the real scanner can settle"): the two cameras sit on
  one printed circuit board, so the distance between them follows the board, not the printed
  housing (the review was told otherwise). What the housing can do is bend the board through its
  four corner screws when it warms: 0.003 degrees of turn between the lenses is 0.02 % of depth
  at 250 mm. To measure first, with one calibration left unchanged: a fixed object from cold
  start to warm, at 150, 250 and 400 mm, with the scanner held and with the cable pulling, then
  cooled again. The calibration already records its temperature. If the board bends: one fixed
  screw and three that let it slide.

### An outside review (2026-11-23)
The owner asked another AI for a second opinion on the four results of E-D3, from a written
summary (no code). Taken as an opinion and weighed against the measurements:
- **Its main point, adopted:** a reconstruction can agree with its own observations very well
  while a dimension stays weakly held (the panel's length is the example). Local surface
  agreement is no evidence of a dimension; report signed dimension errors and tails beside the
  medians. X21 above comes from it.
- **Holes:** agrees with reading the rim from the images. Adopted for X15: say which diameter is
  reported (the entrance rim); keep several candidate edges and choose with one model of
  entrance, exit and wall and what each camera can see of them; ask for angular coverage; the ±
  must not shrink with the frame count for what the frames share; try the rim with the dots
  off. Its warning stands: 0.1 mm of diameter is 0.14 px of shared edge bias, so 0.1 to 0.2 mm
  is a goal under good conditions, not a supported figure.
- **Thin walls:** its first guess for the "always thicker" sign is ours (frames pulled outward
  by a tracking map that swells on a thin wall). Adopted: ablations that separate the matcher,
  the map and the feedback before building ties (exact depth with true poses; the matcher's
  depth tracked against a fixed exact surface; exact depth tracked against the evolving map; the
  order of the sides swapped). If they point at the map: a tracking map that keeps opposite
  faces apart (a directional TSDF).
- **Long parts:** known target geometry matters more than tag count (X21). Tags 100 to 150 mm
  apart in staggered rows as a first layout agrees with X8 step 3. For a tag that moves: leave
  one tag out, watch the geometry between tags seen together, look for a change in time.
- **Merge:** weighting by the sigma is sound only if the sigma behaves like an uncertainty; ours
  is a loose upper bound on the part. It advises putting the pose first and the sigma-weighted
  combination last. Adopted as the order of work, subject to the first tests' table; to check:
  the sigma's reliability by bins, the registration's objective round the true motion (a flat
  valley or a second minimum), and the pose's observability as a better caution than overlap.
- **Checked and fine:** the simulator's dots are projected from the moving scanner, not painted
  on the part. The 19.5 mm parallax rule for small holes holds.
- **Not possible yet:** its optical test of real holes with the projector on and off waits for
  the hardware.
- **Second round, on a length reference** (X22): accepted in substance. One correction to what
  it was told: the cameras share one circuit board, so its figure for the housing's expansion
  (4 micrometres a degree on the 60 mm between the lenses) does not apply to the distance; its
  point about the lenses' relative turn does. Its first hole look with dots on and off has since
  been done in the simulator (X15's first test): the dots do disturb the rim.

- **Third round, on the first results** (the same evening). Weighed, and it changes the order of
  work on thin walls:
  - **Thin walls: the cause is not yet shown.** Our reading was that the loop between tracking
    and its evolving map amplifies a small depth bias (exact depth: the sides 0.6 mm too close;
    the matcher's depth: 0.55 mm too far). The reviewer's simpler account fits the same numbers
    with no feedback: a shared bias of a few hundredths of a millimetre divided by a weak hold
    on the across-wall direction. The standard error measured for that direction (0.035 to
    0.07 mm) is no bound on it. So before any work on the tracking map: (1) a ladder of
    references to track against (the exact scene; a frozen TSDF built at true poses; the
    incremental TSDF integrated at true poses; today's loop), first looking at what the tracker
    sees of each; (2) paired biases put into the depth, separation against bias, slope and
    intercept on each rung; (3) the frames' poses refined together on the raw points, with no
    TSDF, after profiling that objective round the truth. X3 refined against the finished mesh,
    which already holds the error; this does not. A two-sided tracking map comes after these,
    and must keep each observation's side from the first one on.
  - **Holes:** with the dots on, score candidate edges by where they lie in depth in both
    cameras (plate and floor differ by 6.6 px of disparity at 10 mm), not by strength; try a
    median over frames in the face's plane; an edge model with blur and the pixel's area. For
    frames without dots on the real scanner: a diffuse light switched with the cameras and a
    short burst when the user asks for a hole, 3 to 5 good pairs; at 100 mm/s and 30 degrees a
    second, smear under 0.3 px needs an exposure or pulse of about 0.45 ms. A hardware point,
    to weigh when the scanner exists. A hole's diameter does not depend on the frame's pose, so
    reading well on a scan that lost tracking says nothing about the hole's position.
  - **Merge:** separate the two things the per-scan merge changes (each scan the same say;
    opposite faces kept apart) with a two-by-two at the same poses, and report case by case,
    signed, not only counts.

### A second outside review (2026-11-24): ten defects to mend

The owner had a second AI (Codex) review the software at commit 3053e1a and passed its report
on with "feel free to take what you think might be needed". Weighed as before: an opinion,
checked against the code, no instruction. Its view of the four E-D3 results agrees with the
plan (image evidence for hole rims, a causal ladder for thin walls, a length from outside for
long parts, pose evidence before weights for the merge); X15 to X19 have since done most of
that. What it adds is ten concrete defects, each checked against today's code (all ten are
there as described at the level looked at) and each to be mended as its own task with a
guard test seen failing first:

- [x] **R2-1 (its F1): the scorecard's comparison can say "Better or the same on every scene"
  with failed, missing or empty runs.** `tools/scorecard.py`: a recording missing from one
  side drops out, a failed run compares as n/m, and no verdicts at all gives the success
  sentence. This sentence decides adoptions (E-D1). Mended 2026-11-24: an
  incomplete comparison says so, names what is missing and exits with status 1
  (`VALIDATION.md`, "The comparison checks its evidence first").
- [x] **R2-2 (F2): a cached scorecard result can outlive an engine change.** `run_job` returns
  an existing `result.json` unchecked; the engine fingerprint leaves out `ohs/server`, which
  the app chain runs through; a saved baseline keeps no fingerprint. Mended 2026-11-24:
  every result and baseline carries its run's identity and a cached result is reused only
  when it matches (`VALIDATION.md`). The first scorecard command after this reruns its jobs.
- [x] **R2-3 (F3): the tracker's ICP weights its priors with the residual sigma twice**
  (`ohs/track/markerless.py`, `_icp`: the priors' precision times sigma squared and the data
  term divided by it). Hidden at the default of 1 mm; wrong for any other value. Mended
  2026-11-24: the closed-form answer at sigma 0.5 and 2.0; byte for byte the same at 1 mm.
- [x] **R2-4 (F4): after frames without gyro cover, one covered interval makes the whole gap
  count as covered** (`markerless.py`, `have_prior`; `pipeline.py`): recovery then trusts an
  incomplete rotation. Mended 2026-11-24: with any uncovered interval since the last good
  pose the frame recovers as one without a gyro and says so (2.4 degrees and 6.9 mm off
  before, 0.04 degrees and 0.3 mm now, on the box scene).
- [x] **R2-5 (F5): when the mesh model's brick pool is full, new surface is not fused and the
  saved result does not say so** (`recon_tsdf.py` logs once). The thin-parts rule's spirit:
  nothing may go missing in silence. Partly mended 2026-11-24: the model counts what it
  refused and the saved result says it (a block and a warning; shown after Follow the
  camera). A merge says it too, and the fine-walls model returns its
  status (2026-11-26). Closed 2026-11-27: Straighten, Build the surface and the
  thin-walls step show their own warnings (`stages.*.warnings`); the thin walls' finer
  model has a sentence of its own, carried into the base's report and the scan's summary;
  a stage's own warnings are told by where they came from, not by their words;
  `APP_API.md` has the block (`VALIDATION.md`, "Every stage says what its own mesh model
  left out"). Not done: no real pool was filled (its status is replaced in the tests).
- [x] **R2-6 (F6): a failed frame write still gets its row in the recording's index, and
  `meta.json` is replaced in place** (`ohs/io/recording.py`): a recording reopened after a
  failed write lists a frame it cannot read; a write cut short leaves metadata that does
  not parse. Mended 2026-11-24: failed frames go to `frames_failed.csv`, the recording says
  it is incomplete, `meta.json` is replaced whole and a damaged one no longer stops the
  reader. `ohs process` asks for `--mode` when the metadata is damaged.
- [x] **R2-7 (F7): when the matcher falls back to the processor mid-scan the mesh model keeps
  the graphics card's noise profile**, and the saved frame rows lose the event. Mended 2026-11-24 (each frame fused with its
  own matcher's profile; the switch saved and said). A merge and the thin walls' step since
  2026-11-27: a merge already fused each frame rightly and now saves and says the switch,
  naming the scan; the thin walls' step fuses a frame kept after the switch with its own
  matcher's profile.
- [x] **R2-8 (F8): the live recorder's own queue has no bound** (known since MAINTENANCE_PLAN
  B4: the writer below it is bounded). Slow storage can use up memory. Mended 2026-11-24
  in the recorder (at most 1 GiB or 20 s waiting; then the recording stops with a reason and
  keeps what was written). The app stops such a scan by itself and tells the user at
  once (2026-11-24). The motion sensor calibration step stops with its recorder
  and refuses a damaged or too short recording (2026-11-26). The budget is a setting since
  2026-11-27: `[live] max_waiting_mb` (1024) and `max_waiting_seconds` (20), the defaults
  exactly as before, at least 64 MB and 1 s.
- [x] **R2-9 (F9): "frames with laser points" counts additions, not frames** (two lines a
  frame count twice). Mended 2026-11-24 (`LaserCloud.add_frame`; the points are unchanged).
- [x] **R2-10 (F10): the rotation logarithm is about 0.01 degrees off just below a half turn**
  (`ohs/geometry.py`, `so3_log`). Mended 2026-11-24: stable within 0.1 rad of a half turn
  (round trip under 1e-12), unchanged bit for bit elsewhere.

Also taken from it, not started: signed per-recording differences and fixed practical limits
printed beside the scorecard's verdict (a wide spread on one bad seed can hide a smaller
regression); seeds kept aside for judging only (done by hand in X8 and X15; to be the rule);
a pinned set of Python package versions for the Windows bundle; a permanent regression
recording for the 506 mm wrong alignment that was accepted. Not taken now: splitting the
large files, continuous integration (A5, parked by the owner).

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
- [x] A merge of two scans that share little of the part (found by X7, 2026-11-22): from
  picked points the alignment accepts a pose about 1 mm off (bracket, opposite sides, 46 %
  shared) or 3 mm off (sheet, 40 % shared) and says "Aligned"; the merged part is then far
  worse than either scan (0.31 and 0.37 mm median against 0.10 and 0.16) and nothing tells the
  user. **The warning: done 2026-11-23** (`VALIDATION.md`, "Two scans that share little").
  - An accepted alignment with overlap under 0.70 carries a caution (`little_shared`,
    `caution`), shown under the verdict on the Align page; for several scans the verdict and
    the pair lines name the links. A merge measures the overlap itself at the poses it gets
    (2 to 3 s) and puts the caution in its warnings and the merged scan's report; a second
    pass's message says it too. Nothing is refused.
  - The limit was measured on 78 alignments of 39 pairs and 12 merges
    (`tools/merge_overlap_sweep.py`): merges the pose ruined at 0.40 to 0.69, sound from 0.72.
    The margin is thin on the sheet's side (one pair each side).
  - A check after the merge (each scan's own surface against the merged mesh) was measured and
    does not tell ruined from sound: not built.
- [ ] A surer pose for two scans that share little (after the warning above): the position
  itself is still 0.6 to 1.1 mm off on the bracket's opposite sides.
- [ ] An alignment from the shape accepted a pose 506 mm off (found by the sweep, 2026-11-23:
  sheet scans s1@140 and s2@0, overlap 0.649, 109 degrees apart): a miss of the "sure or not
  sure" check (MAINTENANCE_PLAN F11). It now carries the caution, being under 0.70, but it is
  accepted. To do: find why two different starts agreed on it.
- [ ] Short scans of the sheet part can be poor by themselves (found by the sweep: two of its
  90-frame scans 0.29 and 0.37 mm median off the part, 49 and 62 % more than 0.3 mm off), and a
  merge of two sheet scans is worse than the better one even at the true motion.
- [ ] The merged model does not get Refine with lasers, a single scan does (found by X7), nor "Make only the thin walls finer" (no `fine_walls` in `ohs/align/merge.py` or `ohs/server/aligning.py`; read from the code 2026-11-23, not measured), though a merge is where both faces of a wall are seen.
  Measure whether it helps a merge.
- [x] The whole test suite had not been run in one go since before X5; two checks outside the
  per-task lists were broken meanwhile. **Run 2026-11-23** on 2d3031a: with the graphics card
  2123 passed, 6 skipped, 1 failed (44 min); without it 2038 passed, 91 skipped, 1 failed
  (43 min); no driver timeout. The one failure, on both: `ohs/cad/nominal.py` quoted a sentence
  of "Known limitations of the pipeline" that the rewrite of 2026-11-21 removed. That source
  is dropped (four others state the same numbers). The other was `tests/test_merge_guard.py`'s
  stand-in (72407d0). To keep: run the whole suite after every engine item, not only the
  task's files.
- [x] **The whole suite after the second review's fixes, the wall reading and the mended tag
  hold: run 2026-11-26** on 23a7939 (a frozen copy). With the graphics card 2405 passed, 7
  skipped, 1 expected failure, 2 failed (68 min); without it 2321 passed, 92 skipped, 1
  expected failure, 1 failed (46 min); no driver timeout. Both failures were the tests' own,
  no engine defect: (1) on both paths `test_a_baseline_is_not_saved_from_old_results` got a
  study's recording among the scorecard's scenes, because `tools/merge_combine_study.py`
  registered its tagged recordings in the scorecard's sets when an earlier test had called
  it in the same process; they are in no set now (guard test seen failing first). (2) With
  the graphics card `test_each_frame_is_fused_with_its_own_matchers_factor` (R2-7) watched
  only the processor's fusion, which that path does not call; it watches whichever the path
  uses now and passes on both, so the per-frame noise profile is shown on the graphics
  card's fusion too. The expected failure is the tag hold's known weakness (X19).
- [ ] The known-bias table (`ohs/cad/nominal.py`, what Compare with CAD says about a hole)
  holds "a Ø 25 hole over a far background reads 0.9 to 3 mm small with tracking" from the
  studies of 2026-11-12 to 16. The scorecard's baseline reads the plate 120 mm up 2.9 to 6.9 mm
  small tracked (seed 0 lost tracking) and the 200-frame car panel 0.97 to 1.37 mm small. To
  decide: whether the table's range follows the baseline, and with which seeds.
- [ ] Ø10 holes 14 mm above a table on the large plain panel come out closed or half-filled,
  even at true poses (found by X8, 2026-11-23: the Hole tool refuses most clicks and reads the
  rest as Ø4.5 to 5.6). The budget's "near a floor: Ø10 -0.3 to +0.6" is the plate seen from
  above; this panel is seen at 60 to 75 degrees. To do: the same hole on this scene seen from
  above, to tell the viewing angle from the scene.
- [ ] Nothing warns after a scan whose length is not held (found by X8): the tracker notes on
  38 to 40 % of the large panel's frames that the surface let the scanner slide (the track
  report's `weak.slide`), while the path check and the mesh's scores look fine. To measure:
  whether that share, with how far the scan travelled, separates the panel from the door and
  the small parts; if it does, a caution after Make mesh ("the length along the part is not
  sure: use tags").
- [ ] **In Markers mode the mesh keeps no thin-wall rule** (found by X17, 2026-11-24; `VALIDATION.md`,
  "Thin walls in Markers mode"). `[process] thin_walls` "auto" means on for scans without markers and off
  in marker mode, so the same wall at the same true poses reads +0.8 to +1.6 mm in a Markers scan against
  +0.03 to +0.84 markerless. To do (S): measure Markers mode with `thin_walls` "on" on the walls and the
  sheet with tags (recordings in `ed3/walls_looks/exp1/recs` on the work drive) and on the scorecard's
  marker scenes; if nothing gets worse, "auto" means on in both modes (E-D1; the config's words and the
  guide follow).
- [ ] **Straighten's laser crossings can make a short scan worse and pass their own test** (found by X17,
  2026-11-23; `VALIDATION.md`, "Thin walls: the raw reading, and where the error is made"). On the standing
  walls with a ball in view (a 4 s scan, frames 1 degree apart) the frames test passed at 0.83 and 0.77 (limit
  0.85), the crossings met twice as well, and against the truth the path and the walls got worse (the path's
  largest error 1.26 to 1.40 mm and 0.52 to 1.03 mm; the walls up to 0.28 mm thicker). A held-out frame shares
  its geometry and errors with its fitted neighbours, so it is no independent check there. To do (S): hold out
  whole stretches of frames instead of alternate frames, or skip the adjustment when every held-out frame is a
  near neighbour of a fitted one; guard test first, on that recording. Not seen on the scorecard's own scenes.
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
- **A merge weighted by the vertex sigma** (X18): the sigma ranks the true error weakly (0.29 to 0.46) and does
  not see tracking's error.
- **A merge from one model per scan with an equal say** (X18): a poor scan pulls the better one's surface
  towards it; today's one model gives it only its share of the observations. And a gain read from meshes
  along the normal is not a gain in a fused volume.
- **Refining a pose that came from tags against the shapes** when the scans share little (X19): it walks back
  to the shapes' own minimum, 0.5 to 1 mm from the truth.

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
