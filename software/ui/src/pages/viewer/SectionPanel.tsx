/**
 * The viewer's "Sections" panel (FEATURE_PLAN.md I1): the Section tool's controls and the sections kept with the scan.
 * The plane is chosen from a kept plane moved along its normal (an offset typed or dragged), from the part's frame
 * (X, Y or Z = a value, when the scan has a coordinate system) or by clicking three points on the surface; a switch
 * hides the part of the model in front of it. "Keep" keeps the section on screen under a name (its plane as numbers);
 * the kept ones are listed, opened (cut again on the current mesh), renamed and deleted; one cut on an earlier mesh is
 * marked, with "Mark as current". DXF and SVG download the section on screen at 1:1 in mm. The cut itself is
 * useSectionTool.ts's, the profile SectionProfile.tsx's.
 */
import { Bookmark, CircleHelp, Download, FolderOpen, MousePointerClick, RotateCcw, Slice, TriangleExclamationPoint, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, sentence } from "../../api/http";
import type { KeptSection, SectionFile } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Kbd } from "../../components/ui/Feedback";
import { Segmented, Select, Slider, Switch, TextInput } from "../../components/ui/Form";
import { Tooltip } from "../../components/ui/Popover";
import { downloadFile } from "../../lib/download";
import { cn } from "../../lib/util";
import { useSections } from "../../store/sections";
import type { FitItem } from "./FitTool";
import { NameEdit } from "./NameEdit";
import type { SectionAxis, SectionMode, SectionTool } from "./useSectionTool";

export const SECTION_ICON = Slice;

const HELP = {
  fit: "A kept plane, moved along its own normal by the offset (+ the way its normal points). Drag the slider: the cut follows.",
  frame: "A plane of the part's coordinate system: X, Y or Z = the value, in mm from the part's origin. The drawing is in the part's axes.",
  points: "Click three points on the surface: the plane goes through them. Their order sets the side the drawing is seen from (right-hand rule).",
  hide: "Hides the part of the model on the side the plane's normal points to: the side the profile is seen from. Only the view: the mesh is not changed.",
  kept: "A kept section keeps its plane as numbers (the fit or the coordinate system it was named from can change or go: it stays). Its lines are not kept: opening it cuts the current mesh again.",
} as const;

function Help({ text, label }: { text: string; label: string }) {
  return (
    <Tooltip side="top" content={<div className="max-w-[280px] text-muted">{text}</div>}>
      <button type="button" className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:text-fg" aria-label={`${label}: ${text}`}>
        <CircleHelp size={13} aria-hidden />
      </button>
    </Tooltip>
  );
}

/** A number field that takes what is typed once it reads as a number (a half-typed "-" or "1." waits). */
function MmInput({ value, onChange, label }: { value: number; onChange: (v: number) => void; label: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <div className="relative w-[92px] shrink-0">
      <TextInput
        mono
        inputMode="decimal"
        aria-label={label}
        value={draft ?? String(Math.round(value * 1000) / 1000)}
        onChange={(e) => {
          setDraft(e.target.value);
          const v = Number(e.target.value.replace(",", "."));
          if (e.target.value.trim() !== "" && Number.isFinite(v)) onChange(v);
        }}
        onBlur={() => setDraft(null)}
        onKeyDown={(e) => e.stopPropagation()}
        className="pr-8 text-right"
      />
      <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-subtle">mm</span>
    </div>
  );
}

/** How a kept section was named, in a few words. */
function named(s: KeptSection, fits: FitItem[]): string {
  const d = s.definition;
  if (d.kind === "fit") {
    const now = fits.find((f) => f.fid === d.fit);
    const off = d.offset_mm ?? 0;
    return `on ${now?.name ?? d.fit_name ?? "a kept plane"}${off ? `, ${off > 0 ? "+" : ""}${off}\u202fmm` : ""}${now ? "" : " (fit deleted)"}`;
  }
  if (d.kind === "frame") return `${d.axis.replace("-", "")} = ${d.value_mm}\u202fmm${d.axis.startsWith("-") ? ", from −" + d.axis.slice(1) : ""}`;
  if (d.kind === "points") return "through three points";
  return "through a point";
}

export function SectionPanel({
  pid,
  sid,
  on,
  onToggle,
  tool,
  fits,
  hasFrame,
  kept,
  ready,
}: {
  pid: string;
  sid: string;
  /** The tool is on. */
  on: boolean;
  onToggle: (on: boolean) => void;
  tool: SectionTool;
  /** The kept fits (the planes are offered). */
  fits: FitItem[];
  hasFrame: boolean;
  kept: KeptSection[];
  ready: boolean;
}) {
  const [name, setName] = useState("");
  const [keeping, setKeeping] = useState(false);
  const planes = fits.filter((f) => f.kind === "plane");
  const a = tool.answer;
  const file = (f: SectionFile) => {
    const url = tool.keptId ? api.keptSectionFileUrl(pid, sid, tool.keptId, f) : tool.def ? api.sectionFileUrl(pid, sid, tool.def, f) : null;
    if (!url) return;
    const fallback = `section.${f === "section.dxf" ? "dxf" : "svg"}`;
    void downloadFile(url, (f === "section.dxf" ? a?.filenames.dxf : a?.filenames.svg) ?? fallback);
  };
  const keep = async () => {
    if (!tool.def) return;
    setKeeping(true);
    const rec = await useSections.getState().keep(pid, sid, { ...tool.def, ...(name.trim() ? { name: name.trim() } : {}) });
    setKeeping(false);
    if (rec) {
      setName("");
      tool.openKept(rec);
    }
  };
  const row = (label: string, help: { text: string; label: string } | null, control: ReactNode) => (
    <div className="flex items-center gap-1">
      <span className="w-[52px] shrink-0 text-xs text-muted">{label}</span>
      {help ? <Help {...help} /> : <span className="w-5 shrink-0" />}
      <div className="flex min-w-0 flex-1 items-center gap-1.5">{control}</div>
    </div>
  );

  return (
    <div className="space-y-3">
      {!on ? (
        <>
          <p className="text-sm text-muted">Cut the scan with a plane: the 2D profile, a distance on it, and the drawing as DXF or SVG at 1:1.</p>
          <Button size="sm" icon={Slice} disabled={!ready} onClick={() => onToggle(true)} title="Cut a section (S)">
            Cut a section
          </Button>
        </>
      ) : (
        <div className="space-y-2.5">
          <Segmented<SectionMode>
            label="How the plane is chosen"
            size="sm"
            className="w-full"
            value={tool.mode}
            onChange={tool.setMode}
            options={[
              { value: "fit", label: "Kept plane", title: planes.length ? HELP.fit : "Fit a plane first (Fit, P)", disabled: !planes.length },
              { value: "frame", label: "Part's frame", title: hasFrame ? HELP.frame : "Build the coordinate system first", disabled: !hasFrame },
              { value: "points", label: "3 points", title: HELP.points },
            ]}
          />
          {tool.mode === "fit" ? (
            <>
              {row(
                "Plane",
                { text: HELP.fit, label: "Kept plane" },
                <Select className="min-w-0 flex-1" value={tool.fitId} aria-label="Kept plane to cut along" onChange={(e) => tool.setFitId(e.target.value)}>
                  {!tool.fitId ? <option value="">A kept plane…</option> : null}
                  {planes.map((f) => (
                    <option key={f.fid} value={f.fid}>
                      {f.id}. {f.name}
                      {f.stale ? " (fitted on an earlier mesh)" : ""}
                    </option>
                  ))}
                </Select>,
              )}
              {row("Offset", null, <MmInput value={tool.offset} onChange={tool.setOffset} label="Offset along the plane's normal" />)}
              <Slider value={Math.min(Math.max(tool.offset, tool.ranges.offset[0]), tool.ranges.offset[1])} min={tool.ranges.offset[0]} max={tool.ranges.offset[1]} step={0.1} label="Offset along the plane's normal" onChange={tool.setOffset} disabled={!tool.fitId} />
            </>
          ) : null}
          {tool.mode === "frame" ? (
            <>
              {row(
                "Axis",
                { text: HELP.frame, label: "A plane of the part's frame" },
                <>
                  <Segmented<SectionAxis>
                    label="Axis"
                    size="sm"
                    value={tool.axis}
                    onChange={tool.setAxis}
                    options={(["X", "Y", "Z"] as SectionAxis[]).map((x) => ({ value: x, label: `${x} =`, title: `The plane ${x} = the value, seen from +${x}` }))}
                  />
                  <MmInput value={tool.value} onChange={tool.setValue} label={`${tool.axis} value`} />
                </>,
              )}
              <Slider value={Math.min(Math.max(tool.value, tool.ranges.value[0]), tool.ranges.value[1])} min={tool.ranges.value[0]} max={tool.ranges.value[1]} step={0.1} label={`${tool.axis} value`} onChange={tool.setValue} />
            </>
          ) : null}
          {tool.mode === "points" ? (
            <div className="flex items-center gap-2 rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-xs text-muted">
              <MousePointerClick size={14} className="shrink-0 text-accent" aria-hidden />
              <span className="flex-1">{tool.points.length < 3 ? `Click point ${tool.points.length + 1} of 3 on the surface` : "The plane goes through the three points"}</span>
              {tool.points.length ? (
                <Button size="xs" variant="ghost" icon={RotateCcw} onClick={tool.clearPoints} title="Pick the three points again">
                  Again
                </Button>
              ) : null}
            </div>
          ) : null}
          <div className="flex items-start gap-1">
            <div className="min-w-0 flex-1">
              <Switch checked={tool.hideFront} onChange={tool.setHideFront} label="Hide the part in front" />
            </div>
            <Help text={HELP.hide} label="Hide the part in front" />
          </div>
          {tool.planeError || tool.error ? (
            <div className="rounded-lg border border-danger/40 bg-danger-soft/40 px-3 py-2 text-[11.5px] leading-4 text-fg" role="alert">
              <div className="flex items-start gap-1.5">
                <TriangleExclamationPoint size={12} className="mt-px shrink-0 text-danger" aria-hidden />
                <span>{sentence(tool.planeError ?? tool.error!.detail)}.</span>
              </div>
              {!tool.planeError && tool.error?.hint ? <div className="mt-1 pl-[18px] text-subtle">{sentence(tool.error.hint)}.</div> : null}
            </div>
          ) : null}
          <div className="flex items-center gap-1.5">
            <TextInput
              className="min-w-0 flex-1"
              value={name}
              maxLength={80}
              placeholder={tool.keptId ? "Kept: see below" : "Name (optional)"}
              aria-label="Name of the section to keep"
              disabled={!!tool.keptId}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Enter") void keep();
              }}
            />
            <Button size="sm" icon={Bookmark} loading={keeping} disabled={!tool.def || !!tool.keptId || !!tool.planeError || !ready} onClick={() => void keep()} title="Keep this section with the scan">
              Keep
            </Button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Button size="sm" icon={Download} disabled={!a || (!tool.def && !tool.keptId)} onClick={() => file("section.dxf")} title="The section as a DXF drawing (R12) at 1:1 in mm, measured and not-measured stretches on two layers">
              DXF
            </Button>
            <Button size="sm" icon={Download} disabled={!a || (!tool.def && !tool.keptId)} onClick={() => file("section.svg")} title="The section as an SVG at 1:1 in mm, with a scale bar">
              SVG
            </Button>
          </div>
          <div className="text-[11px] text-subtle">
            <Kbd>S</Kbd> or <Kbd>Esc</Kbd> leaves the tool; the view comes back as it was.
          </div>
        </div>
      )}

      <div>
        <div className="mb-1.5 flex items-center gap-1">
          <span className="flex-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">Kept sections</span>
          <Help text={HELP.kept} label="Kept sections" />
        </div>
        {kept.length ? (
          <ul className="space-y-1.5">
            {kept.map((s) => {
              const open = on && tool.keptId === s.id;
              return (
                <li key={s.id} className={cn("group rounded-lg border bg-surface-2/60 px-2.5 py-1.5", open ? "border-accent-line" : "border-line")}>
                  <div className="flex items-center gap-1">
                    <NameEdit value={s.name} what="section" onRename={(n) => useSections.getState().rename(pid, sid, s.id, n)} className="min-w-0 flex-1 text-xs font-semibold text-fg" />
                    <IconButton
                      icon={FolderOpen}
                      size="xs"
                      label={`Open ${s.name} (cut again on the current mesh)`}
                      active={open}
                      disabled={!ready}
                      onClick={() => {
                        if (!on) onToggle(true);
                        tool.openKept(s);
                      }}
                    />
                    <IconButton icon={X} size="xs" label={`Remove ${s.name}`} className="opacity-60 group-hover:opacity-100" onClick={() => void useSections.getState().remove(pid, sid, s.id)} />
                  </div>
                  <div className="mt-0.5 truncate text-[11px] text-subtle" title={s.plane.description}>
                    {named(s, fits)}
                  </div>
                  {s.mesh_changed ? (
                    <div className="mt-1 flex items-center gap-1.5 text-[11px] text-warn">
                      <TriangleExclamationPoint size={12} className="shrink-0" aria-hidden />
                      <span className="flex-1" title={`Kept on mesh version ${s.mesh_version}: opening it cuts the current mesh.`}>
                        The mesh has changed since it was kept
                      </span>
                      <Button size="xs" variant="ghost" onClick={() => void useSections.getState().markCurrent(pid, sid, s.id)} title="Keep it as cut on the current mesh (its plane stays as it is)">
                        Mark as current
                      </Button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="text-[11.5px] text-subtle">None yet: cut a section and press Keep.</div>
        )}
      </div>
    </div>
  );
}
