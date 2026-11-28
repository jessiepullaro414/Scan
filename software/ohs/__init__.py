"""OpenHandScan: software for an open-source handheld stereo + laser 3D scanner.

See ARCHITECTURE.md for conventions (units are millimetres, camera frame is the
rectified left camera with OpenCV axes, poses are T_wc).
"""
import os as _os

# OpenCV's Media Foundation hardware transforms make opening the stereo camera take 20 s per
# setting (80 s in all); without them it opens in under a second with the same frames and stamps.
# OpenCV reads this only when cv2 is first imported, so it is set here, before any ohs module
# imports cv2 (the CLI, the server, the tests and the packaged program all import ohs first).
# A value the user set is kept.
_os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

__version__ = "0.3.0"
