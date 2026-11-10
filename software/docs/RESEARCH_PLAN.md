# OpenHandScan: algorithm research and integration plan

Written 2026-11-09 for milestones M3 to M6 of [the desktop app plan](DESKTOP_APP_PLAN.md). This is a proposal: nothing below is built unless it says so. Revised the same day: section 7 adds an innovation track, with a first simulator test of its strongest idea, and the top 10 now includes two of its items. Read it with [ARCHITECTURE.md](../ARCHITECTURE.md) and [VALIDATION.md](../VALIDATION.md).

**How the research was done.** Candidates came from background knowledge and web searches in November 2026. Then:
- every arXiv ID was checked against the arXiv API, every DOI against Crossref, and the other paper links by opening them;
- every licence was read from the project's own licence file on GitHub, and from the README where a project mixes licences;
- every Open3D 0.20 function named here was checked in the project's own Python environment.

Speeds marked *estimate* were not measured. Accuracy numbers come from `VALIDATION.md` (simulation) unless a source is given.

**Work in progress elsewhere.** Two other sessions are editing this code. `ohs/track/posegraph.py` (loop closure, "Straighten") and `ohs/mesh/` (mesh edits) exist only in the working tree, not yet committed. This plan builds on them as they were on 2026-11-09; names may change.

**Labels**
- Priority: **P1** do in the named milestone; **P2** do next, once the P1 items stand; **P3** later, or only if a measurement asks for it; **✗** don't (the reason is given).
- Effort: **S** a change inside one module, with tests; **M** a new module or GPU kernel, with tests and a validation run; **L** several modules, or research with an uncertain outcome.
- Risk: how likely it is that the gain doesn't show up on real hardware, or that the change breaks something.

## Contents
1. Executive summary
2. The ranked top 10
3. Facts used throughout
4. Per-area detail
   - 4.1 Stereo depth on the dot pattern
   - 4.2 Laser lines
   - 4.3 Tracking and odometry
   - 4.4 Loop closure and global consistency
   - 4.5 Fusion and representation
   - 4.6 Mesh post-processing
   - 4.7 Multi-scan alignment and merging
   - 4.8 Reverse engineering
   - 4.9 Calibration over time
   - 4.10 Difficult surfaces
5. What will not work, in one place
6. Validation approach
7. Innovation track: ideas no scanner ships yet
8. References

---

## 1. Executive summary

**Where the scanner stands.** On the simulator, GPU mode gives a mesh median of 0.065–0.088 mm, laser points at 0.07 mm, and a worst-case drift under 0.3 mm on short scans. The known weak spots:
- stereo depth carries a dot-scale error that is fixed to the camera (tracking works around it; the mesh still gets it);
- the mesh p95 is 0.7–1.2 mm (CPU runs), from grazing angles, depth edges and the range limit;
- flat or symmetric parts drift when the IMU is off;
- loop closure is being built now;
- the simulator has matte surfaces only.

Nothing has run on the real scanner yet.

**What "better than commercial" can honestly mean.** This is a 60 mm-baseline, 1280x800, red-laser design. On raw accuracy per frame it will not beat a metrology laser scanner with blue laser lines and positioning targets (Creaform's HandySCAN class). The consumer tier (Revopoint, Creality, Shining 3D's Einstar) is sold with figures of a few hundredths of a millimetre; the ones found for this plan were in retail listings that don't say how they were measured. OpenHandScan can beat that tier where users hurt, and it can be the scanner that says how sure it is about every number. Five themes:
1. **Accuracy from the whole scan, not from one frame.** The dots slide over the surface as the scanner moves, so every patch is measured under many different dot arrangements. Refining the surface against all of those images at once (world-space spacetime stereo, 7.2) removes the error that per-frame matching leaves.
2. **Accurate data steering dense data.** The laser cross corrects the dense surface, and its crossing curves tie the whole scan together (laser anchoring, 4.2.4; laser-curve bundle adjustment, 7.3).
3. **Every number with an error bar.** Per-pixel confidence, weighted fusion, a per-vertex confidence map, and fits that report "Ø 8.02 ± 0.03 mm", backed by a simulation of that very scan when it matters (7.4).
4. **A calibration that checks itself.** Dense stereo, laser-plane and laser-stereo depth cross-check each other on every scan; a ball-bar test anyone can build gives an independent number.
5. **Gauges for car work.** Live gap & flush, edges and holes at laser accuracy, dent maps, symmetry checks (7.6–7.8).

**The strongest new idea has a first result.** World-space spacetime stereo (7.2) was prototyped on the simulator: three seeds, 24 frames each. Where the scan concentrates, its median error was 0.007–0.014 mm, against 0.044–0.060 mm for averaging per-frame depth (what fusion does today), and no point was off by more than 0.2 mm (8–19 % for averaging). At the edge of coverage the median improved 1.6–1.9 times. A 3° error in the assumed normal, or tracking-sized pose noise, barely changed it. A second prototype was negative: triangulating single dots was noisier than dense matching (4.1.3). Both are simulation only.

**Not now:** learned stereo in the live loop; neural or Gaussian-splatting geometry; non-rigid map deformation; classical spacetime stereo (the world-space version in 7.2 is different: it aggregates over the surface, not over pixels); any code or weights under a non-commercial or GPL licence. Section 5 says why for each.

**The biggest uncertainty.** Every gain here is predicted or measured on simulated data. The first real recordings may reorder this list. Section 6 lists the simulator upgrades and hardware tests that keep the plan honest.

## 2. The ranked top 10

| # | What (details in) | Milestone | Why it matters | Effort / risk | New dependency |
|---|---|---|---|---|---|
| 1 | **Depth confidence and uncertainty-weighted fusion on the GPU, with honest averaging** (4.1.1, 4.5.1, 7.10) | M3 | Attacks the grazing-angle and edge errors behind today's mesh p95. Moves the 1 mm reconstruction TSDF to the GPU (it is still on the CPU). Gives the per-region confidence the app promises, with σ that doesn't over-trust correlated frames. | M / low | none |
| 2 | **World-space spacetime stereo** (7.2) | M3 spike, M4 ship | In a simulator prototype (7.2) it cut the median error 4–6 times where the scan concentrates (1.6–1.9 times at the edge of coverage), with no point off by more than 0.2 mm. It removes the camera-fixed error at its source instead of filtering around it. | M–L / medium | none |
| 3 | **Laser anchoring: a "Refine with lasers" stage** (4.2.3, 4.2.4) | M3 | Rejects laser outliers against the dense surface, then pulls the mesh onto the laser points with a smooth correction. The Studio plan lists "laser refinement" as a step; this is its algorithm. | M / medium | none (scipy) |
| 4 | **Degeneracy-aware tracking** (4.3.1–4.3.3) | M3 | Flat and gently curved panels are the main car job. Stable sampling and per-direction handling keep ICP from sliding, and tell the user when to add tags. | S–M / low | none |
| 5 | **Straighten v2, with laser-curve bundle adjustment** (4.4.1, 4.4.2, 4.3.5, 7.3) | M3 → M4 | On top of `posegraph.py`: global verification for large drift, relocalisation after loss, fragments for long scans; then the laser curves' crossings pull every pose to laser accuracy and calibrate the laser planes and timing on each scan. | M–L / medium | none (Open3D FGR); optional TEASER++ (MIT) |
| 6 | **N-way multi-scan alignment, merged from raw data** (4.7) | M4 | The merge users complain about. Global and fine registration, one pose graph over all scans, residual maps, then one TSDF from every frame of every scan. | M / low–medium | none; optional small_gicp (MIT) |
| 7 | **Fits and deviation with uncertainty, and a digital twin for the ± values** (4.8, 7.4) | M5–M6 | Planes, cylinders, holes and spheres with ± values; deviation maps that say "inconclusive" when the error bar straddles the tolerance; on request, the uncertainty of one measurement by replaying that scan in the simulator. | M–L / medium | optional OpenCascade (LGPL) through OCP for STEP |
| 8 | **Calibration health and an accuracy check** (4.9) | M5–M6 | Stereo/laser agreement on every scan, row error from the dots, temperature logging, and a VDI/VDE 2634-style ball-bar test in the app. | M / medium | none (the firmware reads the IMU's temperature) |
| 9 | **Laser gauges for body work** (7.6) | M5 | Live gap & flush along a seam, and edges and hole rims at laser accuracy instead of from the fusion-rounded mesh. | M / medium | none |
| 10 | **Difficult-surface kit** (4.10) | M4–M6 | Black, glossy and metallic paint: emitter power and exposure control, rejection of glossy laser profiles, a cross-polariser option, and clear advice when spray is needed. | M / medium | polariser film (hardware) |

**Quick wins to do alongside** (each S, low risk, M3): decimate to a tolerance in mm, flag filled faces, smooth less where the data is confident (4.6.1–4.6.3), and the dense-surface check on laser points (4.2.3).

**Moved out of the top 10 in this revision:** the mesh edits above (they are quick wins, not big bets) and dot-centroid triangulation (4.1.3). Its first test was negative: per frame, single dots were noisier than dense matching (median 0.21 mm against 0.15 mm on the simulator's sphere scene). Spacetime stereo (item 2) uses the same dots better, across frames.

**Order and dependencies**
- Item 1 comes first. Items 2, 3, 7 and the quick-win smoothing use its uncertainties.
- Item 2 needs good poses and calibration: it comes after Straighten (item 5's first half) in the processing order, and its accuracy must be re-checked on real recordings against the laser cloud (6.4).
- Items 5 and 6 share the global-registration code. Build it once, in M3, for item 5.
- Items 9 and 10 can only be judged after the simulator upgrades in 6.2, and on real recordings.
- Item 8 needs the real scanner. Start logging temperature with the very first hardware recording; it costs almost nothing.

**Mapped onto the milestones**
- **M3, processing studio, GPU first:** items 1, 3 and 4, the quick wins, the first half of 5 (global verification), and the spike for 2. Item 1 also covers a GPU target that `VALIDATION.md` names: "the 1 mm reconstruction integration still run[s] on the CPU".
- **M4, multi-scan alignment:** item 6, the rest of 5 (relocalisation, fragments, laser-curve bundle adjustment), item 2 as a GPU stage, the software half of 10, and the dot spike (7.5).
- **M5, measure and reverse engineering:** items 7 and 9, and the calibration half of 8 (cross-check, artefact test); dent maps, symmetry and re-measuring from raw frames (7.7–7.9).
- **M6, ship it:** the digital twin's "certify this measurement" (7.4), the temperature model and in-app accuracy check of 8, the hardware half of 10 (polariser option, presets), and documentation that tells users what accuracy to expect, measured rather than quoted.

**New modules this plan proposes** (names are suggestions): `ohs/vision/dots.py` (4.1.3); a spacetime refinement stage, e.g. `ohs/track/refine_wss.py` with an OpenCL kernel (7.2); `ohs/track/curve_ba.py` (7.3); `ohs/mesh/anchor.py`, `remesh.py`, `snap.py` (4.2.4, 4.6.5, 4.6.6); `ohs/calib/online.py` and `ohs/calib/projector.py` (4.9, 7.5); `ohs/align/` (4.7); `ohs/measure/` for fits, holes, deviation, gauges, uncertainty and the artefact check (4.8, 4.9.6, 7.4, 7.6).

## 3. Facts used throughout

**Depth resolution.** With f = 720 px and B = 60 mm, one pixel of disparity is z²/(f·B) of depth:

| Distance | 150 mm | 250 mm | 450 mm |
|---|---|---|---|
| Depth per 1 px of disparity | 0.52 mm | 1.45 mm | 4.7 mm |
| Depth per 0.1 px | 0.05 mm | 0.14 mm | 0.47 mm |

Scanning close is the biggest single accuracy lever, and every hundredth of a pixel matters at the far end. The app should keep saying so.

**Laser geometry** (ARCHITECTURE.md, section 7). The vertical laser sits 116 mm to the right of the left camera, the horizontal one 75 mm above. At 250 mm the triangulation angles are about 25° (vertical) and 16° (horizontal). The vertical line crosses the horizontal epipolar lines, so it can also be triangulated by stereo alone. The horizontal line runs almost along them, so it can't.

**The speckle floor.** Laser light on a rough surface makes speckle, which shifts the apparent centre of a laser spot or line. Dorsch, Häusler and Herrmann (1994) give the floor:

δz ≈ C·λ / (2π · sin u · sin θ)

with C the speckle contrast (at most 1), u the half-angle of the lens aperture seen from the surface, and θ the triangulation angle. For this scanner (estimate):
- the OV9281 has 3 µm pixels, so f ≈ 720 px is a focal length of about 2.2 mm. At about f/2 (the simulator's assumption) the aperture is about 1.1 mm wide, so sin u ≈ 0.0022 at 250 mm;
- with λ = 650 nm and C = 1: δz ≈ 0.11 mm for the vertical line (θ ≈ 25°) and ≈ 0.17 mm for the horizontal one (θ ≈ 16°), per observation.

It averages down only over *different* speckle patterns, which happens as the scanner moves. The simulator's 0.07 mm laser median comes after averaging many frames, with a simplified speckle model (`RenderParams.speckle`, a 15 % multiplicative field). Expect real per-frame laser noise of 0.1–0.2 mm at 250 mm, and rely on multi-frame averaging. The same physics applies to option A's laser dots. Option B's LED dots have no speckle, but they are larger and blur away from the focus distance. Blue (450 nm) lasers would lower the floor by about 30 %, one reason commercial laser scanners use them. That is a hardware choice for a later revision.

**Licences.** OpenHandScan is MIT.
- **Use freely** (as a dependency or copied code, keeping the notice): MIT, BSD, Apache-2.0, zlib.
- **Use as an unmodified dependency:** MPL-2.0 (file-level copyleft, e.g. libigl's main code) and LGPL as a dynamically linked library (e.g. OpenCascade).
- **Inspiration only:** GPL and AGPL. Examples: CGAL's main packages, MeshLab and PyMeshLab, MeshFix and pymeshfix, CloudCompare, FAST-LIO, `gpytoolbox.copyleft`, libigl's `copyleft` folder.
- **Not usable, not even the weights:** non-commercial or research-only licences. Examples: FoundationStereo (NVIDIA), MonSter, Stereo Anywhere (CC BY-NC-SA), ElasticFusion (Imperial College), BundleFusion (CC BY-NC-SA), InfiniTAM (Oxford), the Inria Gaussian-splatting code and derivatives such as 2DGS, Neuralangelo (NVIDIA), DUSt3R (CC BY-NC-SA). Re-implementing an idea from a paper is normally fine; copying the code or weights is not.
- **Weights** also carry the terms of their training data. Training on our own simulator output avoids that question.
- **Patents are separate from licences.** Example: Polka Lines (learned dot patterns for active stereo) is patented (US 12,236,625 B2). This plan does not clear patents, and it is not legal advice.

**Compute rules** (from the M3 plan): any GPU through vendor-neutral OpenCL (PyOpenCL); every GPU kernel has a CPU twin and a test that the two agree; the CPU path always works. Code that is CUDA-only (BAD SLAM, most learned-stereo repositories as written) needs a port.

**Installed today:** numpy, scipy, OpenCV 5 contrib, Open3D 0.20, PyOpenCL. **Not installed:** PyTorch, ONNX Runtime, GTSAM, TEASER++'s Python bindings, small_gicp, trimesh, OCP/CadQuery, geomdl. New dependencies should be optional, have Windows wheels, and survive PyInstaller in M6.

**Open3D 0.20 already has** (checked in the project's environment): FPFH features, Fast Global Registration (FGR), colored and generalized ICP, tensor multi-scale ICP, pose-graph optimisation with a line process, signed distances through `RaycastingScene`, screened Poisson (an older version without the envelope), quadric decimation, Taubin smoothing, single-plane RANSAC (`segment_plane`) and robust multi-plane detection (`detect_planar_patches`). Its TSDF `integrate` has **no per-pixel weight input**. Custom weights need its tensor attribute access, or our own kernel.

## 4. Per-area detail

Each candidate gets a card:
- **What** it is, and the benefit for this scanner and car parts;
- **Evidence:** maturity, and what the source shows;
- **Licence**;
- **Cost:** CPU and GPU time;
- **Plugs into:** the module;
- **Effort / risk**;
- **Sim check:** how `ohs.sim` would show that it works.

Small items get a short paragraph instead.

### 4.1 Stereo depth on the dot pattern

**Today.** GPU census semi-global matching (ternary census, 4 paths, texture support) with a gain-invariant 1-D Lucas-Kanade sub-pixel step (`ohs/vision/depth_gpu.py`). SGBM with the same step is the CPU twin (`ohs/vision/depth.py`). On the simulator: 0.32 mm median error, and 1.8 % of pixels off by more than 2 mm. The remaining dot-scale error stays at nearly the same image positions from frame to frame (VALIDATION.md, finding 1).

#### 4.1.1 Matching confidence from the cost curve — P1, M3 (top 10 #1)
- **What:** a per-pixel confidence built from numbers the matcher already has: the margin between the best and second-best aggregated cost (peak ratio), the left-right disagreement, the texture-support fraction, the Lucas-Kanade residual and gradient energy, and the curvature of the cost at its minimum. Turn them into a disparity standard deviation σ_d, and a depth one: σ_z = z²·σ_d / (f·B).
- **Benefit:** weights for fusion (4.5.1); a principled way to drop the worst pixels at depth edges and grazing angles; the raw material for per-region confidence in the app.
- **Evidence:** Hu and Mordohai (TPAMI 2012) compared 17 classical measures; peak-ratio and left-right measures rank well. Poggi et al. (TPAMI 2021) found learned measures better, with the classical ones close behind and free. Mature.
- **Licence:** own code.
- **Cost:** a few extra reads in the existing winner-take-all and Lucas-Kanade kernels, and one more float image to download. Under 2 ms on either GPU (estimate). On the CPU, SGBM exposes no costs, so the CPU twin uses the left-right, texture and Lucas-Kanade terms only.
- **Plugs into:** `ohs/vision/depth_gpu.py` (kernels); `ohs/vision/depth.py` (`DepthEstimator` returns depth and σ_z); `ohs/app/pipeline.py` (`FrameResult` carries σ_z).
- **Effort / risk:** S–M / low.
- **Result (2026-11-09, shipped):** the Lucas-Kanade residual, plus terms for holes, incidence and near range, was enough (`SigmaModel`, the same terms on both backends). Calibrated on 12 simulated recordings with true depth: 2σ covers 96.7 % of the depth errors on the GPU and 95.9 % on the CPU (90–99 % per recording). Details: `VALIDATION.md`, "Depth confidence".
- **Sim check:** the simulator has the true depth per pixel. Check that the prediction is calibrated: about 95 % of pixels should fall within 2σ, per scene (sphere, car panel, bracket) and per resolution. Plot the actual error against the predicted σ.

#### 4.1.2 Slanted-window sub-pixel refinement — P2, M3–M4
- **What:** extend today's 1-D Lucas-Kanade step to three unknowns per window: the disparity and its slopes along u and v. The right window is warped by the local plane, not just shifted, as in PatchMatch Stereo (Bleyer et al. 2011).
- **Benefit:** car panels are mostly seen at an angle. A window that assumes a surface facing the camera mixes different disparities across its width, and biases them. Expect smaller errors on slanted areas, and fewer "terraces" at grazing angles.
- **Evidence:** slanted windows have been standard since 2011; the gain on strongly slanted surfaces is well documented. New in this code.
- **Licence:** own code.
- **Cost:** a 3x3 solve per pixel instead of 1x1, one to three iterations: a few ms on the GPU (estimate).
- **Plugs into:** `_LKRefiner` in `ohs/vision/depth.py`, and its twin in `depth_gpu.py`.
- **Effort / risk:** M / low–medium (unstable in weak texture; keep the existing rejection rules).
- **Sim check:** the depth error split by incidence angle, on the car panel and on a new tilted-plane scene (45–75°), before and after.

#### 4.1.3 Dot-centroid triangulation: every dot is a laser point — P3 (first test negative; see also 7.5)
- **What:** find each projected dot in both rectified images and fit its centre to a fraction of a pixel (a Gaussian fit or a weighted centroid). Use the SGM disparity to pair each left dot with its right dot on the same row, and triangulate the pair. The result is a sparse cloud, one point per visible dot (the DOE has about 10,000), next to the dense depth.
- **The hope:** block matching mixes several dots in one window. It is biased by their arrangement, by where they fall on the pixel grid, and by foreshortening. That is the camera-fixed error that broke tracking. A dot centre is a much simpler estimate. If real dots gave 0.05 px, that would be 0.07 mm at 250 mm: laser-line class, but over the whole field.
- **Evidence:** sub-pixel centroiding of round targets is standard photogrammetry. No published handheld scanner was found that triangulates its projected random dots this way as a complete system. Treat it as research.
- **Risks:**
  - option A's dots carry laser speckle (section 3), which moves their centres;
  - option B's dots are larger, and blur away from 250 mm;
  - dots merge at grazing angles;
  - on a slanted surface the two cameras see a dot from different angles, which pulls the two centres slightly apart (a bias that grows with dot size and slant);
  - the laser lines hide some dots.
- **First test, negative** (simulator, 2026-11-09; a numpy prototype, lasers off, about 6,000–7,000 dot pairs per frame, compared with the dense GPU depth at the same sub-pixel positions):

  | Scene | Dot triangulation: median / p95 (mm) | Dense depth: median / p95 (mm) |
  |---|---|---|
  | Sphere on plane | 0.21 / 1.5 | 0.15 / 0.80 |
  | Car panel | 0.28 / 2.3 | 0.17 / 1.1 |
  | Sphere on plane, speckle 0.5 instead of 0.15 | 0.54 / 2.5 | 0.39 / 1.7 |

  A single dot is a few pixels, so its centre wanders with noise and speckle, independently in each camera; a matching window pools several dots. Saturated dots snap to whole pixels (a flat top defeats a 3-point fit), which probably drives the p95. A better version (a 2D fit that handles saturation, each dot averaged over frames) might close the gap. But world-space spacetime stereo (7.2) already gets the multi-frame benefit, without fitting single dots.
- **Verdict:** keep dot fitting for jobs that pool many dots at once: the projector's rays and dot-free images (7.5), and the row-error monitor (4.9.2). Not as a per-point accuracy layer.
- **Licence:** own code.
- **Cost:** blob detection plus a small fit per dot, one GPU work-item each: a few ms (estimate).
- **Plugs into:** a new `ohs/vision/dots.py` (with a GPU twin), used by 7.5 and 4.9.2.
- **Effort / risk:** M / medium.
- **Sim check:** if it is revisited: a 2D fit with saturation handling, per-dot averages over frames, and the physical speckle model of 6.2.

#### 4.1.4 Learned stereo matchers — P3, after M6
- **What:** RAFT-Stereo, CREStereo, IGEV-Stereo and IGEV++, Selective-Stereo and DEFOM-Stereo build a correlation or cost volume and refine the disparity in iterations. They lead the public benchmarks, and are strong in weak texture and on reflections.
- **Benefit here:** smaller than on the benchmarks. The dot pattern already gives classical matching plenty of texture. Possible gains: black or glossy paint where the dots fade, and edges.
- **Evidence:** RAFT-Stereo (3DV 2021), CREStereo (CVPR 2022), IGEV-Stereo (CVPR 2023), IGEV++ (2024), Selective-Stereo (CVPR 2024), DEFOM-Stereo (CVPR 2025). All are trained mostly on passive colour images; none on a projected dot pattern seen by a monochrome sensor.
- **Licence:** RAFT-Stereo MIT; CREStereo Apache-2.0; IGEV and IGEV++ MIT; Selective-Stereo MIT. DEFOM-Stereo's code is MIT, but its ViT-L weights build on Depth Anything V2's large model, which is CC BY-NC 4.0; only its small (ViT-S) model is Apache-2.0. FoundationStereo, MonSter and Stereo Anywhere: ✗ (research-only).
- **Cost (estimate):** tens to hundreds of milliseconds per megapixel on a desktop RTX GPU, several times that on the RTX 3070 Ti laptop, and seconds on the Radeon 680M. Not for live use; offline on keyframes at most. They also need a second GPU stack next to OpenCL: ONNX Runtime with DirectML on Windows, OpenVINO, or CUDA. OpenCV's DNN module can run ONNX through OpenCL, but may not support all their operators.
- **Plugs into:** a `DepthParams.backend = "learned"` option behind `DepthEstimator`, offline only, for keyframes. Never in the live pipeline.
- **Effort / risk:** L / high (export, operator support, the gap between their training data and our images, speed).
- **Sim check:** the simulator can render unlimited labelled pairs with our pattern, lens and noise. Train or fine-tune on those (our own weights, no licence questions), and test on held-out scenes and on real recordings.
- **Verdict:** only after 4.1.1 to 4.1.3, and only if real data shows SGM failing somewhere a network demonstrably helps.

#### 4.1.5 Self-supervised active-stereo training — P3, only with 4.1.4
- **What:** training without ground truth. ActiveStereoNet (ECCV 2018) uses a photometric loss with local contrast normalisation, so the brightness fall-off of the dots doesn't dominate. ActiveZero (CVPR 2022) mixes simulated and real data.
- **Benefit:** it closes the gap between simulated and real images for 4.1.4.
- **Evidence:** both show large gains on infrared active stereo of the RealSense kind. ActiveStereoNet has no official code; ActiveZero's licence was not checked.
- **Plugs into:** training scripts outside the shipped engine.

#### 4.1.6 The projector as a third view — P3, research
- **What:** the dot pattern never changes, so it can be stored as a reference image, as the first Kinect did. Each camera can then also be matched against the projector, which adds two baselines (left–projector, right–projector) to left–right. "Connecting the Dots" (Riegler et al., CVPR 2019, MIT code) learns this for one camera plus a projector.
- **Benefit:** depth where only one camera sees the surface (recesses, around brackets, behind ribs), and an independent check that catches wrong stereo matches.
- **Risks:**
  - The projector needs its own calibration (one ray per dot), for example by capturing the dots on the board at several distances in the wizard.
  - Option A: a red laser diode's wavelength rises with temperature (roughly 0.2–0.3 nm per °C), and a DOE's dot angles scale with the wavelength. Over a 20 °C change that moves the dot rays by a few pixels at the edge of the field (estimate), so the model must follow the temperature. Stereo matching alone doesn't care, because both cameras see the same dots. Option B's printed mask is geometrically stable.
  - Polka Lines (CVPR 2021), which treats the pattern as a third view in a learned network, is patented. Build only on the classical reference-image idea.
- **Licence:** own code; Connecting the Dots (MIT) for reference.
- **Cost:** a second matching pass: about the cost of today's depth (estimate).
- **Plugs into:** `ohs/vision/depth_gpu.py` (the left image matched against the reference, rectified to the projector); projector calibration in `ohs/calib/`.
- **Effort / risk:** L / high.
- **Sim check:** render with the true projector model; measure the depth filled in occluded areas, and its error. For option A, add a wavelength-scale change.

#### 4.1.7 Spatio-temporal aggregation of the fixed pattern
- **Classical spacetime stereo: ✗.** Spacetime stereo (Davis et al., TPAMI 2005) assumes the pattern changes while the scene stays still relative to the cameras. Here the pattern is fixed to the cameras and the scene moves, so a pixel sees a different surface point in every frame. A time window per pixel mixes points.
- **Aggregation in the world frame is the right form.** The TSDF averages each surface point over many frames, each with a different dot arrangement on it, and 4.5.1 makes that average weighted. A much stronger form works on the images themselves instead of on per-frame depth: world-space spacetime stereo (7.2), which has a first result on the simulator.
- **One cheap hardware test (P3):** vibrate the projector slightly (a phone-style vibration motor), so the pattern moves on the surface between frames. That would decorrelate the camera-fixed dot-scale errors and, for option A, the speckle. Risks: blur within the exposure, noise, power.

#### 4.1.8 Pixel locking — already handled
The Lucas-Kanade step removes most of SGBM's pixel locking (the effect is described by Shimizu and Okutomi, IJCV 2005), and the GPU path uses the same step. Add one regression test: the histogram of the fractional part of the disparity error should be flat.

**Will not work for stereo:**
- learned matchers live on an integrated GPU (speed);
- FoundationStereo, MonSter and Stereo Anywhere (licences);
- monocular depth models such as Depth Anything as a depth source (relative, not metric);
- edge-preserving smoothing such as WLS as an accuracy tool (it hides errors; the code already keeps WLS from filling holes);
- classical spacetime stereo (above).

### 4.2 Laser lines

**Today** (`ohs/vision/laser.py`): a separable ridge filter that suppresses the dots; a half-level centre on the raw profile (unbiased for symmetric profiles, handles saturation); linking; per-segment checks; a robust local-linear fit; exclusion at the crossing; and a check against the right image. Points are triangulated on the calibrated planes. On synthetic images the centre error is 0.04–0.08 px; on the simulator the laser points are 0.07 mm (median). The laser cloud is exported, but does not correct the mesh yet.

#### 4.2.1 Steger's line detector — P3
- **What:** Steger (TPAMI 1998) finds line centres from second derivatives of the smoothed image along the local normal, with a correction for asymmetric profiles. It also gives the line width.
- **Benefit here:** small. The current method already handles symmetric and saturated profiles, tilts up to 30°, and curvature. Steger helps on tight curves (creases, small radii), because it measures across the true local normal rather than along image rows or columns.
- **Evidence:** mature; the usual baseline in industrial laser-stripe papers.
- **Licence:** own code.
- **Cost:** a few Gaussian derivative filters around the line pixels: a few ms on the CPU.
- **Plugs into:** a `centre_method` option next to `_half_level_centres` in `ohs/vision/laser.py`.
- **Effort / risk:** S–M / low.
- **Sim check:** the existing laser tests, plus tighter curvature (spheres of 10–20 mm radius, a 5 mm crease).
- **Verdict:** keep the current centre. Borrow Steger's width and asymmetry as quality flags (4.2.2).

#### 4.2.2 Profile quality flags for glossy, black and metallic paint — P1–P2, M4 (top 10 #10)
- **What:** score each centre by its profile: contrast (it exists as `strength`), width against the expected width at that range, left/right asymmetry, the length of a saturated run, and whether a second peak sits in the window. Drop or down-weight bad profiles, instead of trusting every point equally.
- **Benefit:** on clearcoat the line shows a sharp glint or a mirrored second line. On metallic flake paint the profile is speckled and has several peaks. On black paint it is weak. All three bias the centres.
- **Evidence:** recent industrial papers on stripe extraction on reflective metal (2020–2026) use these cues: double-Gaussian fits, asymmetry tests, adaptive thresholds, and in 2026 polarisation. None solves interreflection fully.
- **Licence:** own code.
- **Cost:** negligible (the profile is already in hand).
- **Plugs into:** `ohs/vision/laser.py` (a per-point quality in `LaserLines`); weights in `ohs/track/fusion.py`.
- **Effort / risk:** S / low in code; medium in tuning, which needs real painted samples.
- **Sim check:** needs the gloss upgrade of 6.2 (a specular lobe and a mirrored line). Then measure the false-point rate and the centre bias on a glossy panel.

#### 4.2.3 Agreement with the dense surface — P1, M3 (quick win, part of top 10 #3)
- **What:** keep a laser point only if it agrees with the dense depth at that pixel within k·σ, with σ from 4.1.1 plus the laser's own noise. Reflections and glints usually land off the real surface.
- **Benefit:** it removes the most damaging laser outliers on glossy parts and in concave corners, where one stray point per frame becomes a spike after averaging.
- **Evidence:** standard consistency checking between sensors. The existing right-image check is the camera-to-camera version.
- **Licence:** own code.
- **Cost:** one lookup per laser point; negligible.
- **Plugs into:** `laser_points(...)` in `ohs/vision/laser.py` takes an optional depth map; `FramePipeline.process` passes it.
- **Effort / risk:** S / low.
- **Sim check:** today, check that it loses almost no true points on the clean scenes (under 0.5 %). With the gloss upgrade, check that it removes the mirrored line.
- **Result (2026-11-10, shipped):** within 4σ (at least 1 mm) of the dense depth at the point's pixel *or 6 px to either side*: on the line's own pixel the dense depth can be wrong, because the horizontal line runs along the epipolar rows. It drops 0.00–0.12 % of the true points on 4 clean scans (0.3–3.3 % with the pixel alone). The mirrored-line check waits for the gloss upgrade. Details: `VALIDATION.md`, "Refine with lasers".

#### 4.2.4 Laser anchoring of the dense surface — P1, M3 (top 10 #3)
- **What:** after fusion, measure the mesh's offset from the laser cloud along the normals: for each laser point, the nearest surface point and a signed residual. Then fit a smooth correction that explains those residuals. Minimise

  Σᵢ wᵢ·(residual after correction)² + λ·‖L·δ‖²

  over a per-vertex offset δ along the normal, with L the mesh Laplacian (as in Laplacian surface editing, Sorkine et al. 2004) and the weights wᵢ from the laser and depth uncertainties. Vertices far from laser data get the smooth interpolated correction, fading to zero beyond a radius (10–20 mm, to tune).
- **Benefit:** near every laser sweep, the dense surface inherits the laser's accuracy, and slow biases of the dense depth (calibration, real optics) are removed. Scanners that have both kinds of light, as found for this plan, offer them as separate modes rather than using one to correct the other.
- **What it does not fix:** pose errors. Laser points and dense depth share the same poses, so run Straighten first.
- **A per-scan variant:** the same residuals, collected in image coordinates over a whole scan, estimate a camera-fixed, slowly varying bias of the dense depth. That model is part of the calibration cross-check (4.9.1), and can correct every frame's depth before fusion.
- **Evidence:** the parts are standard (weighted least squares with Laplacian regularisation). Using a laser cross to correct dense pattern-stereo depth inside one scanner was not found described elsewhere.
- **Licence:** own code (scipy sparse solvers).
- **Cost:** one sparse solve over the vertices near laser data. For a 1M-vertex mesh, seconds on the CPU with conjugate gradients (estimate).
- **Plugs into:** a new `ohs/mesh/anchor.py` (a pure function on `(V, F)`, like the rest of `ohs.mesh`); a "Refine with lasers" stage after Fuse in `ohs/app/process.py` and the server jobs; residuals before and after in `report.json`.
- **Effort / risk:** M / medium (the laser cloud has its own errors; over-correction near edges; needs 4.2.3 first).
- **Sim check:**
  - inject a smooth bias field (0.2–0.5 mm, a few centimetres in wavelength) into the dense depth of the sphere and car-panel scans; anchoring should remove it near the sweeps;
  - without injected bias, it must never make the mesh worse (median, p95 and signed mean against the analytic surfaces);
  - the residuals it reports must match the truth.
- **Expectation, honestly:** in GPU mode the simulator's mesh median (0.065–0.088 mm) is already close to the laser median, so the simulator will mostly show gains in p95 and bias. The case for anchoring has to be made on real recordings.
- **Result (2026-11-10, shipped as the "Refine with lasers" stage):**
  - Not the least-squares form: on 500,000 vertices the bi-Laplacian solve took 12 s and did not converge. The correction is a normalized convolution by heat diffusion on the mesh (a 5 mm Gaussian, about 0.8 s), with Tukey re-weighting and a fade where the laser coverage thins out.
  - A camera-fixed depth bias (0.1 % scale and a 0.3 mm bowl) drops near the lines from 0.24–0.26 mm to 0.05–0.06 mm (median); the signed mean from −0.24/−0.26 to −0.02/−0.03 mm.
  - Without a bias, nothing gets worse (median within 1 %, p95 and mean slightly better), but only because corrections under 0.1 mm are not applied. The laser lines carry correlated errors of 0.05–0.08 mm per frame, and following them made the sphere 19 % worse. The stage changes the mesh only when at least 1 % of it would move.
  - As expected above, the simulator shows no gain without a bias: its fused surface is as accurate as the laser points. Details: `VALIDATION.md`, "Refine with lasers".

#### 4.2.5 Laser points inside the TSDF — P2
Integrate laser points into the reconstruction TSDF as a sparse, high-weight depth image. It is simpler than 4.2.4, but 1 mm voxels blur a 0.07 mm measurement, and only voxels next to the lines change. Use it for coverage where dense depth is missing (black paint where only the laser shows), not for accuracy.

#### 4.2.6 Plane-free triangulation of the vertical line — P2, with 4.9
The vertical line crosses the epipolar lines, so its left and right centres can be paired row by row and triangulated without the laser plane (`stereo_check` already predicts the right-image position). That gives a third depth, independent of the plane. The calibration cross-check (4.9.1) and the online plane refit (4.9.4) use it.

#### 4.2.7 More laser lines — hardware, later
Commercial handheld laser scanners project many crossing lines, typically seven or more. Two lines give at most about 2,000 points per frame (estimate). More lines need a multi-line optic and a way to tell the lines apart. That is out of scope here; it is noted for a future hardware revision, together with blue lasers (less speckle, and less light scattered inside paint and plastic).

**Will not work for lasers:**
- epipolar or motion-contrast imaging to reject interreflections (O'Toole et al. 2015 and similar): it needs a projector and a rolling shutter synchronised line by line; the OV9281 is global shutter on UVC;
- learned stripe extractors: there is no public, permissively licensed model trained on car paint, and 4.2.2 should be measured first.

### 4.3 Tracking and odometry

**Today** (`ohs/track/markerless.py`), in the family of KinectFusion (Newcombe et al. 2011) and of the real-time structured-light acquisition of Rusinkiewicz, Hall-Holt and Levoy (2002), which already aligned each new range image by ICP: projective point-to-plane Gauss-Newton ICP, three levels with uniform sampling (every 16th, 8th and 4th pixel), Huber weights, a normal gate, the gyro rotation prior (0.02° per frame), a weak translation prior and constant velocity. The target is a 2 mm tracking TSDF of filtered depth (the rolling GPU volume, with its border and weight tests). Degeneracy is flagged by an eigenvalue ratio, but not acted on. The accelerometer is not used.

#### 4.3.1 Geometrically stable sampling — P1, M3 (top 10 #4)
- **What:** pick ICP source points so that all six motion directions are constrained (Gelfand, Ikemoto, Rusinkiewicz and Levoy, 3DIM 2003). Each candidate has a constraint vector [p × n, n]. Points are chosen to balance the eigen-directions of their covariance, instead of taking every k-th pixel.
- **Benefit:** on a car panel most points sit on a nearly flat area and all say the same thing. The few on a crease, a hole edge or a curve carry the information about sliding, and uniform sampling drowns them. Stable sampling raises their share, so ICP holds position on nearly degenerate parts.
- **Evidence:** well established, and widely reimplemented.
- **Licence:** own code.
- **Cost:** a 6x6 covariance and a sort: well under 1 ms on the CPU (estimate).
- **Plugs into:** `MarkerlessTracker._source_points` and `_icp`; the same in the loop verification of `posegraph.py`.
- **Effort / risk:** S / low.
- **Sim check:** the car panel with and without the IMU, 15 seeds, maximum trajectory error; a nearly degenerate scene (a plane with a small bump; the sphere scene) with the gyro off.
- **Result (2026-11-09, simulator):** worse, so not used.
  - It was run on 18 scans, with the gyro on and off. With the gyro, the maximum error on 12 s car-panel sweeps went from 1.9–5.8 mm to 4.2–5.0 mm.
  - The points that say the most about the weak directions sit at grazing angles, where stereo depth is least reliable.
  - Revisit with the depth confidence of 4.1.1, weighting each point's information by it. Details: `VALIDATION.md`, "Degeneracy-aware tracking".

#### 4.3.2 Weak-direction handling — P1, M3 (top 10 #4)
- **What:** take the 6x6 ICP information matrix (rotations scaled to mm at the scene distance, as the degeneracy flag already does), and split the update into well-constrained and weak eigen-directions. Update only the well-constrained ones from ICP. Take the weak ones from the prediction: the gyro for rotation, the IMU or constant velocity for translation. This is the "solution remapping" of Zhang, Kaess and Singh (ICRA 2016) and the localizability analysis of X-ICP (Tuna et al. 2022). Report the weak directions to the UI, and pass them to the pose graph.
- **Benefit:** today's soft priors keep weak directions near the prediction, but with one strength for everything. A per-direction decision is sharper. Its output also tells the user what to do: "sliding along the panel: add two tags, or include the panel's edge".
- **Evidence:** mature in LiDAR odometry; X-ICP shows clear gains in degenerate environments. GenZ-ICP (2024, MIT) mixes point-to-plane and point-to-point terms for the same reason. KISS-ICP's adaptive correspondence threshold (from the observed motion) is a cheap idea for the gates too.
- **Licence:** own code.
- **Cost:** one 6x6 eigen-decomposition per iteration; negligible.
- **Plugs into:** `MarkerlessTracker._icp`; `TrackResult.info` (the weak directions); the server's live status; the odometry edges in `posegraph.py` (an information matrix per edge, instead of one random-walk model).
- **Effort / risk:** S–M / low–medium (the thresholds need real data).
- **Sim check:** as 4.3.1, plus: the reported weak directions match the true symmetry (the sphere's axis, the panel's plane).
- **Result (2026-11-09, simulator):** half of it shipped.
  - **The report shipped.** `info["weak"]` classifies the weak subspace as "slide" and/or "turn", with its axis; the sphere's turn is found in 87–100 % of frames. The live app turns it into hints: sliding even with the gyro, turning only without.
  - **Remapping did not ship.** With a threshold that catches a car panel's weakest directions, it made tracking much worse: 13–57 mm instead of 1.9–5.8 mm on 12 s sweeps with the gyro. It throws away the weak but correct information of a crease or a hole edge, and the soft priors already blend that with the prediction.
  - **Still open:** the per-edge information matrices for the pose graph. Details: `VALIDATION.md`, "Degeneracy-aware tracking".

#### 4.3.3 Symmetric ICP — P2, M3
- **What:** Rusinkiewicz (SIGGRAPH 2019) minimises the distance along the sum of both normals, (p − q)·(n_p + n_q), and rotates both sides half-way. It converges in fewer iterations, and from farther away, than point-to-plane.
- **Benefit:** fewer iterations than today's 8 + 6 + 4, and a wider basin after fast hand motion or a relocalisation. Source normals already exist (the normal gate uses them).
- **Evidence:** the paper shows faster convergence on standard tests, and several libraries adopted it.
- **Licence:** own code.
- **Cost:** the same per iteration as today.
- **Plugs into:** `MarkerlessTracker._icp` (a `FusionParams.icp_objective` switch) and the ICP in `posegraph.py`.
- **Effort / risk:** S / low.
- **Sim check:** iterations to converge, and drift, on the standard scans; relocalisation success from perturbed starts (5–20 mm, 2–10°).

#### 4.3.4 The accelerometer: gravity and short-term translation — P2, M3–M4
- **What:**
  - gravity gives absolute pitch and roll whenever the hand isn't accelerating much. Fused with the gyro (a complementary or Kalman filter), it bounds tilt drift;
  - IMU preintegration between frames (Forster et al., T-RO 2017) predicts translation well over fractions of a second, which can fill the weak directions of 4.3.2.
- **Benefit:** gravity removes two of the three rotation-drift directions on long scans (as pose-graph edges, 4.4.3). Preintegration bridges short degenerate stretches on flat panels.
- **Honest limit:** the accelerometer can't hold position for long, because its bias integrates to centimetres within seconds. It bridges; it doesn't replace tags on a big flat panel.
- **Evidence:** standard in visual-inertial odometry; the theory is mature.
- **Licence:** own code; GTSAM (BSD) later (4.4.3).
- **Cost:** negligible.
- **Plugs into:** `ohs/track/imu_prior.py` (`GyroPredictor` gains accelerometer and gravity handling); `markerless.py` (translation prediction); `posegraph.py` (gravity edges). `ohs/calib/imu.py` calibrates from the gyro only today; it needs the accelerometer bias and scale from the still phase.
- **Effort / risk:** M / medium (accelerometer calibration and the lever arm).
- **Sim check:** the simulator already synthesises the accelerometer, with gravity, lever arm, bias and noise. Measure tilt drift over a long orbit with and without gravity, and the translation error over 0.25–1 s gaps with ICP switched off.

#### 4.3.5 Relocalisation after tracking loss — P2, M4 (part of top 10 #5)
- **What:** when tracking is lost and the user points somewhere else, match the current frame's cloud against the model with FPFH features and FGR (both in Open3D), or with TEASER++ or KISS-Matcher. Then confirm with ICP and the loop-verification gates of `posegraph.py`.
- **Benefit:** "go back to where you were" becomes "point at anything you already scanned".
- **Evidence:** FGR (ECCV 2016), TEASER++ (T-RO 2021) and KISS-Matcher (2024) are mature.
- **Licence:** Open3D, TEASER++ and KISS-Matcher are MIT.
- **Cost:** features on a downsampled frame and a model region: tens of ms on the CPU (estimate), only while lost.
- **Plugs into:** the recovery path of `MarkerlessTracker`, against the reconstruction model (the rolling GPU volume forgets distant surfaces).
- **Effort / risk:** M / medium (false relocalisation on symmetric parts; reuse the strict gates).
- **Sim check:** cut 1–3 s out of a recording, or make the trajectory jump, and count correct and false recoveries over seeds.

#### 4.3.6 Direct SDF tracking — P3
Align the depth points directly to the TSDF, by minimising the interpolated distance at each point (Bylow et al., RSS 2013), with no ray cast. It is accurate below a voxel and suits the GPU, but it isn't urgent: the GPU ray cast is already fast (7 ms on the 680M) and accurate.

**Will not work for tracking:**
- learned visual odometry (DROID-SLAM, DPVO) and feature-based visual odometry on these images: the dot pattern is fixed to the camera and dominates the image, so features and flow lock onto it, just as ICP locked onto the camera-fixed depth errors;
- a photometric term on ambient light while the dots are on, for the same reason. It would need dot-free frames, which UVC can't schedule reliably frame by frame;
- LiDAR odometry stacks: FAST-LIO2 is GPL (inspiration only), and these designs assume 360° sweeps;
- recovering the unobservable directions of a truly symmetric scene from geometry alone: impossible by definition. The gyro (rotation), tags, or the part's own small features must do it.

### 4.4 Loop closure and global consistency

**Today**, in the working tree (`ohs/track/posegraph.py`, "Straighten"):
- keyframes, and revisit candidates by look-at proximity;
- point-to-plane ICP verification within a plausible-drift bound, with point-to-plane information matrices;
- Open3D's Levenberg-Marquardt with the line process of Choi, Zhou and Koltun (CVPR 2015);
- "never worse" checks, corrections spread over the frames, then re-fusion.

The simulator gained orbit and sweep trajectories, and synthetic drift, for it. This design is right for measuring parts: rigid poses, then re-fusion from the raw data. The additions below extend it.

#### 4.4.1 Global verification for large drift — P2, M3–M4 (top 10 #5)
- **What:** today the candidates come from the drifted trajectory, and ICP starts from the drifted relative pose. After a long scan or a tracking loss, the true revisit can lie outside that window. Add a global step: FPFH features and FGR between keyframe clouds (or fragments, 4.4.2), then the existing ICP verification. Optionally TEASER++ (certifiable, very robust to outliers) or KISS-Matcher (faster). Use descriptor similarity to propose the candidates that the proximity test misses.
- **Benefit:** closes loops beyond ICP's reach, and finds revisits that a drifted trajectory hides.
- **Evidence:** Choi et al. (2015) do exactly this between fragments. FGR is in Open3D. TEASER++ certifies its solutions.
- **Licence:** all MIT.
- **Cost (estimate):** FPFH per keyframe cloud, roughly 10–50 ms on the CPU at 2–3 mm voxels; FGR per pair in milliseconds to tens of milliseconds.
- **Plugs into:** `find_loop_candidates` and `verify_loop` in `posegraph.py`.
- **Effort / risk:** M / medium (false loops on repetitive or symmetric parts; keep the line process and the never-worse rule).
- **Sim check:** orbit and sweep scans with 5–10 times today's synthetic drift. Count true and false loops; measure the final error against the truth.
- **Done (2026-11-10, M3 phase 3 item 6).** FPFH + FGR between keyframe clouds, as a second try after ICP from the tracked pose, with a candidate gate that widens with the travel between the views. Two findings shaped it:
  - **The drift model was the first wall, not ICP.** At 10 times today's random walk ICP still verified the loops, but the graph's fixed drift model (about 2 mm over a 60-frame orbit) made the line process prune them all. The model is now scaled to what the loops show (the median of how far they moved against its spread).
  - **FGR was needed only past about 35 mm.** Below that, ICP from the tracked pose with wider bounds verified the loops. FGR verified 2–3 of them at 78 mm.

  On two bracket orbits with 5–20 times the usual drift, the doubled surfaces close 3–18 times, where the old version left 8–50 mm. Sliding along the surfaces is not fixed (up to 17 % worse with steady drift). A large loop counts only when a second loop also passes. Not done: descriptor similarity to propose candidates the widened gate misses, fragments (4.4.2), TEASER++. Untested: symmetric parts, jumps after tracking loss, real recordings (`VALIDATION.md`, "Straighten for large drift").

#### 4.4.2 Fragments — P2, M4
- **What:** split long scans into fragments (by frame count or by travel), and fuse each one locally. Register the fragments to each other (4.4.1), optimise a graph of fragments, then re-fuse everything. This is the pipeline of Choi et al. (2015), which Open3D's reconstruction system follows. Voxgraph (Reijgwart et al. 2020, BSD) does the same with SDF submaps.
- **Benefit:** a whole door or an engine bay stays consistent. Fragments overlap more than single keyframes and are less noisy, so they register more reliably.
- **Licence:** own code on Open3D; voxgraph for reference.
- **Cost:** about one extra local fusion per fragment, plus pairwise registration (seconds per scan, estimate).
- **Plugs into:** `posegraph.py` (fragment nodes) and `ohs/app/process.py`.
- **Effort / risk:** M / medium.
- **Sim check:** a 60–120 s sweep over the car panel with drift; a keyframe graph against a fragment graph.

#### 4.4.3 IMU and gravity in the graph; a factor-graph back end — P3, M4–M6
- **What:** add the gyro's relative rotations and the gravity direction as edges. Later, move from Open3D's pose graph to a factor graph (GTSAM, BSD), with IMU preintegration factors, tag observations and loop closures in one optimiser.
- **Benefit:** rotation drift nearly disappears (gyro plus gravity). Marker and markerless modes stop being separate: tags become constraints in the same graph as loops.
- **Licence:** GTSAM is BSD; it is a new and sizeable dependency.
- **Effort / risk:** M–L / medium.
- **Sim check:** long orbits with the gyro and accelerometer; drift with and without the IMU edges; tags as factors against today's marker mode.

#### 4.4.4 Dense multi-view refinement — P3
After the graph, refine all keyframe poses jointly against each other with dense point-to-plane terms (multiview registration, Pulli 1999), or adjust surfels and poses together as BAD SLAM does (Schöps et al., CVPR 2019, BSD-3, CUDA-only). It removes the sub-millimetre misalignment the graph leaves. Offline only, and heavy. Port the idea, not the CUDA code.

**Will not work, or not for us:**
- **non-rigid deformation graphs** (ElasticFusion, built on embedded deformation, Sumner et al. 2007): bending the surface to close a loop changes the part's shape. For measuring parts, rigid poses plus re-fusion is correct. ElasticFusion's code is also non-commercial;
- **BundleFusion's sparse SIFT term:** features lock onto the fixed dot pattern. Its dense term and its re-integration idea are already how Straighten works. Its code is CC BY-NC-SA;
- **InfiniTAM's loop closure** (Kähler et al. 2016): non-commercial licence.

### 4.5 Fusion and representation

**Today:** two TSDFs. The tracking model is 2 mm, on the GPU (a rolling cube). The reconstruction model is 1 mm, Open3D on the CPU, with weight +1 per observation, and is meshed by marching cubes. The laser cloud is kept separately.

#### 4.5.1 Uncertainty-weighted TSDF on the GPU — P1, M3 (top 10 #1)
- **What:** weight each depth pixel by 1/σ_z² from 4.1.1, times the cosine of the incidence angle, times a fall-off near depth edges and near the ends of the depth range (where today's mesh p95 comes from). Let the truncation band widen with σ_z. Keep a per-voxel second moment, so each vertex gets a standard deviation. This follows the weighted average of Curless and Levoy (1996) and the sensor-noise weighting of Nguyen, Izadi and Lovell (2012).
- **Benefit:** the known p95 artefacts (grazing angles, edges, the range limit) lose their say, good data dominates, and the app gets a per-vertex confidence map ("accuracy you can see").
- **Evidence:** weighted TSDF averaging has been standard since 1996. Noise-model weights are shown to help in Nguyen et al. and many later systems.
- **Licence:** own code.
- **Cost:** Open3D's `integrate` takes no per-pixel weights. This needs either custom integration through Open3D's tensor attributes (on the CPU, slow in Python) or our own OpenCL kernel. `VALIDATION.md` already names the CPU reconstruction integration as a GPU target. The rolling cube can't hold a whole scan at 1 mm, so the reconstruction volume needs a sparse layout (hashed bricks, as in voxel hashing, Nießner et al. 2013) or bricks streamed to the CPU. Per frame it should cost the same order as the tracking volume's integration does now (estimate: a 1 mm grid has 8 times the voxels per volume, but only the band near the surface needs updating).
- **Plugs into:** `ohs/track/volume_gpu.py` (a sparse 1 mm variant with weights); `ohs/track/markerless.py` (`integrate` takes a weight map); `ohs/app/pipeline.py` (passes σ_z); mesh extraction with σ per vertex.
- **Effort / risk:** M–L / low–medium.
- **Result (2026-11-09):** the GPU volume and the per-vertex σ shipped (`ohs/track/recon_tsdf.py`: hashed 8³ bricks, one work-item per voxel, about 20–28 ms a frame at full resolution). The 1/σ² weights did not: on simulated scans they raised the median mesh error by 15–25 % and left more stray surface, and a gentler weight gained nothing. Fusion averages each frame equally, like Open3D, and σ only measures. Details: `VALIDATION.md`, "Depth confidence".
- **Sim check:** mesh median, p95 and signed mean per scene against the analytic surfaces, before and after; per-vertex σ against the actual error (about 95 % within 2σ); a CPU–GPU agreement test.

#### 4.5.2 Thin sheet metal — P2, M4
- **What:** a TSDF can't hold two surfaces closer together than its truncation band (4 mm at 1 mm voxels). Scanning both sides of a 1 mm panel or bracket in one pass makes the two sides erase each other. Options:
  1. scan each side as its own scan, and align them (M4). This is the simplest, and exactly what a thickness measurement needs;
  2. a directional TSDF that keeps separate values per surface orientation (Splietker and Behnke 2019);
  3. a narrower band where σ_z allows it.
- **Benefit:** brackets, body panels and trim are sheet metal or thin plastic.
- **Licence:** own code.
- **Plugs into:** app guidance ("scan each side separately"), 4.7 for merging; option 2 as research in `volume_gpu.py`.
- **Sim check:** a 1–2 mm panel seen from both sides; the recovered thickness.

#### 4.5.3 Surfels — P3
A point-based model with a radius, normal and confidence per point (ElasticFusion; SurfelMeshing, Schöps et al. 2018, BSD-3; BAD SLAM). It has no voxel limit and natural per-point confidence. But meshing gets harder, and everything in `ohs` and the app is built on TSDFs and meshes. Not worth a switch. Keep the TSDF for the surface, and the laser cloud as a separate, more accurate layer, as today.

#### 4.5.4 Neural fields and Gaussian splatting for metrology — ✗ for geometry
- **Accuracy:** the best surface-focused methods (Neuralangelo, 2DGS, PGSR, Gaussian Opacity Fields) report mean Chamfer distances of roughly 0.5–0.8 mm on the DTU benchmark, from many calibrated photos. This scanner measures 0.07–0.3 mm directly. A 2026 comparative study of NeRF and Gaussian-splatting geometry scores them at 2 mm and 5 mm thresholds. A 2025 metrological review of learning-based methods on difficult surfaces (*Metrology*, 2025) is the place to see where they stand.
- **Fit to this hardware:** they optimise appearance, and get shape only as far as appearance demands. A monochrome camera that mostly sees its own dot pattern gives them little to work with.
- **Cost:** minutes to hours of GPU training per scene, mostly CUDA-only.
- **Licences:** the Inria Gaussian-splatting code and its derivatives (2DGS and many more) and Neuralangelo are non-commercial. gsplat (Apache-2.0) is an exception.
- **Verdict:** no. Revisit only for visual texture, if a colour camera is ever added.

#### 4.5.5 Other sparse volumes — P3
VDBFusion (MIT, CPU, OpenVDB) and nvblox (CUDA-only) exist. Our own OpenCL kernel (4.5.1) fits the any-GPU rule better.

### 4.6 Mesh post-processing

**Today**, in the working tree (`ohs/mesh/`):
- floater removal;
- Liepa hole filling (dihedral-angle triangulation, refinement, bi-Laplacian fairing);
- Taubin smoothing, and bilateral normal filtering (Zheng et al. 2011) that keeps sharp edges;
- batched quadric decimation (Garland and Heckbert 1997) with border quadrics;
- topology helpers and statistics.

Meshes come from marching cubes on the TSDF, or from Open3D's screened Poisson. That covers most of the classical toolbox. The additions below are about not damaging measurements.

#### 4.6.1 Decimate to a tolerance — P1, M3 (quick win)
- **What:** stop collapsing edges once the quadric error (or a check against the input mesh) passes a tolerance in mm, say 0.01–0.05 mm. Report the largest deviation actually introduced.
- **Benefit:** a measuring user wants "a smaller file, and the surface moved less than 0.02 mm", not "200k triangles".
- **Evidence:** QEM's error is a squared distance to planes, so a bound in mm is natural.
- **Licence / cost:** own code; free (the quadric is already computed). If the numpy decimator is ever too slow, fast-simplification and meshoptimizer (both MIT) are fast C++ alternatives.
- **Plugs into:** `ohs/mesh/simplify.py` (`decimate(..., max_error_mm=...)`).
- **Effort / risk:** S / low.
- **Sim check:** decimate the sphere and panel meshes; measure the distance to the original mesh and to the analytic surface.
- **Result (2026-11-10, shipped):** the quadric's mean squared distance (cost / weight) may reach half the tolerance, and neither end of a collapse may move off its surface by more than it. The outer edge and folds are held: straightening a scan's ragged edge or flattening a fin moved old vertices by up to 1 mm. On simulated scans 99.9 % of the old vertices stay within the tolerance and all within about twice it; the edit reports both, measured against the original. At 0.02 mm a 450-frame scan keeps 55 % of its triangles; at 0.05 mm, 29 %.

#### 4.6.2 Flag synthetic geometry — P1, M3 (quick win)
- **What:** faces made by hole filling, Poisson or extrapolation carry a "filled" flag. The viewer shows them; fits and deviation maps leave them out by default; the stats count them.
- **Benefit:** measurements never use invented surface without saying so.
- **Licence / cost:** own code; negligible.
- **Plugs into:** `ohs/mesh/holes.py` (return the new faces); the project files (a face attribute); the M5 tools.
- **Effort / risk:** S / low.
- **Sim check:** fill the car panel's hole; every new face is flagged, and fits on the panel ignore them.
- **Result (2026-11-10, shipped for hole filling):** the flag is the PLY face property `filled`; it follows every edit (`ohs.mesh.carry_filled`), the stats give the filled area, both views draw it striped, and the viewer's distance tool won't pick a point on it. Fits (M5) will use it. Poisson meshes are not flagged yet: the app makes TSDF meshes.

#### 4.6.3 Confidence-aware smoothing — P1, M3 (quick win)
- **What:** scale the smoothing per vertex by its σ (4.5.1) and by laser anchoring. Confident regions move little, noisy ones more.
- **Benefit:** smoothing stops eroding the most accurate data.
- **Licence / cost:** own code; negligible.
- **Plugs into:** `ohs/mesh/smoothing.py` (`smooth(..., weights=...)`).
- **Effort / risk:** S / low.
- **Sim check:** smooth with and without weights; the error against the analytic surface should drop more where σ was large, and not rise where it was small.
- **Result (2026-11-10, shipped as a limit):** no vertex moves more than 2σ (`smooth(..., limit=2 * sigma)`), a bound that keeps smoothing inside each point's error bar. On the simulator it never binds: smoothing moves points by hundredths of a millimetre while σ is about 0.5 mm, and it improved the error of the low-σ half too (car panel, bilateral 50 %: median 0.054 → 0.042 mm). It matters where σ is small: long, laser-refined scans.

#### 4.6.4 Screened Poisson with an envelope — P2, M3–M4
- **What:** screened Poisson (Kazhdan and Hoppe, TOG 2013) builds a watertight surface from oriented points. The envelope-constrained version (Kazhdan et al., SGP 2020) keeps it from ballooning across big openings. Both are in PoissonRecon (MIT). Open3D wraps an older version without the envelope.
- **Benefit:** watertight models for 3D printing (brackets, knobs, clips), with controlled filling.
- **Licence:** MIT. Build or ship PoissonRecon's command-line tool; or derive the envelope from the TSDF's observed free space and keep Open3D's version.
- **Cost:** seconds to a minute on the CPU for millions of points (estimate).
- **Plugs into:** `poisson_mesh` in `ohs/track/export.py`. The new regions get the 4.6.2 flag.
- **Effort / risk:** S–M / low.
- **Sim check:** watertightness, the distance to the analytic surfaces, and the flagged area.

#### 4.6.5 Isotropic remeshing with feature lines — P2, M3–M5
- **What:** Botsch and Kobbelt (2004): split long edges, collapse short ones, flip toward even valence, relax tangentially, and project back to the input. Feature edges (from `sharp_edges`) stay fixed, or are remeshed along themselves.
- **Benefit:** even triangles help every later step (smoothing, fitting, CAD export, printing). Marching cubes makes many slivers.
- **Licence:** the pmp-library (MIT, C++) has a clean implementation to follow. gpytoolbox's `remesh_botsch` is in its MIT part (its `copyleft` module is GPL; avoid that part). A numpy version fits the style of `ohs.mesh`. For quad meshes, useful before B-spline fitting, Instant Meshes and QuadriFlow are under permissive BSD-style licences.
- **Cost:** seconds per million triangles (estimate).
- **Plugs into:** a new `ohs/mesh/remesh.py`.
- **Effort / risk:** M / low.
- **Sim check:** triangle quality (minimum angle, aspect ratio); the distance to the input under the tolerance; the bracket's edge angles kept.

#### 4.6.6 Sharp edges on mechanical parts — P2, M5
- **What:** a TSDF rounds corners (voxel size, depth smoothing, marching cubes). Two fixes:
  1. **Primitive snapping:** after M5 fits planes and cylinders (4.8), move vertices within a band of an intersection onto the fitted surfaces and their intersection lines, and flag them "snapped";
  2. **Dual contouring** (Ju et al. 2002) from the TSDF's gradients, which places vertices on sharp features. Learned variants (Neural Marching Cubes, Neural Dual Contouring) need training data and aren't needed.
  
  Do 1 first: it needs no new extraction, and its result is explicit. 2 needs clean normals, which a noisy TSDF doesn't give.
- **Licence:** own code.
- **Plugs into:** a new `ohs/mesh/snap.py`, using the M5 fits.
- **Effort / risk:** M / medium.
- **Sim check:** the bracket scene: edge angles and corner positions against the truth, before and after.

#### 4.6.7 Stronger feature-preserving denoising — P3
Guided normal filtering (Zhang et al. 2015) and L0 denoising (He and Schaefer 2013) keep sharper edges than bilateral normal filtering on CAD-like parts; RIMLS (Öztireli et al. 2009) does the same on points. Add one only if the bracket tests show bilateral filtering rounding edges.

**Not usable:** PyMeshLab and MeshLab, CGAL's repair and remeshing packages, MeshFix and pymeshfix (GPL: inspiration only). Learned shape completion for big holes invents geometry; it is not for measurement, at most a flagged cosmetic option.

### 4.7 Multi-scan alignment and merging (M4, top 10 #6)

**Today:** not started. The app plan asks for N-way automatic alignment, tags, manual point pairs, residual maps and merging.

#### 4.7.1 Coarse alignment — P1, M4
- **What:** FPFH features (Rusu et al., ICRA 2009) on downsampled clouds of each scan; FGR (Zhou, Park and Koltun, ECCV 2016) solves the pose robustly; TEASER++ when the overlap is small or the part is symmetric. ICP overlap checks every candidate. Shared tags go first (Kabsch on the tag corners, as the marker map does). Manual: three point pairs, Kabsch, then ICP.
- **Benefit:** no "two models at a time, three marker pairs" limits.
- **Evidence:** the standard pipeline; Open3D ships it.
- **Licence:** Open3D, TEASER++ and KISS-Matcher are MIT.
- **Cost:** seconds per pair on the CPU (estimate).
- **Plugs into:** a new `ohs/align/` package (global, pairwise, multi-way); jobs in `ohs/server/jobs.py`. It shares the global step with 4.4.1.
- **Effort / risk:** M / low–medium.

#### 4.7.2 Fine alignment — P1, M4
Point-to-plane ICP with a robust kernel, or Generalized ICP (Segal et al., RSS 2009), both in Open3D; small_gicp (MIT) if speed matters. Use stable sampling (4.3.1) here too: two scans of a panel overlap mostly on flat areas.

#### 4.7.3 One pose graph over all scans — P1, M4
Every pair with enough overlap is an edge, with the point-to-plane information matrix that `posegraph.py` already computes. Optimise all scan poses with Open3D's global optimisation and line process (Choi et al. 2015), the same machinery as Straighten. Lu and Milios (1997) and Pulli (1999) are the classic formulations. The benefit: no "two at a time" merging, and errors spread evenly instead of piling up at the last scan.

#### 4.7.4 Merge from the raw recordings — P1, M4
- **What:** once the scan poses are known, every frame of every scan has a world pose. Fuse them all into one TSDF with the 4.5.1 weights, merge the laser clouds, then mesh. No mesh-to-mesh stitching.
- **Benefit:** no seams; overlaps averaged by their weights; one consistent confidence map. This works only because every scan keeps its raw recording, a structural advantage over apps that keep only meshes.
- **Cost:** about the cost of processing every scan again.

#### 4.7.5 Residuals the user can read — P1, M4
- Per pair: the RMS distance along the normals over the overlap, the overlap fraction, the inlier ratio.
- Per scan: the pose covariance from the inverse information matrix (Censi, ICRA 2007, gives a closed form for ICP), and its weakest direction.
- A residual heat map on the merged model, and TEASER++'s certificate when it was used.
- A plain verdict, for example: "aligned to 0.04 mm RMS; scan 3 can slide 0.3 mm along the door: add overlap or tags".

**Sim check for 4.7:** two to five scans of the same scene from different seeds and sides, with known true poses. Measure the pose error, the residuals against the truth, and whether the reported covariance covers the actual error. Add a symmetric scene to test the warnings.

**Not needed:** learned registration (GeoTransformer, PREDATOR) trained on indoor RGB-D data. The gap to painted car parts is large, and FPFH with TEASER++ is enough for scans of one object with decent overlap.

### 4.8 Reverse engineering (M5, top 10 #7)

**Today:** the mesh topology helpers (boundary loops, outer borders) exist. Nothing else for M5 yet.

#### 4.8.1 Plane detection — P1, M5
Open3D's `detect_planar_patches` (Araújo and Oliveira, Pattern Recognition 2020) finds many planes robustly; `segment_plane` is single-plane RANSAC. Refine each plane with a weighted total-least-squares fit, and report flatness (maximum and RMS deviation).

#### 4.8.2 Cylinders, spheres, cones and tori: Efficient RANSAC — P1, M5
- **What:** Schnabel, Wahl and Klein (CGF 2007) sample minimal point sets locally (in octree cells), score candidates using normals, and extract shapes greedily. The method is robust to noise and outliers. Add region growing over the mesh for clean boundaries.
- **Benefit:** the shapes car work needs: mounting faces, bores, bosses, pipe ends, fillets.
- **Evidence:** mature, and the reference method for this job.
- **Licence:** the best-known implementation (CGAL's Shape Detection) is GPL, so implement from the paper. pyRANSAC-3D (Apache-2.0) is a simple starting point for single shapes.
- **Cost:** seconds on a selected region (CPU, estimate).
- **Plugs into:** a new `ohs/measure/` package.
- **Effort / risk:** M / low.

#### 4.8.3 Fits with uncertainty — P1, M5
- **What:** refine each shape by Gauss-Newton on true geometric distances, weighted by the per-point σ (4.5.1). The inverse normal matrix gives the parameter covariance: radius ± σ, axis direction ± angle, plane flatness. Report the residual RMS and the number of points used. Leave out "filled" faces (4.6.2).
- **Benefit:** "hole Ø 8.02 ± 0.03 mm" instead of "8.02 mm".
- **Caveat:** these are statistical errors. Systematic ones (calibration, scale) come on top; the artefact test in 4.9.6 measures those.
- **Sim check (4.8.1–4.8.4):** the bracket scene, plus new ones with known holes, cylinders and fillets. Radius, diameter, axis and position errors against the truth, and whether ±2σ covers the truth about 95 % of the time.

#### 4.8.4 Hole finder — P1, M5
- **What:** take the boundary loops that are holes, not outer borders (as `ohs/mesh/topology.py` defines them). Fit a circle in the plane of the face around each one, or a cylinder to the hole's wall when the wall was scanned. Report the diameter, position and axis, with uncertainty. Slots: two half-circles and two lines.
- **Caveat:** fusion rounds and shrinks small holes (the edge is smoothed over about a voxel, plus the depth filter). Prefer the wall cylinder when there is one, and measure the bias on known holes in the simulator.

#### 4.8.5 Deviation against a reference — P1, M5
- **What:** align the scan to a reference (STL or STEP) with a robust best fit, or by datums (3-2-1, plane-line-point). Compute signed distances (Open3D's `RaycastingScene.compute_signed_distance`). Show a colour map, a histogram, the extremes, the share within tolerance, and per-feature checks (flatness, cylindricity). For comparing two scans, M3C2 (Lague et al. 2013) is a good model: a distance that comes with its own local uncertainty.
- **The verdict** per point or region: pass, fail, or **inconclusive** when the deviation is within k·σ of the tolerance limit.
- **Licence:** Open3D MIT; own code. CloudCompare (GPL) for inspiration only.
- **Sim check:** compare a simulated scan with its own analytic model, then with a deliberately bent copy; the colour map and verdicts must match the known bend.

#### 4.8.6 STEP export of fitted shapes — P2, M5
Build planes, cylinders, holes and a coordinate system with OpenCascade (LGPL-2.1 with an exception) through OCP or CadQuery (Apache-2.0 wrappers), and write STEP. Dynamic linking keeps the MIT project compatible; ship OpenCascade's licence text. It is large, so check the installer size. Another option: a small STEP writer for analytic shapes only.

#### 4.8.7 Freeform panels: B-spline surfaces — P3, after M5
Fit a B-spline surface to a selected four-sided panel region (geomdl, MIT, or OpenCascade's point-to-B-spline fitting). It is doable for single clean regions. Whole panels with trims and character lines are hard.

#### 4.8.8 Learned CAD reconstruction (Point2CAD) — P3, research
Point2CAD (CVPR 2024, Apache-2.0 code) segments a point cloud into surfaces with a pretrained network (from ParSeNet or HPNet), fits primitives and freeform patches, and rebuilds edges and corners. It is trained on clean CAD models (the ABC dataset); scanned car parts with noise, holes and freeform panels are far from that. The two segmentation repositories did not answer at the GitHub addresses tried (Hippogriff/parsenet-codebase, SimingYan/HPNet). Watch it; don't build on it.

**Will not work in M5:** fully automatic CAD of a whole freeform panel (an open research problem); PolyFit and similar polygonal reconstruction (GPL, and aimed at buildings).

### 4.9 Calibration over time (top 10 #8)

**Today:** the wizard (stereo; lasers from the white margin; IMU); a launch-time row-error check (ORB matches refined by Lucas-Kanade; it flags more than 0.6 px). Nothing runs online, and there is no temperature data.

#### 4.9.1 The calibration triangle — P1–P2, M5
- **What:** on every scan, compare three depths at the same pixels:
  - (a) dense stereo depth from the dots;
  - (b) laser depth from the calibrated plane;
  - (c) laser depth from stereo triangulation of the vertical line (4.2.6).

  Each pair isolates a different fault:
  - (b) against (c): the laser plane moved (same pixels; only (b) uses the plane);
  - (a) against (c): a bias in stereo matching or in the disparity (same geometry, different features);
  - all three shifting together with distance: a scale error (baseline or focal length) that none of them can see alone. That needs a known length (4.9.6).
- **Benefit:** a calibration health report on every scan, with no board: "the laser planes moved 0.05° since calibration; refitted from this scan", or "stereo looks fine".
- **Evidence:** cross-checking redundant measurements is basic metrology, and the model here is simple.
- **Licence:** own code.
- **Cost:** a few thousand lookups per frame; negligible.
- **Plugs into:** a new `ohs/calib/online.py`, fed by `FramePipeline`, summarised in `report.json`, and shown in the app.
- **Effort / risk:** M / medium.
- **Sim check:** record scans with a perturbed true calibration (a laser plane tilted 0.05–0.2° or shifted 0.2 mm; the baseline 0.1 % longer; a 0.02° relative rotation). Check that each fault is detected and correctly attributed, and that clean scans raise no alarm.

#### 4.9.2 Row error watched during scans — P2, M5
The launch check measures vertical disparity on ORB features of idle frames. During a scan, the dot centres (4.1.3) or the SGM matches give thousands of correspondences per frame. Track their median vertical disparity across the image, to see pitch or roll changes between the cameras as they happen (warm-up, a knock). Then, optionally, re-estimate that small rotation online (Dang et al., TIP 2009; Hansen et al., CVPR 2012; Ling and Shen, IROS 2016). Yaw and baseline changes don't show up in row error. They change the depth scale, which 4.9.6 checks.

#### 4.9.3 Temperature — P2, M4–M6
- **What:**
  1. read the LSM6DSOX's built-in temperature sensor in the firmware, and log it with the IMU stream (one more field, or a periodic line). It is almost free; start with the first hardware recordings;
  2. show a warm-up state ("warming up: best accuracy in N minutes") once real data shows how long the drift lasts;
  3. learn temperature-indexed corrections (row error, laser planes, depth scale) from the per-scan checks of 4.9.1 and 4.9.2;
  4. if the IMU on the back cover proves a poor proxy, add two NTC thermistors on Pico ADC pins, at the camera board and at the laser block.
- **Why:** mounts expand, lenses shift and laser modules tilt as they warm. For option A, the laser's wavelength shifts too (4.1.6).
- **Licence / cost:** own code; negligible.
- **Plugs into:** `firmware/main.py` and `firmware/lsm6dsox.py`; `ohs/devices/pico.py`; the recording format (a temperature column or file); `ohs/calib/online.py`.
- **Effort / risk:** S for the logging, M for the models / low.
- **Sim check:** step 7 of 6.2 (calibration drift as a function of temperature); the corrections must remove the injected drift.

#### 4.9.4 Online laser-plane refit — P2, M5
Refit each plane from the laser pixels whose 3D position is known from dense stereo (both lines) or from stereo triangulation (the vertical line): a weighted plane fit with RANSAC over many frames. Apply it when 4.9.1 says the plane moved, and say so. Never silently.

#### 4.9.5 Depth self-calibration from fused models — P3
CLAMS (Teichman et al., RSS 2013) and Zhou and Koltun (CVPR 2014) learn per-pixel depth corrections by comparing frames with the fused model. Here the dot-scale error moves with depth: the projector sits 52 mm from the left camera, so dots slide along the image by several pixels per centimetre of depth. It doesn't fit a per-pixel table. Only a slowly varying part would (lens-model residuals). Try it only if real data shows a stable low-frequency bias.

#### 4.9.6 An accuracy check anyone can run — P2, M5–M6
- **What:** VDI/VDE 2634 Part 3 (2008) is the acceptance test for area-scanning systems that move between views, which covers handheld scanners. It checks the probing error (form and size, on a sphere), the sphere-spacing error (on a ball bar) and the length error. Part 2, for single views, also has a flatness test. Build a reduced version into the app:
  - a ball bar: two precision steel balls (bearing balls, whose sizes are held to microns) glued to a rod, with the centre distance measured by calipers;
  - a flat reference: a ground or lapped plate (float glass for coarse checks);
  - the app scans them, fits the spheres and the plane (4.8.3), and reports the sphere-spacing, sphere form and size, and flatness errors, with a history over time.
- **Benefit:** "accuracy you can see" becomes a number that can be compared with vendors who quote VDI/VDE 2634. It also catches the scale errors that 4.9.1 can't see. An independent ISPRS study (2022) tested handheld scanners this way on dumbbell artefacts: that is the comparison to make about ourselves.
- **Honest limit:** calipers measure the ball bar to about ±0.02–0.03 mm, which bounds how well scale can be checked at home.
- **Licence:** own code.
- **Cost:** a short scan and a few fits.
- **Plugs into:** a check flow in the app (like the M2 wizard); artefact definitions and the test in `ohs/measure/`; fitting from 4.8.3.
- **Effort / risk:** M / low.
- **Sim check:** a ball-bar and sphere scene with known geometry; the test values from simulated scans; then the same artefacts on hardware.

**Will not work:** recovering the baseline or focal length from the scene alone. Scale is unobservable without a known length.

### 4.10 Difficult surfaces: black, glossy and metallic paint (top 10 #10)

**Today:** the dots give texture to plain panels, and the app plan expects the lasers to handle black and glossy paint better than the dots. Exposure is manual (keys in the preview), and emitter levels are fixed per scan. AESUB Blue spray is in the parts list. The simulator has matte surfaces only.

#### 4.10.1 Closed-loop emitter power and exposure — P1–P2, M4
- **What:** per frame, measure the laser peak (`strength`), the share of saturated line pixels and the dot contrast. Adjust the emitters' PWM through the Pico first (one serial command, `EMIT v h d`), and the camera exposure second (UVC exposure changes can take several frames, and DirectShow's steps are coarse). Keep the line peaks under saturation on white primer, and above the noise on black paint.
- **Benefit:** one setting for a white panel and a black bumper in the same scan.
- **Evidence:** adapting exposure and projector intensity is standard in structured-light HDR work (Feng et al. 2018 review).
- **Licence:** own code.
- **Cost:** negligible.
- **Plugs into:** `ohs/app/live.py` and `ohs/server/session.py` (the control loop); `ohs/devices/pico.py`. Record the settings as events, so processing knows them per frame.
- **Effort / risk:** M / medium (UVC latency; the emitter changes must never disturb tracking).
- **Sim check:** the renderer already models emitter power and saturation. Add a scene with black (albedo 0.03–0.05) and white (0.9) regions, and test the loop with a simulated latency.

#### 4.10.2 Two power levels, merged by fusion — P2, M4–M6
- **What:** alternate two emitter power levels frame by frame (or every few frames). Each frame keeps only its well-exposed pixels, and the TSDF averages them with their weights (4.5.1). Classic HDR merges images of a still scene (Zhang and Yau 2009; Feng et al. 2018). A handheld scanner can't do that, but fusion in the world frame can.
- **Cost:** half the samples per level; fine at 60 fps for slow scanning.
- **Risk:** without a camera sync signal, power changes must be detected in the image (dot brightness), not assumed.
- **Sim check:** the black-and-white scene of 4.10.1; coverage and error on both regions, against a single power level.

#### 4.10.3 Cross-polarisation — P2, hardware option, M6
- **What:** laser light is linearly polarised. Mirror-like reflection off clearcoat keeps the polarisation; light scattered by the paint underneath mostly loses it. A linear polariser film on each camera, turned 90° to the lasers' polarisation, removes most of the glare and passes about half of the scattered light. Option B's LED needs a polariser film on the projector too. All lasers must share one polarisation direction (turn the modules in their holders). Multi-polarisation fringe projection (Salahieh et al. 2014) and a 2026 polarisation-based stripe separation method use the same physics.
- **Cost:** half or more of the diffuse signal (more exposure or power); a few dollars of film and a printed holder.
- **Honest limits:** metallic flake paint and bare metal keep part of their polarisation, so the gain there is smaller. Chrome stays a spray job.
- **Validation:** a hardware A/B test on real panels. The simulator can't model polarisation without a large upgrade.

#### 4.10.4 Black paint and the dot matcher — P2
Use an adaptive census threshold from the local noise level (the GPU matcher's notes say a fixed threshold loses dim dots on dark panels), plus more exposure or dot power from 4.10.1.

#### 4.10.5 Speckle
It sets the laser noise floor (section 3). What helps: motion (the fused average over many frames), a larger lens aperture (less speckle, but less depth of field), a shorter wavelength (blue, hardware), and option B's LED (no speckle at all). Make the simulator's speckle physical (6.2) before comparing option A and option B on accuracy.

#### 4.10.6 Say what's wrong, and what to do — P1, M4
Detect and name the case from the same statistics as 4.10.1: "surface too dark", "glare on the left half", "laser saturating". Suggest the fixes in order: move closer, change the angle, a power and exposure preset, tags, then spray.

**Will not work here:**
- polarisation cameras and shape from polarisation: the OV9281 has no polarisation filter array;
- phase-shifting or Gray-code structured light: needs a programmable projector and a still scanner;
- epipolar imaging: needs synchronised scanning hardware.

## 5. What will not work, in one place

| Idea | Why not here | Instead |
|---|---|---|
| Learned stereo in the live loop | Seconds per frame on an integrated GPU; a second GPU stack | Census SGM plus confidence (4.1.1); learned stereo only offline, later (4.1.4) |
| FoundationStereo, MonSter, Stereo Anywhere | Research-only licences, weights included | RAFT-Stereo, CREStereo or IGEV class, retrained on our simulator |
| Monocular depth models (Depth Anything etc.) as depth | Relative, not metric | Stereo, lasers, dots |
| Classical spacetime stereo | The pattern moves with the cameras, so a pixel sees different points over time | Weighted fusion (4.5.1) and world-space spacetime stereo (7.2) |
| Speckle tracking with the main cameras | At 60 fps and hand speed the speckle decorrelates completely between frames, and its grain is smaller than a pixel | A dedicated speckle sensor, later (7.12) |
| Photometric terms between frames (in bundle adjustment) | The light moves with the camera, so a surface point's brightness changes from frame to frame | Left-right terms within each frame (7.2) |
| Visual odometry (DROID-SLAM, DPVO, features, photometric terms) | The camera-fixed dot pattern dominates the images | ICP with stable sampling, and the IMU (4.3) |
| Geometry-only tracking of truly symmetric parts | Unobservable by definition | The gyro, tags, the part's small features |
| Non-rigid deformation graphs (ElasticFusion) | They bend the part's shape; the code is non-commercial | A rigid pose graph and re-fusion (4.4) |
| BundleFusion's sparse feature term, and its code | Features lock onto the pattern; CC BY-NC-SA | Dense verification and re-fusion (4.4) |
| NeRF or Gaussian splatting for geometry | 0.5–0.8 mm at best on DTU; slow; mostly non-commercial code | TSDF plus laser and dot layers |
| GPL tools (CGAL's main packages, MeshLab, MeshFix, CloudCompare, FAST-LIO) | Can't ship inside an MIT app | Own code from the papers; MIT and BSD libraries |
| Epipolar or motion-contrast imaging | Needs a line-synchronised projector and rolling shutter | Profile flags and dense agreement (4.2.2, 4.2.3) |
| Polarisation cameras | Not this sensor | Cross-polariser film (4.10.3) |
| Phase shifting or Gray codes | Needs a programmable projector and a still scanner | The fixed dots plus the lasers |
| Self-calibrating the depth scale from the scene | Scale is unobservable without a known length | The ball-bar check (4.9.6) |
| Learned CAD reconstruction of car panels, now | Trained on clean CAD parts; research-grade | Primitive fits with uncertainty (4.8) |

## 6. Validation approach

### 6.1 Rules
- Every algorithm lands with: a simulator test with fixed seeds; a switch to turn it off (for ablations); a CPU–GPU agreement test if it has a kernel; and a line in `VALIDATION.md`.
- **Never worse.** A change ships only if no scan of the robustness set gets worse beyond noise. The set is the one from the GPU work: 3 scenes, extra seeds, 2 resolutions, 15 scans. Report the median and the worst case.
- Report bias (the signed mean) separately from spread, and p95 for meshes.
- **Uncertainty must be calibrated.** Wherever the plan reports a σ, check that about 95 % of the true errors fall inside 2σ.
- **Speed.** Run `ohs bench` before and after. M3 items must keep the M3 target: a 2-minute scan meshed at full quality in under 2 minutes on a laptop GPU.
- **Tuned on the simulator means re-checked on hardware.** `VALIDATION.md` already warns about this; every parameter tuned here goes on that list.

### 6.2 Simulator upgrades, in order of need
1. **Glossy clearcoat:** a matte base plus a specular lobe with Fresnel, and one-bounce mirror images of the laser lines and dots on nearby surfaces. Needed by 4.2.2, 4.2.3 and 4.10.
2. **Black and metallic paint:** albedo 0.03–0.05, and flake sparkle (random bright micro-facets).
3. **Physical speckle** for coherent light (the lines and option A's dots): independent per camera, with a grain size set by the lens aperture, partly averaged by the pixel, and changing as the surface moves. Needed to compare option A and option B fairly, and to test 4.1.3.
4. **MJPEG:** encode and decode each frame as JPEG at the camera's quality. The real stream is MJPEG.
5. **Motion blur** from the exposure time and the hand speed.
6. **New scenes:** a 1–2 mm sheet visible from both sides; a bracket with holes, fillets and slots of known size; a ball bar and spheres. For 4.5.2, 4.8 and 4.9.6.
7. **Calibration drift inside a recording:** plane tilt, baseline change and relative rotation as functions of time or temperature. For 4.9.
8. **IMU errors:** scale factor, axis misalignment, temperature-dependent bias. For 4.3.4.
9. **Long scans with loops and tracking losses** (the orbit and sweep trajectories exist now). For 4.3.5 and 4.4.
10. **A triangle-mesh object** (ray casting through Open3D's `RaycastingScene`), so a scanned shape can be re-scanned in simulation. For the digital twin (7.4).
11. **Car-body scenes:** two panels with a seam of known gap and flush; a panel with dents of known depth; a mirrored part with a deliberate bend. For 7.6–7.8.

### 6.3 Metrics

| Area | Metrics, against the simulator's truth |
|---|---|
| Depth | absolute error median and p95; share above 2 mm; bias; σ calibration |
| Dots | per-dot error by distance and incidence; share of dots found |
| Lasers | centre error (px); point error (mm); false-point rate |
| Tracking | trajectory error, median and maximum, over 15 seeds; correct weak directions |
| Loops | true and false loops; error before and after; never worse |
| Fusion | mesh distance median, p95 and signed mean; per-vertex σ calibration |
| Mesh edits | deviation introduced (maximum, RMS); edge angles kept; flagged area |
| Multi-scan | pose error; residual RMS; covariance covers the true error |
| Fits | radius, diameter, axis and position errors; σ calibration |
| Calibration | faults detected and correctly attributed; artefact test values |
| Difficult surfaces | coverage; error on black, white and glossy regions |

### 6.4 On the real scanner
- **First recordings:** the calibration board; a flat plate; a ball bar; painted panels (black, white primer, metallic), each sprayed and unsprayed; a bracket with holes measured by calipers.
- Measure the same metrics as in simulation, wherever a reference exists. Write down where real and simulated results differ, and re-tune.
- **Repeatability:** scan the same object 5–10 times, and report the spread of the fitted values.
- **The laser as the reference for dense methods.** Real recordings have no ground truth, but the laser cloud is measured by a different principle (a plane, not dot matching). Wherever the lasers swept, compare each dense method (today's fusion, 4.2.4, 7.2) against the laser cloud. Thanks to tracking, a point the laser swept in one frame checks the dense depth of every other frame that saw it.
- **References:** calipers on the artefacts at minimum; a commercial scan or a CMM measurement if one can be borrowed.

## 7. Innovation track: ideas no scanner ships yet

Sections 2 and 4 make the classical pieces solid. This section is about ideas that use what only this scanner has:
- a dot pattern fixed to the scanner, which slides across the surface as the scanner moves;
- a laser cross whose two lines sweep the surface and cross their own earlier tracks;
- an IMU, and a simulator of the exact hardware;
- every raw frame, kept.

Each idea is a research bet with a cheap first test. Prior art is cited, and where a closely related paper exists, the card says so. Cards use the same fields as section 4.

### 7.1 Ranked

| # | Idea | What it could give | First test | Milestone | Risk |
|---|---|---|---|---|---|
| I1 | **World-space spacetime stereo** (7.2) | 4–6 times lower median error where the scan concentrates, 1.6–1.9 times at the edges (simulation) | Done on the simulator: see 7.2 | M3 phase 2 spike, M4 ship | medium |
| I2 | **Laser-curve bundle adjustment** (7.3) | Poses and laser planes at laser accuracy, self-calibrated on every scan | Simulated scan with injected drift and a tilted plane | M4 | medium |
| I3 | **A digital twin that states each measurement's uncertainty** (7.4) | "Ø 8.02 ± 0.03 mm" that includes calibration and noise, by simulation | Coverage test on simulated parts | M5–M6 | medium |
| I4 | **The projector calibrates itself; dot-free images** (7.5) | Per-dot rays (dot triangulation, one-camera depth, temperature) and images with the dots removed (clean tags, texture) | Fitted rays against the simulator's projector; tag error with and without | M4–M5 | medium–high |
| I5 | **Laser gauges for body work** (7.6) | Live gap & flush along a seam; edges and hole rims at laser accuracy | Simulated panel seam and holes of known size | M5 | medium |
| I6 | **Dent maps and highlight lines** (7.7) | Dents shown only where they exceed the error bar; virtual zebra stripes | Simulated dented panel | M5 | medium |
| I7 | **Symmetry tools** (7.8) | Automatic mirror plane; "is this side bent?" | Mirrored simulated part with a bend | M5 | low |
| I8 | **Re-measure from the raw frames** (7.9) | The best possible estimate for the one feature being measured | Hole diameter: whole-scan result against region re-run | M5 | low |
| I9 | **Honest averaging** (7.10) | Uncertainties that don't shrink faster than the evidence | σ coverage on the simulator | M3 | low |
| I10 | **Guidance by expected information gain** (7.11) | "Aim here, from the left" instead of a coverage colour | User test; simulated operator | M4–M6 | medium |

### 7.2 I1: World-space spacetime stereo — P1 spike in M3, ship in M4 (top 10 #2)
- **What:** after tracking, Straighten and fusion, refine the surface patch by patch against the raw images. A patch is a small piece of the fused surface (about 4 x 4 mm) with its own normal and curvature. For each candidate offset along the normal, project the patch into the left and right rectified images of every frame that saw it, compare the two samplings with zero-mean normalised cross-correlation (ZNCC, which ignores gain differences between the cameras), and add up the cost over all those frames. The best offset moves the vertex. Later, refine the poses the same way: a photometric bundle adjustment whose terms are only left-right *within* each frame, because the light moves with the camera.
- **Why it works, and why only here:** the dots are fixed to the scanner, so as the scanner moves, every frame lights the patch with a different dot arrangement. Per-frame matching makes a slightly different, camera-fixed error in each frame; this estimate never makes those errors in the first place:
  - no square window that assumes the surface faces the camera (the patch has the surface's own slant and curvature);
  - no smoothness penalty between pixels;
  - no pixel locking (the dots land at a different sub-pixel position in every frame).
  
  Classical spacetime stereo needed the pattern to change while the scene stood still. Here the motion that tracking already measures changes the pattern for free.
- **First result.** A numpy prototype (not committed) ran on the simulator's sphere-on-plane scene with three seeds: 24 frames each over 1.2 s of hand-held motion, full resolution, dots and lasers on, and the project's GPU census SGM for the per-frame depth. Errors are along the true normal, in mm, at sample points seen by both cameras in at least 8 frames. "Averaged" is the mean of the per-frame depth at the point over all frames, which is what TSDF fusion does. Each cell gives the range over the three seeds.

  | Points on | Measure | One frame | Averaged | Spacetime | Spacetime, normal 3° off | Spacetime, pose noise |
  |---|---|---|---|---|---|---|
  | Sphere (206–254 points) | median | 0.13–0.18 | 0.044–0.060 | **0.007–0.014** | 0.011–0.017 | 0.008–0.019 |
  | | p95 | 0.52–0.68 | 0.30–1.16 | **0.027–0.072** | 0.038–0.062 | 0.041–0.070 |
  | | off by > 0.2 mm | 34–45 % | 8–19 % | **0 %** | 0–0.4 % | 0 % |
  | Table around it, 50–110 mm out (354–442 points) | median | 0.19–0.22 | 0.039–0.053 | **0.021–0.033** | 0.019–0.036 | 0.019–0.039 |
  | | p95 | 0.59–0.68 | 0.15–0.18 | **0.11–0.17** | 0.10–0.18 | 0.10–0.18 |
  | | off by > 0.2 mm | 47–54 % | 2.5–4.2 % | **0.9–2.5 %** | 0.6–3.6 % | 0.3–3.9 % |

  "Spacetime" uses the true poses and normals; "normal 3° off" tilts every patch's assumed normal by 3°; "pose noise" adds a random 0.1 mm and 0.02° to every frame, about what tracking leaves. What it shows:
  - where the scan concentrates (the sphere, seen in every frame from changing angles), the median error drops 4–6 times and the p95 5–20 times, and no point is off by more than 0.2 mm;
  - at the edge of coverage (the table around the sphere) the median improves 1.6–1.9 times, and the tail is about as long as averaging's. The few failures sit where fewer frames carried dots on the patch (17–21 textured views against 23–24). So the refinement must check its own confidence (textured views, sharpness of the cost minimum) and keep the fused value where it is weak;
  - a wrong normal or tracking-sized pose noise barely matters;
  - two bugs found on the way, both relevant to the real version: a patch must be visible in *both* cameras (the right camera's occlusions matter), and a frame counts only if the patch holds at least one dot (noise alone looks like texture).
  
  Caveats: matte surfaces, simplified speckle, a perfect lens model, no MJPEG; patches with the true curvature; true poses as the baseline case.

- **Evidence and prior art:** spacetime stereo (Zhang, Curless and Seitz 2003; Davis et al. 2005); photometric refinement of multi-view meshes (Vu et al. 2012); photometric bundle adjustment (Delaunoy and Pollefeys 2014); motion-compensated structured light (Weise, Leibe and Van Gool 2007). Closest to this idea: a multi-frame active-stereo bundle adjustment by inverse rendering for an endoscope (2022), and neural reconstructions that render a projected pattern (Shandilya et al. 2023; papers from 2024 and 2025). No open, practical version for a handheld dot-and-laser scanner was found.
- **Licence:** own code.
- **Cost:** the prototype is plain numpy and slow (about 50 s for some 600 patches and 24 frames on the CPU, per variant). On the GPU it is one kernel: per patch and frame, 169 samples in each image. With a few Gauss-Newton steps instead of a grid search, and 30–50 well-spread keyframes per patch, a million patches need about 10¹¹ samples: seconds on the RTX 3070 Ti and tens of seconds on the Radeon 680M (estimate). It can also run only where the confidence map (4.5.1) is weak, or only on a measured feature (7.9).
- **Plugs into:** a "Refine (spacetime)" stage after Straighten and Fuse in `ohs/app/process.py`; a new `ohs/track/refine_wss.py` with an OpenCL kernel and a CPU twin. Visibility in both cameras comes from rendering the fused mesh into each view. Output: moved vertices, and a per-vertex σ from the curvature of the summed cost.
- **Effort / risk:** M–L / medium. Risks:
  - it is still stereo: rectification errors bias it like any matcher;
  - option A's dots carry speckle that differs between the two cameras, which makes the cost noisier (ZNCC copes, but less well);
  - MJPEG artefacts;
  - glossy paint looks different to the two cameras; frames with a poor best match must be down-weighted;
  - pose drift biases it like everything else, so it runs after Straighten.
- **Next tests:** the car panel and the bracket (crease, edges); the realistic lens; physical speckle and MJPEG (6.2); tracked poses instead of true ones; then real recordings, judged against the laser cloud (6.4).
- **Trial build (2026-11-10, `ohs/track/spacetime.py`, the "Refine from the images" stage).** It runs on the fused mesh and tracked poses, with the realistic lens, on the GPU. `VALIDATION.md`, "Refine from the images", has the numbers. What the trial changed from the prototype:
  - **Patch size in pixels:** 7 px half-width at the scan's depth (3.5 mm at 360 mm). On the 640x400 recordings a 4 mm patch is 4 px and the cost curve is flat: the disparity changes 0.13 px per mm there.
  - **The patch shape is a quadric fitted to the mesh within 7 mm.** Fitted on the 2.5 mm the prototype's patch covered, its curvature was noise and made things worse (0.042 against 0.032 mm for a flat patch on the table).
  - **The texture test is done once per frame,** on the fitted surface, not at every offset. A frame dropping in and out across offsets made sharp false minima, the worst patches of all.
  - **The vertices move onto the patches,** not by a smooth field. A field at the patch spacing leaves the fused mesh's per-vertex noise in place: the patches improved, the mesh barely did.
  - **A patch needs 16 textured frames.** With 8–16 it was worse than the fitted surface.
  - **Results with tracked poses:** the median error where it measured fell 1.8–2.8 times on the sphere and about 2 times on the car panel. On the panel the gain is mostly the fit, not the images: its dots have little contrast on the simulated paint, and most cost curves are flat there. At the true poses the sphere improves 4.7 times (0.071 → 0.015 mm), as in the prototype. So the tracking error (0.06–0.13 mm) is now the limit, and the photometric pose refinement above is the next step.
  - **Not answered by the simulator:** real speckle and MJPEG, glossy paint, the bracket's edges, and whether the sigma could be cut where it measures. The verdict for M4: worth shipping as a GPU stage, after a test on real recordings against the laser cloud.
- **Refining the poses, first try (2026-11-10): no gain.** Each frame's pose was solved against the refined patches. Each patch the frame saw with texture gave where that frame alone put the surface: the minimum of its own left-right cost. The fit was point-to-plane, robust, along the measurable directions, then the patches were searched again.
  - **The poses got more consistent, not the shape.** On the sphere scan with tracked poses, the pose error after a best rigid fit halved (0.051 → 0.027 mm, 0.133 → 0.046°). The sideways corrections followed the true errors (correlation 0.8), but the corrections along the view did not (−0.01).
  - **The shape after a rigid fit to the truth stayed at 0.037–0.038 mm,** against 0.019 mm with the true poses. Smoothing the corrections over 3 or 8 frames didn't change that.
  - **The likely reason:** a single frame's left-right offsets carry that frame's camera-fixed matching error, the very error spacetime averages away across frames. A per-frame pose fit puts it back, mostly along the depth axis.
  - **Next, if tried again:** a joint fit of poses and patches, with the tracking's frame-to-frame motion as a prior and only slow corrections allowed, or anchoring to the laser lines (7.3), which carry no camera-fixed dot error.

### 7.3 I2: Laser-curve bundle adjustment, with self-calibration — P2, M4 (top 10 #5)
- **What:** every frame gives two accurate 3D curves, one per laser line. As the scanner moves, curves from different frames cross each other on the surface, thousands of times per scan. At every crossing the two curves must meet. Optimise all frame poses so they do, together with weak dense terms (point-to-plane against the fused surface) and the IMU. Add the laser planes and the camera–IMU time offset as unknowns: a wrong plane or a wrong time offset makes the curves miss each other in a pattern that depends on the viewing geometry, so the scan calibrates them.
- **Why only this scanner:** a laser cross plus dense depth plus an IMU. The cross gives accurate curves; the dense depth and the IMU hold the directions that crossings can't (sliding along a flat panel).
- **Evidence and prior art:**
  - Patrick Hébert's self-referenced hand-held crosshair sensor (3DIM 2001), and the curve-based modelling of Tubic, Hébert and Laurendeau (CVPR 2003; Image and Vision Computing 2004), registered laser curves from a hand-held crosshair sensor to each other. Université Laval's patent on it (US 7,487,063) is listed as expired.
  - Sheehan, Harrison and Newman (IJRR 2012) calibrated a 3D laser by maximising the "crispness" of its point cloud.
  - Combining crossing curves with dense stereo and an IMU in one handheld scanner was not found.
- **Licence:** own code (scipy sparse least squares; GTSAM or Ceres, both BSD, if it grows).
- **Cost:** crossings come from a k-d tree over curve points; a sparse Gauss-Newton over 6 numbers per keyframe plus about 10 calibration numbers. Seconds to a minute per scan on the CPU (estimate).
- **Plugs into:** a new `ohs/track/curve_ba.py`, run after Straighten and before the final fusion. Calibration updates go through `ohs/calib/online.py` (4.9), never silently.
- **Effort / risk:** M–L / medium (crossings near edges are outliers; a flat panel constrains only the normal direction).
- **Sim check:** scans with injected drift, a laser plane tilted by 0.1° and a 2 ms time-offset error; the final pose, plane and offset errors against the truth.

### 7.4 I3: A digital twin that states each measurement's uncertainty — P2, M5–M6 (top 10 #7)
- **What:** for one measurement (a hole diameter, a gap, a flatness), replay the real scan path in the simulator against the scanned shape, many times. Each replay draws the calibration from its uncertainty and adds fresh noise; the same processing and the same fit run each time. The spread, and the offset from the simulated truth, is the uncertainty of *that* measurement on *that* scan. This is the "virtual CMM" principle of ISO/TS 15530-4, with the Monte Carlo method of JCGM 101 (GUM Supplement 1), applied to a handheld scanner.
- **Why only this project:** it already has a detailed, validated simulator of its own hardware, and it keeps the real trajectory. No consumer scanner exposes anything like it.
- **Needs:** a triangle-mesh object in the simulator (Open3D's `RaycastingScene`, MIT, casts rays against a mesh); calibration covariances from the solvers in `ohs/calib/`; rendering limited to the region being measured.
- **Evidence:** the method is standardised for coordinate measuring machines (ISO/TS 15530-4) and for Monte Carlo uncertainty (JCGM 101). Its quality depends entirely on how honest the twin is, so it must be checked against the artefact test (4.9.6).
- **Licence:** own code.
- **Cost:** heavy and offline. About 20 replays of 50 frames is 1,000 renders: minutes to tens of minutes on the CPU (estimate). A "certify this measurement" button, not a default.
- **Plugs into:** `ohs/sim/scene.py` (a mesh object), a new `ohs/measure/uncertainty.py`, the measurement report.
- **Effort / risk:** M–L / medium.
- **Sim check:** on simulated parts with known truth, the twin's 95 % interval must contain the actual error about 95 % of the time.

### 7.5 I4: The projector calibrates itself; dot-free images — P2, M4–M5
- **What:**
  - *Dot rays.* Every projected dot travels along a fixed ray in the scanner's frame. Stereo-triangulated dot centres (4.1.3) from many frames, at many depths, line up along those rays. Fit one ray per dot during the calibration session or any scan. For option A, also fit one wavelength-scale number per scan, which tracks the laser's temperature. With the rays known, each dot gives depth from one camera alone (where the other camera is blocked), and a three-way check (left, right, projector).
  - *Dot-free images.* With each dot's centre and brightness fitted, subtract the modelled dots (and the modelled laser lines) from the image, the way crowded-field star photometry fits and removes overlapping star images (DAOPHOT, 1987). What remains is the surface under ambient light.
- **Benefit:** marker mode loses accuracy because dots and lines degrade the tag corners (VALIDATION.md, finding 5: 0.3–0.55 px reprojection error). Dot-free images should fix that, give the mesh a grey-level texture, and give natural-texture features in engine bays and interiors.
- **Evidence:** fitting rays to many points is the same geometry as the existing laser-plane fit. Subtracting a fitted pattern is routine in astronomy; using it to clean active-stereo images was not found.
- **Licence:** own code.
- **Cost:** small per frame once the dots are fitted (4.1.3).
- **Plugs into:** `ohs/vision/dots.py`, a new `ohs/calib/projector.py`, `ohs/track/markers.py` (tags on dot-free images).
- **Effort / risk:** M / medium. Single dot centres are noisy per frame (4.1.3), but a ray fit and the pattern removal pool thousands of observations.
- **Sim check:** fitted rays against the simulator's projector model; tag reprojection error and marker-mode drift with and without dot removal (the sphere scene has tags).

### 7.6 I5: Laser gauges for body work — P2, M5 (top 10 #9)
- **What:**
  - *Gap and flush, live.* When a laser line crosses the gap between two panels, its profile shows two edges. Fit each side, then compute gap and flush per frame: the light-section method of industrial gauges such as Hexagon's handheld CALIPRI. Accumulate along the seam as the user sweeps, with live values and a plot along the seam.
  - *Edges and hole rims.* Where a line breaks at an edge or a hole, its end points trace the edge at laser accuracy. Fit circles to rim points for hole diameters, instead of measuring the fusion-rounded mesh.
- **Benefit:** fusion rounds edges; laser profiles don't. Gap & flush is an everyday body-shop measurement, and the app plan already lists it.
- **Evidence:** the light-section method is the industrial standard. A 2022 robot-mounted system with a UV line laser reported expanded uncertainties of 0.22 mm for gap and 0.18 mm for flush (Kholkhujaev et al., IEEE Access). No consumer scanner app was found that offers it live.
- **Licence:** own code.
- **Cost:** small; per-profile fits on the CPU.
- **Plugs into:** `ohs/vision/laser.py` (segments and their ends exist), a new `ohs/measure/gapflush.py`, a live overlay in the app.
- **Effort / risk:** M / medium (line width and edge radius bias the edge points; shops define "gap" differently).
- **Sim check:** a simulated seam between two panels and holes of known size; gap, flush and diameter errors, then the bias correction.

### 7.7 I6: Dent maps and highlight lines — P2, M5
- **What:** fit a smooth "fair" reference surface to a panel region (a stiff B-spline or thin-plate fit) and show the deviation as a dent map, only where it exceeds k·σ. Render virtual highlight lines (zebra stripes) on the scanned mesh, the way surface quality is judged in car design (Beier and Chen 1994). Later, specular glints on clearcoat could serve as exact normal samples, a deflectometry-like cue (Knauer, Kaminski and Häusler 2004).
- **Benefit:** "where are the dents, and how deep?" is a real question for restorers and body shops.
- **Needs:** low noise. Dents of 0.1–0.3 mm over 10–50 mm need the accuracy of I1 and laser anchoring.
- **Licence / cost:** own code; seconds.
- **Plugs into:** `ohs/measure/`, a viewer overlay.
- **Effort / risk:** M / medium.
- **Sim check:** a simulated panel with dents of known depth; detection rate and false alarms against depth and σ.

### 7.8 I7: Symmetry tools — P2, M5
- **What:** find a part's (or a car's) symmetry plane automatically (Mitra, Guibas and Pauly 2006), compare the two sides ("is this side bent?"), and mirror one side to fill hidden areas, flagged as synthetic (4.6.2).
- **Benefit:** left and right parts are the norm on cars. The app plan has a manual mirror; this adds detection and comparison.
- **Licence / cost:** own code; seconds.
- **Effort / risk:** M / low.
- **Sim check:** a mirrored simulated part with a deliberate bend; the plane error, and whether the bend is found.

### 7.9 I8: Re-measure from the raw frames — P2, M5
- **What:** when the user measures a feature, re-run the most accurate path (I1, the laser edges of I5, finer voxels) on only the frames that saw it.
- **Benefit:** the best estimate for the feature that matters, at a cost proportional to its size. Apps that keep only meshes can't do this.
- **Licence / cost:** own code; seconds to a minute per feature.
- **Effort / risk:** M / low.
- **Sim check:** a hole diameter from the whole-scan mesh against the region re-run.

### 7.10 I9: Honest averaging — P1, M3 (with top 10 #1)
- **What:** nearby frames see nearly the same dots at a surface point, so their errors are correlated (VALIDATION.md measured 0.4 between frames 0.1 s apart). Plain averaging over-trusts them. Count only independent looks per voxel (frames whose dots have moved on by about a dot spacing at that point), and give each voxel a robust inlier/outlier model (Vogiatzis and Hernández 2011).
- **Benefit:** the per-vertex σ means what it says, and every "±" in the app depends on it.
- **Licence / cost:** own code; a few extra numbers per voxel.
- **Effort / risk:** S–M / low.
- **Sim check:** σ coverage (about 95 % within 2σ) with and without the correction.
- **Result (2026-11-09, shipped with 4.5.1):** a voxel counts a new look once its pixel has moved by the dot spacing since the last one. A scanner held still keeps one look, and 2σ covers the true error for 94.9–100 % of the vertices on simulated scans. The robust inlier/outlier model is not done.

### 7.11 I10: Guidance by expected information gain — P3, M4–M6
- **What:** per region, combine the uncertainty, the spread of viewing angles and the laser coverage, and show where to aim next, and from which side, to reduce the uncertainty most: the information-gain view planning of Isler et al. (2016), made for a person instead of a robot.
- **Benefit:** the live screen tells the user what to do, not just what is covered.
- **Licence / cost:** own code; light per frame.
- **Effort / risk:** M / medium (the UI must stay calm).
- **Sim check:** a simulated operator that follows the hints against one that sweeps at random; σ reached per minute of scanning.

### 7.12 Future hardware ideas
- **A speckle motion sensor for flat panels.** A laser and a strongly defocused camera measure motion from speckle flow, even on featureless surfaces (SpeDo: Jo, Gupta and Nayar, ICCV 2015). It would see the sliding that geometry can't see on a flat panel. The main cameras can't do this: at 60 fps and hand speed the speckle decorrelates completely between frames, and its grain is smaller than a pixel.
- **A strobe line from the camera to the Pico,** if the ELP board has one. Emitters could then switch per frame: dot-free frames for tags and texture, laser-only frames.
- **Blue line lasers and more lines** (4.2.7).

**Will not work, added by this section:** speckle tracking with the main cameras (above); photometric terms *between* frames in any bundle adjustment, because the light moves with the camera. Only left-right terms within a frame are valid, which is what I1 uses.

## 8. References

Links were checked on 2026-11-09 (arXiv API, Crossref, or by opening the page). Licences are as stated in each project's licence file on GitHub on the same day.

### Stereo depth
- Hirschmüller, "Stereo processing by semiglobal matching and mutual information", TPAMI 2008. https://doi.org/10.1109/TPAMI.2007.1166
- Hu, Mordohai, "A quantitative evaluation of confidence measures for stereo vision", TPAMI 2012. https://doi.org/10.1109/TPAMI.2012.46
- Poggi et al., "On the confidence of stereo matching in a deep-learning era: a quantitative evaluation", TPAMI 2021. https://arxiv.org/abs/2101.00431
- Bleyer, Rhemann, Rother, "PatchMatch Stereo: stereo matching with slanted support windows", BMVC 2011. https://doi.org/10.5244/C.25.14
- Shimizu, Okutomi, "Sub-pixel estimation error cancellation on area-based matching", IJCV 2005. https://doi.org/10.1007/s11263-005-6878-5
- Davis, Nehab, Ramamoorthi, Rusinkiewicz, "Spacetime stereo: a unifying framework for depth from triangulation", TPAMI 2005. https://doi.org/10.1109/TPAMI.2005.37
- Lipson, Teed, Deng, "RAFT-Stereo: multilevel recurrent field transforms for stereo matching", 3DV 2021. https://arxiv.org/abs/2109.07547 (code, MIT: https://github.com/princeton-vl/RAFT-Stereo)
- Li et al., "Practical stereo matching via cascaded recurrent network with adaptive correlation" (CREStereo), CVPR 2022. https://arxiv.org/abs/2203.11483 (code, Apache-2.0: https://github.com/megvii-research/CREStereo)
- Xu et al., "Iterative geometry encoding volume for stereo matching" (IGEV-Stereo), CVPR 2023. https://arxiv.org/abs/2303.06615 (code, MIT: https://github.com/gangweiX/IGEV)
- Xu et al., "IGEV++: iterative multi-range geometry encoding volumes for stereo matching", 2024. https://arxiv.org/abs/2409.00638 (code, MIT: https://github.com/gangweiX/IGEV-plusplus)
- Wang et al., "Selective-Stereo: adaptive frequency information selection for stereo matching", CVPR 2024. https://arxiv.org/abs/2403.00486 (code, MIT: https://github.com/Windsrain/Selective-Stereo)
- Jiang et al., "DEFOM-Stereo: depth foundation model based stereo matching", CVPR 2025. https://arxiv.org/abs/2501.09466 (code, MIT: https://github.com/Insta360-Research-Team/DEFOM-Stereo; the ViT-L weights build on a CC BY-NC 4.0 model)
- Yang et al., "Depth Anything V2", 2024. https://arxiv.org/abs/2406.09414 (small model Apache-2.0; base, large and giant CC BY-NC 4.0)
- Wen et al., "FoundationStereo: zero-shot stereo matching", CVPR 2025. https://arxiv.org/abs/2501.09898 (research-only licence: https://github.com/NVlabs/FoundationStereo)
- Cheng et al., "MonSter" / "MonSter++", 2025. https://arxiv.org/abs/2501.08643 (research-only licence: https://github.com/Junda24/MonSter)
- Bartolomei, Tosi, Poggi, Mattoccia, "Stereo Anywhere", CVPR 2025. https://arxiv.org/abs/2412.04472 (CC BY-NC-SA 4.0)
- Tankovich et al., "HITNet: hierarchical iterative tile refinement network for real-time stereo matching", CVPR 2021. https://arxiv.org/abs/2007.12140
- Zhang et al., "ActiveStereoNet: end-to-end self-supervised learning for active stereo systems", ECCV 2018. https://arxiv.org/abs/1807.06009
- Liu et al., "ActiveZero: mixed domain learning for active stereovision with zero annotation", CVPR 2022. https://arxiv.org/abs/2112.02772
- Riegler et al., "Connecting the Dots: learning representations for active monocular depth estimation", CVPR 2019. https://openaccess.thecvf.com/content_CVPR_2019/html/Riegler_Connecting_the_Dots_Learning_Representations_for_Active_Monocular_Depth_Estimation_CVPR_2019_paper.html (code, MIT: https://github.com/autonomousvision/connecting_the_dots)
- Baek, Heide, "Polka Lines: learning structured illumination and reconstruction for active stereo", CVPR 2021. https://arxiv.org/abs/2011.13117 (patent US 12,236,625 B2: https://patents.google.com/patent/US12236625B2/en)

### Laser lines and optics
- Steger, "An unbiased detector of curvilinear structures", TPAMI 1998. https://doi.org/10.1109/34.659930
- Dorsch, Häusler, Herrmann, "Laser triangulation: fundamental uncertainty in distance measurement", Applied Optics 1994. https://doi.org/10.1364/AO.33.001306
- Gupta, Agrawal, Veeraraghavan, Narasimhan, "Structured light 3D scanning in the presence of global illumination", CVPR 2011. https://doi.org/10.1109/CVPR.2011.5995321
- O'Toole, Achar, Narasimhan, Kutulakos, "Homogeneous codes for energy-efficient illumination and imaging", SIGGRAPH 2015. https://doi.org/10.1145/2766897
- "Physically based polarization reflection separation for laser stripe extraction" on highly reflective metal, IEEE TIM, 2026. https://pure.nwpu.edu.cn/en/publications/physically-based-polarization-reflection-separation-for-laser-str/
- Rusinkiewicz, Hall-Holt, Levoy, "Real-time 3D model acquisition", SIGGRAPH 2002. https://doi.org/10.1145/566654.566600

### Tracking
- Rusinkiewicz, Levoy, "Efficient variants of the ICP algorithm", 3DIM 2001. https://doi.org/10.1109/IM.2001.924423
- Gelfand, Ikemoto, Rusinkiewicz, Levoy, "Geometrically stable sampling for the ICP algorithm", 3DIM 2003. https://doi.org/10.1109/IM.2003.1240258
- Rusinkiewicz, "A symmetric objective function for ICP", ACM TOG (SIGGRAPH) 2019. https://doi.org/10.1145/3306346.3323037
- Censi, "An accurate closed-form estimate of ICP's covariance", ICRA 2007. https://doi.org/10.1109/ROBOT.2007.363961
- Zhang, Kaess, Singh, "On degeneracy of optimization-based state estimation problems", ICRA 2016. https://doi.org/10.1109/ICRA.2016.7487211
- Tuna et al., "X-ICP: localizability-aware LiDAR registration for robust localization in extreme environments", 2022. https://arxiv.org/abs/2211.16335
- Lee, Lim, Han, "GenZ-ICP: generalizable and degeneracy-robust LiDAR odometry using an adaptive weighting", 2024. https://arxiv.org/abs/2411.06766 (code, MIT: https://github.com/cocel-postech/genz-icp)
- Vizzo et al., "KISS-ICP: in defense of point-to-point ICP", RA-L 2023. https://arxiv.org/abs/2209.15397 (code, MIT: https://github.com/PRBonn/kiss-icp)
- Forster, Carlone, Dellaert, Scaramuzza, "On-manifold preintegration for real-time visual-inertial odometry", T-RO 2017. https://arxiv.org/abs/1512.02363
- Segal, Haehnel, Thrun, "Generalized-ICP", RSS 2009. http://www.roboticsproceedings.org/rss05/p21.pdf
- Bylow, Sturm, Kerl, Kahl, Cremers, "Real-time camera tracking and 3D reconstruction using signed distance functions", RSS 2013. http://www.roboticsproceedings.org/rss09/p35.pdf
- Newcombe et al., "KinectFusion: real-time dense surface mapping and tracking", ISMAR 2011. https://doi.org/10.1109/ISMAR.2011.6092378

### Loop closure and global consistency
- Choi, Zhou, Koltun, "Robust reconstruction of indoor scenes", CVPR 2015. https://openaccess.thecvf.com/content_cvpr_2015/html/Choi_Robust_Reconstruction_of_2015_CVPR_paper.html
- Zhou, Park, Koltun, "Fast global registration", ECCV 2016. https://doi.org/10.1007/978-3-319-46475-6_47
- Yang, Shi, Carlone, "TEASER: fast and certifiable point cloud registration", T-RO 2021. https://arxiv.org/abs/2001.07715 (code, MIT: https://github.com/MIT-SPARK/TEASER-plusplus)
- Lim et al., "KISS-Matcher: fast and robust point cloud registration revisited", 2024. https://arxiv.org/abs/2409.15615 (code, MIT: https://github.com/MIT-SPARK/KISS-Matcher)
- Rusu, Blodow, Beetz, "Fast point feature histograms (FPFH) for 3D registration", ICRA 2009. https://doi.org/10.1109/ROBOT.2009.5152473
- Reijgwart et al., "Voxgraph: globally consistent, volumetric mapping using signed distance function submaps", RA-L 2020. https://arxiv.org/abs/2004.13154 (code, BSD-2: https://github.com/ethz-asl/voxgraph)
- Whelan et al., "ElasticFusion: dense SLAM without a pose graph", RSS 2015. http://www.roboticsproceedings.org/rss11/p01.pdf; IJRR 2016: https://doi.org/10.1177/0278364916669237 (non-commercial licence)
- Sumner, Schmid, Pauly, "Embedded deformation for shape manipulation", SIGGRAPH 2007. https://doi.org/10.1145/1276377.1276478
- Dai, Nießner, Zollhöfer, Izadi, Theobalt, "BundleFusion", ACM TOG 2017. https://arxiv.org/abs/1604.01093, https://doi.org/10.1145/3054739 (CC BY-NC-SA 4.0)
- Schöps, Sattler, Pollefeys, "BAD SLAM: bundle adjusted direct RGB-D SLAM", CVPR 2019. https://openaccess.thecvf.com/content_CVPR_2019/html/Schops_BAD_SLAM_Bundle_Adjusted_Direct_RGB-D_SLAM_CVPR_2019_paper.html (code, BSD-3: https://github.com/ETH3D/badslam)
- Kähler, Prisacariu, Murray, "Real-time large-scale dense 3D reconstruction with loop closure", ECCV 2016. https://doi.org/10.1007/978-3-319-46484-8_30 (InfiniTAM's code is non-commercial)
- Pulli, "Multiview registration for large data sets", 3DIM 1999. https://doi.org/10.1109/IM.1999.805346
- Lu, Milios, "Globally consistent range scan alignment for environment mapping", Autonomous Robots 1997. https://doi.org/10.1023/A:1008854305733
- GTSAM (BSD): https://github.com/borglab/gtsam

### Fusion and representation
- Curless, Levoy, "A volumetric method for building complex models from range images", SIGGRAPH 1996. https://doi.org/10.1145/237170.237269
- Nguyen, Izadi, Lovell, "Modeling Kinect sensor noise for improved 3D reconstruction and tracking", 3DIMPVT 2012. https://doi.org/10.1109/3DIMPVT.2012.84
- Nießner, Zollhöfer, Izadi, Stamminger, "Real-time 3D reconstruction at scale using voxel hashing", ACM TOG 2013. https://doi.org/10.1145/2508363.2508374
- Splietker, Behnke, "Directional TSDF: modeling surface orientation for coherent meshes", IROS 2019. https://arxiv.org/abs/1908.05146
- Schöps, Sattler, Pollefeys, "SurfelMeshing: online surfel-based mesh reconstruction", 2018. https://arxiv.org/abs/1810.00729 (code, BSD-3: https://github.com/puzzlepaint/surfelmeshing)
- Vizzo et al., "VDBFusion: flexible and efficient TSDF integration of range sensor data", Sensors 2022. https://doi.org/10.3390/s22031296 (code, MIT: https://github.com/PRBonn/vdbfusion)
- Kerbl et al., "3D Gaussian splatting for real-time radiance field rendering", SIGGRAPH 2023. https://arxiv.org/abs/2308.04079 (non-commercial licence)
- Huang et al., "2D Gaussian splatting for geometrically accurate radiance fields", SIGGRAPH 2024. https://arxiv.org/abs/2403.17888 (non-commercial licence)
- Chen et al., "PGSR: planar-based Gaussian splatting for efficient and high-fidelity surface reconstruction", 2024. https://arxiv.org/abs/2406.06521
- Yu, Sattler, Geiger, "Gaussian Opacity Fields", 2024. https://arxiv.org/abs/2404.10772
- Li et al., "Neuralangelo: high-fidelity neural surface reconstruction", CVPR 2023. https://arxiv.org/abs/2306.03092 (NVIDIA non-commercial licence)
- "A comparative evaluation of geometric accuracy in NeRF and Gaussian splatting", 2026. https://arxiv.org/abs/2604.18205
- "Learning-based 3D reconstruction methods for non-collaborative surfaces: a metrological evaluation", *Metrology* 2025. https://doi.org/10.3390/metrology5020020
- gsplat (Apache-2.0): https://github.com/nerfstudio-project/gsplat

### Mesh post-processing
- Kazhdan, Hoppe, "Screened Poisson surface reconstruction", ACM TOG 2013. https://doi.org/10.1145/2487228.2487237
- Kazhdan, Chuang, Rusinkiewicz, Hoppe, "Poisson surface reconstruction with envelope constraints", SGP 2020. https://doi.org/10.1111/cgf.14077 (code, MIT: https://github.com/mkazhdan/PoissonRecon)
- Liepa, "Filling holes in meshes", SGP 2003. https://doi.org/10.2312/SGP/SGP03/200-206
- Taubin, "A signal processing approach to fair surface design", SIGGRAPH 1995. https://doi.org/10.1145/218380.218473
- Fleishman, Drori, Cohen-Or, "Bilateral mesh denoising", SIGGRAPH 2003. https://doi.org/10.1145/1201775.882368
- Zheng, Fu, Au, Tai, "Bilateral normal filtering for mesh denoising", TVCG 2011. https://doi.org/10.1109/TVCG.2010.264
- Zhang, Deng, Zhang, Bouaziz, Liu, "Guided mesh normal filtering", Pacific Graphics 2015. https://doi.org/10.1111/cgf.12742
- He, Schaefer, "Mesh denoising via L0 minimization", SIGGRAPH 2013. https://doi.org/10.1145/2461912.2461965
- Öztireli, Guennebaud, Gross, "Feature preserving point set surfaces based on non-linear kernel regression", CGF 2009. https://doi.org/10.1111/j.1467-8659.2009.01388.x
- Garland, Heckbert, "Surface simplification using quadric error metrics", SIGGRAPH 1997. https://doi.org/10.1145/258734.258849
- Botsch, Kobbelt, "A remeshing approach to multiresolution modeling", SGP 2004. https://doi.org/10.1145/1057432.1057457
- Sorkine et al., "Laplacian surface editing", SGP 2004. https://doi.org/10.1145/1057432.1057456
- Ju, Losasso, Schaefer, Warren, "Dual contouring of Hermite data", SIGGRAPH 2002. https://doi.org/10.1145/566654.566586
- Chen, Zhang, "Neural Marching Cubes", 2021. https://arxiv.org/abs/2106.11272; Chen et al., "Neural Dual Contouring", 2022. https://arxiv.org/abs/2202.01999
- Jakob, Tarini, Panozzo, Sorkine-Hornung, "Instant field-aligned meshes", SIGGRAPH Asia 2015. https://doi.org/10.1145/2816795.2818078 (code, BSD-style: https://github.com/wjakob/instant-meshes)
- Libraries: pmp-library (MIT) https://github.com/pmp-library/pmp-library; gpytoolbox (MIT, except `gpytoolbox.copyleft`, GPL) https://github.com/sgsellan/gpytoolbox; libigl (MPL-2.0, `copyleft` folder GPL) https://github.com/libigl/libigl; QuadriFlow (BSD-style) https://github.com/hjwdzh/QuadriFlow; meshoptimizer (MIT) https://github.com/zeux/meshoptimizer; fast-simplification (MIT) https://github.com/pyvista/fast-simplification

### Multi-scan alignment and reverse engineering
- Araújo, Oliveira, "A robust statistics approach for plane detection in unorganized point clouds", Pattern Recognition 2020. https://doi.org/10.1016/j.patcog.2019.107115
- Schnabel, Wahl, Klein, "Efficient RANSAC for point-cloud shape detection", CGF 2007. https://doi.org/10.1111/j.1467-8659.2007.01016.x
- pyRANSAC-3D (Apache-2.0): https://github.com/leomariga/pyRANSAC-3D
- Lague, Brodu, Leroux, "Accurate 3D comparison of complex topography with terrestrial laser scanner" (M3C2), ISPRS Journal 2013. https://doi.org/10.1016/j.isprsjprs.2013.04.009
- Liu, Obukhov, Wegner, Schindler, "Point2CAD: reverse engineering CAD models from 3D point clouds", CVPR 2024. https://arxiv.org/abs/2312.04962 (code, Apache-2.0: https://github.com/YujiaLiu76/point2cad)
- Sharma et al., "ParSeNet: a parametric surface fitting network for 3D point clouds", ECCV 2020. https://arxiv.org/abs/2003.12181
- Yan et al., "HPNet: deep primitive segmentation using hybrid representations", ICCV 2021. https://arxiv.org/abs/2105.10620
- Qin et al., "Geometric Transformer for fast and robust point cloud registration", CVPR 2022. https://arxiv.org/abs/2202.06688
- Libraries: small_gicp (MIT) https://github.com/koide3/small_gicp; NURBS-Python / geomdl (MIT) https://github.com/orbingol/NURBS-Python; OpenCascade (LGPL-2.1 with an exception) https://github.com/Open-Cascade-SAS/OCCT; OCP (Apache-2.0) https://github.com/CadQuery/OCP; CadQuery (Apache-2.0) https://github.com/CadQuery/cadquery

### Calibration and accuracy testing
- Dang, Hoffmann, Stiller, "Continuous stereo self-calibration by camera parameter tracking", TIP 2009. https://doi.org/10.1109/TIP.2009.2017824
- Hansen, Alismail, Rander, Browning, "Online continuous stereo extrinsic parameter estimation", CVPR 2012. https://doi.org/10.1109/CVPR.2012.6247784
- Ling, Shen, "High-precision online markerless stereo extrinsic calibration", IROS 2016. https://arxiv.org/abs/1903.10705
- Teichman, Miller, Thrun, "Unsupervised intrinsic calibration of depth sensors via SLAM", RSS 2013. http://www.roboticsproceedings.org/rss09/p27.pdf
- Zhou, Koltun, "Simultaneous localization and calibration: self-calibration of consumer depth cameras", CVPR 2014. https://openaccess.thecvf.com/content_cvpr_2014/html/Zhou_Simultaneous_Localization_and_2014_CVPR_paper.html
- VDI/VDE 2634 Part 3, "Optical 3D measuring systems: multiple view systems based on area scanning", 2008 (table of contents): https://m.vdi.de/fileadmin/pages/vdi_de/redakteure/richtlinien/inhaltsverzeichnisse/1456386.pdf
- "Accuracy investigations of hand-held scanning systems using different dumbbell artefacts", ISPRS Archives XLIII-B2-2022. https://isprs-archives.copernicus.org/articles/XLIII-B2-2022/401/2022/

### Difficult surfaces
- Zhang, Yau, "High dynamic range scanning technique", Optical Engineering 2009. https://doi.org/10.1117/1.3099720
- Feng et al., "High dynamic range 3D measurements with fringe projection profilometry: a review", Measurement Science and Technology 2018. https://doi.org/10.1088/1361-6501/aae4fb
- Salahieh, Chen, Liang, "Multi-polarization fringe projection imaging for high dynamic range objects", Optics Express 2014. https://doi.org/10.1364/OE.22.010064

### Innovation track (section 7)
- Zhang, Curless, Seitz, "Spacetime stereo: shape recovery for dynamic scenes", CVPR 2003. https://doi.org/10.1109/CVPR.2003.1211492
- Vu, Labatut, Pons, Keriven, "High accuracy and visibility-consistent dense multiview stereo", TPAMI 2012. https://doi.org/10.1109/TPAMI.2011.172
- Delaunoy, Pollefeys, "Photometric bundle adjustment for dense multi-view 3D modeling", CVPR 2014. https://doi.org/10.1109/CVPR.2014.193
- Weise, Leibe, Van Gool, "Fast 3D scanning with automatic motion compensation", CVPR 2007. https://doi.org/10.1109/CVPR.2007.383291
- Konolige, "Projected texture stereo", ICRA 2010. https://doi.org/10.1109/ROBOT.2010.5509796
- "Multi-frame optimisation for active stereo with inverse rendering to obtain consistent shape and projector-camera poses for 3D endoscopic system", Computer Methods in Biomechanics and Biomedical Engineering: Imaging & Visualization, 2022. https://doi.org/10.1080/21681163.2022.2155578
- Shandilya et al., "Neural fields for structured lighting", ICCV 2023. https://openaccess.thecvf.com/content/ICCV2023/html/Shandilya_Neural_Fields_for_Structured_Lighting_ICCV_2023_paper.html
- "Depth reconstruction with neural signed distance fields in structured light systems", 2024. https://arxiv.org/abs/2405.12006
- "Neural shape reconstruction from multiple views with static pattern projection", 2025. https://arxiv.org/abs/2506.01389
- Chugunov et al., "The implicit values of a good hand shake: handheld multi-frame neural depth refinement", CVPR 2022. https://arxiv.org/abs/2111.13738
- Hébert, "A self-referenced hand-held range sensor", 3DIM 2001. https://doi.org/10.1109/IM.2001.924380
- Tubic, Hébert, Laurendeau, "3D surface modeling from range curves", CVPR 2003. https://doi.org/10.1109/CVPR.2003.1211440; "3D surface modeling from curves", Image and Vision Computing 2004. https://doi.org/10.1016/j.imavis.2004.03.006 (patent US 7,487,063, listed as expired: https://patents.google.com/patent/US7487063B2/en)
- Sheehan, Harrison, Newman, "Self-calibration for a 3D laser", IJRR 2012. https://doi.org/10.1177/0278364911429475
- ISO/TS 15530-4:2008, "Coordinate measuring machines (CMM): technique for determining the uncertainty of measurement, part 4: evaluating task-specific measurement uncertainty using simulation" (store page): https://www.csagroup.org/store/product/iso_038696
- JCGM 100:2008, Guide to the expression of uncertainty in measurement (GUM). https://doi.org/10.59161/JCGM100-2008E
- JCGM 101:2008, GUM Supplement 1: propagation of distributions using a Monte Carlo method. https://doi.org/10.59161/JCGM101-2008
- Stetson, "DAOPHOT: a computer program for crowded-field stellar photometry", PASP 1987. https://doi.org/10.1086/131977
- Kholkhujaev et al., "Non-contact articulated robot-integrated gap and flushness measurement system for automobile assembly", IEEE Access 2022. https://doi.org/10.1109/ACCESS.2022.3199066
- Hexagon CALIPRI handheld light-section gap and flush gauge (product page): https://hexagon.com/products/calipri-c14
- Beier, Chen, "Highlight-line algorithm for realtime surface-quality assessment", Computer-Aided Design 1994. https://doi.org/10.1016/0010-4485(94)90073-6
- Knauer, Kaminski, Häusler, "Phase measuring deflectometry: a new approach to measure specular free-form surfaces", Proc. SPIE 5457, 2004. https://doi.org/10.1117/12.545704
- Nehab, Rusinkiewicz, Davis, Ramamoorthi, "Efficiently combining positions and normals for precise 3D geometry", SIGGRAPH 2005. https://doi.org/10.1145/1186822.1073226
- Ohtake, Belyaev, Seidel, "Ridge-valley lines on meshes via implicit surface fitting", SIGGRAPH 2004. https://doi.org/10.1145/1186562.1015768
- Mitra, Guibas, Pauly, "Partial and approximate symmetry detection for 3D geometry", SIGGRAPH 2006. https://doi.org/10.1145/1179352.1141924
- Vogiatzis, Hernández, "Video-based, real-time multi-view stereo", Image and Vision Computing 2011. https://doi.org/10.1016/j.imavis.2011.01.006
- Isler, Sabzevari, Delmerico, Scaramuzza, "An information gain formulation for active volumetric 3D reconstruction", ICRA 2016. https://doi.org/10.1109/ICRA.2016.7487527
- Jo, Gupta, Nayar, "SpeDo: 6 DOF ego-motion sensor using speckle defocus imaging", ICCV 2015. https://doi.org/10.1109/ICCV.2015.491

### Licences checked, at a glance

| Usable (MIT, BSD, Apache-2.0; MPL or LGPL as a dependency) | Inspiration only (GPL) | Not usable (non-commercial or research-only) |
|---|---|---|
| Open3D, PyOpenCL, RAFT-Stereo, CREStereo, IGEV, IGEV++, Selective-Stereo, DEFOM-Stereo code, Connecting the Dots, KISS-ICP, GenZ-ICP, TEASER++, KISS-Matcher, GeoTransformer, small_gicp, GTSAM, voxgraph, BAD SLAM, SurfelMeshing, VDBFusion, DROID-SLAM, gsplat, PoissonRecon, pmp-library, gpytoolbox (main), meshoptimizer, fast-simplification, Instant Meshes, QuadriFlow, pyRANSAC-3D, Point2CAD, NURBS-Python, OCP, CadQuery, libigl (MPL-2.0), OpenCascade (LGPL) | CGAL (main packages), MeshLab / PyMeshLab, pymeshfix, CloudCompare, FAST-LIO, gpytoolbox.copyleft, libigl's copyleft folder | FoundationStereo, MonSter, Stereo Anywhere, ElasticFusion, BundleFusion, InfiniTAM, Inria Gaussian splatting and 2DGS, Neuralangelo, DUSt3R, Depth Anything V2 base/large/giant weights |
