/**
 * The edit history: the base mesh, then every edit with its triangles after and how long it
 * took. Clicking a step undoes (or redoes) to it; steps after the cursor are greyed until a
 * new edit drops them. Undo / redo buttons (Ctrl+Z, Ctrl+Shift+Z or Ctrl+Y) sit on top.
 */
import { ListClock, Redo2, Undo2 } from "lucide-react";
import type { ProcessState } from "../../api/types";
import { IconButton } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Feedback";
import { fmtDateTime, fmtInt, fmtRelative } from "../../lib/format";
import { cn } from "../../lib/util";
import { processKey, useProcess } from "../../store/process";
import { Help, Panel } from "./bits";

const STAGE_LABEL = { track: "Track", straighten: "Straighten", fuse: "Fuse" } as const;

function fmtMs(ms: number): string {
  if (!Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.max(1, Math.round(ms))} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

export function HistoryPanel({ pid, sid, state, busy }: { pid: string; sid: string; state: ProcessState; busy: boolean }) {
  const undo = useProcess((s) => s.undo);
  const redo = useProcess((s) => s.redo);
  const jumpTo = useProcess((s) => s.jumpTo);
  const action = useProcess((s) => s.entries[processKey(pid, sid)]?.action);
  const { edits, cursor } = state;
  const prev = cursor > 0 ? edits[cursor - 1] : null;
  const next = cursor < edits.length ? edits[cursor] : null;
  const moving = action === "undo" || action === "redo" || action === "jump";

  return (
    <Panel
      title="History"
      icon={ListClock}
      aside={
        <div className="flex items-center gap-0.5">
          {moving ? <Spinner size={13} className="mr-1 text-accent" /> : null}
          <IconButton
            icon={Undo2}
            size="xs"
            label={prev ? `Undo “${prev.label}” (Ctrl+Z)` : "Nothing to undo"}
            disabled={busy || !state.can_undo}
            onClick={() => void undo(pid, sid)}
          />
          <IconButton
            icon={Redo2}
            size="xs"
            label={next ? `Redo “${next.label}” (Ctrl+Shift+Z)` : "Nothing to redo"}
            disabled={busy || !state.can_redo}
            onClick={() => void redo(pid, sid)}
          />
        </div>
      }
    >
      {state.base ? (
        <ol className="space-y-1" aria-label="Edit history">
          <li>
            <button
              type="button"
              disabled={busy || cursor === 0}
              aria-current={cursor === 0 ? "step" : undefined}
              onClick={() => void jumpTo(pid, sid, 0)}
              title={cursor === 0 ? "You're at the base mesh" : "Undo every edit"}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-lg border px-2.5 py-1.5 text-left transition-colors disabled:cursor-default",
                cursor === 0 ? "border-accent-line bg-accent-soft" : "border-transparent hover:bg-surface-2",
              )}
            >
              <span className={cn("grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] font-bold", cursor === 0 ? "bg-accent text-accent-fg" : "bg-surface-3 text-muted")}>
                0
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-fg">Base mesh</span>
                <span className="block text-[11px] text-subtle">
                  from {STAGE_LABEL[state.base.stage] ?? state.base.stage} · <span className="tnum">{fmtInt(state.base.triangles)}</span> triangles
                </span>
              </span>
            </button>
          </li>
          {edits.map((e, i) => {
            const applied = i < cursor;
            const here = i === cursor - 1;
            return (
              <li key={e.id}>
                <button
                  type="button"
                  disabled={busy || here}
                  aria-current={here ? "step" : undefined}
                  onClick={() => void jumpTo(pid, sid, i + 1)}
                  title={here ? "The current step" : applied ? "Undo back to this step" : "Redo up to this step"}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg border px-2.5 py-1.5 text-left transition-colors disabled:cursor-default",
                    here ? "border-accent-line bg-accent-soft" : "border-transparent hover:bg-surface-2",
                    !applied && "opacity-55",
                  )}
                >
                  <span
                    className={cn(
                      "grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] font-bold tnum",
                      here ? "bg-accent text-accent-fg" : applied ? "bg-surface-3 text-fg" : "border border-dashed border-line-strong text-subtle",
                    )}
                  >
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={cn("block truncate text-xs font-medium text-fg", !applied && "line-through decoration-subtle")}>{e.label}</span>
                    <span className="block text-[11px] text-subtle">
                      <span className="tnum">{fmtInt(e.triangles_after)}</span> triangles · {fmtMs(e.ms)}
                      {!applied ? " · undone" : ""}
                    </span>
                  </span>
                  <time className="shrink-0 text-[10.5px] text-subtle" dateTime={e.created} title={fmtDateTime(e.created)}>
                    {fmtRelative(e.created)}
                  </time>
                </button>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="text-sm text-muted">The history starts once there is a mesh.</div>
      )}
      {state.base && edits.length ? (
        <Help className="mt-2.5">Click a step to go back to it. Undone steps stay until you make a new edit.</Help>
      ) : state.base ? (
        <Help className="mt-2.5">No edits yet: the mesh is the base from the last stage.</Help>
      ) : null}
    </Panel>
  );
}
