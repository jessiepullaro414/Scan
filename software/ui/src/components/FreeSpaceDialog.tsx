/**
 * R2: free a scan's space (or every scan's of a project) on purpose. The dialog asks the engine what each
 * choice would remove and free (`GET .../keep?mode=`, R1's numbers), says what can no longer be done
 * afterwards, and for "the result only" asks once more, because that cannot be undone. R3 adds a choice
 * between the two: the recording thinned to the frames Standard quality uses (the engine's "engineering" with
 * `thin`), which also asks once more and says beforehand what Best quality then loses. Nothing is
 * removed before the user confirms. The words come from `lib/keepMode.ts`.
 */
import { HardDrive } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/http";
import type { Project, ProjectKeepPlan, Scan, ScanKeepPlan } from "../api/types";
import { fmtBytes } from "../lib/format";
import {
  KEEP_CHOICE,
  KEEP_CHOICES,
  choiceArgs,
  confirmStandard,
  confirmThin,
  doneLine,
  hintLine,
  keptLine,
  lossLines,
  planLine,
  projectDoneLines,
  projectPlanLines,
  removeLines,
  thinnedLine,
  writeLines,
  type KeepChoice,
} from "../lib/keepMode";
import { cn } from "../lib/util";
import { toast } from "../store/toasts";
import { Button } from "./ui/Button";
import { ConfirmDialog, Dialog } from "./ui/Dialog";

type Choice = KeepChoice;
type Plans = Partial<Record<Choice, ScanKeepPlan | ProjectKeepPlan | ApiError>>;

/** The choice the dialog opens on: the next lighter one than what the scan keeps now. */
function firstChoice(scan: Scan | null): Choice {
  if (scan?.keep !== "engineering") return "engineering";
  return scan.thinned ? "standard" : "thinned";
}

const isProjectPlan = (p: ScanKeepPlan | ProjectKeepPlan): p is ProjectKeepPlan => "scans" in p;

/** Whether the plan can be carried out and frees something, and what it frees (0 when it can't: UX_PLAN R11, the
 * footer must not offer "Free 196 MB" beside a choice whose own line says it cannot be done). */
function planState(p: ScanKeepPlan | ProjectKeepPlan | ApiError | undefined): { ok: boolean; frees: number } {
  if (!p || p instanceof ApiError) return { ok: false, frees: 0 };
  const ok = (isProjectPlan(p) ? p.n_scans > 0 : p.ok) && p.frees_bytes > 0;
  return { ok, frees: ok ? p.frees_bytes : 0 };
}

/** The choice to start on once every plan is known (R4, R11): the preferred one if it can apply, else the next that
 * can (lighter first, then heavier), else null (nothing applies: the focus goes to Cancel). */
function usableChoice(preferred: Choice, plans: Plans): Choice | null {
  const i = KEEP_CHOICES.indexOf(preferred);
  const order = [...KEEP_CHOICES.slice(i), ...KEEP_CHOICES.slice(0, i).reverse()];
  return order.find((c) => planState(plans[c]).ok) ?? null;
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
  // R4, R11: once every plan is known, start on a choice that can apply (unless the user picked one already) and put
  // the focus on it, or on Cancel when none can
  const touched = useRef(false);
  const settled = useRef(false);
  const group = useRef<HTMLDivElement>(null);
  const cancelBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open || settled.current || !KEEP_CHOICES.every((c) => plans[c])) return;
    settled.current = true;
    if (touched.current) return; // the user picked a choice already: theirs stays, and so does the focus
    const pick = usableChoice(mode, plans);
    if (pick && pick !== mode) setMode(pick);
    // only move a focus the user has not moved on: still on a choice, or nowhere in the dialog
    const dialog = group.current?.closest('[role="dialog"]');
    const focusNow = document.activeElement;
    if (dialog && focusNow && dialog.contains(focusNow) && focusNow !== dialog && !group.current?.contains(focusNow)) return;
    requestAnimationFrame(() => {
      if (pick) group.current?.querySelector<HTMLElement>(`[data-choice="${pick}"]`)?.focus();
      else cancelBtn.current?.focus();
    });
  }, [open, plans, mode]);

  useEffect(() => {
    if (!open) return;
    let live = true;
    touched.current = false;
    settled.current = false;
    setPlans({});
    setMode(firstChoice(scan));
    for (const m of KEEP_CHOICES) {
      const a = choiceArgs(m);
      const ask = sid ? api.keepPlan(project.id, sid, a.mode, a.thin) : api.projectKeepPlan(project.id, a.mode, a.thin);
      ask.then(
        (p) => live && setPlans((s) => ({ ...s, [m]: p })),
        (e) => live && setPlans((s) => ({ ...s, [m]: e instanceof ApiError ? e : new ApiError(0, String(e), null, "") })),
      );
    }
    return () => {
      live = false;
    };
  }, [open, project.id, sid, scan?.keep, scan?.thinned]);

  const plan = plans[mode];
  const { ok, frees } = planState(plan);
  const needsSecondYes = !!plan && !(plan instanceof ApiError) && plan.confirm;

  async function run() {
    setBusy(true);
    const a = choiceArgs(mode);
    const confirm = needsSecondYes; // asked once more already (it cannot be undone)
    try {
      if (sid) {
        const r = await api.keep(project.id, sid, a.mode, confirm, a.thin);
        toast.success(doneLine(r.freed_bytes, a.mode, scan?.name, a.thin));
      } else {
        const r = await api.projectKeep(project.id, a.mode, confirm, a.thin);
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

  const whose = sid ? { name: scan?.name } : { scans: plan && !(plan instanceof ApiError) && isProjectPlan(plan) ? plan.n_scans : 0 };
  const second = mode === "thinned" ? confirmThin(whose, frees) : confirmStandard(whose, frees);

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
            <Button ref={cancelBtn} variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant={needsSecondYes ? "danger" : "primary"}
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
        <div ref={group} role="radiogroup" aria-label="What to keep" className="space-y-2">
          {KEEP_CHOICES.map((m) => {
            const p = plans[m];
            const line = !p ? "Working out what this frees…" : p instanceof ApiError ? p.detail : isProjectPlan(p) ? projectPlanLines(p).text : planLine(p);
            const st = planState(p);
            return (
              <button
                key={m}
                type="button"
                role="radio"
                data-choice={m}
                data-autofocus={mode === m || undefined}
                aria-checked={mode === m}
                onClick={() => {
                  touched.current = true;
                  setMode(m);
                }}
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
    return plan.refusal?.hint ? <p className="mt-4 text-sm text-muted">{hintLine(plan.refusal.hint)}</p> : null;
  }
  const kept = keptLine(plan.keeps);
  const thin = thinnedLine(plan.thinned, true);
  return (
    <div className="mt-4 space-y-3 text-sm">
      {thin && plan.remove.some((p) => p.part === "frames") ? <p className="text-xs text-fg">{thin}</p> : null}
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
