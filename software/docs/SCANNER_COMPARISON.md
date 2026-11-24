# OpenHandScan compared with existing 3D scanning tools

Written 2026-11-14. It compares OpenHandScan with consumer, professional and open-source scanning
tools, and says where OpenHandScan can improve. OpenHandScan's numbers come from this repository's
documents; the section is named each time. Everything about other products comes from the web, with a
source tag such as [S21] that links to the page. The list of sources at the end says which pages
were opened and read, and which are known only from a search result's summary.

Updated 2026-11-23. OpenHandScan's side is rewritten from today's documents: merging, comparison
with a reference, sections, flush, STEP export, the report and the Windows program now exist, and
today's numbers replace those of 2026-11-14, worse and better alike. The cited pages were opened
again, new products and software (2025 to late 2026) were added as [S50] to [S77], and the summary
now answers "does OpenHandScan have the best scanner features on the market" for features,
accuracy and hardware separately.

## Summary

**The short answer: no, not as a scanner.** It has a software feature set that no scanner in its
price class was found to match, and simulated accuracy that is level with consumer claims on
smooth surfaces and well behind on holes, thin walls and the length of long plain parts. Its
hardware is behind every laser scanner read here. Nothing of its own has been measured on a real
part.

1. **Features: ahead of the consumer tier, level with professional suites in breadth, behind
   them in a few named places.** Since 2026-11-14 the app has gained aligning and merging of 2 to
   8 scans, comparison with a reference mesh, sections as DXF and SVG, flush along a seam, fitted
   shapes with a ± exported as STEP and as a FreeCAD macro, approved nominal sizes, a printable
   report, a calibration status, guided second passes and a Windows program
   (`FEATURE_PLAN.md`, sections K, I, C, H, N; `docs/guide/README.md`). The consumer makers sell
   inspection as paid add-ons (Revo Measure, Creality Inspect) [S71][S72] or leave it to other
   software [S4][S8]. What it does that no product read here does: a per-vertex uncertainty
   checked against the truth, a tolerance answer that names each weak area's reason and a place
   to scan it from, and a written record of what failed. What it lacks: a gap measurement (built,
   measured, not offered), comparison with a STEP file (STL, OBJ or PLY only), free-form scan to
   CAD, an installer, colour.
2. **Accuracy (simulated only): level on smooth surfaces, behind on everything that holds a part
   in place.** Smooth surfaces read 0.02 to 0.06 mm in the median on the graphics-card path
   (`VALIDATION.md`, "The scorecard's baseline"), inside the 0.02 to 0.1 mm the consumer tier
   claims and far better than the 0.3 to 1.7 mm measured on three consumer scanners on a plate
   [S20]. But a Ø 25 mm hole reads 1 to 7 mm small over open space and Ø 6 to 10 mm holes there
   give no reading; standing walls of 0.5 to 2 mm read 0.5 to 1.3 mm too thick with tracking; and
   a simulated 1.3 m plain panel came out 0.12 to 0.50 % wrong in length (1.6 to 6.5 mm) with no
   warning, where the need is 0.05 % (`VALIDATION.md`, "The overall size of a large part").
   Consumer makers claim 0.02 mm + 0.06 to 0.1 mm/m [S1][S6][S50], about 0.1 to 0.15 mm over
   1.3 m, and professional ones 0.02 mm + 0.01 to 0.04 mm/m, tested to a named standard
   [S21][S23][S61]. A merge adds coverage but beat the better single scan on 1 of 5 pairs
   (`VALIDATION.md`, "The merge on today's engine").
3. **Hardware: behind.** Two red Class 3R laser lines (four in a variant), against 7 to 81 blue
   lines at Class 1 to 2M on every laser scanner read here, the newest consumer ones at 42 + 7
   [S50] and 34 + 15 + 1 [S55]; 1-megapixel monochrome cameras 60 mm apart. Ahead only on price
   ($230 to 300 in parts, against $699 to $2,499 for the current consumer handhelds read here
   [S53][S55]) and on openness (MIT).
4. **What cannot be known until it is built:** every accuracy figure above. The simulator has no
   gloss, no motion blur, no MJPEG, a perfect calibration board and an ideal IMU (`VALIDATION.md`,
   "Simulation limitations"). The one independent test of low-cost scanners found their real
   error ten to fifty times the brochure figure [S20]; nothing says OpenHandScan would escape
   that. The LED projector chosen for the build has no end-to-end simulated result yet (section
   1).
5. **The one thing that would change this answer:** the real scanner measured on reference parts
   (a flat plate, a ball bar, a stepped block, a drilled plate, a long plain panel), with the
   results published as `VALIDATION.md` publishes the simulator's. Until then OpenHandScan's lead
   is in knowing how good a scan is, not in how good the scan is.

**The improvements that matter most** (the full ranked list, with what is done, is in section 6):

1. Build it and measure a flat plate, a ball bar, a stepped part and a drilled plate. Until then
   nothing here is a fact. Open; waits for the hardware.
2. Holes and edges from the camera images (and the laser lines, once the real line's width is
   known), not from the fused mesh. The laser-line ends were measured and fall 0.4 to 4 mm short
   of the rim (ENGINE_PLAN X2, not built); the image-edge route is open.
3. Hold the length of long plain parts: tags as anchors in Straighten (ENGINE_PLAN X8 step 2,
   open), and a warning when the surface lets the scanner slide.
4. Thin walls: the cause is narrowed down (the frames of a wall's two sides are placed 0.3 to 1.0 mm apart by tracking; why is being tested (2026-11-23)), two
   fixes were measured and not adopted, and a per-scan choice "Make only the thin walls finer"
   brings walls 0.04 to 0.52 mm nearer (`VALIDATION.md`, "Fine mesh detail on thin walls only").
5. Tell the user about errors the sigma cannot see: a whole region moved by tracking, and the
   slide along a plain part (ENGINE_PLAN X13; `VALIDATION.md`, "The overall size of a large
   part").

Done since 2026-11-14: merging several scans (the feature; its accuracy gain is still open) and
comparison with a reference. Still behind the targets: the wait after a scan.

---

## 1. How to read the numbers

**OpenHandScan.**
- All accuracy figures are from the simulator (`ohs.sim`). `VALIDATION.md` calls them "an upper
  bound on what the real scanner will do". `ENGINE_PLAN.md` says the same of every gain it lists.
- The simulator has matte surfaces only, no motion blur, a perfect lens model, a perfectly flat
  calibration board and lossless images (`VALIDATION.md`, "Simulation limitations"). Several
  parameters were tuned on the same simulator.
- Since 2026-11-20 most figures come from one scorecard: 36 recordings on the graphics-card path,
  several seeds a scene, each part placed alone by its own best fit (`VALIDATION.md`, "The
  scorecard's baseline"; `ENGINE_PLAN.md`, X0). Some numbers differ from those of 2026-11-14
  because the part is now scored alone, not with its table; the section says which.
- The roadmap forbids any accuracy claim before reference parts have been measured on the real
  hardware ([`ROADMAP.md`](ROADMAP.md), section 1 "On accuracy claims" and section 5). The app's
  report says the same on every page (`FEATURE_PLAN.md`, I3).
- The LED dot projector (option B) is the build route. `PARTS.md` ("Pattern projector: two
  options") gives its simulated depth error per frame as 0.23 to 0.27 % with a film mask, about
  twice option A's 0.11 to 0.18 %. The simulator's standard projector is described in
  `ohs/sim/nominal.py` as a DOE field of about 10,000 dots, which is option A's pattern. No
  end-to-end result with the LED mask pattern was found in the documents read on 2026-11-23
  either. See section 7.

**Makers' figures.**
- "Accuracy up to 0.02 mm" is normally a single-frame or probing figure under ideal conditions.
  Scantech's footnote says so directly: "accuracy" is the probing error on a sphere, "volumetric
  accuracy" is the sphere-spacing error [S23].
- Volumetric accuracy is written as a + b × length, for example 0.020 mm + 0.040 mm/m [S21]. This is
  the figure that matters for a part larger than one camera view. Shining 3D says the same about its
  own consumer scanner [S15].
- Professional makers name an acceptance test: VDI/VDE 2634 Part 3 (Creaform, Scantech, Artec) or
  ISO 10360 (ZEISS, Scanology's SIMSCAN-S Gen2) [S21][S23][S26][S61][S62][S64]. Scantech and
  Creaform add ISO 17025 accredited laboratories [S21][S23]. VDI/VDE 2634 is a German guideline,
  not an international standard [S43].
- Consumer makers mostly name no test on the pages read. Creality calls the Raptor Pro Max's
  volumetric figure a laboratory figure [S50]. New since 2026-11-14: Revopoint sells a MetroY Ultra
  "CMM Edition" with a certified carbon-fibre ball plate, and its software checks the scanner
  against the plate's calibration data in the field [S55][S56]. That is the first user-side
  accuracy check found in the consumer tier; the article that describes it was paid for by
  Revopoint, and its testers failed the check twice during a heatwave before recalibrating [S56].

**The one independent test found for current low-cost handheld scanners** (Kersten, Marsmann and
Przybilla, ISPRS 2026, peer reviewed [S20]). Reference: a ZEISS ATOS 5. Flatness of a 300 × 300 mm
granite plate, distance of the scan to a fitted plane (figures from the full text read on
2026-11-14; on 2026-11-23 the paper was still online and its abstract was read again, see
section 7):

| Scanner | Maker's figure in the paper | Price in the paper | Average distance | Standard deviation |
|---|---|---|---|---|
| Revopoint POP 3 Plus | ± 0.08 mm | about 700 | 1.68 mm | 0.96 mm |
| Revopoint MetroX | ± 0.03 mm | about 1,200 | −0.40 mm | 0.29 mm |
| 3DMakerpro Moose | ± 0.03 mm | about 700 | 0.89 mm | 0.60 mm |
| Hexagon MARVELSCAN | ± 0.02–0.03 mm | 40,000–60,000 | −0.04 mm | 0.08 mm |
| Hexagon Absolute Arm + AS1 | ± 0.006–0.04 mm | 100,000–150,000 | 0.05 mm | 0.03 mm |

The same paper, on other objects:
- A staircase with 20 mm steps: the two professional systems were within 0.05 mm. The low-cost
  scanners blurred the edges, and both Revopoint scanners came out too small throughout, which the
  authors read as a scale error.
- A plaster bust: all scanners within ± 0.5 mm, except the two Revopoint scanners with up to 7.6 mm.
- A porous stone figure: the Revopoint scanners lost tracking, and their models were noisy,
  distorted and incomplete.
- The Moose came close to the professional systems on some objects and not on others.

The authors' conclusion: low-cost scanners are an addition to professional ones, not a
replacement. Their earlier studies (2016, 2018) found the same pattern and blamed unstable scale
calibration.

**Still the only one.** A search on 2026-11-23 found no newer independent test of consumer or
prosumer handheld scanners on reference parts. The same group's 2025 paper tests mobile-mapping
systems for buildings, not object scanners [S73]. A 2026 dental study of face scans found the
low-cost handheld scanner the least precise, but its models could not be read (summary only)
[S74]. Review sites that quote gauge-block readings for the Raptor, the MIRACO Plus or the MetroX
give no method or uncertainty and are left out. No independent measurement was found for the
Creality Raptor line, the Einstar line, Artec, Creaform, Scantech or ZEISS. For those, every
figure below is the maker's.

---

## 2. OpenHandScan today

As of 2026-11-23. A row marked "changed" gives today's figure where the 2026-11-14 version gave
another.

**The hardware**

| Point | OpenHandScan | Where it is written |
|---|---|---|
| Build state | Designed; the housing was printed in ASA and dry-fitted on 2026-11-16. Not assembled or wired. The 4-laser variant's extra laser blocks have not been tried on a print | `README.md` (root), "4-laser variant" |
| Technology | Active stereo on a projected dot pattern for the dense surface; two calibrated red laser lines for sparse, more accurate points; gyro for tracking | `README.md` (root), "How it works" |
| Line lasers | Two (one vertical, one horizontal); a 4-laser variant adds a second of each, with its own head files. The software takes any set of line lasers the build has | `README.md` (root), "4-laser variant"; `VALIDATION.md`, "Four line lasers" |
| Cameras | Two OV9281, monochrome, global shutter, 1280 × 800 each, 60 mm apart | `README.md` (root), "Stereo board measurements" |
| Depth per pixel of disparity | 0.52 mm at 150 mm, 1.45 mm at 250 mm, 4.7 mm at 450 mm | `RESEARCH_PLAN.md`, section 3 |
| Lasers | 650 nm, 5 mW, Class 3R | `software/README.md`, section 11; `docs/BUILD.md` |
| Laser points | About 700 to 830 a frame with two lasers and 1,370 to 1,560 with four (simulated, 15-frame scans) | `VALIDATION.md`, "Four line lasers", "Where the two new lasers point" |
| Control board | Raspberry Pi Pico. A Waveshare ESP32-S3-Pico takes the same firmware on the same pins; not tried on a real board | `README.md` (root); `firmware/README.md`, "ESP32 instead of a Pico" |
| Working distance | 200–350 mm recommended; depth kept from 150 to 450 mm | `software/README.md`, section 7 |
| Frame rate | 60 fps camera; 46–55 fps delivered on Windows | `PARTS.md`, "Notes before ordering" |
| Markers and spray | Markerless by default. Printed AprilTags for large smooth panels. Matte spray for glossy paint, dark plastic, chrome | `software/README.md`, section 7 |
| Parts cost | $230–300 plus filament | `README.md` (root), "Parts to order" |
| Licence | MIT for hardware, firmware, software and documents | `README.md` (root), last line |
| Colour | None: the cameras are monochrome | `README.md` (root), "How it works" |

**Accuracy, simulated** (graphics-card path, Standard quality, tracked unless said; "a..b" is the
range over seeds)

| Point | OpenHandScan | Where it is written |
|---|---|---|
| Smooth surfaces | Changed. Part median 0.059 mm, p95 0.25 mm (sphere); 0.038 / 0.17 mm (car panel end to end); 0.021 to 0.022 mm median on the 200-frame car panel, whose p95 is 1.7 to 1.9 mm because 7 to 9 % of its vertices are fill hanging past its free edge. 2026-11-14 gave 0.065 to 0.088 mm with the table counted | `VALIDATION.md`, "The scorecard's baseline" |
| Smooth surfaces, no graphics card | Sphere 0.053 / 0.215 mm; brackets 0.48 to 0.69 mm median (0.08 to 0.23 with a graphics card) | `VALIDATION.md`, "The scorecard's baseline", "Known limitations of the pipeline" |
| Laser points | Median 0.061 mm, 95 % level 0.26 to 0.28 mm, with two lasers and with four | `VALIDATION.md`, "Four line lasers", "A tracked scan with two and with four lasers" |
| One depth frame | Graphics-card matcher median 0.32 mm, p95 1.41 mm | `ENGINE_PLAN.md`, "The error budget today" |
| Large smooth part | Changed. Door trim panel, 141-frame stretches: median 0.045 to 0.055 mm, p95 0.36 to 0.44 mm, coverage 0.92 to 0.99. The 0.07 / 0.79 mm of 2026-11-10 is superseded | `VALIDATION.md`, "The scorecard's baseline" (set `full`) |
| Overall size of a long plain part | New. A simulated 1300 × 400 mm plain panel in three passes, 3 seeds: 0.12 to 0.50 % wrong end to end (1.6 to 6.5 mm), where the need is 0.05 % (0.65 mm). The cause is drift along the panel within a pass; the passes sit 2 to 11 mm apart along it. Straighten does not see it and the app gives no warning, while the mesh's median looks excellent (0.023 to 0.032 mm). No scale error, no bow | `VALIDATION.md`, "The overall size of a large part"; `ENGINE_PLAN.md`, X8 |
| Detailed part | Changed. Carburettor stretches: median 0.125 to 0.179 mm, p95 0.67 to 1.01 mm, coverage 0.68 to 0.71. The 0.80 / 2.7 mm of 2026-11-10 is superseded | `VALIDATION.md`, "The scorecard's baseline" (set `full`) |
| Part with edges | Bracket: 11.5 to 31.6 % of the part more than 0.3 mm off (about 10 % at true poses), p95 0.45 to 0.49 mm; boss radius −0.07 to −0.27 mm, shaft radius within 0.07 mm | `VALIDATION.md`, "The scorecard's baseline" |
| Thin walls | Changed, mostly better. Standing walls of 0.5 / 1 / 2 mm read 1.18 to 1.26 / 0.70 to 0.91 / 0.53 to 0.66 mm too thick tracked (0.16 to 0.84 at true poses). The sheet part's 1 mm rib 0.66 to 1.21 mm too thick, its 2 mm flange 0.25 to 0.60 (2026-11-14: about 1 mm). A 0.5 mm plate lying flat reads within 0.01 mm. Cause: one camera offset of 0.19 to 0.25 mm outward, made in tracking, which the scan cannot see | `VALIDATION.md`, "Known limitations of the pipeline", "Poses refined against the finished scan" |
| Thin walls, the per-scan choice | New. "Make only the thin walls finer" (off unless chosen): every wall of 0.5 to 2 mm 0.04 to 0.52 mm nearer the truth on 12 of 12 runs, for 16 to 29 % more time and no more memory. Not a default: the part's median got worse on two true-pose runs and a 3 mm rib read 0.05 to 0.09 mm thin | `VALIDATION.md`, "Fine mesh detail on thin walls only", "Make only the thin walls finer in the app" |
| Holes | Changed, worse over open space. A Ø 25 mm hole reads 0.97 to 1.37 mm small on the car panel; 120 mm above its floor 1.40 to 1.57 mm small at true poses and 2.9 to 6.9 mm small tracked (one seed lost tracking), with no fit for its Ø 10 and Ø 6 holes. 10 mm above a floor: Ø 25 −0.20 to +0.47 mm, Ø 10 within 0.2 mm, Ø 6 no fit. Ø 10 holes 14 mm above a table on the long panel: no reading, even at true poses | `VALIDATION.md`, "Known limitations of the pipeline", "The overall size of a large part" |
| Gap and flush | New. Flush along a seam: the mean within 0.04 mm of the truth, one station within 0.15 to 0.25 mm 95 % of the time. Gap: every 2 to 6 mm gap is bridged, so gap is measured and not offered | `VALIDATION.md`, "Gap and flush between two panels" |
| Edges after smoothing | Changed. The built-in recipes smooth "Off edges" since 2026-11-17: 1.4 to 2.9 % of the bracket's edge vertices moved by more than 0.05 mm, against 23 to 46 % before. Free smoothing still rounds 26 to 30 % of them | `VALIDATION.md`, "Smoothing's within 2σ limit on today's engine" |
| Merge of two scans | New. A merge adds coverage. Against the better single scan it was better on 1 of 5 pairs; pairs that share little of the part (40 to 46 %) came out far worse than either scan, because the accepted pose was 1 to 3 mm off. Since 2026-11-23 the app cautions when an alignment shares under 70 % | `VALIDATION.md`, "The merge on today's engine", "Two scans that share little" |
| Marker mode | Not measured again: mesh median 0.49 mm, 95 % level 1.48 mm; trajectory error 0.94 mm median. Tags now also give a start for aligning two scans (0.26 to 0.33 mm off on a sphere its shape cannot align) | `VALIDATION.md`, "Results (full runs)", row (e), "Aligning and merging two scans" ("Tags as a start") |
| Vertex uncertainty | Every vertex has a sigma. 2 sigma covers 99.2 % of the tracked bracket's vertices and 96.3 % of the car panel's. The Check passes 58.5 to 59.7 % of a scan at ± 0.3 mm and 99.8 % of that is truly within. What it cannot see: a region moved as a whole by tracking (a tracked plane's offset is covered for 44 %), and the 1.6 to 6.5 mm length error of the long panel. Without a graphics card there is no sigma, so no Check | `VALIDATION.md`, "Where a tracked scan's shape errors come from", "The vertex sigma", "The fits' ± on today's engine", "Why an area is weak"; `ENGINE_PLAN.md`, X13 |
| Uncertainty of a fitted shape | Changed, better. The ± is widened for errors that come in patches: ±2σ now covers the true error for 42 to 100 % of fits at true poses and 44 to 100 % tracked (2026-11-14: 32 % and 0 % for a cylinder's radius). It does not cover biases that are the same on every scan (bosses small, holes small) | `VALIDATION.md`, "The fits' ± on today's engine" |
| Calibration health | New. Each scan records signs of a stale calibration; a status says Valid, Check recommended or Recalibration required, and warns without blocking. In the simulator a camera fault of 0.01 to 0.05° puts a product 0.1 mm off; a camera roll is caught but blamed on a laser. Without a graphics card only the row error is judged | `VALIDATION.md`, "Which signs show a stale calibration", "The calibration's status" |
| Guided second pass | New. Passes aimed at the weak areas raised the first scan's surface that meets 1 mm by 13.2 points on average against 8.4 for random passes (better on 4 of 5 seeds); on the whole part they were level with random (+24.8 against +23.1). Hints while scanning were measured and not adopted | `VALIDATION.md`, "Why an area is weak", "A second pass that joins the first", "Hints while scanning" |

**Speed**

| Point | OpenHandScan | Where it is written |
|---|---|---|
| Live view | 15.1–15.3 fps on a Radeon 680M, 39.2 fps on an RTX 3070 Ti laptop; about 14 fps at 640 × 400 with four lasers on the Radeon | `VALIDATION.md`, "The waits a user feels"; `ENGINE_PLAN.md`, "Where it falls shortest", item 9 |
| Wait after a scan | Standard quality: about 4.8 times the scan's length on the Radeon, about 1.5 times on the RTX; targets 3 and 1. Not met. On the long panel (recorded at 10 fps) Make mesh took 2.5 to 3.7 times the scan | `ENGINE_PLAN.md`, "Where it falls shortest", item 9; `VALIDATION.md`, "The overall size of a large part" |
| Disk | A recording takes about 36 MB a second of scanning (2.1 GB a minute) | `docs/guide/getting-started.md`, "Disk space and memory" |

**Tracking**

| Point | OpenHandScan | Where it is written |
|---|---|---|
| Hard surfaces | Dark, speckled and hard scenes slip in their first frames: last frame 0.35 to 3.79 mm off on the graphics-card path. One hole-plate recording loses tracking while the tracker's numbers look healthy. Three fixes (X4, X4b, X4c) were measured and not adopted | `VALIDATION.md`, "Known limitations of the pipeline"; `ENGINE_PLAN.md`, X4 to X4c |
| Without a graphics card | Path errors of 4.5 to 43.5 mm on brackets, sheets, walls and plates; 0.5 mm plates slide and come out thinned. The app warns when no graphics card is used | `VALIDATION.md`, "Known limitations of the pipeline" |
| Warnings | The camera path's check warns above a 4 mm jump within 0.8 s; it misses slow drift | same section |
| Untested | Fast or jerky motion; recovery after a real loss | `VALIDATION.md`, "Simulation limitations"; `ROADMAP.md`, section 2 |

**The app** (OpenHandScan Studio)

| Point | OpenHandScan | Where it is written |
|---|---|---|
| Scanning and mesh | Projects, live 3D view, rewind, Make mesh with presets ("General", "Thin sheet metal and small parts") and quality, calibration wizard, self-test, Straighten (loop closure), Refine with lasers, the thin-parts hint | `docs/guide/README.md`; `docs/guide/make-mesh.md`; `FEATURE_PLAN.md`, P1 to P3 |
| Editing | Clean, remove the table, fill, smooth (everywhere, off edges, flat only), simplify, with undo, a map of what an edit moved, and recipes that apply the same edits to every scan | `docs/guide/editing.md`; `VALIDATION.md`, "Repeatable edits" |
| Is it good? | The Scan check: the share of the surface within a tolerance, the weak areas with a reason each (not seen, steep, far, few looks, edge) and a pose to scan each from | `docs/guide/viewer.md`; `VALIDATION.md`, "Why an area is weak" |
| Several scans | 2 to 8 scans aligned from their shape, from picked points or from shared tags, with "Not sure" when a pose is in doubt and "The scans share little" under 70 % overlap; merged from the raw frames, scans of different calibrations included; a second pass that joins the first | `docs/guide/align-merge.md`; `VALIDATION.md`, "Aligning and merging two scans", "Two scans that share little" |
| Measuring | Distance; fitted plane, cylinder (boss or bore) and hole with a ±; relations between fits; the part's coordinate system from three fits; sections; flush along a seam | `docs/guide/measuring.md`; `FEATURE_PLAN.md`, K1 to K3, I1, I4 |
| Comparison | With a reference mesh (a CAD model exported as STL, OBJ or PLY, or a scan of a good part): a best fit, the part's own axes or picked points; deviation colours and the areas outside a tolerance, each marked as beyond or within the scan's own scatter. A STEP file is not read | `docs/guide/compare.md`; `VALIDATION.md`, "A scan compared with a reference model" |
| Scan to CAD | Fitted shapes as STEP (AP214) and as a FreeCAD macro, opened valid in FreeCAD 1.1.4; nominal sizes suggested and approved by the user, never applied silently. Not free-form surfaces | `FEATURE_PLAN.md`, C1 to C3; `docs/guide/to-cad.md` |
| Report | One self-contained HTML page per scan that prints to PDF: warnings first, the Check answer, the features with their ±, sections, the comparison, the flush; features as CSV and JSON | `FEATURE_PLAN.md`, I3 |
| Export | PLY, STL, OBJ in millimetres, the laser point cloud, the trajectory, a JSON processing report, STEP, FreeCAD macro, DXF and SVG sections | `software/README.md`, section 8; `docs/guide/to-cad.md` |
| Windows program | A folder of about 526 MB with `ohs.exe`; no Python needed. No installer, not signed | `docs/guide/getting-started.md`, "The Windows program (ohs.exe)" |
| Graphics card | Any OpenCL GPU (NVIDIA, AMD, Intel). The CPU alone works, with weaker tracking, no sigma, no Check and no thin-wall rule | `software/README.md`, section 8; `ENGINE_PLAN.md`, "Where it falls shortest", item 10 |
| Not built | Gap between panels (not offered); a STEP file as the reference; free-form scan to CAD; an installer; colour; recovery after a real loss | `FEATURE_PLAN.md`, I2, I4, "What this plan leaves out"; `docs/guide/compare.md`, "Limits" |

---

## 3. Existing tools

All figures in this section are the maker's claims unless the row says "measured". Prices are in
US dollars as seen on the date in the source list.

### 3.1 Consumer and prosumer handheld scanners

| Scanner | Technology | Claimed accuracy | Distance, speed | Markers | Price | Source |
|---|---|---|---|---|---|---|
| Creality Raptor Pro | 22 crossed + 7 parallel blue laser lines (405 nm), plus infrared structured light | Blue: up to 0.02 mm, volumetric 0.02 mm + 0.08 mm/m. Infrared: 0.075 mm + 0.1 mm/m | 160–550 mm (blue), up to 1,000 mm (infrared); 60 fps blue, 30 fps infrared; 420,000–660,000 points/s blue | Blue modes need reflective markers; infrared mode tracks geometry or texture. Has an IMU | $1,899 (unchanged 2026-11-23) | Reseller listing [S1] |
| Creality Raptor Pro Max (new, on sale 19 Nov 2026) | 42 crossed + 7 parallel blue lines, plus infrared | Volumetric 0.02 mm + 0.08 mm/m, called a laboratory figure | 60 fps blue, 30 fps infrared | Up to 25 % fewer markers; marker-free blue laser only when wired to a PC | $1,799 ($1,619 at launch) | Press release [S50] |
| Creality Sermoon P1S (new, Sep 2026) | 34 crossed + 7 parallel + 1 single blue line, plus infrared; computer built in | Up to 0.02 mm; 0.02 mm + 0.08 mm/m | | | $2,299 ($2,099 early) | Press release [S51] |
| Creality Pika (new, Jul 2026) | Blue laser and infrared structured light, 260 g, works with a phone | 0.03 mm blue, 0.1 mm infrared | | Marker-free | $699 ($629 early); $899 on Amazon in one review | Press release [S53]; review, no reference part measured [S54] |
| Creality Otter | Infrared structured light, four lenses | Up to 0.02 mm "at 60 mm" | 110–1,000 mm, up to 20 fps | Not stated | about $900–960 | Reseller listings, not opened [S5] |
| Revopoint MetroX | 14 crossed + 7 parallel blue laser lines, plus 62-line blue structured light | Single frame up to 0.03 mm. Volumetric 0.03 mm + 0.1 mm × L at launch; Revopoint's page gave 0.025 mm + 0.05 mm × L on 2026-11-14 (not re-checked: the page did not load) | 200–400 mm; up to 800,000 points/s (laser) | Feature, marker and global-marker tracking | $999 list at launch; $1,169 on a review page (2026-11-23) | Sponsored post [S6]; Revopoint page, summary only [S7]; review [S8] |
| Revopoint MetroX, **measured** | | 0.40 mm average, 0.29 mm standard deviation on a 300 mm flat plate; scale error on a stepped part | Turntable mode with markers | | | Independent study [S20] |
| Revopoint MetroY Ultra (new, Apr 2026) | 34 crossed + 15 parallel + 1 deep-hole blue line, plus blue structured light, turntable | Volumetric 0.015 mm + 0.04 mm × L | 90 fps | | $1,899; "CMM Edition" $2,499 with a certified ball plate and a year of Revo Measure | News article [S55]; sponsored field check [S56] |
| Revopoint POP 4 (new, 2026) | Blue laser and infrared | 0.03 mm single frame; no volumetric figure | 105 fps | | Kickstarter | News article [S55] |
| Revopoint MIRACO Plus | Infrared structured light, standalone, with a kit of coded targets | Single frame up to 0.04 mm; length accuracy with the target kit 0.02 mm + 0.05 mm × L | Up to 20 fps | Coded targets for the photogrammetry step | about $1,760–2,200 (not re-checked) | Revopoint page, summary only [S10] |
| Shining 3D Einstar | Structured light | None in the listing read (accuracy "-"); resolution 0.1 mm | 980,000 points/s | Not stated | from $959 (unchanged) | Aniwaa listing [S13] |
| Shining 3D Einstar Vega | Infrared MEMS (HD mode) and infrared VCSEL (fast mode), standalone | None in the listing read; point distance 0.05–3 mm (HD) | HD: 100–250 mm, 15 fps. Fast: 350–1,500 mm, 20 fps | Markers are in the box | $1,999 list; $1,499 in a sale to 30 Nov 2026 | Reseller listing [S12] |
| Shining 3D Einstar Rockit | 38 crossed + 7 parallel blue laser lines, plus infrared VCSEL | Up to 0.05 mm; volumetric 0.1 mm/m | Up to 90 fps | Markerless and marker modes | $1,700–1,900 | A review site that does not document its tests; unverified [S14]. Launched 11 Oct 2025 with the Einstar 2 (17 parallel blue lines) [S59] |
| Einstar EZ GO (new, Nov 2026) | Wireless; 30 crossed + 1 single blue line, plus infrared VCSEL | Shown only in images that could not be read | 60 fps | "AI tracking" | Not readable | Sponsored post [S58] |
| 3DMakerpro Moose | Blue structured light | ± 0.03 mm (Lite 0.05 mm) | 10 fps | Marker-free tracking | about $700 in the study; no price on the listing read | Study's table of makers' data [S20]; reseller listing [S67] |
| 3DMakerpro Moose, **measured** | | 0.89 mm average, 0.60 mm standard deviation on the flat plate; close to professional systems on some other objects | Handheld mode | | | Independent study [S20] |
| 3DeVOK MQ (by Scantech) | 22 infrared laser lines plus infrared speckle | Up to 0.08 mm, footnoted as a laboratory figure; no volumetric figure | 150–1,500 mm; up to 70 fps with markers, 30 fps without | Works without markers | about €2,000–2,800 | Maker's page, unchanged [S19]; price from a reseller, not opened [S48] |
| 3DeVOK MT | 34 blue laser lines, 22 infrared lines, infrared speckle | Up to 0.04 mm | | | $6,299 | Reseller listing, not opened [S19b] |
| Matter and Form THREE | Stereo structured light with a DLP projector, 13-megapixel sensors. Tripod and turntable, not handheld | 0.033 mm at 220 mm, 0.150 mm at 400 mm, 0.400 mm at 700 mm | 4 s per scan plus 10–15 s processing | Not needed | $1,499 (list $2,399) | Maker's specification sheet, not re-read [S18]; price from a reseller, not opened [S18b] |

What stands out:
- **Blue laser lines with markers are still the standard for accuracy below $2,500**, and the line
  counts keep rising: 42 + 7 on the Raptor Pro Max [S50], 34 + 15 + 1 on the MetroY Ultra [S55].
  The makers now sell fewer markers, not none: Creality's marker-free blue mode needs a wired PC
  [S50].
- **Revopoint's MetroY Ultra claims the best volumetric figure in the tier** (0.015 mm + 0.04 mm/m)
  and ships a certified ball plate to check it in the field [S55][S56]. It is the only consumer
  product found that lets a user verify the claim.
- **Matter and Form is the only maker here that states how accuracy falls with distance** [S18].
  It loses a factor of twelve between 220 and 700 mm. Stereo triangulation behaves this way
  everywhere, OpenHandScan included.
- **Shining 3D's listings for the Einstar and the Vega give a point distance and no accuracy**
  [S12][S13]. Its own blog says it tests random units, not every unit [S15].
- **Reviews complain about software more than hardware.** A Raptor Pro reviewer lost tracking
  half-way through scans, could not get it back, and blamed the software [S3]. A MetroX reviewer hit
  repeated "more markers" errors and needed about 30 % overlap to merge scans, and found no
  measuring tools in the MetroX software [S8]. A Pika review measured no reference part [S54].
  `DESKTOP_APP_PLAN.md` (section 1) lists more user reports, with links.

### 3.2 Professional, metrology-grade handheld scanners

| Scanner | Technology | Claimed accuracy | Acceptance test | Speed | Markers | Price | Source |
|---|---|---|---|---|---|---|---|
| Creaform HandySCAN 3D BLACK Elite | 22 blue laser lines + 1, Class 2M | 0.025 mm; volumetric 0.020 mm + 0.040 mm/m; mesh resolution 0.100 mm | VDI/VDE 2634 Part 3, ISO/IEC 17025 laboratories | 1,300,000 measurements/s | Positioning targets; Creaform says targets cannot yet be dropped [S22] | Quote only. A competitor's page says from $65,000; unverified [S46] | Maker's page, unchanged [S21] |
| Creaform HandySCAN MAX EVO and MAX EVO Elite (new, Jul 2026), for large parts | Not checked here | Volumetric 0.150 mm + 0.020 mm/m (MAX EVO), 0.075 mm + 0.010 mm/m (MAX EVO Elite) | VDI/VDE 2634 in ISO/IEC 17025 laboratories | Not stated | Targets up to 30 cm apart on the MAX series [S22] | Quote only | Press release [S61] |
| Scantech SIMSCAN-E | 81 lines: 63 crossed blue lines, 17 parallel lines for fine work, 1 line for deep holes | Up to 0.020 mm; volumetric 0.015 mm + 0.035 mm/m, or + 0.012 mm/m with a photogrammetry unit | ISO 17025 laboratory, VDI/VDE 2634 Part 3 | 6,300,000 measurements/s | Not stated on the page | Quote only | Maker's page, unchanged [S23] |
| Scanology SIMSCAN-S Gen2 (new, Mar 2026) | Not checked | 0.015 mm; 0.015 mm + 0.03 mm/m | ISO 10360 | | | Quote only | Maker's news page, summary only [S64] |
| Artec Point | 17 crossed blue laser lines, 7 parallel, 1 single | Up to 0.02 mm; volumetric 0.015 mm + 0.035 mm/m | "ISO & VDI/VDE"; ISO 17025 certificate as a $2,900 extra | 120 fps, 2,800,000 measurements/s | Not target-free | $25,600–29,900; now marked "currently unavailable" | Reseller listing [S25] |
| Artec Neo (new, Sep 2026) | Wireless blue light; tracks on geometry, texture and colour | 0.025 mm "at functional features"; 0.025 mm + 0.035 mm/m | VDI/VDE 2634 Part 3, a certificate per unit | | Targets only as anchors where tolerances matter | Not stated | Trade press [S62] |
| ZEISS T-SCAN hawk 2 | Blue laser crosses, one line for deep pockets, Class 2 | Volumetric 0.02 mm + 0.015 mm/m | Based on ISO 10360 | Not stated | Reference points; a "satellite mode" for parts of several metres | On request. One reseller lists $90,848; unverified [S47] | ZEISS's handheld site, unchanged [S26] |
| Artec Leo | VCSEL structured light, computer and screen built in | Up to 0.1 mm; 0.1 mm + 0.3 mm/m over distance; resolution 0.2 mm | Not stated | Fusion at 22 fps | None needed (geometry and texture) | $38,300 (unchanged) | Reseller listing [S24] |
| Creaform peel 3 | Not checked here | Up to 0.050 mm; volumetric 0.050 mm + 0.100 mm/m; mesh resolution 0.250 mm | Not stated | Not stated | Geometry, texture or targets (`DESKTOP_APP_PLAN.md`, section 1) | $5,990, or $8,990 with CAD software (2026-11-14; not re-checked) | Creaform page and a review, not opened [S28][S28b] |
| Hexagon MARVELSCAN, **measured** | Blue light, as the paper lists it | 0.04 mm average, 0.08 mm standard deviation on the flat plate; 20 mm steps within 0.05 mm | | | Markers | 40,000–60,000 | Independent study [S20] |

What car-part work expects, read from this table:
- **0.02 to 0.05 mm on a part, with a stated length term**, tested against a named standard. The
  length terms keep falling: 0.010 mm/m on the MAX EVO Elite [S61], 0.015 mm/m on ZEISS [S26].
- **Targets, used more sparingly.** Creaform writes that 3D scanning cannot do without targets yet
  [S22]. Artec's Neo uses them only where tolerances matter [S62]; Creality sells "25 % fewer
  markers" [S50]. Nobody read here claims its best figure without them.
- **A separate mode for holes and pockets**: one single laser line (Scantech, Artec Point, ZEISS,
  and now consumer models from Creality and Revopoint) [S23][S25][S26][S51][S55].
- **Markerless scanners in this class are sold at 0.1 mm, not 0.02 mm** (Artec Leo [S24]).
  OpenHandScan's default mode is markerless, so 0.1 mm is the honest class to aim at first.

### 3.3 Open-source and do-it-yourself tools, and phones

| Tool | What it is | Claimed or measured | Limits | Licence, price | Source |
|---|---|---|---|---|---|
| OpenScan Mini | Photogrammetry rig: turntable, Raspberry Pi camera, polarised ring light | Maker: up to 0.02 mm, from its own test print, and it depends on preparation and skill | Objects up to 9 × 9 × 9 cm. Not handheld | Firmware and hardware plans published; the page names no licence. From €232 (unchanged; no new product on the page) | Maker's page [S31] |
| FabScan Pi | Open-source laser scanner built on a Raspberry Pi and its camera | A 2016 paper on the original FabScan: resolution 0.1–0.2 mm in 14 × 14 × 15 cm | Small objects on a turntable. Volunteer project of two or three people | Copyleft (repository copies show GPL v2 or AGPL v3) | Project documentation, still online, no date [S32]; paper, not opened [S33] |
| COLMAP | Photogrammetry software: structure from motion and multi-view stereo | No accuracy claim | Photogrammetry in general needs surface texture, and a known length to set the scale | New BSD | Repository, still maintained [S34] |
| Meshroom (AliceVision) | Photogrammetry software with a node editor | No accuracy claim | As COLMAP | MPL 2.0 | Manual, not opened [S35] |
| Pi handheld scanner | Raspberry Pi 4, Intel RealSense D415, RTAB-Map | Author: the quality does not reach photogrammetry's; the depth is noisy | Loses tracking on plain walls | Open; RTAB-Map is open source | Author's page [S36] |
| SL Sensor (ETH Zurich) | Open structured-light sensor, phase shifting at 5 Hz, for robots | Authors: sub-millimetre, better than two commercial depth cameras | Motion compensation relies on the robot's known motion; not handheld | Open source; licence not checked | Paper, not opened [S37] |
| SLStudio | Open structured-light software, one camera and one projector, 20 Hz | No figure found | Static scanner | Open source; licence not checked | Repository, not opened [S38] |
| Phone LiDAR (iPhone 12 Pro), **measured** | Time-of-flight depth in the phone | ± 1 cm for objects larger than 10 cm; worse below that | Not a tool for parts | App-dependent | Scientific Reports 2021, abstract only [S39] |
| Phone LiDAR with Scaniverse, **measured** | | Errors under 1 % of the object's size for small and medium objects | | | Journal paper 2026, abstract only [S44] |
| Phone photogrammetry, **measured** | Phone photos processed in Agisoft Metashape | A little behind models from SLR photos, against a ZEISS ATOS 5; no figure in the abstract | Needs texture, many photos and a scale | | ISPRS 2024, abstract [S40] |
| Polycam | Phone app | Maker: ± ½ inch on standard interior captures | A room tool | Commercial | Maker's help page, not opened [S41] |

What stands out:
- **Still no open-source project was found that does what OpenHandScan sets out to do**:
  handheld, markerless tracking, projected pattern, laser lines, its own desktop app with
  inspection. A search on 2026-11-23 found only turntable or motorised line-laser projects and a
  depth-camera tool under a non-commercial licence (search summaries only, not cited as sources).
  One search does not prove there is none.
- **Photogrammetry is the strong open route for small textured objects**, and it is weak on
  exactly what car parts are: plain, dark or shiny, and in need of true scale.
- **Phones are centimetre tools** by the measurements found. Consumer scanners now pair with
  phones (Creality Pika [S53]), but none of those was measured independently.

### 3.4 The software side

| Feature | Consumer apps (Creality Scan, Revo Scan, EXStar / StarVision) | Professional (Artec Studio, VXelements, ZEISS INSPECT) | OpenHandScan |
|---|---|---|---|
| Tracking | Feature (geometry), texture, marker; global markers on the MetroX [S6]; an IMU in the Raptor Pro [S1]; "AI tracking" on new models [S58][S67] | Targets (Creaform [S22]); geometry and texture without targets (Artec Leo [S24]); targets only where tolerances matter (Artec Neo [S62]) | Geometry on the dot pattern plus gyro; AprilTag mode; hints when a surface cannot hold the position (`ROADMAP.md`, section 2) |
| Recovery after a loss | A Raptor Pro reviewer found none [S3]. Shining 3D has a rewind (`DESKTOP_APP_PLAN.md`, section 1) | Not checked | Rewind exists; recovery after a real loss is open (`ENGINE_PLAN.md`, X4c) |
| Aligning several scans | Creality: by features or by markers [S4]. Revo Scan: needs about 30 % overlap [S8]. Matter and Form: automatic or by picked points [S18] | ZEISS INSPECT: alignments [S27] | Built: 2 to 8 scans, from the shape, picked points or tags, with "Not sure" and a "share little" caution; a merge adds coverage, not accuracy (section 2) |
| Guided scanning | Not found in the pages read | Artec Studio 20's Autopilot guides new users through capture and processing [S70] | The Scan check names each weak area's reason and a pose to scan it from; a second pass joins the first (section 2) |
| Mesh editing | Clean, fill, simplify, texture | Mesh editing in ZEISS INSPECT [S27]; Artec Studio's chained workflows and command-line scripting [S70] | Clean, remove the table, fill, smooth off edges, simplify, with undo, a map of what the edit moved, and recipes |
| Measuring and inspection | Creality Scan: quick measure "for quick comparison only"; Creality Inspect adds reference-against-scan deviation, GD&T and reports (summary only) [S72]. Revo Scan MetroX: no measuring tools [S8]; Revo Measure, sold separately, gives a CAD-against-scan colour map, 23 GD&T controls and PDF or JSON reports; Revo Scan 6 adds tolerance settings and a deviation map (summary only; prices not re-checked) [S71] | ZEISS INSPECT: 3D inspection, comparison of measured against CAD, reports; what the free licence covers is not clear on the page read again [S27]. Creaform's VXinspect and VXmodel are sold separately [S29]. Artec Studio 20 sends scans straight to ZEISS INSPECT [S70] | Distance, fitted plane / cylinder / hole with ±, relations, the part's axes, sections, flush, comparison with a reference mesh (not a STEP file), report |
| Scan to CAD | Revo Design (built on QuickSurface): $540 a year, $1,900 for Pro on 2026-11-14; that page now shows other content [S11]. MIRACO 2 is announced with automatic scan to CAD (no source opened; not used) | VXmodel [S29]; peel 3 with CAD software [S28b] | Fitted shapes as STEP and a FreeCAD macro, with approved nominal sizes. No free-form surfaces |
| Export | Creality: PLY, OBJ, STL; no STEP [S1][S4]. Vega: PLY, STL, OBJ, ASC [S12]. Matter and Form: OBJ, PLY, XYZ, GLTF, DAE, STL, FBX [S18] | The scanners feed inspection and CAD packages [S21] | PLY, STL, OBJ, laser points, trajectory, JSON, HTML report, CSV, STEP, FreeCAD macro, DXF, SVG |
| Graphics card | Revo Scan's GPU mode needs an NVIDIA RTX 30 or 40 card (as recorded in `DESKTOP_APP_PLAN.md`, section 1; link [S9], not opened again) | Not checked | Any OpenCL GPU, or none (weaker without) |
| Accuracy reporting in the app | None found. The Revopoint software output no calibration data the study could verify [S20]. New: a field check against a certified ball plate on the MetroY Ultra CMM Edition [S56] | Acceptance certificates for the device [S21][S23][S62]; inspection reports about the part [S27] | Per-vertex sigma, a tolerance check with a reason per weak area, a calibration status, a report that states what the ± is not, and a validation document that includes what failed |
| Openness | Free with the scanner, closed; inspection sold separately [S71][S72]. Matter and Form has an open API [S18] | Paid or free-with-limits, closed | MIT, source and file formats open |

Two things are worth noticing:
- **The consumer apps no longer stop at the mesh, but inspection is a paid extra.** Revopoint and
  Creality now offer CAD comparison, GD&T and reports in separate products [S71][S72] (both known
  from search summaries only). The professional suites have done this for years [S27][S29].
- **Nothing found reports uncertainty per vertex**, or tells the user which areas miss a tolerance
  and why. Checked again on 2026-11-23 across the consumer apps, Artec Studio 20 and the scanners
  launched in 2026: none found [S62][S70][S71]. Artec's Neo comes closest in spirit, with targets
  "where tolerances matter" [S62]; MetroY Ultra's ball-plate check verifies the device, not the
  scan [S56]. This is an absence in the pages read, not proof that no product does it.

### 3.5 Holes and edges are hard for everybody

A Loughborough University study of fringe-projection scanning found that artefacts at hole edges
put the measured diameter off by more than 0.25 mm, while hole centres stayed accurate. Finding
the edge in the camera image to sub-pixel accuracy, together with a compensation step, cut the
RMS diameter error by up to 9.3 times [S42][S75] (abstract only). A 2022 study found that the
optical set-up decides how well hole diameters come out of fitted cylinders [S76] (summary only).
No study was found on handheld laser-line scanners and drilled holes.

OpenHandScan's simulated hole error of 1 to 7 mm over open space is far larger than that starting
point. The direction of the fix is the same: take the edge from the image, not from the surface.
The laser-line ends were tried and stop 0.4 to 4 mm short of the rim with today's extraction
(`ENGINE_PLAN.md`, X2).

---

## 4. Comparison

| Point | OpenHandScan (simulated, unbuilt) | Consumer and prosumer (claims; measured where it says so) | Professional metrology (claims; measured where it says so) | Open-source, DIY, phones |
|---|---|---|---|---|
| Technology | Dot-pattern stereo, 2 or 4 red laser lines, gyro | 21–50 blue laser lines with markers, plus infrared pattern without [S1][S50][S55] | 22–81 blue laser lines with targets; or structured light without | Photogrammetry, turntable laser line, depth cameras, phone LiDAR |
| Accuracy, smooth surface | Median 0.02–0.06 mm, p95 0.17–0.25 mm (part alone) | Claimed 0.02–0.1 mm. Measured on a flat plate: 0.3–1.7 mm [S20] | Claimed 0.015–0.025 mm. Measured: about 0.05 mm [S20] | Photogrammetry rig claims 0.02 mm on small objects [S31]; phones ± 1 cm [S39] |
| Accuracy over length | Measured on a 1.3 m plain panel: 0.12–0.50 % (1.6–6.5 mm), no warning | 0.015–0.03 mm + 0.04–0.1 mm/m claimed [S1][S6][S50][S55] | 0.015–0.02 mm + 0.010–0.04 mm/m, tested [S21][S23][S26][S61] | Not stated |
| Edges and thin walls | Walls 0.5–2 mm read 0.5–1.3 mm too thick tracked; 10–32 % of a bracket more than 0.3 mm off; a per-scan choice brings walls 0.04–0.52 mm nearer | Measured: edges blurred, scale error on steps [S20] | Measured: 20 mm steps within 0.05 mm [S20]. Mesh resolution 0.1 mm [S21] | Not found |
| Holes | Ø 25 mm 1–7 mm small over open space; Ø 6–10 mm there give no reading; near a floor within 0.5 mm | No figure found; a single-line deep-hole mode on new models [S51][S55] | A single-line mode for deep holes [S23][S25][S26]; no figure found | Not found |
| Gap and flush | Flush: mean within 0.04 mm; gap not offered | Not found | Not checked | Not found |
| Working distance | 150–450 mm | 160–550 mm with lasers; to 1–1.5 m with infrared [S1][S12] | About 0.3 m stand-off, depth of field 250–550 mm [S21][S23]; Leo 0.35–1.2 m [S24] | Fixed rigs; phones not checked |
| Object size | Brackets to a 1.3 m panel (simulated) | 5 mm to 4 m claimed [S1] | 0.05 to 4 m [S21]; several metres [S26][S61] | About 9 cm on the OpenScan Mini [S31] |
| Frame rate | 60 fps camera; live view 14–39 fps | 15–105 fps [S1][S12][S14][S55] | 22–120 fps [S24][S25] | 5–20 Hz [S37][S38] |
| Laser points | 700–1,560 a frame (2 or 4 lasers) | 0.4–0.8 million a second with lasers [S1][S6] | 1.3–6.3 million a second [S21][S23] | Not applicable |
| Wait after a scan | 1.5–4.8 times the scan's length | Not found | Not found; Leo fuses live on the device [S24] | Not checked |
| Markers | None by default; AprilTags for big smooth panels | Needed in the accurate laser modes, fewer on new models [S1][S8][S50] | Targets, in most [S22]; only where tolerances matter on the Neo [S62] | Not checked |
| Spray | Glossy, dark, chrome | Now and then, says one review [S8]; one listing contradicts itself on this [S1] | Not checked | Not checked |
| Tracking robustness | Dark and speckled scenes 0.35–3.8 mm off; one recording lost; fast motion and recovery untested | Losses in reviews and in the study [S3][S20] | The two tested systems gave complete, consistent models [S20] | Loses plain surfaces [S36] |
| Multi-scan alignment | Yes: 2–8 scans, with doubt shown; adds coverage, beat the better scan on 1 of 5 pairs | Yes, with limits [S4][S8] | Yes [S27] | In other software |
| Inspection against CAD | Yes, against a mesh (STL, OBJ, PLY), with areas judged against the scan's scatter | A paid extra [S71][S72], or none [S8] | Yes [S27][S29] | In other software |
| Fitted shapes to CAD | STEP and FreeCAD macro | Paid add-on [S11] | Yes [S29] | Not found |
| Report | HTML that prints to PDF, CSV, JSON | In the paid add-ons [S71][S72] | Yes [S27] | Not found |
| Uncertainty shown to the user | Yes, per vertex, checked on simulated scans; blind to a region moved whole | None found; a device check with a ball plate on one model [S56] | A certificate for the device [S21][S62]; none per scan found | None found |
| Guided rescanning | Weak areas with a reason and a pose; better than random on the first scan's surface, level on the whole part | Not found | Autopilot for new users [S70] | Not found |
| Calibration health | A Valid / Check / Recalibrate status from every scan | Not found | Not checked | Not found |
| Raw data kept, reprocessing | Yes, always | Not found | Not checked | Photos are the raw data |
| Colour | No | Yes on the ones checked [S12][S19] | Leo has a texture light [S24]; Neo tracks on colour [S62] | Photogrammetry: yes |
| Laser class | 3R | Class I or II [S1] | Class 2 or 2M [S21][S26] | Not applicable |
| Price | $230–300 in parts | $699–2,499 [S53][S55]; $6,299 at the top [S19b] | $6,000–40,000 listed; more by quote | €232 for a rig [S31]; free software |
| Licence | MIT, open hardware | Closed, free with the device; inspection paid [S71] | Closed, paid | Open, mixed licences |
| Installer | No: a folder with `ohs.exe` | Yes | Yes | Varies |
| Proven on real parts | **No** | Partly: sold in volume, one study shows 0.3–1.7 mm on a plate | Yes: acceptance tests, one study confirms | Varies |

---

## 5. Where OpenHandScan stands

### Behind

**1. Nothing is proven.** This is the largest gap and no feature closes it. Kersten's study shows
what happens to low-cost scanners when someone finally measures a flat plate: ten to fifty times
the brochure figure, mostly from scale and calibration errors [S20]. The simulator gives
OpenHandScan a perfect board and matte surfaces, so it cannot show this kind of error. It is the
same today as on 2026-11-14: the housing is printed, nothing is wired.

**2. Holes, gaps and small round features.**
- A Ø 25 mm hole over open space reads 1.1 to 1.7 mm small at true poses and up to 6.9 mm small tracked; Ø 6 and Ø 10 there give no reading. Near a floor it reads 0.2 to 0.75 mm off (+0.58 to +0.75 at true poses). This is worse
  than the "about 1 mm" of 2026-11-14 because the scorecard now includes a plate 120 mm above its
  floor (`VALIDATION.md`, "The scorecard's baseline").
- A gap of 2 to 6 mm between two panels is bridged; gap is not offered (`VALIDATION.md`, "Gap and
  flush between two panels").
- The bracket's Ø 14 bosses read 0.07 to 0.27 mm small in radius, outside any ± (same section).
- Two fixes were measured and not adopted: a wider gap guard (holes better, walls worse) and hole
  rims from the laser lines' ends (no fit within 0.2 mm) (`ENGINE_PLAN.md`, X1, X2).
- `ENGINE_PLAN.md` ranks mounting holes first among the engine's gaps, short by 5 to 15 times.
  The professional reference is 20 mm steps within 0.05 mm, measured [S20].

**3. Thin walls with both sides seen.** Better than on 2026-11-14 and still short: standing walls
of 0.5 to 2 mm read 0.5 to 1.3 mm too thick tracked, a 1 mm rib 0.7 to 1.2 mm. The cause is now
narrowed down (the frames of a wall's two sides are placed 0.3 to 1.0 mm apart by tracking; why is being tested (2026-11-23), and the scan does not see it); a shorter
tracking band and poses refined against the scan were measured and not adopted (`VALIDATION.md`,
"Poses refined against the finished scan", "Tracking on dark, plain and thin parts"). The
per-scan choice "Make only the thin walls finer" takes 0.04 to 0.52 mm off, for 16 to 29 % more
time. A part lying flat is fine (0.5 mm within 0.01 mm).

**4. The length of long plain parts.** New and serious. A 1.3 m plain panel came out 0.12 to
0.50 % wrong in length, 2.4 to 10 times the need, and nothing in the app said so (`VALIDATION.md`,
"The overall size of a large part"). Consumer makers claim about 0.1 to 0.15 mm over that length
[S1][S50], professional ones about 0.03 to 0.07 mm, tested [S21][S61]. OpenHandScan's simulated
1.6 to 6.5 mm is 10 to 50 times the consumer claim. The door panel of 946 mm, which looked
drift-free on 2026-11-14, has features along it; the plain panel does not.

**5. The dense surface is stereo, not laser.** A commercial laser scanner triangulates every point
of its surface from laser lines. OpenHandScan's lines come within 3 mm of 12 to 21 % of the
surface, with two or four lasers (`VALIDATION.md`, "Refine with lasers with two and with four
lasers"); the rest comes from stereo matching with a median error of 0.32 mm per frame, averaged
down by fusion.

**6. Marker mode is the weak mode.** Mesh median 0.49 mm against 0.02 to 0.06 mm markerless, not
measured again since. In the professional scanners read here, targets are what the volumetric
figure rests on [S22][S23]. The reason for OpenHandScan's result is written down: dots and laser
lines spoil the tag corners, and in most frames only two or three 20 mm tags are seen
(`VALIDATION.md`, "Findings and fixes", item 5). The plan to use tags as anchors in Straighten
for long parts is written and not built (`ENGINE_PLAN.md`, X8 step 2).

**7. A merge does not improve accuracy.** Merging is built, and adds coverage. It beat the better
single scan on 1 of 5 pairs, and pairs that share little of the part came out far worse than
either scan, which the app now cautions about (`VALIDATION.md`, "The merge on today's engine",
"Two scans that share little"). Even at true poses a fused merge averages and loses good surface.

**8. Speed.** The wait after a scan is 1.5 to 4.8 times the scan at Standard quality, and the
project's own targets (1 and 3 times) are not met. No comparable figure was found for commercial
apps, but the Artec Leo fuses at 22 fps on the device itself [S24].

**9. Tracking outside the comfortable case.** Dark and speckled scenes end 0.35 to 3.8 mm off;
single frames can slip a few millimetres while the tracker's numbers look healthy; three fixes
were measured and not adopted (`ENGINE_PLAN.md`, X4 to X4c). Fast or jerky motion is untested,
and there is no recovery after a real loss. Commercial scanners lose tracking too [S3][S20], so
this is a gap to the professional tier with targets, not to the consumer tier.

**10. Features still missing.** Gap between panels (measured, not offered); comparison with a
STEP file (a mesh export is needed); free-form scan to CAD; an installer (a folder with
`ohs.exe`, unsigned); colour.

**11. Hardware.**
- 2 or 4 red laser lines against 7 to 81 blue ones; the newest consumer scanners have 42 + 7 and
  34 + 15 + 1 [S50][S55]. Blue has less speckle and scatters less inside paint and plastic
  (`RESEARCH_PLAN.md`, section 3 and 4.2.7). The 4-laser variant gives 1.7 to 2 times the laser
  points and covers 1.3 to 1.8 times the surface, which is still about a fifth of it
  (`VALIDATION.md`, "Four line lasers").
- 1280 × 800 sensors; the Raptor Pro's camera is 1920 × 1200 [S1], Matter and Form's 13 megapixels [S18].
- Class 3R lasers. The commercial handhelds read here are Class 1, 2 or 2M [S1][S21][S26].
- No colour camera. This matters little for reverse engineering.
- The LED projector chosen for the build has about twice the simulated depth error per frame of
  the laser and DOE option (`PARTS.md`). It has no speckle and uses commodity parts, which may
  matter more on real paint; only the hardware can tell.

**12. The CPU path is a second-class result.** Without a graphics card the camera path slides 4.5
to 43.5 mm on the harder scenes, a 0.5 mm plate can be thinned to 0.11 mm without a word, and
there is no sigma, so no Check and no guidance (`VALIDATION.md`, "Known limitations of the
pipeline"; `ENGINE_PLAN.md`, "Where it falls shortest", item 10). Commercial apps have one result.

### Level, as far as simulation can say

- **Smooth-surface accuracy.** 0.02 to 0.06 mm median is inside the 0.02 to 0.1 mm the consumer
  tier claims, and better than what was measured for three consumer scanners on a plate [S20]. It
  is simulated against real, so "level" is the most that can be said.
- **Large smooth parts with features.** The door panel's stretches read 0.045 to 0.055 mm median,
  p95 0.36 to 0.44 mm, without markers. Plain long parts are behind (above).
- **Flush along a seam**: the mean within 0.04 mm in simulation. No consumer figure was found.
- **Working distance, object size and frame rate** are in the consumer range.
- **Tracking modes**: markerless plus a marker option, as in the consumer tier.
- **Aligning several scans** is in the app, as in every commercial app [S4][S8][S27].
- **Mesh editing and export formats** match or exceed the consumer apps.
- **Inspection in breadth**: comparison with a reference, fitted shapes, sections, a report: what
  the consumer makers now sell as add-ons [S71][S72] and what ZEISS INSPECT does [S27], though
  against a mesh, not a STEP file.
- **Guided calibration and self-test.** The consumer tier has a calibration board and a guided
  procedure too [S3][S20]; OpenHandScan's writes a report the user can read.

### Ahead, or doing what the others do not

- **Uncertainty per vertex, checked against the truth.** 2 sigma covers 96 to 99 % of the
  vertices on simulated scans. Nothing like it was found in any other tool, again on 2026-11-23.
  Its limit is written down too: it cannot see a region moved whole by tracking.
- **An answer the user can act on**: the share of the surface within a tolerance, each weak area
  with its reason and a place to scan it from; 99.8 % of what it passes is truly within. Safe,
  and still pessimistic.
- **Inspection that says when a difference is real.** Each area outside a tolerance is marked as
  beyond or within the scan's own scatter (`docs/guide/compare.md`). Not found elsewhere.
- **A calibration status from every scan.** Valid, Check recommended or Recalibration required,
  with what to redo. The consumer tier offers, at best, a device check against a ball plate on
  one model [S56].
- **Two measuring principles that check each other.** The laser lines and the stereo depth are
  compared on every scan, and the disagreement is shown on the model.
- **The raw recording is always kept.** A scan survives a crash and can be reprocessed with
  better software later. Reviews of consumer apps describe lost scans (`DESKTOP_APP_PLAN.md`,
  section 1).
- **A map of what editing moved**, and smoothing that leaves edges alone by default in the recipes.
- **Inspection and CAD export included**, not sold separately as Revopoint and Creality do
  [S71][S72].
- **Honest documents.** The validation file records negative results, biases and the limits of
  the simulator; the report states that a ± is not an accuracy. No maker's page read for this
  comparison does that; Matter and Form's distance table and Shining 3D's blog come closest
  [S18][S15].
- **Open hardware and software under MIT**, with a simulator, so anyone can run the whole chain
  without buying anything.
- **Any graphics card, or none.**
- **Price.** About $230 to 300 in parts against $699 to 2,499 for the current consumer tier read
  here [S53][S55].

The honest reading: OpenHandScan's lead is in **knowing how good a scan is**, and now in
**what the software does with a scan**, not in how good the scan is. That lead only counts once
the sigma has been checked on real scans.

---

## 6. Improvements worth making, ranked

"Plan" says where the item is written: `ROADMAP.md`, `RESEARCH_PLAN.md`, `DESKTOP_APP_PLAN.md`,
`MAINTENANCE_PLAN.md`, `FEATURE_PLAN.md` or `ENGINE_PLAN.md`. "Status" is as of 2026-11-23: **done**
(built and on), **choice** (built, offered per scan, not a default), **measured, not adopted**
(tried against a stop rule written beforehand, recorded, off), **open** (not tried).

| # | Improvement | What it would take | Who shows it can be done | Plan | Status 2026-11-23 |
|---|---|---|---|---|---|
| 1 | **Measure reference parts on the real scanner and publish the result.** A flat plate of 300 mm or more, a ball bar, a stepped block, a bracket with drilled holes, a long plain panel. Report bias, spread and coverage by distance | The hardware. A ball bar from two bearing balls on a rod ([Amazon search](https://www.amazon.com/s?k=25mm+chrome+steel+bearing+balls); they need a matte coat, which adds thickness), a sheet of flat glass for a coarse flatness check ([Amazon search](https://www.amazon.com/s?k=12x12+inch+clear+glass+sheet)), and the calipers already on the parts list. The fits exist in the Fit tool. A few days once the scanner works | Creaform, Scantech and ZEISS quote such tests [S21][S23][S26]. Kersten's plate and staircase are a ready-made recipe that separates good from bad scanners [S20]. Revopoint now ships a certified ball plate with one model [S55] | `ROADMAP.md` section 3 item 6; `RESEARCH_PLAN.md` 4.9.6 and 6.4; `ENGINE_PLAN.md`, "Only the real scanner can settle" | **Open**; waits for the hardware (housing printed, not wired) |
| 1a | **Validate the LED pattern end to end in the simulator before the hardware arrives.** Run the standard scans with the printed-mask pattern, not the DOE pattern | Simulator work only: a projector model for the mask (dot size, blur away from focus), then the scorecard | Not applicable | Not found in the plans read | **Open**. No end-to-end LED result found |
| 2 | **Holes and edges from the camera images and the laser lines.** In the camera images a hole's edge is a grey-level step that can be found to a fraction of a pixel and projected onto the fitted face; a laser line's end marks where the surface stops | A detector for image edges near a fitted plane; a line-end finder that does not trim; a circle fit with its own error model | The Loughborough study cut hole-diameter errors up to 9.3 times with sub-pixel image edges [S42][S75]. Professional scanners carry a single-line mode for holes [S23][S25][S26] | `ENGINE_PLAN.md` X1, X2, X14 | Laser-line ends: **measured, not built** (0.4 to 4 mm short of the rim; `VALIDATION.md`, "Hole rims from the laser lines' ends"). A wider gap guard: **measured, not adopted** ("The gap guard by the matcher's doubt"). The image edge: **open**. Reading the wall below the shoulder (X14): **open** |
| 3 | **Hold the length of long plain parts.** Tags on the table as anchors in Straighten, and a warning from the tracker's own "the surface let the scanner slide" count | Tags as pose-graph nodes from single-frame detections, 300 mm apart at most; a study on 3 seeds | Professional length terms of 0.01 to 0.04 mm/m rest on targets or photogrammetry [S21][S23][S61] | `ENGINE_PLAN.md` X8 | Step 1 **measured: fails** (0.12 to 0.50 %; `VALIDATION.md`, "The overall size of a large part"). Step 2 (anchors) and the warning: **open** |
| 4 | **Align and merge several scans** (M4) | Global registration, a pose graph over all scans, one fusion from all raw frames | Every commercial app has it, with limits users complain about [S4][S8] | `DESKTOP_APP_PLAN.md` M4; `ENGINE_PLAN.md` X7 | The feature: **done** (2 to 8 scans, tags, picked points, "Not sure", "share little" caution). A merge more accurate than the better scan: **measured, not adopted** (`VALIDATION.md`, "The merge on today's engine"); taking each place from the best scan: measured, even its upper bound loses |
| 5 | **Thin walls and edges in the fusion.** Hold a 1 to 2 mm sheet with tracking; finer voxels where the surface is thin | The cause is a camera offset made in tracking (X3, X4); what is left is a tracking model that does not settle outward | Measured: professional scanners keep 20 mm steps within 0.05 mm, low-cost ones blur them [S20] | `ENGINE_PLAN.md` X3, X4, X5 | Opposite-sides rule: **done**. Thin-parts hint: **done**. Finer voxels on thin walls only: **choice** (0.04 to 0.52 mm nearer; `VALIDATION.md`, "Fine mesh detail on thin walls only"). Poses refined against the scan, a shorter tracking band: **measured, not adopted** |
| 6 | **Make marker mode the accurate mode.** Detect targets in images without dots, solve all target positions together, use them as constraints with the loops. Then consider round retro-reflective targets lit from beside the lenses | Software: medium. Hardware for retro-reflective targets: a few red LEDs next to each lens ([Amazon search](https://www.amazon.com/s?k=5mm+red+LED+620nm)) and a sheet of targets ([Amazon search](https://www.amazon.com/s?k=3D+scanner+reference+marker+dots+6mm)). A per-frame emitter switch would help | Creaform's volumetric figure rests on targets [S21][S22]. Revopoint's coded-target kit brings a $2,000 scanner to 0.02 mm + 0.05 mm × L (claimed) [S10]. Scantech's photogrammetry unit improves the length term from 0.035 to 0.012 mm/m (claimed) [S23] | `RESEARCH_PLAN.md` 4.4 and 7.5; `ENGINE_PLAN.md` X8 step 2 | Tags as a start for aligning scans: **done**. Tags in Straighten: **open** (X8 step 2). Marker mode itself: **open**, not measured again |
| 7 | **Tell the user about errors the sigma cannot see.** A term for the pose quality in the vertex sigma; a warning when a plain part lets the scanner slide | An error model per scan from the tracker's weak directions | Nobody found | `ENGINE_PLAN.md` X13, X8 | **Open** |
| 8 | **Use the whole scan for each patch** (spacetime stereo). It removes the stereo error at its source | A GPU stage after tracking; the prototype cut the median error 4 to 6 times in simulation | Not shipped by anyone found | `RESEARCH_PLAN.md` top 10 item 2; `ENGINE_PLAN.md` X6 | Refine from the images inside Make mesh: **measured, not adopted** on time (`VALIDATION.md`, "Refine from the images inside Make mesh") |
| 9 | **Cut the wait after a scan** to 1 time (gaming GPU) and 3 times (integrated) the scan's length | The matcher for the next frame while this one is tracked; laser filtering on the GPU | Artec Leo fuses at 22 fps on a handheld computer [S24]; consumer scanners capture at 60 to 105 fps [S1][S55] | `MAINTENANCE_PLAN.md`; `ENGINE_PLAN.md` X9, X10 | **Open**: 4.8 and 1.5 times today |
| 10 | **Tracking that recovers.** Find the pose again after a loss, test fast and jerky motion, hold a weakly held step | Relocalisation by global registration against the model; simulator recordings with blur | Shining 3D's rewind (`DESKTOP_APP_PLAN.md`, section 1). Consumer reviews show how much users mind [S3] | `ROADMAP.md` section 2; `ENGINE_PLAN.md` X4 to X4c | The scan's start, a slip check, a lag in the tracking model: **measured, not adopted** (the three together miss on several scenes). Recovery after a loss: **open** |
| 11 | **Comparison with CAD** | The in-app deviation map against a reference | Free ZEISS INSPECT compares a mesh with CAD [S27]; Revopoint and Creality sell it [S71][S72] | `FEATURE_PLAN.md` I2 | **Done** against a mesh (STL, OBJ, PLY), with best fit, datums or picked points. A STEP file as the reference: **open** |
| 12 | **An error bar for fitted shapes that knows the biases** | An error model per feature type; then the reference parts of item 1 | Nobody found | `ROADMAP.md` section 3 | Widened for patchy errors: **done** (`VALIDATION.md`, "The fits' ± on today's engine"). Known biases offered when approving nominals: **choice** (`FEATURE_PLAN.md` C3). A floor per quantity: **measured, not adopted** (it would be an accuracy claim) |
| 13 | **Gap between panels** | The depth must stop filling narrow openings | Not found in any product read | `FEATURE_PLAN.md` I4 | Flush: **done**. Gap: **measured, not offered** |
| 14 | **Next hardware revision.** In order of value: (a) a longer baseline or sensors with more pixels, since the depth step per pixel is z² / (f · B) (`RESEARCH_PLAN.md`, section 3); (b) blue line lasers, and more lines; (c) laser power inside Class 2 | (a) Two separate global-shutter cameras with a hardware trigger ([Amazon search](https://www.amazon.com/s?k=global+shutter+USB+camera+module+external+trigger)) in place of the single 60 mm board; a new faceplate; recalibration. (b) Blue line modules ([Amazon search](https://www.amazon.com/s?k=450nm+blue+line+laser+module+12mm); check the power class before buying) and a way to tell several lines apart. (c) Lower power with a longer exposure or a larger lens opening | Every laser scanner in sections 3.1 and 3.2 uses blue lines, 7 to 81 of them, at Class 1 to 2M [S1][S21][S23][S26][S50] | `RESEARCH_PLAN.md` 4.2.7, 7.12 | More lines: the 4-laser variant is **designed and in software**, simulated only; it does not change the comparison. Blue lines, baseline, pixels, laser class: **open** |
| 15 | **Difficult surfaces.** Exposure and emitter power that follow the surface, a polariser option, plain advice when spray is needed | Control loop through the Pico; polariser film ([Amazon search](https://www.amazon.com/s?k=linear+polarizer+film+sheet)); simulator upgrades for gloss | One blue-laser listing claims metal parts without spray and, further down, asks for spray on reflective metal [S1]; a review says spray is needed now and then [S8]. So nobody has solved it | `RESEARCH_PLAN.md` 4.10 | **Open**; needs the hardware |
| 16 | **Calibration health on every scan**, with temperature | Cross-check of stereo, laser plane and laser-by-stereo; log the IMU's temperature | The study could not get verifiable calibration data out of a consumer app [S20] | `FEATURE_PLAN.md` H1 to H5 | **Done** in simulation (`VALIDATION.md`, "The calibration's status"). Temperature and real limits: **open**, need the hardware (H5) |
| 17 | **One download that installs** | Packaging (M6) | All commercial apps | `DESKTOP_APP_PLAN.md` M6 | A trial folder with `ohs.exe` (about 526 MB): **done**. An installer and signing: **open** |

Not recommended now:
- **A colour camera.** Every consumer scanner has one, and car-part reverse engineering does not
  need it.
- **Chasing "0.02 mm".** The markerless professional scanner is sold at 0.1 mm [S24], and three
  consumer scanners sold at 0.03 to 0.08 mm measured 0.3 to 1.7 mm on a plate [S20]. A measured,
  published 0.1 to 0.2 mm on real parts, with honest error bars, would already be better
  information than this price class offers.
- **Trying again what was measured and failed**, listed in `ENGINE_PLAN.md`, "Not worth trying
  again (measured)".

---

## 7. What could not be verified

- **Makers' own pages.** On 2026-11-14 Revopoint's, Creality's store, Einstar's and Matter and
  Form's shop pages refused automated reading; Artec's and ZEISS's main pages came back empty. On
  2026-11-23 Revopoint's pages again returned only a title or "too many requests", Artec's news
  pages and Creality's wiki returned only titles, and several news sites refused (all3dp,
  fabbaloo, techpowerup, Wiley). Figures from those come from reseller listings, press releases
  carried by news sites, sponsored posts, or a search result's summary. The source list marks
  each one.
- **The Kersten study's numbers** [S20] were read from its full text on 2026-11-14. On 2026-11-23
  the paper was still online (published 22 July 2026) and its abstract was read again, but the
  PDF's tables could not be read automatically, so the table in section 1 was not checked again.
- **Prices** change weekly and differ by seller. Creaform, Scantech, Scanology, ZEISS and Artec's
  Neo sell by quote; the two figures given for Creaform and ZEISS come from one competitor's page
  and one reseller and are marked unverified. Revopoint's software prices (S11) could not be
  confirmed: the page now shows other content.
- **Independent accuracy** for the Creality Raptor line, the Einstar line, Artec, Creaform,
  Scantech and ZEISS: none found, again on 2026-11-23. Review sites that give gauge-block numbers
  without a method are left out.
- **The Revopoint MetroX's volumetric figure** appears as 0.03 mm + 0.1 mm × L and, on 2026-11-14
  only, as 0.025 mm + 0.05 mm × L. The second could not be checked again.
- **The Matter and Form specification sheet** [S18] could not be read again; its numbers are
  those of 2026-11-14.
- **New products known from summaries only** and therefore not in the tables: Revopoint RANGE 3
  and MIRACO 2, Shining 3D EinScan Trak and FreeScan Omni, Artec Point II and Spider II 2026,
  3DeVOK MT Gen2, ELEGOO M1 Ultra, Hexagon HYPERSCAN. Their figures were not read on a page.
- **The Einstar EZ GO's accuracy and price** are only in images that could not be read [S58].
- **The consumer inspection software** (Revo Measure, Revo Scan 6, Creality Inspect) is known from
  search summaries only [S71][S72]; whether either shows uncertainty was not seen on a page.
- **What the free ZEISS INSPECT licence covers** is not clear on the page read again [S27].
- **Processing time after a scan** in commercial apps: no figure found.
- **Whether any commercial app shows uncertainty per vertex:** none found in the pages read. Not proof.
- **Artec Studio's price and Revo Scan's GPU requirement** are taken from `DESKTOP_APP_PLAN.md` and
  were not opened again.
- **Papers behind browser checks** (the hole studies [S42][S75][S76], the phone LiDAR study [S39],
  the handheld-scanner standard discussion [S43], the dental study [S74]): only their abstracts,
  as summarised by the search, were used.
- **Licences** of OpenScan, FabScan Pi, SL Sensor and SLStudio were not read from their licence files.
- **Which projector the validation scans simulate.** `VALIDATION.md` does not say. A comment in
  `ohs/sim/nominal.py` describes a DOE field of about 10,000 dots. If that is right, the headline
  figures belong to option A's pattern and the LED build route has no end-to-end figure yet.
- **Background knowledge.** No fact about another product in this document comes from memory;
  where no page could be read, the gap is stated instead.
- **OpenHandScan's numbers** are all simulated, from one hand model, mostly a few seeds a scene;
  the long-panel study used one scene and three seeds (`VALIDATION.md`, "The overall size of a
  large part", "Limits of this study").

---

## Sources

Opened and read on 2026-11-14 unless marked. "Summary only" means the page was not opened and the
claim comes from a web search's summary of it. "2026-11-23" notes what a second visit found.

**This repository** (read 2026-11-23)
- `README.md` (root): "How it works", "Parts to order", "Stereo board measurements", "4-laser variant".
- `PARTS.md`: "Pattern projector: two options", "Notes before ordering".
- `docs/BUILD.md`: laser safety.
- `firmware/README.md`: "ESP32 instead of a Pico", laser safety.
- `software/README.md`: sections 1, 7, 8, 11.
- `software/VALIDATION.md`: "Results (full runs)", "GPU processing", "The waits a user feels", "Fitted shapes against the truth", "The Check answer against the truth", "The vertex sigma", "Where a tracked scan's shape errors come from", "Four line lasers", "Aligning and merging two scans", "The fits' ± on today's engine", "Smoothing's within 2σ limit on today's engine", "Sections", "A scan compared with a reference model", "Gap and flush between two panels", "Which signs show a stale calibration", "The calibration's status", "Why an area is weak, and where to scan it from", "A second pass that joins the first", "Hints while scanning", "The scorecard's baseline", "Hole rims from the laser lines' ends", "Poses refined against the finished scan", "Fine mesh detail on thin walls only", "Make only the thin walls finer in the app", "The merge on today's engine", "Two scans that share little", "The overall size of a large part", "Findings and fixes", "Simulation limitations", "Known limitations of the pipeline".
- `software/docs/ENGINE_PLAN.md`: "The error budget today", "Where it falls shortest", X1 to X14, "Not worth trying again (measured)", "Only the real scanner can settle".
- `software/docs/FEATURE_PLAN.md`: sections K, I, C, H, N, R, P (headings and status), I3, I4, C1 to C3 in full.
- `software/docs/APP_API.md`: headings.
- `software/docs/ROADMAP.md`: sections 1 to 5.
- `software/docs/RESEARCH_PLAN.md`: sections 1 to 3, 4.2.7, 4.7, 4.9, 4.10, 5, 6, 7.12.
- `software/docs/DESKTOP_APP_PLAN.md`: sections 1, 2, 5, 8.
- `software/docs/MAINTENANCE_PLAN.md`: overview table.
- `docs/guide/README.md`, `docs/guide/compare.md`, `docs/guide/getting-started.md` ("The Windows program (ohs.exe)", "Disk space and memory").

**Consumer and prosumer scanners**
- S1. Top 3D Shop, Creality Raptor Pro listing. 2026-11-23: read again, unchanged. https://top3dshop.com/product/creality-raptor-pro-3d-scanner
- S3. 3DPrint.com, review of the Creality Raptor Pro (May 2025). 2026-11-23: read again, unchanged. https://3dprint.com/317412/creality-raptor-pro-software-limited/
- S4. Creality Wiki, Sermoon S1 FAQ. Summary only. https://wiki.creality.com/en/3d-scanner/sermoon-series/s1-faq
- S5. B&H, Creality CR-Scan Otter listing. Summary only. https://www.bhphotovideo.com/c/product/1833196-REG/creality_cr_scan_otter_portable_3d.html
- S6. CNX Software, Revopoint MetroX (sponsored post, November 2024). 2026-11-23: read again, unchanged. https://www.cnx-software.com/2024/11/04/revopoint-metrox-3d-scanner-blue-laser-scanning-breakthrough-at-an-appealing-price/
- S7. Revopoint, MetroX product page. Summary only; 2026-11-23: could not be reached (title only, then "too many requests"). https://www.revopoint3d.com/pages/3d-laser-scanner-metrox
- S8. 3DPrinting.com, Revopoint MetroX review. 2026-11-23: read again; no reference part measured; price shown $1,169. https://3dprinting.com/reviews/revopoint-metrox-review/
- S9. Revopoint, Revo Scan 5 MetroX 5.6.4 release note. Not opened; cited in `DESKTOP_APP_PLAN.md`. https://global.revopoint3d.com/blogs/news/revo-scan-5-metrox-v5-6-4-released-with-gpu-acceleration-for-windows
- S10. Revopoint, MIRACO Plus product page. Summary only; 2026-11-23: could not be reached. https://www.revopoint3d.com/products/all-in-one-3d-scanner-miracoplus
- S11. Revopoint, software prices (Revo Measure, Revo Design). Summary only; 2026-11-23: the address now shows a different event page with no prices. https://www.revopoint3d.com/pages/2025md
- S12. MatterHackers, Einstar Vega listing. 2026-11-23: read again; $1,499 in a sale (list $1,999). https://www.matterhackers.com/store/l/einstar-vega-wireless-handheld-3d-scanner/sk/M1RMZN1R
- S13. Aniwaa, Shining 3D Einstar listing. 2026-11-23: read again, unchanged. https://www.aniwaa.com/product/3d-scanners/shining-3d-einstar/
- S14. 3D Tech Valley, Einstar Rockit review. Tests not documented; used for the listed specifications only. 2026-11-23: read again, unchanged. https://www.3dtechvalley.com/einstar-rockit-review/amp/
- S15. Einstar (Shining 3D), "The accuracy of Einstar: what you really need to know". Summary only. https://www.einstar.com/blogs/news/the-accuracy-of-einstar-what-you-really-need-to-know
- S16. Creative Bloq, 3DMakerpro Moose review. Summary only. Kept for reference; no longer cited above. https://creativebloq.com/3d/3dmakerpro-moose-3d-scanner-review-compact-device-with-great-results
- S18. Matter and Form, THREE technical specifications (PDF). 2026-11-23: the file opens but its text could not be read; not checked again. https://cdn.shopify.com/s/files/1/1745/6181/files/THREE_Technical_Specifications.docx.pdf
- S18b. MatterHackers, Matter and Form THREE listing. Summary only. https://www.matterhackers.com/store/l/matter-and-form-three-3d-scanner/sk/MZV7GS5U
- S19. Scantech (Scanology), 3DeVOK MQ product page. 2026-11-23: read again, unchanged. https://www.3d-scantech.com/product/top-choice-for-professionals/
- S19b. RobotShop, 3DeVOK MT listing. Summary only. https://www.robotshop.com/products/3devok-mt-infrared-blue-laser-professional-3d-scanner-004mm-accuracy
- S48. OpenELAB, 3DeVOK MQ listing. Summary only. https://openelab.io/products/3devok-mq-professional-3d-scanner
- S50. TechNode Global, Creality press release on the Raptor Pro Max (November 2026). Read 2026-11-23. https://technode.global/?p=998322
- S51. TechNode Global, Creality press release on the Sermoon P1S (September 2026). Read 2026-11-23. https://technode.global/prnasia/creality-launches-sermoon-p1s-an-all-in-one-blue-laser-3d-scanner-with-built-in-computing/
- S53. Utusan (PR Newswire), Creality Pika on sale at $699 (July 2026). Read 2026-11-23. https://www.utusan.com.my/pr-newswire-en/2026/07/creality-pika-pocket-sized-ai-3d-scanner-is-now-on-sale-at-699/
- S54. CNX Software, Creality Pika review (July 2026). Read 2026-11-23. https://cnx-software.com/2026/07/26/creality-pika-review-an-affordable-portable-3d-scanner-with-infrared-and-blue-laser-modes
- S55. 3D Printing Industry, Revopoint MetroY Ultra and POP 4 (28 April 2026). Read 2026-11-23. https://3dprintingindustry.com/news/revopoint-launches-metroy-ultra-and-opens-pop-4-kickstarter-sign-ups-251017/
- S56. 3DVF, MetroY Ultra and its one-click verification (3 June 2026), written in partnership with Revopoint. Read 2026-11-23. https://3dvf.com/en/redaction/revopoint-metroy-ultra-3d-scanner-0-015mm-accuracy-one-click-verification/
- S58. CNX Software, Einstar EZ GO (sponsored, 19 November 2026). Read 2026-11-23; figures only in images. https://www.cnx-software.com/2026/11/19/einstar-ez-go-your-first-ai-powered-mobile-3d-scanner/
- S59. 3DPrinting.com, Shining 3D launches the Einstar Rockit and Einstar 2 (October 2025). Read 2026-11-23. https://3dprinting.com/news/shining-3d-product-launch-einstar-rockit-and-einstar-2/
- S67. MatterHackers, 3DMakerpro Moose listing. Read 2026-11-23. https://matterhackers.com/store/l/3dmakerpro-moose-3d-scanner/sk/MM82Y2GX

**Independent tests and papers**
- S20. Kersten, Marsmann, Przybilla, "Geometrical accuracy investigations of handheld 3D scanners in comparison: low-cost vs. high-end", ISPRS Archives XLIX-B1-2026, pp. 263-270, peer reviewed. Full text read 2026-11-14. 2026-11-23: still online; abstract read again; tables not re-read. https://isprs-archives.copernicus.org/articles/XLIX-B1-2026/263/2026/isprs-archives-XLIX-B1-2026-263-2026.pdf
- S39. Luetzenburg, Kroon, Bjørk, "Evaluation of the Apple iPhone 12 Pro LiDAR for an application in geosciences", Scientific Reports 2021. Abstract, summary only. https://doi.org/10.1038/s41598-021-01763-9
- S40. Kersten, Sönksen, Przybilla, "Geometric accuracy investigations of mobile phone devices in the laboratory using high-precision reference bodies", ISPRS Archives 2024. Abstract. https://isprs-archives.copernicus.org/articles/XLVIII-2-W8-2024/211/2024/
- S42. Loughborough University, "Accurate characterisation of hole geometries by fringe projection profilometry". Abstract, summary only. https://repository.lboro.ac.uk/articles/conference_contribution/Accurate_characterisation_of_hole_geometries_by_fringe_projection_profilometry/9550679/1
- S43. Bonin, Khameneifar, Mayer, "Evaluation of the metrological performance of a handheld 3D laser scanner using a pseudo-3D ball-lattice artifact", Sensors 2021. Abstract, summary only. https://www.mdpi.com/1424-8220/21/6/2137/htm
- S44. Geodetski vestnik 2026, accuracy of Scaniverse on an iPhone. Abstract, summary only. https://geodetski-vestnik.com/en/clanek/10.15292/geodetski-vestnik.2026.01.47-61
- S33. ISPRS Archives XLI-B3 (2016), paper that tested the original FabScan. Summary only. https://isprs-archives.copernicus.org/articles/XLI-B3/261/2016/isprs-archives-XLI-B3-261-2016.pdf
- S73. Kersten and others, mobile mapping systems against a terrestrial laser scanner, ISPRS Archives XLVIII-1-W6-2025. Read 2026-11-23. https://isprs-archives.copernicus.org/articles/XLVIII-1-W6-2025/107/2025/
- S74. Journal of Prosthodontics 2026, face scans with two handheld scanners and a smartphone. Summary only; the page refused. https://onlinelibrary.wiley.com/doi/full/10.1111/jopr.70228
- S75. Wu, Dantanarayana, Yue, Huntley, Loughborough, SPIE 2017, hole geometries by fringe projection. Summary only. https://dspace.lboro.ac.uk/dspace-jspui/handle/2134/25980
- S76. Schild and others, Measurement Science and Technology 2022, hole diameters from fitted cylinders. Summary only. https://publikationen.bibliothek.kit.edu/1000147685/148863644

**Professional scanners and software**
- S21. Creaform, HandySCAN 3D BLACK series technical specifications. 2026-11-23: read again, unchanged. https://www.creaform3d.com/en/portable-3d-scanner-handyscan-3d/technical-specifications
- S22. Creaform, blog post on the HandySCAN 3D MAX series and targets. 2026-11-23: read again, unchanged. https://www.creaform3d.com/en/resources/blog/new-3d-scanner-revolutionizing-extensive-surface-damage-assessment
- S23. Scantech (Scanology), SIMSCAN-E product page. 2026-11-23: read again, unchanged. https://www.3d-scantech.com/product/simscan-e-intelligent-wireless-and-palm-sized-3d-scanner/
- S24. MatterHackers, Artec Leo listing. 2026-11-23: read again, unchanged. https://www.matterhackers.com/store/l/artec-leo-3d-scanner/sk/MY5TQJ0S
- S25. MatterHackers, Artec Point listing. 2026-11-23: read again; now marked "currently unavailable". https://www.matterhackers.com/store/l/artec-point
- S26. ZEISS #HandsOnMetrology, T-SCAN hawk 2. 2026-11-23: read again, unchanged. https://www.handsonmetrology.com/products/t-scan-hawk-2/
- S27. ZEISS #HandsOnMetrology, ZEISS INSPECT. 2026-11-23: read again; what the free licence covers is not clear. https://www.handsonmetrology.com/products/software/zeiss-inspect/
- S28. Creaform, peel 3 page. Summary only. https://go.creaform3d.com/peel-3d-ab
- S28b. 3D Printing Industry, peel 3 Pro.CAD review. Summary only. https://3dprintingindustry.com/news/review-peel-3-pro-cad-professional-3d-scanning-without-the-price-tag-238026/
- S29. Creaform, VXelements brochure (hosted by a reseller). Summary only. https://www.ashtead-technology.com/wp-content/uploads/2023/05/VXelement-Brochure.pdf
- S30. Artec 3D, Artec Studio page. Summary only. Kept for reference; replaced above by S70. https://www.artec3d.com/software/artec-studio
- S46. Artec 3D, comparison page that quotes a HandySCAN price. Competitor's claim; summary only. https://creaform-handyscan.artec3d.com
- S47. MatterHackers, ZEISS T-SCAN hawk 2 listing. Summary only. https://www.matterhackers.com/store/l/zeiss-t-scan-hawk-2-3d-scanner
- S61. Creaform, press release on the MAX EVO and EVO series (8 July 2026). Read 2026-11-23. https://www.creaform3d.com/en/about-us/press-releases/faro-creaform-strengthens-handyscan-3d-portfolio-with-next-generation-max-series-and-new-evo-series-models
- S62. Engineering.com, Artec 3D launches the Neo (September 2026). Read 2026-11-23. https://www.engineering.com/artec-3d-launches-wireless-scanner-for-dimensional-control/
- S64. Scanology, SIMSCAN-S Gen2 (March 2026). Summary only. https://www.3d-scantech.com/scanology-introduces-simscan-s-gen2-confidence-in-every-critical-surface/
- S70. 3DPrint.com, Artec Studio 20 update. Read 2026-11-23. https://3dprint.com/320445/artec-3ds-as20-software-update-supercharges-its-entire-scanner-lineup/
- S71. Revopoint, Revo Measure page (with the Revo Scan 6 page, https://www.revopoint3d.com/pages/revo-scan-6). Summary only; the product page returned "too many requests". https://www.revopoint3d.com/pages/revo-measure
- S72. Creality Wiki, Creality Inspect tutorial. Summary only; the page gave its title only. Which wiki page the "quick comparison only" remark came from is not certain. https://wiki.creality.com/en/3d-scanner/products/inspectsoftware/tutorial

**Open-source, DIY and phones**
- S31. OpenScan, OpenScan Mini. 2026-11-23: read again, unchanged. https://www.openscan.eu/openscan-mini
- S32. FabScan Pi documentation. 2026-11-23: still online. https://fabscanpi-server.readthedocs.io/
- S34. COLMAP repository. 2026-11-23: still online, BSD licence. https://github.com/colmap/colmap
- S35. Meshroom manual, licences. Summary only. https://meshroom-manual.readthedocs.io/en/latest/about/licenses/licenses.html
- S36. Eleccelerator, "Pi handheld 3D scanner". https://eleccelerator.com/pi-handheld-3d-scanner/
- S37. Lam et al., "SL Sensor: an open-source, ROS-based, real-time structured light sensor", arXiv 2201.09025. Summary only. https://arxiv.org/pdf/2201.09025
- S38. SLStudio repository (a copy). Summary only. https://www.github.com/arkbriar/slstudio
- S41. Polycam, "How accurate are Polycam scans". Summary only; the page refused to open. https://learn.poly.cam/hc/en-us/articles/50434745985556-How-Accurate-Are-Polycam-Scans

[S1]: https://top3dshop.com/product/creality-raptor-pro-3d-scanner
[S3]: https://3dprint.com/317412/creality-raptor-pro-software-limited/
[S4]: https://wiki.creality.com/en/3d-scanner/sermoon-series/s1-faq
[S5]: https://www.bhphotovideo.com/c/product/1833196-REG/creality_cr_scan_otter_portable_3d.html
[S6]: https://www.cnx-software.com/2024/11/04/revopoint-metrox-3d-scanner-blue-laser-scanning-breakthrough-at-an-appealing-price/
[S7]: https://www.revopoint3d.com/pages/3d-laser-scanner-metrox
[S8]: https://3dprinting.com/reviews/revopoint-metrox-review/
[S9]: https://global.revopoint3d.com/blogs/news/revo-scan-5-metrox-v5-6-4-released-with-gpu-acceleration-for-windows
[S10]: https://www.revopoint3d.com/products/all-in-one-3d-scanner-miracoplus
[S11]: https://www.revopoint3d.com/pages/2025md
[S12]: https://www.matterhackers.com/store/l/einstar-vega-wireless-handheld-3d-scanner/sk/M1RMZN1R
[S13]: https://www.aniwaa.com/product/3d-scanners/shining-3d-einstar/
[S14]: https://www.3dtechvalley.com/einstar-rockit-review/amp/
[S15]: https://www.einstar.com/blogs/news/the-accuracy-of-einstar-what-you-really-need-to-know
[S16]: https://creativebloq.com/3d/3dmakerpro-moose-3d-scanner-review-compact-device-with-great-results
[S18]: https://cdn.shopify.com/s/files/1/1745/6181/files/THREE_Technical_Specifications.docx.pdf
[S18b]: https://www.matterhackers.com/store/l/matter-and-form-three-3d-scanner/sk/MZV7GS5U
[S19]: https://www.3d-scantech.com/product/top-choice-for-professionals/
[S19b]: https://www.robotshop.com/products/3devok-mt-infrared-blue-laser-professional-3d-scanner-004mm-accuracy
[S20]: https://isprs-archives.copernicus.org/articles/XLIX-B1-2026/263/2026/isprs-archives-XLIX-B1-2026-263-2026.pdf
[S21]: https://www.creaform3d.com/en/portable-3d-scanner-handyscan-3d/technical-specifications
[S22]: https://www.creaform3d.com/en/resources/blog/new-3d-scanner-revolutionizing-extensive-surface-damage-assessment
[S23]: https://www.3d-scantech.com/product/simscan-e-intelligent-wireless-and-palm-sized-3d-scanner/
[S24]: https://www.matterhackers.com/store/l/artec-leo-3d-scanner/sk/MY5TQJ0S
[S25]: https://www.matterhackers.com/store/l/artec-point
[S26]: https://www.handsonmetrology.com/products/t-scan-hawk-2/
[S27]: https://www.handsonmetrology.com/products/software/zeiss-inspect/
[S28]: https://go.creaform3d.com/peel-3d-ab
[S28b]: https://3dprintingindustry.com/news/review-peel-3-pro-cad-professional-3d-scanning-without-the-price-tag-238026/
[S29]: https://www.ashtead-technology.com/wp-content/uploads/2023/05/VXelement-Brochure.pdf
[S30]: https://www.artec3d.com/software/artec-studio
[S31]: https://www.openscan.eu/openscan-mini
[S32]: https://fabscanpi-server.readthedocs.io/
[S33]: https://isprs-archives.copernicus.org/articles/XLI-B3/261/2016/isprs-archives-XLI-B3-261-2016.pdf
[S34]: https://github.com/colmap/colmap
[S35]: https://meshroom-manual.readthedocs.io/en/latest/about/licenses/licenses.html
[S36]: https://eleccelerator.com/pi-handheld-3d-scanner/
[S37]: https://arxiv.org/pdf/2201.09025
[S38]: https://www.github.com/arkbriar/slstudio
[S39]: https://doi.org/10.1038/s41598-021-01763-9
[S40]: https://isprs-archives.copernicus.org/articles/XLVIII-2-W8-2024/211/2024/
[S41]: https://learn.poly.cam/hc/en-us/articles/50434745985556-How-Accurate-Are-Polycam-Scans
[S42]: https://repository.lboro.ac.uk/articles/conference_contribution/Accurate_characterisation_of_hole_geometries_by_fringe_projection_profilometry/9550679/1
[S43]: https://www.mdpi.com/1424-8220/21/6/2137/htm
[S44]: https://geodetski-vestnik.com/en/clanek/10.15292/geodetski-vestnik.2026.01.47-61
[S46]: https://creaform-handyscan.artec3d.com
[S47]: https://www.matterhackers.com/store/l/zeiss-t-scan-hawk-2-3d-scanner
[S48]: https://openelab.io/products/3devok-mq-professional-3d-scanner
[S50]: https://technode.global/?p=998322
[S51]: https://technode.global/prnasia/creality-launches-sermoon-p1s-an-all-in-one-blue-laser-3d-scanner-with-built-in-computing/
[S53]: https://www.utusan.com.my/pr-newswire-en/2026/07/creality-pika-pocket-sized-ai-3d-scanner-is-now-on-sale-at-699/
[S54]: https://cnx-software.com/2026/07/26/creality-pika-review-an-affordable-portable-3d-scanner-with-infrared-and-blue-laser-modes
[S55]: https://3dprintingindustry.com/news/revopoint-launches-metroy-ultra-and-opens-pop-4-kickstarter-sign-ups-251017/
[S56]: https://3dvf.com/en/redaction/revopoint-metroy-ultra-3d-scanner-0-015mm-accuracy-one-click-verification/
[S58]: https://www.cnx-software.com/2026/11/19/einstar-ez-go-your-first-ai-powered-mobile-3d-scanner/
[S59]: https://3dprinting.com/news/shining-3d-product-launch-einstar-rockit-and-einstar-2/
[S61]: https://www.creaform3d.com/en/about-us/press-releases/faro-creaform-strengthens-handyscan-3d-portfolio-with-next-generation-max-series-and-new-evo-series-models
[S62]: https://www.engineering.com/artec-3d-launches-wireless-scanner-for-dimensional-control/
[S64]: https://www.3d-scantech.com/scanology-introduces-simscan-s-gen2-confidence-in-every-critical-surface/
[S67]: https://matterhackers.com/store/l/3dmakerpro-moose-3d-scanner/sk/MM82Y2GX
[S70]: https://3dprint.com/320445/artec-3ds-as20-software-update-supercharges-its-entire-scanner-lineup/
[S71]: https://www.revopoint3d.com/pages/revo-measure
[S72]: https://wiki.creality.com/en/3d-scanner/products/inspectsoftware/tutorial
[S73]: https://isprs-archives.copernicus.org/articles/XLVIII-1-W6-2025/107/2025/
[S74]: https://onlinelibrary.wiley.com/doi/full/10.1111/jopr.70228
[S75]: https://dspace.lboro.ac.uk/dspace-jspui/handle/2134/25980
[S76]: https://publikationen.bibliothek.kit.edu/1000147685/148863644
