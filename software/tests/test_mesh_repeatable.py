"""Edits that give the same result whatever order the mesh lists its vertices and faces in
(``MAINTENANCE_PLAN.md`` F15, the owner's decision D9: an option, ``by_position``, off by default).

Found in review: a scan made twice from the same frames had the same vertices and faces in another
order (the GPU path lists them as its threads finish), and 12 of the 769,362 faces of the result
differed, all made by hole filling, which picks among equally good triangulations by vertex number.

The guards, for every edit the app runs (remove loose pieces, fill holes, smooth in its three ways,
simplify to a count and to a tolerance, find the table): the edit runs on a mesh and on the same mesh
with its vertices renumbered at random, its faces shuffled and each face's corners rotated; with
``by_position`` the two results are the same content, bit for bit (vertices named by their values,
faces by their vertices' values). The meshes have real ties: a regular grid with round holes punched
in it, a box, a sphere, a sheet whose seam has pairs of vertices equal in every bit, a thin plate
(0.3 mm) with a hole in one face, a box and a thin disc on a table. With the option off the same
shuffle gives other triangles (so the guard is not empty).

And with the option on nothing measured changes: every edit leaves the vertices and faces it does not
make as they were, hole filling covers the same holes with patches of the same area, and the thin
plate's patch stays in its face (nothing bridges to the other side).

The edits are numpy / scipy only: they do not depend on the compute path, so this runs once.
"""
from __future__ import annotations

import numpy as np
import pytest

from mesh_shapes import box, grid, icosphere, punch
from ohs import mesh as M
from ohs.mesh import support
from ohs.mesh.order import canonical
from ohs.mesh.topology import boundary_loops, face_areas, vertex_normals
from test_mesh_support import box_on_table, raise_disc


# --------------------------------------------------------------------------- helpers
def shuffle(V: np.ndarray, F: np.ndarray, seed: int, *extra: np.ndarray | None):
    """The same mesh, its vertices renumbered at random (``extra`` per-vertex arrays with them), its
    faces in a random order, each face's corners rotated (orientation kept)."""
    rng = np.random.default_rng(seed)
    pv = rng.permutation(len(V))
    new = np.empty_like(pv)
    new[pv] = np.arange(len(pv))
    F2 = new[F[rng.permutation(len(F))]]
    r = rng.integers(0, 3, len(F2))
    F2 = np.stack([F2[np.arange(len(F2)), (r + i) % 3] for i in range(3)], axis=1)
    return (V[pv], F2, *[None if x is None else x[pv] for x in extra])


def content(V: np.ndarray, F: np.ndarray, decimals: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The mesh as content, independent of its order: every vertex's coordinates as raw bits, sorted;
    every face as its three vertices named by their values (smallest first, orientation kept), sorted
    (two vertices equal in every bit are one name, as in ``test_server_keep_thin.mesh_content``).
    ``decimals``: coordinates rounded first (the same choice, last bits aside)."""
    X = np.ascontiguousarray(V if decimals is None else np.round(V, decimals) + 0.0, dtype=np.float64)
    bits = X.view(np.uint64)
    _, inv = np.unique(bits, axis=0, return_inverse=True)
    G = inv.reshape(-1)[F]
    r = np.argmin(G, axis=1)
    G = np.stack([G[np.arange(len(G)), (r + i) % 3] for i in range(3)], axis=1)
    return bits[np.lexsort(bits.T[::-1])], G[np.lexsort(G.T[::-1])]


def same(a: tuple, b: tuple, decimals: int | None = None) -> bool:
    (Va, Fa), (Vb, Fb) = a[:2], b[:2]
    ca, cb = content(Va, Fa, decimals), content(Vb, Fb, decimals)
    return all(x.shape == y.shape and np.array_equal(x, y) for x, y in zip(ca, cb))


def thin_plate(t: float = 0.3, n: int = 41, step: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """A closed plate ``t`` mm thick (top at +t/2, bottom at -t/2, one row of wall faces), 20 x 20 mm."""
    V, F = grid(n, n, step)
    top = V + [0.0, 0.0, t / 2]
    bot = V - [0.0, 0.0, t / 2]
    Fb = F[:, [0, 2, 1]] + len(V)
    lp = boundary_loops(top, F)
    u, v = lp.start, lp.end  # top's border half-edges u -> v
    walls = np.concatenate([np.stack([v, u, u + len(V)], 1), np.stack([v, u + len(V), v + len(V)], 1)])
    return np.vstack([top, bot]), np.vstack([F, Fb, walls])


def plate_with_holes(t: float = 0.3):
    """The thin plate with a round hole in its top face and another in its bottom face."""
    V, F = thin_plate(t)
    C = V[F].mean(axis=1)
    hole_top = (C[:, 2] > t / 4) & (np.linalg.norm(C[:, :2] - [7.0, 8.0], axis=1) < 2.6)
    hole_bot = (C[:, 2] < -t / 4) & (np.linalg.norm(C[:, :2] - [13.0, 11.0], axis=1) < 1.7)
    return V, F[~hole_top & ~hole_bot]


def seam_sheet():
    """Two grids side by side, not welded: the seam's vertices come in pairs equal in every bit, with
    other neighbours each; a hole punched in each half."""
    V, F = grid(16, 16, 1.0)
    V2 = V + [15.0, 0.0, 0.0]
    V, F = np.vstack([V, V2]), np.vstack([F, F + len(V)])
    V[:, 2] = 0.2 * np.sin(V[:, 0] * 0.7) * np.cos(V[:, 1] * 0.5)
    return punch(V, F, [[7.0, 7.0, 0.0], [23.0, 8.0, 0.0]], [2.1, 3.1])


def noisy(V: np.ndarray, F: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return V + rng.normal(0.0, sigma, len(V))[:, None] * vertex_normals(V, F)


MESHES = {
    "grid with holes": lambda: punch(*grid(30, 30, 1.0), [[8, 8, 0], [20, 21, 0], [22, 7, 0]], [1.6, 2.6, 4.2]),
    "box with holes": lambda: punch(*box(20.0, 12), [[10, 0, 0], [0, 10, 3], [-10, 4, -4]], [1.0, 2.0, 3.5]),
    "sphere with holes": lambda: (lambda V, F: (noisy(V, F, 0.05, 1), F))(
        *punch(*icosphere(20.0, 3), [[20, 0, 0], [0, 0, 20], [0, -20, 0]], [2.5, 4.0, 6.0])),
    "seam sheet": seam_sheet,
    "thin plate with holes": plate_with_holes,
}

EDITS = {
    "fill holes 5 mm": lambda V, F, **k: M.fill_holes(V, F, 5.0, **k),
    "fill holes 30 mm": lambda V, F, **k: M.fill_holes(V, F, 30.0, **k),
    "make watertight": lambda V, F, **k: M.fill_holes(V, F, 1e4, include_borders=True, **k),
    "remove loose pieces": lambda V, F, **k: M.remove_floaters(V, F, min_size_mm=5.0, min_fraction=0.01, **k),
    "smooth (Taubin)": lambda V, F, **k: M.smooth(V, F, "taubin", 0.5, True, **k),
    "smooth (bilateral, away from edges)": lambda V, F, **k: M.smooth(V, F, "bilateral", 0.5, True,
                                                                     away_from_edges=True, **k),
    "smooth (bilateral, flat areas only)": lambda V, F, **k: M.smooth(V, F, "bilateral", 0.5, True, flat_only=True, **k),
    "simplify to half": lambda V, F, **k: M.decimate(V, F, ratio=0.5, **k),
    "simplify within 0.05 mm": lambda V, F, **k: M.decimate(V, F, max_error_mm=0.05, **k),
}


@pytest.fixture(scope="module", params=list(MESHES))
def mesh(request):
    return request.param, MESHES[request.param]()


# --------------------------------------------------------------------------- the guards
@pytest.mark.parametrize("edit", list(EDITS))
def test_by_position_gives_the_same_result_whatever_the_order(mesh, edit):
    name, (V, F) = mesh
    fn = EDITS[edit]
    a = fn(V, F, by_position=True)
    for seed in (1, 2):
        V2, F2 = shuffle(V, F, seed)
        b = fn(V2, F2, by_position=True)
        assert same(a, b), f"{edit} on the {name}: another result after shuffle {seed}"


def test_the_meshes_have_real_ties():
    """With the option off (as today) a shuffle gives other triangles: hole filling on the grid and
    the seam sheet, simplifying the grid. So the guard above is not empty."""
    for name, fn in (("grid with holes", EDITS["fill holes 30 mm"]), ("seam sheet", EDITS["fill holes 30 mm"]),
                     ("grid with holes", EDITS["simplify to half"])):
        V, F = MESHES[name]()
        a = fn(V, F)
        assert any(not same(a, fn(*shuffle(V, F, s)), decimals=6) for s in (1, 2, 3)), name


def test_the_option_keeps_the_input_and_the_order_of_what_it_keeps():
    """Fill holes and smooth keep the input's numbering (old vertices and faces first, as they were:
    what the sigmas and the filled flags follow); remove loose pieces keeps its faces in their order."""
    V, F = MESHES["grid with holes"]()
    V2, F2 = M.fill_holes(V, F, 30.0, by_position=True)
    assert np.array_equal(V2[: len(V)], V) and np.array_equal(F2[: len(F)], F) and len(F2) > len(F)
    X, F3 = M.smooth(V, F, "taubin", 0.5, by_position=True)
    assert np.array_equal(F3, F) and X.shape == V.shape
    # each vertex moves where it moves without the option, to the last bits
    X0, _ = M.smooth(V, F, "taubin", 0.5)
    assert np.allclose(X, X0, rtol=0, atol=1e-9)
    # a limit per vertex follows its vertex
    lim = np.linspace(0.0, 0.02, len(V))
    XL, _ = M.smooth(V, F, "taubin", 0.5, limit=lim, by_position=True)
    assert np.all(np.linalg.norm(XL - V, axis=1) <= lim + 1e-12)
    Vs, Fs, Ls = shuffle(V, F, 5, lim)
    XS, _ = M.smooth(Vs, Fs, "taubin", 0.5, limit=Ls, by_position=True)
    assert same((XL, F), (XS, Fs))
    Vf = np.vstack([V, V[:3] + [100.0, 0.0, 0.0]])
    Ff = np.vstack([F, [[len(V), len(V) + 1, len(V) + 2]]])
    on = M.remove_floaters(Vf, Ff, min_size_mm=5.0, return_kept=True, by_position=True)
    off = M.remove_floaters(Vf, Ff, min_size_mm=5.0, return_kept=True)
    assert np.array_equal(on[2], np.arange(len(F))) and all(np.array_equal(a, b) for a, b in zip(on, off))


def test_of_two_equal_pieces_the_same_one_is_the_largest():
    """Keep the largest piece only, with two pieces of the same count of triangles: by number today
    (whichever comes first), by position with the option."""
    V, F = grid(8, 8, 1.0)
    V, F = np.vstack([V, V + [50.0, 0.0, 0.0]]), np.vstack([F, F + len(V)])
    picks = set()
    for seed in range(6):
        V2, F2 = shuffle(V, F, seed)
        out = M.remove_floaters(V2, F2, keep_largest_only=True, by_position=True)
        picks.add(round(float(out[0][:, 0].min()), 6))
        assert same(out, M.remove_floaters(V, F, keep_largest_only=True, by_position=True))
    assert picks == {0.0}
    off = {round(float(M.remove_floaters(*shuffle(V, F, s), keep_largest_only=True)[0][:, 0].min()), 6) for s in range(6)}
    assert off == {0.0, 50.0}  # today: by number


def test_the_table_is_found_the_same_whatever_the_order():
    """Find the table (and remove it): the same faces, the same thin part kept, the same numbers."""
    V, F, _ = box_on_table(hx=20.0, hy=15.0, H=15.0, half=60.0, seed=3)
    V = raise_disc(V, [0, 0, 1], 0.0, [35.0, 35.0, 0.0], 12.0, 0.5)
    S = np.full(len(V), 0.1)
    r = support.find_support(V, F, sigma=S, by_position=True)
    assert r.raised and any(x["kept"] for x in r.raised)  # the thin disc is kept
    for seed in (1, 2):
        V2, F2, S2 = shuffle(V, F, seed, S)
        r2 = support.find_support(V2, F2, sigma=S2, by_position=True)
        assert same((V, F[r.faces]), (V2, F2[r2.faces])) and same((V, F[r.kept]), (V2, F2[r2.kept]))
        d, d2 = r.to_dict(), r2.to_dict()
        d.pop("ms"), d2.pop("ms")
        assert d == d2
        for x, y in zip(r.raised, r2.raised):
            assert same((V, F[x["faces"]]), (V2, F2[y["faces"]]))
    # the faces are the input's, in order, and remove_support applies them
    assert np.all(np.diff(r.faces) > 0) and np.all(np.diff(r.kept) > 0) and len(r.faces) + len(r.kept) == len(F)
    V3, F3, _ = support.remove_support(V, F, sigma=S, by_position=True)
    assert same((V3, F3), M.keep_faces(V, F, r.kept))
    # nothing measured changes: the same table, the same thin part, as without the option
    r0 = support.find_support(V, F, sigma=S)
    assert abs(len(r0.faces) - len(r.faces)) <= 0.001 * len(F) and [x["kept"] for x in r0.raised] == [x["kept"] for x in r.raised]


def test_the_sigmas_and_filled_flags_follow_a_simplified_mesh_the_same_way():
    """A simplified vertex halfway along an edge is as near to both ends: its sigma (and looks) come
    from the old vertex numbered first, and a new face's filled flag from the nearest old face; with
    the option the old mesh is searched in its order by position, so the result is the same."""
    V, F = MESHES["grid with holes"]()
    rng = np.random.default_rng(4)
    S = rng.uniform(0.05, 0.5, len(V)).astype(np.float32)
    V1, F1 = M.fill_holes(V, F, 30.0, by_position=True)
    S1 = M.carry_sigma("fill_holes", V, F, S, V1)
    Fl1 = M.carry_filled("fill_holes", V, F, None, V1, F1)

    def run(V1, F1, S1, Fl1):
        V2, F2 = M.decimate(V1, F1, ratio=0.5, by_position=True)
        S2 = M.carry_sigma("decimate", V1, F1, S1, V2, by_position=True)
        Fl2 = M.carry_filled("decimate", V1, F1, Fl1, V2, F2, by_position=True)
        return np.column_stack([V2, S2.astype(np.float64)]), F2, Fl2

    a = run(V1, F1, S1, Fl1)
    rng = np.random.default_rng(9)
    pv = rng.permutation(len(V1))
    new = np.empty_like(pv)
    new[pv] = np.arange(len(pv))
    pf = rng.permutation(len(F1))
    b = run(V1[pv], new[F1[pf]], S1[pv], Fl1[pf])
    ca, cb = content(a[0], a[1]), content(b[0], b[1])
    assert all(np.array_equal(x, y) for x, y in zip(ca, cb))
    fa = np.sort(np.round(a[0][a[1][a[2]]].reshape(-1, 12), 12), axis=0)
    fb = np.sort(np.round(b[0][b[1][b[2]]].reshape(-1, 12), 12), axis=0)
    assert a[2].sum() == b[2].sum() > 0 and np.array_equal(fa, fb)


def test_vertices_equal_in_every_bit_are_told_apart_by_their_neighbours():
    V, F = seam_sheet()
    bits = np.ascontiguousarray(V).view(np.uint64)
    assert len(np.unique(bits, axis=0)) < len(V)  # the seam's pairs
    c1 = canonical(V, F)
    V2, F2 = shuffle(V, F, 7)
    c2 = canonical(V2, F2)
    assert np.array_equal(c1.V, c2.V) and np.array_equal(c1.F, c2.F)
    assert np.array_equal(c1.V, V[c1.pv]) and np.array_equal(c1.rank[c1.pv], np.arange(len(V)))
    assert same((V, F), (c1.V, c1.F))


# --------------------------------------------------------------------------- nothing measured changes
@pytest.mark.parametrize("name", ["grid with holes", "box with holes", "sphere with holes", "thin plate with holes"])
def test_filled_holes_are_the_same_holes_with_the_same_area(name):
    V, F = MESHES[name]()
    off = M.fill_holes(V, F, 30.0)
    on = M.fill_holes(V, F, 30.0, by_position=True)
    for V2, F2 in (off, on):  # the surface that was there is untouched
        assert np.array_equal(V2[: len(V)], V) and np.array_equal(F2[: len(F)], F)
    # the same holes are closed: the same outlines are left open (by value)
    lo, ln = boundary_loops(*off), boundary_loops(*on)
    assert sorted(np.round(lo.perimeter, 9)) == sorted(np.round(ln.perimeter, 9))
    # the patch over each hole: the same area to within 0.5 % (other diagonals, the same hole)
    ao = face_areas(*off)[len(F):].sum()
    an = face_areas(*on)[len(F):].sum()
    assert abs(an - ao) <= 0.005 * ao, (name, ao, an)
    # each patch uses the same outline vertices
    assert set(np.unique(off[1][len(F):][off[1][len(F):] < len(V)])) == set(np.unique(on[1][len(F):][on[1][len(F):] < len(V)]))


def test_simplifying_by_position_moves_the_surface_no_further():
    """Numbered by position alone, the equal-cost edges of a flat plate that make the candidate pool
    came from one end of it and the collapses bunched up there: the thin plate's surface moved 0.59 mm
    (99.9 %) instead of 0.30. Scrambled by a fixed shuffle they spread as in a scan's own order."""
    from ohs.mesh.simplify import deviation

    V, F = plate_with_holes()
    on = deviation(V, *M.decimate(V, F, ratio=0.5, by_position=True))
    off = [deviation(V2, *M.decimate(V2, F2, ratio=0.5)) for V2, F2 in [shuffle(V, F, s) for s in (1, 2, 3)]]
    assert np.percentile(on, 99.9) <= max(np.percentile(d, 99.9) for d in off) + 1e-9
    assert on.max() <= max(d.max() for d in off) + 1e-9


def test_the_thin_plates_patches_stay_in_their_faces():
    """A plate 0.3 mm thick with a hole in its top face and one in its bottom face: each is closed in its
    own face, flat, with no triangle reaching across to the other face, with the option on as off."""
    t = 0.3
    V, F = plate_with_holes(t)
    for kw in ({}, {"by_position": True}):
        V2, F2 = M.fill_holes(V, F, 30.0, **kw)
        new = F2[len(F):]
        assert len(new)
        z = V2[new][:, :, 2]
        side = np.sign(z.mean(axis=1))
        assert np.all(np.abs(z - side[:, None] * t / 2) < 1e-6), kw  # every corner in its own face
        assert set(side) == {-1.0, 1.0}
        assert len(V2) - len(V) == 0 or np.all(np.abs(np.abs(V2[len(V):, 2]) - t / 2) < 1e-6)
