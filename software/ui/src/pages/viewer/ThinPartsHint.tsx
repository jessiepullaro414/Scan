/**
 * The thin-parts hint on the result page (ENGINE_PLAN X5 step 1): what Make mesh found that a finer mesh detail could
 * help (thin walls, a thin plate, small holes), what the preset “Thin sheet metal and small parts” would change, as
 * measured, and what it costs. A suggestion: one button sets that preset for this scan and makes the mesh again (the
 * Preset box's way: `PUT .../preset`, then Make mesh), and “Not for this scan” puts it away for good. Nothing happens
 * by itself.
 */
import { Lightbulb, Wand, X } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/http";
import type { ProcessQuality, ThinPartsHint as Hint } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { normalizeQuality } from "../../lib/quality";
import { useJobs } from "../../store/jobs";
import { usePresets } from "../../store/presets";
import { useProcess } from "../../store/process";
import { toast } from "../../store/toasts";

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
  const [busy, setBusy] = useState<"make" | "dismiss" | null>(null);
  const w = hint.words;
  if (!w) return null;
  const q: ProcessQuality = normalizeQuality(quality) ?? "standard";

  const make = async () => {
    setBusy("make");
    const set = await usePresets.getState().setScan(pid, sid, { preset_id: hint.preset_id });
    if (set) await useJobs.getState().process(pid, sid, q, name);
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
          <p className="text-muted">{w.intro}</p>
          <div>
            <div className="font-medium text-fg">What it would change</div>
            <ul className="space-y-0.5 text-muted">
              {w.gain.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
          <div>
            <div className="font-medium text-fg">What it costs</div>
            <ul className="space-y-0.5 text-muted">
              {w.cost.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
          <p className="text-subtle">{w.measured}</p>
          <div className="flex flex-wrap items-center gap-2 pt-0.5">
            <Button size="xs" icon={Wand} loading={busy === "make"} disabled={busy !== null || !!blocked} title={blocked ?? undefined} onClick={() => void make()}>
              {w.action}
            </Button>
            <Button size="xs" variant="ghost" disabled={busy !== null} loading={busy === "dismiss"} onClick={() => void dismiss()}>
              Not for this scan
            </Button>
          </div>
          <p className="text-subtle">
            The button sets this scan's preset to “{w.preset_name}” and makes the mesh again; the mesh you have stays until the new one is ready. Nothing
            changes unless you press it.
          </p>
          {blocked ? <p className="text-subtle">{blocked}</p> : null}
        </div>
      </div>
    </section>
  );
}
