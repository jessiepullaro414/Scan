/**
 * Edits on the mesh: remove floaters, fill holes (the holes it would close are highlighted in
 * the view; optionally the outer edge too, for a watertight mesh), smooth (by default no point
 * moves more than 2 sigma, when the mesh has its confidence) and simplify (by default to an
 * accuracy in mm), each with sensible defaults and a one-line explanation, then Apply. Deleting
 * or keeping a selection happens in the view. The Floaters tab also has "Remove the table"
 * (TableSection: its faces are shown first, then removed).
 */
import { CircleHelp, Eraser, PaintBucket, Shrink, Sparkles, WandSparkles, WavesHorizontal, type LucideIcon } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import type { EditBody, HoleOutline, MeshStats, SmoothMethod } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Segmented, Slider, Switch } from "../../components/ui/Form";
import { Tooltip } from "../../components/ui/Popover";
import { fmtFixed, fmtInt } from "../../lib/format";
import { cn } from "../../lib/util";
import { processKey, useProcess } from "../../store/process";
import { Help, NumberField, Panel } from "./bits";
import type { LoadedMesh } from "./mesh";
import type { MarkedArea } from "./ProcessCanvas";
import { TableSection } from "./TableSection";

export type EditTab = "floaters" | "holes" | "smooth" | "simplify";

const TABS: { id: EditTab; label: string; icon: LucideIcon }[] = [
  { id: "floaters", label: "Floaters", icon: Eraser },
  { id: "holes", label: "Holes", icon: PaintBucket },
  { id: "smooth", label: "Smooth", icon: WavesHorizontal },
  { id: "simplify", label: "Simplify", icon: Shrink },
];

type FloaterMode = "size" | "triangles" | "largest";
type SmoothWhere = "all" | "edges" | "flat";

/** The "Where to smooth" choices; the text is the hover card's and each button's title. */
const WHERE_HELP: { value: SmoothWhere; label: string; text: string }[] = [
  {
    value: "all",
    label: "Everywhere",
    text: "Evens out the noise on the whole part. It also rounds sharp edges: on simulated scans 17–46 % of the edge points moved by more than 0.05 mm.",
  },
  {
    value: "edges",
    label: "Off edges",
    text: "Leaves 1.5 mm on each side of a sharp edge as scanned and smooths everything else, round features like shafts and bosses too. 1–8 % of the edge points moved.",
  },
  {
    value: "flat",
    label: "Flat only",
    text: "Smooths only where the surface is flat. Edges, corners and small round features stay as scanned: 0–4 % of the edge points moved, and a small shaft keeps a few microns of noise.",
  },
];

function WhereHelp() {
  return (
    <div className="space-y-1.5">
      <div className="font-semibold">Where to smooth</div>
      {WHERE_HELP.map((w) => (
        <div key={w.value}>
          <span className="font-medium text-fg">{w.label}: </span>
          <span className="text-muted">{w.text}</span>
        </div>
      ))}
      <div className="text-muted">Measuring edges or holes? Flat only keeps them truest.</div>
    </div>
  );
}
type SimplifyMode = "accuracy" | "percent" | "count";
const TOLERANCES = [0.01, 0.02, 0.05, 0.1];

/** 5 → "5", 2.5 → "2.5". */
const mmText = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

/** The fill-holes slider range: up to a bit past the largest hole (at least 60 mm). */
export function fillRange(stats: MeshStats | null): number {
  const largest = stats?.largest_hole_mm ?? 0;
  return Math.min(2000, Math.max(60, Math.ceil((largest * 1.15) / 10) * 10));
}

export function EditsPanel({
  pid,
  sid,
  stats,
  busy,
  hasMesh,
  tab,
  setTab,
  fillMm,
  setFillMm,
  holes,
  table,
}: {
  pid: string;
  sid: string;
  stats: MeshStats | null;
  busy: boolean;
  hasMesh: boolean;
  tab: EditTab;
  setTab: (t: EditTab) => void;
  fillMm: number;
  setFillMm: (v: number) => void;
  /** The current mesh's holes (for the fill preview), when loaded. */
  holes: HoleOutline[] | null;
  /** "Remove the table" (Floaters tab): the mesh its faces are shown on, and the page's selection count. */
  table?: { mesh: LoadedMesh | null; onSelection: (n: number) => void; onUseSelection: () => void; onAreas: (areas: MarkedArea[]) => void };
}) {
  const edit = useProcess((s) => s.edit);
  const action = useProcess((s) => s.entries[processKey(pid, sid)]?.action);
  const tabsId = useId();

  // ---- floaters
  const [floaterMode, setFloaterMode] = useState<FloaterMode>("size");
  const [minSize, setMinSize] = useState(5);
  const [minTris, setMinTris] = useState(500);
  // ---- holes
  const [watertight, setWatertight] = useState(false);
  // ---- smooth
  const [method, setMethod] = useState<SmoothMethod>("taubin");
  const [strength, setStrength] = useState(0.5);
  const [keepEdges, setKeepEdges] = useState(true);
  const [withinSigma, setWithinSigma] = useState(true);
  const [where, setWhere] = useState<SmoothWhere>("all");
  const hasSigma = !!stats?.sigma_mm;
  // ---- simplify
  const [simplifyMode, setSimplifyMode] = useState<SimplifyMode>("accuracy");
  const [pct, setPct] = useState(50);
  const [tol, setTol] = useState(0.02);
  const tris = stats?.triangles ?? 0;
  const [target, setTarget] = useState(() => Math.max(100, Math.round(tris / 2)));
  useEffect(() => {
    // A new mesh: keep the target sensible.
    setTarget((t) => (tris > 0 && t >= tris ? Math.max(100, Math.round(tris / 2)) : t));
  }, [tris]);

  const maxFill = fillRange(stats);
  const fillCount = useMemo(() => (holes ? holes.filter((h) => h.perimeter_mm <= fillMm).length : null), [holes, fillMm]);

  const plan: { body: EditBody; label: string; button: string; invalid: string | null } = (() => {
    switch (tab) {
      case "floaters":
        if (floaterMode === "largest")
          return { body: { op: "remove_floaters", params: { keep_largest_only: true } }, label: "Keep the largest piece", button: "Remove floaters", invalid: null };
        if (floaterMode === "triangles")
          return {
            body: { op: "remove_floaters", params: { min_triangles: minTris } },
            label: `Remove floaters under ${fmtInt(minTris)} triangles`,
            button: "Remove floaters",
            invalid: null,
          };
        return { body: { op: "remove_floaters", params: { min_size_mm: minSize } }, label: `Remove floaters smaller than ${mmText(minSize)} mm`, button: "Remove floaters", invalid: null };
      case "holes":
        if (watertight) {
          const borderFits = stats?.border_mm != null && stats.border_mm <= fillMm;
          return {
            body: { op: "fill_holes", params: { max_hole_mm: fillMm, watertight: true } },
            label: `Make watertight: close every opening up to ${mmText(fillMm)} mm`,
            button: "Make watertight",
            invalid: stats?.watertight ? "The mesh is already watertight" : stats && !borderFits && fillCount === 0 ? "No opening is that small" : null,
          };
        }
        return {
          body: { op: "fill_holes", params: { max_hole_mm: fillMm } },
          label: `Fill holes up to ${mmText(fillMm)} mm`,
          button: "Fill holes",
          invalid: stats && stats.holes === 0 ? "No holes to fill" : fillCount === 0 ? "No hole is that small" : null,
        };
      case "smooth": {
        const within = withinSigma && hasSigma;
        const whereText = where === "flat" ? ", flat areas only" : where === "edges" ? ", away from edges" : "";
        const pctText = `${Math.round(strength * 100)} %${whereText}${within ? ", within 2σ" : ""}`;
        return {
          body: {
            op: "smooth",
            params: {
              method,
              strength,
              keep_edges: method === "bilateral" ? true : keepEdges,
              ...(where === "flat" ? { flat_only: true } : where === "edges" ? { away_from_edges: true } : {}),
              ...(within ? { within_sigma: true } : {}),
            },
          },
          label: method === "bilateral" ? `Smooth, keep edges (${pctText})` : `Smooth (Taubin, ${pctText})`,
          button: "Smooth",
          invalid: strength <= 0 ? "Choose a strength above 0" : null,
        };
      }
      case "simplify":
        if (simplifyMode === "accuracy")
          return {
            body: { op: "decimate", params: { max_error_mm: tol } },
            label: `Simplify to within ${tol} mm`,
            button: "Simplify",
            invalid: !(tol > 0) ? "Choose an accuracy above 0" : null,
          };
        if (simplifyMode === "count")
          return {
            body: { op: "decimate", params: { target_triangles: target } },
            label: `Simplify to ${fmtInt(target)} triangles`,
            button: "Simplify",
            invalid: tris && target >= tris ? `The mesh already has ${fmtInt(tris)} triangles` : null,
          };
        return { body: { op: "decimate", params: { ratio: pct / 100 } }, label: `Simplify to ${pct} %`, button: "Simplify", invalid: null };
    }
  })();

  const apply = () => void edit(pid, sid, plan.body, plan.label);
  const disabled = busy || !hasMesh || !!plan.invalid;

  return (
    <Panel title="Edit the mesh" icon={WandSparkles}>
      <div role="tablist" aria-label="Edits" className="mb-3 grid grid-cols-4 gap-1 rounded-lg border border-line bg-surface-2 p-0.5">
        {TABS.map((t) => {
          const on = t.id === tab;
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`${tabsId}-${t.id}`}
              aria-selected={on}
              aria-controls={`${tabsId}-panel`}
              tabIndex={on ? 0 : -1}
              onClick={() => setTab(t.id)}
              onKeyDown={(e) => {
                const i = TABS.findIndex((x) => x.id === tab);
                if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                  e.preventDefault();
                  const n = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
                  setTab(n.id);
                  document.getElementById(`${tabsId}-${n.id}`)?.focus();
                }
              }}
              className={cn(
                "flex h-12 flex-col items-center justify-center gap-0.5 rounded-md text-[11px] font-medium transition-colors",
                on ? "bg-surface-3 text-fg shadow-sm ring-1 ring-line-strong" : "text-muted hover:text-fg",
              )}
            >
              <Icon size={15} aria-hidden />
              {t.label}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" id={`${tabsId}-panel`} aria-labelledby={`${tabsId}-${tab}`} className="space-y-3">
        {tab === "floaters" ? (
          <>
            <Help>Removes loose bits that float apart from the scan: dust, reflections, the edge of the table.</Help>
            <Segmented<FloaterMode>
              label="Which pieces to remove"
              size="sm"
              className="w-full"
              value={floaterMode}
              onChange={setFloaterMode}
              options={[
                { value: "size", label: "By size", title: "Pieces smaller than a size across" },
                { value: "triangles", label: "By triangles", title: "Pieces with few triangles" },
                { value: "largest", label: "Largest only", title: "Keep only the biggest piece" },
              ]}
            />
            {floaterMode === "size" ? (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted">Smaller than</span>
                <NumberField label="Smallest piece to keep, mm across" value={minSize} onChange={setMinSize} min={0.5} max={500} step={0.5} unit="mm" className="w-28" />
              </div>
            ) : floaterMode === "triangles" ? (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted">Fewer than</span>
                <NumberField label="Fewest triangles a piece keeps" value={minTris} onChange={setMinTris} min={1} max={10_000_000} unit="tri" className="w-32" />
              </div>
            ) : (
              <Help>Keeps only the biggest piece, the scan itself. Everything else goes.</Help>
            )}
            {stats ? (
              <p className="text-xs text-muted">
                {stats.components <= 1 ? (
                  "The mesh is in one piece: nothing floats loose."
                ) : (
                  <>
                    The mesh is in <b className="font-semibold text-fg tnum">{fmtInt(stats.components)}</b> pieces.
                  </>
                )}
              </p>
            ) : null}
          </>
        ) : tab === "holes" ? (
          <>
            <Help>
              Closes gaps in the surface with a smooth patch, up to a size. Filled surface is marked as not measured: measuring skips it.
              {watertight ? " The outer edge is closed too, when it fits the size." : " The outer edge of the scan stays open."}
            </Help>
            <Switch
              label="Make it watertight"
              description={
                stats?.border_mm != null
                  ? `Closes the outer edge too (${fmtFixed(stats.border_mm, 0)} mm around), for a part scanned all round: ready to 3D print.`
                  : "Closes the outer edge too, for a part scanned all round: ready to 3D print."
              }
              checked={watertight}
              onChange={setWatertight}
            />
            <div className="flex items-center gap-2">
              <span className="shrink-0 text-xs text-muted">Up to</span>
              <div className="min-w-0 flex-1">
                <Slider label="Largest hole to fill, mm around" value={Math.min(fillMm, maxFill)} onChange={setFillMm} min={1} max={maxFill} step={1} />
              </div>
              <NumberField label="Largest hole to fill, mm around" value={fillMm} onChange={setFillMm} min={0.5} max={5000} step={0.5} unit="mm" className="w-24" />
            </div>
            <p className="text-xs text-muted" aria-live="polite">
              {stats && stats.holes === 0 ? (
                "No holes: the surface has no gaps inside its outer edge."
              ) : fillCount !== null && holes ? (
                <>
                  Fills <b className="font-semibold text-ok tnum">{fmtInt(fillCount)}</b> of {fmtInt(holes.length)} hole{holes.length === 1 ? "" : "s"}
                  {fillCount ? <span className="text-subtle"> (green in the view)</span> : null}.
                </>
              ) : stats ? (
                <>
                  {fmtInt(stats.holes)} hole{stats.holes === 1 ? "" : "s"}, the largest {fmtFixed(stats.largest_hole_mm, 1)} mm around.
                </>
              ) : null}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {[5, 10, 25].map((v) => (
                <Button key={v} size="xs" variant={fillMm === v ? "primary" : "secondary"} onClick={() => setFillMm(v)}>
                  {v} mm
                </Button>
              ))}
              {stats?.largest_hole_mm ? (
                <Button size="xs" variant="secondary" onClick={() => setFillMm(Math.ceil(stats.largest_hole_mm! + 0.5))} title="Fill every hole">
                  All holes
                </Button>
              ) : null}
            </div>
            <Help>Size is the distance around a hole's edge: a 10 mm hole is about 3 mm wide.</Help>
          </>
        ) : tab === "smooth" ? (
          <>
            <Segmented<SmoothMethod>
              label="Smoothing method"
              size="sm"
              className="w-full"
              value={method}
              onChange={(m) => {
                setMethod(m);
                setStrength(m === "bilateral" ? 0.3 : 0.5);
              }}
              options={[
                { value: "taubin", label: "Taubin", title: "Even smoothing that keeps the size" },
                { value: "bilateral", label: "Keep edges", title: "Bilateral: smooths flat areas, keeps sharp edges" },
              ]}
            />
            <Help>
              {method === "taubin"
                ? "Evens out scan noise everywhere without shrinking the part: good for body panels and curved surfaces."
                : "Smooths the flat and gently curved areas but keeps sharp edges crisp: brackets, flanges, machined parts."}
            </Help>
            <div>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="text-muted">Strength</span>
                <span className="font-mono text-fg tnum">{Math.round(strength * 100)} %</span>
              </div>
              <Slider label="Smoothing strength" value={strength} onChange={setStrength} min={0} max={1} step={0.05} />
              <div className="mt-0.5 flex justify-between text-[10.5px] text-subtle">
                <span>Light</span>
                <span>Strong</span>
              </div>
            </div>
            {method === "taubin" ? (
              <Switch label="Protect sharp edges" description="Leaves corners and creases alone." checked={keepEdges} onChange={setKeepEdges} />
            ) : null}
            <div>
              <div className="mb-1 flex items-center gap-1 text-xs">
                <span className="text-muted">Where to smooth</span>
                <Tooltip side="top" content={<WhereHelp />}>
                  <button
                    type="button"
                    className="grid h-5 w-5 place-items-center rounded text-subtle hover:text-fg"
                    aria-label={`Where to smooth: ${WHERE_HELP.map((w) => `${w.label}: ${w.text}`).join(" ")}`}
                  >
                    <CircleHelp size={13} aria-hidden />
                  </button>
                </Tooltip>
              </div>
              <Segmented<SmoothWhere>
                label="Where to smooth"
                size="sm"
                className="w-full"
                value={where}
                onChange={setWhere}
                options={WHERE_HELP.map((w) => ({ value: w.value, label: w.label, title: w.text }))}
              />
            </div>
            <Switch
              label="Stay within each point's uncertainty"
              description={
                hasSigma
                  ? "No point moves more than 2σ from where it was measured: confident areas stay put, noisy ones smooth out."
                  : "Needs a mesh with confidence data (made with a GPU): this one has none."
              }
              checked={withinSigma && hasSigma}
              onChange={setWithinSigma}
              disabled={!hasSigma}
            />
          </>
        ) : (
          <>
            <Help>Fewer triangles for a lighter file. Flat areas give up triangles first; edges and curves keep theirs.</Help>
            <Segmented<SimplifyMode>
              label="Simplify to"
              size="sm"
              className="w-full"
              value={simplifyMode}
              onChange={setSimplifyMode}
              options={[
                { value: "accuracy", label: "Accuracy", title: "As few triangles as the surface allows within a tolerance in mm" },
                { value: "percent", label: "Percent" },
                { value: "count", label: "Triangle count" },
              ]}
            />
            {simplifyMode === "accuracy" ? (
              <>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="mr-1 text-xs text-muted">Within</span>
                  {TOLERANCES.map((v) => (
                    <Button key={v} size="xs" variant={tol === v ? "primary" : "secondary"} onClick={() => setTol(v)}>
                      {v} mm
                    </Button>
                  ))}
                  <NumberField label="Tolerance, mm" value={tol} onChange={setTol} min={0.001} max={10} step={0.005} unit="mm" className="w-24" />
                </div>
                <Help>The surface stays within about this distance of the scan; the result says how far it actually moved. Measurements stay true; the outer edge keeps every point.</Help>
              </>
            ) : simplifyMode === "percent" ? (
              <div>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <span className="text-muted">Keep</span>
                  <span className="font-mono text-fg tnum">{pct} %</span>
                </div>
                <Slider label="Percent of the triangles to keep" value={pct} onChange={setPct} min={5} max={95} step={5} />
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted">Target</span>
                <NumberField label="Target triangle count" value={target} onChange={setTarget} min={100} max={Math.max(101, tris)} unit="tri" className="w-36" />
              </div>
            )}
            {tris && simplifyMode !== "accuracy" ? (
              <p className="text-xs text-muted">
                About <b className="font-semibold text-fg tnum">{fmtInt(simplifyMode === "percent" ? Math.round((tris * pct) / 100) : Math.min(target, tris))}</b> triangles
                after, from {fmtInt(tris)}.
              </p>
            ) : null}
          </>
        )}

        <div className="flex items-center gap-2 pt-1">
          <Button variant="primary" icon={TABS.find((t) => t.id === tab)?.icon ?? Sparkles} disabled={disabled} loading={action === "edit"} onClick={apply} className="flex-1">
            {plan.button}
          </Button>
        </div>
        {!hasMesh ? (
          <Help>Edits need a mesh: run Track first.</Help>
        ) : plan.invalid ? (
          <Help>{plan.invalid}.</Help>
        ) : busy ? (
          <Help>Waiting for the running job to finish.</Help>
        ) : (
          <Help>
            Adds “{plan.label}” to the history. <span className="whitespace-nowrap">Ctrl+Z</span> undoes it.
          </Help>
        )}
        {tab === "floaters" && table && hasMesh ? (
          <TableSection
            pid={pid}
            sid={sid}
            busy={busy}
            mesh={table.mesh}
            onSelection={table.onSelection}
            onUseSelection={table.onUseSelection}
            onAreas={table.onAreas}
          />
        ) : null}
      </div>
    </Panel>
  );
}
