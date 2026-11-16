"""PyInstaller hook for numba: the standard hook (pyinstaller-hooks-contrib) plus every numba module
except numba's own test suites (2 MB packed). ``--collect-submodules numba`` took the tests too, and
with ``--exclude-module numba.tests`` it printed 400 "hidden import not found" errors."""
import importlib.util
import os
import runpy

from PyInstaller.utils.hooks import collect_submodules

_std = os.path.join(os.path.dirname(importlib.util.find_spec("_pyinstaller_hooks_contrib").origin),
                    "stdhooks", "hook-numba.py")
_ns = runpy.run_path(_std)
hiddenimports = list(_ns.get("hiddenimports", [])) + collect_submodules(
    "numba", filter=lambda n: ".tests" not in n and not n.endswith(".tests"))
excludedimports = list(_ns.get("excludedimports", [])) + ["numba.tests", "numba.cuda.tests"]
