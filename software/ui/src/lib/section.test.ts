/**
 * Checks for the section profile as the viewer draws and measures it (`section.ts`, `npm test`): the cutting plane by
 * the engine's rule, fitting the profile into the panel, zoom and pan, the distance with snapping, the scale bar, the
 * axes' names and the numbers. Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed
 * check throws.
 */
import type { PartFrameView, SectionAnswer, SectionLineCompact, SectionPlane } from "../api/types.ts";
import {
  axisNames,
  distance2d,
  fitView,
  panBy,
  planeFromDef,
  planeFromFit,
  planeFromFrame,
  planeFromPoints,
  profileNumbers,
  profileOf,
  scaleBar,
  snapTo,
  to3d,
  toDrawing,
  toScreen,
  zoomAbout,
} from "./section.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`section.test: ${what}`);
}
const near = (a: ArrayLike<number>, b: ArrayLike<number>, tol = 1e-9) => a.length === b.length && Array.from(a).every((x, i) => Math.abs(x - b[i]) <= tol);
const dot = (a: ArrayLike<number>, b: ArrayLike<number>) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
function throws(fn: () => unknown, needle: string): boolean {
  try {
    fn();
    return false;
  } catch (e) {
    return String((e as Error).message).includes(needle);
  }
}

// ---------------------------------------------------------------- the plane, by the engine's rule (ohs/mesh/section.py)
{
  // an upright plane: "up" is Z turned into it, u = v x n; the 2D origin is where the scan's origin falls on it
  const p = planeFromFit({ point: [5, 2, 3], normal: [2, 0, 0] }, 0, "Side", null);
  check(near(p.normal, [1, 0, 0]) && near(p.v, [0, 0, 1]) && near(p.u, [0, 1, 0]), "upright: v is Z, u = v x n");
  check(near(p.point, [5, 0, 0]), "the 2D origin: the scan's origin on the plane");
  check(p.description === "Section on Side" && !p.in_part_frame, "the description names the fit");
  const q = planeFromFit({ point: [5, 2, 3], normal: [1, 0, 0] }, -1.5, "Side", null);
  check(near(q.point, [3.5, 0, 0]) && q.description === "Section on Side, moved -1.5\u202fmm along its normal", "moved along the normal");
  // a level plane (within 45° of horizontal): "up" is Y
  const lv = planeFromFit({ point: [0, 0, 10], normal: [0, 0.3, 1] }, 0, "Top", null);
  const yIn = [0, 1, 0].map((x, i) => x - dot([0, 1, 0], lv.normal) * lv.normal[i]);
  check(near(lv.v, yIn.map((x) => x / Math.hypot(...yIn)), 1e-12), "level: v is Y turned into the plane");
  const cr = [lv.u[1] * lv.v[2] - lv.u[2] * lv.v[1], lv.u[2] * lv.v[0] - lv.u[0] * lv.v[2], lv.u[0] * lv.v[1] - lv.u[1] * lv.v[0]];
  check(near(cr, lv.normal, 1e-12), "u x v = n: never mirrored");
  check(Math.abs(dot(to3d(lv, 0, 0), lv.normal) - dot([0, 0, 10], lv.normal)) < 1e-12, "to3d lies on the plane");
  // three points: the right-hand rule, through their centroid; on a line refused in the engine's words
  const t = planeFromPoints([0, 0, 0], [1, 0, 0], [0, 1, 0], null);
  check(near(t.normal, [0, 0, 1]), "three points: (p2 - p1) x (p3 - p1)");
  check(throws(() => planeFromPoints([0, 0, 0], [1, 0, 0], [2, 0, 0], null), "lie on one line"), "three points on a line refused");
  check(throws(() => planeFromDef({ kind: "point_normal", point: [0, 0, 0], normal: [0, 0, 0] }, [], null), "zero"), "a zero normal refused");
  check(throws(() => planeFromDef({ kind: "frame", axis: "Z", value_mm: 1 }, [], null), "no part's axes"), "a frame axis without a frame refused");
}

// the part's frame: X is the scan's y, Y the scan's -x, Z the scan's z; the origin at (2, -1, 5)
const R = [
  [0, 1, 0],
  [-1, 0, 0],
  [0, 0, 1],
];
const O = [2, -1, 5];
const tr = R.map((row) => -(row[0] * O[0] + row[1] * O[1] + row[2] * O[2]));
const frame = {
  format_version: 1,
  units: "mm",
  matrix: [[...R[0], tr[0]], [...R[1], tr[1]], [...R[2], tr[2]], [0, 0, 0, 1]],
  axes: { primary: "Z", secondary: "X", third: "Y" },
  flip_primary: false,
  flip_secondary: false,
  secondary_zero: false,
  built_from: [],
  sigma: {},
  warnings: [],
  readable: true,
  out_of_date: false,
  out_of_date_reasons: [],
} as unknown as PartFrameView;
{
  const z = planeFromFrame(frame, "Z", 12);
  check(near(z.normal, [0, 0, 1]) && near(z.u, R[0]) && near(z.v, R[1]), "Z = 12 is drawn in the part's X and Y");
  check(near(z.point, [2, -1, 17]) && z.in_part_frame, "its 2D origin is the part's origin on the plane");
  check(z.description === "Section at Z = 12\u202fmm in the part's axes", "the engine's description");
  const front = planeFromFrame(frame, "-y", 2);
  check(near(front.normal, [1, 0, 0]) && near(front.u, R[0]) && near(front.v, R[2]), "Y = c seen from -Y: the front view in X and Z");
  check(front.description.endsWith("seen from -Y"), "said in its description");
  check(throws(() => planeFromFrame(frame, "W", 1), "axis must be"), "an unknown axis refused");
  const names = axisNames(z, frame);
  check(names.u === "X" && names.v === "Y", "the part's axes are named in capitals");
  check(axisNames(front, frame).u === "X" && axisNames(front, frame).v === "Z", "the front view's axes");
  const s = planeFromFit({ point: [0, 0, 0], normal: [1, 0, 0] }, 0, "Side", null);
  check(axisNames(s, null).u === "y" && axisNames(s, null).v === "z", "the scan's axes in small letters");
  const sk = planeFromFit({ point: [0, 0, 0], normal: [1, 1, 0] }, 0, "Skew", null);
  check(axisNames(sk, null).u === "u" && axisNames(sk, null).v === "z", "a direction along no axis is u");
  check(axisNames({ ...s, u: [0, -1, 0] }, null).u === "−y", "a reversed axis");
}

// ---------------------------------------------------------------- fitting the profile into the panel, zoom and pan
{
  const v = fitView([-10, -5, 30, 15], 400, 300, 20);
  // 40 x 20 mm into 360 x 260 px: width limits, 9 px per mm, centred
  check(Math.abs(v.scale - 9) < 1e-12 && v.cx === 10 && v.cy === 5, "fitted: the larger ratio limits, centred");
  const [sx, sy] = toScreen(v, 400, 300, -10, 15);
  check(Math.abs(sx - 20) < 1e-9 && Math.abs(sy - 60) < 1e-9, "the top left corner at the margin (y up)");
  check(near(toDrawing(v, 400, 300, sx, sy), [-10, 15]), "and back");
  const tall = fitView([0, 0, 2, 100], 400, 300, 20);
  check(Math.abs(tall.scale - 2.6) < 1e-12, "a tall profile: the height limits");
  const flat = fitView([0, 3, 50, 3], 400, 300, 20);
  check(Math.abs(flat.scale - 7.2) < 1e-12 && flat.cy === 3, "a straight line: its length fits");
  const none = fitView(null, 400, 300, 20);
  check(none.scale > 0 && Number.isFinite(none.scale), "nothing cut: still a view");
  const dot1 = fitView([1, 1, 1, 1], 400, 300, 20);
  check(dot1.scale > 0 && Number.isFinite(dot1.scale) && dot1.cx === 1, "a single point: a view round it");
  // zoom about the cursor: the drawing point under it stays there
  const before = toDrawing(v, 400, 300, 100, 80);
  const z = zoomAbout(v, 400, 300, 100, 80, 2.5);
  check(Math.abs(z.scale - 22.5) < 1e-12 && near(toDrawing(z, 400, 300, 100, 80), before), "zoom keeps the point under the cursor");
  const p = panBy(v, 18, -9);
  check(near(toScreen(p, 400, 300, 10, 5), [218, 141]), "pan moves the drawing with the mouse");
}

// ---------------------------------------------------------------- reading the answer, snapping, the distance
const line = (pts: number[], closed: boolean, extra: Partial<SectionLineCompact> = {}): SectionLineCompact => ({
  closed,
  length_mm: 0,
  area_mm2: null,
  bbox: [0, 0, 0, 0],
  n_points: pts.length / 2,
  points: pts,
  sigma_mm: null,
  stretches: [[0, closed ? pts.length / 2 : pts.length / 2 - 1, false]],
  ...extra,
});
const plane: SectionPlane = { point: [0, 0, 0], normal: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0], description: "d", in_part_frame: false };
const answer = (lines: SectionLineCompact[]): SectionAnswer => ({
  format_version: 1,
  units: "mm",
  plane,
  lines,
  length_mm: 52,
  n_points: lines.reduce((s, l) => s + l.n_points, 0),
  bbox: [0, 0, 10, 10],
  n_lines: lines.length,
  n_closed: lines.filter((l) => l.closed).length,
  mesh_version: 1,
  definition: { kind: "point_normal", point: [0, 0, 0], normal: [0, 0, 1] },
  stale_fit: false,
  warnings: [],
  compact: true,
  truncated: null,
  filenames: { dxf: "a.dxf", svg: "a.svg" },
  kept: null,
});
{
  const sq = line([0, 0, 10, 0, 10, 10, 0, 10], true, { area_mm2: 100, length_mm: 40 });
  const hole = line([4, 4, 4, 6, 6, 6, 6, 4], true, { area_mm2: -4, length_mm: 8 });
  const open = line([20, 0, 24, 3], false, { length_mm: 5 });
  const a = answer([sq, hole, open]);
  const prof = profileOf(a);
  check(prof.length === 3 && prof[0].n === 4 && prof[0].closed && prof[0].pts[2] === 10, "compact lines read as flat points");
  // the full form reads the same
  const full = profileOf({ ...a, compact: false, lines: [{ closed: true, length_mm: 40, area_mm2: 100, bbox: [0, 0, 10, 10], points: [[0, 0], [10, 0], [10, 10], [0, 10]], points3d: [], sigma: null, filled: [false, false, false, false], faces: [0, 1, 2, 3], stretches: [[0, 4, false]] }] } as unknown as SectionAnswer);
  check(full[0].n === 4 && near(full[0].pts, prof[0].pts), "full lines read the same");

  const v = fitView([0, 0, 24, 10], 480, 220, 0); // 20 px per mm
  const at = (x: number, y: number) => toScreen(v, 480, 220, x, y);
  const [sx, sy] = at(10, 10);
  const s1 = snapTo(prof, v, 480, 220, sx + 4, sy - 3, 12);
  check(!!s1 && s1.x === 10 && s1.y === 10 && s1.line === 0 && s1.index === 2, "a click near a corner snaps to it");
  const [hx, hy] = at(6, 6);
  const s2 = snapTo(prof, v, 480, 220, hx - 2, hy + 2, 12);
  check(!!s2 && s2.line === 1 && s2.x === 6 && s2.y === 6, "the nearest point of any line");
  const [mx, my] = at(15, 5);
  check(snapTo(prof, v, 480, 220, mx, my, 12) === null, "nothing within the radius: no point");
  const d = distance2d({ x: 10, y: 10 }, { x: 24, y: 3 });
  check(Math.abs(d.dx - 14) < 1e-12 && Math.abs(d.dy + 7) < 1e-12 && Math.abs(d.d - Math.hypot(14, 7)) < 1e-12, "the distance with dx and dy");

  const nums = profileNumbers(a);
  check(nums.lines === 3 && nums.closed === 2 && nums.length === 52, "the counts and the length are the answer's");
  check(nums.loops.length === 2 && nums.loops[0].area === 100 && nums.loops[0].kind === "outline" && nums.loops[1].kind === "hole", "closed loops, largest first, outline or hole");
  check(nums.net === 96, "the net area: outlines less holes");
}

// ---------------------------------------------------------------- the scale bar
{
  const v = fitView([0, 0, 100, 10], 420, 200, 10); // 4 px per mm
  const bar = scaleBar(v, 420);
  check(bar.mm === 20 && bar.px === 80 && bar.label === "20\u202fmm", "a round length, about a quarter of the width");
  const fine = scaleBar({ cx: 0, cy: 0, scale: 400 }, 420);
  check(fine.mm === 0.2 && fine.label === "0.2\u202fmm", "below a millimetre");
}

console.log(`section.test: ${checks} checks passed`);
