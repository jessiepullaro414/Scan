"""PyInstaller hook for Open3D: its libraries and the modules the engine uses (core, geometry, io,
pipelines, t, utility, the tensor ``VoxelBlockGrid`` and ``RaycastingScene``); without the
rendering resources (Filament's shaders and materials, 42 MB, only for Open3D's own windows),
the examples, the tutorial data, the Open3D-ML code and the PyTorch ops library (11 MB, needs
torch). ``--collect-all open3d`` took all of them."""
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

_SKIP = ("open3d.examples", "open3d._ml3d", "open3d.ml.tf", "open3d.ml.torch", "open3d.ml.vis",
         "open3d.ml.datasets", "open3d.ml.configs", "open3d.ml.utils", "open3d.tools", "open3d.web_visualizer",
         "open3d.app")

binaries = [b for b in collect_dynamic_libs("open3d") if "open3d_torch_ops" not in b[0]]
hiddenimports = collect_submodules("open3d", filter=lambda n: not n.startswith(_SKIP))
excludedimports = list(_SKIP)
