/**
 * The thin-parts hint on the result page (ENGINE_PLAN X5): what Make mesh found that a finer mesh detail could help
 * (thin walls, a thin plate, small holes), and what it would cost, as measured. Where the engine offers it (the graphics
 * card's path, mesh detail 1 mm: `words.fine`) the first and primary choice is “Make only the thin walls finer” (step 2:
 * Make mesh again with the scan's choice on, `POST .../process {quality, fine_walls: true}`); the second is the preset
 * “Thin sheet metal and small parts”, as before (the Preset box's way: `PUT .../preset`, then Make mesh). Elsewhere the
 * preset alone, as before. “Not for this scan” puts it away for good. Nothing happens by itself.
 *
 * After a mesh made with the choice on, `FineWallsNote` takes the hint's place: what was done and one action, “Make the
 * mesh again without this” (`fine_walls: false`), or why it did not act, in one line.
 */
import { Layers, Lightbulb, Undo2, Wand, X } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/http";
import type { ProcessQuality, ThinPartsHint as Hint } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { normalizeQuality } from "../../lib/quality";
import { fineWallsNote, hintChoices } from "../../lib/thinParts";
import { useJobs } from "../../store/jobs";
import { usePresets } from "../../store/presets";
import { useProcess } from "../../store/process";
import { toast } from "../../store/toasts";
import type { MadeInfo } from "../../api/types";

function Lines({ title, lines, muted = true }: { title: string; lines: string[]; muted?: boolean }) {
  return (
    <div>
      <div className="font-medium text-fg">{title}</div>
      <ul className={`space-y-0.5 ${muted ? "text-muted" : "text-fg"}`}>
        {lines.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </div>
  );
}

export function ThinPartsHint({
  pid,
  sid,
  name,
  hint,
  quality,
  blocked,
}: {
  pid: string;
  sid: string;
  name: string;
  hint: Hint;
  /** The quality the mesh was made at: the new one is made at the same. */
  quality?: unknown;
  /** Why the mesh cannot be made again (a freed recording), or null. */
  blocked?: string | null;
}) {
  const [busy, setBusy] = useState<"fine" | "preset" | "dismiss" | null>(null);
  const w = hint.words;
  if (!w) return null;
  const q: ProcessQuality = normalizeQuality(quality) ?? "standard";
  const choices = hintChoices(hint);
  const fine = w.fine ?? null;

  const makePreset = async () => {
    setBusy("preset");
    const set = await usePresets.getState().setScan(pid, sid, { preset_id: hint.preset_id });
    if (set) await useJobs.getState().process(pid, sid, q, name);
    setBusy(null);
  };
  const makeFine = async () => {
    setBusy("fine");
    await useJobs.getState().process(pid, sid, q, name, true);
    setBusy(null);
  };
  const dismiss = async () => {
    setBusy("dismiss");
    try {
      useProcess.getState().apply(await api.dismissThinParts(pid, sid));
    } catch (e) {
      toast.error("Couldn't put the hint away", e);
    }
    setBusy(null);
  };

  return (
    <section className="rounded-lg border border-accent-line bg-accent-soft/40 px-3 py-2.5 text-xs" aria-label={w.title} role="note">
      <div className="flex items-start gap-2.5">
        <Lightbulb size={14} className="mt-px shrink-0 text-accent" aria-hidden />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1 font-semibold text-fg">{w.title}</div>
            <IconButton size="xs" icon={X} label="Not for this scan" onClick={() => void dismiss()} disabled={busy !== null} />
          </div>
          <ul className="space-y-0.5 text-fg">
            {w.found.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          {fine ? (
            <div className="space-y-1.5 rounded-md border border-accent-line/60 bg-surface/60 px-2.5 py-2">
              <div className="font-semibold text-fg">{fine.action}</div>
              <p className="text-muted">{fine.intro}</p>
              <Lines title="What it would change" lines={fine.gain} />
              <Lines title="What it costs" lines={fine.cost} />
            </div>
          ) : null}
          <div className={fine ? "space-y-1.5 px-2.5" : "space-y-1.5"}>
            {fine ? <div className="font-semibold text-fg">Or the preset “{w.preset_name}”</div> : null}
            <p className="text-muted">{w.intro}</p>
            <Lines title="What it would change" lines={w.gain} />
            <Lines title="What it costs" lines={w.cost} />
          </div>
          <p className="text-subtle">{w.measured}</p>
          <div className="flex flex-wrap items-center gap-2 pt-0.5">
            {choices.map((c) =>
              c.kind === "fine" ? (
                <Button key="fine" size="xs" icon={Layers} loading={busy === "fine"} disabled={busy !== null || !!blocked} title={blocked ?? undefined} onClick={() => void makeFine()}>
                  {c.label}
                </Button>
              ) : (
                <Button
                  key="preset"
                  size="xs"
                  icon={Wand}
                  variant={c.primary ? "primary" : "secondary"}
                  loading={busy === "preset"}
                  disabled={busy !== null || !!blocked}
                  title={blocked ?? undefined}
                  onClick={() => void makePreset()}
                >
                  {c.label}
                </Button>
              ),
            )}
            <Button size="xs" variant="ghost" disabled={busy !== null} loading={busy === "dismiss"} onClick={() => void dismiss()}>
              Not for this scan
            </Button>
          </div>
          <p className="text-subtle">
            {fine
              ? `“${fine.action}” makes the mesh again with it on for this scan; the preset's button sets this scan's preset to “${w.preset_name}” and makes the mesh again. `
              : `The button sets this scan's preset to “${w.preset_name}” and makes the mesh again; `}
            {fine ? "Either way the mesh you have stays until the new one is ready." : "the mesh you have stays until the new one is ready."} Nothing changes
            unless you press one.
          </p>
          {blocked ? <p className="text-subtle">{blocked}</p> : null}
        </div>
      </div>
    </section>
  );
}

/** In the hint's place after a Make mesh with “Make only the thin walls finer” on: what it did and one action, or why
 * it did not, in one line. Null when the scan did not choose it. */
export function FineWallsNote({
  pid,
  sid,
  name,
  made,
  quality,
  blocked,
}: {
  pid: string;
  sid: string;
  name: string;
  made: MadeInfo | null | undefined;
  quality?: unknown;
  blocked?: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const note = fineWallsNote(made);
  if (!note) return null;
  const q: ProcessQuality = normalizeQuality(quality) ?? "standard";
  const undo = async () => {
    setBusy(true);
    await useJobs.getState().process(pid, sid, q, name, false);
    setBusy(false);
  };
  return (
    <section className="rounded-lg border border-line bg-surface-2/50 px-3 py-2 text-xs" aria-label="Make only the thin walls finer" role="note">
      <div className="flex items-start gap-2.5">
        <Layers size={14} className="mt-px shrink-0 text-accent" aria-hidden />
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="text-fg">{note.line}</p>
          {note.action ? (
            <Button size="xs" variant="secondary" icon={Undo2} loading={busy} disabled={busy || !!blocked} title={blocked ?? undefined} onClick={() => void undo()}>
              {note.action}
            </Button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
