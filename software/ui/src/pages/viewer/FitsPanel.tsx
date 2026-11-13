/**
 * The viewer's list of fitted shapes: each fit's numbers with their ± (1σ, statistical only), its
 * warnings in plain words, and whether it was made on an earlier version of the mesh. Two fits
 * picked from the list show their relation (lib/fits.ts `relate`).
 */
import { CircleDashed, Cylinder, Link2, Shapes, Square, TriangleExclamationPoint, X } from "lucide-react";
import type { ReactNode } from "react";
import { sentence } from "../../api/http";
import type { FitKind } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button, IconButton } from "../../components/ui/Button";
import { Kbd } from "../../components/ui/Feedback";
import { fitName, fitValue, fmtLength, fmtPm, fmtSmall, relate } from "../../lib/fits";
import { fmtInt } from "../../lib/format";
import { cn } from "../../lib/util";
import { PICKED_COLOR, type FitItem } from "./FitTool";
import { fmtSigma } from "./ViewerCanvas";

export const FIT_ICON: Record<FitKind, typeof Square> = { plane: Square, cylinder: Cylinder, hole: CircleDashed };

// Measured on simulated scans (VALIDATION.md, "Fitted shapes against the truth"): the true errors were
// several times the ±, because the scan's shapes are biased and the ± only knows the points' scatter.
const SIGMA_NOTE =
  "± is how well the points agree with the fitted shape (1σ). It is not the accuracy: in tests on simulated scans the real errors were several times larger.";
const degSigma = (x: number) => `±${fmtSmall(x)}°`;
const mmSigma = (x: number) => `±${fmtSmall(x)} mm`;

type Row = [label: string, value: string, tip?: string];

function rowsOf(f: FitItem): Row[] {
  const s = f.shape;
  const rows: Row[] = [];
  if (s.kind === "plane") {
    rows.push(
      ["Flatness", fmtLength(s.flatness_mm), "The largest minus the smallest distance of the points from the plane."],
      ["Residual RMS", fmtLength(s.rms_mm), "How far the points lie from the plane, RMS."],
      ["Normal", degSigma(s.normal_sigma_deg), `1σ of the normal's direction (RMS angle). ${SIGMA_NOTE}`],
      ["Offset", mmSigma(s.offset_sigma_mm), `1σ of the plane's position along its normal, at the region's centre. ${SIGMA_NOTE}`],
    );
  } else if (s.kind === "cylinder") {
    rows.push(
      ["Diameter", fmtPm(s.diameter_mm, s.diameter_sigma_mm), SIGMA_NOTE],
      ["Radius", fmtPm(s.radius_mm, s.radius_sigma_mm), SIGMA_NOTE],
      ["Residual RMS", fmtLength(s.rms_mm), "How far the points lie from the cylinder, RMS."],
      ["Axis", degSigma(s.axis_sigma_deg), `1σ of the axis direction (RMS angle). ${SIGMA_NOTE}`],
      ["Axis position", mmSigma(s.point_sigma_mm), `1σ of the axis across itself (RMS distance). ${SIGMA_NOTE}`],
    );
  } else {
    rows.push(
      ["Diameter", fmtPm(s.diameter_mm, s.diameter_sigma_mm), SIGMA_NOTE],
      ["Residual RMS", fmtLength(s.rms_mm), "How far the edge points lie from the circle, in its plane, RMS."],
      ["Centre", mmSigma(s.center_sigma_mm), `1σ of the centre in the hole's plane (RMS distance). ${SIGMA_NOTE}`],
    );
  }
  rows.push(["Points", `${fmtInt(s.n)} · ${fmtInt(f.nFaces)} faces`, "Points the fit used, and the faces of the region lit on the mesh."]);
  if (f.meanSigma !== null) rows.push(["Mean sigma", fmtSigma(f.meanSigma), "The fitted points' own 1σ from the scan (the confidence map's values), averaged."]);
  if (f.tolerance !== null) rows.push(["Tolerance", fmtLength(f.tolerance), "How far from the shape a face could lie and still join the region."]);
  return rows;
}

function KV({ rows }: { rows: Row[] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11.5px]">
      {rows.map(([k, v, tip]) => (
        <div key={k} className="contents">
          <dt className="text-subtle" title={tip}>
            {k}
          </dt>
          <dd className="text-right font-mono text-fg tnum" title={tip}>
            {v}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Relation({ a, b, meshId }: { a: FitItem; b: FitItem; meshId: string | null }) {
  const r = relate(a, b);
  const stale = a.meshId !== meshId || b.meshId !== meshId;
  return (
    <div className="mt-3 rounded-lg border bg-surface-2/60 px-3 py-2.5" style={{ borderColor: `${PICKED_COLOR}80` }} aria-label="Relation of the two picked fits">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-fg">
        <Link2 size={13} style={{ color: PICKED_COLOR }} aria-hidden />
        Fit {a.id} and fit {b.id}
      </div>
      <dl className="space-y-1">
        {r.rows.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3" title={row.tip}>
            <dt className="text-xs text-muted">{row.label}</dt>
            <dd className="font-mono text-[13.5px] font-semibold text-fg tnum">{row.value}</dd>
          </div>
        ))}
      </dl>
      {r.notes.map((n) => (
        <div key={n} className="mt-1.5 text-[11px] leading-4 text-subtle">
          {n}
        </div>
      ))}
      <div className="mt-1.5 text-[11px] leading-4 text-subtle">
        {r.rows.some((x) => x.sigma !== null)
          ? "A ± is shown only where the fits' ± give one soundly; hover a number for how it is made."
          : "No ± here: the fits' ± don't give one soundly for these numbers (hover for why)."}
      </div>
      {stale ? (
        <div className="mt-1.5 flex items-start gap-1.5 text-[11px] leading-4 text-warn">
          <TriangleExclamationPoint size={12} className="mt-px shrink-0" aria-hidden />
          {a.meshId === b.meshId ? "Both were made on an earlier version of the mesh." : "They were made on different versions of the mesh."}
        </div>
      ) : null}
    </div>
  );
}

/** The fits, newest last, with their numbers; click one to pick it for a relation (two at most). */
export function FitList({
  fits,
  meshId,
  picked,
  onTogglePick,
  onRemove,
  onStart,
  fitOn,
  ready,
}: {
  fits: FitItem[];
  /** The mesh on screen (its geometry's uuid). */
  meshId: string | null;
  picked: number[];
  onTogglePick: (id: number) => void;
  onRemove: (id: number) => void;
  onStart: () => void;
  fitOn: boolean;
  ready: boolean;
}) {
  const pickedFits = picked.map((id) => fits.find((f) => f.id === id)).filter((f): f is FitItem => !!f);
  let hint: ReactNode = null;
  if (fits.length >= 2 && pickedFits.length === 0) hint = "Click two fits to see how they relate: the angle and the distance between them.";
  else if (pickedFits.length === 1) hint = "Click one more fit to see how they relate.";
  return (
    <>
      {fits.length ? (
        <ul className="space-y-2">
          {fits.map((f) => {
            const on = picked.includes(f.id);
            const stale = f.meshId !== meshId;
            const Icon = FIT_ICON[f.kind];
            return (
              <li key={f.id} className={cn("group rounded-lg border bg-surface-2/60 px-3 py-2", !on && "border-line", stale && "opacity-75")} style={on ? { borderColor: PICKED_COLOR } : undefined}>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    aria-pressed={on}
                    title={on ? "Picked for a relation: click to drop it" : "Pick it for a relation (two at most)"}
                    onClick={() => onTogglePick(f.id)}
                    className="flex min-w-0 flex-1 items-start gap-2 rounded text-left focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    <span
                      className={cn("grid h-5 min-w-5 place-items-center rounded-full px-1 text-[10.5px] font-bold", !on && "bg-accent-soft text-accent")}
                      style={on ? { background: PICKED_COLOR, color: "#0b0d11" } : undefined}
                    >
                      {f.id}
                    </span>
                    <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2">
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-fg">
                        <Icon size={13} className="shrink-0 text-muted" aria-hidden />
                        {fitName(f.kind)}
                      </span>
                      <span className="font-mono text-[13px] font-semibold text-fg tnum" title={f.shape.kind === "plane" ? undefined : SIGMA_NOTE}>
                        {fitValue(f.shape)}
                      </span>
                    </span>
                  </button>
                  <IconButton icon={X} size="xs" label={`Remove fit ${f.id}`} className="opacity-60 group-hover:opacity-100" onClick={() => onRemove(f.id)} />
                </div>
                {stale ? (
                  <div className="mt-1.5 pl-7">
                    <Badge tone="warn" icon={TriangleExclamationPoint} title="The mesh was changed after this fit (an edit). Its numbers are for that version; fit again to measure the mesh shown now.">
                      Made on an earlier version (v{f.version})
                    </Badge>
                  </div>
                ) : null}
                <div className="mt-1.5 pl-7">
                  <KV rows={rowsOf(f)} />
                </div>
                {f.warnings.length ? (
                  <ul className="mt-1.5 space-y-0.5 pl-7">
                    {f.warnings.map((w) => (
                      <li key={w} className="flex items-start gap-1.5 text-[11.5px] leading-4 text-warn">
                        <TriangleExclamationPoint size={12} className="mt-px shrink-0" aria-hidden />
                        {sentence(w)}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="text-sm text-muted">
          {ready ? (
            <>
              Turn on <b className="text-fg">Fit</b> (<Kbd>G</Kbd>), choose Plane, Cylinder or Hole, and click the surface: the shape is fitted there with its ±.
            </>
          ) : (
            "Available once the mesh is loaded."
          )}
        </div>
      )}
      {pickedFits.length === 2 ? <Relation a={pickedFits[0]} b={pickedFits[1]} meshId={meshId} /> : null}
      {hint ? <div className="mt-2 text-[11.5px] text-subtle">{hint}</div> : null}
      {fits.length ? <div className="mt-2 text-[11px] leading-4 text-subtle">{SIGMA_NOTE}</div> : null}
      {ready && !fitOn ? (
        <Button size="sm" className="mt-3" icon={Shapes} onClick={onStart}>
          Fit a shape
        </Button>
      ) : null}
    </>
  );
}
