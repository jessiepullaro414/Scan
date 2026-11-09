"""Mesh / point-cloud export and simple surface post-processing.

* :func:`save_mesh` writes binary PLY, binary STL or ASCII OBJ (chosen by file extension) with
  numpy only; :func:`save_points_ply` writes binary PLY point clouds (optional normals/colours).
* :func:`load_ply` is a minimal PLY reader (ASCII and binary, triangles or polygons) and
  :func:`load_mesh` reads back anything :func:`save_mesh` writes; both exist for tests and the app.
* :func:`poisson_mesh` runs screened Poisson reconstruction (Open3D, imported lazily so the rest
  of this module works without it) and trims low-density vertices.
* :func:`clean_mesh` removes small disconnected components (scipy ``csgraph``).

Units are millimetres throughout (written as-is; PLY/OBJ/STL carry no unit, a header comment
says "mm"). Faces are (M,3) vertex-index triangles, counter-clockwise when seen from outside, so
the right-hand-rule face normal points outward.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "save_mesh",
    "save_points_ply",
    "load_ply",
    "load_mesh",
    "poisson_mesh",
    "clean_mesh",
    "face_normals",
]

log = logging.getLogger(__name__)

_PLY_TYPES = {
    "char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}


# --------------------------------------------------------------------------- helpers
def _as_vertices(V: np.ndarray, name: str = "V") -> np.ndarray:
    V = np.asarray(V, dtype=np.float64)
    if V.ndim != 2 or V.shape[1] != 3:
        raise ValueError(f"{name} must have shape (N,3), got {V.shape}")
    return V


def _as_faces(F: np.ndarray | None, n_vertices: int) -> np.ndarray:
    if F is None:
        return np.zeros((0, 3), dtype=np.int64)
    F = np.asarray(F)
    if F.size == 0:
        return np.zeros((0, 3), dtype=np.int64)
    if F.ndim != 2 or F.shape[1] != 3:
        raise ValueError(f"F must have shape (M,3), got {F.shape}")
    F = F.astype(np.int64)
    if F.min() < 0 or F.max() >= n_vertices:
        raise ValueError("face indices out of range")
    return F


def _as_colors(colors: np.ndarray, n: int) -> np.ndarray:
    """(n,3) uint8 from uint8 / float (0..1 or 0..255) / grey (n,) input."""
    c = np.asarray(colors)
    if c.ndim == 1:
        c = np.repeat(c[:, None], 3, axis=1)
    if c.shape != (n, 3):
        raise ValueError(f"colors must have shape ({n},3) or ({n},), got {c.shape}")
    if c.dtype == np.uint8:
        return c
    c = c.astype(np.float64)
    if c.size and np.nanmax(c) <= 1.0:
        c = c * 255.0
    return np.clip(np.nan_to_num(c), 0, 255).round().astype(np.uint8)


def face_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    """Unit right-hand-rule normals (M,3) of triangles; zero for degenerate faces."""
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    return np.divide(n, norm, out=np.zeros_like(n), where=norm > 0)


def _ply_header(n_vertices: int, has_normals: bool, has_colors: bool, n_faces: int | None) -> bytes:
    lines = ["ply", "format binary_little_endian 1.0", "comment OpenHandScan export, units: mm",
             f"element vertex {n_vertices}", "property float x", "property float y", "property float z"]
    if has_normals:
        lines += ["property float nx", "property float ny", "property float nz"]
    if has_colors:
        lines += ["property uchar red", "property uchar green", "property uchar blue"]
    if n_faces is not None:
        lines += [f"element face {n_faces}", "property list uchar int vertex_indices"]
    lines.append("end_header")
    return ("\n".join(lines) + "\n").encode("ascii")


def _write_ply(path: Path, V: np.ndarray, F: np.ndarray | None, N: np.ndarray | None,
               colors: np.ndarray | None) -> None:
    fields: list[tuple[str, Any]] = [("x", "<f4"), ("y", "<f4"), ("z", "<f4")]
    if N is not None:
        fields += [("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4")]
    if colors is not None:
        fields += [("red", "u1"), ("green", "u1"), ("blue", "u1")]
    vert = np.empty(len(V), dtype=np.dtype(fields))
    vert["x"], vert["y"], vert["z"] = V[:, 0], V[:, 1], V[:, 2]
    if N is not None:
        vert["nx"], vert["ny"], vert["nz"] = N[:, 0], N[:, 1], N[:, 2]
    if colors is not None:
        vert["red"], vert["green"], vert["blue"] = colors[:, 0], colors[:, 1], colors[:, 2]
    with open(path, "wb") as fh:
        fh.write(_ply_header(len(V), N is not None, colors is not None, None if F is None else len(F)))
        fh.write(vert.tobytes())
        if F is not None:
            face = np.empty(len(F), dtype=np.dtype([("n", "u1"), ("i", "<i4", (3,))]))
            face["n"] = 3
            face["i"] = F
            fh.write(face.tobytes())


def _write_stl(path: Path, V: np.ndarray, F: np.ndarray) -> None:
    tri = np.zeros(len(F), dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")]))
    tri["n"] = face_normals(V, F)
    tri["v"] = V[F]
    header = b"OpenHandScan binary STL, units: mm".ljust(80, b" ")
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(np.uint32(len(F)).tobytes())
        fh.write(tri.tobytes())


def _write_obj(path: Path, V: np.ndarray, F: np.ndarray, N: np.ndarray | None) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("# OpenHandScan export, units: mm\n")
        np.savetxt(fh, V, fmt="v %.6f %.6f %.6f")
        if N is not None:
            np.savetxt(fh, N, fmt="vn %.6f %.6f %.6f")
            f1 = F + 1
            np.savetxt(fh, np.repeat(f1, 2, axis=1), fmt="f %d//%d %d//%d %d//%d")
        else:
            np.savetxt(fh, F + 1, fmt="f %d %d %d")


# --------------------------------------------------------------------------- writers
def save_mesh(path: str | Path, V: np.ndarray, F: np.ndarray, N: np.ndarray | None = None) -> Path:
    """Write a triangle mesh; format by extension: ``.ply`` (binary), ``.stl`` (binary), ``.obj``.

    ``V`` (N,3) vertices in mm, ``F`` (M,3) vertex indices, ``N`` optional (N,3) vertex normals
    (ignored for STL, which stores per-face normals computed from the geometry).
    """
    path = Path(path)
    V = _as_vertices(V)
    F = _as_faces(F, len(V))
    if N is not None:
        N = _as_vertices(N, "N")
        if len(N) != len(V):
            raise ValueError("N must have one normal per vertex")
    ext = path.suffix.lower()
    if ext == ".ply":
        _write_ply(path, V, F, N, None)
    elif ext == ".stl":
        _write_stl(path, V, F)
    elif ext == ".obj":
        _write_obj(path, V, F, N)
    else:
        raise ValueError(f"unsupported mesh format {ext!r} (use .ply, .stl or .obj)")
    log.info("wrote mesh %s (%d vertices, %d faces)", path, len(V), len(F))
    return path


def save_points_ply(path: str | Path, P: np.ndarray, N: np.ndarray | None = None,
                    colors: np.ndarray | None = None) -> Path:
    """Write a binary little-endian PLY point cloud.

    ``P`` (N,3) mm; ``N`` optional (N,3) normals; ``colors`` optional (N,3) or (N,) grey, either
    uint8 or float (0..1 is scaled to 0..255).
    """
    path = Path(path)
    P = _as_vertices(P, "P")
    if N is not None:
        N = _as_vertices(N, "N")
        if len(N) != len(P):
            raise ValueError("N must have one normal per point")
    C = None if colors is None else _as_colors(colors, len(P))
    _write_ply(path, P, None, N, C)
    log.info("wrote point cloud %s (%d points)", path, len(P))
    return path


# --------------------------------------------------------------------------- readers
def _parse_ply_header(fh) -> tuple[str, list[dict]]:
    if fh.readline().strip() != b"ply":
        raise ValueError("not a PLY file")
    fmt = None
    elements: list[dict] = []
    while True:
        line = fh.readline()
        if not line:
            raise ValueError("truncated PLY header")
        tok = line.decode("ascii", errors="replace").split()
        if not tok or tok[0] in ("comment", "obj_info"):
            continue
        if tok[0] == "format":
            fmt = tok[1]
        elif tok[0] == "element":
            elements.append({"name": tok[1], "count": int(tok[2]), "props": []})
        elif tok[0] == "property":
            if tok[1] == "list":
                elements[-1]["props"].append((tok[4], "list", _PLY_TYPES[tok[2]], _PLY_TYPES[tok[3]]))
            else:
                elements[-1]["props"].append((tok[2], "scalar", _PLY_TYPES[tok[1]], None))
        elif tok[0] == "end_header":
            break
    if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise ValueError(f"unsupported PLY format {fmt!r}")
    return fmt, elements


def _fan_triangulate(polys: list[np.ndarray]) -> np.ndarray:
    tris = [np.stack([np.full(len(p) - 2, p[0]), p[1:-1], p[2:]], axis=1) for p in polys if len(p) >= 3]
    return np.concatenate(tris).astype(np.int64) if tris else np.zeros((0, 3), dtype=np.int64)


def load_ply(path: str | Path) -> dict[str, np.ndarray | None]:
    """Minimal PLY reader (ASCII / binary, any endianness).

    Returns ``{"vertices": (N,3) float64, "normals": (N,3) | None, "colors": (N,3) uint8 | None,
    "faces": (M,3) int64 | None}``. Polygons are fan-triangulated; unknown elements are skipped.
    """
    path = Path(path)
    with open(path, "rb") as fh:
        fmt, elements = _parse_ply_header(fh)
        body = fh.read()
    end = "<" if fmt == "binary_little_endian" else ">"
    data: dict[str, dict[str, np.ndarray]] = {}
    faces: np.ndarray | None = None
    if fmt == "ascii":
        lines = body.decode("ascii").splitlines()
        pos = 0
        for el in elements:
            rows = lines[pos : pos + el["count"]]
            pos += el["count"]
            has_list = any(p[1] == "list" for p in el["props"])
            if el["name"] == "face" and has_list:
                polys = [np.asarray(r.split()[1:], dtype=np.int64) for r in rows]
                faces = _fan_triangulate(polys)
            elif not has_list:
                arr = np.asarray([r.split() for r in rows], dtype=np.float64).reshape(len(rows), len(el["props"]))
                data[el["name"]] = {p[0]: arr[:, k] for k, p in enumerate(el["props"])}
    else:
        off = 0
        for el in elements:
            props = el["props"]
            if not any(p[1] == "list" for p in props):
                dt = np.dtype([(p[0], end + p[2]) for p in props])
                arr = np.frombuffer(body, dtype=dt, count=el["count"], offset=off)
                off += dt.itemsize * el["count"]
                data[el["name"]] = {p[0]: arr[p[0]] for p in props}
                continue
            if el["name"] != "face" or len(props) != 1:
                raise ValueError(f"unsupported list element {el['name']!r} in {path}")
            _, _, ctype, itype = props[0]
            cdt, idt = np.dtype(end + ctype), np.dtype(end + itype)
            n = el["count"]
            tri_dt = np.dtype([("n", cdt), ("i", idt, (3,))])
            if n and off + tri_dt.itemsize * n <= len(body):
                tri = np.frombuffer(body, dtype=tri_dt, count=n, offset=off)
                if np.all(tri["n"] == 3):
                    faces = tri["i"].astype(np.int64)
                    off += tri_dt.itemsize * n
                    continue
            polys = []
            for _ in range(n):  # general polygons: slow path
                k = int(np.frombuffer(body, dtype=cdt, count=1, offset=off)[0])
                off += cdt.itemsize
                polys.append(np.frombuffer(body, dtype=idt, count=k, offset=off).astype(np.int64))
                off += idt.itemsize * k
            faces = _fan_triangulate(polys)
    if "vertex" not in data:
        raise ValueError(f"no vertex element in {path}")
    v = data["vertex"]
    out: dict[str, np.ndarray | None] = {
        "vertices": np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float64),
        "normals": None, "colors": None, "faces": faces,
    }
    if all(k in v for k in ("nx", "ny", "nz")):
        out["normals"] = np.stack([v["nx"], v["ny"], v["nz"]], axis=1).astype(np.float64)
    if all(k in v for k in ("red", "green", "blue")):
        out["colors"] = np.stack([v["red"], v["green"], v["blue"]], axis=1).astype(np.uint8)
    return out


def _load_stl(path: Path) -> tuple[np.ndarray, np.ndarray]:
    raw = path.read_bytes()
    n = int(np.frombuffer(raw, dtype="<u4", count=1, offset=80)[0]) if len(raw) >= 84 else -1
    if n >= 0 and len(raw) == 84 + 50 * n:
        tri = np.frombuffer(raw, dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                            count=n, offset=84)
        corners = tri["v"].reshape(-1, 3)
    else:  # ASCII STL
        nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", raw)
        corners = np.asarray(nums, dtype=np.float32).reshape(-1, 3)
    V, inv = np.unique(corners, axis=0, return_inverse=True)
    return V.astype(np.float64), inv.reshape(-1, 3).astype(np.int64)


def _load_obj(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    verts, norms, polys, vn_of_v = [], [], [], {}
    with open(path, encoding="ascii", errors="replace") as fh:
        for line in fh:
            if line.startswith("v "):
                verts.append(line.split()[1:4])
            elif line.startswith("vn "):
                norms.append(line.split()[1:4])
            elif line.startswith("f "):
                idx = []
                for tok in line.split()[1:]:
                    parts = tok.split("/")
                    vi = int(parts[0])
                    vi = vi - 1 if vi > 0 else len(verts) + vi
                    idx.append(vi)
                    if len(parts) == 3 and parts[2]:
                        ni = int(parts[2])
                        vn_of_v[vi] = ni - 1 if ni > 0 else len(norms) + ni
                polys.append(np.asarray(idx, dtype=np.int64))
    V = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
    F = _fan_triangulate(polys)
    N = None
    if norms and vn_of_v:
        nn = np.asarray(norms, dtype=np.float64).reshape(-1, 3)
        N = np.zeros_like(V)
        vi = np.fromiter(vn_of_v.keys(), dtype=np.int64)
        N[vi] = nn[np.fromiter(vn_of_v.values(), dtype=np.int64)]
    return V, F, N


def load_mesh(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Read ``(V, F, N)`` from PLY, STL (binary or ASCII; vertices are merged) or OBJ."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".ply":
        d = load_ply(path)
        F = d["faces"] if d["faces"] is not None else np.zeros((0, 3), dtype=np.int64)
        return d["vertices"], F, d["normals"]
    if ext == ".stl":
        V, F = _load_stl(path)
        return V, F, None
    if ext == ".obj":
        return _load_obj(path)
    raise ValueError(f"unsupported mesh format {ext!r}")


# --------------------------------------------------------------------------- processing
def _import_open3d() -> Any:
    try:
        import open3d as o3d  # noqa: PLC0415 (optional heavy dependency)
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError("poisson_mesh needs open3d (pip install open3d)") from exc
    return o3d


def _median_spacing(P: np.ndarray, max_samples: int = 20000, seed: int = 0) -> float:
    from scipy.spatial import cKDTree  # noqa: PLC0415

    rng = np.random.default_rng(seed)
    sample = P if len(P) <= max_samples else P[rng.choice(len(P), max_samples, replace=False)]
    d, _ = cKDTree(P).query(sample, k=2)
    return float(np.median(d[:, 1]))


def poisson_mesh(
    P: np.ndarray,
    N: np.ndarray | None = None,
    depth: int = 9,
    density_quantile: float = 0.02,
    *,
    scale: float = 1.1,
    knn: int = 30,
    orient_k: int = 15,
    orient_towards: np.ndarray | None = None,
    n_threads: int = -1,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Screened Poisson surface from points ``P`` (mm) with optional normals ``N``.

    Without normals they are estimated (PCA over ``knn`` neighbours within ~5x the median point
    spacing) and oriented consistently (tangent-plane MST); the global sign is then chosen so
    normals point away from the centroid, or towards ``orient_towards`` (e.g. the mean scanner
    position) if given. Vertices whose Poisson density is below the ``density_quantile`` quantile
    are trimmed (removes the balloon surface Poisson invents far from the data).

    Returns ``(V, F, N)`` with outward vertex normals.
    """
    o3d = _import_open3d()
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    keep = np.all(np.isfinite(P), axis=1)
    if N is not None:
        N = np.asarray(N, dtype=np.float64).reshape(-1, 3)
        keep &= np.all(np.isfinite(N), axis=1) & (np.linalg.norm(N, axis=1) > 0)
        N = N[keep]
    P = P[keep]
    if len(P) < 10:
        raise ValueError("poisson_mesh needs at least 10 points")
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P))
    if N is None:
        spacing = _median_spacing(P)
        pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=5.0 * spacing, max_nn=knn))
        if orient_towards is not None:
            pcd.orient_normals_towards_camera_location(np.asarray(orient_towards, dtype=np.float64).reshape(3))
        else:
            pcd.orient_normals_consistent_tangent_plane(orient_k)
            n = np.asarray(pcd.normals)
            if np.mean(np.sum((P - P.mean(axis=0)) * n, axis=1)) < 0:
                pcd.normals = o3d.utility.Vector3dVector(-n)
    else:
        pcd.normals = o3d.utility.Vector3dVector(N / np.linalg.norm(N, axis=1, keepdims=True))
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=int(depth), scale=float(scale), linear_fit=False, n_threads=int(n_threads))
    dens = np.asarray(densities)
    if density_quantile > 0 and dens.size:
        mesh.remove_vertices_by_mask(dens < np.quantile(dens, density_quantile))
    mesh.remove_unreferenced_vertices()
    mesh.compute_vertex_normals()
    V = np.asarray(mesh.vertices, dtype=np.float64).copy()
    F = np.asarray(mesh.triangles, dtype=np.int64).copy()
    Nv = np.asarray(mesh.vertex_normals, dtype=np.float64).copy()
    log.info("poisson_mesh: %d points -> %d vertices, %d faces (depth %d)", len(P), len(V), len(F), depth)
    return V, F, Nv


def clean_mesh(
    V: np.ndarray, F: np.ndarray, min_component_fraction: float = 0.01, return_index: bool = False
) -> tuple[np.ndarray, np.ndarray] | tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Drop degenerate faces and connected components with fewer than
    ``min_component_fraction * len(F)`` faces, then unreferenced vertices.

    Returns ``(V, F)``; with ``return_index=True`` also the indices of the kept vertices in the
    input (use ``N[idx]`` to carry per-vertex attributes along).
    """
    from scipy.sparse import coo_matrix  # noqa: PLC0415
    from scipy.sparse.csgraph import connected_components  # noqa: PLC0415

    V = _as_vertices(V)
    F = _as_faces(F, len(V))
    F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
    if len(F) == 0:
        empty = (V[:0], F[:0], np.zeros(0, dtype=np.int64))
        return empty if return_index else empty[:2]
    rows = np.concatenate([F[:, 0], F[:, 1], F[:, 2]])
    cols = np.concatenate([F[:, 1], F[:, 2], F[:, 0]])
    A = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(len(V), len(V)))
    n_comp, labels = connected_components(A, directed=False)
    face_comp = labels[F[:, 0]]
    counts = np.bincount(face_comp, minlength=n_comp)
    F = F[(counts >= min_component_fraction * len(F))[face_comp]]
    used = np.unique(F)
    remap = np.full(len(V), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    log.debug("clean_mesh: %d components, kept %d faces / %d vertices", n_comp, len(F), len(used))
    if return_index:
        return V[used], remap[F], used
    return V[used], remap[F]
