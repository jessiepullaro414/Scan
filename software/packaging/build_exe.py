"""Build OpenHandScan as a Windows program with PyInstaller: a folder with ``ohs.exe`` and what it
needs, no Python installation required (desktop app plan, M6). A first trial (2026-11-16); no
installer yet.

    python packaging/build_exe.py [--out DIR]

(run from ``software/`` with the engine's environment, after ``npm run build`` in ``ui/`` so that
the app's page is in ``ohs/server/static``; needs ``pip install pyinstaller``). The result is
``<out>/ohs/ohs.exe`` (default ``<out>`` = ``packaging/out``, which git ignores):

* ``ohs.exe app`` starts the desktop app, ``ohs.exe bench``, ``ohs.exe process <recording>`` and
  the other commands work as ``ohs ...`` does;
* what the engine and the app use goes in: Open3D, OpenCV, SciPy, PyOpenCL (the GPU is used when
  the graphics driver offers OpenCL), numba (compiles on the user's PC at the first scan with
  four lasers, then caches it in the user's cache folder), the app's server and window. Left out
  (``EXCLUDES``, ``hooks/``, ``rthook_stub_viewers.py``): Open3D's rendering resources, examples
  and ML parts, its web viewers' plotly and dash (empty stand-ins answer the import), IPython,
  OpenCV's ffmpeg library (video files), numba's tests, SciPy's stats/io/signal/integrate/
  interpolate and Pillow's AVIF codec.

Measured (``VALIDATION.md``, "A trial exe"): the first build with everything was about 720 MB
unpacked, 5 minutes to build; the same results as from Python. Signing, an installer and the
version in the file's properties are not done.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOFTWARE = HERE.parent


# Not used by the engine or the app; pulled in only by the libraries' optional parts (checked with an
# import trace of the checks in VALIDATION.md and by importing every ohs module with them blocked):
EXCLUDES = [
    # Open3D's web viewers (open3d/visualization/draw_plotly.py) and notebooks: plotly and dash with what
    # they bring (stand-ins in rthook_stub_viewers.py), IPython and Jupyter, pytest, setuptools
    "plotly", "dash", "narwhals", "flask", "IPython", "jedi", "parso", "pygments", "prompt_toolkit",
    "nbformat", "jsonschema", "jsonschema_specifications", "ipywidgets", "traitlets", "pytest", "_pytest",
    "setuptools", "pkg_resources", "matplotlib", "tkinter",
    # SciPy parts nothing here imports (the engine uses spatial, sparse, optimize.least_squares, ndimage)
    "scipy.stats", "scipy.io", "scipy.signal", "scipy.integrate", "scipy.interpolate", "scipy.cluster",
    "scipy.odr", "scipy.datasets",
    # Pillow: the Tk bridge and the AVIF codec (the engine draws boards and masks, reads and writes PNG/JPEG)
    "PIL.ImageTk", "PIL._avif", "PIL.AvifImagePlugin",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=HERE / "out", help="folder for the build and the result")
    a = ap.parse_args(argv)
    if not (SOFTWARE / "ohs" / "server" / "static" / "index.html").is_file():
        print("the app's page is not built: run `npm run build` in software/ui first", file=sys.stderr)
        return 2
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "PyInstaller", str(HERE / "ohs_entry.py"), "--name", "ohs", "--noconfirm", "--clean",
           "--console", "--distpath", str(out), "--workpath", str(out / "work"), "--specpath", str(out),
           "--icon", str(SOFTWARE / "ohs" / "server" / "assets" / "ohs.ico"),  # the app's logo (make_icon.py)
           "--paths", str(SOFTWARE), "--log-level", "WARN",
           "--additional-hooks-dir", str(HERE / "hooks"),  # Open3D and OpenCV without what is not used
           "--runtime-hook", str(HERE / "rthook_stub_viewers.py"),  # open3d imports plotly and dash: stand-ins
           "--collect-data", "ohs", "--collect-submodules", "ohs",  # the app's page and every engine module
           "--collect-all", "pyopencl",  # native libraries and OpenCL kernels' sources
           "--collect-submodules", "uvicorn",
           # the window: the launcher imports pywebview by name and pywebview its Windows backend (WinForms
           # through pythonnet) the same way, so PyInstaller sees neither; without them only webview's data
           # folder came along and `import webview` found an empty package
           "--hidden-import", "webview", "--collect-submodules", "webview", "--collect-data", "webview"]
    for name in EXCLUDES:
        cmd += ["--exclude-module", name]
    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=SOFTWARE)
    if r.returncode == 0:
        shutil.rmtree(out / "work", ignore_errors=True)
        # numba's cache from runs in this source tree (ohs/**/__pycache__/*.nbc|nbi) comes along with
        # --collect-data; the program compiles and caches in the user's cache folder instead
        for p in (out / "ohs").rglob("*.nb[ci]"):
            p.unlink()
        print(f"built: {out / 'ohs' / 'ohs.exe'}")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
