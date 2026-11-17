/**
 * The viewer's "Flush along a seam" panel (FEATURE_PLAN.md I4): the Flush tool's controls, what the engine read, and
 * the seams kept with the scan. Two clicks pick the panels (the first is panel A, the second panel B); the answer
 * shows the mean flush first (the number to trust), then the lowest and the highest and where along the seam, which
 * panel stands proud in words, a small chart along the seam (FlushChart.tsx), and "Gap: not offered" with the engine's
 * reason on hover. "Keep" keeps the seam under a name (its two clicks); the kept ones are listed, opened (measured
 * again on the current mesh), renamed and deleted; one measured on an earlier mesh is marked, with "Mark as current".
 * "Stations (CSV)" downloads every station. The measuring is useFlushTool.ts's, the marks on the model FlushMarks.tsx's.
 */
import { Bookmark, CircleHelp, Download, FolderOpen, MousePointerClick, RotateCcw, SeparatorVertical, TriangleExclamationPoint, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, sentence } from "../../api/http";
import type { KeptSeam } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Kbd, Spinner } from "../../components/ui/Feedback";
import { TextInput } from "../../components/ui/Form";
import { Tooltip } from "../../components/ui/Popover";
import { downloadFile } from "../../lib/download";
import { flushWords } from "../../lib/flush";
import { cn } from "../../lib/util";
import { useSeams } from "../../store/seams";
import { FlushChart } from "./FlushChart";
import { NameEdit } from "./NameEdit";
import type { FlushTool } from "./useFlushTool";

export const FLUSH_ICON = SeparatorVertical;

const HELP = {
  clicks:
    "Click once on each panel, a few millimetres from the seam. The first click's panel is panel A, the second's panel B. The engine follows the seam both ways and reads a section across it every 3 mm.",
  mean: "The mean over the stations whose flush is trusted: the number to trust. + means panel B stands above panel A (square to panel A), − below.",
  pm: "A ± is the spread from scan to scan (1σ), worked out from the scan's own points: not the accuracy. Errors of the calibration and of the scale come on top.",
  trusted: "A station is not trusted when a panel has no surface there, or its reading is far from its neighbours'. Those are left out of the lowest, mean and highest.",
  kept: "A kept seam keeps its two clicks. Its stations are not kept: opening it measures the current mesh again from the same clicks.",
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

function Row({ label, value, help }: { label: string; value: ReactNode; help?: { text: string; label: string } }) {
  return (
    <div className="flex items-baseline gap-2 text-xs">
      <span className="w-[62px] shrink-0 text-muted">{label}</span>
      <span className="min-w-0 flex-1 font-mono text-fg tnum">{value}</span>
      {help ? <Help {...help} /> : null}
    </div>
  );
}

export function FlushPanel({
  pid,
  sid,
  on,
  onToggle,
  tool,
  kept,
  ready,
}: {
  pid: string;
  sid: string;
  /** The tool is on. */
  on: boolean;
  onToggle: (on: boolean) => void;
  tool: FlushTool;
  kept: KeptSeam[];
  ready: boolean;
}) {
  const [name, setName] = useState("");
  const [keeping, setKeeping] = useState(false);
  const a = tool.answer;
  const w = a ? flushWords(a) : null;
  const keep = async () => {
    if (!tool.request) return;
    setKeeping(true);
    const rec = await useSeams.getState().keep(pid, sid, { ...tool.request, ...(name.trim() ? { name: name.trim() } : {}) });
    setKeeping(false);
    if (rec) {
      setName("");
      tool.openKept(rec);
    }
  };
  const csv = () => {
    const url = tool.keptId ? api.keptSeamStationsUrl(pid, sid, tool.keptId) : tool.request ? api.flushStationsUrl(pid, sid, tool.request) : null;
    if (url) void downloadFile(url, a?.filename ?? "flush.csv");
  };

  return (
    <div className="space-y-3">
      {!on ? (
        <>
          <p className="text-sm text-muted">The step between two panels along their seam: the mean, the lowest and the highest, and a chart along the seam.</p>
          <Button size="sm" icon={FLUSH_ICON} disabled={!ready} onClick={() => onToggle(true)} title="Measure the flush along a seam (U)">
            Measure flush
          </Button>
        </>
      ) : (
        <div className="space-y-2.5">
          <div className="flex items-center gap-2 rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-xs text-muted">
            <MousePointerClick size={14} className="shrink-0 text-accent" aria-hidden />
            <span className="flex-1">
              {tool.keptId
                ? "A kept seam, measured on the current mesh"
                : tool.clicks.length === 0
                  ? "Click panel A, near the seam"
                  : tool.clicks.length === 1
                    ? "Now click panel B, across the seam"
                    : "The flush between panel A and panel B"}
            </span>
            {tool.busy ? <Spinner size={13} className="text-accent" /> : null}
            {tool.clicks.length ? (
              <Button size="xs" variant="ghost" icon={RotateCcw} onClick={tool.clear} title="Pick the two panels again">
                Again
              </Button>
            ) : null}
            <Help text={HELP.clicks} label="How to pick the seam" />
          </div>

          {tool.error ? (
            <div className="rounded-lg border border-danger/40 bg-danger-soft/40 px-3 py-2 text-[11.5px] leading-4 text-fg" role="alert">
              <div className="flex items-start gap-1.5">
                <TriangleExclamationPoint size={12} className="mt-px shrink-0 text-danger" aria-hidden />
                <span>{sentence(tool.error.detail)}.</span>
              </div>
              {tool.error.hint ? <div className="mt-1 pl-[18px] text-subtle">{sentence(tool.error.hint)}.</div> : null}
            </div>
          ) : null}

          {a && w ? (
            <div className={cn("space-y-2", tool.busy && "opacity-60")}>
              <div className="rounded-lg border border-line bg-surface-2/60 px-3 py-2">
                <div className="flex items-baseline gap-2">
                  <span className="text-xs text-muted">Mean flush</span>
                  <span className="flex-1 text-right font-mono text-[17px] font-semibold text-fg tnum">{w.mean !== null ? `${w.mean} mm` : "—"}</span>
                  <Help text={HELP.mean} label="Mean flush" />
                </div>
                {w.sigma !== null ? (
                  <div className="flex items-center justify-end gap-1 text-[11px] text-subtle">
                    each station ± about {w.sigma} mm
                    <Help text={HELP.pm} label="What the ± means" />
                  </div>
                ) : null}
              </div>
              <Row label="Lowest" value={w.lowest !== null ? `${w.lowest} mm ${w.lowestAt}` : "—"} />
              <Row label="Highest" value={w.highest !== null ? `${w.highest} mm ${w.highestAt}` : "—"} />
              <Row label="Stations" value={<span className="font-sans">{w.trusted}</span>} help={{ text: HELP.trusted, label: "Stations not trusted" }} />
              {w.proud ? <p className="text-xs text-fg">{w.proud}</p> : null}
              <FlushChart record={a} />
              <div className="flex items-center gap-1 text-xs">
                <span className="text-muted">Gap:</span>
                <span className="flex-1 text-fg">not offered</span>
                <Tooltip side="top" content={<div className="max-w-[300px] text-muted">{a.gap_note}</div>}>
                  <button type="button" className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:text-fg" aria-label={`Why the gap is not offered: ${a.gap_note}`}>
                    <CircleHelp size={13} aria-hidden />
                  </button>
                </Tooltip>
              </div>
              {a.warnings.length ? (
                <ul className="space-y-1">
                  {a.warnings.map((x) => (
                    <li key={x} className="flex items-start gap-1.5 text-[11px] leading-4 text-warn">
                      <TriangleExclamationPoint size={12} className="mt-px shrink-0" aria-hidden />
                      <span>{sentence(x)}.</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          <div className="flex items-center gap-1.5">
            <TextInput
              className="min-w-0 flex-1"
              value={name}
              maxLength={80}
              placeholder={tool.keptId ? "Kept: see below" : "Name (optional)"}
              aria-label="Name of the seam to keep"
              disabled={!!tool.keptId}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Enter") void keep();
              }}
            />
            <Button size="sm" icon={Bookmark} loading={keeping} disabled={!tool.request || !!tool.keptId || !a || !ready} onClick={() => void keep()} title="Keep this seam with the scan (its two clicks)">
              Keep
            </Button>
          </div>
          <Button size="sm" className="w-full" icon={Download} disabled={!a} onClick={csv} title="Every station along the seam: its place, the flush and its ±, whether it is trusted and why not">
            Stations (CSV)
          </Button>
          <div className="text-[11px] text-subtle">
            <Kbd>U</Kbd> or <Kbd>Esc</Kbd> leaves the tool; the view comes back as it was.
          </div>
        </div>
      )}

      <div>
        <div className="mb-1.5 flex items-center gap-1">
          <span className="flex-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">Kept seams</span>
          <Help text={HELP.kept} label="Kept seams" />
        </div>
        {kept.length ? (
          <ul className="space-y-1.5">
            {kept.map((s) => {
              const open = on && tool.keptId === s.id;
              return (
                <li key={s.id} className={cn("group rounded-lg border bg-surface-2/60 px-2.5 py-1.5", open ? "border-accent-line" : "border-line")}>
                  <div className="flex items-center gap-1">
                    <NameEdit value={s.name} what="seam" onRename={(n) => useSeams.getState().rename(pid, sid, s.id, n)} className="min-w-0 flex-1 text-xs font-semibold text-fg" />
                    <IconButton
                      icon={FolderOpen}
                      size="xs"
                      label={`Open ${s.name} (measured again on the current mesh)`}
                      active={open}
                      disabled={!ready}
                      onClick={() => {
                        if (!on) onToggle(true);
                        tool.openKept(s);
                      }}
                    />
                    <IconButton icon={X} size="xs" label={`Remove ${s.name}`} className="opacity-60 group-hover:opacity-100" onClick={() => void useSeams.getState().remove(pid, sid, s.id)} />
                  </div>
                  {s.mesh_changed ? (
                    <div className="mt-1 flex items-center gap-1.5 text-[11px] text-warn">
                      <TriangleExclamationPoint size={12} className="shrink-0" aria-hidden />
                      <span className="flex-1" title={`Kept on mesh version ${s.mesh_version}: opening it measures the current mesh.`}>
                        The mesh has changed since it was kept
                      </span>
                      <Button size="xs" variant="ghost" onClick={() => void useSeams.getState().markCurrent(pid, sid, s.id)} title="Keep it as measured on the current mesh (its clicks stay as they are)">
                        Mark as current
                      </Button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="text-[11.5px] text-subtle">None yet: measure a seam and press Keep.</div>
        )}
      </div>
    </div>
  );
}
