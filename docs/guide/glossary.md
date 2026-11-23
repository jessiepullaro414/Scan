# Glossary

This page explains the words the app and this guide use, in alphabetical order. Each entry links to the page that covers it.

| Word | Meaning | More |
|---|---|---|
| **±** (sigma) | How much a value scatters: scanned again, it would typically land within that distance. It is the scatter, not the accuracy. | [What the ± means](measuring.md#what-the--means) |
| **Align** | To work out how one scan sits relative to another (or to a CAD model), so that they lie on each other. | [Aligning and merging scans](align-merge.md), [Comparing a scan with a reference](compare.md) |
| **AprilTag** | See **Tag**. | |
| **Bias** | An error that is the same on every scan, so the ± does not show it. Example: a hole's open edge reads small. | [Known biases](measuring.md#known-biases) |
| **Calibration** | The measured geometry of your scanner: how its two cameras sit and how their lenses bend the picture, where each laser line lies, how the motion sensor is mounted. The app needs it to turn pictures into millimetres. | [Set up and calibrate the scanner](setup.md) |
| **Camera path** | Where the scanner was at each frame of a scan, worked out from the recording. | [From a recording to a mesh](make-mesh.md) |
| **Colour mode** | How the viewer colours the mesh: plain, by direction, by confidence, by what your edits moved, by the laser lines, or against the reference. | [Colour modes](viewer.md#colour-modes) |
| **Confidence** | How sure the scan is of each point of the mesh, as a ± in millimetres. Also a colour mode of the viewer. | [Confidence](viewer.md#confidence) |
| **Control board** | The small board in the scanner (a Pico or an ESP32) that switches the lasers and the dots, and reads the trigger and the motion sensor. | [Step 1: Connect](setup.md#step-1-connect) |
| **Demo** | The app running on built-in example data with a simulated scanner, without the engine or any hardware. A **Demo** badge shows in the sidebar. | [The Demo badge](getting-started.md#the-demo-badge) |
| **Deviation** | How far a surface is from another one: from a CAD model in a comparison, or from where it was before your edits in the viewer's Deviation mode. | [The deviation colours](compare.md#the-deviation-colours), [Deviation](viewer.md#deviation) |
| **Dots** | The pattern of random dots the scanner projects, so that the cameras find something to match on plain surfaces. | [The lasers and dots](scanning.md#the-lasers-and-dots) |
| **Edit** | A change to a mesh after it is made: removing loose bits or the table, filling holes, smoothing, simplifying, deleting selected faces. Every edit can be undone. | [Cleaning a mesh](editing.md) |
| **Engine** | The program (`ohs`) that talks to the scanner and does the processing. The window shows the app and talks to the engine. | [What Studio is](getting-started.md#what-studio-is) |
| **Fit** | A simple shape (a plane, a cylinder, a hole's circle) laid through the scan's points where you clicked, to measure it. | [Fit a plane, a cylinder or a hole](measuring.md#fit-a-plane-a-cylinder-or-a-hole) |
| **Flush** | How far one panel stands above or below its neighbour along a seam. | [Measure the flush along a seam](measuring.md#measure-the-flush-along-a-seam) |
| **Frame** | One moment of a scan: the two camera pictures taken together, with the motion sensor's readings. | [Scanning](scanning.md) |
| **Graphics card** (GPU) | The part of a computer that the engine uses to process frames quickly. Without one, scans still work, with limits. | [What this computer can do](getting-started.md#what-this-computer-can-do) |
| **Job** | A longer piece of work the engine does in the background, such as making a mesh or aligning two scans. | [The Jobs panel](projects.md#the-jobs-panel) |
| **Laser points** | The points measured along the laser lines themselves. They can be downloaded as their own file, and **Refine with lasers** uses them. | [Download the scan](viewer.md#download-the-scan) |
| **Line lasers** | The laser lines the scanner projects (two, or four on the four-laser build). They give precise extra points along each line. | [The lasers and dots](scanning.md#the-lasers-and-dots), [Line lasers](settings.md#line-lasers) |
| **Loose bits** | Small pieces of mesh that float apart from the part: dust, reflections, bits of the table's edge. | [Remove loose bits](editing.md#remove-loose-bits) |
| **Markerless / Markers** | The two tracking modes. Markerless follows the part's own shape. Markers follows tags you stick on or beside the part. | [Tags for large plain parts](scanning.md#tags-for-large-plain-parts) |
| **Merge** | To make one new scan out of two or more aligned scans. A merge adds coverage; it does not make the part more accurate than the better scan. | [Merge into a new scan](align-merge.md#merge-into-a-new-scan) |
| **Mesh** | The 3D surface of the part, made of small triangles. What you view, measure and download. | [From a recording to a mesh](make-mesh.md) |
| **Mesh detail** | The size of the smallest cell the surface is built from, in millimetres. Smaller is finer and slower. The default is 1 mm. | [Presets and mesh detail](make-mesh.md#presets-and-mesh-detail) |
| **Motion sensor** (IMU) | The sensor in the scanner that feels how it turns and moves. It helps tracking on fast sweeps and plain surfaces. | [Step 6: Motion sensor (IMU)](setup.md#step-6-motion-sensor-imu) |
| **Nominal** | The value a feature should have by design, such as a hole's drawn diameter. | [Nominal values](measuring.md#nominal-values) |
| **Overlap** | In an alignment: the share of the smaller scan's part that lies within 1 mm of the other scan. Under 70 % the app says the scans share little. | [Read the verdict and the figures](align-merge.md#read-the-verdict-and-the-figures) |
| **Part's axes** | The part's own X, Y and Z and its origin, set from shapes you fitted (a face, a second face, a point). With them set, numbers and files can be given along the part instead of along the scan. | [Set the part's axes](measuring.md#set-the-parts-axes) |
| **Pass** | One sweep of the scanner over the part. A second pass is another scan of the same part, guided towards the areas the first one saw poorly, and merged with it. | [A second pass](scanning.md#a-second-pass) |
| **Pose** | A position together with a direction: where something is and which way it faces. | [If the engine is not sure](align-merge.md#if-the-engine-is-not-sure) |
| **Preset** | A named set of values for a kind of part, such as **Thin sheet metal and small parts**. | [Presets and mesh detail](make-mesh.md#presets-and-mesh-detail) |
| **Project** | A folder on your computer that holds the scans of one job, with their results and measurements. | [Projects and scans](projects.md) |
| **Quality** | How many of the recorded frames are used to make the mesh: **Preview**, **Standard** or **Best**. | [Choose the quality](make-mesh.md#choose-the-quality) |
| **Recipe** | A saved set of stage settings and edits that can be applied to any scan. Make mesh runs the **Default** recipe. | [Recipes](editing.md#recipes) |
| **Recording** | The raw camera frames and motion sensor readings of a scan, kept as the scanner sent them. The mesh can be made again from it at any time. | [What a project and a scan are](projects.md#what-a-project-and-a-scan-are) |
| **Reference** | The part as it should be, for a comparison: a CAD model, or a scan of a good part. | [Add a reference](compare.md#add-a-reference) |
| **Scan** | One recording of a part, with the mesh and the measurements made from it. | [Projects and scans](projects.md) |
| **Scan check** | The app's answer to "is this scan good enough for my tolerance?", with the areas that are not. | [Is it good? The Scan check](viewer.md#is-it-good-the-scan-check) |
| **Seam** | The line where two panels meet. The flush tool measures along it at stations, points spaced along the seam. | [Measure the flush along a seam](measuring.md#measure-the-flush-along-a-seam) |
| **Section** | A cut through the mesh along a plane, shown as a profile you can measure on. | [Cut a section](measuring.md#cut-a-section) |
| **Self-test** | A short check of the scanner's hardware: cameras, control board, motion sensor, lasers, dots and trigger. | [Step 2: Self-test](setup.md#step-2-self-test) |
| **Simulator** | A simulated scanner the app can connect to instead of a real one, to try every step. | [Try the simulator](getting-started.md#try-the-simulator) |
| **Stage** | One step of making the mesh, such as **Follow the camera** or **Straighten**. | [What Make mesh does](make-mesh.md#what-make-mesh-does) |
| **STEP** | A file format CAD programs exchange exact geometry in. The app writes the shapes you fitted and kept (planes, cylinders, holes, axes) as a STEP file. It does not turn the scanned surface into one. | [From the mesh to STL and STEP](to-cad.md#what-the-app-does-and-does-not-do) |
| **STL** | A file format for a mesh: the surface as triangles, without units (the app writes millimetres). What slicers and most CAD programs read. | [Make a good STL](to-cad.md#make-a-good-stl) |
| **Tag** | A printed square sticker with a pattern the cameras can recognise (an AprilTag). Tags hold tracking on large plain parts and can help align two scans. | [Tags for large plain parts](scanning.md#tags-for-large-plain-parts), [Align from tags](align-merge.md#align-from-tags) |
| **Tolerance** | How far off a surface may be before you call it wrong. You give it to the Scan check and to a comparison. | [Is it good? The Scan check](viewer.md#is-it-good-the-scan-check) |
| **Tracking** | How the app works out where the scanner is for each frame, by matching what it sees with the model built so far. | [Tracking](scanning.md#tracking) |
| **Trash** | Where deleted projects and scans go. You can restore them until you empty it. | [Restore from the trash, or empty it](projects.md#restore-from-the-trash-or-empty-it) |
| **Watertight** | A mesh that is closed all round, with no open edge. A 3D print needs one. **Make it watertight** on the Edit mesh page closes the outer edge too. | [Fill holes](editing.md#fill-holes) |
