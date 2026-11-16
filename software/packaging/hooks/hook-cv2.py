"""PyInstaller hook for OpenCV: the standard hook (pyinstaller-hooks-contrib) without
``opencv_videoio_ffmpeg*.dll`` (29 MB): that library reads and writes video files and streams;
OpenHandScan only grabs frames from cameras (DirectShow/Media Foundation) and reads images."""
import importlib.util
import os
import runpy

_std = os.path.join(os.path.dirname(importlib.util.find_spec("_pyinstaller_hooks_contrib").origin),
                    "stdhooks", "hook-cv2.py")
_ns = runpy.run_path(_std)
binaries = [b for b in _ns["binaries"] if "ffmpeg" not in os.path.basename(b[0]).lower()]
hiddenimports = _ns["hiddenimports"]
excludedimports = _ns["excludedimports"]
datas = _ns["datas"]
module_collection_mode = _ns["module_collection_mode"]
