"""Vectorised (numpy) ray casting of simple primitives for the scanner simulator.

**Sim world frame W:** right-handed, millimetres, **Z up**, gravity ``g_w = (0, 0, -9.81)`` m/s^2
(:data:`GRAVITY_W`). Tables and floors are the plane ``Z = 0`` (or parallel to it); objects sit on
top and the scanner looks down at them. Camera poses are ``T_wc`` (frame C -> W).

Primitives (all take world-frame rays ``o`` (3,) or (N,3) and unit directions ``d`` (N,3)):

* :class:`Rect` - bounded rectangle in its local XY plane (optionally textured / with decals);
* :class:`Sphere`;
* :class:`Box` - posed box (axis-aligned when ``T_wo`` has no rotation);
* :class:`Cylinder` - posed, capped cylinder (axis = local z);
* :class:`HeightField` - smooth surface ``z = h(x, y)`` over a rectangle, with circular holes
  (solved by safeguarded Newton iteration; used for the curved car panel);
* :class:`TriangleMesh` - any triangle mesh, e.g. a downloaded scan of a real part (Open3D's
  Embree ray caster; :func:`mesh_on_floor` builds a scene around a mesh file).

``Primitive.intersect(o, d) -> t`` gives the hit distance (``inf`` = miss), ``Primitive.shade(p,
footprint)`` the geometric normal and albedo at hit points. :meth:`Scene.cast` returns a
:class:`Hit` (distance, normal facing the ray, albedo, object id; id ``-1`` = nothing hit) and
:meth:`Scene.occluded` is the any-hit query used for shadow rays.

**Texture convention** (same as OpenCV board/marker object frames): a texture's columns run along
local +x and its rows along local +y; it reads correctly (unmirrored) when viewed from the local
**-z** side, i.e. local z points *into* the surface. A Z-up table viewed from above therefore uses
``R_wo = diag(1, -1, -1)``. Albedo is a reflectance in [0, 1]; printed black is 0.04, white 0.85.

Albedo models: :class:`ConstantAlbedo`, :class:`NoiseAlbedo` (procedural low-contrast "paint"
noise fixed to the surface, triplanar), :class:`TextureAlbedo` (image texture with a mip pyramid
selected by the pixel footprint, so printed boards are area-sampled, not aliased) and
:class:`DecalAlbedo` (stickers such as AprilTags on top of a base albedo).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, Sequence

import cv2
import numpy as np

from ohs.geometry import make_se3, so3_exp

__all__ = [
    "GRAVITY_W",
    "INK_BLACK",
    "PAPER_WHITE",
    "Hit",
    "Albedo",
    "ConstantAlbedo",
    "NoiseAlbedo",
    "TextureAlbedo",
    "Decal",
    "DecalAlbedo",
    "Primitive",
    "Rect",
    "Sphere",
    "Box",
    "Cylinder",
    "HeightField",
    "TriangleMesh",
    "load_mesh",
    "mesh_on_floor",
    "Scene",
    "charuco_texture",
    "apriltag_texture",
    "marker_corners_world",
    "sphere_on_plane",
    "car_panel",
    "bracket",
    "charuco_board",
    "SCENE_PRESETS",
    "make_scene",
]

log = logging.getLogger(__name__)

GRAVITY_W = np.array([0.0, 0.0, -9.81])  # m/s^2, sim world (Z up)
INK_BLACK = 0.04
PAPER_WHITE = 0.85
EPS_T = 1e-6  # mm, minimum hit distance
_REMAP_COLS = 1024  # cv2.remap needs dst dims < 32767 -> sample points in (rows, 1024) blocks


# --------------------------------------------------------------------------- small helpers
def _dot(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise dot product supporting (3,) x (N,3) broadcasting."""
    if a.ndim == 1:
        return b @ a
    if b.ndim == 1:
        return a @ b
    return np.einsum("ij,ij->i", a, b)


def _sample(img: np.ndarray, x: np.ndarray, y: np.ndarray, border: int, value: float = 0.0) -> np.ndarray:
    """Bilinear samples of a float32 image at pixel coordinates (x = column, y = row)."""
    m = x.shape[0]
    if m == 0:
        return np.zeros(0, np.float32)
    rows = -(-m // _REMAP_COLS)
    mx = np.zeros(rows * _REMAP_COLS, np.float32)
    my = np.zeros(rows * _REMAP_COLS, np.float32)
    mx[:m] = x
    my[:m] = y
    out = cv2.remap(
        img,
        mx.reshape(rows, _REMAP_COLS),
        my.reshape(rows, _REMAP_COLS),
        cv2.INTER_LINEAR,
        borderMode=border,
        borderValue=float(value),
    )
    return out.reshape(-1)[:m]


def _pose(T: np.ndarray | None) -> np.ndarray:
    return np.eye(4) if T is None else np.asarray(T, dtype=np.float64).reshape(4, 4)


# --------------------------------------------------------------------------- albedo models
class Albedo(Protocol):
    """Reflectance at surface points: ``albedo(p_local (M,3), n_local (M,3), footprint_mm (M,))``."""

    def __call__(self, p: np.ndarray, n: np.ndarray, footprint: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class ConstantAlbedo:
    value: float = 0.7

    def __call__(self, p: np.ndarray, n: np.ndarray, footprint: np.ndarray) -> np.ndarray:
        return np.full(len(p), self.value, dtype=np.float64)


class NoiseAlbedo:
    """Low-contrast procedural albedo ``mean * (1 + contrast * noise)`` fixed to the surface.

    ``noise`` is a smooth, unit-variance random field with features of about ``scale_mm`` (two
    octaves), evaluated triplanar in the primitive's local coordinates so it does not depend on
    the viewpoint (stereo-consistent) and has no seams on curved objects.
    """

    def __init__(self, mean: float = 0.7, contrast: float = 0.03, scale_mm: float = 4.0, seed: int = 0, size: int = 512) -> None:
        from scipy.ndimage import gaussian_filter

        self.mean, self.contrast, self.scale_mm = float(mean), float(contrast), float(scale_mm)
        rng = np.random.default_rng(seed)
        base = gaussian_filter(rng.standard_normal((size, size)), 2.0, mode="wrap")
        fine = gaussian_filter(rng.standard_normal((size, size)), 0.8, mode="wrap")
        tex = base / base.std() + 0.4 * fine / fine.std()
        tex -= tex.mean()
        self._tex = (tex / tex.std()).astype(np.float32)
        self._size = size
        self._texel = self.scale_mm / 4.0  # mm per texel: a sigma-2 blur gives ~scale_mm features
        self._offsets = rng.uniform(0, size, (3, 2))

    def _lookup(self, a: np.ndarray, b: np.ndarray, k: int) -> np.ndarray:
        s = self._size
        u = np.mod(a / self._texel + self._offsets[k, 0], s)
        v = np.mod(b / self._texel + self._offsets[k, 1], s)
        return _sample(self._tex, u, v, cv2.BORDER_WRAP).astype(np.float64)

    def __call__(self, p: np.ndarray, n: np.ndarray, footprint: np.ndarray) -> np.ndarray:
        if len(p) == 0:
            return np.zeros(0)
        w = np.abs(n) ** 4
        w /= np.maximum(w.sum(1, keepdims=True), 1e-12)
        noise = np.zeros(len(p))
        planes = ((1, 2), (0, 2), (0, 1))
        for k, (i, j) in enumerate(planes):
            sel = w[:, k] > 1e-3
            if np.any(sel):
                noise[sel] += w[sel, k] * self._lookup(p[sel, i], p[sel, j], k)
        return self.mean * (1.0 + self.contrast * noise)


class TextureAlbedo:
    """Image texture on the local XY plane, area-sampled through a mip pyramid.

    Texture pixel ``(col, row)`` covers local ``x in origin_x + [col, col+1] / px_per_mm`` and
    likewise for ``y``. A ``uint8`` image maps 0 -> ``black``, 255 -> ``white`` (printed ink on
    paper); a float image is used as reflectance directly. Outside the image -> ``border``.
    The pyramid level is chosen per sample from the pixel footprint on the surface (mm).
    """

    def __init__(
        self,
        image: np.ndarray,
        px_per_mm: float,
        origin_mm: tuple[float, float] = (0.0, 0.0),
        border: float = PAPER_WHITE,
        black: float = INK_BLACK,
        white: float = PAPER_WHITE,
        levels: int = 6,
    ) -> None:
        img = np.asarray(image)
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if img.dtype == np.uint8:
            alb = black + (white - black) * (img.astype(np.float32) / 255.0)
        else:
            alb = img.astype(np.float32)
        self.px_per_mm = float(px_per_mm)
        self.origin = (float(origin_mm[0]), float(origin_mm[1]))
        self.border = float(border)
        self.size_mm = (img.shape[1] / self.px_per_mm, img.shape[0] / self.px_per_mm)
        self._pad = 2 ** (levels + 1)
        padded = cv2.copyMakeBorder(alb, self._pad, self._pad, self._pad, self._pad, cv2.BORDER_CONSTANT, value=self.border)
        pyr = [padded.astype(np.float32)]
        for _ in range(levels - 1):
            if min(pyr[-1].shape) < 8:
                break
            pyr.append(cv2.pyrDown(pyr[-1]))
        self._pyr = pyr

    def sample_xy(self, x: np.ndarray, y: np.ndarray, footprint: np.ndarray | None = None) -> np.ndarray:
        """Reflectance at local coordinates ``x, y`` (mm), footprint (mm per camera sample)."""
        if len(x) == 0:
            return np.zeros(0)
        col = (x - self.origin[0]) * self.px_per_mm - 0.5 + self._pad
        row = (y - self.origin[1]) * self.px_per_mm - 0.5 + self._pad
        nlev = len(self._pyr)
        if footprint is None or nlev == 1:
            lod = np.zeros(len(x))
        else:
            lod = np.clip(np.log2(np.maximum(np.asarray(footprint) * self.px_per_mm, 1.0)), 0.0, nlev - 1.0)
        out = np.zeros(len(x))
        for k, img in enumerate(self._pyr):
            wk = np.clip(1.0 - np.abs(lod - k), 0.0, 1.0)
            sel = wk > 0
            if not np.any(sel):
                continue
            sc = 0.5**k  # cv2.pyrDown keeps even samples: level-k pixel i sits at level-0 coordinate 2^k i
            vals = _sample(img, col[sel] * sc, row[sel] * sc, cv2.BORDER_CONSTANT, self.border)
            out[sel] += wk[sel] * vals
        return out

    def __call__(self, p: np.ndarray, n: np.ndarray, footprint: np.ndarray) -> np.ndarray:
        return self.sample_xy(p[:, 0], p[:, 1], footprint)


@dataclass
class Decal:
    """A printed sticker on a surface's local XY plane (e.g. an AprilTag label).

    ``image`` spans ``size_mm`` centred at ``center_xy``; its columns run along
    ``(cos angle, sin angle)``; ``margin_mm`` of ``margin_albedo`` (white label) surrounds it.
    By default it reads correctly from the host's local -z side (texture convention);
    ``flip_y=True`` mirrors it so it reads from the +z side (e.g. the top of a HeightField).
    """

    image: np.ndarray
    px_per_mm: float
    center_xy: tuple[float, float]
    angle: float = 0.0
    margin_mm: float = 0.0
    margin_albedo: float = PAPER_WHITE
    tag_id: int | None = None
    flip_y: bool = False
    size_mm: tuple[float, float] = field(init=False)
    _tex: TextureAlbedo = field(init=False, repr=False)

    def __post_init__(self) -> None:
        h_px, w_px = np.asarray(self.image).shape[:2]
        self.size_mm = (w_px / self.px_per_mm, h_px / self.px_per_mm)
        self._tex = TextureAlbedo(
            self.image, self.px_per_mm, origin_mm=(-self.size_mm[0] / 2, -self.size_mm[1] / 2), border=self.margin_albedo
        )

    def to_decal(self, xy: np.ndarray) -> np.ndarray:
        c, s = math.cos(self.angle), math.sin(self.angle)
        dx = xy[:, 0] - self.center_xy[0]
        dy = xy[:, 1] - self.center_xy[1]
        q = np.stack([c * dx + s * dy, -s * dx + c * dy], axis=1)
        if self.flip_y:
            q[:, 1] *= -1.0
        return q

    def corners_local(self) -> np.ndarray:
        """Image corners (TL, TR, BR, BL of the image) in host-local XY (4,2) - the OpenCV
        ArUco corner order for markers."""
        hw, hh = self.size_mm[0] / 2, self.size_mm[1] / 2
        q = np.array([[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]])
        if self.flip_y:
            q[:, 1] *= -1.0
        c, s = math.cos(self.angle), math.sin(self.angle)
        R = np.array([[c, -s], [s, c]])
        return q @ R.T + np.asarray(self.center_xy)


class DecalAlbedo:
    """Base albedo with decals pasted on the local XY plane (later decals win)."""

    def __init__(self, base: Albedo | float, decals: Sequence[Decal]) -> None:
        self.base = _as_albedo(base)
        self.decals = list(decals)
        # bounding circles (host-local XY) for a cheap first test
        self._bounds = [
            (dc.center_xy[0], dc.center_xy[1], math.hypot(dc.size_mm[0] / 2 + dc.margin_mm, dc.size_mm[1] / 2 + dc.margin_mm))
            for dc in self.decals
        ]

    def __call__(self, p: np.ndarray, n: np.ndarray, footprint: np.ndarray) -> np.ndarray:
        a = self.base(p, n, footprint)
        x, y = p[:, 0], p[:, 1]
        for dc, (cx, cy, r) in zip(self.decals, self._bounds):
            cand = np.flatnonzero((np.abs(x - cx) <= r) & (np.abs(y - cy) <= r))
            if cand.size == 0:
                continue
            q = dc.to_decal(p[cand, :2])
            ex = dc.size_mm[0] / 2 + dc.margin_mm
            ey = dc.size_mm[1] / 2 + dc.margin_mm
            inside = (np.abs(q[:, 0]) <= ex) & (np.abs(q[:, 1]) <= ey)
            if np.any(inside):
                idx = cand[inside]
                a[idx] = dc._tex.sample_xy(q[inside, 0], q[inside, 1], footprint[idx])
        return a


def _as_albedo(a: Albedo | float | None) -> Albedo:
    if a is None:
        return ConstantAlbedo(0.7)
    if isinstance(a, (int, float, np.floating)):
        return ConstantAlbedo(float(a))
    return a


# --------------------------------------------------------------------------- primitives
class Primitive:
    """Base class: a posed surface with an albedo model. Subclasses implement ``intersect`` and
    ``_local_normal`` (geometric normal in local coordinates at local points)."""

    def __init__(self, T_wo: np.ndarray | None, albedo: Albedo | float | None, name: str) -> None:
        self.T_wo = _pose(T_wo)
        self.R = self.T_wo[:3, :3].copy()
        self.t = self.T_wo[:3, 3].copy()
        self.albedo = _as_albedo(albedo)
        self.name = name

    def to_local(self, o: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (o - self.t) @ self.R, d @ self.R

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def _local_normal(self, pl: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def _hit_normal_local(self, pl: np.ndarray, idx: np.ndarray, n_rays: int) -> np.ndarray:
        """Local normals at the hit points ``pl`` of rays ``idx`` of the last :meth:`intersect`
        (of ``n_rays`` rays); primitives that remember their hits (meshes) override it."""
        return self._local_normal(pl)

    def shade(self, p: np.ndarray, footprint: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """World normals (M,3) (geometric, not yet facing the ray) and albedo (M,) at points ``p``."""
        pl = (p - self.t) @ self.R
        nl = self._local_normal(pl)
        return nl @ self.R.T, self.albedo(pl, nl, footprint)


class Rect(Primitive):
    """Rectangle ``bounds = (xmin, xmax, ymin, ymax)`` in the local z = 0 plane (two-sided).

    The geometric normal is local **-z** (the front side of textures, see module docstring).
    """

    def __init__(
        self,
        T_wo: np.ndarray | None,
        bounds: tuple[float, float, float, float],
        albedo: Albedo | float | None = 0.7,
        name: str = "rect",
    ) -> None:
        super().__init__(T_wo, albedo, name)
        self.bounds = tuple(float(b) for b in bounds)
        self._n = self.R[:, 2]
        self._c = -float(self._n @ self.t)
        self._ex, self._ey = self.R[:, 0], self.R[:, 1]

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        den = d @ self._n
        with np.errstate(divide="ignore", invalid="ignore"):
            t = -(_dot(o, self._n) + self._c) / den
            x = _dot(o - self.t, self._ex) + t * (d @ self._ex)
            y = _dot(o - self.t, self._ey) + t * (d @ self._ey)
            x0, x1, y0, y1 = self.bounds
            ok = (t > EPS_T) & (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
        return np.where(ok, t, np.inf)

    def _local_normal(self, pl: np.ndarray) -> np.ndarray:
        n = np.zeros_like(pl)
        n[:, 2] = -1.0
        return n


class Sphere(Primitive):
    def __init__(self, center: Sequence[float], radius: float, albedo: Albedo | float | None = 0.7, name: str = "sphere") -> None:
        super().__init__(make_se3(t=np.asarray(center, dtype=np.float64)), albedo, name)
        self.center = self.t
        self.radius = float(radius)

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        oc = o - self.center
        b = _dot(oc, d) if oc.ndim > 1 else d @ oc
        c = _dot(oc, oc) - self.radius**2
        disc = b * b - c
        with np.errstate(invalid="ignore"):
            sq = np.sqrt(np.maximum(disc, 0.0))
        t1, t2 = -b - sq, -b + sq
        t = np.where(t1 > EPS_T, t1, np.where(t2 > EPS_T, t2, np.inf))
        return np.where(disc >= 0, t, np.inf)

    def _local_normal(self, pl: np.ndarray) -> np.ndarray:
        return pl / np.linalg.norm(pl, axis=1, keepdims=True)


def _bounding_sphere_candidates(o: np.ndarray, d: np.ndarray, c: np.ndarray, r: float) -> np.ndarray:
    """Indices of rays that pass within ``r`` of ``c`` in front of the origin."""
    oc = o - c
    b = _dot(oc, d) if oc.ndim > 1 else d @ oc
    cc = _dot(oc, oc) - r * r
    disc = b * b - cc
    return np.flatnonzero((disc >= 0) & ((-b + np.sqrt(np.maximum(disc, 0.0))) > 0))


class _Culled(Primitive):
    """Primitives with a bounding sphere: only candidate rays run the exact test."""

    _bs_center: np.ndarray
    _bs_radius: float

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        out = np.full(len(d), np.inf)
        idx = _bounding_sphere_candidates(o, d, self._bs_center, self._bs_radius)
        if idx.size:
            oo = o[idx] if o.ndim > 1 else o
            ol, dl = self.to_local(oo, d[idx])
            out[idx] = self._intersect_local(ol, dl)
        return out

    def _intersect_local(self, ol: np.ndarray, dl: np.ndarray) -> np.ndarray:
        raise NotImplementedError


class Box(_Culled):
    """Box with half extents ``half`` (3,) centred at the local origin."""

    def __init__(self, T_wo: np.ndarray | None, half: Sequence[float], albedo: Albedo | float | None = 0.7, name: str = "box") -> None:
        super().__init__(T_wo, albedo, name)
        self.half = np.asarray(half, dtype=np.float64).reshape(3)
        self._bs_center, self._bs_radius = self.t, float(np.linalg.norm(self.half)) + 1e-6

    def _intersect_local(self, ol: np.ndarray, dl: np.ndarray) -> np.ndarray:
        ol = np.broadcast_to(ol, dl.shape)
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / dl
            t1 = (-self.half - ol) * inv
            t2 = (self.half - ol) * inv
        tmin = np.fmax.reduce(np.fmin(t1, t2), axis=1)
        tmax = np.fmin.reduce(np.fmax(t1, t2), axis=1)
        hit = (tmax >= tmin) & (tmax > EPS_T)
        t = np.where(tmin > EPS_T, tmin, tmax)
        return np.where(hit, t, np.inf)

    def _local_normal(self, pl: np.ndarray) -> np.ndarray:
        q = np.abs(pl) / self.half
        ax = np.argmax(q, axis=1)
        n = np.zeros_like(pl)
        n[np.arange(len(pl)), ax] = np.sign(pl[np.arange(len(pl)), ax])
        return n


class Cylinder(_Culled):
    """Capped cylinder of ``radius`` along local z, ``z in [-half_height, half_height]``."""

    def __init__(
        self,
        T_wo: np.ndarray | None,
        radius: float,
        half_height: float,
        albedo: Albedo | float | None = 0.7,
        name: str = "cylinder",
        caps: bool = True,
    ) -> None:
        super().__init__(T_wo, albedo, name)
        self.radius, self.half_height, self.caps = float(radius), float(half_height), bool(caps)
        self._bs_center, self._bs_radius = self.t, math.hypot(self.radius, self.half_height) + 1e-6

    def _intersect_local(self, ol: np.ndarray, dl: np.ndarray) -> np.ndarray:
        ol = np.broadcast_to(ol, dl.shape)
        ox, oy, oz = ol[:, 0], ol[:, 1], ol[:, 2]
        dx, dy, dz = dl[:, 0], dl[:, 1], dl[:, 2]
        r, h = self.radius, self.half_height
        a = dx * dx + dy * dy
        b = ox * dx + oy * dy
        c = ox * ox + oy * oy - r * r
        disc = b * b - a * c
        best = np.full(len(dl), np.inf)
        with np.errstate(divide="ignore", invalid="ignore"):
            sq = np.sqrt(np.maximum(disc, 0.0))
            for t in ((-b - sq) / a, (-b + sq) / a):
                z = oz + t * dz
                ok = (disc >= 0) & (a > 1e-14) & (t > EPS_T) & (np.abs(z) <= h)
                best = np.where(ok & (t < best), t, best)
            if self.caps:
                for zc in (h, -h):
                    t = (zc - oz) / dz
                    x, y = ox + t * dx, oy + t * dy
                    ok = (t > EPS_T) & (x * x + y * y <= r * r)
                    best = np.where(ok & (t < best), t, best)
        return best

    def _local_normal(self, pl: np.ndarray) -> np.ndarray:
        rad = np.hypot(pl[:, 0], pl[:, 1])
        side = np.abs(rad - self.radius) <= np.abs(np.abs(pl[:, 2]) - self.half_height)
        n = np.zeros_like(pl)
        with np.errstate(invalid="ignore", divide="ignore"):
            n[:, 0] = np.where(side, pl[:, 0] / np.maximum(rad, 1e-12), 0.0)
            n[:, 1] = np.where(side, pl[:, 1] / np.maximum(rad, 1e-12), 0.0)
        n[:, 2] = np.where(side, 0.0, np.sign(pl[:, 2]))
        return n


HeightFn = Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray]]


class HeightField(_Culled):
    """Surface ``z = h(x, y)`` (local frame) over ``bounds``, minus circular ``holes``.

    ``height(x, y) -> (h, dh/dx, dh/dy)`` must be smooth (C1) and vectorised; ``z_range`` bounds
    ``h`` over the domain. Rays are assumed to cross the surface at most once inside the slab
    ``z_range`` (true for gently curved panels). The root of ``g(t) = o_z + t d_z - h(x(t), y(t))``
    is bracketed by the slab and refined by safeguarded Newton steps to ~1e-9 mm. The geometric
    normal is ``(-h_x, -h_y, 1)`` normalised (local +z side = "top").
    """

    def __init__(
        self,
        T_wo: np.ndarray | None,
        height: HeightFn,
        bounds: tuple[float, float, float, float],
        z_range: tuple[float, float],
        holes: Sequence[tuple[float, float, float]] = (),
        albedo: Albedo | float | None = 0.7,
        name: str = "heightfield",
        iters: int = 40,
    ) -> None:
        super().__init__(T_wo, albedo, name)
        self.height = height
        self.bounds = tuple(float(b) for b in bounds)
        self.z_range = (float(z_range[0]) - 0.5, float(z_range[1]) + 0.5)
        self.holes = [tuple(float(v) for v in hh) for hh in holes]
        self.iters = int(iters)
        x0, x1, y0, y1 = self.bounds
        lo = np.array([x0, y0, self.z_range[0]])
        hi = np.array([x1, y1, self.z_range[1]])
        self._lo, self._hi = lo, hi
        c_local = 0.5 * (lo + hi)
        self._bs_center = self.R @ c_local + self.t
        self._bs_radius = float(np.linalg.norm(hi - lo) / 2) + 1e-6

    def _g(self, ol: np.ndarray, dl: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = ol[:, 0] + t * dl[:, 0]
        y = ol[:, 1] + t * dl[:, 1]
        h, hx, hy = self.height(x, y)
        g = ol[:, 2] + t * dl[:, 2] - h
        dg = dl[:, 2] - hx * dl[:, 0] - hy * dl[:, 1]
        return g, dg

    def _intersect_local(self, ol: np.ndarray, dl: np.ndarray) -> np.ndarray:
        ol = np.ascontiguousarray(np.broadcast_to(ol, dl.shape))
        n = len(dl)
        out = np.full(n, np.inf)
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / dl
            t1 = (self._lo - ol) * inv
            t2 = (self._hi - ol) * inv
        lo = np.maximum(np.fmax.reduce(np.fmin(t1, t2), axis=1), 0.0)
        hi = np.fmin.reduce(np.fmax(t1, t2), axis=1)
        cand = np.flatnonzero(hi > lo)
        if cand.size == 0:
            return out
        ol, dl, lo, hi = ol[cand], dl[cand], lo[cand], hi[cand]
        g_lo, _ = self._g(ol, dl, lo)
        g_hi, _ = self._g(ol, dl, hi)
        ok = (g_lo * g_hi <= 0) & (g_lo != g_hi)
        sel = np.flatnonzero(ok)
        if sel.size == 0:
            return out
        ol, dl, lo, hi, g_lo, g_hi = ol[sel], dl[sel], lo[sel], hi[sel], g_lo[sel], g_hi[sel]
        sign_lo = np.sign(g_lo)
        t = lo - g_lo * (hi - lo) / (g_hi - g_lo)  # regula falsi start
        act = np.arange(len(t))  # rays still iterating
        for _ in range(self.iters):
            ta, la, ha = t[act], lo[act], hi[act]
            g, dg = self._g(ol[act], dl[act], ta)
            same = np.sign(g) == sign_lo[act]
            la = np.where(same, ta, la)
            ha = np.where(same, ha, ta)
            with np.errstate(divide="ignore", invalid="ignore"):
                tn = ta - g / dg
            bad = (g != 0) & (~np.isfinite(tn) | (tn <= la) | (tn >= ha))
            tn = np.where(bad, 0.5 * (la + ha), np.where(g == 0, ta, tn))
            t[act], lo[act], hi[act] = tn, la, ha
            act = act[np.abs(tn - ta) >= 1e-10]
            if act.size == 0:
                break
        x = ol[:, 0] + t * dl[:, 0]
        y = ol[:, 1] + t * dl[:, 1]
        x0, x1, y0, y1 = self.bounds
        good = (t > EPS_T) & (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
        for hx, hy, hr in self.holes:
            good &= (x - hx) ** 2 + (y - hy) ** 2 > hr * hr
        res = np.where(good, t, np.inf)
        out[cand[sel]] = res
        return out

    def _local_normal(self, pl: np.ndarray) -> np.ndarray:
        _, hx, hy = self.height(pl[:, 0], pl[:, 1])
        n = np.stack([-hx, -hy, np.ones_like(hx)], axis=1)
        return n / np.linalg.norm(n, axis=1, keepdims=True)


_MESH_TNEAR = 0.01  # mm: mesh hits closer than this to the ray origin are ignored (float32 self-hits)


class TriangleMesh(Primitive):
    """A triangle mesh (local frame, mm), e.g. a downloaded 3D scan of a car part.

    Rays are cast with Open3D's ``RaycastingScene`` (Embree, float32: about 1e-4 mm at 1 m). Hits
    closer than 0.01 mm to the ray origin are ignored, so shadow rays leaving the surface don't hit
    their own triangle. The normal is interpolated from the vertex normals (smooth shading of a
    fine scan; the depth is the triangles'). Winding doesn't matter: :meth:`Scene.cast` turns
    normals towards the ray. ``vertices`` (N,3) and ``triangles`` (M,3) are in the local frame.
    """

    def __init__(self, T_wo: np.ndarray | None, vertices: np.ndarray, triangles: np.ndarray,
                 albedo: Albedo | float | None = 0.7, name: str = "mesh") -> None:
        import open3d as o3d  # noqa: PLC0415 - optional dependency ([fusion] extra)

        super().__init__(T_wo, albedo, name)
        V = np.ascontiguousarray(vertices, dtype=np.float32).reshape(-1, 3)
        F = np.ascontiguousarray(triangles, dtype=np.uint32).reshape(-1, 3)
        if len(F) == 0 or int(F.max()) >= len(V):
            raise ValueError("TriangleMesh needs triangles that index its vertices")
        self.vertices, self.triangles = V, F
        self._o3c = o3d.core
        self._rs = o3d.t.geometry.RaycastingScene()
        self._rs.add_triangles(o3d.core.Tensor(V), o3d.core.Tensor(F))
        Vd = V.astype(np.float64)
        fn = np.cross(Vd[F[:, 1]] - Vd[F[:, 0]], Vd[F[:, 2]] - Vd[F[:, 0]])  # area-weighted face normals
        vn = np.zeros_like(Vd)
        for k in range(3):
            np.add.at(vn, F[:, k], fn)
        ln = np.linalg.norm(fn, axis=1, keepdims=True)
        self._fn = np.where(ln > 0, fn / np.maximum(ln, 1e-300), np.array([0.0, 0.0, 1.0]))
        lv = np.linalg.norm(vn, axis=1, keepdims=True)
        self._vn = np.where(lv > 0, vn / np.maximum(lv, 1e-300), 0.0)
        lo, hi = Vd.min(axis=0), Vd.max(axis=0)
        self.bounds_local = (lo, hi)
        self._last: tuple[int, np.ndarray, np.ndarray] | None = None  # (rays, triangle ids, barycentric uv)

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        ol, dl = self.to_local(o, d)
        ol = np.broadcast_to(ol, dl.shape)
        rays = np.empty((len(dl), 6), dtype=np.float32)
        rays[:, :3] = ol + _MESH_TNEAR * dl  # tnear: start the ray a little along itself
        rays[:, 3:] = dl
        ans = self._rs.cast_rays(self._o3c.Tensor(rays))
        t = ans["t_hit"].numpy().astype(np.float64)
        hit = np.isfinite(t)
        t = np.where(hit, t + _MESH_TNEAR, np.inf)
        ids = np.where(hit, ans["primitive_ids"].numpy().astype(np.int64), -1)
        self._last = (len(dl), ids, ans["primitive_uvs"].numpy())
        return t

    def _normals(self, ids: np.ndarray, uv: np.ndarray) -> np.ndarray:
        F = self.triangles[ids]
        u, v = uv[:, 0:1].astype(np.float64), uv[:, 1:2].astype(np.float64)
        n = (1.0 - u - v) * self._vn[F[:, 0]] + u * self._vn[F[:, 1]] + v * self._vn[F[:, 2]]
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        return np.where(ln > 1e-9, n / np.maximum(ln, 1e-300), self._fn[ids])

    def _hit_normal_local(self, pl: np.ndarray, idx: np.ndarray, n_rays: int) -> np.ndarray:
        last = self._last
        if last is not None and last[0] == n_rays:
            ids, uv = last[1][idx], last[2][idx]
            if np.all(ids >= 0):
                return self._normals(ids, uv)
        return self._local_normal(pl)

    def _local_normal(self, pl: np.ndarray) -> np.ndarray:
        if len(pl) == 0:
            return np.zeros((0, 3))
        ans = self._rs.compute_closest_points(self._o3c.Tensor(np.ascontiguousarray(pl, dtype=np.float32)))
        return self._normals(ans["primitive_ids"].numpy().astype(np.int64), ans["primitive_uvs"].numpy())


def load_mesh(path: str, size_mm: float | None = None, scale: float = 1.0, orient: str = "flat",
              flip: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (N,3, mm) and triangles (M,3) of a mesh file (OBJ, PLY, STL, glTF/GLB...), ready
    for :class:`TriangleMesh`: scaled by ``scale``, or so that its longest side is ``size_mm``.
    ``orient``: ``"flat"`` turns its principal axes into x (longest), y and z (thinnest), so a door
    or a panel lies on the floor; ``"y-up"`` turns a Y-up file (glTF, most scans) Z-up; ``"z-up"``
    keeps it. ``flip`` turns it upside down (about x). Its bounding box is centred on x and y and
    its lowest point is at z = 0."""
    if orient not in ("flat", "y-up", "z-up"):
        raise ValueError(f"orient must be 'flat', 'y-up' or 'z-up', got {orient!r}")
    import open3d as o3d  # noqa: PLC0415

    m = o3d.io.read_triangle_mesh(str(path))
    V = np.asarray(m.vertices, dtype=np.float64)
    F = np.asarray(m.triangles, dtype=np.int64)
    if len(V) == 0 or len(F) == 0:
        raise ValueError(f"{path}: no triangles")
    V = V - V.mean(axis=0)
    if orient == "flat":
        _, _, Vt = np.linalg.svd(V[:: max(1, len(V) // 200000)], full_matrices=False)
        R = Vt.copy()
        if np.linalg.det(R) < 0:
            R[2] *= -1.0
        V = V @ R.T
    elif orient == "y-up":
        V = np.stack([V[:, 0], -V[:, 2], V[:, 1]], axis=1)  # +90 deg about x: y -> z
    if flip:
        V = V * np.array([1.0, -1.0, -1.0])
    ext = V.max(axis=0) - V.min(axis=0)
    s = float(size_mm) / float(ext.max()) if size_mm is not None else float(scale)
    V = V * s
    lo, hi = V.min(axis=0), V.max(axis=0)
    V = V - np.array([0.5 * (lo[0] + hi[0]), 0.5 * (lo[1] + hi[1]), lo[2]])
    return V, F


def mesh_on_floor(path: str, size_mm: float | None = None, scale: float = 1.0, orient: str = "flat",
                  flip: bool = False, lift_mm: float = 0.0, seed: int = 0, albedo: Albedo | float | None = None,
                  name: str | None = None) -> Scene:
    """A mesh file (:func:`load_mesh`) lying on a dark floor, ``lift_mm`` above it (on supports
    that aren't modelled), painted with low-contrast paint noise. ``meta["object_mm"]`` is its
    size, ``meta["mesh"]`` the world vertices and triangles (the ground truth)."""
    V, F = load_mesh(path, size_mm=size_mm, scale=scale, orient=orient, flip=flip)
    V = V + np.array([0.0, 0.0, float(lift_mm)])
    alb = albedo if albedo is not None else NoiseAlbedo(mean=0.72, contrast=0.015, scale_mm=4.0, seed=seed)
    obj = TriangleMesh(None, V, F, alb, name="object")
    size = V.max(axis=0) - V.min(axis=0)
    floor = _table(0.0, float(max(800.0, 0.75 * size[:2].max() + 300.0)),
                   NoiseAlbedo(mean=0.3, contrast=0.08, scale_mm=5.0, seed=seed + 1), name="floor")
    return Scene([floor, obj], name=name or f"mesh:{Path(path).stem}",
                 target=(0.0, 0.0, float(V[:, 2].max()) * 0.5), radius=float(np.linalg.norm(size) / 2),
                 meta={"object_mm": size.tolist(), "mesh": (V, F), "source": str(path)})


# --------------------------------------------------------------------------- scene
@dataclass
class Hit:
    """Primary-ray result. ``normal`` faces the incoming ray; rows with ``obj == -1`` are misses
    (``t = inf``, zero normal and albedo)."""

    t: np.ndarray
    obj: np.ndarray
    normal: np.ndarray
    albedo: np.ndarray


class Scene:
    """A list of primitives (object id = index) plus metadata used by the presets.

    ``target`` / ``radius``: what a scan trajectory should look at; ``markers``: true world
    corners (4,3) of AprilTag decals by id (ArUco order TL, TR, BR, BL); ``meta``: free-form dict
    (e.g. ``meta["trajectory"]`` holds suggested :class:`~ohs.sim.trajectory.HandheldTrajectory`
    keyword arguments, ``meta["board"]`` the ChArUco spec and ``T_wb``).
    """

    def __init__(
        self,
        primitives: Sequence[Primitive] = (),
        name: str = "scene",
        target: Sequence[float] = (0.0, 0.0, 0.0),
        radius: float = 100.0,
        markers: dict[int, np.ndarray] | None = None,
        meta: dict | None = None,
    ) -> None:
        self.primitives: list[Primitive] = list(primitives)
        self.name = name
        self.target = np.asarray(target, dtype=np.float64).reshape(3)
        self.radius = float(radius)
        self.markers: dict[int, np.ndarray] = dict(markers or {})
        self.meta: dict = dict(meta or {})

    def add(self, prim: Primitive) -> int:
        self.primitives.append(prim)
        return len(self.primitives) - 1

    def object_id(self, name: str) -> int:
        for i, p in enumerate(self.primitives):
            if p.name == name:
                return i
        raise KeyError(name)

    def cast(self, o: np.ndarray, d: np.ndarray, pix_angle: np.ndarray | float | None = None, surface: bool = True) -> Hit:
        """Nearest hit along rays ``o + t d`` (``d`` unit). ``pix_angle`` (rad per sample) sets the
        texture footprint ``t * pix_angle / sqrt(|cos|)``. ``surface=False`` skips normals and
        albedo (zeros), e.g. for depth-only casts."""
        o = np.asarray(o, dtype=np.float64)
        d = np.asarray(d, dtype=np.float64)
        n = len(d)
        t_best = np.full(n, np.inf)
        obj = np.full(n, -1, dtype=np.int32)
        for i, prim in enumerate(self.primitives):
            t = prim.intersect(o, d)
            better = t < t_best
            t_best[better] = t[better]
            obj[better] = i
        normal = np.zeros((n, 3))
        albedo = np.zeros(n)
        if not surface:
            return Hit(t_best, obj, normal, albedo)
        pa = None if pix_angle is None else np.broadcast_to(np.asarray(pix_angle, dtype=np.float64), (n,))
        for i, prim in enumerate(self.primitives):
            idx = np.flatnonzero(obj == i)
            if idx.size == 0:
                continue
            di = d[idx]
            p = (o[idx] if o.ndim > 1 else o) + t_best[idx, None] * di
            # footprint uses the geometric normal, computed by shade(); estimate from a first pass
            nl = prim._hit_normal_local((p - prim.t) @ prim.R, idx, n)
            nw = nl @ prim.R.T
            cosv = np.abs(np.einsum("ij,ij->i", nw, di))
            fp = np.zeros(idx.size) if pa is None else t_best[idx] * pa[idx] / np.sqrt(np.maximum(cosv, 0.05))
            alb = prim.albedo((p - prim.t) @ prim.R, nl, fp)
            flip = np.einsum("ij,ij->i", nw, di) > 0
            nw[flip] *= -1.0
            normal[idx] = nw
            albedo[idx] = alb
        return Hit(t_best, obj, normal, albedo)

    def occluded(self, o: np.ndarray, d: np.ndarray, t_max: np.ndarray) -> np.ndarray:
        """True where something is hit at ``EPS_T < t < t_max`` (shadow rays)."""
        o = np.asarray(o, dtype=np.float64)
        d = np.asarray(d, dtype=np.float64)
        t_max = np.broadcast_to(np.asarray(t_max, dtype=np.float64), (len(d),))
        occ = np.zeros(len(d), dtype=bool)
        for prim in self.primitives:
            todo = np.flatnonzero(~occ)
            if todo.size == 0:
                break
            oo = o[todo] if o.ndim > 1 else o
            t = prim.intersect(oo, d[todo])
            occ[todo] = t < t_max[todo]
        return occ


# --------------------------------------------------------------------------- printed textures
def _aruco_dictionary(name: str):
    try:
        key = getattr(cv2.aruco, name)
    except AttributeError as exc:
        raise ValueError(f"unknown ArUco dictionary {name!r}") from exc
    return cv2.aruco.getPredefinedDictionary(key)


def charuco_texture(
    squares_x: int = 11,
    squares_y: int = 8,
    square_mm: float = 20.0,
    marker_mm: float = 15.0,
    dictionary: str = "DICT_5X5_100",
    px_per_mm: float = 10,
) -> np.ndarray:
    """ChArUco board image (uint8) covering exactly ``squares_x * square_mm`` x
    ``squares_y * square_mm`` at ``px_per_mm`` (no margin), built with ``cv2.aruco.CharucoBoard``
    defaults (non-legacy pattern). Pixel ``(col, row)`` is board point
    ``((col + 0.5) / px_per_mm, (row + 0.5) / px_per_mm)`` in the OpenCV board frame."""
    board = cv2.aruco.CharucoBoard((int(squares_x), int(squares_y)), float(square_mm), float(marker_mm), _aruco_dictionary(dictionary))
    w = int(round(squares_x * square_mm * px_per_mm))
    h = int(round(squares_y * square_mm * px_per_mm))
    return board.generateImage((w, h), marginSize=0, borderBits=1)


def apriltag_texture(tag_id: int, tag_mm: float = 20, dictionary: str = "DICT_APRILTAG_36h11", px_per_mm: float = 10) -> np.ndarray:
    """Marker image (uint8) whose outer black square is ``tag_mm`` wide (OpenCV marker length);
    add a white quiet zone when placing it (see :class:`Decal` ``margin_mm``)."""
    side = int(round(tag_mm * px_per_mm))
    return cv2.aruco.generateImageMarker(_aruco_dictionary(dictionary), int(tag_id), side, borderBits=1)


def marker_corners_world(prim: Primitive, decal: Decal) -> np.ndarray:
    """World coordinates (4,3) of a decal's image corners (TL, TR, BR, BL) on a Rect/HeightField."""
    xy = decal.corners_local()
    if isinstance(prim, HeightField):
        z = prim.height(xy[:, 0], xy[:, 1])[0]
    else:
        z = np.zeros(4)
    pl = np.column_stack([xy, z])
    return pl @ prim.R.T + prim.t


# --------------------------------------------------------------------------- presets
_R_TABLE = np.diag([1.0, -1.0, -1.0])  # local frame of a Z-up table viewed from above


def _table(z: float = 0.0, half: float = 600.0, albedo: Albedo | float | None = None, name: str = "table") -> Rect:
    return Rect(make_se3(_R_TABLE, [0.0, 0.0, z]), (-half, half, -half, half), albedo, name=name)


def _tag_decals(ids: Sequence[int], radius: float, tag_mm: float, seed: int, px_per_mm: float = 10) -> list[Decal]:
    rng = np.random.default_rng(seed + 101)
    decals = []
    for k, tid in enumerate(ids):
        ang = 2 * math.pi * k / len(ids) + rng.uniform(-0.15, 0.15)
        r = radius + rng.uniform(-8, 8)
        decals.append(
            Decal(
                apriltag_texture(tid, tag_mm, px_per_mm=px_per_mm),
                px_per_mm,
                (r * math.cos(ang), r * math.sin(ang)),
                angle=rng.uniform(-math.pi, math.pi),
                margin_mm=tag_mm / 4.0,
                tag_id=int(tid),
            )
        )
    return decals


def sphere_on_plane(seed: int = 0, radius: float = 40.0, tags: bool = True, tag_mm: float = 20.0) -> Scene:
    """Sphere (r = 40 mm, resting on the table at the origin) on a large matte table with
    AprilTag 36h11 stickers ids 0..5 on a ring of ~95 mm around it (``Scene.markers``)."""
    base = NoiseAlbedo(mean=0.55, contrast=0.05, scale_mm=3.0, seed=seed)
    decals = _tag_decals(range(6), 95.0, tag_mm, seed) if tags else []
    table = _table(0.0, 600.0, DecalAlbedo(base, decals) if decals else base, name="table")
    sphere = Sphere((0.0, 0.0, radius), radius, NoiseAlbedo(mean=0.8, contrast=0.02, scale_mm=2.5, seed=seed + 1), name="sphere")
    markers = {dc.tag_id: marker_corners_world(table, dc) for dc in decals}
    return Scene(
        [table, sphere],
        name="sphere_on_plane",
        target=(0.0, 0.0, radius * 0.75),
        radius=radius + 30,
        markers=markers,
        meta={
            "sphere_center": [0.0, 0.0, radius],
            "sphere_radius": radius,
            "plane": {"normal": [0.0, 0.0, 1.0], "d": 0.0},
            "trajectory": {"distance": (220.0, 300.0), "elevation_deg": (45.0, 70.0), "sweep_deg_s": 20.0},
        },
    )


def _panel_height(Rx: float, Ry: float, crease_y: float, crease_k: float, crease_r: float) -> HeightFn:
    def h(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        q = np.sqrt((y - crease_y) ** 2 + crease_r**2)
        z = -(x * x) / (2 * Rx) - (y * y) / (2 * Ry) - crease_k * (q - crease_r)
        zx = -x / Rx
        zy = -y / Ry - crease_k * (y - crease_y) / q
        return z, zx, zy

    return h


def car_panel(seed: int = 0, tags: bool = False, tag_mm: float = 20.0) -> Scene:
    """~300 x 200 mm painted car panel 120 mm above a dark floor: gently doubly curved
    (radii 900 / 1400 mm), a crease (styling line, ~14 deg slope change, 3 mm radius) at
    local y = 30 mm and a 25 mm hole. Nearly uniform albedo (1.5 % paint noise)."""
    hfn = _panel_height(900.0, 1400.0, 30.0, 0.12, 3.0)
    T = make_se3(so3_exp(np.radians([8.0, -4.0, 15.0])), [0.0, 0.0, 120.0])
    bounds = (-150.0, 150.0, -100.0, 100.0)
    holes = [(85.0, -45.0, 12.5)]
    base = NoiseAlbedo(mean=0.72, contrast=0.015, scale_mm=4.0, seed=seed)
    decals: list[Decal] = []
    if tags:
        rng = np.random.default_rng(seed + 7)
        pos = [(-110, -70), (-110, 70), (0, -75), (0, 72), (110, 70), (115, -10)]
        for k, (x, y) in enumerate(pos):
            # +z is the top side of a heightfield: mirror the decal so it reads from above
            decals.append(
                Decal(
                    apriltag_texture(k, tag_mm),
                    10,
                    (float(x), float(y)),
                    angle=rng.uniform(-math.pi, math.pi),
                    margin_mm=tag_mm / 4,
                    tag_id=k,
                    flip_y=True,
                )
            )
    alb: Albedo = DecalAlbedo(base, decals) if decals else base
    # z extent of the surface over the domain, for root bracketing (HeightField pads it by 0.5 mm)
    xs, ys = np.meshgrid(np.linspace(-150, 150, 61), np.linspace(-100, 100, 41))
    zs = hfn(xs.ravel(), ys.ravel())[0]
    panel = HeightField(T, hfn, bounds, (float(zs.min()), float(zs.max())), holes, alb, name="panel")
    floor = _table(0.0, 800.0, NoiseAlbedo(mean=0.3, contrast=0.08, scale_mm=5.0, seed=seed + 1), name="floor")
    markers = {dc.tag_id: marker_corners_world(panel, dc) for dc in decals}
    return Scene(
        [floor, panel],
        name="car_panel",
        target=(0.0, 0.0, 120.0),
        radius=180.0,
        markers=markers,
        meta={
            "panel_T_wo": T.tolist(),
            "trajectory": {"distance": (230.0, 300.0), "elevation_deg": (60.0, 80.0), "sweep_deg_s": 12.0, "pan_mm": (120.0, 0.0, 0.0)},
        },
    )


def bracket(seed: int = 0) -> Scene:
    """A machined L-bracket on a table: base plate, upright, two bosses and a cross shaft."""
    metal = NoiseAlbedo(mean=0.6, contrast=0.03, scale_mm=2.0, seed=seed + 3)  # bead-blasted aluminium
    Rz = so3_exp(np.array([0.0, 0.0, math.radians(20.0)]))

    def pose(R_local: np.ndarray, t_local: Sequence[float]) -> np.ndarray:
        return make_se3(Rz @ R_local, Rz @ np.asarray(t_local, dtype=np.float64))

    I3 = np.eye(3)
    Rx90 = so3_exp(np.array([math.pi / 2, 0.0, 0.0]))  # local z -> world -y (shaft along y)
    prims: list[Primitive] = [
        _table(0.0, 600.0, NoiseAlbedo(mean=0.45, contrast=0.06, scale_mm=4.0, seed=seed), name="table"),
        Box(pose(I3, [0.0, 0.0, 4.0]), (45.0, 30.0, 4.0), metal, name="base"),
        Box(pose(I3, [0.0, -26.0, 34.0]), (45.0, 4.0, 26.0), metal, name="upright"),
        Cylinder(pose(I3, [-28.0, 12.0, 12.0]), 7.0, 4.0, metal, name="boss_left"),
        Cylinder(pose(I3, [28.0, 12.0, 12.0]), 7.0, 4.0, metal, name="boss_right"),
        Cylinder(pose(Rx90, [0.0, -12.0, 42.0]), 8.0, 22.0, NoiseAlbedo(mean=0.75, contrast=0.02, scale_mm=2.0, seed=seed + 4), name="shaft"),
        Box(pose(I3, [0.0, 18.0, 10.0]), (12.0, 8.0, 2.0), metal, name="tab"),
    ]
    return Scene(
        prims,
        name="bracket",
        target=(0.0, -5.0, 22.0),
        radius=70.0,
        meta={"trajectory": {"distance": (200.0, 280.0), "elevation_deg": (35.0, 65.0), "sweep_deg_s": 22.0}},
    )


def charuco_board(
    T_wb: np.ndarray | None = None,
    background: bool = True,
    margin_mm: float | None = None,
    page_mm: tuple[float, float] | None = None,
    px_per_mm: float = 10,
    paper: str = "letter",
    **spec: object,
) -> Scene:
    """ChArUco board (``spec``: squares_x, squares_y, square_mm, marker_mm, dictionary - the
    ``CharucoSpec`` fields) on its paper, at pose ``T_wb``.

    By default the paper is exactly the page that ``ohs.calib.board.render_charuco_page`` prints
    on ``paper`` (the source of truth): same orientation, board position (for the default board
    on letter: 29.7 mm of white left/right, 18.4 mm above, the scale bar and two lines of label
    text below), rendered from that page raster at ``px_per_mm`` (the board area is identical to
    :func:`charuco_texture`). ``page_mm`` (legacy) instead centres the board on a blank page of
    that size, and ``margin_mm`` uses the board plus that much blank margin; a board that does
    not fit on ``paper`` falls back to a 5 mm margin. ``meta["board"]["page"]`` holds the layout.
    The board frame is OpenCV's: origin at the board's outer top-left corner, x along squares_x,
    y along squares_y, z into the board (front side = -z). ``T_wb=None`` lays the board on a
    Z-up table centred at the world origin (``R_wb = diag(1, -1, -1)``). With ``background`` a
    grey table plane lies 2 mm behind the board. Usage: ``charuco_board(T_wb, **asdict(spec))``.
    """
    s = {"squares_x": 11, "squares_y": 8, "square_mm": 20.0, "marker_mm": 15.0, "dictionary": "DICT_5X5_100"}
    unknown = set(spec) - set(s)
    if unknown:
        raise TypeError(f"unknown ChArUco spec fields {sorted(unknown)}")
    s.update(spec)  # type: ignore[arg-type]
    W = float(s["squares_x"]) * float(s["square_mm"])  # type: ignore[arg-type]
    H = float(s["squares_y"]) * float(s["square_mm"])  # type: ignore[arg-type]
    if T_wb is None:
        T_wb = make_se3(_R_TABLE, [-W / 2, H / 2, 0.0])
    T_wb = _pose(T_wb)
    page_meta = None
    tex = None
    if margin_mm is None and page_mm is None:
        from ohs.calib.board import CharucoSpec, render_charuco_page_image  # noqa: PLC0415 (printed page = truth)

        try:
            img, layout = render_charuco_page_image(CharucoSpec(**s), paper=paper, dpi=float(px_per_mm) * 25.4)  # type: ignore[arg-type]
        except ValueError as exc:
            log.warning("charuco_board: %s; using a 5 mm blank margin instead of the printed page", exc)
            margin_mm = 5.0
        else:
            # pad (white, outside the paper bounds) so that the board origin sits on a multiple of
            # 64 texels: the mip levels then sample the board exactly like charuco_texture()
            bx, by = (int(round(v * float(px_per_mm))) for v in layout.board_origin_mm)
            px, py = (-bx) % 64, (-by) % 64
            img = cv2.copyMakeBorder(img, py, 0, px, 0, cv2.BORDER_CONSTANT, value=255)
            tex = TextureAlbedo(img, px_per_mm, (-(bx + px) / px_per_mm, -(by + py) / px_per_mm), border=PAPER_WHITE)
            bounds = layout.paper_bounds_mm
            page_meta = layout.to_dict()
    if tex is None:
        tex = TextureAlbedo(charuco_texture(px_per_mm=px_per_mm, **s), px_per_mm, (0.0, 0.0), border=PAPER_WHITE)  # type: ignore[arg-type]
        if margin_mm is not None:
            mx = my = float(margin_mm)
        else:
            pm = (279.4, 215.9) if page_mm is None else page_mm
            mx = max((float(pm[0]) - W) / 2, 5.0)
            my = max((float(pm[1]) - H) / 2, 5.0)
        bounds = (-mx, W + mx, -my, H + my)
    board = Rect(T_wb, bounds, tex, name="board")
    prims: list[Primitive] = [board]
    if background:
        T_bg = T_wb @ make_se3(t=[W / 2, H / 2, 2.0])
        prims.append(Rect(T_bg, (-1500.0, 1500.0, -1500.0, 1500.0), NoiseAlbedo(mean=0.35, contrast=0.08, scale_mm=6.0, seed=5), name="background"))
    center = (T_wb @ np.array([W / 2, H / 2, 0.0, 1.0]))[:3]
    meta = {"board": {**s, "T_wb": T_wb.tolist(), "paper_bounds_mm": [float(b) for b in bounds], "page": page_meta}}
    return Scene(prims, name="charuco_board", target=center, radius=0.5 * math.hypot(W, H), meta=meta)


SCENE_PRESETS: dict[str, Callable[..., Scene]] = {
    "sphere_on_plane": sphere_on_plane,
    "car_panel": car_panel,
    "bracket": bracket,
}


def make_scene(scene: str | Scene, seed: int = 0) -> Scene:
    """A preset by name (``sphere_on_plane``, ``car_panel``, ``bracket``) or the scene itself."""
    if isinstance(scene, Scene):
        return scene
    try:
        return SCENE_PRESETS[scene](seed=seed)
    except KeyError as exc:
        raise ValueError(f"unknown scene {scene!r}; presets: {sorted(SCENE_PRESETS)}") from exc
