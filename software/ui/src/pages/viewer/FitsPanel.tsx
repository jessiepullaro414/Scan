/**
 * The viewer's list of fitted shapes, kept with the scan: each fit's name (the user can change it in place), its
 * numbers with their ± (1σ, statistical only, counting points that err together), its warnings in plain words,
 * where its region lies (a plane's size and area, a cylinder's length and arc, as the engine measured them:
 * lib/extent.ts), whether a cylinder is a boss or a bore (the engine's `form`), and whether it was fitted on an earlier mesh, with one "Fit again" for those (and for fits kept
 * before their extent was measured). Two fits picked from the list show their relation as the engine works it out
 * (ohs/measure/relate.py, with its ±: lib/relationView.ts), which can be kept too. Each fit's position and direction are shown in the
 * scan's frame, or in the part's frame when the Coordinate system panel's switch is on (lib/partFrame.ts); its
 * sizes, ± values and the relations are the same in both.
 *
 * Beside a cylinder's or a hole's diameter, and beside each value of a kept relation, a suggested nominal value
 * ("Ø 14?") to accept, change or ignore, or the approved one ("nominal Ø 14", "check again" when the engine marked
 * it): NominalSlot.tsx. The measured numbers stay as they are. A kept relation with an approved value says how it goes
 * into the CAD file: listed only (the default), or one of its two fits moves to suit it (the engine's ohs/cad/moves.py).
 * Under the list, a switch "Offers a known bias allows" (off by default, kept in this browser) also shows the values
 * only a known bias allows, with a hover card that says what a known bias is.
 */
import { Bookmark, CircleDashed, CircleHelp, Cylinder, Link2, RefreshCw, Shapes, Square, TriangleExclamationPoint, X } from "lucide-react";
import type { ReactNode } from "react";
import { sentence } from "../../api/http";
import type { FitKind, KeptRelation, MoveChoice, NominalAnswer, PartFrameView } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button, IconButton } from "../../components/ui/Button";
import { Kbd } from "../../components/ui/Feedback";
import { Segmented, Switch } from "../../components/ui/Form";
import { Tooltip } from "../../components/ui/Popover";
import { extentRows } from "../../lib/extent";
import { fitName, fitValue, fmtLength, fmtPm, fmtSmall } from "../../lib/fits";
import { fmtInt } from "../../lib/format";
import { BIAS_SWITCH_HELP, MOVE_HELP, approvedText, moveChoice, moveOptions, moveText, offerFor, sizeApproval, valueApproval } from "../../lib/nominal";
import { fmtVec, placeOf } from "../../lib/partFrame";
import { relationFooter, relationRows } from "../../lib/relationView";
import { cn } from "../../lib/util";
import { PICKED_COLOR, type FitItem } from "./FitTool";
import { NameEdit } from "./NameEdit";
import { NominalSlot, type NominalActions } from "./NominalSlot";
import type { NominalAnswers } from "./useNominals";
import type { PairRelationState } from "./usePairRelation";
import { fmtSigma } from "./ViewerCanvas";

export const FIT_ICON: Record<FitKind, typeof Square> = { plane: Square, cylinder: Cylinder, hole: CircleDashed };

// Measured on simulated scans (VALIDATION.md, "The fits' ± on today's engine"): a plane's and a cylinder's ±
// count that neighbouring points err together, and cover the spread from scan to scan; the scan's biases come on top.
const SIGMA_NOTE =
  "± (1σ) is how far the fitted shape could move with the scan's scatter, counting that neighbouring points err together. It is not the accuracy: on simulated scans it covered the spread from one scan to the next, but the shapes were also off the same way on every scan (Ø 14 bosses 0.15–0.19\u202fmm small in radius as tracked, 0.23–0.28\u202fmm with the true camera path).";
const HOLE_NOTE =
  "± is how well the edge points agree with the circle (1σ). It is not the accuracy: on simulated scans a hole's edge read about 1\u202fmm small in diameter, several times its ±.";
const FORM_TIP = {
  boss: "A boss: the surface faces away from the axis, with material inside (a shaft, a pin). Read by the engine from the region's faces. Bosses and bores read off in size differently on scans.",
  bore: "A bore: the surface faces the axis, the wall of a hole. Read by the engine from the region's faces. Bosses and bores read off in size differently on scans.",
} as const;
const degSigma =(x: number) => `±${fmtSmall(x)}°`;
const mmSigma = (x: number) => `±${fmtSmall(x)}\u202fmm`;

type Row = [label: string, value: string, tip?: string, extra?: ReactNode];

/** What goes beside a fit's diameter or radius (a nominal value: NominalSlot), or null. */
type SizeSlot = (value: "diameter" | "radius") => ReactNode;

/** Where the fit is and which way it points: in the part's frame when `frame` is given, else in the scan's. */
function placeRows(f: FitItem, frame: PartFrameView | null): Row[] {
  const p = placeOf(f.shape, frame);
  const where = frame ? "along the part's axes" : "along the scan's axes";
  const sign = f.shape.kind === "plane" || f.shape.kind === "circle" ? " Which way a normal points is not measured: either sign is the same face." : " Which way an axis points is not measured.";
  return [
    [p.positionLabel, fmtVec(p.position), `${f.shape.kind === "plane" ? "The centre of the points the plane was fitted on" : f.shape.kind === "cylinder" ? "The point on the axis nearest the centre of its points" : "The hole's centre"}, mm, ${where}.`],
    [p.directionLabel, fmtVec(p.direction, 3), `The ${p.directionLabel.toLowerCase()} as a unit direction, ${where}.${sign}`],
  ];
}

function rowsOf(f: FitItem, frame: PartFrameView | null, slot: SizeSlot = () => null): Row[] {
  const s = f.shape;
  const rows: Row[] = [];
  if (s.kind === "plane") {
    rows.push(
      ["Flatness", fmtLength(s.flatness_mm), "The largest minus the smallest distance of the region's points from the plane. The region only takes points within its tolerance, so a face that is really out of flat reads flatter here than it is."],
      ["Off the plane, typical", fmtLength(s.rms_mm), "How far the points typically lie from the plane."],
      ["Normal", degSigma(s.normal_sigma_deg), `1σ of the normal's direction (a typical angle). ${SIGMA_NOTE}`],
      ["Offset", mmSigma(s.offset_sigma_mm), `1σ of the plane's position along its normal, at the region's centre. ${SIGMA_NOTE}`],
    );
  } else if (s.kind === "cylinder") {
    rows.push(
      [
        "Form",
        f.form === "boss" ? "Boss" : f.form === "bore" ? "Bore" : "Not known",
        f.form ? FORM_TIP[f.form] : "Whether this cylinder is a boss or a bore is not known: its faces did not clearly face away from the axis or towards it, or it was kept before this was read.",
      ],
      ["Diameter", fmtPm(s.diameter_mm, s.diameter_sigma_mm), SIGMA_NOTE, slot("diameter")],
      ["Radius", fmtPm(s.radius_mm, s.radius_sigma_mm), SIGMA_NOTE, slot("radius")],
      ["Off the cylinder, typical", fmtLength(s.rms_mm), "How far the points typically lie from the cylinder."],
      ["Axis", degSigma(s.axis_sigma_deg), `1σ of the axis direction (a typical angle). ${SIGMA_NOTE}`],
      ["Axis position", mmSigma(s.point_sigma_mm), `1σ of the axis across itself (a typical distance). ${SIGMA_NOTE}`],
    );
  } else {
    rows.push(
      ["Diameter", fmtPm(s.diameter_mm, s.diameter_sigma_mm), HOLE_NOTE, slot("diameter")],
      ["Off the circle, typical", fmtLength(s.rms_mm), "How far the edge points typically lie from the circle, in its plane."],
      ["Centre", mmSigma(s.center_sigma_mm), `1σ of the centre in the hole's plane (a typical distance). ${HOLE_NOTE}`],
    );
  }
  rows.push(...extentRows(f.extent));
  rows.push(["Points", `${fmtInt(s.n)} · ${fmtInt(f.nFaces)} faces`, "Points the fit used, and the faces of the region lit on the mesh."]);
  if (f.correlated !== null && f.correlated > 0)
    rows.push([
      "Independent",
      `≈ ${fmtInt(Math.max(1, Math.round(s.n / f.correlated)))}`,
      `Neighbouring points err together, in patches of about ${fmtInt(Math.round(f.correlated))} points, so the fit knows about as much as this many independent points would tell. Its ± are counted that way.`,
    ]);
  if (f.meanSigma !== null) rows.push(["Points' mean ±", fmtSigma(f.meanSigma), "The fitted points' own ± from the scan (the confidence map's values), averaged."]);
  if (f.tolerance !== null) rows.push(["Tolerance", fmtLength(f.tolerance), "How far from the shape a face could lie and still join the region."]);
  rows.push(...placeRows(f, frame));
  return rows;
}

function KV({ rows }: { rows: Row[] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11.5px]">
      {rows.map(([k, v, tip, extra]) => (
        <div key={k} className="contents">
          <dt className="text-subtle" title={tip}>
            {k}
          </dt>
          {extra ? (
            <dd className="flex flex-wrap items-center justify-end gap-x-1.5 text-right font-mono text-fg tnum">
              <span title={tip}>{v}</span>
              {extra}
            </dd>
          ) : (
            <dd className="text-right font-mono text-fg tnum" title={tip}>
              {v}
            </dd>
          )}
        </div>
      ))}
    </dl>
  );
}

/** The relation of two picked fits as the engine works it out (its values, notes and ±); kept with the scan on request. */
function Relation({
  a,
  b,
  kept,
  relation,
  onKeep,
  onRename,
  answer,
  actions,
}: {
  a: FitItem;
  b: FitItem;
  kept: KeptRelation | null;
  relation: PairRelationState;
  onKeep: () => void;
  onRename: (name: string) => Promise<boolean>;
  /** The engine's offers for the kept relation (nominal values; none until it is kept). */
  answer?: NominalAnswer;
  actions?: NominalActions;
}) {
  const r = relation.record;
  const rows = r ? relationRows(r) : [];
  const stale = a.stale || b.stale;
  return (
    <div className="group mt-3 rounded-lg border bg-surface-2/60 px-3 py-2.5" style={{ borderColor: `${PICKED_COLOR}80` }} aria-label="Relation of the two picked fitted shapes">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-fg">
        <Link2 size={13} className="shrink-0" style={{ color: PICKED_COLOR }} aria-hidden />
        {kept ? (
          <NameEdit value={kept.name} what="relation" onRename={onRename} className="min-w-0 flex-1" />
        ) : (
          <span className="min-w-0 flex-1 truncate">
            {a.name} and {b.name}
          </span>
        )}
        {kept ? (
          <span className="text-[11px] font-normal text-subtle" title="Kept with the scan: it comes back when the viewer opens again">
            kept
          </span>
        ) : (
          <Button size="xs" variant="ghost" icon={Bookmark} onClick={onKeep} title="Keep this relation with the scan">
            Keep
          </Button>
        )}
      </div>
      {kept ? (
        <div className="-mt-1 mb-1.5 truncate pl-[19px] text-[11px] text-subtle">
          {a.name} and {b.name}
        </div>
      ) : null}
      {r ? (
        <>
          <dl className="space-y-1">
            {rows.map((row, i) => {
              const name = r.values[i]?.name;
              const slot =
                kept && actions && name ? (
                  <NominalSlot id={kept.id} value={name} measuredLabel={row.label} answer={answer} offer={offerFor(answer, name)} approval={valueApproval(kept.nominals, name)} actions={actions} />
                ) : null;
              return (
                <div key={row.label} className="flex items-baseline justify-between gap-3">
                  <dt className="text-xs text-muted" title={row.tip}>
                    {row.label}
                  </dt>
                  <dd className="flex flex-wrap items-center justify-end gap-x-1.5">
                    <span className="font-mono text-[13.5px] font-semibold text-fg tnum" title={row.tip}>
                      {row.value}
                    </span>
                    {slot}
                  </dd>
                </div>
              );
            })}
          </dl>
          {r.notes.map((n) => (
            <div key={n} className="mt-1.5 text-[11px] leading-4 text-subtle">
              {n}
            </div>
          ))}
          {relationFooter(r).map((n) => (
            <div key={n} className="mt-1.5 text-[11px] leading-4 text-subtle">
              {n}
            </div>
          ))}
          {kept && actions?.setMove && (kept.nominals ?? []).length ? <MoveChoiceRow kept={kept} a={a} b={b} setMove={actions.setMove} /> : null}
        </>
      ) : relation.error ? (
        <div className="flex items-start gap-1.5 text-[11px] leading-4 text-warn">
          <TriangleExclamationPoint size={12} className="mt-px shrink-0" aria-hidden />
          {sentence(relation.error)}
        </div>
      ) : (
        <div className="text-[11px] leading-4 text-subtle">Working out how they relate…</div>
      )}
      {stale ? (
        <div className="mt-1.5 flex items-start gap-1.5 text-[11px] leading-4 text-warn">
          <TriangleExclamationPoint size={12} className="mt-px shrink-0" aria-hidden />
          {a.version !== b.version ? "They were fitted on different versions of the mesh." : "Both were fitted on an earlier mesh."}
        </div>
      ) : null}
    </div>
  );
}

/** How a kept relation with an approved value goes into the CAD file: listed only, or one of its two fits moves. The
 * kept relation's own order of its fits counts ("a" is its first), whatever order they were picked in. */
function MoveChoiceRow({ kept, a, b, setMove }: { kept: KeptRelation; a: FitItem; b: FitItem; setMove: (id: string, move: MoveChoice) => void }) {
  const [first, second] = kept.a === a.fid ? [a, b] : [b, a];
  const choice = moveChoice(kept);
  return (
    <div className="mt-2 border-t border-line pt-2">
      <div className="mb-1 flex items-center gap-1 text-[11px] text-subtle">
        <span className="flex-1">In the CAD file</span>
        <Tooltip side="top" content={<div className="max-w-[300px] text-muted">{MOVE_HELP}</div>}>
          <button type="button" className="grid h-4 w-4 place-items-center rounded text-subtle hover:text-fg" aria-label={`In the CAD file: ${MOVE_HELP}`}>
            <CircleHelp size={12} aria-hidden />
          </button>
        </Tooltip>
      </div>
      <Segmented size="sm" className="w-full" label={`How ${kept.name} goes into the CAD file`} value={choice} options={moveOptions(first.name, second.name)} onChange={(v) => setMove(kept.id, v)} />
      <div className="mt-1 text-[11px] leading-4 text-subtle">{moveText(choice, first.name, second.name)}</div>
    </div>
  );
}

/** The kept fits, oldest first, with their numbers; click one to pick it for a relation (two at most). */
export function FitList({
  fits,
  relations,
  picked,
  pairRelation,
  onTogglePick,
  onPickPair,
  onRemove,
  onRename,
  onKeepRelation,
  onRemoveRelation,
  onRefit,
  refitting,
  onStart,
  fitOn,
  ready,
  frame = null,
  nominals,
  nominalActions,
  biasOffers,
}: {
  fits: FitItem[];
  /** The relations kept with the scan. */
  relations: KeptRelation[];
  /** Ids of the fits picked for a relation. */
  picked: string[];
  /** The engine's relation of the two picked fits (usePairRelation). */
  pairRelation: PairRelationState;
  onTogglePick: (fid: string) => void;
  /** Pick these two fits (a kept relation was clicked). */
  onPickPair: (a: string, b: string) => void;
  onRemove: (fid: string) => void;
  /** Rename a fit or a relation; false when the engine refused it. */
  onRename: (id: string, name: string) => Promise<boolean>;
  onKeepRelation: (a: string, b: string) => void;
  onRemoveRelation: (id: string) => void;
  /** Fit the stale fits again on the current mesh. */
  onRefit: () => void;
  refitting: boolean;
  onStart: () => void;
  fitOn: boolean;
  ready: boolean;
  /** Show positions and directions in this part's frame (null: the scan's). */
  frame?: PartFrameView | null;
  /** The engine's nominal offers by kept fit or relation id (useNominals.ts); without them no nominal is shown. */
  nominals?: NominalAnswers;
  nominalActions?: NominalActions;
  /** The switch "Offers a known bias allows" (a viewer preference); without it the switch is not shown. */
  biasOffers?: { on: boolean; onChange: (on: boolean) => void };
}) {
  const byId = (fid: string) => fits.find((f) => f.fid === fid);
  /** Beside a cylinder's or a hole's diameter (or a cylinder's radius, where a radius was approved): its nominal value. */
  const sizeSlot =
    (f: FitItem): SizeSlot =>
    (value) => {
      if (!nominalActions || f.shape.kind === "plane") return null;
      const approval = sizeApproval(f.nominals);
      const ownRow = f.shape.kind === "cylinder" ? approval?.value === value : value === "diameter"; // a hole shows a radius on its diameter's row
      const here = approval && ownRow ? approval : null;
      if (value === "radius" && !here) return null; // the radius row has no offers of its own
      const answer = nominals?.[f.fid];
      return (
        <NominalSlot
          id={f.fid}
          value={here?.value ?? value}
          measuredLabel={here?.value === "radius" ? "Radius" : "Diameter"}
          answer={answer}
          offer={(approval && !here) || (here?.value ?? value) !== "diameter" ? null : offerFor(answer, "diameter")}
          approval={here}
          actions={nominalActions}
        />
      );
    };
  const pickedFits = picked.map(byId).filter((f): f is FitItem => !!f);
  const [p0, p1] = pickedFits;
  const keptPair = p0 && p1 ? (relations.find((r) => (r.a === p0.fid && r.b === p1.fid) || (r.a === p1.fid && r.b === p0.fid)) ?? null) : null;
  const stale = fits.filter((f) => f.stale).length;
  // kept before the engine measured where a fit's region lies: "Fit again" measures it (on its own mesh)
  const unmeasured = fits.filter((f) => !f.stale && !f.extent).length;
  let hint: ReactNode = null;
  if (fits.length >= 2 && pickedFits.length === 0) hint = "Click two fitted shapes to see how they relate: the angle and the distance between them.";
  else if (pickedFits.length === 1) hint = "Click one more fit to see how they relate.";
  return (
    <>
      {stale ? (
        <div className="mb-2 flex items-center gap-2 rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[11.5px] leading-4 text-warn">
          <TriangleExclamationPoint size={13} className="shrink-0" aria-hidden />
          <span className="flex-1">{stale === 1 ? "One fitted shape was" : `${stale} fitted shapes were`} fitted on an earlier mesh.</span>
          <Button size="xs" icon={RefreshCw} loading={refitting} disabled={!ready} onClick={onRefit} title="Fit them again on the current mesh, from the clicks they were made with">
            Fit again
          </Button>
        </div>
      ) : unmeasured ? (
        <div className="mb-2 flex items-center gap-2 rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[11.5px] leading-4 text-muted">
          <span className="flex-1">{unmeasured === 1 ? "One fitted shape was saved before its size was measured." : `${unmeasured} fitted shapes were saved before their size was measured.`}</span>
          <Button size="xs" icon={RefreshCw} loading={refitting} disabled={!ready} onClick={onRefit} title="Fit them again from the clicks they were made with, on the same mesh: the shapes stay the same, and their size and outline are measured">
            Fit again
          </Button>
        </div>
      ) : null}
      {fits.length ? (
        <ul className="space-y-2">
          {fits.map((f) => {
            const on = picked.includes(f.fid);
            const Icon = FIT_ICON[f.kind];
            return (
              <li key={f.fid} className={cn("group rounded-lg border bg-surface-2/60 px-3 py-2", !on && "border-line", f.stale && "opacity-75")} style={on ? { borderColor: PICKED_COLOR } : undefined}>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    aria-pressed={on}
                    aria-label={on ? `${f.name}: picked for a relation` : `Pick ${f.name} for a relation`}
                    title={on ? "Picked for a relation: click to drop it" : "Pick it for a relation (two at most)"}
                    onClick={() => onTogglePick(f.fid)}
                    className={cn("grid h-5 min-w-5 shrink-0 place-items-center rounded-full px-1 text-[10.5px] font-bold focus-visible:outline-2 focus-visible:outline-accent", !on && "bg-accent-soft text-accent")}
                    style={on ? { background: PICKED_COLOR, color: "#0b0d11" } : undefined}
                  >
                    {f.id}
                  </button>
                  <div className="flex min-w-0 flex-1 cursor-pointer flex-wrap items-baseline gap-x-2" onClick={() => onTogglePick(f.fid)}>
                    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 text-xs font-semibold text-fg" title={fitName(f.kind)}>
                      <Icon size={13} className="shrink-0 text-muted" aria-hidden />
                      <NameEdit value={f.name} what="fit" onRename={(n) => onRename(f.fid, n)} />
                    </span>
                    <span className="font-mono text-[13px] font-semibold text-fg tnum" title={f.shape.kind === "plane" ? undefined : SIGMA_NOTE}>
                      {fitValue(f.shape)}
                    </span>
                  </div>
                  <IconButton icon={X} size="xs" label={`Remove ${f.name}`} className="opacity-60 group-hover:opacity-100" onClick={() => onRemove(f.fid)} />
                </div>
                {f.stale ? (
                  <div className="mt-1.5 pl-7">
                    <Badge tone="warn" icon={TriangleExclamationPoint} title="The mesh was changed after this fit (an edit, or the mesh made again). Its numbers are for that version; Fit again measures the current mesh.">
                      Fitted on an earlier mesh (v{f.version})
                    </Badge>
                  </div>
                ) : null}
                <div className="mt-1.5 pl-7">
                  <KV rows={rowsOf(f, frame, sizeSlot(f))} />
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
              Turn on <b className="text-fg">Fit</b> (<Kbd>G</Kbd>), choose Plane, Cylinder or Hole, and click the surface: the shape is fitted there with its ±, and kept with the scan.
            </>
          ) : (
            "Available once the mesh is loaded."
          )}
        </div>
      )}
      {p0 && p1 ? (
        <Relation
          a={p0}
          b={p1}
          kept={keptPair}
          relation={pairRelation}
          onKeep={() => onKeepRelation(p0.fid, p1.fid)}
          onRename={(n) => (keptPair ? onRename(keptPair.id, n) : Promise.resolve(false))}
          answer={keptPair ? nominals?.[keptPair.id] : undefined}
          actions={nominalActions}
        />
      ) : null}
      {relations.length ? (
        <ul className="mt-3 space-y-1" aria-label="Kept relations">
          {relations.map((r) => {
            const a = byId(r.a),
              b = byId(r.b);
            const on = picked.includes(r.a) && picked.includes(r.b);
            return (
              <li key={r.id} className={cn("group flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[11.5px]", on ? "bg-surface-2" : "hover:bg-surface-2/60")}>
                <Link2 size={12} className="shrink-0 text-muted" style={on ? { color: PICKED_COLOR } : undefined} aria-hidden />
                <button type="button" className="min-w-0 flex-1 truncate text-left text-fg" title="Show this relation" onClick={() => onPickPair(r.a, r.b)}>
                  {r.name}
                  <span className="text-subtle">
                    {" "}
                    · {a?.name ?? "?"} and {b?.name ?? "?"}
                  </span>
                  {(r.nominals ?? []).map((n) => (
                    <span key={n.value} className={cn("ml-1.5 text-[10.5px] font-semibold", n.check_again ? "text-warn" : "text-accent")} title={n.check_again ? (n.check_reason ?? undefined) : undefined}>
                      {approvedText(n)}
                      {n.check_again ? " · check again" : ""}
                    </span>
                  ))}
                  {r.move && (r.nominals ?? []).length ? (
                    <span className="ml-1.5 text-[10.5px] text-subtle" title={moveText(r.move.shape, a?.name ?? "?", b?.name ?? "?")}>
                      · moves {(r.move.shape === "a" ? a : b)?.name ?? "?"} in CAD
                    </span>
                  ) : null}
                </button>
                <IconButton icon={X} size="xs" label={`Remove ${r.name}`} className="opacity-60 group-hover:opacity-100" onClick={() => onRemoveRelation(r.id)} />
              </li>
            );
          })}
        </ul>
      ) : null}
      {hint ? <div className="mt-2 text-[11.5px] text-subtle">{hint}</div> : null}
      {fits.length ? <div className="mt-2 text-[11px] leading-4 text-subtle">{SIGMA_NOTE}</div> : null}
      {biasOffers && fits.some((f) => f.kind !== "plane") ? (
        <div className="mt-2 flex items-start gap-1">
          <div className="min-w-0 flex-1">
            <Switch checked={biasOffers.on} onChange={biasOffers.onChange} label="Offers a known bias allows" />
          </div>
          <Tooltip side="top" content={<div className="max-w-[300px] text-muted">{BIAS_SWITCH_HELP}</div>}>
            <button type="button" className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:text-fg" aria-label={`Offers a known bias allows: ${BIAS_SWITCH_HELP}`}>
              <CircleHelp size={13} aria-hidden />
            </button>
          </Tooltip>
        </div>
      ) : null}
      {ready && !fitOn ? (
        <Button size="sm" className="mt-3" icon={Shapes} onClick={onStart}>
          Fit a shape
        </Button>
      ) : null}
    </>
  );
}
