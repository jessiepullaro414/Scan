# Troubleshooting

This page helps when something goes wrong. Find what you see in the tables below; each row says what it usually means and where the guide explains what to do. If you are looking for the exact words of a message, see [Where each message is explained](#where-each-message-is-explained).

On this page:

- [Starting and connecting](#starting-and-connecting)
- [Calibration](#calibration)
- [While scanning](#while-scanning)
- [Making the mesh](#making-the-mesh)
- [The mesh does not look right](#the-mesh-does-not-look-right)
- [Measurements that read small, large or thick](#measurements-that-read-small-large-or-thick)
- [Files for a printer or for CAD](#files-for-a-printer-or-for-cad)
- [Long plain parts](#long-plain-parts)
- [Aligning and merging](#aligning-and-merging)
- [Files and disk space](#files-and-disk-space)
- [Where each message is explained](#where-each-message-is-explained)
- [Report a problem](#report-a-problem)

The app warns, it does not block: a warning tells you what may be wrong and lets you go on. So far the software has been tested on simulated scans, and the numbers on this page come from the simulator.

## Starting and connecting

| What you see | What it usually means | What to do |
|---|---|---|
| **Can't reach the OpenHandScan engine** | The window is open but the engine is not running, or something else uses its port. | [If the app cannot reach the engine](getting-started.md#if-the-app-cannot-reach-the-engine) |
| **Lost the live connection to the engine.** | The engine stopped answering. A scan that is recording keeps running on the engine. | The app retries by itself. See [If the app cannot reach the engine](getting-started.md#if-the-app-cannot-reach-the-engine). |
| The top bar says **No device** | No scanner or simulator is connected. | [Step 1: Connect](setup.md#step-1-connect), or [Try the simulator](getting-started.md#try-the-simulator) |
| The cameras are found but not the control board, or the other way round | A cable, the wrong camera chosen, or the control board's firmware. | [Step 1: Connect](setup.md#step-1-connect) and its messages |
| The self-test reports a failed check | Wiring, a camera, a laser or the trigger. | [Step 2: Self-test](setup.md#step-2-self-test) |
| **The 3D view couldn't start** | "WebGL is unavailable or the graphics driver reset." The 3D view needs the graphics driver. | Update the graphics driver, or close other 3D apps, then press **Try again**. |
| **Something went wrong on this page** | A page of the app failed. "Your scans are safe on disk." | Press **Try again**, or **Reload Studio**. If it happens again, [report it](#report-a-problem) with the log. |
| **This page doesn't exist** | The address is wrong, or it belongs to a project or scan that is gone. | Go back to Home with the sidebar. |
| A page says it needs a newer engine (**This engine has no Edit mesh page yet**, or the wizard needs "engine 0.2 or newer") | The app's pages are newer than the engine that is running. | [Update the app](getting-started.md#update-the-app), then start it again. |
| A feature is greyed out and names the graphics card | This computer has no graphics card the engine can use. Scans still work, with limits. | [What this computer can do](getting-started.md#what-this-computer-can-do), [Without a graphics card](make-mesh.md#without-a-graphics-card) |

## Calibration

| What you see | What it usually means | What to do |
|---|---|---|
| A new calibration is worse than the one before | The app keeps one backup of the previous calibration file. | [Go back to the previous calibration](setup.md#go-back-to-the-previous-calibration) |
| A calibration result that is not good | Too few views, views too alike, or a print of the wrong size. | [Read a calibration result](setup.md#read-a-calibration-result) |
| The app says the calibration needs a check after you connect | The cameras seem to have moved since the calibration (a knock, heat). | [The calibration status after connecting](setup.md#the-calibration-status-after-connecting), then [Step 7: Calibration check](setup.md#step-7-calibration-check) |
| **Show the board to the camera** stays on the capture screen | No board is found in the picture. | [The capture screen](setup.md#the-capture-screen) |
| **Not captured:** and a reason | The view could not be used. | Follow the reason, for example hold the board still. See [The capture screen](setup.md#the-capture-screen). |
| A scan's row says **Scanned with a calibration that needed a check** | The scan was made while the calibration was in doubt. Its sizes may be off. | Check the calibration ([setup.md](setup.md)), then scan again if sizes matter. |

## While scanning

| What you see | What it usually means | What to do |
|---|---|---|
| **Tracking weak** | The frame matched the model, but not well: a plain surface, too fast, too close or too far. | [If tracking is weak](scanning.md#if-tracking-is-weak) |
| **Tracking lost** | The frame did not match. Nothing is added to the model. | [If tracking is lost](scanning.md#if-tracking-is-lost): return to the dashed scanner, or rewind. |
| The distance gauge is not green | The scanner is too close to the part or too far from it. | [The distance gauge](scanning.md#the-distance-gauge) |
| The model grows in the wrong place | Tracking slid before you noticed. | [Pause, rewind and stop](scanning.md#pause-rewind-and-stop): rewind to before it went wrong. |
| Glossy paint, dark plastic, chrome or glass stays empty | These surfaces reflect or swallow the dots and the laser lines. | [Before you start](scanning.md#before-you-start): a matte scanning spray helps. |
| **The live preview is slow on this computer.** | The computer cannot keep up with the live view. The recording still keeps every frame. | Move more slowly. See [The live 3D view](scanning.md#the-live-3d-view) and [What this computer can do](getting-started.md#what-this-computer-can-do). |

## Making the mesh

| What you see | What it usually means | What to do |
|---|---|---|
| A scan shows **Failed** | Making the mesh stopped. The recording is kept, and so is an earlier mesh. | [If making the mesh fails or you cancel it](make-mesh.md#if-making-the-mesh-fails-or-you-cancel-it) |
| **Tracking may have slid** | The camera path has a jump no hand makes. Thin parts and sizes across that part of the scan may be off. | [If tracking may have slid](make-mesh.md#if-tracking-may-have-slid) |
| **Thin walls found** | The part has walls thinner than the mesh detail can show well. | [If thin walls are found](make-mesh.md#if-thin-walls-are-found) |
| A stage says **Skipped** | Usually nothing was wrong: there was nothing for that stage to correct. | [Other notes after Make mesh](make-mesh.md#other-notes-after-make-mesh) |
| Making the mesh takes very long | Best quality, a long scan, fine mesh detail, or no graphics card. | [How long it takes](make-mesh.md#how-long-it-takes), [Choose the quality](make-mesh.md#choose-the-quality) |
| A second pass stopped before it was merged | A step failed or was cancelled. The pass is kept as its own scan, and the first scan is not changed. | [If the pass stops part-way](scanning.md#if-the-pass-stops-part-way) |
| A job seems stuck | It may still be running. | [The Jobs panel](projects.md#the-jobs-panel): see its progress, or cancel it. |

## The mesh does not look right

| What you see | What it usually means | What to do |
|---|---|---|
| Holes in the surface | The scanner never saw that area, or saw it too poorly. | Scan it in [a second pass](scanning.md#a-second-pass). To close small holes: [Fill holes](editing.md#fill-holes). A filled hole is made up, not measured. |
| The table, or bits floating in the air | They were in view while you scanned. | [Remove the table](editing.md#remove-the-table), [Remove loose bits](editing.md#remove-loose-bits) |
| A thin part is missing or thinner after an edit | An edit took it for the table or for a loose bit. The app warns when it is unsure. | [Thin parts are never removed without a word](editing.md#thin-parts-are-never-removed-without-a-word), [See what was removed](editing.md#see-what-was-removed), then [undo](editing.md#undo-and-the-history) |
| Areas marked weak by the Scan check | Too few views, a steep angle, or a poor match there. | [Why an area is marked weak](viewer.md#why-an-area-is-marked-weak) |
| A surface that shows twice, slightly apart | This can mean tracking slid, so that two passes do not lie on each other. | [If tracking may have slid](make-mesh.md#if-tracking-may-have-slid). Scan again more slowly, with the part's edges in view. |
| Small holes in the part are closed or too small | The depth carries the surface a little way into a hole. | [Known biases](measuring.md#known-biases) |

## Measurements that read small, large or thick

The ± after a value is how much it scatters from scan to scan. It is not the accuracy: an error that is the same on every scan does not show in it. See [What the ± means](measuring.md#what-the--means).

| What you see | What is known (simulated scans) | Where |
|---|---|---|
| A hole reads small | A hole's open edge reads about 1 mm small in diameter, more with tracking. Small holes can close up altogether. The Hole tool's second answer reads the edge from the camera images (within about 0.15 mm on simulated scans; it needs a graphics card). | [Known biases](measuring.md#known-biases), [A hole's second answer, from the camera images](measuring.md#a-holes-second-answer-from-the-camera-images) |
| The Hole card says "No diameter from the camera images" | The camera images gave no answer at this click, in the engine's words: too few frames show the edge, only part of it shows, the click is by another hole, or the frames read the edge differently. The mesh's answer still stands. | Click on the flat face right beside the hole; scan it again looking down into it from several sides; else measure it with calipers or a pin gauge. [The table of these messages](measuring.md#a-holes-second-answer-from-the-camera-images) |
| The Hole card says "The hole's position may be off" | The camera path check warned, or the frames placed the hole's centre apart. The diameter does not depend on the camera path; the position does. | [A hole's second answer, from the camera images](measuring.md#a-holes-second-answer-from-the-camera-images), [Scanning](scanning.md#messages-on-this-page) |
| A boss or a pin reads small | Small bosses read a few tenths of a millimetre small. | [Known biases](measuring.md#known-biases) |
| A thin wall reads too thick | The thinner the wall, the more: a 1 mm rib can read 1.7 to 2.2 mm with tracking. The Fit tool's **Wall** choice reads it nearer from the camera frames, as an upper bound ("at most"); it needs a graphics card. | [Known biases](measuring.md#known-biases), [Read a thin wall's thickness](measuring.md#read-a-thin-walls-thickness), [If thin walls are found](make-mesh.md#if-thin-walls-are-found) |
| A fit tool refuses a click | The tool says why in its own words (for example no hole edge near the click). | [Fit a plane, a cylinder or a hole](measuring.md#fit-a-plane-a-cylinder-or-a-hole) and the messages on that page |
| A comparison with CAD is off everywhere by the same amount | The alignment, or the reference's units. | [Align the scan with the reference](compare.md#align-the-scan-with-the-reference), [If the app asks about units](compare.md#if-the-app-asks-about-units) |

When a size matters, check it another way (calipers, a gauge) before you rely on the scan.

## Files for a printer or for CAD

| What you see | What it usually means | What to do |
|---|---|---|
| The STEP file holds only a few faces, not the whole part | The app writes the shapes you fitted and kept, as exact geometry. It does not turn the scanned surface into a STEP solid. | [What the app does and does not do](to-cad.md#what-the-app-does-and-does-not-do): use the STL for the surface and the STEP file for the exact faces and axes. |
| **STEP** and **FreeCAD macro** are greyed out | No fitted shape is kept yet. | [Make a STEP file of the fitted shapes](to-cad.md#make-a-step-file-of-the-fitted-shapes) |
| The STL opens tilted, or far from the origin | The plain **STL** is along the scan's own axes. | Set the part's axes and use **STL along the part's axes**: [Make a good STL](to-cad.md#make-a-good-stl). |
| The slicer says the mesh is open or not manifold | The mesh has holes or an open outer edge. | **Make it watertight** on the Edit mesh page: [Fill holes](editing.md#fill-holes). The closed-in surface is made up, not measured. |
| The other program asks for the units, or the part comes in 25 times too big or too small | STL carries no units. The app's files are in millimetres. | Choose millimetres when the other program asks. |
| The STL file is very large | The mesh has many triangles. | [Simplify](editing.md#simplify) before you download. |

## Long plain parts

On a long part with nothing along its length to hold on to (a plain panel, a sill, a bumper), tracking can slide along the part without any sign of it.

- Measured on a simulated plain panel 1.3 m long, scanned in three passes without tags: the overall length came out 1.6 to 6.5 mm wrong (0.12 to 0.50 %).
- The surface itself looked fine, and the app gave no warning. The passes lay on each other across the surface and were shifted along it.
- What helps today: scan with tags in **Markers** mode ([Tags for large plain parts](scanning.md#tags-for-large-plain-parts)), keep the part's ends and edges in view, and check a long part's overall length with a tape measure. On the same panel scanned with tags on the table, the mesh's length was 0.2 to 0.6 mm off with the setting `straighten_tags` on, and 0.7 to 2.6 mm off without it: see [Tags that hold a markerless scan](scanning.md#tags-that-hold-a-markerless-scan-a-setting-off). Where no frame sees two tags spaced along the part, the tags join the passes but do not hold the length.

## Aligning and merging

| What you see | What it usually means | What to do |
|---|---|---|
| **Not aligned: not sure** | The engine found a pose but too few of its tries agree on it. | [If the engine is not sure](align-merge.md#if-the-engine-is-not-sure): pick matching points, or scan with more overlap. |
| **The scans share little** | The two scans are aligned, but under 70 % of the smaller one lies on the other. Away from where they meet the position can be a millimetre or more off. | [The scans share little](align-merge.md#the-scans-share-little) |
| A merged scan looks worse than the scans it came from | A wrong position, or the merge averaging a good view with a poor one. | [What a merge does and does not improve](align-merge.md#what-a-merge-does-and-does-not-improve) |
| The scans do not align after the part was turned over | The app needs to know the part was moved. | [Was the part moved between the scans?](align-merge.md#was-the-part-moved-between-the-scans) |

## Files and disk space

| What you see | What to do |
|---|---|
| The disk is filling up | [See how much space a project takes](projects.md#see-how-much-space-a-project-takes), [Free space](projects.md#free-space) |
| A scan or a project was deleted by mistake | [Restore from the trash, or empty it](projects.md#restore-from-the-trash-or-empty-it) |
| You cannot find a file you downloaded | Files are saved as ordinary downloads; where they land depends on the window or browser. | [Where downloaded files go](getting-started.md#where-downloaded-files-go) |
| You cannot find a mesh file | [Download a mesh](projects.md#download-a-mesh), [Where a project lives on disk](projects.md#where-a-project-lives-on-disk) |
| You want to move your projects to another folder or disk, or back them up | [Move or back up your projects](projects.md#move-or-back-up-your-projects) |

## Where each message is explained

Every page of the guide ends with a table of the messages you can meet on that part of the app, in the app's words:

| Messages about | Table |
|---|---|
| Keys that do nothing | [Keys and mouse](keys.md#when-keys-work) |
| Starting the app, the engine, the simulator | [Getting started](getting-started.md#messages-on-this-page) |
| Connecting, the self-test, printing, calibration | [Set up and calibrate the scanner](setup.md#messages-on-this-page) |
| Scanning and tracking | [Scanning](scanning.md#messages-on-this-page) |
| Making the mesh, its stages and notes | [From a recording to a mesh](make-mesh.md#messages-on-this-page) |
| Edits, the history, recipes | [Cleaning a mesh](editing.md#messages-on-this-page) |
| The viewer, the Scan check, downloads | [The result viewer](viewer.md#messages-on-this-page) |
| Distances, fits, sections, flush, CAD files | [Measuring on the scan](measuring.md#messages-on-this-page) |
| Comparing with a reference | [Comparing a scan with a reference](compare.md#messages-on-this-page) |
| Aligning and merging | [Aligning and merging scans](align-merge.md#messages-on-this-page) |
| Projects, the trash, disk space, jobs | [Projects and scans](projects.md#messages-on-this-page) |
| Settings | [Settings](settings.md#messages-on-this-page) |

## Report a problem

If the guide does not help, report the problem on the project's GitHub page (Issues). A good report has:

1. What you did, what you expected and what happened instead, in a few lines.
2. The app's log: on the Settings page, in the **About** box, press **Save a log for a bug report…** and attach the file. See [About](settings.md#about).
3. The versions shown in the **About** box (**Studio UI**, **Engine**) and the **Graphics card** line.
4. For a problem with one scan: its `report.json` from the scan's folder ([Where a project lives on disk](projects.md#where-a-project-lives-on-disk)), and a picture of the screen.

The log and `report.json` can hold the names of your projects and folders. Read them before you post them in public.
