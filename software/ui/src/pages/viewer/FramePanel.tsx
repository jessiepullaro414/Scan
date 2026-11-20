/**
 * The viewer's "Coordinate system" panel (FEATURE_PLAN.md K3): the user picks three kept fits (primary, secondary,
 * origin) and the engine builds the part's own frame from them by the 3-2-1 rule (ohs/measure/frame.py), kept
 * with the scan. The panel shows what each fit sets, the frame's ± (angle, origin) and warnings, a refusal in the
 * engine's words, and "out of date" with one "Build again" when a fit it was built from was deleted, fitted again
 * or changed (nothing is rebuilt on its own). One switch shows the numbers of the Fits and Measurements panels in
 * the part's frame; the 3D view stays in the scan's frame and draws the part's axes.
 */
import { Axis3d, CircleHelp, Crosshair, RefreshCw, Trash, TriangleExclamationPoint } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { sentence, type ApiError } from "../../api/http";
import type { FitKind, FrameRole, NewFrame, PartFrameView } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Select, Switch } from "../../components/ui/Form";
import { Tooltip } from "../../components/ui/Popover";
import { fitName, fmtSmall } from "../../lib/fits";
import { axisOrder, frameProblem, readFrame, rebuildBody, ROLES } from "../../lib/partFrame";
import type { FitItem } from "./FitTool";

export const FRAME_ICON = Axis3d;

/** The kinds of kept fit each slot takes (a point as the origin is the API's; the panel offers fits only). */
const SLOT_KINDS: Record<FrameRole, FitKind[]> = { primary: ["plane"], secondary: ["plane", "cylinder", "hole"], origin: ["hole", "cylinder", "plane"] };

const SLOT_HELP: Record<FrameRole, { title: string; text: string }> = {
  primary: {
    title: "Primary: a flat face",
    text: "The face the part sits on, or its main flat face. Its normal is the first axis (Z by default) and the face is Z = 0: it levels the part (the tilt and the height).",
  },
  secondary: {
    title: "Secondary: a side face, a cylinder or a hole",
    text: "It sets the turn about Z. A side face's normal, turned into the primary face, is the second axis (X). A cylinder or hole lying along the primary face gives X its direction. A hole or pin standing across the primary face sets X from the origin to it: plane, hole, hole, the usual way to line up a part with two dowel holes.",
  },
  origin: {
    title: "Origin: a hole, a cylinder or a face",
    text: "A hole's centre, or where a cylinder's axis meets the primary face, is the origin: X = 0 and Y = 0 there, on the primary face. A third face makes the origin the corner where the three faces meet.",
  },
};
const STRICT_HELP =
  "Off: the origin hole's centre is the origin, as measuring machines do it (level, turn, origin). On: the 3-2-1 count in the strict sense: a side face is also X = 0, and the origin hole only sets the coordinate left over (Y).";
const FLIP_HELP = "Each axis points from its face into the part. Flip one to make it point out instead; the third axis turns with it, so the frame stays right-handed.";
const AXES = ["ZX", "ZY", "XY", "XZ", "YZ", "YX"];
const ROLE_NAME: Record<FrameRole, string> = { primary: "Primary", secondary: "Secondary", origin: "Origin" };

type Form = { slots: Record<FrameRole, string>; axes: string; flip_primary: boolean; flip_secondary: boolean; secondary_zero: boolean };
const blankForm = (): Form => ({ slots: { primary: "", secondary: "", origin: "" }, axes: "ZX", flip_primary: false, flip_secondary: false, secondary_zero: false });

function formOf(f: PartFrameView | null): Form {
  const b = f ? rebuildBody(f) : null;
  if (!b) return blankForm();
  return {
    slots: { primary: b.primary, secondary: b.secondary, origin: typeof b.origin === "string" ? b.origin : "" },
    axes: b.axes ?? "ZX",
    flip_primary: !!b.flip_primary,
    flip_secondary: !!b.flip_secondary,
    secondary_zero: !!b.secondary_zero,
  };
}

function Help({ title, children, label }: { title: string; children: ReactNode; label: string }) {
  return (
    <Tooltip
      side="top"
      content={
        <div className="max-w-[280px] space-y-1">
          <div className="font-semibold">{title}</div>
          <div className="text-muted">{children}</div>
        </div>
      }
    >
      <button type="button" className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:text-fg" aria-label={label}>
        <CircleHelp size={13} aria-hidden />
      </button>
    </Tooltip>
  );
}

const sig = (x: number | null, unit: string) => (x === null ? "no ±" : `±${fmtSmall(x)}${unit}`);

/** A built frame: what each fit sets, its ± and its warnings; out of date or unreadable said plainly. */
function FrameSummary({ frame, fits }: { frame: PartFrameView; fits: FitItem[] }) {
  const [a1, a2, a3] = axisOrder(frame);
  const s = frame.sigma;
  return (
    <div className="rounded-lg border border-line bg-surface-2/60 px-3 py-2.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-fg">
        <Axis3d size={13} className="shrink-0 text-muted" aria-hidden />
        <span className="flex-1">
          {a1} from the primary, {a2} from the secondary, {a3} completes it
        </span>
      </div>
      <ul className="space-y-1 text-[11.5px]">
        {frame.built_from.map((slot) => {
          const now = slot.id ? fits.find((f) => f.fid === slot.id) : undefined;
          const name = now?.name ?? slot.name ?? (slot.kind === "point" ? "a point" : "?");
          return (
            <li key={slot.role} className="flex items-baseline gap-2" title={sentence(slot.sets)}>
              <span className="w-[68px] shrink-0 text-subtle">{ROLE_NAME[slot.role]}</span>
              <span className="min-w-0 flex-1 truncate text-fg">
                {now ? <span className="mr-1 font-mono text-subtle">{now.id}.</span> : null}
                {name}
                <span className="text-subtle"> · sets {slot.sets}</span>
              </span>
            </li>
          );
        })}
      </ul>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11.5px]">
        <dt className="text-subtle" title="1σ of the frame's whole rotation (RMS), and of its turn about the first axis. A turn error moves a point L mm from the origin by about L × the angle.">
          Angle
        </dt>
        <dd className="text-right font-mono text-fg tnum">
          {sig(s.angle_deg, "°")} <span className="text-subtle">(turn {sig(s.turn_deg, "°")})</span>
        </dd>
        <dt className="text-subtle" title="1σ of where the origin lies (RMS of its 3D displacement), carried over from the fits' ±.">
          Origin
        </dt>
        <dd className="text-right font-mono text-fg tnum">{sig(s.origin_mm, "\u202fmm")}</dd>
      </dl>
      <div className="mt-1.5 text-[11px] leading-4 text-subtle">± (1σ) from the fits' own ±: the spread from scan to scan, not the calibration's scale or a bias the whole scan shares.</div>
      {frame.warnings.length ? (
        <ul className="mt-1.5 space-y-0.5">
          {frame.warnings.map((w) => (
            <li key={w} className="flex items-start gap-1.5 text-[11.5px] leading-4 text-warn">
              <TriangleExclamationPoint size={12} className="mt-px shrink-0" aria-hidden />
              {sentence(w)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function FramePanel({
  raw,
  fits,
  selected,
  inPart,
  onInPart,
  framing,
  onBuild,
  onRemove,
  ready,
}: {
  /** `FeatureSet.frame` as the engine sent it. */
  raw: Record<string, unknown> | null;
  fits: FitItem[];
  /** The fit last picked in the Fits panel ("use the selected fit"). */
  selected: string | null;
  /** Show the panels' numbers in the part's frame. */
  inPart: boolean;
  onInPart: (on: boolean) => void;
  framing: boolean;
  /** Build (or build again); resolves to the engine's refusal, or null when built. */
  onBuild: (body: NewFrame) => Promise<ApiError | null>;
  onRemove: () => void;
  ready: boolean;
}) {
  const frame = readFrame(raw);
  const problem = frameProblem(raw);
  const again = frame ? rebuildBody(frame) : null;
  const key = frame ? JSON.stringify(again) : "";
  const [form, setForm] = useState<Form>(() => formOf(frame));
  const [error, setError] = useState<ApiError | null>(null);
  // a frame built or removed (here or after a reload): the form shows what it was built from
  useEffect(() => {
    setForm(formOf(frame));
    setError(null);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const picked = useMemo(() => fits.find((f) => f.fid === selected) ?? null, [fits, selected]);
  const complete = ROLES.every((r) => form.slots[r]);

  const build = async (body: NewFrame) => {
    setError(null);
    setError(await onBuild(body));
  };
  const setSlot = (role: FrameRole, id: string) => setForm((f) => ({ ...f, slots: { ...f.slots, [role]: id } }));
  const [n1, n2] = [form.axes[0], form.axes[1]];

  return (
    <div className="space-y-3">
      {frame ? <FrameSummary frame={frame} fits={fits} /> : null}
      {frame?.out_of_date ? (
        <div className="rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[11.5px] leading-4 text-warn">
          <div className="flex items-center gap-2">
            <TriangleExclamationPoint size={13} className="shrink-0" aria-hidden />
            <span className="flex-1 font-semibold">Out of date</span>
            <Button size="xs" icon={RefreshCw} loading={framing} disabled={!again || !ready} onClick={() => again && void build(again)} title="Build it again from the same three fits and options">
              Build again
            </Button>
          </div>
          <ul className="mt-1 space-y-0.5 pl-[21px]">
            {frame.out_of_date_reasons.map((r) => (
              <li key={r}>{sentence(r)}.</li>
            ))}
          </ul>
          {!again ? <div className="mt-1 pl-[21px] text-subtle">A fit it was built from is gone: pick another below.</div> : null}
        </div>
      ) : null}
      {problem ? (
        <div className="flex items-start gap-2 rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[11.5px] leading-4 text-warn">
          <TriangleExclamationPoint size={13} className="mt-px shrink-0" aria-hidden />
          <span className="flex-1">{sentence(problem)}. Build it again below, or remove it.</span>
        </div>
      ) : null}

      <Switch
        checked={inPart && !!frame}
        onChange={onInPart}
        disabled={!frame}
        label="Numbers in the part's frame"
        description={
          frame
            ? "Points, centres, normals, axes and a distance's Δ in the Fits and Measurements panels. Sizes, distances, angles and ± stay the same; the 3D view stays in the scan's frame."
            : "Build the coordinate system first."
        }
      />

      <div className="space-y-1.5" aria-label="The three fits of the coordinate system">
        {ROLES.map((role) => {
          const kinds = SLOT_KINDS[role];
          const usable = picked && kinds.includes(picked.kind) && !picked.stale;
          return (
            <div key={role} className="flex items-center gap-1">
              <span className="w-[68px] shrink-0 text-xs text-muted">{ROLE_NAME[role]}</span>
              <Help title={SLOT_HELP[role].title} label={`${SLOT_HELP[role].title}: ${SLOT_HELP[role].text}`}>
                {SLOT_HELP[role].text}
              </Help>
              <Select className="min-w-0 flex-1" value={form.slots[role]} aria-label={`${ROLE_NAME[role]} fit`} onChange={(e) => setSlot(role, e.target.value)} disabled={!fits.length}>
                <option value="">{kinds.map((k) => fitName(k).toLowerCase()).join(", ").replace(/, ([^,]*)$/, " or $1")}…</option>
                {fits
                  .filter((f) => kinds.includes(f.kind))
                  .map((f) => (
                    <option key={f.fid} value={f.fid} disabled={f.stale}>
                      {f.id}. {f.name}
                      {f.stale ? " (fitted on an earlier mesh)" : ""}
                    </option>
                  ))}
              </Select>
              <IconButton
                icon={Crosshair}
                size="xs"
                label={picked ? (usable ? `Use ${picked.name}, the selected fit` : `${picked.name} can't be the ${role}`) : "Use the selected fit (click one in Fitted shapes)"}
                disabled={!usable}
                onClick={() => picked && setSlot(role, picked.fid)}
              />
            </div>
          );
        })}
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-1">
          <span className="w-[68px] shrink-0 text-xs text-muted">Axes</span>
          <Help title="Axis names" label="Axis names: which axis the primary and the secondary give">
            The first letter is the primary face's axis, the second the secondary's; the third completes a right-handed frame.
          </Help>
          <Select className="min-w-0 flex-1" value={form.axes} aria-label="Axis names" onChange={(e) => setForm((f) => ({ ...f, axes: e.target.value }))}>
            {AXES.map((a) => (
              <option key={a} value={a}>
                {a[0]} from the primary, {a[1]} from the secondary
              </option>
            ))}
          </Select>
        </div>
        <div className="flex items-start gap-1">
          <div className="min-w-0 flex-1 space-y-2">
            <Switch checked={form.flip_primary} onChange={(v) => setForm((f) => ({ ...f, flip_primary: v }))} label={`Flip ${n1}`} />
            <Switch checked={form.flip_secondary} onChange={(v) => setForm((f) => ({ ...f, flip_secondary: v }))} label={`Flip ${n2}`} />
          </div>
          <Help title="Flips" label={`Flips: ${FLIP_HELP}`}>
            {FLIP_HELP}
          </Help>
        </div>
        <div className="flex items-start gap-1">
          <div className="min-w-0 flex-1">
            <Switch checked={form.secondary_zero} onChange={(v) => setForm((f) => ({ ...f, secondary_zero: v }))} label="Strict 3-2-1" />
          </div>
          <Help title="Strict 3-2-1" label={`Strict 3-2-1: ${STRICT_HELP}`}>
            {STRICT_HELP}
          </Help>
        </div>
      </div>

      {error ? (
        <div className="rounded-lg border border-danger/40 bg-danger-soft/40 px-3 py-2 text-[11.5px] leading-4 text-fg" role="alert">
          <div className="flex items-start gap-1.5">
            <TriangleExclamationPoint size={12} className="mt-px shrink-0 text-danger" aria-hidden />
            <span>{sentence(error.detail)}.</span>
          </div>
          {error.hint ? <div className="mt-1 pl-[18px] text-subtle">{sentence(error.hint)}.</div> : null}
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          icon={Axis3d}
          variant={frame ? "secondary" : "primary"}
          loading={framing}
          disabled={!complete || !ready}
          onClick={() =>
            void build({
              primary: form.slots.primary,
              secondary: form.slots.secondary,
              origin: form.slots.origin,
              axes: form.axes,
              flip_primary: form.flip_primary,
              flip_secondary: form.flip_secondary,
              secondary_zero: form.secondary_zero,
            })
          }
          title={complete ? "Build the part's coordinate system from these three fits and keep it with the scan" : "Pick a fit for each of the three slots"}
        >
          {frame || problem ? "Build" : "Build the coordinate system"}
        </Button>
        {frame || problem ? (
          <Button size="sm" variant="ghost" icon={Trash} disabled={framing} onClick={onRemove} title="Forget the coordinate system (the fits stay)">
            Remove
          </Button>
        ) : null}
      </div>
      {!fits.length ? <div className="text-[11.5px] text-subtle">Fit a plane, then a side face or a hole, and a hole for the origin: they fill the slots.</div> : null}
    </div>
  );
}
