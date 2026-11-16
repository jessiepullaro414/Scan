"""Runtime hook of the packaged program: stand-ins for plotly and dash.

``import open3d`` imports its web viewers (``open3d.visualization.draw_plotly``), which need
plotly and dash only when a viewer is opened: at import time they take three names. OpenHandScan
opens none (it shows meshes in its own page), so the program ships without plotly and dash (about
90 MB unpacked) and these empty modules answer the import. Anything that really used a plotly
figure or a dash app would fail here with a clear message instead of an ``ImportError`` at start.
"""
import importlib.util
import sys
import types


class _Unavailable:
    def __init__(self, what):
        self._what = what

    def __call__(self, *a, **k):
        raise RuntimeError(f"{self._what} is not part of the packaged program (Open3D's web viewers are not used)")

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Unavailable(f"{self._what}.{name}")


def _stub(name):
    if name in sys.modules:
        return
    try:
        if importlib.util.find_spec(name) is not None:
            return
    except (ImportError, ValueError, AttributeError):
        pass
    m = types.ModuleType(name)
    m.__path__ = []  # lets ``import plotly.graph_objects`` find the stand-in below
    m.__getattr__ = lambda attr, _n=name: _Unavailable(f"{_n}.{attr}")  # PEP 562
    sys.modules[name] = m


if getattr(sys, "frozen", False):
    for _n in ("plotly", "plotly.graph_objects", "dash"):
        _stub(_n)
