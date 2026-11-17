/**
 * The viewer's Fit tool, in the 3D view: a click on the surface asks the engine for a plane,
 * cylinder or hole there and keeps it with the scan (ViewerPage sends it); each kept fit made on the
 * mesh on screen has its region drawn over the mesh with the shape's outline and its label. Where the
 * engine measured the fit's extent (lib/extent.ts) the outline is the engine's: a plane's rectangle
 * round its region, a cylinder's axis over the region's span and the arc of the round it covers. A
 * fit kept before extents existed is drawn as before, from the viewer's own `summarizeRegion`: a
 * square where it was clicked, the axis over the region. A hole is its circle either way.
 *
 * A region is its own small geometry: an index of the region's triangles over the mesh's position
 * attribute (shared, so its GPU buffer is too). Nothing of the mesh is copied or rebuilt, and each
 * fit's marks are memoised, so the cursor moving or another fit coming does not touch them. The
 * Check panel lights its areas the same way (`RegionOverlay`, CheckMark.tsx).
 */
import { Html, Line } from "@react-three/drei";
import { memo, useEffect, useMemo, type RefObject } from "react";
import { BufferAttribute, BufferGeometry, DoubleSide, MeshBasicMaterial, type Mesh } from "three";
import type { FitExtent, FitKind, FitShape } from "../../api/types";
import { cylinderMarks, rectangleOutline } from "../../lib/extent";
import { fitName, type RegionSummary } from "../../lib/fits";
import { useThemeColors } from "../../three/Studio";
import { useSurfacePick } from "./picking";
import type { Vec3 } from "./ViewerCanvas";

/** One kept fit as the viewer shows it: the engine's record, and its region on the mesh on screen when it was fitted on it. */
export type FitItem = {
  /** The number shown on the mesh and in the list (its place in the list). */
  id: number;
  /** The kept feature's id ("f-…"). */
  fid: string;
  /** "Fit 3" unless the user named it. */
  name: string;
  kind: FitKind;
  shape: FitShape;
  nFaces: number;
  /** The region's tolerance (null for a hole). */
  tolerance: number | null;
  /** Mean sigma of the fitted points (null when the mesh has no sigmas). */
  meanSigma: number | null;
  /** How many points err together with each one (the engine's `correlated_points`); null for a hole. */
  correlated: number | null;
  warnings: string[];
  /** The mesh version the engine fitted it on. */
  version: number;
  /** Fitted on an earlier mesh than the one on screen: its numbers are that mesh's, and it is not drawn. */
  stale: boolean;
  /** The region's triangles (corner triples into the vertices of the mesh on screen); null while not read, or when stale. */
  tris: Uint32Array | null;
  region: RegionSummary | null;
  /** Where the region lies, as the engine measured it; null for a fit kept before extents existed. */
  extent: FitExtent | null;
  /** "Plane · RMS 0.04 mm". */
  label: string;
};

/** The second colour of the view: fits picked for a relation. */
export const PICKED_COLOR = "#f5a524";

/** The look of a region lit on the mesh: a flat colour drawn over the surface. */
export function overlayMaterial(color: string, opacity: number): MeshBasicMaterial {
  return new MeshBasicMaterial({
    color,
    side: DoubleSide,
    transparent: true,
    opacity,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
    toneMapped: false,
  });
}

/** Two unit vectors across a direction. */
function across(n: number[]): [number[], number[]] {
  const l = Math.hypot(n[0], n[1], n[2]) || 1;
  const d = [n[0] / l, n[1] / l, n[2] / l];
  const e = Math.abs(d[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  let u = [d[1] * e[2] - d[2] * e[1], d[2] * e[0] - d[0] * e[2], d[0] * e[1] - d[1] * e[0]];
  const lu = Math.hypot(u[0], u[1], u[2]) || 1;
  u = [u[0] / lu, u[1] / lu, u[2] / lu];
  return [u, [d[1] * u[2] - d[2] * u[1], d[2] * u[0] - d[0] * u[2], d[0] * u[1] - d[1] * u[0]]];
}

/** One line of a fit's outline; a cylinder's axis is dashed. */
type OutlineLine = { points: Vec3[]; dashed: boolean };

/**
 * The shape's outline. With the engine's extent: a plane's rectangle, a cylinder's axis over its span and its arc.
 * Without (a fit kept before extents existed): a plane's square where it was clicked, a cylinder's axis over the
 * region as the viewer measured it. A hole: its circle.
 */
function outlineOf(s: FitShape, region: RegionSummary, extent: FitExtent | null, size: number): OutlineLine[] {
  if (s.kind === "plane" && extent?.kind === "plane") return [{ points: rectangleOutline(extent), dashed: false }];
  if (s.kind === "cylinder" && extent?.kind === "cylinder") {
    const { axis, arc } = cylinderMarks(s, extent, 0.15 * extent.length_mm + size * 2);
    return [
      { points: axis, dashed: true },
      { points: arc, dashed: false },
    ];
  }
  return [{ points: ownOutline(s, region, size), dashed: s.kind === "cylinder" }];
}

/** The viewer's own outline (no extent from the engine): a plane's square where it was clicked, a cylinder's axis over the region, a hole's circle. */
function ownOutline(s: FitShape, region: RegionSummary, size: number): Vec3[] {
  const at = (o: number[], a: number[], x: number, b: number[], y: number): Vec3 => [o[0] + a[0] * x + b[0] * y, o[1] + a[1] * x + b[1] * y, o[2] + a[2] * x + b[2] * y];
  if (s.kind === "plane") {
    const [u, v] = across(s.normal);
    const h = Math.max(size * 5, Math.min(region.spread * 0.4, size * 14));
    const o = region.anchor;
    return [at(o, u, -h, v, -h), at(o, u, h, v, -h), at(o, u, h, v, h), at(o, u, -h, v, h), at(o, u, -h, v, -h)];
  }
  if (s.kind === "cylinder") {
    const [lo, hi] = region.along ?? [-s.radius_mm, s.radius_mm];
    const m = 0.15 * (hi - lo) + size * 2;
    return [at(s.point, s.axis, lo - m, s.axis, 0), at(s.point, s.axis, hi + m, s.axis, 0)];
  }
  const [u, v] = across(s.normal);
  const pts: Vec3[] = [];
  for (let k = 0; k <= 72; k++) {
    const a = (k / 72) * 2 * Math.PI;
    pts.push(at(s.center, u, s.radius_mm * Math.cos(a), v, s.radius_mm * Math.sin(a)));
  }
  return pts;
}

/**
 * A region lit on the mesh (a fit's, or an area of the Check panel): `tris` are its triangles, corner
 * triples into the vertices of `base`. Its geometry is that index over the mesh's own position
 * attribute, so nothing of the mesh is copied.
 */
export function RegionOverlay({ tris, base, material }: { tris: Uint32Array; base: BufferGeometry; material: MeshBasicMaterial }) {
  const position = base.getAttribute("position");
  const overlay = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute("position", position);
    g.setIndex(new BufferAttribute(tris, 1));
    g.boundingSphere = base.boundingSphere; // no pass over the mesh's vertices for culling
    g.boundingBox = base.boundingBox;
    return g;
  }, [tris, position, base]);
  useEffect(() => {
    overlay.setAttribute("position", position);
    return () => {
      // only the region's index leaves the GPU: disposing with the shared positions attached would
      // drop the mesh's own position buffer and upload it again
      overlay.deleteAttribute("position");
      overlay.dispose();
    };
  }, [overlay, position]);
  return <mesh geometry={overlay} material={material} raycast={() => null} renderOrder={2} />;
}

/** One fit on the mesh: its region, the outline and the label. Memoised on what it draws, so the cursor moving,
 * another fit coming or a rename does not touch it. */
const FitMark = memo(function FitMark({
  num,
  label,
  shape,
  tris,
  region,
  extent,
  base,
  material,
  color,
  size,
}: {
  num: number;
  label: string;
  shape: FitShape;
  tris: Uint32Array;
  region: RegionSummary;
  extent: FitExtent | null;
  base: BufferGeometry;
  material: MeshBasicMaterial;
  color: string;
  size: number;
}) {
  const outline = useMemo(() => outlineOf(shape, region, extent, size), [shape, region, extent, size]);
  return (
    <group>
      <RegionOverlay tris={tris} base={base} material={material} />
      {outline.map((l, i) => (
        <Line key={i} points={l.points} color={color} lineWidth={l.dashed ? 2.2 : 1.8} dashed={l.dashed} dashSize={size * 3} gapSize={size * 1.6} depthTest={false} renderOrder={10} transparent />
      ))}
      <Html position={region.anchor} center zIndexRange={[30, 0]} className="html-label">
        <div
          className="flex items-center gap-1.5 rounded-full border bg-[rgb(17_20_25/0.9)] py-1 pl-1 pr-2.5 font-mono text-[12.5px] font-semibold text-white shadow-lg tnum"
          style={{ borderColor: color }}
        >
          <span className="grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 text-[10.5px] font-bold text-[#0b0d11]" style={{ background: color }}>
            {num}
          </span>
          {label}
        </div>
      </Html>
    </group>
  );
});

/** Picking for the Fit tool, the fits made on this mesh, and the pending click while the engine works. */
export function FitTool({
  enabled,
  busy,
  target,
  geometry,
  fits,
  picked,
  markerRadius,
  onPick,
}: {
  enabled: boolean;
  /** The clicked point and the kind asked, while that fit runs. */
  busy: { p: Vec3; kind: FitKind } | null;
  target: RefObject<Mesh | null>;
  geometry: BufferGeometry;
  /** Fits made on this mesh (those without a region on it are not drawn). */
  fits: FitItem[];
  /** Ids of the fits picked for a relation. */
  picked: number[];
  markerRadius: number;
  /** The clicked point and the file's face under it. */
  onPick: (p: Vec3, face: number) => void;
}) {
  const accent = useThemeColors().accent;
  const { hover, overFilled } = useSurfacePick(
    enabled,
    target,
    (h) => {
      if (h.tri < 0) return;
      const map = geometry.userData.faceOfTri as Uint32Array | null | undefined;
      onPick(h.p, map ? map[h.tri] : h.tri);
    },
    busy ? "progress" : "crosshair",
  );
  const plain = useMemo(() => overlayMaterial(accent, 0.34), [accent]);
  const strong = useMemo(() => overlayMaterial(PICKED_COLOR, 0.5), []);
  useEffect(() => () => plain.dispose(), [plain]);
  useEffect(() => () => strong.dispose(), [strong]);
  const r = markerRadius;
  return (
    <>
      {fits.map((f) => {
        if (!f.tris || !f.region) return null;
        const on = picked.includes(f.id);
        return (
          <FitMark key={f.fid} num={f.id} label={f.label} shape={f.shape} tris={f.tris} region={f.region} extent={f.extent} base={geometry} material={on ? strong : plain} color={on ? PICKED_COLOR : accent} size={r} />
        );
      })}
      {busy ? (
        <group>
          <mesh position={busy.p} renderOrder={12}>
            <sphereGeometry args={[r, 18, 14]} />
            <meshBasicMaterial color={accent} depthTest={false} transparent />
          </mesh>
          <Html position={busy.p} zIndexRange={[25, 0]} className="html-label" style={{ pointerEvents: "none" }}>
            <div className="ml-3 -translate-y-1/2 whitespace-nowrap rounded-full border border-white/15 bg-[rgb(17_20_25/0.88)] px-2 py-0.5 text-[11.5px] text-white shadow-lg">
              Fitting a {fitName(busy.kind).toLowerCase()}…
            </div>
          </Html>
        </group>
      ) : null}
      {enabled && hover && !busy ? (
        <mesh position={hover} renderOrder={12}>
          <sphereGeometry args={[r * 0.8, 14, 10]} />
          <meshBasicMaterial color={overFilled ? "#8a8781" : accent} depthTest={false} transparent opacity={0.85} />
        </mesh>
      ) : null}
      {enabled && hover && overFilled && !busy ? (
        <Html position={hover} zIndexRange={[25, 0]} className="html-label" style={{ pointerEvents: "none" }}>
          <div className="ml-3 -translate-y-1/2 whitespace-nowrap rounded-full border border-white/15 bg-[rgb(17_20_25/0.88)] px-2 py-0.5 text-[11.5px] text-white shadow-lg">
            Filled, not measured: pick a scanned spot
          </div>
        </Html>
      ) : null}
    </>
  );
}
