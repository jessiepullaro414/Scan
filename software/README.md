# OpenHandScan software: user guide

`ohs` is the command-line tool for the OpenHandScan handheld scanner. It checks the hardware,
prints the calibration targets, calibrates the scanner, scans with live feedback, and turns
recordings into meshes (STL/PLY, millimetres). You can try all of it without hardware on simulated
data (section 9).

The hardware, parts list and assembly are in the [main README](../README.md). The design and
conventions are in [ARCHITECTURE.md](ARCHITECTURE.md).

```
ohs app                      the desktop app, OpenHandScan Studio (section 1, "Desktop app")
ohs devices                  find the camera and the Pico
ohs selftest                 check camera, IMU, clock, emitters and trigger
ohs printables print/        calibration board, AprilTag stickers, LED projector mask
ohs calibrate stereo         then: ohs calibrate lasers, ohs calibrate imu
ohs scan --process           scan (the trigger starts and stops), then build the final mesh
ohs process <recording>      (re)process a recording at full quality
```

Every command has `--help`.

---

## 1. Install

You need Python 3.10 or newer (3.11+ recommended), and Git or a copy of this repository.

**Windows (PowerShell)**

```powershell
py -3.13 -m venv C:\venvs\ohs
C:\venvs\ohs\Scripts\Activate.ps1
cd <repository>\software
pip install -e .[fusion]
ohs --version
```

**Linux**

```bash
python3 -m venv ~/.venvs/ohs
source ~/.venvs/ohs/bin/activate
cd <repository>/software
pip install -e ".[fusion]"
sudo usermod -aG dialout $USER     # access to the Pico's serial port; log out and in again
```

* `[fusion]` installs Open3D, which `ohs scan` and `ohs process` need. Everything else (devices,
  self-test, preview, printables, calibration, recording, simulation) works without it.
* **Windows path length:** Windows limits paths to 260 characters (MAX_PATH) by default, and
  Open3D and OpenCV install deep folder trees. Keep the virtual environment at a **short path**
  such as `C:\venvs\ohs`, not inside a deeply nested project or OneDrive folder, or installation
  fails with confusing "file not found" errors. (Enabling Windows long-path support also works.)
* Python 3.10 only: `pip install tomli` to read the settings file (3.11+ has `tomllib` built in).

### Desktop app (OpenHandScan Studio)

Studio is the point-and-click way to scan. It has projects, a live 3D view while you scan,
rewind, one-click meshing, and a viewer with measuring. It runs entirely on this PC: a local
server (`ohs.server`) plus a window that shows the UI. Install the app extras and build the UI
once. Building needs [Node.js](https://nodejs.org) 20 or newer.

```powershell
pip install -e .[fusion,app]
cd ui
npm install
npm run build        # writes the UI to ohs/server/static/
cd ..
ohs app
```

* `ohs app` opens the window. With no scanner yet, click **Try the simulator demo**: it builds a
  simulated scan once (about a minute) and then behaves like a connected scanner.
* **Setup** in the sidebar is the first-run wizard. Its steps:
  1. Connect the scanner.
  2. Run the self-test.
  3. Print the calibration board, tags and LED mask, and check the print's scale with calipers.
  4. Calibrate the stereo cameras: views are taken by themselves once the board is steady, a live
     coverage map shows what's still missing, and the result comes in plain words.
  5. Calibrate the laser planes.
  6. Calibrate the IMU.
  7. Run the 2-minute check.

  On the simulator, a virtual board moves in front of a virtual scanner, so you can try every step
  before the hardware arrives. Its results go to a sandbox, and your real calibration is never
  touched.
* After each connect the app quietly checks whether the calibration still fits the cameras. It
  only speaks up when the cameras seem to have moved.
* `ohs app --no-window --open-browser` uses your web browser instead of the window.
* `--port` and `--host` change where the server listens (default `127.0.0.1:8765`; settings
  `[app]`). `--host 0.0.0.0` also serves phones and tablets on your network. There is no
  password, so only do that on a network you trust.
* Projects are saved in `~/OpenHandScan/projects` (setting `paths.projects_dir`): one plain folder
  per project with its recordings and results. Deleting a project in the app moves it to
  `.trash` in that folder.
* Other places:
  * printables go to `~/OpenHandScan/printables` (`paths.printables_dir`);
  * the wizard's state goes to `~/.ohs/studio` (`paths.data_dir`);
  * calibration captures go to `<scans_dir>/calibration/`, like `ohs calibrate`.
* UI development: `npm run dev` in `ui/` serves the UI with hot reload on
  http://localhost:5173 and talks to a running `ohs app`. `npm run dev:mock` needs no backend.
* The design and the API are in [docs/DESKTOP_APP_PLAN.md](docs/DESKTOP_APP_PLAN.md) and
  [docs/APP_API.md](docs/APP_API.md).

## 2. Flash the Pico firmware

The Pico runs MicroPython plus two files from `firmware/`. Follow
[firmware/README.md](../firmware/README.md): flash the MicroPython `.uf2` once, then copy
`main.py` and `lsm6dsox.py` with `mpremote`. It also has a manual serial-terminal test and the
pin map.

## 3. First run

1. Plug in the stereo camera and the Pico. Check the laser kill toggle on the back cover.
2. Find the devices:

   ```
   ohs devices
   ```

   It lists the camera indices (the stereo camera usually shows a wide default mode such as
   2560x800 and is marked "side-by-side stereo?") and the Pico's serial port. Sizes shown are each
   camera's *default* mode; `ohs` asks for `camera.width x camera.height` (2560x800).
3. If the camera is not index 0, or several Picos/serial devices are attached, create a settings
   file and set them:

   ```
   ohs config --init          # writes ~/.ohs/config.toml with every setting commented
   ohs config --show          # prints the settings actually in use
   ```

   Edit `[camera] index = 1` or `[pico] port = "COM5"`.
4. Run the self-test with the scanner pointed at a wall 20-40 cm away:

   ```
   ohs selftest
   ```

   | Check | PASS means |
   |---|---|
   | camera, camera fps, camera image | 2560x800 frames at the requested rate, image neither black nor saturated, left and right differ |
   | pico | the firmware answers HELLO (`ohs-fw`) |
   | imu rate, imu values | the IMU streams at 416 Hz and reads about 9.8 m/s^2 at rest |
   | clock sync | round trip under 2 ms, so timestamps are good to better than 1 ms |
   | vertical / horizontal line laser, dot projector | each switched on for 1 s **(confirm visually)**; PASS also needs the camera to see it light up |
   | trigger | press and release the trigger when asked |

   The summary ends with `Overall: PASS`, `WARN` or `FAIL`; the exit code is 1 on FAIL. Options:
   `--seconds N` (measuring time), `--no-camera`, `--no-pico`, `--no-emitters`.
5. Look at the live image:

   ```
   ohs preview
   ```

   Without a calibration you see the raw left | right images. Keys: `1`/`2`/`3` toggle the
   vertical laser, horizontal laser and dot projector, `SPACE` all emitters, `+`/`-` exposure, `s`
   swap left/right, `l` laser-line overlay, `d` depth colour map (needs a calibration), `r` raw or
   rectified, `g` guide grid (for aligning the lasers; in rectified images a feature must lie on
   the same row in both halves), `q` quit.

## 4. Settings

`~/.ohs/config.toml` (TOML). Use `--config PATH` or the `OHS_CONFIG` environment variable for
another file. Every setting is optional; a missing file means "all defaults". Unknown keys print a
warning with a "did you mean" hint and are ignored. Wrong types stop with an error that names the
key. Use `"auto"` for an optional value (exposure, port, voxel sizes) or leave it out.

| Section | Main settings |
|---|---|
| `[paths]` | `calibration` (default `~/.ohs/calibration.json`), `scans_dir` (default `~/OpenHandScan/scans`) |
| `[camera]` | `index`, `backend` (`auto`/`dshow`/`msmf`/`v4l2`), `width`, `height`, `fps`, `fourcc`, `exposure`, `gain`, `swap_lr` |
| `[pico]` | `port`, `imu_rate_hz` (104/208/416/833), plus advanced timing |
| `[emitters]` | `markers_dot_level` (25 %), `[emitters.profiles]`: `[vertical, horizontal, dots]` in percent per profile (`scan`, `stereo`, `lasers`, `imu`, `check`, `lines`, `dots`, `off`, plus your own) |
| `[live]` | `scale` (0.5 = 640x400), `depth_min_mm` / `depth_max_mm` (150-450), `voxel_mm` (auto = 2 mm) |
| `[process]` | `voxel_mm` (auto = 1 mm at 1280x800), `frame_step`, `poisson`, `export` (`ply`, `stl`, `obj`) |
| `[board]` | ChArUco board (11 x 8 squares, 20 mm squares, 15 mm markers, `DICT_5X5_100`) and `paper` (`letter`/`a4`) |
| `[markers]` | `tag_mm` (20): side of the black square of the printed AprilTags |

The global options `--config PATH`, `--calib PATH` (another calibration file) and `-v` / `-vv`
(log output) work before or after the command name.

## 5. Print the targets

```
ohs printables print/
```

writes:

* `charuco_board.pdf` / `.png`: the calibration board, with a 100 mm scale bar;
* `apriltags.pdf` / `.png`: 48 AprilTag stickers (tag36h11, 20 mm) for marker mode;
* `projector_mask/`: the mask for the LED pattern projector option (see below).

How to print:

* **Print at 100 % / "actual size"**. Turn off "fit to page" / "shrink to fit". Then **check the
  100 mm scale bar** with calipers or a good ruler. If it is not 100.0 mm, fix the printer
  settings and print again. Do not compensate in the settings.
* **Glue the board flat to glass**, for example the glass of an 8.5 x 11 in picture frame (spray
  adhesive, no bubbles). The calibration assumes a perfectly flat board; a wavy sheet of paper
  gives a bad calibration. Matte paper avoids glare.
* Print the AprilTags on **matte removable label paper** and cut along the dashed lines.
* On A4 paper use `--paper a4` and set `[board] paper = "a4"`: the laser calibration uses the white
  page margin around the board.
* Options: `--dpi` (300), `--tag-mm` (default `markers.tag_mm`), `--tags N` (number of stickers).

**Two projector options.** The dot pattern that gives stereo texture on plain surfaces comes from
one of two projectors (hardware details in the [main README](../README.md)):

* **laser + DOE**: a 650 nm dot laser and a 10,000-dot diffractive element;
* **LED + printed mask**: an LED, a diffuser, a printed random-dot mask and an M12 lens.

The software does not care which one is fitted: both are switched by the Pico's dots channel
(GP6), and every setting and command treats them the same. For the LED option, print
`projector_mask/projector_mask_sheet.pdf` at 100 % on laser transparency film (1200 dpi), or send
the Gerber file (`projector_mask_film.gbr`) to a photoplot service for a sharper film. Options:
`--mask-dpi` (1200), `--mask-dot-um` (42, the printed dot size), `--no-mask`. Generating the
sheet takes about 20 s.

## 6. Calibrate

Calibrate once after assembly, then again whenever something moved: lens focus, a laser was
re-aimed, the faceplate was opened. Run the quick **check** before an important scan, because
heat in a car can shift the focus.

Order: **stereo -> lasers -> imu**. Each step captures a recording with a live overlay, runs the
calibration, prints a report and updates `~/.ohs/calibration.json`. The previous file is kept as
`calibration.json.bak`.

| Step | Emitters | Capture | Guidance |
|---|---|---|---|
| `ohs calibrate stereo` | all off | SPACE or trigger per view | **15-25 views, 200-500 mm, tilts 20-45 deg, cover corners and edges** |
| `ohs calibrate lasers` | lines on, dots off | SPACE or trigger per pose | **6-10 poses, 150-450 mm, +-30 deg tilt, both lines crossing the white page margin** |
| `ohs calibrate imu` | all off | continuous: SPACE or trigger starts and stops | **hold still 2 s, then rotate about all axes for 20-60 s** |
| `ohs calibrate check` | lines on, dots off | SPACE or trigger per view | 3-5 views at different distances; reports, never writes |

Keys: `SPACE` / trigger capture (imu: start/stop), `q` finish and calibrate, `ESC` abort,
`+`/`-` exposure.

**Stereo.** Hold the glass board (or keep it fixed and move the scanner). The overlay shows the
detected corners in both images, an 8 x 8 **coverage map** (green cells already have corners
from captured views), the board distance and its tilt. Tilt the board 20-45 deg in different
directions, and move it so that, over all views, the corners reach the image corners and edges.
Hold still when you press: motion blur raises the error. A view is only taken when both cameras
see at least 8 common corners. Good results: `rms` below 0.5 px, coverage above 60 %. The verdict
line says what to improve.

A new stereo calibration **invalidates the laser planes and the IMU calibration**: they are
expressed in the old rectified camera frame. They are dropped, with a warning, unless you pass
`--keep`. Re-run `lasers` and `imu` afterwards.

**Lasers.** Needs the stereo calibration. The two line lasers are on and the view is rectified.
Each pose needs the board pose (corners found) and **both laser lines crossing the white page
margin** around the printed pattern. Lines on the black-and-white squares are biased; the HUD counts the
line points on the page and warns when a line misses it. Use different distances (150-450 mm) and
tilts (up to +-30 deg) so the planes are well determined.

**IMU.** Needs the stereo calibration and the Pico. Put the board where the camera sees it, press
SPACE or the trigger, **hold still for 2 s** (the HUD counts down), then **rotate the scanner
about all three axes** (roll, pitch, yaw; +-20-40 deg, about once per second) for 20-60 s,
keeping the board in view. Press again to stop (it stops by itself after 62 s, `--seconds`).

**Options.** `--views N` finishes after N views. `--from REC` skips the capture and calibrates an
existing recording, for example one from `ohs record --profile imu`. `--no-save` only prints the
result. `--step N` uses every N-th frame. `--out DIR` chooses the capture folder (default
`~/OpenHandScan/scans/calibration/<step>-<time>`). The full report is saved as
`calib_report_<step>.json` in the capture recording.

## 7. Scan

```
ohs scan                     # markerless (default)
ohs scan --mode markers      # with AprilTag stickers
ohs scan --process           # also build the final mesh afterwards
```

The window shows the rectified left image with the laser lines (red = vertical, green = horizontal),
a depth inset (top right), **TRACKING OK / LOST** with the reason, the live and camera frame rates,
the laser point count, the recording time and the emitter levels. **The trigger (or SPACE) starts
the scan**: the emitters switch on and the raw stream is recorded. **Press it again to stop**;
`q`, `ESC` or closing the window also stop and save. Ctrl-C stops too, and the recording is still
closed properly. Recordings go to `~/OpenHandScan/scans/scan-<date>-<time>` (`--out DIR` to choose).

**Markerless or markers?**

* **Markerless** (default) tracks the scanner against the model built so far, using stereo depth
  on the dot pattern plus the gyro. It is good for parts with shape: brackets, trim pieces,
  engine-bay components, interiors.
* **Markers** is for large, smooth, featureless areas (a whole door, a hood, a roof) where
  markerless tracking can slide. Stick the 20 mm AprilTags (`ohs printables`) 5-10 cm apart, so
  that at least 2-3 are always in view, and don't move them during the scan. In live marker mode
  the dot projector runs dimmed (`emitters.markers_dot_level`, 25 %). AprilTags are not detected
  at the live 640x400 resolution under full-brightness dots; the final processing at full
  resolution copes with them.

**Prepare the surface.** Glossy paint, dark plastic, chrome and glass reflect or swallow the dots
and the laser lines. Spray them with **AESUB Blue** (it evaporates within hours), or another
matte scanning spray.

**Technique.**

* Keep the scanner **200-350 mm** from the surface. The laser lines cross on the centre axis at
  about 250 mm, the sweet spot. The live depth range is 150-450 mm.
* Move **slowly**, in **overlapping sweeps**, like spray painting. Keep the laser "+" on the
  surface and part of the already scanned area in view.
* If the HUD says **TRACKING LOST**, go back slowly to an area that was already scanned and hold
  it there until it says OK again.

**Live versus final quality.** The live view processes half-resolution images (640x400, 2 mm
voxels) only to give feedback, and drops frames when the PC falls behind. Every raw frame, the IMU
and the trigger events are recorded at full resolution, with a copy of the calibration. The final
model comes from `ohs process` at full resolution (1 mm voxels, laser points at 0.2 mm). You can
reprocess a recording later with other settings.

`ohs record <folder>` makes a raw recording without live processing (`--profile NAME` picks the
emitter levels from `[emitters.profiles]`), for example to calibrate or process later.

## 8. Process and export

```
ohs process ~/OpenHandScan/scans/scan-20261108-141500
```

writes to `<recording>/result/` (`--out DIR` to choose):

| File | Content |
|---|---|
| `mesh.ply`, `mesh.stl` (`mesh.obj` with `export = ["ply", "stl", "obj"]`) | the fused surface |
| `laser_points.ply` | the laser-line points, the most accurate data |
| `trajectory.csv` | the scanner pose of every frame |
| `report.json` | frame counts, tracking failures, timings, mesh statistics (and the error against the ground truth for simulated recordings) |

**All units are millimetres.** Set the import units to mm in your CAD or mesh tool (MeshLab,
CloudCompare, Blender, Fusion). The coordinate frame is the camera at the first tracked frame
(markerless) or at the frame that started the marker map (markers).

Options: `--mode` (default: the recording's mode), `--voxel MM` (smaller = finer, slower, more RAM),
`--step N` (every N-th frame, faster), `--poisson` (watertight Poisson mesh), `--start` / `--stop`
(frame range). The calibration comes from the recording itself; `--calib PATH` overrides it.

## 9. Try it without hardware

```
ohs sim demo                 # simulated scan: 60 frames at 1280x800, about 1 minute
ohs process demo             # final mesh in demo/result/, report with the error vs. ground truth
ohs scan --replay demo       # the live view, replaying the recording in real time
ohs preview --replay demo    # raw/rectified view, laser overlay (l), depth (d)
```

Scenes: `--scene sphere_on_plane | car_panel | bracket`. `--size 640x400` and `--frames 30` are
faster. `--realistic` adds lens distortion and imperfect stereo geometry. Simulated recordings
contain their true calibration, so no calibration is needed. Replay never uses the Pico: the IMU
data comes from the recording.

## 10. Troubleshooting

**Camera not found, wrong camera, low frame rate.** Run `ohs devices` and set `camera.index`.
Close other programs that use the camera. 2560x800 needs MJPG (`fourcc = "MJPG"`). A USB 2 hub or
an overloaded port lowers the frame rate, and so does a long exposure. `ohs selftest` shows the
mode and fps actually delivered.

**Windows camera backend and exposure.** `backend = "auto"` tries DirectShow (`dshow`) first and
then Media Foundation (`msmf`). MSMF opens slowly and often ignores exposure settings, so prefer
DirectShow. **DirectShow exposure is log2 of the exposure time in seconds**, in whole steps:
`-5` = 1/32 s, `-7` = 1/128 s (7.8 ms), `-8` = 3.9 ms, `-9` = 2 ms, `-10` = about 1 ms. Short
exposures avoid motion blur but darken the image. Try values live with `+`/`-` in `ohs preview`,
then write the one you like into `[camera] exposure = -8`. On Linux (V4L2) the unit is 100 us
(`50` = 5 ms). Leave `exposure` out to keep the camera's automatic setting.

**Left and right swapped (`swap_lr`).** Symptoms: no depth at all in `ohs preview` (key `d`),
nothing tracks, or stereo calibration fails or gives a strange baseline. In the raw preview, a
near object must appear further *right* in the left half than in the right half. Press `s` in
`ohs preview` to try swapping. If depth works afterwards, set `[camera] swap_lr = true`, then
recalibrate stereo.

**Tracking lost.** The HUD shows why:

* `no_depth` / `bad_depth`: too close or too far (stay at 200-350 mm), or a shiny or dark surface
  (spray it), or the dot projector is off (kill switch, `ohs selftest`).
* `few_inliers` / `low_fitness` / `jump`: moving too fast, or pointing at something new. Slow
  down and go back to the scanned area.
* Smooth, flat, featureless areas let the scanner "slide": use marker mode.
* Without an IMU calibration there is no gyro prior: run `ohs calibrate imu`.

**Laser focus and roll alignment.** In `ohs preview`, switch on one line (`1` vertical, `2`
horizontal) and the overlay (`l`) against a flat wall at about 25 cm. Focus each laser module
until the line is thin and sharp at that distance. Then turn the module in its bore until the
right-hand laser's line is exactly vertical and the top laser's line exactly horizontal (use the
guide grid, `g`). Tighten the pinch screw gently. After any change of focus or roll, run
`ohs calibrate lasers` again.

**AprilTags not detected (marker mode).** Check that they were printed at 100 % and that
`markers.tag_mm` is the measured side of the black square. Use matte paper, avoid glare, and keep
at least 2 tags in view, close enough to look sharp. The live view dims the dots for detection
(`emitters.markers_dot_level`); if the live view still loses tags, dim them further. The full-
resolution offline processing (`ohs process`) detects tags much better than the live view.

**Pico.** "no Pico found": use a data USB cable, check `ohs devices` and set `pico.port`. "no
HELLO reply": the firmware is not running (see the firmware README). `imu=NONE`: check the IMU
wiring (SDA GP4, SCL GP5). Emitters that switch off by themselves after 1.5 s mean the PC stopped
talking to the Pico; that is the safety watchdog doing its job.

**Calibration quality.** A high `rms` usually comes from motion blur (hold still, shorter
exposure), soft focus, or a board that is not flat. Low coverage: add views near the image corners.

**Processing.** "this needs Open3D": `pip install -e .[fusion]`. "calibration is for WxH": the
camera mode changed since calibration; use the calibration of that mode. Out of memory: use a
larger `--voxel`.

## 11. Laser safety

The two line lasers are **Class 3R**, and so is the dot laser of the laser + DOE projector.

* **Never look into a beam**, and never point the scanner at anyone's eyes, including pets'.
  Beware of specular reflections from chrome, glass and mirrors.
* Use the **kill toggle** on the back cover whenever you are not scanning. Optional 650 nm safety
  glasses (OD 2+) are listed in [PARTS.md](../PARTS.md).
* The software switches the emitters on only while scanning or recording, during the laser
  calibration and check, for 1 s each in the self-test, and when you press `1`/`2`/`3`/`SPACE`
  in the preview. It switches them off at the end, on errors, on Ctrl-C and when the window
  closes. The firmware also switches them off 1.5 s after the PC stops talking to it.
* The LED + printed mask projector is not a laser, but it is bright: don't stare into it.

## 12. Reference

**Commands**

| Command | Purpose |
|---|---|
| `ohs devices` | list cameras and the Pico's serial port |
| `ohs selftest` | hardware check with a PASS/WARN/FAIL summary |
| `ohs config --init` / `--show` | write the commented settings template / print the settings in use |
| `ohs printables DIR` | board, AprilTags and LED projector mask to print |
| `ohs preview` | live raw/rectified view with laser overlay and depth |
| `ohs record DIR` | raw recording; the trigger or SPACE starts and stops it |
| `ohs calibrate stereo/lasers/imu/check` | interactive calibration (`--from REC` to reuse a recording) |
| `ohs scan` | live scan, always recording (`--mode`, `--process`, `--replay`, `--no-gui`, `--max-frames`) |
| `ohs process REC` | full-quality mesh, laser points and trajectory |
| `ohs sim DIR` | simulated scan recording |

**Live keys**

| Window | Keys |
|---|---|
| preview | `1` `2` `3` emitters, `SPACE` all emitters, `+` `-` exposure, `s` swap L/R, `l` lines, `d` depth, `r` raw/rectified, `g` guides, `q` quit |
| scan, record | `SPACE` start/stop (same as the trigger), `+` `-` exposure, `q` / `ESC` stop and quit |
| calibrate | `SPACE` capture (imu: start/stop), `q` finish and calibrate, `ESC` abort, `+` `-` exposure |

`--no-gui` replaces the window with a status line per second (the trigger still works; a
calibration capture then finishes with `--views N`), and `--no-pico` runs without emitters, IMU
and trigger. A recording is a plain folder (raw PNG frames,
`imu.csv`, `events.csv`, `meta.json`, `calibration.json`), described in ARCHITECTURE.md section 6.

**Exit codes:** 0 success, 1 error (or a failed self-test or calibration check), 2 wrong usage,
130 interrupted.
