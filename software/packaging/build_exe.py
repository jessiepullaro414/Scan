"""Build OpenHandScan as a Windows program with PyInstaller: a folder with ``ohs.exe`` and what it
needs, no Python installation required (desktop app plan, M6). A first trial (2026-11-16); no
installer yet.

    python packaging/build_exe.py [--out DIR]

(run from ``software/`` with the engine's environment, after ``npm run build`` in ``ui/`` so that
the app's page is in ``ohs/server/static``; needs ``pip install pyinstaller``). The result is
``<out>/ohs/ohs.exe`` (default ``<out>`` = ``packaging/out``, which git ignores):

* ``ohs.exe app`` starts the desktop app, ``ohs.exe bench``, ``ohs.exe process <recording>`` and
  the other commands work as ``ohs ...`` does;
* everything the environment has goes in: Open3D, OpenCV, SciPy, PyOpenCL (the GPU is used when
  the graphics driver offers OpenCL), numba (compiles on the user's PC at the first scan with
  four lasers, then caches it in the user's cache folder), the app's server and window.

Measured on the first build (``VALIDATION.md``, "A trial exe"): about 720 MB unpacked, 5 minutes
to build; the same results as from Python. Signing, an installer, a smaller build and the
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
           "--paths", str(SOFTWARE), "--log-level", "WARN",
           "--collect-data", "ohs", "--collect-submodules", "ohs",  # the app's page and every engine module
           "--collect-all", "open3d", "--collect-all", "pyopencl",  # native libraries and OpenCL kernels' sources
           "--collect-submodules", "numba", "--collect-submodules", "uvicorn", "--collect-data", "webview"]
    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=SOFTWARE)
    if r.returncode == 0:
        shutil.rmtree(out / "work", ignore_errors=True)
        print(f"built: {out / 'ohs' / 'ohs.exe'}")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
