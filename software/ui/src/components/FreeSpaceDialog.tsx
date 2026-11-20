/**
 * R2: free a scan's space (or every scan's of a project) on purpose. The dialog asks the engine what each
 * choice would remove and free (`GET .../keep?mode=`, R1's numbers), says what can no longer be done
 * afterwards, and for "the result only" asks once more, because that cannot be undone. Nothing is
 * removed before the user confirms. The words come from `lib/keepMode.ts`.
 */
import { HardDrive } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/http";
import type { KeepMode, Project, ProjectKeepPlan, Scan, ScanKeepPlan } from "../api/types";
import { fmtBytes } from "../lib/format";
import {
  KEEP_CHOICE,
  KEEP_CHOICES,
  confirmStandard,
  doneLine,
  keptLine,
  lossLines,
  planLine,
  projectDoneLines,
  projectPlanLines,
  removeLines,
  writeLines,
} from "../lib/keepMode";
import { cn } from "../lib/util";
import { toast } from "../store/toasts";
import { Button } from "./ui/Button";
import { ConfirmDialog, Dialog } from "./ui/Dialog";

type Choice = Exclude<KeepMode, "full">;
type Plans = Partial<Record<Choice, ScanKeepPlan | ProjectKeepPlan | ApiError>>;

const isProjectPlan = (p: ScanKeepPlan | ProjectKeepPlan): p is ProjectKeepPlan => "scans" in p;

/** Whether the plan can be carried out, and what it frees. */
function planState(p: ScanKeepPlan | ProjectKeepPlan | ApiError | undefined): { ok: boolean; frees: number } {
  if (!p || p instanceof ApiError) return { ok: false, frees: 0 };
  if (isProjectPlan(p)) return { ok: p.n_scans > 0, frees: p.frees_bytes };
  return { ok: p.ok, frees: p.frees_bytes };
}

export function FreeSpaceDialog({
  open,
  onClose,
  project,
  scan,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  project: Project;
  /** One scan, or null for every scan of the project. */
  scan: Scan | null;
  onDone: () => void;
}) {
  const [mode, setMode] = useState<Choice>("engineering");
  const [plans, setPlans] = useState<Plans>({});
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const sid = scan?.id ?? null;

  useEffect(() => {
    if (!open) return;
    let live = true;
    setPlans({});
    setMode(scan?.keep === "engineering" ? "standard" : "engineering");
    for (const m of KEEP_CHOICES) {
      const ask = sid ? api.keepPlan(project.id, sid, m) : api.projectKeepPlan(project.id, m);
      ask.then(
        (p) => live && setPlans((s) => ({ ...s, [m]: p })),
        (e) => live && setPlans((s) => ({ ...s, [m]: e instanceof ApiError ? e : new ApiError(0, String(e), null, "") })),
      );
    }
    return () => {
      live = false;
    };
  }, [open, project.id, sid, scan?.keep]);

  const plan = plans[mode];
  const { ok, frees } = planState(plan);
  const needsSecondYes = !!plan && !(plan instanceof ApiError) && plan.confirm;

  async function run() {
    setBusy(true);
    try {
      if (sid) {
        const r = await api.keep(project.id, sid, mode, mode === "standard");
        toast.success(doneLine(r.freed_bytes, mode, scan?.name));
      } else {
        const r = await api.projectKeep(project.id, mode, mode === "standard");
        const d = projectDoneLines(r);
        if (d.failed.length) toast.warn(d.text, `Left as they were: ${d.failed.join(" ")}`);
        else toast.success(d.text);
      }
      onClose();
    } catch (e) {
      toast.error("Couldn't free all of the space", e);
    } finally {
      setBusy(false);
      setAsking(false);
      onDone();
    }
  }

  const second = confirmStandard(sid ? { name: scan?.name } : { scans: plan && !(plan instanceof ApiError) && isProjectPlan(plan) ? plan.n_scans : 0 }, frees);

  return (
    <>
      <Dialog
        open={open && !asking}
        onClose={() => !busy && onClose()}
        title={sid ? `Free space: “${scan?.name}”` : `Free space in “${project.name}”`}
        description="Choose what to keep. Nothing is removed until you confirm."
        width={560}
        footer={
          <>
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              data-autofocus
              variant={mode === "standard" ? "danger" : "primary"}
              icon={HardDrive}
              loading={busy}
              disabled={!ok || !plan}
              onClick={() => (needsSecondYes ? setAsking(true) : void run())}
            >
              {frees > 0 ? `Free ${fmtBytes(frees)}` : "Free space"}
            </Button>
          </>
        }
      >
        <div role="radiogroup" aria-label="What to keep" className="space-y-2">
          {KEEP_CHOICES.map((m) => {
            const p = plans[m];
            const line = !p ? "Working out what this frees…" : p instanceof ApiError ? p.detail : isProjectPlan(p) ? projectPlanLines(p).text : planLine(p);
            const st = planState(p);
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={mode === m}
                onClick={() => setMode(m)}
                className={cn(
                  "block w-full rounded-lg border px-3 py-2.5 text-left transition-colors",
                  mode === m ? "border-accent bg-surface-2" : "border-line hover:bg-surface-2",
                )}
              >
                <div className="flex items-center gap-2 text-sm font-medium text-fg">
                  <span aria-hidden className={cn("grid h-3.5 w-3.5 place-items-center rounded-full border", mode === m ? "border-accent" : "border-line-strong")}>
                    {mode === m ? <span className="h-1.5 w-1.5 rounded-full bg-accent" /> : null}
                  </span>
                  {KEEP_CHOICE[m].title}
                </div>
                <div className="mt-1 text-xs text-muted">{KEEP_CHOICE[m].text}</div>
                <div className={cn("mt-1 text-xs font-medium tnum", st.ok ? "text-fg" : "text-subtle")}>{line}</div>
              </button>
            );
          })}
        </div>
        {plan && !(plan instanceof ApiError) ? <PlanDetails plan={plan} /> : null}
      </Dialog>
      <ConfirmDialog open={asking} onClose={() => !busy && setAsking(false)} title={second.title} confirmLabel={second.button} tone="danger" onConfirm={run}>
        {second.text}
      </ConfirmDialog>
    </>
  );
}

function PlanDetails({ plan }: { plan: ScanKeepPlan | ProjectKeepPlan }) {
  if (isProjectPlan(plan)) {
    const { skipped } = projectPlanLines(plan);
    const moving = plan.scans.filter((s) => s.ok && s.frees_bytes > 0);
    const losses = [...new Set(moving.flatMap((s) => s.loses))];
    return (
      <div className="mt-4 space-y-3 text-sm">
        {moving.length ? (
          <Section title="Scans it frees">
            {moving.map((s) => (
              <li key={s.scan_id} className="tnum">
                {s.name}: {fmtBytes(s.frees_bytes)}
              </li>
            ))}
          </Section>
        ) : null}
        {losses.length ? <Section title="Afterwards, for these scans, you can no longer">{lossLines(losses).map((l) => <li key={l}>{l}</li>)}</Section> : null}
        {skipped.length ? <Section title="Left as they are">{skipped.map((l) => <li key={l}>{l}</li>)}</Section> : null}
      </div>
    );
  }
  if (!plan.ok) {
    return plan.refusal?.hint ? <p className="mt-4 text-sm text-muted">{plan.refusal.hint[0].toUpperCase() + plan.refusal.hint.slice(1)}.</p> : null;
  }
  const kept = keptLine(plan.keeps);
  return (
    <div className="mt-4 space-y-3 text-sm">
      {plan.remove.length ? (
        <Section title="What is removed">
          {[...removeLines(plan), ...writeLines(plan)].map((l) => (
            <li key={l} className="tnum">
              {l}
            </li>
          ))}
        </Section>
      ) : null}
      {plan.loses.length ? <Section title="Afterwards you can no longer">{lossLines(plan.loses).map((l) => <li key={l}>{l}</li>)}</Section> : null}
      {kept ? <p className="text-xs text-muted">{kept}</p> : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-xs font-semibold text-fg">{title}</div>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-muted">{children}</ul>
    </div>
  );
}
