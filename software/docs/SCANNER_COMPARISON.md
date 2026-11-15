# OpenHandScan compared with existing 3D scanning tools

Written 2026-11-14. It compares OpenHandScan with consumer, professional and open-source scanning
tools, and says where OpenHandScan can improve. OpenHandScan's numbers come from this repository's
documents; the section is named each time. Everything about other products comes from the web, with a
source tag such as [S21] that links to the page. The list of sources at the end says which pages
were opened and read, and which are known only from a search result's summary.

## Summary

1. **Two different kinds of numbers are being compared.** Every OpenHandScan figure is simulated and
   the hardware is not built ([`VALIDATION.md`](../VALIDATION.md), first paragraph). Every maker's
   figure is a best-case claim. The one independent laboratory test found for current low-cost
   scanners measured 0.3 to 1.7 mm of error on a flat 300 mm granite plate, for scanners sold with
   0.03 to 0.08 mm. Two professional systems in the same test stayed at about 0.05 mm [S20].
2. **On smooth surfaces OpenHandScan's simulated result is in the range the consumer tier claims.**
   Mesh median 0.065 to 0.088 mm, laser points 0.07 mm. A simulated door panel of 946 mm did not
   drift. This is unproven until reference parts are measured on the real scanner, and the
   simulator leaves out the things that hurt real low-cost scanners most: calibration that is a
   little wrong, gloss, heat.
3. **OpenHandScan is clearly behind on edges, thin walls and holes.** A 2 mm flange reads 3.0 mm
   with tracking, a Ø 25 mm hole reads about 1 mm small, and holes of Ø 6 to 10 mm can stay closed.
   Professional laser scanners are sold at 0.02 to 0.025 mm with a tested volumetric accuracy
   [S21][S23], and the two professional systems that were tested independently reproduced 20 mm
   steps within 0.05 mm [S20]. Holes are what car parts are mounted by, so this is the gap that
   matters most.
4. **It is behind on hardware and on features that are standard elsewhere.** Two red laser lines
   against 22 to 81 blue ones; a 1-megapixel stereo pair with a 60 mm baseline; Class 3R lasers
   where commercial units are Class 2. No merging of several scans, no comparison with CAD, no
   colour. The wait after a scan is 1.5 to 4.8 times the scan's length.
5. **It is ahead where nobody else seems to compete.** A per-vertex uncertainty in millimetres that
   was checked against the truth, an answer of the form "58 % of the surface meets ± 0.3 mm; 12
   areas need another pass", raw recordings that can be reprocessed, a published record of what did
   not work, and open hardware and software for about $230 to 300 in parts. No commercial or open
   tool was found that offers the first two.

**The five improvements that matter most** (the full ranked list is in section 6):

1. Build it and measure a flat plate, a ball bar and a stepped part. Until then nothing here is a fact.
2. Measure holes and edges from the laser lines and the camera images, not from the fused mesh.
3. Align and merge several scans. A bracket cannot be scanned from all sides without it.
4. Bring marker mode up to the level of markerless mode, then beyond it. Today it is the less accurate mode; in the professional scanners read here, targets are what the accuracy rests on.
5. Cut the wait after a scan to the project's own targets.

---

## 1. How to read the numbers

**OpenHandScan.**
- All accuracy figures are from the simulator (`ohs.sim`). `VALIDATION.md` calls them "an upper
  bound on what the real scanner will do".
- The simulator has matte surfaces only, no motion blur, a perfect lens model, a perfectly flat
  calibration board and lossless images (`VALIDATION.md`, "Simulation limitations"). Several
  parameters were tuned on the same simulator.
- The roadmap forbids any accuracy claim before reference parts have been measured on the real
  hardware ([`ROADMAP.md`](ROADMAP.md), section 1 "On accuracy claims" and section 5).
- The LED dot projector (option B) is the build route. `PARTS.md` ("Pattern projector: two
  options") gives its simulated depth error per frame as 0.23 to 0.27 % with a film mask, about
  twice option A's 0.11 to 0.18 %. The simulator's standard projector is described in
  `ohs/sim/nominal.py` as a DOE field of about 10,000 dots, which is option A's pattern. No
  end-to-end result with the LED mask pattern was found in the documents read. See section 7.

**Makers' figures.**
- "Accuracy up to 0.02 mm" is normally a single-frame or probing figure under ideal conditions.
  Scantech's footnote says so directly: "accuracy" is the probing error on a sphere, "volumetric
  accuracy" is the sphere-spacing error [S23].
- Volumetric accuracy is written as a + b × length, for example 0.020 mm + 0.040 mm/m [S21]. This is
  the figure that matters for a part larger than one camera view. Shining 3D says the same about its
  own consumer scanner [S15].
- Professional makers name an acceptance test: VDI/VDE 2634 Part 3 (Creaform, Scantech) or ISO
  10360 (ZEISS) [S21][S23][S26]. Scantech adds an ISO 17025 accredited laboratory [S23].
  VDI/VDE 2634 is a German guideline, not an international standard [S43].
- Consumer makers mostly name no test on the specification pages read. Shining 3D's blog refers to
  its own laboratory and VDI/VDE 2634 [S15]. The independent study below notes that the Revopoint
  software gave out no calibration data that could be checked, so the result could not be traced
  to a standard [S20].

**The one independent test found for current low-cost handheld scanners** (Kersten, Marsmann and
Przybilla, ISPRS 2026, peer reviewed [S20]). Reference: a ZEISS ATOS 5. Flatness of a 300 × 300 mm
granite plate, distance of the scan to a fitted plane:

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

No independent measurement was found for the Creality Raptor line, the Einstar line, Artec,
Creaform, Scantech or ZEISS. For those, every figure below is the maker's.

---

## 2. OpenHandScan today

| Point | OpenHandScan | Where it is written |
|---|---|---|
| Technology | Active stereo on a projected dot pattern for the dense surface; two calibrated red laser lines for sparse, more accurate points; gyro for tracking | `README.md` (root), "How it works" |
| Cameras | Two OV9281, monochrome, global shutter, 1280 × 800 each, 60 mm apart | `README.md` (root), "Stereo board measurements" |
| Depth per pixel of disparity | 0.52 mm at 150 mm, 1.45 mm at 250 mm, 4.7 mm at 450 mm | `RESEARCH_PLAN.md`, section 3 |
| Lasers | 650 nm, 5 mW, Class 3R; about 2,000 laser points per frame (the plan's estimate) | `software/README.md`, section 11; `RESEARCH_PLAN.md`, 4.2.7 |
| Working distance | 200–350 mm recommended; depth kept from 150 to 450 mm | `software/README.md`, section 7 |
| Frame rate | 60 fps camera; 46–55 fps delivered on Windows | `PARTS.md`, "Notes before ordering" |
| Markers and spray | Markerless by default. Printed AprilTags for large smooth panels. Matte spray for glossy paint, dark plastic, chrome | `software/README.md`, section 7 |
| Parts cost | $230–300 plus filament | `README.md` (root), "Parts to order" |
| Licence | MIT for hardware, firmware, software and documents | `README.md` (root), last line |
| Mesh accuracy, smooth surfaces | Median 0.065 mm (sphere) and 0.088 mm (car panel) on the GPU path; 0.20 and 0.29 mm on the CPU path | `VALIDATION.md`, "GPU processing" and "Results (full runs)" |
| Laser points | Median 0.068 mm, 95 % level 0.28 mm | `VALIDATION.md`, "Results (full runs)", row (d) |
| One depth frame | Median error 0.316 mm; 1.8 % of pixels off by more than 2 mm | `VALIDATION.md`, "GPU processing" |
| Large part | Door trim panel 946 × 868 × 135 mm: all 1,410 frames tracked, mesh median 0.07 mm, 95 % level 0.79 mm, 99.5 % of the seen surface within 1 mm | `VALIDATION.md`, "Large and detailed parts" |
| Detailed part | Carburetor: mesh median 0.80 mm, 95 % level 2.7 mm, 72 % within 1 mm. Measured before the tracking change of 2026-11-14 and not measured again | same section |
| Part with edges | Bracket with tracking: 16.2 % of the part more than 0.3 mm off, 95 % level 0.51 mm, shaft radius +0.10 mm, boss radius −0.10 mm | `VALIDATION.md`, "Where a tracked scan's shape errors come from" |
| Thin walls | 2 mm flange reads 2.72 mm at true poses and 3.02 mm with tracking; 1 mm rib 2.04 and 2.46 mm. With tracking, thin walls stay about 1 mm too thick | same section |
| Holes | A Ø 25.2 mm hole reads about 0.9–1.2 mm small in the median, up to 3 mm small with tracking. Holes of Ø 6–10 mm over a far, dark background stay closed | `VALIDATION.md`, "Fitted shapes against the truth" and "Known limitations of the pipeline" |
| Edges after smoothing | The default smoothing rounds edges by up to 0.47 mm | `VALIDATION.md`, "The deviation map against the truth" |
| Marker mode | Mesh median 0.49 mm, 95 % level 1.48 mm on the GPU path; trajectory error 0.94 mm median | `VALIDATION.md`, "Where a tracked scan's shape errors come from" (table "Smooth scenes and poor poses") and "Results (full runs)", row (e) |
| Uncertainty | Every vertex has a sigma. 2 sigma covers 99.2 % of the tracked bracket's vertices and 96.3 % of the car panel's. The Check answer passes 58–60 % of a scan at ± 0.3 mm and 99.8 % of that is truly within | `VALIDATION.md`, same section; `ROADMAP.md`, section 3 |
| Uncertainty of a fitted shape | Too small: ± 2σ covered a cylinder's true radius for 32 % of fits at true poses and 0 % with tracking (2026-11-13) | `VALIDATION.md`, "Fitted shapes against the truth" |
| Live view | 15.1–15.3 fps on a Radeon 680M, 39.2 fps on an RTX 3070 Ti laptop | `VALIDATION.md`, "The waits a user feels" |
| Wait after a scan | Standard quality: about 4.8 times the scan's length on the Radeon, about 1.5 times on the RTX. Best: twice that. Targets: 3 times and 1 time. Computed from the bench, not timed on a real scan | `VALIDATION.md`, "Standard and Best quality" |
| Tracking limits | Fast or jerky motion untested. No recovery after a real loss. Without the gyro a symmetric scene drifts 30 mm. On the CPU path 3 of 15 recordings slid 13–42 mm; detailed parts need a GPU | `VALIDATION.md`, "Simulation limitations", "Results (full runs)", "GPU processing", "Large and detailed parts"; `ROADMAP.md`, section 2 |
| App | Projects, live 3D view, rewind, one-click mesh, calibration wizard, self-test, Straighten (loop closure), Refine with lasers, clean / fill / smooth / simplify with undo | `software/README.md`, section 1; `ROADMAP.md`, section 2 |
| Measuring | Point-to-point distance; one-click fit of a plane, a cylinder or a hole; deviation map of edited against measured mesh; laser-against-mesh map | `ROADMAP.md`, section 3, items 1 to 4 |
| Not built yet | Aligning several scans, comparison with CAD, sections, gap and flush, reports, STEP export, installer | `ROADMAP.md`, section 2; `DESKTOP_APP_PLAN.md`, section 8 |
| Export | PLY, STL, OBJ in millimetres, the laser point cloud, the trajectory, a JSON report | `software/README.md`, section 8 |
| Colour | None: the cameras are monochrome | `README.md` (root), "How it works" |
| Graphics card | Any OpenCL GPU (NVIDIA, AMD, Intel); the CPU alone also works, less accurately | `software/README.md`, section 8; `VALIDATION.md`, "Known limitations of the pipeline" |

---

## 3. Existing tools

All figures in this section are the maker's claims unless the row says "measured".

### 3.1 Consumer and prosumer handheld scanners

| Scanner | Technology | Claimed accuracy | Distance, speed | Markers | Price | Source |
|---|---|---|---|---|---|---|
| Creality Raptor Pro | 22 crossed + 7 parallel blue laser lines (405 nm), plus infrared structured light | Blue: up to 0.02 mm, volumetric 0.02 mm + 0.08 mm/m. Infrared: 0.075 mm + 0.1 mm/m | 160–550 mm (blue), up to 1,000 mm (infrared); 60 fps blue, 30 fps infrared; 420,000–660,000 points/s blue | Blue modes need reflective markers; infrared mode tracks geometry or texture. Has an IMU | $1,899 | Reseller listing [S1] |
| Creality Otter | Infrared structured light, four lenses | Up to 0.02 mm "at 60 mm" | 110–1,000 mm, up to 20 fps | Not stated | about $900–960 | Reseller listings, not opened [S5] |
| Revopoint MetroX | 14 crossed + 7 parallel blue laser lines, plus 62-line blue structured light | Single frame up to 0.03 mm. Volumetric 0.03 mm + 0.1 mm × L at launch; Revopoint's page now gives 0.025 mm + 0.05 mm × L | 200–400 mm; up to 800,000 points/s (laser) | Feature, marker and global-marker tracking | $999 list at launch | Sponsored post [S6]; Revopoint page, not opened [S7] |
| Revopoint MetroX, **measured** | | 0.40 mm average, 0.29 mm standard deviation on a 300 mm flat plate; scale error on a stepped part | Turntable mode with markers | | | Independent study [S20] |
| Revopoint MIRACO Plus | Infrared structured light, standalone, with a kit of coded targets | Single frame up to 0.04 mm; length accuracy with the target kit 0.02 mm + 0.05 mm × L | Up to 20 fps | Coded targets for the photogrammetry step | about $1,760–2,200 | Revopoint page, not opened [S10] |
| Shining 3D Einstar | Structured light | None in the listing read (accuracy "-"); resolution 0.1 mm | 980,000 points/s | Not stated | from $959 | Aniwaa listing [S13] |
| Shining 3D Einstar Vega | Infrared MEMS (HD mode) and infrared VCSEL (fast mode), standalone | None in the listing read; point distance 0.05–3 mm (HD) | HD: 100–250 mm, 15 fps. Fast: 350–1,500 mm, 20 fps | Markers are in the box | $1,999 list | Reseller listing [S12] |
| Shining 3D Einstar Rockit | 38 crossed + 7 parallel blue laser lines, plus infrared VCSEL | Up to 0.05 mm; volumetric 0.1 mm/m | Up to 90 fps | Markerless and marker modes | $1,700–1,900 | A review site that does not document its tests; unverified [S14] |
| 3DMakerpro Moose | Blue structured light | ± 0.03 mm | 10 fps | Not stated | about $700 | Study's table of makers' data [S20]; review, not opened [S16] |
| 3DMakerpro Moose, **measured** | | 0.89 mm average, 0.60 mm standard deviation on the flat plate; close to professional systems on some other objects | Handheld mode | | | Independent study [S20] |
| 3DeVOK MQ (by Scantech) | 22 infrared laser lines plus infrared speckle | Up to 0.08 mm, footnoted as a laboratory figure | 150–1,500 mm; up to 70 fps with markers, 30 fps without | Works without markers | about €2,000–2,800 | Maker's page [S19]; price from a reseller, not opened [S48] |
| 3DeVOK MT | 34 blue laser lines, 22 infrared lines, infrared speckle | Up to 0.04 mm | | | $6,299 | Reseller listing, not opened [S19b] |
| Matter and Form THREE | Stereo structured light with a DLP projector, 13-megapixel sensors. Tripod and turntable, not handheld | 0.033 mm at 220 mm, 0.150 mm at 400 mm, 0.400 mm at 700 mm | 4 s per scan plus 10–15 s processing | Not needed | $1,499 (list $2,399) | Maker's specification sheet [S18]; price from a reseller, not opened [S18b] |

What stands out:
- **Blue laser lines with markers are now the standard for accuracy below $2,000**; infrared
  patterns without markers are the convenient mode with a looser figure. The Raptor Pro's own
  figures show the gap: 0.02 mm against 0.075 mm [S1].
- **Matter and Form is the only maker here that states how accuracy falls with distance** [S18].
  It loses a factor of twelve between 220 and 700 mm. Stereo triangulation behaves this way
  everywhere, OpenHandScan included.
- **Shining 3D's listings for the Einstar and the Vega give a point distance and no accuracy**
  [S12][S13]. Its own blog says it tests random units, not every unit [S15].
- **Reviews complain about software more than hardware.** A Raptor Pro reviewer lost tracking
  half-way through scans, could not get it back, and called the meshes unusable [S3]. A MetroX
  reviewer hit repeated "more markers" errors and needed about 30 % overlap to merge scans [S8].
  `DESKTOP_APP_PLAN.md` (section 1) lists more user reports, with links.

### 3.2 Professional, metrology-grade handheld scanners

| Scanner | Technology | Claimed accuracy | Acceptance test | Speed | Markers | Price | Source |
|---|---|---|---|---|---|---|---|
| Creaform HandySCAN 3D BLACK Elite | 22 blue laser lines + 1, Class 2M | 0.025 mm; volumetric 0.020 mm + 0.040 mm/m; mesh resolution 0.100 mm | VDI/VDE 2634 Part 3 | 1,300,000 measurements/s | Positioning targets; Creaform says targets cannot yet be dropped [S22] | Quote only. A competitor's page says from $65,000; unverified [S46] | Maker's page [S21] |
| Scantech SIMSCAN-E | 63 blue laser lines, 17 parallel lines for fine work, 1 line for deep holes | Up to 0.020 mm; volumetric 0.015 mm + 0.035 mm/m, or + 0.012 mm/m with a photogrammetry unit | ISO 17025 laboratory, VDI/VDE 2634 Part 3 | 6,300,000 measurements/s | Not stated on the page | Quote only | Maker's page [S23] |
| Artec Point | 17 crossed blue laser lines, 7 parallel, 1 single | Up to 0.02 mm; volumetric 0.015 mm + 0.035 mm/m | "ISO & VDI/VDE"; ISO 17025 certificate as a $2,900 extra | 120 fps, 2,800,000 measurements/s | Not target-free | $25,600–29,900 | Reseller listing [S25] |
| ZEISS T-SCAN hawk 2 | Blue laser crosses, one line for deep pockets, Class 2 | Volumetric 0.02 mm + 0.015 mm/m | Based on ISO 10360 | Not stated | Reference points; a "satellite mode" for parts of several metres | On request. One reseller lists $90,848; unverified [S47] | ZEISS's handheld site [S26] |
| Artec Leo | VCSEL structured light, computer and screen built in | Up to 0.1 mm; 0.1 mm + 0.3 mm/m over distance; resolution 0.2 mm | Not stated | Fusion at 22 fps | None needed (geometry and texture) | $38,300 | Reseller listing [S24] |
| Creaform peel 3 | Not checked here | Up to 0.050 mm; volumetric 0.050 mm + 0.100 mm/m; mesh resolution 0.250 mm | Not stated | Not stated | Geometry, texture or targets (`DESKTOP_APP_PLAN.md`, section 1) | $5,990, or $8,990 with CAD software | Creaform page and a review, not opened [S28][S28b] |
| Hexagon MARVELSCAN, **measured** | Blue light, as the paper lists it | 0.04 mm average, 0.08 mm standard deviation on the flat plate; 20 mm steps within 0.05 mm | | | Markers | 40,000–60,000 | Independent study [S20] |

What car-part work expects, read from this table:
- **0.02 to 0.05 mm on a part, with a stated length term**, tested against a named standard.
- **Targets.** Creaform writes that 3D scanning cannot do without targets yet, and that 35 % of
  its users call targets a significant limitation [S22]. The accurate scanners accept the
  nuisance.
- **A separate mode for holes and pockets**: one single laser line (Scantech, Artec Point, ZEISS)
  [S23][S25][S26].
- **Markerless scanners in this class are sold at 0.1 mm, not 0.02 mm** (Artec Leo [S24]).
  OpenHandScan's default mode is markerless, so 0.1 mm is the honest class to aim at first.

### 3.3 Open-source and do-it-yourself tools, and phones

| Tool | What it is | Claimed or measured | Limits | Licence, price | Source |
|---|---|---|---|---|---|
| OpenScan Mini | Photogrammetry rig: turntable, Raspberry Pi camera, polarised ring light | Maker: up to 0.02 mm, from its own test print, and it depends on preparation and skill | Objects up to 9 × 9 × 9 cm. Not handheld | Firmware and hardware plans published; the page names no licence. From €232 | Maker's page [S31] |
| FabScan Pi | Open-source laser scanner built on a Raspberry Pi and its camera | A 2016 paper on the original FabScan: resolution 0.1–0.2 mm in 14 × 14 × 15 cm | Small objects on a turntable. Volunteer project of two or three people | Copyleft (repository copies show GPL v2 or AGPL v3) | Project documentation [S32]; paper, not opened [S33] |
| COLMAP | Photogrammetry software: structure from motion and multi-view stereo | No accuracy claim | Photogrammetry in general needs surface texture, and a known length to set the scale | New BSD | Repository [S34] |
| Meshroom (AliceVision) | Photogrammetry software with a node editor | No accuracy claim | As COLMAP | MPL 2.0 | Manual, not opened [S35] |
| Pi handheld scanner | Raspberry Pi 4, Intel RealSense D415, RTAB-Map | Author: the quality does not reach photogrammetry's; the depth is noisy | Loses tracking on plain walls | Open; RTAB-Map is open source | Author's page [S36] |
| SL Sensor (ETH Zurich) | Open structured-light sensor, phase shifting at 5 Hz, for robots | Authors: sub-millimetre, better than two commercial depth cameras | Motion compensation relies on the robot's known motion; not handheld | Open source; licence not checked | Paper, not opened [S37] |
| SLStudio | Open structured-light software, one camera and one projector, 20 Hz | No figure found | Static scanner | Open source; licence not checked | Repository, not opened [S38] |
| Phone LiDAR (iPhone 12 Pro), **measured** | Time-of-flight depth in the phone | ± 1 cm for objects larger than 10 cm; worse below that | Not a tool for parts | App-dependent | Scientific Reports 2021, abstract only [S39] |
| Phone LiDAR with Scaniverse, **measured** | | Errors under 1 % of the object's size for small and medium objects | | | Journal paper 2026, abstract only [S44] |
| Phone photogrammetry, **measured** | Phone photos processed in Agisoft Metashape | A little behind models from SLR photos, against a ZEISS ATOS 5; no figure in the abstract | Needs texture, many photos and a scale | | ISPRS 2024, abstract [S40] |
| Polycam | Phone app | Maker: ± ½ inch on standard interior captures | A room tool | Commercial | Maker's help page, not opened [S41] |

What stands out:
- **No open-source project was found that does what OpenHandScan sets out to do**: handheld,
  markerless tracking, projected pattern, laser lines, its own desktop app. The open tools are
  turntable rigs for small objects, photogrammetry software, or a depth camera with a mapping
  library. One search does not prove there is none.
- **Photogrammetry is the strong open route for small textured objects**, and it is weak on
  exactly what car parts are: plain, dark or shiny, and in need of true scale.
- **Phones are centimetre tools** by the measurements found.

### 3.4 The software side

| Feature | Consumer apps (Creality Scan, Revo Scan, EXStar / StarVision) | Professional (Artec Studio, VXelements, ZEISS INSPECT) | OpenHandScan |
|---|---|---|---|
| Tracking | Feature (geometry), texture, marker; global markers on the MetroX [S6]; an IMU in the Raptor Pro [S1] | Targets (Creaform [S22]); geometry and texture without targets (Artec Leo [S24]) | Geometry on the dot pattern plus gyro; AprilTag mode; hints when a surface cannot hold the position (`ROADMAP.md`, section 2) |
| Recovery after a loss | A Raptor Pro reviewer found none [S3]. Shining 3D has a rewind (`DESKTOP_APP_PLAN.md`, section 1) | Not checked | Rewind exists; recovery after a real loss is open (`ROADMAP.md`, section 2) |
| Aligning several scans | Creality: by features or by markers [S4]. Revo Scan: needs about 30 % overlap [S8]. Matter and Form: automatic or by picked points [S18] | ZEISS INSPECT: alignments in the free version [S27] | Not built (M4) |
| Mesh editing | Clean, fill, simplify, texture | Mesh editing in the free ZEISS INSPECT [S27]; Artec Studio's Autopilot and scripting [S30] | Clean, fill, smooth, simplify with undo, and a map of what the edit moved |
| Measuring and inspection | Creality: no dimensional inspection for now; export an STL to other software [S4]. Revo Scan 5 MetroX: no measuring tools [S8]; Revo Measure is sold separately, $499 a year [S11] | ZEISS INSPECT free: 3D inspection, comparison of measured against CAD, reports; the paid version adds native CAD, GD&T filters, trends, scripting [S27]. Creaform's VXinspect and VXmodel are sold separately [S29] | Distance, fitted plane / cylinder / hole. No CAD comparison yet |
| Scan to CAD | Revo Design (built on QuickSurface), $540 a year, $1,900 for Pro [S11] | VXmodel [S29]; peel 3 with CAD software $8,990 [S28b] | Not built (M5: fitted shapes to STEP) |
| Export | Creality: PLY, OBJ, STL; no STEP [S1][S4]. Vega: PLY, STL, OBJ, ASC [S12]. Matter and Form: OBJ, PLY, XYZ, GLTF, DAE, STL, FBX [S18] | The scanners feed inspection and CAD packages [S21] | PLY, STL, OBJ, laser points, trajectory, JSON report |
| Graphics card | Revo Scan's GPU mode needs an NVIDIA RTX 30 or 40 card (as recorded in `DESKTOP_APP_PLAN.md`, section 1; link [S9], not opened again) | Not checked | Any OpenCL GPU, or none |
| Accuracy reporting in the app | None found. The Revopoint software output no calibration data the study could verify [S20] | Acceptance certificates for the device [S21][S23]; inspection reports about the part [S27] | Per-vertex sigma, a tolerance check with a list of weak areas, calibration reports, and a validation document that includes what failed |
| Openness | Free with the scanner, closed. Matter and Form has an open API [S18] | Paid or free-with-limits, closed | MIT, source and file formats open |

Two things are worth noticing:
- **The consumer apps stop at the mesh.** Inspection is "export it and use something else". The
  free ZEISS INSPECT is that something else for many users, and it already compares a mesh with CAD
  [S27].
- **Nothing found reports uncertainty per vertex**, or tells the user which areas miss a tolerance.
  This is an absence in the pages read, not proof that no product does it.

### 3.5 Holes and edges are hard for everybody

A Loughborough University study of fringe-projection scanning found that artefacts at hole edges
put the measured diameter off by more than 0.25 mm, while hole centres stayed accurate. Finding
the edge in the camera image to sub-pixel accuracy, together with a compensation step, cut the
diameter error by up to 9.3 times [S42] (abstract only).

OpenHandScan's simulated hole error of about 1 mm is larger than that starting point. The
direction of the fix is the same: take the edge from the image and the laser, not from the
surface.

---

## 4. Comparison

| Point | OpenHandScan (simulated, unbuilt) | Consumer and prosumer (claims; measured where it says so) | Professional metrology (claims; measured where it says so) | Open-source, DIY, phones |
|---|---|---|---|---|
| Technology | Dot-pattern stereo, 2 red laser lines, gyro | 21–45 blue laser lines with markers, plus infrared pattern without | 22–81 blue laser lines with targets; or structured light without | Photogrammetry, turntable laser line, depth cameras, phone LiDAR |
| Accuracy, smooth surface | Median 0.065–0.088 mm; 95 % level 0.26–0.39 mm | Claimed 0.02–0.1 mm. Measured on a flat plate: 0.3–1.7 mm [S20] | Claimed 0.02–0.025 mm. Measured: about 0.05 mm [S20] | Photogrammetry rig claims 0.02 mm on small objects [S31]; phones ± 1 cm [S39] |
| Accuracy over length | Not defined. A 946 mm panel did not drift in simulation | 0.02–0.03 mm + 0.05–0.1 mm/m [S1][S6][S7] | 0.015–0.02 mm + 0.015–0.04 mm/m, tested [S21][S23][S26] | Not stated |
| Edges and thin walls | A 2 mm wall reads 3.0 mm; 16 % of a bracket more than 0.3 mm off | Measured: edges blurred, scale error on steps [S20] | Measured: 20 mm steps within 0.05 mm [S20]. Mesh resolution 0.1 mm [S21] | Not found |
| Holes | Ø 25 mm about 1 mm small; Ø 6–10 mm may stay closed | No figure found | A single-line mode for deep holes [S23][S25][S26]; no figure found | Not found |
| Working distance | 150–450 mm | 160–550 mm with lasers; to 1–1.5 m with infrared [S1][S12] | About 0.3 m stand-off, depth of field 250–550 mm [S21][S23]; Leo 0.35–1.2 m [S24] | Fixed rigs; phones not checked |
| Object size | Brackets to a door panel (simulated) | 5 mm to 4 m claimed [S1] | 0.05 to 4 m [S21]; several metres [S26] | About 9 cm on the OpenScan Mini [S31] |
| Frame rate | 60 fps camera; live view 15–39 fps | 15–90 fps [S1][S12][S14] | 22–120 fps [S24][S25] | 5–20 Hz [S37][S38] |
| Laser points per second | About 0.1 million (2,000 per frame at 60 fps) | 0.4–0.8 million with lasers [S1][S6] | 1.3–6.3 million [S21][S23] | — |
| Wait after a scan | 1.5–4.8 times the scan's length | Not found | Not found; Leo fuses live on the device [S24] | Not checked |
| Markers | None by default; AprilTags for big smooth panels | Needed in the accurate laser modes [S1][S8] | Targets, in most [S22] | Not checked |
| Spray | Glossy, dark, chrome | Now and then, says one review [S8]; one listing contradicts itself on this [S1] | Not checked | Not checked |
| Tracking robustness | Loses nothing in simulation; fast motion and recovery untested | Losses in reviews and in the study [S3][S20] | The two tested systems gave complete, consistent models [S20] | Loses plain surfaces [S36] |
| Multi-scan alignment | No | Yes, with limits [S4][S8] | Yes [S27] | In other software |
| Inspection against CAD | No | No, or a paid extra [S4][S11] | Yes [S27][S29] | In other software |
| Uncertainty shown to the user | Yes, per vertex, checked on simulated scans | None found | A certificate for the device; none per scan found | None found |
| Raw data kept, reprocessing | Yes, always | Not found | Not checked | Photos are the raw data |
| Colour | No | Yes on the ones checked [S12][S19] | Leo has a texture light [S24]; others not checked | Photogrammetry: yes |
| Laser class | 3R | Class I or II [S1] | Class 2 or 2M [S21][S26] | — |
| Price | $230–300 in parts | $700–2,200; $6,299 at the top [S19b] | $6,000–40,000 listed; more by quote | €230 for a rig [S31]; free software |
| Licence | MIT, open hardware | Closed, free with the device | Closed, paid | Open, mixed licences |
| Proven on real parts | **No** | Partly: sold in volume, one study shows 0.3–1.7 mm on a plate | Yes: acceptance tests, one study confirms | Varies |

---

## 5. Where OpenHandScan stands

### Behind

**1. Nothing is proven.** This is the largest gap and no feature closes it. Kersten's study shows
what happens to low-cost scanners when someone finally measures a flat plate: ten to fifty times
the brochure figure, mostly from scale and calibration errors [S20]. The simulator gives
OpenHandScan a perfect board and matte surfaces, so it cannot show this kind of error.

**2. Edges, thin walls and holes.**
- A 2 mm flange reads 3.0 mm with tracking and a 1 mm rib 2.5 mm. The cause is known (the fusion
  marks space behind a surface as solid, and the tracking model has 2 mm voxels) and partly
  repaired on the GPU path only (`VALIDATION.md`, "Where a tracked scan's shape errors come from").
- A Ø 25 mm hole reads about 1 mm small. Holes of Ø 6 to 10 mm, the size of most mounting holes,
  can stay closed.
- Smoothing rounds edges by up to 0.47 mm.
- The professional reference is 20 mm steps within 0.05 mm, measured [S20], and a mesh resolution
  of 0.1 mm [S21].

**3. The dense surface is stereo, not laser.** A commercial laser scanner triangulates every point
of its surface from laser lines. OpenHandScan's laser lines cover 8 to 9 % of the surface
(`ROADMAP.md`, section 3, item 4); the rest comes from stereo matching with a median error of
0.3 mm per frame, averaged down by fusion. One pixel of disparity is 1.45 mm at 250 mm.

**4. Marker mode is the weak mode.** Mesh median 0.49 mm against 0.06 to 0.09 mm markerless. In
the professional scanners read here, targets are what the volumetric figure rests on [S22][S23].
The reason for OpenHandScan's result is written down: dots and laser lines spoil the tag corners, and in most frames only two or three
20 mm tags are seen (`VALIDATION.md`, "Findings and fixes", item 5).

**5. Speed.** The wait after a scan is 1.5 to 4.8 times the scan at Standard quality, and the
project's own targets (1 and 3 times) are not met. A minute of scanning costs about five minutes
on integrated graphics. No comparable figure was found for commercial apps, but the Artec Leo
fuses at 22 fps on the device itself [S24].

**6. Tracking outside the comfortable case.** Fast or jerky motion is untested, there is no
recovery after a real loss, a scan that starts on a smooth area and moves to a detailed one stays
in the wrong mode, and detailed parts need a GPU. Commercial scanners lose tracking too [S3][S20],
so this is a gap to the professional tier with targets, not to the consumer tier.

**7. Features.** No merging of scans, no comparison with CAD or a reference mesh, no sections, no
report, no installer. The first is the one that blocks real work: a part has an underside.

**8. Hardware.**
- 2 laser lines against 22 to 81, and red against blue. Blue has less speckle and scatters less
  inside paint and plastic (`RESEARCH_PLAN.md`, section 3 and 4.2.7).
- 1280 × 800 sensors; the Raptor Pro's camera is 1920 × 1200 [S1], Matter and Form's 13 megapixels [S18].
- Class 3R lasers. The commercial handhelds read here are Class 1, 2 or 2M [S1][S21][S26].
- No colour camera. This matters little for reverse engineering.
- The LED projector that is being built has about twice the simulated depth error per frame of the
  laser and DOE option (`PARTS.md`). It has no speckle and uses commodity parts, which may matter
  more on real paint; only the hardware can tell.

**9. The CPU path is a second-class result.** Without a GPU a scan is about 0.3 mm too fat and
keeps the thicker walls (`ROADMAP.md`, section 3). Commercial apps have one result.

### Level, as far as simulation can say

- **Smooth-surface accuracy.** 0.065 to 0.088 mm median is inside the 0.02 to 0.1 mm the consumer
  tier claims, and better than what was measured for three consumer scanners on a plate. It is
  simulated against real, so "level" is the most that can be said.
- **Large smooth parts.** A 946 mm door panel in five passes stayed within 0.79 mm at the 95 %
  level without markers. Consumer makers sell marker modes and target kits for parts of that size
  [S6][S10].
- **Working distance, object size and frame rate** are in the consumer range.
- **Tracking modes**: markerless plus a marker option, as in the consumer tier.
- **Mesh editing and export formats** match the consumer apps.
- **Measuring**: a distance tool and fitted shapes are more than Creality Scan and Revo Scan 5
  MetroX include [S4][S8], and far less than ZEISS INSPECT [S27].
- **Guided calibration and self-test.** The consumer tier has a calibration board and a guided
  procedure too [S3][S20]; OpenHandScan's writes a report the user can read.

### Ahead, or doing what the others do not

- **Uncertainty per vertex, checked against the truth.** 2 sigma covers 96 to 99 % of the
  vertices on simulated scans. Nothing like it was found in any other tool.
- **An answer the user can act on**: "58 % of the surface meets ± 0.3 mm; 12 areas need another
  pass", and 99.8 % of what it passes is truly within. Safe, and still pessimistic.
- **Two measuring principles that check each other.** The laser lines and the stereo depth are
  compared on every scan, and the disagreement is shown on the model.
- **The raw recording is always kept.** A scan survives a crash and can be reprocessed with
  better software later. Reviews of consumer apps describe lost scans (`DESKTOP_APP_PLAN.md`,
  section 1).
- **A map of what editing moved.** Smoothing and filling are shown against the measured surface.
- **Honest documents.** The validation file records negative results, biases and the limits of
  the simulator. No maker's page read for this comparison does that; Matter and Form's
  distance table and Shining 3D's blog come closest [S18][S15].
- **Open hardware and software under MIT**, with a simulator, so anyone can run the whole chain
  without buying anything.
- **Any graphics card, or none.**
- **Price.** About $230 to 300 in parts against $700 to 2,200 for the consumer tier.

The honest reading: OpenHandScan's lead is in **knowing how good a scan is**, not in how good the
scan is. That lead only counts once the sigma has been checked on real scans.

---

## 6. Improvements worth making, ranked

"Roadmap" says whether the item is already in `ROADMAP.md`, `RESEARCH_PLAN.md`,
`DESKTOP_APP_PLAN.md` or `MAINTENANCE_PLAN.md`. "New" means it was not found in the parts of those
documents that were read; it may still be written somewhere else.

| # | Improvement | What it would take | Who shows it can be done | Roadmap |
|---|---|---|---|---|
| 1 | **Measure reference parts on the real scanner and publish the result.** A flat plate of 300 mm or more, a ball bar, a stepped block, a bracket with drilled holes. Report bias, spread and coverage by distance | The hardware. A ball bar from two bearing balls on a rod ([Amazon search](https://www.amazon.com/s?k=25mm+chrome+steel+bearing+balls); they need a matte coat, which adds thickness), a sheet of flat glass for a coarse flatness check ([Amazon search](https://www.amazon.com/s?k=12x12+inch+clear+glass+sheet)), and the calipers already on the parts list. The fits exist in the Fit tool. A few days once the scanner works | Creaform, Scantech and ZEISS quote such tests [S21][S23][S26]. Kersten's plate and staircase are a ready-made recipe that separates good from bad scanners [S20] | **Yes**: `ROADMAP.md` section 3 item 6; `RESEARCH_PLAN.md` 4.9.6 and 6.4 (which already list a flat plate and a ball bar). **New:** a stepped block, and the flat plate first, because that is where the low-cost scanners failed |
| 1a | **Validate the LED pattern end to end in the simulator before the hardware arrives.** Run the standard scans with the printed-mask pattern, not the DOE pattern | Simulator work only: a projector model for the mask (dot size, blur away from focus), then the existing test runs | — | **New**, as far as found. `PARTS.md` has the per-frame figure only |
| 2 | **Holes and edges from the laser lines and the images.** Where a laser line breaks at a rim, its end points lie on the edge. In the camera images a hole's edge is a grey-level step that can be found to a fraction of a pixel and projected onto the fitted face | A detector for line ends and for image edges near a fitted plane; a circle fit with its own error model; a simulator scene with drilled holes in a plate with thickness. Medium effort | The Loughborough study cut hole-diameter errors up to 9.3 times with sub-pixel image edges [S42]. Professional scanners carry a single-line mode for holes [S23][S25][S26] | **Partly**: laser line ends are in `ROADMAP.md` section 3 and `RESEARCH_PLAN.md` 7.6. **New:** the edge from the camera image |
| 3 | **Align and merge several scans** (M4) | Global registration, a pose graph over all scans, one fusion from all raw frames, a residual map. The plan estimates medium effort and reuses the loop-closure code | Every commercial app has it, with limits users complain about [S4][S8] | **Yes**: `DESKTOP_APP_PLAN.md` M4; `RESEARCH_PLAN.md` 4.7 |
| 4 | **Make marker mode the accurate mode.** Detect targets in images without dots (or with the dots removed), solve all target positions together, and use them as constraints in the same graph as the loops. Then consider round retro-reflective targets lit from beside the lenses, which give sharper centres than printed tags | Software: medium. Hardware for retro-reflective targets: a few red LEDs next to each lens ([Amazon search](https://www.amazon.com/s?k=5mm+red+LED+620nm)) and a sheet of targets ([Amazon search](https://www.amazon.com/s?k=3D+scanner+reference+marker+dots+6mm)). A per-frame emitter switch would help and needs a strobe line from the camera | Creaform's volumetric figure rests on targets [S21][S22]. Revopoint sells a coded-target kit that brings a $2,000 scanner to 0.02 mm + 0.05 mm × L (claimed) [S10]. Scantech's photogrammetry unit improves the length term from 0.035 to 0.012 mm/m (claimed) [S23] | **Partly**: dot-free images for tags and tags as graph constraints are in `RESEARCH_PLAN.md` 4.4 and 7.5. **New:** retro-reflective round targets and a target-only survey pass |
| 5 | **Thin walls and edges in the fusion.** A tracking model that can hold a 1–2 mm sheet; finer voxels where the surface bends sharply; the same rules on the CPU path | Research with an uncertain outcome. The cause and the first two fixes are done; what is left is the 2 mm tracking voxel and the CPU models | Measured: professional scanners keep 20 mm steps within 0.05 mm, low-cost ones blur them [S20] | **Yes**: `ROADMAP.md` section 3, "Still open" |
| 6 | **Use the whole scan for each patch** (world-space spacetime stereo). It removes the stereo error at its source, which also sharpens edges | A GPU stage after tracking; medium to large. The prototype cut the median error 4 to 6 times in simulation | Not shipped by anyone found; this would be OpenHandScan's own | **Yes**: `RESEARCH_PLAN.md` top 10 item 2 |
| 7 | **Cut the wait after a scan** to 1 time (gaming GPU) and 3 times (integrated) the scan's length | Move laser extraction, the tracking filter and the fusion step off the CPU; keep the depth on the GPU between stages | Artec Leo fuses at 22 fps on a handheld computer [S24]; consumer scanners capture at 60–90 fps [S1][S14] | **Yes**: `MAINTENANCE_PLAN.md` F and E tasks; `VALIDATION.md`, "The waits a user feels" |
| 8 | **Tracking that recovers.** Find the pose again after a loss, test fast and jerky motion, switch to detail mode in mid-scan | Relocalisation by global registration against the model; simulator recordings with blur and fast motion; medium | Shining 3D's rewind and recovery (`DESKTOP_APP_PLAN.md`, section 1). Consumer reviews show how much users mind [S3] | **Yes**: `ROADMAP.md` section 2; `RESEARCH_PLAN.md` 4.3.5 and 4.4 |
| 9 | **Comparison with CAD: first by export, then built in.** Write a short guide and a one-click export for the free ZEISS INSPECT or a similar tool (units, alignment, the sigma as a vertex value). Build the in-app deviation map against a reference later | The guide: a day. The built-in version is M5 | Free ZEISS INSPECT compares a mesh with CAD and writes a report [S27]. The consumer makers tell users to do exactly this [S4] | Built-in version: **yes** (M5). The export route first: **new** |
| 10 | **An error bar for fitted shapes that knows the biases**, and a term for the pose quality of the whole scan | An error model per feature type, checked on the fitted-shapes harness; then on the reference parts of item 1 | Nobody found | **Yes**: `ROADMAP.md` section 3 |
| 11 | **Next hardware revision.** In order of value: (a) a longer baseline or sensors with more pixels, since the depth step per pixel is z² / (f · B) (`RESEARCH_PLAN.md`, section 3); (b) blue line lasers, and more lines; (c) laser power inside Class 2 | (a) Two separate global-shutter cameras with a hardware trigger ([Amazon search](https://www.amazon.com/s?k=global+shutter+USB+camera+module+external+trigger)) in place of the single 60 mm board; a new faceplate; recalibration. (b) Blue line modules ([Amazon search](https://www.amazon.com/s?k=450nm+blue+line+laser+module+12mm); check the power class before buying) and a way to tell several lines apart. (c) Lower power with a longer exposure or a larger lens opening | Every laser scanner in sections 3.1 and 3.2 uses blue lines, 7 to 81 of them, at Class 1 to 2M [S1][S21][S23][S26] | Blue and more lines: **yes**, later (`RESEARCH_PLAN.md` 4.2.7, 7.12). Baseline, pixels and laser class: **new** |
| 12 | **Difficult surfaces.** Exposure and emitter power that follow the surface, a polariser option, plain advice when spray is needed | Control loop through the Pico; polariser film ([Amazon search](https://www.amazon.com/s?k=linear+polarizer+film+sheet)); simulator upgrades for gloss | One blue-laser listing claims metal parts without spray and, further down, asks for spray on reflective metal [S1]; a review says spray is needed now and then [S8]. So nobody has solved it | **Yes**: `RESEARCH_PLAN.md` 4.10 |
| 13 | **Calibration health on every scan**, with temperature | Cross-check of stereo, laser plane and laser-by-stereo; log the IMU's temperature from the first recording | The study could not get verifiable calibration data out of a consumer app [S20]; this is a place to be visibly better | **Yes**: `RESEARCH_PLAN.md` 4.9; `ROADMAP.md` section 4 |
| 14 | **One download that installs** | Packaging (M6) | All commercial apps | **Yes**: `DESKTOP_APP_PLAN.md` M6 |

Not recommended now:
- **A colour camera.** Every consumer scanner has one, and car-part reverse engineering does not
  need it.
- **Chasing "0.02 mm".** The markerless professional scanner is sold at 0.1 mm [S24], and three
  consumer scanners sold at 0.03 to 0.08 mm measured 0.3 to 1.7 mm on a plate [S20]. A measured,
  published 0.1 to 0.2 mm on real parts, with honest error bars, would already be better
  information than this price class offers.

---

## 7. What could not be verified

- **Makers' own pages.** Revopoint's, Creality's store, Einstar's and Matter and Form's shop pages
  refused automated reading; Artec's and ZEISS's main pages came back empty. Their figures here come
  from reseller listings, one sponsored post, the makers' PDF sheets, or a search result's summary.
  The source list marks each one.
- **Prices** change weekly and differ by seller. Creaform, Scantech and ZEISS sell by quote; the two
  figures given for them come from one competitor's page and one reseller and are marked unverified.
- **Independent accuracy** for the Creality Raptor line, the Einstar line, Artec, Creaform, Scantech
  and ZEISS: none found. One review site gives measured numbers for the Einstar Rockit without a
  method; they are left out.
- **The Revopoint MetroX's volumetric figure** appears as 0.03 mm + 0.1 mm × L and as 0.025 mm +
  0.05 mm × L. Both are given.
- **Processing time after a scan** in commercial apps: no figure found.
- **Whether any commercial app shows uncertainty per vertex:** none found in the pages read. Not proof.
- **Artec Studio's price and Revo Scan's GPU requirement** are taken from `DESKTOP_APP_PLAN.md` and
  were not opened again.
- **Three papers** (the hole study [S42], the phone LiDAR study [S39], the handheld-scanner standard
  discussion [S43]) sat behind browser checks. Only their abstracts, as summarised by the search,
  were used.
- **Licences** of OpenScan, FabScan Pi, SL Sensor and SLStudio were not read from their licence files.
- **Which projector the validation scans simulate.** `VALIDATION.md` does not say. A comment in
  `ohs/sim/nominal.py` describes a DOE field of about 10,000 dots. If that is right, the headline
  figures belong to option A's pattern and the LED build route has no end-to-end figure yet.
- **OpenHandScan's detailed-part figures** (carburetor, fitted shapes) predate the tracking change
  of 2026-11-14 and were not measured again, as `VALIDATION.md` says itself.

---

## Sources

Opened and read on 2026-11-14 unless marked. "Summary only" means the page was not opened and the
claim comes from a web search's summary of it.

**This repository**
- `README.md` (root): "How it works", "Parts to order", "Stereo board measurements".
- `PARTS.md`: "Pattern projector: two options", "Notes before ordering".
- `software/README.md`: sections 1, 7, 8, 11.
- `software/VALIDATION.md`: "Results (full runs)", "GPU processing", "Large and detailed parts", "The waits a user feels", "Standard and Best quality", "Fitted shapes against the truth", "The Check answer against the truth", "The deviation map against the truth", "Where a tracked scan's shape errors come from", "Findings and fixes", "Simulation limitations", "Known limitations of the pipeline".
- `software/docs/ROADMAP.md`: sections 1 to 5.
- `software/docs/RESEARCH_PLAN.md`: sections 1 to 3, 4.2.7, 4.7, 4.9, 4.10, 5, 6, 7.12.
- `software/docs/DESKTOP_APP_PLAN.md`: sections 1, 2, 5, 8.
- `software/docs/MAINTENANCE_PLAN.md`: overview table.

**Consumer and prosumer scanners**
- S1. Top 3D Shop, Creality Raptor Pro listing. https://top3dshop.com/product/creality-raptor-pro-3d-scanner
- S3. 3DPrint.com, review of the Creality Raptor Pro (May 2025). https://3dprint.com/317412/creality-raptor-pro-software-limited/
- S4. Creality Wiki, Sermoon S1 FAQ. Summary only. https://wiki.creality.com/en/3d-scanner/sermoon-series/s1-faq
- S5. B&H, Creality CR-Scan Otter listing. Summary only. https://www.bhphotovideo.com/c/product/1833196-REG/creality_cr_scan_otter_portable_3d.html
- S6. CNX Software, Revopoint MetroX (sponsored post, November 2024). https://www.cnx-software.com/2024/11/04/revopoint-metrox-3d-scanner-blue-laser-scanning-breakthrough-at-an-appealing-price/
- S7. Revopoint, MetroX product page. Summary only. https://www.revopoint3d.com/pages/3d-laser-scanner-metrox
- S8. 3DPrinting.com, Revopoint MetroX review. https://3dprinting.com/reviews/revopoint-metrox-review/
- S9. Revopoint, Revo Scan 5 MetroX 5.6.4 release note. Not opened; cited in `DESKTOP_APP_PLAN.md`. https://global.revopoint3d.com/blogs/news/revo-scan-5-metrox-v5-6-4-released-with-gpu-acceleration-for-windows
- S10. Revopoint, MIRACO Plus product page. Summary only. https://www.revopoint3d.com/products/all-in-one-3d-scanner-miracoplus
- S11. Revopoint, software prices (Revo Measure, Revo Design). Summary only. https://www.revopoint3d.com/pages/2025md
- S12. MatterHackers, Einstar Vega listing. https://www.matterhackers.com/store/l/einstar-vega-wireless-handheld-3d-scanner/sk/M1RMZN1R
- S13. Aniwaa, Shining 3D Einstar listing. https://www.aniwaa.com/product/3d-scanners/shining-3d-einstar/
- S14. 3D Tech Valley, Einstar Rockit review. Tests not documented; used for the listed specifications only. https://www.3dtechvalley.com/einstar-rockit-review/amp/
- S15. Einstar (Shining 3D), "The accuracy of Einstar: what you really need to know". Summary only. https://www.einstar.com/blogs/news/the-accuracy-of-einstar-what-you-really-need-to-know
- S16. Creative Bloq, 3DMakerpro Moose review. Summary only. https://creativebloq.com/3d/3dmakerpro-moose-3d-scanner-review-compact-device-with-great-results
- S18. Matter and Form, THREE technical specifications (PDF). https://cdn.shopify.com/s/files/1/1745/6181/files/THREE_Technical_Specifications.docx.pdf
- S18b. MatterHackers, Matter and Form THREE listing. Summary only. https://www.matterhackers.com/store/l/matter-and-form-three-3d-scanner/sk/MZV7GS5U
- S19. Scantech (Scanology), 3DeVOK MQ product page. https://www.3d-scantech.com/product/top-choice-for-professionals/
- S19b. RobotShop, 3DeVOK MT listing. Summary only. https://www.robotshop.com/products/3devok-mt-infrared-blue-laser-professional-3d-scanner-004mm-accuracy
- S48. OpenELAB, 3DeVOK MQ listing. Summary only. https://openelab.io/products/3devok-mq-professional-3d-scanner

**Independent tests and papers**
- S20. Kersten, Marsmann, Przybilla, "Geometrical accuracy investigations of handheld 3D scanners in comparison: low-cost vs. high-end", ISPRS Archives XLIX-B1-2026, peer reviewed. Full text read. https://isprs-archives.copernicus.org/articles/XLIX-B1-2026/263/2026/isprs-archives-XLIX-B1-2026-263-2026.pdf
- S39. Luetzenburg, Kroon, Bjørk, "Evaluation of the Apple iPhone 12 Pro LiDAR for an application in geosciences", Scientific Reports 2021. Abstract, summary only. https://doi.org/10.1038/s41598-021-01763-9
- S40. Kersten, Sönksen, Przybilla, "Geometric accuracy investigations of mobile phone devices in the laboratory using high-precision reference bodies", ISPRS Archives 2024. Abstract. https://isprs-archives.copernicus.org/articles/XLVIII-2-W8-2024/211/2024/
- S42. Loughborough University, "Accurate characterisation of hole geometries by fringe projection profilometry". Abstract, summary only. https://repository.lboro.ac.uk/articles/conference_contribution/Accurate_characterisation_of_hole_geometries_by_fringe_projection_profilometry/9550679/1
- S43. Bonin, Khameneifar, Mayer, "Evaluation of the metrological performance of a handheld 3D laser scanner using a pseudo-3D ball-lattice artifact", Sensors 2021. Abstract, summary only. https://www.mdpi.com/1424-8220/21/6/2137/htm
- S44. Geodetski vestnik 2026, accuracy of Scaniverse on an iPhone. Abstract, summary only. https://geodetski-vestnik.com/en/clanek/10.15292/geodetski-vestnik.2026.01.47-61
- S33. ISPRS Archives XLI-B3 (2016), paper that tested the original FabScan. Summary only. https://isprs-archives.copernicus.org/articles/XLI-B3/261/2016/isprs-archives-XLI-B3-261-2016.pdf

**Professional scanners and software**
- S21. Creaform, HandySCAN 3D BLACK series technical specifications. https://www.creaform3d.com/en/portable-3d-scanner-handyscan-3d/technical-specifications
- S22. Creaform, blog post on the HandySCAN 3D MAX series and targets. https://www.creaform3d.com/en/resources/blog/new-3d-scanner-revolutionizing-extensive-surface-damage-assessment
- S23. Scantech (Scanology), SIMSCAN-E product page. https://www.3d-scantech.com/product/simscan-e-intelligent-wireless-and-palm-sized-3d-scanner/
- S24. MatterHackers, Artec Leo listing. https://www.matterhackers.com/store/l/artec-leo-3d-scanner/sk/MY5TQJ0S
- S25. MatterHackers, Artec Point listing. https://www.matterhackers.com/store/l/artec-point
- S26. ZEISS #HandsOnMetrology, T-SCAN hawk 2. https://www.handsonmetrology.com/products/t-scan-hawk-2/
- S27. ZEISS #HandsOnMetrology, ZEISS INSPECT. https://www.handsonmetrology.com/products/software/zeiss-inspect/
- S28. Creaform, peel 3 page. Summary only. https://go.creaform3d.com/peel-3d-ab
- S28b. 3D Printing Industry, peel 3 Pro.CAD review. Summary only. https://3dprintingindustry.com/news/review-peel-3-pro-cad-professional-3d-scanning-without-the-price-tag-238026/
- S29. Creaform, VXelements brochure (hosted by a reseller). Summary only. https://www.ashtead-technology.com/wp-content/uploads/2023/05/VXelement-Brochure.pdf
- S30. Artec 3D, Artec Studio page. Summary only. https://www.artec3d.com/software/artec-studio
- S46. Artec 3D, comparison page that quotes a HandySCAN price. Competitor's claim; summary only. https://creaform-handyscan.artec3d.com
- S47. MatterHackers, ZEISS T-SCAN hawk 2 listing. Summary only. https://www.matterhackers.com/store/l/zeiss-t-scan-hawk-2-3d-scanner

**Open-source, DIY and phones**
- S31. OpenScan, OpenScan Mini. https://www.openscan.eu/openscan-mini
- S32. FabScan Pi documentation. https://fabscanpi-server.readthedocs.io/
- S34. COLMAP repository. https://github.com/colmap/colmap
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
