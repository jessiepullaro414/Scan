/**
 * The viewer's "Compare with a reference" panel (FEATURE_PLAN I2): pick a reference model of the project or add one
 * (a file picker, or a file dropped on the panel); the units question as a choice with the two sizes; how to align,
 * as one choice with a card for each way (from the shape, on the part's axes, from points), a progress line while a
 * best fit runs, and the result in words; the reference as a faint ghost over the scan; the "Against the reference"
 * shading with a tolerance, the answer's sentence, what was not compared and why, how much of the reference the scan
 * did not cover; and the areas outside the tolerance, each lit and flown to on a click, each saying whether it exceeds
 * the scan's own uncertainty. The state is useComparison.ts's; picking points is PointPairPicker.tsx's.
 */
import { CircleHelp, CircleExclamationPoint, Eye, FileBox, GitCompareArrows, MousePointerClick, RefreshCw, Trash, TriangleExclamationPoint, Upload, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type DragEvent } from "react";
import { sentence } from "../../api/http";
import type { ToleranceAnswer, ToleranceRegion } from "../../api/types";
import { JobProgress } from "../../components/ScanBits";
import { Button, IconButton } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Feedback";
import { Switch } from "../../components/ui/Form";
import { Callout } from "../../components/ui/Layout";
import { Tooltip } from "../../components/ui/Popover";
import { fmtAreaSize, fmtExtent, fmtShare, fmtTolerance, validTolerance } from "../../lib/check";
import { fmtInt } from "../../lib/format";
import { ALIGN_METHODS, alignmentLines, coverageLines, fmtDev, fmtSize, regionTitle, regionUncertainty, toleranceSentence, unitsChoice, unitsNote, type AlignMethod } from "../../lib/reference";
import { cn } from "../../lib/util";
import { NameEdit } from "./NameEdit";
import { sideColor } from "./ReferenceMark";
import type { ComparisonTool } from "./useComparison";

export const COMPARE_ICON = GitCompareArrows;

/** Areas shown before "Show all". */
const FIRST = 6;
const STEP_MM = 0.05;
const ACCEPT = ".stl,.obj,.ply";

function Help({ text, label }: { text: string; label: string }) {
  return (
    <Tooltip side="top" content={<div className="max-w-[280px] text-muted">{text}</div>}>
      <button type="button" className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:text-fg" aria-label={`${label}: ${text}`}>
        <CircleHelp size={13} aria-hidden />
      </button>
    </Tooltip>
  );
}

const HELP = {
  references:
    "The part as it should be: an STL, OBJ or PLY exported from CAD (or a scan of a good part), in mm. It is kept with the project, so every scan of it can be compared with it. A STEP file must be exported as STL first.",
  ghost: "The CAD model drawn over the scan, faint, where the alignment puts it. Only the view.",
  uncertainty:
    "Each area says whether its deviation is larger than the scan's own scatter there. One that is not could be the scan's error as much as the part's.",
  tolerance: "How far the surface may be from the CAD model. The shares are of the compared area; the strongest colours of the shading are at ± this.",
} as const;

/** The tolerance field: type freely; Enter, the arrow keys or leaving the field set it. */
function ToleranceField({ id, value, onChange }: { id: string; value: number; onChange: (mm: number) => void }) {
  const [text, setText] = useState(fmtTolerance(value));
  useEffect(() => setText(fmtTolerance(value)), [value]);
  const commit = (t = text) => {
    const typed = Number(t.trim().replace(",", "."));
    if (t.trim() && Number.isFinite(typed) && typed > 0) {
      const v = validTolerance(typed);
      if (v !== value) onChange(v);
    }
  };
  return (
    <div className="relative">
      <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-subtle" aria-hidden>
        ±
      </span>
      <input
        id={id}
        type="text"
        inputMode="decimal"
        autoComplete="off"
        spellCheck={false}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          commit(e.target.value); // the answer is quick: each number typed asks (after a short wait)
        }}
        onBlur={() => setText(fmtTolerance(value))}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            onChange(validTolerance(value + (e.key === "ArrowUp" ? STEP_MM : -STEP_MM)));
          }
        }}
        className={cn(
          "h-8 w-full rounded-md border border-line bg-surface-2 pl-7 pr-10 font-mono text-[12.5px] text-fg tnum transition-[border-color,box-shadow] duration-150",
          "hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--c-accent-soft)] focus:outline-none",
        )}
      />
      <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-subtle" aria-hidden>
        mm
      </span>
    </div>
  );
}

/** How the compared area splits: within, above, below. The sentence says it in words. */
function ShareBar({ a }: { a: ToleranceAnswer }) {
  return (
    <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-surface-3" aria-hidden>
      <span className="bg-ok" style={{ width: `${(a.share_within ?? 0) * 100}%` }} />
      <span style={{ width: `${(a.share_above ?? 0) * 100}%`, background: sideColor("above") }} />
      <span style={{ width: `${(a.share_below ?? 0) * 100}%`, background: sideColor("below") }} />
    </div>
  );
}

function AreaRow({ r, n, on, k, onSelect }: { r: ToleranceRegion; n: number; on: boolean; k: number; onSelect: () => void }) {
  const t = regionTitle(r);
  const u = regionUncertainty(r, k);
  const color = sideColor(r.side);
  return (
    <li>
      <button
        type="button"
        aria-pressed={on}
        title={on ? "Lit on the mesh: click to turn it off" : "Show it on the mesh"}
        onClick={onSelect}
        className={cn("block w-full rounded-lg border bg-surface-2/60 px-3 py-2 text-left transition-colors duration-150 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent", !on && "border-line")}
        style={on ? { borderColor: color } : undefined}
      >
        <span className="flex items-start gap-2">
          <span className={cn("grid h-5 min-w-5 place-items-center rounded-full px-1 text-[10.5px] font-bold", !on && "bg-surface-3 text-muted")} style={on ? { background: color, color: "#fff" } : undefined}>
            {n}
          </span>
          <span className="flex min-w-0 flex-1 flex-wrap items-baseline justify-between gap-x-2">
            <span className="text-xs font-semibold text-fg">{t.title}</span>
            <span className="font-mono text-[12.5px] font-semibold text-fg tnum">
              {fmtAreaSize(r.area_mm2)}
              <span className="font-normal text-muted"> · {fmtExtent(r.extent_mm)} across</span>
            </span>
          </span>
        </span>
        <span className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 pl-7 text-[11.5px]">
          <span className="text-subtle">Mean, worst</span>
          <span className="text-right font-mono text-fg tnum">
            {fmtDev(r.mean_mm)}, {fmtDev(r.worst_mm)}
          </span>
          <span className="text-subtle">Share</span>
          <span className="text-right font-mono text-fg tnum">{fmtShare(r.share)} of the compared area</span>
        </span>
        <span className={cn("mt-1 block pl-7 text-[11.5px] leading-4", u.tone === "warn" ? "text-warn" : "text-muted")}>{u.text}</span>
      </button>
    </li>
  );
}

export function ComparePanel({
  tool,
  ready,
  frame,
  shadingOn,
  onShading,
  onPickPoints,
  picking,
}: {
  tool: ComparisonTool;
  /** A mesh is on screen. */
  ready: boolean;
  /** The scan's coordinate system: none, out of date, or current. */
  frame: "none" | "out_of_date" | "current";
  shadingOn: boolean;
  onShading: (on: boolean) => void;
  /** Open the point picker (the reference's mesh is needed). */
  onPickPoints: () => void;
  picking: boolean;
}) {
  const id = useId();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [uploadError, setUploadError] = useState<{ detail: string; hint: string | null } | null>(null);
  const [drag, setDrag] = useState(false);
  const [method, setMethod] = useState<AlignMethod>("shape");
  const [again, setAgain] = useState(false);
  const [changing, setChanging] = useState(false);
  const [all, setAll] = useState(false);
  const c = tool.comparison;
  const a = tool.answer;
  useEffect(() => setAll(false), [a]);
  useEffect(() => {
    setAgain(false);
    setChanging(false);
  }, [c?.alignment, c?.reference_id]);
  useEffect(() => {
    if (method === "datums" && frame !== "current") setMethod("shape");
  }, [frame, method]);

  if (!ready) return <div className="text-sm text-muted">Available once the mesh is loaded.</div>;

  const add = async (file: File | undefined) => {
    if (!file) return;
    setUploadError(null);
    const err = await tool.upload(file);
    if (err) setUploadError({ detail: sentence(err.detail), hint: err.hint ? sentence(err.hint) : null });
  };
  const dropZone = {
    onDragOver: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes("Files")) return;
      e.preventDefault();
      setDrag(true);
    },
    onDragLeave: () => setDrag(false),
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      setDrag(false);
      void add(e.dataTransfer.files?.[0]);
    },
  };

  // ---------------------------------------------------------------- choosing a reference
  const picker = (
    <div {...dropZone} className={cn("space-y-2 rounded-lg transition-colors", drag && "bg-accent-soft/40 outline-2 outline-dashed outline-accent")}>
      <div className="flex items-center gap-1">
        <span className="flex-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">CAD models of the project</span>
        <Help text={HELP.references} label="CAD models" />
      </div>
      {tool.refs === null ? (
        tool.refsError ? (
          <div className="text-[11.5px] text-danger">{sentence(tool.refsError.detail)}</div>
        ) : (
          <div className="flex items-center gap-2 text-xs text-muted">
            <Spinner size={13} /> Reading the list…
          </div>
        )
      ) : tool.refs.length ? (
        <ul className="space-y-1.5">
          {tool.refs.map((r) => {
            const current = c?.reference_id === r.id;
            return (
              <li key={r.id} className={cn("group rounded-lg border bg-surface-2/60 px-2.5 py-1.5", current ? "border-accent-line" : "border-line")}>
                <div className="flex items-center gap-1">
                  <FileBox size={13} className="shrink-0 text-muted" aria-hidden />
                  <NameEdit value={r.name} what="reference" onRename={(n) => tool.renameRef(r.id, n)} className="min-w-0 flex-1 text-xs font-semibold text-fg" />
                  <Button size="xs" variant={current ? "ghost" : "secondary"} disabled={current || tool.busy} onClick={() => tool.choose(r.id)} title="Compare this scan with it">
                    {current ? "In use" : "Compare"}
                  </Button>
                  <IconButton icon={X} size="xs" label={`Remove ${r.name} from the project (and the comparisons that use it)`} className="opacity-60 group-hover:opacity-100" onClick={() => tool.removeRef(r.id)} />
                </div>
                <div className="mt-0.5 pl-[18px] text-[11px] text-subtle tnum">
                  {fmtInt(r.triangles)} triangles · {fmtSize(r.size_mm)} across{r.display.decimated ? ` · shown with ${fmtInt(r.display.triangles)}` : ""}
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="text-[11.5px] text-subtle">None yet: add the part's CAD model as an STL, OBJ or PLY.</div>
      )}
      <input ref={fileRef} type="file" accept={ACCEPT} className="hidden" onChange={(e) => void add(e.target.files?.[0]).finally(() => (e.target.value = ""))} />
      <div className="flex items-center gap-2">
        <Button size="sm" icon={Upload} loading={tool.uploading} onClick={() => fileRef.current?.click()} title="An STL, OBJ or PLY of the part as it should be (up to 100&#8239;MB)">
          Add a file…
        </Button>
        <span className="text-[11px] text-subtle">or drop it here</span>
      </div>
      {tool.uploading ? <div className="text-[11.5px] text-muted">Reading and checking the file: a few seconds for a large one.</div> : null}
      {uploadError ? (
        <div className="rounded-lg border border-danger/40 bg-danger-soft/40 px-3 py-2 text-[11.5px] leading-4 text-fg" role="alert">
          <div className="flex items-start gap-1.5">
            <TriangleExclamationPoint size={12} className="mt-px shrink-0 text-danger" aria-hidden />
            <span>{uploadError.detail.replace(/\.?$/, ".")}</span>
          </div>
          {uploadError.hint ? <div className="mt-1 pl-[18px] text-subtle">{uploadError.hint.replace(/\.?$/, ".")}</div> : null}
        </div>
      ) : null}
    </div>
  );

  if (!tool.loaded) return <div className="flex items-center gap-2 text-sm text-muted"><Spinner size={14} /> Reading the comparison…</div>;
  if (!c || changing)
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted">Compare the scan with the part as it should be: how much of it is within a tolerance, and where it is not.</p>
        {picker}
        {changing ? (
          <Button size="xs" variant="ghost" onClick={() => setChanging(false)}>
            Keep the comparison as it is
          </Button>
        ) : null}
      </div>
    );

  // ---------------------------------------------------------------- a comparison
  const ref = c.reference;
  const choice = unitsChoice(c.units);
  const note = unitsNote(c.units, c.scale);
  const al = c.alignment;
  const lines = al ? alignmentLines(al) : null;
  const refused = tool.lastResult && !tool.lastResult.kept ? alignmentLines(tool.lastResult.alignment) : null;
  const choosing = !al || again;

  const header = (
    <div className="flex items-start gap-2 rounded-lg border border-line bg-surface-2/60 px-3 py-2">
      <FileBox size={14} className="mt-0.5 shrink-0 text-accent" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-semibold text-fg" title={ref?.name}>
          {ref?.name ?? "The CAD model is gone"}
        </div>
        {ref ? (
          <div className="text-[11px] text-subtle tnum">
            {fmtInt(ref.triangles)} triangles · {fmtSize(ref.size_mm * c.scale)} across
          </div>
        ) : null}
        {note ? <div className="text-[11px] text-muted">{note}</div> : null}
        {ref?.warnings.map((w) => (
          <div key={w} className="text-[11px] text-warn">
            {sentence(w)}.
          </div>
        ))}
      </div>
      <Button size="xs" variant="ghost" onClick={() => setChanging(true)} title="Pick another CAD model, or add one">
        Change
      </Button>
      <IconButton icon={Trash} size="xs" label="Remove this comparison (the CAD model stays with the project)" onClick={tool.remove} />
    </div>
  );

  if (choice)
    return (
      <div className="space-y-3">
        {header}
        <Callout tone="warn" icon={CircleExclamationPoint} title={choice.question}>
          <div className="space-y-2">
            <p className="text-[12px] leading-4">{c.units.message}</p>
            <div className="grid gap-1.5">
              {choice.scaled ? (
                <Button size="sm" variant="primary" loading={tool.busy} onClick={() => tool.units("apply")} title={`Multiply its numbers by ${choice.scaled.factor}`}>
                  {choice.scaled.label}: {choice.scaled.size}
                </Button>
              ) : null}
              <Button size="sm" variant={choice.scaled ? "secondary" : "primary"} loading={tool.busy && !choice.scaled} onClick={() => tool.units("keep")}>
                {choice.asIs.label}: {choice.asIs.size}
              </Button>
            </div>
            <div className="text-[11.5px] text-muted">The scan's part is about {choice.scan} across.</div>
          </div>
        </Callout>
      </div>
    );

  const alignBlock = (
    <div className="space-y-2">
      <div role="radiogroup" aria-label="How to align the scan with the CAD model" className="space-y-1">
        {ALIGN_METHODS.map((m) => {
          const off = m.value === "datums" && frame !== "current";
          const why = frame === "none" ? "Set the part's axes first (the panel “The part's axes”)." : "The part's axes are out of date: set them again first.";
          return (
            <div key={m.value} className="flex items-center gap-1">
              <label className={cn("flex flex-1 cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs", method === m.value ? "bg-accent-soft text-fg" : "text-muted hover:bg-surface-2", off && "cursor-not-allowed opacity-50")} title={off ? why : undefined}>
                <input type="radio" name={`${id}-method`} className="accent-[var(--c-accent)]" checked={method === m.value} disabled={off} onChange={() => setMethod(m.value)} />
                <span className="font-medium">{m.label}</span>
              </label>
              <Help text={off ? `${m.help} ${why}` : m.help} label={m.label} />
            </div>
          );
        })}
      </div>
      {tool.job ? (
        <JobProgress job={tool.job} label="Aligning with the CAD model" cancelTitle="Stop aligning. The alignment kept before stays." icon={GitCompareArrows} />
      ) : method === "points" ? (
        <Button size="sm" icon={MousePointerClick} disabled={!tool.refGeometry || picking} onClick={onPickPoints} title={tool.refGeometry ? "Click matching points on the scan and on the CAD model" : "Reading the CAD model's mesh…"}>
          Pick points…
        </Button>
      ) : (
        <Button size="sm" variant="primary" icon={GitCompareArrows} loading={tool.busy} onClick={() => tool.align({ method })}>
          {method === "shape" ? "Align from the shape" : "Align on the part's axes"}
        </Button>
      )}
      {al && again && !tool.job ? (
        <Button size="xs" variant="ghost" onClick={() => setAgain(false)}>
          Keep the alignment as it is
        </Button>
      ) : null}
    </div>
  );

  const result = lines ? (
    <div className="rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[11.5px] leading-4">
      <div className="flex items-start gap-1.5">
        <span className="flex-1 font-semibold text-fg">{lines.title}</span>
        {!choosing ? (
          <Button size="xs" variant="ghost" icon={RefreshCw} onClick={() => setAgain(true)} disabled={!!tool.job}>
            Again
          </Button>
        ) : null}
      </div>
      <div className="mt-1 text-muted">{lines.verdict}</div>
      {lines.notes.map((n) => (
        <div key={n} className="mt-1 text-muted">
          {n}
        </div>
      ))}
      {c.mesh_changed ? <div className="mt-1 text-warn">The mesh has changed since the scan was aligned with the CAD model. The alignment is kept (the scan's axes are the same) and the deviation is worked out again on the current mesh.</div> : null}
    </div>
  ) : null;

  return (
    <div className="space-y-3">
      {header}
      {result}
      {refused ? (
        <Callout tone="warn" icon={TriangleExclamationPoint} title={refused.title}>
          <span className="text-[12px] leading-4">
            {refused.verdict} {al ? "The alignment kept before stays." : ""}
          </span>
        </Callout>
      ) : null}
      {tool.lastError ? (
        <Callout tone="danger" icon={CircleExclamationPoint} title="The alignment failed">
          <span className="text-[12px] leading-4">{sentence(tool.lastError)}</span>
        </Callout>
      ) : null}
      {choosing || tool.job ? alignBlock : null}

      {al ? (
        <>
          <div className="flex items-start gap-1">
            <div className="min-w-0 flex-1">
              <Switch checked={tool.ghost} onChange={tool.setGhost} label="Show the CAD model as a ghost" disabled={!tool.refGeometry} />
            </div>
            <Help text={HELP.ghost} label="Ghost" />
          </div>
          {tool.refMeshError ? <div className="text-[11px] text-danger">Couldn't read the CAD model's mesh: {tool.refMeshError}</div> : null}
          <Button size="sm" variant={shadingOn ? "primary" : "secondary"} icon={Eye} className="w-full" aria-pressed={shadingOn} onClick={() => onShading(!shadingOn)} title="The scan coloured by its deviation from the CAD model (7)">
            {shadingOn ? "Shown on the scan" : "Show it on the scan"}
          </Button>
          <div>
            <div className="mb-1.5 flex items-center gap-1">
              <label htmlFor={id} className="flex-1 text-xs font-medium text-muted">
                Tolerance
              </label>
              <Help text={HELP.tolerance} label="Tolerance" />
            </div>
            <ToleranceField id={id} value={tool.tolerance} onChange={tool.setTolerance} />
          </div>
          <div aria-live="polite" aria-busy={tool.answerLoading}>
            {tool.answerError ? (
              <div className="text-[11.5px] text-danger">{sentence(tool.answerError)}</div>
            ) : a ? (
              <div className={cn("transition-opacity duration-150", tool.answerLoading && "opacity-50")}>
                <p className="text-sm font-medium leading-5 text-fg">{toleranceSentence(a)}</p>
                <ShareBar a={a} />
                {a.share_uncertain ? (
                  <div className="mt-1.5 text-[11.5px] leading-4 text-muted">
                    On {fmtShare(a.share_uncertain)} of the compared area the scan's own scatter is wider than the tolerance: there it cannot tell within from outside.
                  </div>
                ) : null}
                {coverageLines(a.coverage).map((l) => (
                  <div key={l} className="mt-1.5 text-[11.5px] leading-4 text-muted">
                    {l}
                  </div>
                ))}
              </div>
            ) : tool.answerLoading ? (
              <div className="flex items-center gap-2 text-sm text-fg" role="status">
                <Spinner size={14} className="text-accent" /> Comparing at ± {fmtTolerance(tool.tolerance)}&#8239;mm…
              </div>
            ) : null}
          </div>
          {a && a.regions.length ? (
            <div>
              <div className="mb-1.5 flex items-center gap-1">
                <span className="flex-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">Outside the tolerance</span>
                <Help text={HELP.uncertainty} label="The scan's own scatter" />
              </div>
              <ol className="space-y-2" aria-label="Areas outside the tolerance, largest first">
                {(all ? a.regions : a.regions.slice(0, FIRST)).map((r, i) => (
                  <AreaRow key={i} r={r} n={i + 1} on={tool.lit?.index === i} k={a.k} onSelect={() => tool.select(i)} />
                ))}
              </ol>
              {a.regions.length > FIRST ? (
                <Button size="xs" variant="ghost" className="mt-2" aria-expanded={all} onClick={() => setAll((v) => !v)}>
                  {all ? "Show the largest only" : `Show all ${a.regions.length} areas`}
                </Button>
              ) : null}
              {a.n_regions > a.regions.length ? <div className="mt-1 text-[11px] text-subtle">The {a.regions.length} largest of {a.n_regions} are listed.</div> : null}
              <div className="mt-1 text-[11px] text-subtle">Click an area to see it on the mesh. Smaller areas count in the shares, but are not listed.</div>
            </div>
          ) : a && a.share_within !== null ? (
            <div className="text-[11.5px] text-muted">No area outside the tolerance is large enough to list.</div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
