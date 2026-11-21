/**
 * A second pass on the Scan page (FEATURE_PLAN N2):
 *
 * - `PassSetup` (side panel, before and while scanning): which scan the pass belongs to, the first scan as a
 *   reference picture with the areas lit (PassReference.tsx: beside the live view, not on it, because the two
 *   are not lined up until they are aligned after Stop), the places to scan from in words, the preset the pass
 *   takes, "the part was moved", and what a merge does and does not do.
 * - `PassChainCard` (after Stop, in place of the "Make mesh" card): the steps the engine runs on its own (make
 *   the pass's mesh, align it to the first scan, merge the two), the running one with its progress and Cancel,
 *   what happened in the engine's words, Continue after a stop, the Align page when the alignment was not
 *   accepted, and the before and after once merged.
 */
import { ArrowRightLeft, CircleCheck, CircleDashed, CircleExclamationPoint, Eye, Layers, Play, Plus, SkipForward, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/http";
import type { CheckPose, PassCompare, PassView, Scan } from "../../api/types";
import { JobProgress } from "../../components/ScanBits";
import { Button } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Feedback";
import { MovedQuestion } from "../../components/MovedQuestion";
import { cannotMerge } from "../../lib/align";
import {
  areasWords,
  chainActive,
  chainCanContinue,
  chainRows,
  chainTitle,
  compareRows,
  compareSentence,
  MERGE_NOTE,
  poseWords,
  REFERENCE_NOTE,
  areaLines,
  missingSentence,
  placesWords,
  setupWords,
  type PendingPass,
} from "../../lib/pass";
import { placeLetter } from "../../lib/checkWhy";
import { navigate, paths } from "../../lib/router";
import { cn } from "../../lib/util";
import { useJobs } from "../../store/jobs";
import { usePass } from "../../store/pass";
import { useProjects } from "../../store/projects";
import { useSession } from "../../store/session";
import { toast } from "../../store/toasts";
import { useLive } from "../../store/live";
import { PassReference } from "./PassReference";

const asPose = (p: unknown): CheckPose | null => {
  const q = p as Partial<CheckPose> | null;
  return q && Array.isArray(q.position_mm) && Array.isArray(q.look_dir) && Array.isArray(q.up) && Array.isArray(q.target_mm) && typeof q.distance_mm === "number"
    ? (q as CheckPose)
    : null;
};

/** The id of the pass's moved-part question (the Scan page's "Answer it" focuses it). */
export const PASS_MOVED_ID = "pass-moved-question";

/** The pass's setup: the first scan for reference, the areas and places, the preset, the part moved or not. */
export function PassSetup({ pending, live, firstScan, presetName }: { pending: PendingPass; live: boolean; firstScan: Scan | undefined; presetName: string }) {
  const moved = usePass((s) => s.moved);
  const setMoved = usePass((s) => s.setMoved);
  const clear = usePass((s) => s.clear);
  const poses = useMemo(() => pending.poses.map((p) => asPose(p.pose)).filter((p): p is CheckPose => !!p), [pending.poses]);
  const noMerge = firstScan ? cannotMergeAsFirst(firstScan) : null;
  return (
    <section className="border-b border-line px-4 py-3" aria-label="Second pass">
      <div className="flex items-start gap-2">
        <Layers size={15} className="mt-0.5 shrink-0 text-accent" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-fg">Second pass of “{pending.firstName}”</h2>
          <div className="text-[11.5px] leading-4 text-muted">{areasWords(pending.areas, pending.tolerance)}</div>
        </div>
        {!live ? (
          <Button
            size="xs"
            variant="ghost"
            icon={X}
            onClick={() => {
              clear();
              navigate(paths.scan(pending.projectId), { replace: true });
            }}
          >
            Make an ordinary scan instead
          </Button>
        ) : null}
      </div>
      <div className="mt-2">
        <PassReference
          meshUrl={pending.meshUrl}
          firstId={pending.firstId}
          faces={pending.areas.map((a) => a.faces)}
          poses={poses}
          label={`The first scan, “${pending.firstName}”, with the areas to scan lit`}
        />
        <div className="mt-1 text-[11px] leading-4 text-subtle">{REFERENCE_NOTE}</div>
      </div>
      {pending.poses.length ? (
        <ol className="mt-2 space-y-1 text-[11.5px] leading-4 text-muted" aria-label="Places to scan from">
          {pending.poses.map((p, i) => (
            <li key={i} className="flex gap-2">
              <span className="grid h-4 min-w-4 place-items-center rounded-full bg-surface-3 px-1 text-[10px] font-bold text-fg" aria-label={`Place ${placeLetter(i)}`}>
                {placeLetter(i)}
              </span>
              <span>{poseWords(p)}</span>
            </li>
          ))}
        </ol>
      ) : (
        <div className="mt-2 text-[11.5px] leading-4 text-subtle">{placesWords(pending)}</div>
      )}
      {live && moved !== null ? <div className="mt-2 text-[11.5px] leading-4 text-muted">{setupWords(!moved)}</div> : null}
      {!live ? (
        <>
          <div className="mt-3 text-[11.5px] leading-4 text-muted">
            Preset: <span className="font-semibold text-fg">{presetName}</span>
            {pending.preset ? " (the first scan's; change it below if this pass needs another)" : " (as the first scan)"}.
          </div>
          {/* U15: no default; Start waits for the answer */}
          <div className="mt-3">
            <MovedQuestion
              id={PASS_MOVED_ID}
              value={moved}
              onChange={setMoved}
              question="Did you move or turn the part since the first scan?"
              texts={{
                moved: "The table is not used to align the two scans, and each scan's table is left out of the merge.",
                same: "The table both scans share helps to align them, and it stays in the merge.",
              }}
            />
          </div>
        </>
      ) : null}
      {noMerge ? <div className="mt-2 rounded-md border border-warn/40 bg-warn-soft px-2.5 py-1.5 text-[11.5px] leading-4 text-fg">{noMerge}</div> : null}
      <div className="mt-2 text-[11.5px] leading-4 text-muted">After Stop the pass is made into a mesh, aligned to “{pending.firstName}” and merged with it into a new scan; “{pending.firstName}” itself is not changed.</div>
      <div className="mt-1.5 text-[11px] leading-4 text-subtle">{MERGE_NOTE}</div>
    </section>
  );
}

/** Why a pass to this scan could be aligned but not merged (as the engine says it), or null. */
function cannotMergeAsFirst(s: Scan): string | null {
  if (s.merged_from?.length) return null; // the engine merges again from the scans it was made from
  const r = cannotMerge(s, false);
  return r ? `${r.title}: the pass can be aligned to it, but not merged with it. ${r.text.split(". ")[0]}.` : null;
}

function StepIcon({ state }: { state: string }) {
  if (state === "done") return <CircleCheck size={14} className="text-ok" aria-hidden />;
  if (state === "running") return <Spinner size={13} className="text-accent" />;
  if (state === "stopped") return <CircleExclamationPoint size={14} className="text-warn" aria-hidden />;
  if (state === "skipped") return <SkipForward size={13} className="text-subtle" aria-hidden />;
  return <CircleDashed size={14} className="text-subtle" aria-hidden />;
}

/** Before and after, at the same tolerance. */
export function PassBeforeAfter({ cmp }: { cmp: PassCompare }) {
  const rows = compareRows(cmp);
  return (
    <div className="mt-3 rounded-lg border border-line bg-surface-2/60 px-3 py-2">
      <div className="text-xs font-semibold text-fg">Before and after, at ± {cmp.tolerance_mm}&#8239;mm</div>
      <table className="mt-1.5 w-full text-[11.5px]">
        <thead>
          <tr className="text-left text-subtle">
            <th className="font-normal" />
            <th className="font-normal">Scan</th>
            <th className="text-right font-normal">Meets the tolerance</th>
            <th className="text-right font-normal">Areas to scan</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td className="pr-2 font-semibold text-fg">{r.label}</td>
              <td className="max-w-[160px] truncate pr-2 text-muted" title={r.name}>
                {r.name}
              </td>
              <td className="text-right font-mono text-fg tnum">{r.share}</td>
              <td className="text-right font-mono text-fg tnum">{r.areas}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {cmp.whole_note ? <div className="mt-1 text-[11px] leading-4 text-subtle">{cmp.whole_note}</div> : null}
      <div className="mt-1.5 text-[11.5px] leading-4 text-fg">{compareSentence(cmp)}</div>
      {missingSentence(cmp) ? <div className="mt-1 text-[11.5px] leading-4 text-muted">{missingSentence(cmp)}</div> : null}
      {cmp.areas.length ? (
        <ul className="mt-1.5 space-y-0.5 text-[11.5px] leading-4 text-muted" aria-label="The areas asked for">
          {areaLines(cmp.areas).map((t, i) => (
            <li key={i} className={cn(t.includes("passes now") && "text-fg")}>
              {t}
            </li>
          ))}
        </ul>
      ) : null}
      {cmp.areas_note ? <div className="mt-1 text-[11px] text-subtle">{cmp.areas_note}</div> : null}
      <div className="mt-1.5 text-[11px] leading-4 text-subtle">{cmp.note || MERGE_NOTE}</div>
    </div>
  );
}

/** After Stop: the pass's steps, run by the engine on its own. */
export function PassChainCard({ projectId, scan }: { projectId: string; scan: Scan }) {
  const clear = useSession((s) => s.clearLastStopped);
  const [view, setView] = useState<PassView | null>(null);
  const [cmp, setCmp] = useState<PassCompare | null>(null);
  const [busy, setBusy] = useState(false);
  const state = view?.chain.state ?? scan.continues?.chain.state ?? "waiting";
  const jid = view?.chain.step ? view.chain.jobs[view.chain.step] : undefined;
  const liveJob = useJobs((s) => (jid ? s.jobs[jid] : undefined));
  const job = liveJob ?? view?.job ?? null;

  const refresh = useCallback(async () => {
    try {
      const v = await api.pass(projectId, scan.id);
      setView(v);
      return v;
    } catch {
      return null;
    }
  }, [projectId, scan.id]);

  // follow the chain while it runs (each step is an ordinary job; the chain moves on in the engine)
  useEffect(() => {
    let stop = false;
    let t: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      const v = await refresh();
      if (stop) return;
      if (!v || chainActive(v.chain.state)) t = setTimeout(tick, 900);
      else void useProjects.getState().fetchProject(projectId);
    };
    void tick();
    return () => {
      stop = true;
      if (t) clearTimeout(t);
    };
  }, [refresh, projectId, busy]);

  useEffect(() => {
    if (state !== "merged" || cmp) return;
    api.passCompare(projectId, scan.id).then(setCmp, (e: unknown) => toast.error("Couldn't compare before and after", e));
  }, [state, cmp, projectId, scan.id]);

  const go = async () => {
    setBusy(true);
    try {
      setView(await api.passRun(projectId, scan.id));
    } catch (e) {
      toast.error("Couldn't continue the pass", e);
    } finally {
      setBusy(false);
    }
  };
  const newScan = () => {
    clear();
    useLive.getState().resetScan();
    navigate(paths.scan(projectId), { replace: true }); // an ordinary scan next (a new pass starts from the Check panel)
  };
  const first = view?.first ?? (scan.continues ? { id: scan.continues.scan_id, name: scan.continues.scan_name } : null);
  const merged = view?.chain.merged_scan_id ?? null;
  const rows = chainRows(view?.chain ?? scan.continues?.chain ?? { state: "waiting", step: null });
  const bad = state === "failed" || state === "not_aligned" || state === "not_merged" || state === "cancelled";

  return (
    <div className="glass w-[600px] max-w-[calc(100%-2rem)] animate-pop-in rounded-2xl border border-line-strong p-4 shadow-lg" aria-live="polite">
      <div className="flex items-start gap-3">
        <div className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-full", bad ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok")}>
          {bad ? <CircleExclamationPoint size={18} aria-hidden /> : state === "merged" ? <CircleCheck size={18} aria-hidden /> : <Layers size={18} aria-hidden />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-fg">
            {chainTitle(state)}
            <span className="font-normal text-muted"> · {scan.name}{first ? ` → “${first.name}”` : ""}</span>
          </div>
          <div className="mt-0.5 text-xs text-muted">{view?.chain.message ?? "Starting the pass's steps…"}</div>
          {view?.setup_words ? <div className="mt-0.5 text-[11.5px] text-subtle">{view.setup_words}</div> : null}
        </div>
      </div>
      <ol className="mt-3 space-y-1" aria-label="The pass's steps">
        {rows.map((r) => (
          <li key={r.step} className="flex items-center gap-2 text-[12px]">
            <StepIcon state={r.state} />
            <span className={cn(r.state === "waiting" || r.state === "skipped" ? "text-subtle" : "text-fg")}>{r.label}</span>
          </li>
        ))}
      </ol>
      {job && (job.state === "running" || job.state === "queued") ? (
        <JobProgress job={job} className="mt-3" label="The pass's step" cancelTitle="Stop the pass's steps here. The pass is kept as its own scan." />
      ) : null}
      {cmp ? <PassBeforeAfter cmp={cmp} /> : state === "merged" ? <div className="mt-3 text-xs text-muted">Comparing before and after…</div> : null}
      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        {state === "merged" && merged ? (
          <Button variant="primary" icon={Eye} onClick={() => navigate(paths.viewer(projectId, merged))}>
            Open the merged scan
          </Button>
        ) : null}
        {chainCanContinue(state) && state !== "not_aligned" ? (
          <Button variant="primary" icon={Play} loading={busy} onClick={() => void go()}>
            Continue
          </Button>
        ) : null}
        {(state === "not_aligned" || state === "not_merged") && first ? (
          <Button variant={state === "not_aligned" ? "primary" : "secondary"} icon={ArrowRightLeft} onClick={() => navigate(paths.align(projectId, first.id, scan.id))}>
            Open the Align page
          </Button>
        ) : null}
        {state === "not_aligned" ? (
          <Button icon={Play} loading={busy} onClick={() => void go()} title="Align again from the shape (the same rule)">
            Try aligning again
          </Button>
        ) : null}
        {!chainActive(state) ? (
          <Button variant="ghost" icon={Eye} onClick={() => navigate(paths.viewer(projectId, scan.id))}>
            Open the pass
          </Button>
        ) : null}
        <div className="flex-1" />
        <Button icon={Plus} onClick={newScan}>
          New scan
        </Button>
      </div>
    </div>
  );
}
