/**
 * The viewer's Fit tool, in the 3D view: a click on the surface asks the engine for a plane,
 * cylinder or hole there (ViewerPage sends it); each fit's region is drawn over the mesh with the
 * shape's outline (a plane's square, a cylinder's axis, a hole's circle) and its label.
 *
 * A region is its own small geometry: an index of the region's triangles over the mesh's position
 * attribute (shared, so its GPU buffer is too). Nothing of the mesh is copied or rebuilt, and each
 * fit's marks are memoised, so the cursor moving or another fit coming does not touch them. The
 * Check panel lights its areas the same way (`RegionOverlay`, CheckMark.tsx).
 */
import { Html, Line } from "@react-three/drei";
import { memo, useEffect, useMemo, type RefObject } from "react";
import { BufferAttribute, BufferGeometry, DoubleSide, MeshBasicMaterial, type Mesh } from "three";
import type { FitKind, FitShape } from "../../api/types";
import { fitName, type RegionSummary } from "../../lib/fits";
import { useThemeColors } from "../../three/Studio";
import { useSurfacePick } from "./picking";
import type { Vec3 } from "./ViewerCanvas";

/** One fit as the viewer keeps it: the engine's answer, its region decoded once, and where it was made. */
export type FitItem = {
  /** Shown as "Fit 3". */
  id: number;
  kind: FitKind;
  shape: FitShape;
  nFaces: number;
  /** The region's tolerance (null for a hole). */
  tolerance: number | null;
  /** Mean sigma of the fitted points (null when the mesh has no sigmas). */
  meanSigma: number | null;
  warnings: string[];
  /** The mesh version the engine fitted it on. */
  version: number;
  /** The viewer's mesh it was made on (its geometry's uuid): another one means an earlier version. */
  meshId: string;
  /** The region's triangles (corner triples into that mesh's vertices). */
  tris: Uint32Array;
  region: RegionSummary;
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

/** The shape's outline: a plane's square where it was clicked, a cylinder's axis over the region, a hole's circle. */
function outlineOf(fit: FitItem, size: number): Vec3[] {
  const s = fit.shape;
  const at = (o: number[], a: number[], x: number, b: number[], y: number): Vec3 => [o[0] + a[0] * x + b[0] * y, o[1] + a[1] * x + b[1] * y, o[2] + a[2] * x + b[2] * y];
  if (s.kind === "plane") {
    const [u, v] = across(s.normal);
    const h = Math.max(size * 5, Math.min(fit.region.spread * 0.4, size * 14));
    const o = fit.region.anchor;
    return [at(o, u, -h, v, -h), at(o, u, h, v, -h), at(o, u, h, v, h), at(o, u, -h, v, h), at(o, u, -h, v, -h)];
  }
  if (s.kind === "cylinder") {
    const [lo, hi] = fit.region.along ?? [-s.radius_mm, s.radius_mm];
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

/** One fit on the mesh: its region, the outline and the label. Memoised: it renders only when it changes. */
const FitMark = memo(function FitMark({
  fit,
  base,
  material,
  color,
  size,
}: {
  fit: FitItem;
  base: BufferGeometry;
  material: MeshBasicMaterial;
  color: string;
  size: number;
}) {
  const outline = useMemo(() => outlineOf(fit, size), [fit, size]);
  return (
    <group>
      <RegionOverlay tris={fit.tris} base={base} material={material} />
      <Line points={outline} color={color} lineWidth={fit.shape.kind === "cylinder" ? 2.2 : 1.8} dashed={fit.shape.kind === "cylinder"} dashSize={size * 3} gapSize={size * 1.6} depthTest={false} renderOrder={10} transparent />
      <Html position={fit.region.anchor} center zIndexRange={[30, 0]} className="html-label">
        <div
          className="flex items-center gap-1.5 rounded-full border bg-[rgb(17_20_25/0.9)] py-1 pl-1 pr-2.5 font-mono text-[12.5px] font-semibold text-white shadow-lg tnum"
          style={{ borderColor: color }}
        >
          <span className="grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 text-[10.5px] font-bold text-[#0b0d11]" style={{ background: color }}>
            {fit.id}
          </span>
          {fit.label}
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
  /** Fits made on this mesh. */
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
        const on = picked.includes(f.id);
        return <FitMark key={f.id} fit={f} base={geometry} material={on ? strong : plain} color={on ? PICKED_COLOR : accent} size={r} />;
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
