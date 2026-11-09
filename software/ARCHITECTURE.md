# OpenHandScan software: architecture and work plan

This is the contract every module is written against. If something here is ambiguous, pick the
simplest reading, document it in your module docstring, and mention it in your report.

## 1. What the software does

```
 ELP stereo cam (2x OV9281, synced, side-by-side 2560x800 @ up to 60-100 fps, one USB)
 Pico (MicroPython): emitters (V-line laser, H-line laser, dot projector), trigger, IMU @ 416 Hz
        |                                   |
        v                                   v
 devices/camera.py                    devices/pico.py  (serial protocol, clock sync)
        \__________________ recording (io/recording.py) _________________/
                                   |
              +--------------------+---------------------+
              | live: decimated (half-res) for feedback | offline: full quality
              +--------------------+---------------------+
                                   v
   vision/rectify -> vision/depth (SGBM on dot pattern) -> track/markerless (ICP + TSDF, Open3D)
                  -> vision/laser (extract "+" lines, triangulate on calibrated planes) -> track/fusion LaserCloud
                  -> track/markers (AprilTag stereo map, optional mode)
   track/imu_prior (gyro rotation prior for ICP)
                                   v
   track/export: mesh (PLY/STL/OBJ), point clouds (PLY)
```

Modes:
- **markerless** (default): stereo depth on every frame + frame-to-model ICP + TSDF fusion. The gyro gives the rotation prior.
- **markers**: AprilTag stickers (tag36h11, printed on matte label paper) give drift-free poses. Depth is still fused with TSDF.

Live scanning **always records** the raw stream. The live pipeline runs at reduced resolution for feedback.
The final model comes from `ohs process <recording>` at full quality, so scans can be reprocessed with better settings later.

## 2. Conventions (mandatory)

- **Units:** millimetres for every 3D quantity (points, translations, depths, plane offsets, voxel sizes). Seconds for time. Gyro in rad/s, accel in m/s^2.
- **Camera frame `C`:** the **rectified left camera**, OpenCV optical axes (x right, y down, z forward). All per-frame 3D data (depth maps, laser points, laser planes, marker corners) is expressed in `C`. All image processing after rectification uses the ideal pinhole `K = calib.stereo.K_rect` with zero distortion.
- **Raw vs rectified:** the camera delivers raw (distorted, unrectified) left/right images. `vision.rectify.Rectifier` maps them to rectified images. Nothing else should ever touch raw images (calibration excepted).
- **Poses:** `T_wc` is a 4x4 float64 matrix with `p_w = T_wc @ p_c` (homogeneous). The world frame `W` is the camera frame of the first successfully tracked frame (markerless), or the marker map frame (markers).
- **Stereo extrinsics:** OpenCV `stereoCalibrate` convention, `x_right = R @ x_left + T`. With parallel cameras `T = (-baseline, 0, 0)`.
- **Laser planes:** `n . X + d = 0`, `X` in frame `C` (mm), `|n| = 1`, sign normalised so `d <= 0`. Names: `"vertical"` (the sheet that appears as a near-vertical line in the image, mounted to the right of the cameras) and `"horizontal"` (near-horizontal line, mounted above).
- **IMU:** `v_c = R_ci @ v_i`. Camera timestamp = IMU host timestamp + `time_offset`. Gyro bias (IMU frame) is subtracted before use.
- **Time:** host timestamps come from `time.perf_counter()` in seconds. Each frame's `t_host` is taken right after the grab returns.
- **Images:** grayscale `uint8`, shape `(H, W)`. Per-camera nominal size is 1280x800. Side-by-side frames are 2560x800 with left on the left (unless `swap_lr`).

## 3. Package layout and ownership

```
software/
  pyproject.toml            (core)   ARCHITECTURE.md (core)   README.md (F)
  ohs/
    types.py                (core)   dataclasses + calibration JSON I/O
    geometry.py             (core)   SE3/SO3, planes, rays, Kabsch, gyro integration, depth->points
    io/recording.py         (core)   recording folder read/write
    devices/camera.py       (A)      StereoCamera (OpenCV UVC), list_cameras
    devices/pico.py         (A)      Pico serial client
    vision/rectify.py       (D)
    vision/depth.py         (D)
    vision/laser.py         (D)
    calib/board.py          (C)      ChArUco + AprilTag printables and detection
    calib/stereo.py         (C)
    calib/laser_planes.py   (C)
    calib/imu.py            (C)
    track/imu_prior.py      (E)
    track/markerless.py     (E)      Open3D frame-to-model ICP + TSDF
    track/markers.py        (E)
    track/fusion.py         (E)      LaserCloud accumulation
    track/export.py         (E)      mesh/point writers, Poisson, cleanup
    sim/nominal.py, scene.py, render.py, trajectory.py, imu.py, datasets.py   (B)
    app/cli.py, config.py, live.py, process.py, selftest.py                  (F, after A-E)
  tests/                    test_<area>_*.py, owned by the same agent as the module
firmware/                   (A) MicroPython for the Pico: main.py, lsm6dsox.py, README.md
```

**Ownership rule:** edit only files you own. If a core file (`types.py`, `geometry.py`,
`io/recording.py`) has a bug or lacks something you need, put a local helper in your own module
and describe the issue in your final report. Do not edit core files.

## 4. Core API (already implemented, import these)

`ohs.types`: `StereoFrame(index, t_host, left, right)` (`from_sbs`, `to_sbs`), `ImuSample(t_host, t_device, gyro, accel)`,
`Event(t_host, kind, value)`, `StereoCalibration` (`build(...)`, `K_rect`, `baseline`, `scaled(f)`),
`LaserPlane(name, normal, d, rms, n_points)` (`distance`), `ImuCalibration(R_ci, time_offset, gyro_bias)`,
`ScannerCalibration(stereo, lasers, imu)` (`save(path)`, `load(path)`), `TrackResult(T_wc, ok, info)`.

`ohs.geometry`: `make_se3`, `invert_se3`, `transform_points`, `skew`, `so3_exp`, `so3_log`,
`rotation_angle_deg`, `pose_error`, `integrate_gyro`, `pixels_to_rays`, `project_points`,
`ray_plane_intersect`, `fit_plane`, `fit_plane_ransac`, `kabsch`, `depth_to_points`.

`ohs.io.recording`: `RecordingWriter`, `RecordingReader` (format in section 6).

## 5. Module contracts

### A. Devices + firmware
`ohs/devices/camera.py`
```python
@dataclass
class CameraParams:
    index: int = 0
    backend: str = "auto"          # "auto" | "dshow" | "msmf" | "v4l2" | "any"
    width: int = 2560              # full side-by-side frame
    height: int = 800
    fps: float = 60.0
    fourcc: str = "MJPG"
    exposure: float | None = None  # backend-native units; None = leave camera default
    gain: float | None = None
    swap_lr: bool = False
    second_index: int | None = None  # fallback: two separate (unsynced) UVC cameras
class StereoCamera:                # context manager; background grab thread; read() returns newest frame
    def __init__(self, params: CameraParams): ...
    def open(self) -> None: ...
    def close(self) -> None: ...
    def read(self, timeout: float = 1.0) -> StereoFrame: ...   # never returns the same index twice; raises TimeoutError
    def set_exposure(self, value: float) -> None: ...
    def set_gain(self, value: float) -> None: ...
    @property
    def info(self) -> dict: ...    # actual width/height/fps/backend/fourcc
def list_cameras(max_index: int = 8) -> list[dict]: ...
```
Windows needs DirectShow (`cv2.CAP_DSHOW`, exposure is log2 seconds, e.g. -7 = 1/128 s) and MSMF. Linux needs V4L2.
Convert colour frames to grayscale. Document exposure semantics per backend.

`ohs/devices/pico.py`
```python
@dataclass
class PicoParams:
    port: str | None = None        # None = auto-detect USB VID 0x2E8A
    baud: int = 115200
    imu_rate_hz: int = 416
    heartbeat_s: float = 0.4
class Pico:                        # context manager; reader thread; heartbeat thread while any emitter is on
    def open(self) -> dict: ...    # returns HELLO info
    def close(self) -> None: ...   # turns emitters off first
    def set_emitters(self, vertical: float, horizontal: float, dots: float) -> None: ...   # percent 0..100
    def all_off(self) -> None: ...
    def start_imu(self, rate_hz: int | None = None) -> None: ...
    def stop_imu(self) -> None: ...
    def read_imu(self) -> list[ImuSample]: ...   # drains queue; t_host from clock sync
    def read_events(self) -> list[Event]: ...    # kind "button" ("down"/"up") or "temperature" ("31.25", degC)
    def sync_clock(self, n: int = 16) -> tuple[float, float]: ...   # (offset_s, best_rtt_s); auto-resync every ~5 s
def find_pico_port() -> str | None: ...
```

**Serial protocol** (ASCII lines, `\n`-terminated, 115200 baud nominal over USB CDC):

| Host -> Pico | Reply |
|---|---|
| `HELLO` | `OK HELLO ohs-fw <ver> imu=<LSM6DSOX\|NONE>` |
| `EMIT <v> <h> <d>` (ints 0..100, % PWM) | `OK EMIT <v> <h> <d>` |
| `IMU <rate_hz>` (0 = stop; supported 104/208/416/833) | `OK IMU <rate> <gyro_rad_per_lsb> <accel_ms2_per_lsb>` |
| `TIME` | `T <t_us>` (microseconds; the firmware unwraps `ticks_us` into a 64-bit counter and sends that everywhere) |
| `HB` | (no reply) |
| `STATUS` | `S emit=<v>,<h>,<d> imu=<rate> btn=<0\|1> up=<s>`, plus ` temp=<degC>` once a temperature was read (1.1.0+) |

Asynchronous Pico -> host lines: `I <t_us> <gx> <gy> <gz> <ax> <ay> <az>` (raw int16 counts, IMU frame),
`B <t_us> <0|1>` (debounced button, 1 = pressed), `C <t_us> <centi_degC>` (the IMU chip's temperature
in hundredths of a degree, about once a second while the IMU streams; firmware 1.1.0+), `E <msg>` (error),
`L <msg>` (log). Unknown commands reply `E unknown <cmd>`.

**Safety:** emitters are off at boot. Any emitter turns off if no host line arrives for 1.5 s. Values are clamped to 0..100.

Pins: V-laser GP2, H-laser GP3, dot projector GP6 (each drives an NPN low-side switch, PWM ~10 kHz),
trigger GP15 (to GND, internal pull-up), IMU I2C0 SDA GP4 / SCL GP5 @ 400 kHz, LSM6DSOX address 0x6A (fallback 0x6B).
IMU config: gyro +-1000 dps, accel +-8 g, both at the requested ODR. Timestamps must be accurate to better than 1 ms.

### B. Simulator (`ohs/sim/`)
- `nominal.py`: `nominal_calibration(image_size=(1280, 800), fx=720.0, baseline=60.0) -> ScannerCalibration` and `nominal_emitters() -> dict`, built from the CAD geometry in section 7.
- `scene.py`: vectorised ray casting of primitives (bounded plane/rectangle, sphere, box, cylinder) with albedo (constant, procedural low-contrast noise, or an image texture for calibration boards). Presets: `sphere_on_plane`, `car_panel` (large gently curved panel with a crease and a hole), `bracket`, and `charuco_board(spec, T_wb)`.
- `render.py`: `Renderer(calib, emitters, scene, params).render(T_wc) -> (left_raw, right_raw, depth_left_mm)`. It models ambient light, the dot projector (a pinhole "projector camera" with a random-dot image, 10k dots in 80 x 64.4 deg, 1/r^2 falloff), the two laser sheets (Gaussian cross-section, finite fan angle, line width growing with distance), shadows from each emitter, sensor blur, noise, and saturation. Flags turn dots, lines and ambient on/off.
- `trajectory.py`: smooth hand-held-like trajectories around a target, always looking at it, 150-400 mm away.
- `imu.py`: synthesises `ImuSample`s at 416 Hz from a continuous trajectory (gyro + specific force incl. gravity, bias, noise, a device clock offset), given `R_ci` and `time_offset`.
- `datasets.py`: `make_scan_recording(out_dir, scene="sphere_on_plane", n_frames=60, fps=30, image_size=(1280, 800), seed=0) -> Path` writes a full recording (frames + imu + events + calibration.json + ground_truth). Also `make_stereo_calib_set(...)` and `make_laser_calib_set(...)` returning images plus the true calibration.

### C. Calibration (`ohs/calib/`)
- `board.py`: `CharucoSpec(squares_x=11, squares_y=8, square_mm=20.0, marker_mm=15.0, dictionary="DICT_5X5_100")`, `make_charuco_board(spec)`, `render_charuco_page(spec, path, paper="letter", dpi=300)` (exact-scale PDF + PNG), `render_apriltag_sheet(path, tag_mm=20.0, ids=range(48), paper="letter", dpi=300)`, `detect_charuco(img, spec) -> (corners (N,2) float32, ids (N,))`, `estimate_board_pose(corners, ids, spec, K, D=None) -> (ok, T_cb)` (board -> camera).
- `stereo.py`: `calibrate_stereo(pairs: list[tuple[left, right]], spec, image_size) -> (StereoCalibration, report)`. Report fields: rms, per-view errors, views used, image-coverage fraction. Per-camera intrinsics are calibrated first, then the stereo extrinsics.
- `laser_planes.py`: `LaserBoardObservation(T_cb, board_polygon_uv, line_uv: dict[str, (N,2)])` in rectified frame `C`; `board_points_from_line(obs, K, name) -> (N,3)`; `fit_laser_planes(observations, K, names=("vertical","horizontal"), ransac_thresh_mm=0.5) -> (dict[str, LaserPlane], report)`; `check_laser_planes(planes, observations, K) -> report`.
- `imu.py`: `calibrate_imu_camera(cam_t, cam_R_wc, imu_t, gyro, max_offset_s=0.2) -> (ImuCalibration, report)`. Estimate gyro bias, time offset (cross-correlation of angular speed) and `R_ci` (Kabsch on angular velocity), with a final joint refinement.

### D. Per-frame vision (`ohs/vision/`)
- `rectify.py`: `Rectifier(calib: StereoCalibration, scale=1.0)` with `.calib` (geometry at this scale), `.K`, `.baseline`, `.rectify(left, right)`, `.rectify_left(left)`.
- `depth.py`: `DepthParams(min_depth_mm=150, max_depth_mm=450, block_size=5, ...)`, `DepthEstimator(K, baseline_mm, params).compute(left_r, right_r) -> depth_mm float32 (0 = invalid)` and `.disparity(...)`. Use OpenCV SGBM with a left-right check and speckle filtering. Derive the disparity search range from the depth range. It must work on the random-dot pattern with bright laser lines present. `DepthParams.backend` (`"auto"` / `"cpu"` / `"gpu"`) picks the matcher; `.backend` and `.device` say which one runs.
- `depth_gpu.py`: `GpuStereoMatcher`, the GPU twin behind `DepthEstimator` (OpenCL via `ohs.compute`): ternary census, 4-path SGM, the same uniqueness / left-right / median / speckle / sub-pixel steps and output contract.
- `laser.py`: `LaserParams`, `LaserLines(vertical (N,2), horizontal (M,2), vertical_strength, horizontal_strength)`, `extract_laser_lines(img_r, params) -> LaserLines` (sub-pixel ridge centres; robust to the dot pattern; distinguish the two lines by orientation; reject the crossing zone where they overlap; tolerate up to +-30 deg tilt and broken lines), `triangulate_plane(uv, K, plane) -> (N,3)` (NaN for invalid), `stereo_check(...)` (reject points whose predicted right-image position has no laser ridge), and `laser_points(left_r, right_r, K, baseline, planes, params) -> dict[str, (N,3)]`.

### E. Tracking + fusion (`ohs/track/`)
- `imu_prior.py`: `GyroPredictor(imu_calib).add(samples)`, `.relative_rotation(t0, t1) -> R_c0_c1` (`v_c0 = R @ v_c1`, camera timestamps), `.covers(t0, t1) -> bool`.
- `markerless.py`: `FusionParams(voxel_mm=1.0, trunc_mult=4.0, depth_min_mm=120, depth_max_mm=500, device="auto", ...)`, `MarkerlessTracker(K, image_size, params)` with `.track(depth_mm, intensity, t, R_prior=None) -> TrackResult` (frame-to-model ICP; integrates on success; the first frame defines the world), `.integrate(depth_mm, intensity, T_wc)` (external pose), `.extract_mesh() -> (V, F, N)`, `.extract_points() -> (P, N)`, `.trajectory` (list of (t, T_wc, ok)), `.reset()`. On tracking loss, report `ok=False` with a reason, skip integration, and try to recover from the last good pose. Built on Open3D tensor `t.pipelines.slam` or equivalent. The public API is in mm.
- `markers.py`: `MarkerParams(dictionary="DICT_APRILTAG_36h11", tag_mm=20.0, min_tags=2)`, `MarkerTracker(K, baseline_mm, params).track(left_r, right_r, t) -> TrackResult`. It detects tags in both rectified images, triangulates corners by stereo, poses via Kabsch to the map, grows the map, and has `.map` and an optional `.refine_map()`.
- `fusion.py`: `LaserCloud` with `.add(pts_c, T_wc, name)`, `.points(voxel_mm=0.2)`, `.save_ply(path)`.
- `export.py`: `save_mesh(path, V, F, N=None)` (binary PLY/STL, OBJ; numpy only), `save_points_ply(path, P, N=None, colors=None)`, `poisson_mesh(P, N=None, depth=9, density_quantile=0.02)` (Open3D), `clean_mesh(V, F, min_component_fraction=0.01)`.

### F. App (after A-E): `ohs` CLI
`ohs devices | selftest | preview | record | calibrate stereo|lasers|imu|check | scan | process | printables | sim | bench`
(`bench.py`: speed and accuracy on a standard simulated recording, CPU against GPU, JSON results.)
`live.py` runs threads (capture, Pico reader, processing at reduced scale, recorder) with an OpenCV preview window.
The trigger button starts/stops scanning. `process.py` produces the full-quality offline result from a recording.
`config.py` loads TOML (`tomllib`) merged over defaults. `README.md` is the user guide.

## 6. Recording folder format (implemented in `ohs/io/recording.py`)
```
<rec>/
  meta.json            format_version=1, created, source ("camera"|"sim"), image_size [w,h] per camera, ...,
                       temperature_c {first,last,min,max,n} when the recording has temperatures
  frames.csv           index,t_host,file
  frames/000000.png    side-by-side grayscale left|right (raw, unrectified)
  imu.csv              t_host,t_device,gx,gy,gz,ax,ay,az    (rad/s, m/s^2, IMU frame)
  events.csv           t_host,kind,value    kinds: button (down|up), session (pause|resume), cut ("t0,t1"),
                       temperature (IMU chip, degC, ~1 Hz; RecordingReader.temperature())
  calibration.json     optional: ScannerCalibration (true calibration for sim recordings)
  ground_truth/        optional (sim): poses.csv (index,t_host,16 values of T_wc row-major), depth/000000.npy
```

## 7. Nominal hardware geometry (from the CAD), in frame `C` (left camera), mm
- Right camera centre: (60, 0, 0). `fx = fy ~ 720`, `cx = 640`, `cy = 400` at 1280x800 (83 deg HFOV lens; true values come from calibration).
- Dot projector: origin (30, -42, -2), pointing +z, DOE field 80 x 64.4 deg, ~10k dots.
- Vertical-line laser: origin (116.4, 0, -1.4); its sheet contains the camera y axis; plane normal (0.9473, 0, 0.3205), d = -109.8. It crosses the midline x = 30 at z = 254.
- Horizontal-line laser: origin (30, -75.1, -1.2); its sheet contains the camera x axis; plane normal (0, -0.9594, 0.2822), d = -71.67. It crosses y = 0 at z = 254.
- Line laser fan angle: assume 60 deg full (sellers vary 60-90).
- IMU: on the back cover, ~30 mm behind the cameras. Nominal `R_ci = I` (the true value comes from calibration).
- Working range 150-450 mm, sweet spot ~250 mm.

## 8. Environment (installed, verified)
Windows 11, PowerShell 5.1, Python 3.13 venv `C:\Users\Garage\.venvs\ohs`: numpy 2.5.4, scipy 1.18.1,
**opencv-contrib-python 5.0.0** (aruco with CharucoDetector, stereoCalibrate, StereoSGBM and ximgproc are present),
**open3d 0.20.0**, pyserial, Pillow, pytest. OpenCV 5 and Open3D 0.20 are newer than most examples online.
Verify every API you use against the installed version (`help(...)`, small experiments) rather than from memory.

Camera facts found so far: ELP dual-lens synced OV9281 module (e.g. ELP-USB3DGS800P-V90). Its board is about 80 x 16.5 mm.
MJPEG side-by-side modes are 2560x800 @ 120 fps, 2560x720, 1600x600, 1280x480 and 640x240 @ 210 fps.
Lens options run from 63 to 86 deg. The baseline is unknown (assume 60 mm until measured).

## 9. Engineering rules
- Python >= 3.10, type hints, module docstrings, `logging` (no prints in library code), vectorised numpy, no global state.
- Optional heavy imports (open3d) go inside functions/classes so the rest imports without them.
- Tests: pytest, deterministic seeds, each test file < 60 s (mark longer ones `@pytest.mark.slow`), no hardware needed (`@pytest.mark.hardware` for tests that need it).
- Python environment: `C:\Users\Garage\.venvs\ohs` (kept outside the workspace because the workspace path is too long for Windows MAX_PATH). `ohs` is installed editable into it.
- Run tests with: `C:\Users\Garage\.venvs\ohs\Scripts\python.exe -m pytest software\tests\<file> -q` from the workspace root (Windows PowerShell 5.1).
- Keep file paths you create short (pytest `tmp_path` is fine). Do not create deep directory trees inside the workspace.
- Do not install packages. If you truly need one, explain why in your report.
