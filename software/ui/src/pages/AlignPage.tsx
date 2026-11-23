/**
 * Align scans and merge them (APP_API.md "M4 first slice" for two scans, "M4 second slice" for up to
 * eight). Processed scans of the same part: the first stays in place, the others are moved onto it. The
 * alignment runs from the shape alone or from three or more matching points clicked on two meshes at a
 * time; its verdict and figures are shown, the scans can be looked at together where it put them, and a
 * merge makes a new scan of them.
 *
 * When the engine is not sure (MAINTENANCE_PLAN F11: a pose turned round fits nearly as well, the guesses
 * disagree, the shapes contradict the picked points), the page says so in the engine's words, shows the
 * poses it had in mind to look at laid over the first scan and to choose, and leads to picking points. No
 * merge is offered on such a pose unless the person chose it or picked the points (lib/alignChoice.ts).
 *
 * With exactly two scans the page is the first slice's: `align` and the merge's target / source form.
 * With three or more it calls `align_all` and the merge's `scans` form, shows where each scan was placed,
 * and picks points between any two of the listed scans.
 *
 * The state is in the align store (store/align.ts); the 3D views in align/AlignCanvas.tsx.
 */
import {
  ArrowLeft,
  ArrowUpDown,
  CircleCheck,
  CircleExclamationPoint,
  Columns2,
  Combine,
  Eye,
  FolderOpen,
  Layers,
  Layers2,
  MapPin,
  Move3d,
  Plus,
  RefreshCw,
  Trash,
  TriangleExclamationPoint,
  Wand,
  X,
} from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, sentence } from "../api/http";
import type { AlignAllResult, AlignResult, MergeResult, Scan } from "../api/types";
import { JobProgress } from "../components/ScanBits";
import { ErrorBoundary } from "../components/shell/ErrorBoundary";
import { Button, IconButton } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/Dialog";
import { EmptyState, ProgressBar, Spinner } from "../components/ui/Feedback";
import { Field, Segmented, Select, TextInput } from "../components/ui/Form";
import { Callout, KeyValue } from "../components/ui/Layout";
import {
  alignAllFigures,
  alignFigures,
  allCautionOf,
  cautionOf,
  chooseAt,
  chooseList,
  completePairs,
  flattenPose,
  cannotMerge,
  hasMesh,
  MAX_SCANS,
  mergeLeftOut,
  mergeNameOf,
  mergeNeedsConfirm,
  MIN_PAIRS,
  SETUP_TEXT,
  namesText,
  needsALook,
  nextFreeScan,
  pairKey,
  pairLine,
  pairRows,
  pairsDisagree,
  pairsOf,
  pickedPoints,
  pickHint,
  pickPairOf,
  picksFor,
  poseOf,
  recordingMark,
  removeAt,
  sameList,
  scanColor,
  scanPlacements,
  suggestPickPair,
  unfinishedPairs,
  type Figure,
  type Picks,
  type Side,
  type Vec3,
} from "../lib/align";
import { choiceText, mergeOffer, poseChoices, poseInUse, standingLabel, standingOf } from "../lib/alignChoice";
import { freeName, nameTaken } from "../lib/names";
import { fmtBytes } from "../lib/format";
import { movedNeeded } from "../lib/moved";
import { focusMovedQuestion, MovedQuestion } from "../components/MovedQuestion";
import { href, navigate, paths } from "../lib/router";
import { cn } from "../lib/util";
import { useAlign, type Problem } from "../store/align";
import { isActive, useJobs } from "../store/jobs";
import { useProjects } from "../store/projects";
import { PickView, TogetherView, type PlacedMesh } from "./align/AlignCanvas";
import { useScanMeshes, type MeshLoad } from "./align/useScanMeshes";

type View = "pick" | "together";

/** What each scan does, in the words of the page: two scans, and the list of several. */
const ROLE: Record<Side, string> = { target: "Stays in place", source: "Moved onto it" };
const roleOf = (index: number): string => (index === 0 ? ROLE.target : ROLE.source);

/** U15: what each answer to the moved-part question does (no answer is chosen first). */
const MOVED_TEXT = { two: { moved: SETUP_TEXT.moved, same: "The table both scans share helps to line them up, and it stays in the merged scan." }, several: { moved: SETUP_TEXT.moved, same: SETUP_TEXT.same } };
const ALIGN_MOVED_ID = "align-moved-question";

/**
 * What the merge left out of each scan's table (the part was moved), what its cleaning took (R7: always said), and what
 * to look at. Its lines also say what it kept (a raised area that may be a thin part), so the title fits both.
 */
function LeftOut({ merged }: { merged: MergeResult }) {
  const { lines, warnings, warn } = mergeLeftOut(merged);
  if (!lines.length && !warnings.length) return null;
  return (
    <Callout tone={warn ? "warn" : "neutral"} icon={warn ? TriangleExclamationPoint : Layers} title={warn ? "Notes from the merge, and what to look at" : "Notes from the merge"} className="mt-3">
      <ul className="space-y-1.5" aria-label="What the merge left out and kept">
        {lines.map((l) => (
          <li key={l} className="leading-5">
            {l}
          </li>
        ))}
      </ul>
      {warnings.length ? (
        <ul className="mt-2 space-y-1.5" aria-label="Warnings">
          {warnings.map((w) => (
            <li key={w} className="flex items-start gap-1.5 leading-5 text-warn">
              <TriangleExclamationPoint size={13} className="mt-1 shrink-0" aria-hidden />
              <span>{w}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Callout>
  );
}

const EMPTY_PICKS: Picks = { target: [], source: [] };
const NO_SCANS: string[] = [];

function Panel({ title, icon: Icon, children, aside }: { title: string; icon: typeof Layers; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-b border-line px-4 py-4" aria-label={title}>
      <div className="mb-3 flex items-center gap-2">
        <Icon size={14} className="text-muted" aria-hidden />
        <h2 className="flex-1 text-xs font-semibold uppercase tracking-wider text-muted">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** An error as the API gave it: what happened, then what to do. */
function ProblemCallout({ problem, className }: { problem: Problem; className?: string }) {
  return (
    <Callout tone="danger" icon={CircleExclamationPoint} title={problem.detail} className={className}>
      {problem.hint}
    </Callout>
  );
}

const ColorDot = ({ color, hollow }: { color: string; hollow?: boolean }) => (
  <span aria-hidden className="inline-block h-2.5 w-2.5 shrink-0 rounded-full border" style={{ borderColor: color, background: hollow ? "transparent" : color }} />
);

/** One scan's mesh in its own view (or how far its download is), with its role and name. */
const MeshPane = memo(function MeshPane({
  side,
  role,
  color,
  scan,
  load,
  picks,
  enabled,
  onPick,
  onRetry,
  className,
}: {
  side: Side;
  role: string;
  color: string;
  scan: Pick<Scan, "id" | "name"> | null;
  load: MeshLoad | undefined;
  picks: Vec3[];
  enabled: boolean;
  onPick: (side: Side, p: Vec3) => void;
  onRetry: (id: string) => void;
  className?: string;
}) {
  const pick = useCallback((p: Vec3) => onPick(side, p), [onPick, side]);
  return (
    <div className={cn("relative min-w-0 overflow-hidden", className)}>
      {scan && load?.phase === "ready" ? (
        <ErrorBoundary title="The 3D view couldn't start">
          <PickView
            geometry={load.geometry}
            picks={picks}
            color={color}
            label={`3D view of “${scan.name}” (${role.toLowerCase()}): click the part to pick a point`}
            enabled={enabled}
            onPick={pick}
          />
        </ErrorBoundary>
      ) : (
        <div className="absolute inset-0 grid place-items-center p-6">
          {!scan ? (
            <div className="text-sm text-muted">Choose a scan on the right.</div>
          ) : load?.phase === "error" ? (
            <MeshError scan={scan} load={load} onRetry={onRetry} />
          ) : (
            <MeshLoading scan={scan} load={load} />
          )}
        </div>
      )}
      {scan ? (
        <div className="pointer-events-none absolute bottom-4 left-4 right-28">
          <div className="glass inline-flex max-w-full items-center gap-2 rounded-xl border border-line-strong px-3 py-1.5 text-xs shadow-md">
            <ColorDot color={color} />
            <span className="shrink-0 text-muted">{role}</span>
            <span className="min-w-0 truncate font-medium text-fg">{scan.name}</span>
            {picks.length ? (
              <span className="shrink-0 text-subtle tnum">
                · {picks.length} point{picks.length === 1 ? "" : "s"}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
});

function MeshError({ scan, load, onRetry }: { scan: Pick<Scan, "id" | "name">; load: Extract<MeshLoad, { phase: "error" }>; onRetry: (id: string) => void }) {
  return (
    <EmptyState
      compact
      tone="danger"
      icon={CircleExclamationPoint}
      title={`Couldn't open the mesh of “${scan.name}”`}
      actions={
        <Button size="sm" variant="primary" icon={RefreshCw} onClick={() => onRetry(scan.id)}>
          Try again
        </Button>
      }
    >
      {load.detail}
      {load.hint ? <div className="mt-1 text-subtle">{load.hint}</div> : null}
    </EmptyState>
  );
}

function MeshLoading({ scan, load }: { scan: Pick<Scan, "id" | "name">; load: MeshLoad | undefined }) {
  return (
    <div className="w-[240px] text-center">
      <div className="mb-3 flex items-center justify-center gap-2 text-sm text-fg">
        <Spinner size={16} className="text-accent" />
        Loading the mesh…
      </div>
      <ProgressBar value={load?.phase === "loading" && load.total ? load.loaded / load.total : null} label={`Loading the mesh of ${scan.name}`} />
      <div className="mt-2 font-mono text-xs text-subtle tnum">
        {load?.phase === "loading" && load.loaded ? `${fmtBytes(load.loaded)}${load.total ? ` of ${fmtBytes(load.total)}` : ""}` : " "}
      </div>
    </div>
  );
}

const Together = memo(TogetherView);

function Figures({ figures }: { figures: Figure[] }) {
  return (
    <KeyValue
      dense
      className="mt-3"
      rows={figures.map((f) => [
        f.label,
        <span className="tnum">
          <span className={cn(f.warn && "font-medium text-warn")}>{f.value}</span>
          {f.note ? <span className="text-xs text-subtle"> {f.note}</span> : null}
        </span>,
      ])}
    />
  );
}

/**
 * The verdict sentence, accepted or not, the caution when the scans share little (a caution, not an error: Merge
 * stays possible), and the small list of figures.
 */
function Verdict({ ok, verdict, okLabel, notLabel, figures, caution }: { ok: boolean; verdict: string; okLabel: string; notLabel: string; figures: Figure[]; caution?: string | null }) {
  return (
    <div className="mt-3">
      <div className={cn("flex items-start gap-2.5 rounded-lg border px-3.5 py-3", ok ? "border-ok/30 bg-ok-soft/60" : "border-warn/30 bg-warn-soft/60")} role="status">
        {ok ? <CircleCheck size={19} className="mt-0.5 shrink-0 text-ok" aria-hidden /> : <TriangleExclamationPoint size={19} className="mt-0.5 shrink-0 text-warn" aria-hidden />}
        <div className="min-w-0">
          <div className={cn("text-[11px] font-semibold uppercase tracking-wider", ok ? "text-ok" : "text-warn")}>{ok ? okLabel : notLabel}</div>
          <p className="mt-0.5 text-sm font-medium leading-5 text-fg">{sentence(verdict)}</p>
        </div>
      </div>
      {caution ? (
        <Callout tone="warn" icon={TriangleExclamationPoint} title="The scans share little" className="mt-2">
          <span className="leading-5">{caution}</span>
        </Callout>
      ) : null}
      <Figures figures={figures} />
    </div>
  );
}

const TwoVerdict = ({ result }: { result: AlignResult }) => (
  <Verdict ok={result.accepted} verdict={result.verdict} okLabel="Aligned" notLabel={standingLabel(result)} figures={alignFigures(result)} caution={cautionOf(result)} />
);

/**
 * The engine was not sure (MAINTENANCE_PLAN F11): the poses it had in mind, each to look at laid over the
 * first scan and to choose. The choice is the person's, and is said as theirs.
 */
function PoseChooser({
  result,
  looking,
  chosen,
  disabled,
  onLook,
  onChoose,
}: {
  result: AlignResult;
  looking: number | null;
  chosen: number | null;
  disabled: boolean;
  onLook: (index: number) => void;
  onChoose: (index: number | null) => void;
}) {
  const choices = poseChoices(result);
  const shown = looking ?? chosen ?? 0;
  const mine = choiceText(result, chosen);
  return (
    <div className="mt-3 rounded-lg border border-warn/30 bg-surface-2/50 px-3 py-2.5">
      <div className="text-xs font-semibold text-fg">The poses the engine had in mind</div>
      <p className="mt-0.5 text-xs leading-5 text-muted">
        Look at each one laid over the first scan. Where the two scans sit on each other, with the colours finely mixed, choose it. If none fits, pick matching points.
      </p>
      <ul className="mt-2 space-y-1.5" aria-label="The poses the engine had in mind">
        {choices.map((c) => (
          <li key={c.index} className={cn("rounded-md border px-2.5 py-2 text-xs", shown === c.index ? "border-accent bg-accent-soft/40" : "border-line")}>
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 font-medium text-fg">{c.title}</span>
              {chosen === c.index ? <span className="shrink-0 font-medium text-ok">Your choice</span> : null}
            </div>
            <div className="mt-0.5 leading-5 text-subtle">{c.detail}</div>
            <div className="mt-1.5 flex gap-1.5">
              <Button size="xs" icon={Eye} disabled={disabled} onClick={() => onLook(c.index)} title="Show this pose with both scans together">
                Look
              </Button>
              {chosen === c.index ? (
                <Button size="xs" variant="ghost" disabled={disabled} onClick={() => onChoose(null)}>
                  Undo the choice
                </Button>
              ) : (
                <Button size="xs" variant="secondary" icon={CircleCheck} disabled={disabled} onClick={() => onChoose(c.index)} title="Use this pose for the merge">
                  Choose this pose
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {mine ? <p className="mt-2 text-xs font-medium leading-5 text-ok">{mine}</p> : null}
    </div>
  );
}

/** Several scans: the verdict, then each scan placed or not, with a short line for each of its pairs. */
function MultiVerdict({ result, list, nameOf, colorOf }: { result: AlignAllResult; list: string[]; nameOf: (id: string) => string; colorOf: (id: string) => string }) {
  const rows = scanPlacements(result, list);
  return (
    <>
      <Verdict ok={result.accepted} verdict={result.verdict} okLabel="All placed" notLabel="Not all placed" figures={alignAllFigures(result)} caution={allCautionOf(result)} />
      <ul className="mt-3 space-y-1.5" aria-label="Where each scan was placed">
        {rows.map((r) => (
          <li key={r.id} className="rounded-lg border border-line bg-surface-2/50 px-2.5 py-2 text-xs">
            <div className="flex items-center gap-2">
              <ColorDot color={colorOf(r.id)} hollow={!r.placed} />
              <span className="min-w-0 flex-1 truncate font-medium text-fg">{nameOf(r.id)}</span>
              <span className={cn("shrink-0", r.index === 0 ? "text-muted" : r.placed ? "text-ok" : "font-medium text-warn")}>
                {r.index === 0 ? "Stays in place" : r.placed ? "Placed" : "Not placed"}
              </span>
            </div>
            {r.pairs.length ? (
              <ul className="mt-1 space-y-0.5 pl-[18px]">
                {r.pairs.map(({ pair, other }) => (
                  <li key={other} className={cn("leading-5", pair.accepted ? "text-muted" : "text-subtle")} title={sentence(pair.verdict)}>
                    {pairLine(pair, nameOf(other))}
                    {pairsDisagree({ pairs_rms_mm: pair.pairs_rms_mm ?? null }) ? (
                      <span className="text-warn"> · your points disagree by {pair.pairs_rms_mm?.toFixed(1)}&#8239;mm</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
    </>
  );
}

export default function AlignPage({
  pid,
  target: wantTarget,
  source: wantSource,
  more: wantMore = NO_SCANS,
}: {
  pid: string;
  target: string | null;
  source: string | null;
  more?: string[];
}) {
  const project = useProjects((s) => s.details[pid]);
  const projectError = useProjects((s) => s.detailError[pid]);
  const projectLoading = useProjects((s) => !!s.detailLoading[pid]);
  const fetchProject = useProjects((s) => s.fetchProject);
  const st = useAlign();
  /** The store holds this project's choices (after `open`; before, another project's or none). */
  const mine = st.pid === pid;

  const [view, setView] = useState<View>("pick");
  /** The typed name of the merged scan; null: the default, which follows the scans. */
  const [name, setName] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const wantMoreKey = wantMore.join(",");

  useEffect(() => {
    void fetchProject(pid);
  }, [pid, fetchProject]);

  useEffect(() => {
    useAlign.getState().open(pid, wantTarget, wantSource, wantMoreKey ? wantMoreKey.split(",") : []);
  }, [pid, wantTarget, wantSource, wantMoreKey]);

  useEffect(() => () => useAlign.getState().leave(), []);

  // ---------------------------------------------------------------- the scans
  const scans = useMemo(() => (project ? project.scans.filter(hasMesh) : []), [project]);
  const ids = useMemo(() => scans.map((s) => s.id), [scans]);
  const list = mine ? st.scans : NO_SCANS;
  const listKey = list.join(",");
  const several = list.length > 2;
  /** The listed scans, each once and with a mesh (null where the list still names one it should not). */
  const listScans = useMemo(() => list.map((id, i) => (list.indexOf(id) === i ? (scans.find((s) => s.id === id) ?? null) : null)), [list, scans]);
  const ready = list.length >= 2 && listScans.every((s) => !!s);
  // U15: aligning waits for the answer to the moved-part question
  const movedWait = movedNeeded(st.sameSetup === null ? null : !st.sameSetup, "align");
  const nameOf = useCallback((id: string) => scans.find((s) => s.id === id)?.name ?? "a scan", [scans]);
  const colorOf = useCallback((id: string) => scanColor(Math.max(0, list.indexOf(id))), [list]);

  // Scans the project does not have (an old address), or none chosen yet: the first two with a mesh.
  useEffect(() => {
    if (!project || !mine) return;
    const c = chooseList(ids, list[0], list[1], list.slice(2));
    if (!sameList(c, list)) useAlign.getState().setScans(c);
  }, [project, mine, ids, list]);

  // The address names the scans, so a reload comes back to them.
  useEffect(() => {
    if (!mine || !ready) return;
    const more = list.slice(2).join(",");
    if (list[0] !== wantTarget || list[1] !== wantSource || more !== wantMoreKey) navigate(paths.align(pid, list[0], list[1], list.slice(2)), { replace: true });
  }, [mine, ready, pid, list, wantTarget, wantSource, wantMoreKey]);

  useEffect(() => setName(null), [listKey]);

  const wanted = useMemo(
    () => listScans.filter((s): s is Scan => !!s).map((s) => ({ id: s.id, url: s.result?.mesh_url || api.fileUrl(pid, s.id, "mesh.ply") })),
    [pid, listScans],
  );
  const { loads, retry } = useScanMeshes(wanted);

  // ---------------------------------------------------------------- jobs and the result
  const alignJob = useJobs((s) => (mine && st.alignJobId ? s.jobs[st.alignJobId] : undefined));
  const mergeJob = useJobs((s) => (mine && st.mergeJobId ? s.jobs[st.mergeJobId] : undefined));
  const aligning = mine && (st.alignStarting || isActive(alignJob));
  const merging = mine && (st.mergeStarting || isActive(mergeJob));
  const busy = aligning || merging;
  const result = mine && !several ? st.result : null;
  const multi = mine && several && st.multi && sameList(st.multi.scans, list) ? st.multi : null;
  const merged = mine ? st.merged : null;
  /** Two scans, the engine not sure: the pose being looked at (null: the chosen one, else the engine's). */
  const [looking, setLooking] = useState<number | null>(null);
  useEffect(() => setLooking(null), [result]);
  const chosenPose = mine ? st.chosenPose : null;
  const pose = useMemo(() => (result ? flattenPose(poseInUse(result, looking ?? chosenPose)) : null), [result, looking, chosenPose]);
  const hasAnswer = !!result || !!multi;

  // A new result: look at the scans together. No result (other scans, another setting): back to picking.
  useEffect(() => setView(hasAnswer ? "together" : "pick"), [result, multi, hasAnswer]);

  /**
   * What "Both together" shows: the first scan and each placed one at its pose (`items`, null while one
   * of them is still loading or failed: `waiting` names those). Unplaced scans are not drawn.
   */
  const { items, waiting } = useMemo(() => {
    const geometryOf = (id: string) => {
      const l = loads[id];
      return l?.phase === "ready" ? l.geometry : null;
    };
    if (result && pose && list.length === 2) {
      const g0 = geometryOf(list[0]),
        g1 = geometryOf(list[1]);
      const ready2: PlacedMesh[] | null =
        g0 && g1
          ? [
              { id: list[0], geometry: g0, pose: null, color: scanColor(0) },
              { id: list[1], geometry: g1, pose, color: scanColor(1) },
            ]
          : null;
      return { items: ready2, waiting: [] as string[] };
    }
    if (multi) {
      const shown = list.map((id, i) => ({ id, i, pose: i === 0 ? null : poseOf(multi, id) })).filter((x) => x.i === 0 || !!x.pose);
      const missing = shown.filter((x) => !geometryOf(x.id)).map((x) => x.id);
      if (missing.length) return { items: null, waiting: missing };
      return { items: shown.map((x): PlacedMesh => ({ id: x.id, geometry: geometryOf(x.id)!, pose: x.pose, color: scanColor(x.i) })), waiting: [] as string[] };
    }
    return { items: null, waiting: [] as string[] };
  }, [result, pose, multi, list, loads]);

  // ---------------------------------------------------------------- picking: two scans at a time
  const pair = pickPairOf(list, mine ? st.pickPair : null);
  const leftScan = pair ? (scans.find((s) => s.id === pair[0]) ?? null) : null;
  const rightScan = pair ? (scans.find((s) => s.id === pair[1]) ?? null) : null;
  const picks = mine && pair ? picksFor(st.picked, pair[0], pair[1]) : EMPTY_PICKS;
  const rows = pairRows(picks);
  const nPairs = completePairs(picks).length;
  const toSend = mine && several ? pickedPoints(st.picked, list) : [];
  const unfinished = mine && several ? unfinishedPairs(st.picked, list) : [];
  const canAlignFromPoints = several ? toSend.length > 0 : nPairs >= MIN_PAIRS;
  const pickNames = { target: leftScan?.name ?? "the first scan", source: rightScan?.name ?? "the second scan" };

  // ---------------------------------------------------------------- merging
  // UX_PLAN R21: no two scans of a project share a name: the default takes the next free one, as the engine's does,
  // and a typed name another scan has is warned about with the free one as a button (the app warns, it does not block)
  const takenNames = (project?.scans ?? []).map((s) => s.name);
  const defaultName = ready ? freeName(mergeNameOf(listScans.map((s) => s!.name)), takenNames) : "";
  const mergeName = name ?? defaultName;
  const nameClash = nameTaken(mergeName.trim(), takenNames) ? freeName(mergeName.trim(), takenNames) : null;
  /** A merged scan, or one that keeps its result only (R2), has no recording: it can be aligned to, not merged. */
  const noRecording = listScans.find((s) => s && cannotMerge(s, several)) ?? null;
  const noMerge = noRecording ? cannotMerge(noRecording, several) : null;
  const unplacedNames = multi ? list.filter((id) => multi.unplaced.includes(id)).map(nameOf) : [];
  /** Two scans: whether a merge is offered at all (not on a pose the engine was not sure of, unless chosen or picked). */
  const offer = result ? mergeOffer(result, chosenPose) : null;

  const onPick = useCallback((side: Side, p: Vec3) => useAlign.getState().pick(side, p), []);
  const choose = (index: number, id: string) => st.setScans(chooseAt(list, index, id));
  const free = nextFreeScan(ids, list);
  const addScan = () => {
    if (free) st.setScans([...list, free]);
  };
  const pickOn = (p: [string, string] | null) => {
    if (p) st.setPickPair(p[0], p[1]);
    setView("pick");
  };

  // ---------------------------------------------------------------- nothing to align
  if (projectError && !project) {
    const notFound = projectError.status === 404;
    return (
      <div className="grid h-full place-items-center p-6">
        <EmptyState
          tone={notFound ? "neutral" : "danger"}
          icon={notFound ? FolderOpen : CircleExclamationPoint}
          title={notFound ? "This project doesn't exist any more" : "Couldn't load the project"}
          actions={
            <>
              <Button icon={ArrowLeft} onClick={() => navigate(paths.home())}>
                All projects
              </Button>
              {!notFound ? (
                <Button variant="primary" icon={RefreshCw} loading={projectLoading} onClick={() => void fetchProject(pid)}>
                  Try again
                </Button>
              ) : null}
            </>
          }
        >
          {notFound ? "It may have been moved to the trash, or renamed outside Studio." : projectError.detail}
          {!notFound && projectError.hint ? <div className="mt-1 text-subtle">{projectError.hint}</div> : null}
        </EmptyState>
      </div>
    );
  }
  if (!project || !mine) {
    return (
      <div className="grid h-full place-items-center viewport-bg">
        <div className="flex items-center gap-2.5 text-sm text-muted">
          <Spinner size={16} /> Loading the project…
        </div>
      </div>
    );
  }
  if (scans.length < 2) {
    return (
      <div className="grid h-full place-items-center p-6">
        <EmptyState
          icon={Combine}
          title="Two scans with a mesh are needed"
          actions={
            <Button variant="primary" icon={ArrowLeft} onClick={() => navigate(paths.project(pid))}>
              Back to the project
            </Button>
          }
        >
          {scans.length === 1 ? `Only “${scans[0].name}” has a mesh so far.` : "No scan of this project has a mesh yet."} Scan the part a second time (or make the
          mesh of another scan) on the project page, then come back.
        </EmptyState>
      </div>
    );
  }

  // ---------------------------------------------------------------- what "Both together" shows
  /** The scans in one view: needs a result's poses and their meshes (until then each scan's own view shows how far it is). */
  const together = view === "together" && !!items;
  const togetherWaiting = view === "together" && !items && waiting.length > 0;
  const frameKey = items ? items.map((it) => `${it.geometry.uuid}:${it.pose?.join(",") ?? ""}`).join("/") : "";
  const title = "Align and merge scans";

  return (
    <div className="flex h-full">
      <div className="relative min-w-0 flex-1 overflow-hidden viewport-bg">
        {/* The two views stay mounted (hidden) while the scans are shown together: coming back to pick costs nothing.
            Keyed by scan: swapping the two moves each view to the other side with its camera and its mesh. */}
        <div className={cn("absolute inset-0 grid grid-cols-2", (together || togetherWaiting) && "pointer-events-none invisible")} aria-hidden={together || togetherWaiting || undefined}>
          {(["target", "source"] as const).map((side, i) => {
            const scan = side === "target" ? leftScan : rightScan;
            return (
              <MeshPane
                key={scan?.id ?? side}
                side={side}
                role={roleOf(scan ? list.indexOf(scan.id) : i)}
                color={scan ? colorOf(scan.id) : scanColor(i)}
                scan={scan}
                load={scan ? loads[scan.id] : undefined}
                picks={picks[side]}
                enabled={!busy && !together}
                onPick={onPick}
                onRetry={retry}
                className={i ? "border-l border-line" : undefined}
              />
            );
          })}
        </div>
        {together && items ? (
          <ErrorBoundary title="The 3D view couldn't start">
            <Together
              items={items}
              label={
                several
                  ? `3D view of the placed scans together: ${namesText(items.slice(1).map((it) => nameOf(it.id)))} moved onto “${nameOf(list[0])}”`
                  : `3D view of both scans together: “${nameOf(list[1])}” moved onto “${nameOf(list[0])}”`
              }
              frameKey={frameKey}
            />
          </ErrorBoundary>
        ) : null}
        {togetherWaiting ? (
          <div className="absolute inset-0 grid place-items-center p-6">
            {(() => {
              const failed = waiting.find((id) => loads[id]?.phase === "error");
              const s = scans.find((x) => x.id === (failed ?? waiting[0]));
              if (!s) return null;
              const l = loads[s.id];
              return l?.phase === "error" ? <MeshError scan={s} load={l} onRetry={retry} /> : <MeshLoading scan={s} load={l} />;
            })()}
          </div>
        ) : null}

        {/* breadcrumb and the choice of view */}
        <div className="pointer-events-none absolute left-4 top-4 flex flex-col items-start gap-2">
          <div className="glass pointer-events-auto flex h-9 items-center gap-1.5 rounded-xl border border-line-strong pl-1.5 pr-3 text-sm shadow-md">
            <IconButton icon={ArrowLeft} size="sm" label="Back to the project" onClick={() => navigate(paths.project(pid))} />
            <a href={href(paths.project(pid))} className="max-w-[220px] truncate text-muted hover:text-fg">
              {project.name}
            </a>
            <span className="text-subtle" aria-hidden>
              /
            </span>
            <span className="font-medium text-fg">{title}</span>
          </div>
          <div className="glass pointer-events-auto rounded-xl border border-line-strong p-1 shadow-md" role="toolbar" aria-label="View">
            <Segmented<View>
              label="View"
              size="sm"
              value={view}
              onChange={setView}
              options={[
                {
                  value: "pick",
                  label: "Side by side",
                  icon: Columns2,
                  title: several ? "Two of the scans, each in its own view: click the part to pick matching points" : "Each scan in its own view: click the part to pick matching points",
                },
                {
                  value: "together",
                  label: "Both together",
                  icon: Layers2,
                  disabled: several ? !multi : !pose,
                  title: several
                    ? multi
                      ? "Every placed scan in one view, each in its own colour, to judge the fit by eye"
                      : "Align the scans first"
                    : pose
                      ? "Both scans in one view, the second moved onto the first, to judge the fit by eye"
                      : "Align the two scans first",
                },
              ]}
            />
          </div>
        </div>

        {together ? (
          <div className="pointer-events-none absolute bottom-4 left-4 right-28">
            <div className="glass inline-block max-w-full rounded-xl border border-line-strong px-3 py-2 text-xs shadow-md">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                {list.map((id, i) => {
                  const shown = !multi || i === 0 || !!poseOf(multi, id);
                  return (
                    <span key={id} className="inline-flex min-w-0 items-center gap-2">
                      <ColorDot color={colorOf(id)} hollow={!shown} />
                      <span className={cn("truncate font-medium", shown ? "text-fg" : "text-subtle")}>{nameOf(id)}</span>
                      <span className={cn("shrink-0", shown ? "text-muted" : "text-warn")}>{i === 0 ? "stays in place" : !shown ? "not placed, not shown" : "moved onto it"}</span>
                    </span>
                  );
                })}
              </div>
              {result && standingOf(result) === "not_sure" ? (
                <div className="mt-1 font-medium text-warn">
                  Showing pose {(looking ?? chosenPose ?? 0) + 1} of {result.poses?.length ?? 1}
                  {chosenPose !== null && (looking ?? chosenPose) === chosenPose ? ", your choice" : ""}. The engine was not sure which one is right.
                </div>
              ) : null}
              <div className="mt-1 text-subtle">
                {several ? "A good fit shows the colours finely mixed where the scans overlap." : "A good fit shows the two colours finely mixed where the scans overlap."} Doubled edges
                or a gap mean it is off.
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <aside className="relative flex w-[360px] shrink-0 flex-col overflow-y-auto border-l border-line bg-panel 2xl:w-[400px]" aria-label="Align and merge">
        <div className="border-b border-line px-4 py-4">
          <h1 className="text-base font-semibold text-fg">{title}</h1>
          <p className="mt-1 text-xs leading-5 text-subtle">
            {several
              ? "Line up several scans of the same part, check the fit, then merge them into one new scan."
              : "Line up two scans of the same part, check the fit, then merge them into one new scan."}
          </p>
        </div>

        <Panel title={several ? "The scans" : "The two scans"} icon={Layers}>
          {several ? (
            <ol className="space-y-2.5" aria-label="The scans, the first stays in place">
              {list.map((id, i) => (
                <li key={`${i}-${id}`}>
                  <Field label={roleOf(i)} htmlFor={`align-scan-${i}`} aside={<ColorDot color={colorOf(id)} />}>
                    <div className="flex items-center gap-1.5">
                      <Select id={`align-scan-${i}`} className="min-w-0 flex-1" value={id} disabled={busy} onChange={(e) => choose(i, e.target.value)}>
                        {scans.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                            {recordingMark(s)}
                          </option>
                        ))}
                      </Select>
                      <IconButton icon={X} size="sm" label={`Remove “${nameOf(id)}” from the list`} disabled={busy} onClick={() => st.setScans(removeAt(list, i))} />
                    </div>
                  </Field>
                </li>
              ))}
            </ol>
          ) : (
            <>
              <Field label={ROLE.target} htmlFor="align-target" aside={<ColorDot color={scanColor(0)} />}>
                <Select id="align-target" value={list[0] ?? ""} disabled={busy} onChange={(e) => choose(0, e.target.value)}>
                  {scans.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {recordingMark(s)}
                    </option>
                  ))}
                </Select>
              </Field>
              <div className="my-1.5 flex justify-center">
                <Button size="xs" variant="ghost" icon={ArrowUpDown} disabled={busy || !ready} onClick={() => st.swap()} title="The other scan stays in place; the picked points are kept">
                  Swap the two
                </Button>
              </div>
              <Field label={ROLE.source} htmlFor="align-source" aside={<ColorDot color={scanColor(1)} />}>
                <Select id="align-source" value={list[1] ?? ""} disabled={busy} onChange={(e) => choose(1, e.target.value)}>
                  {scans.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {recordingMark(s)}
                    </option>
                  ))}
                </Select>
              </Field>
            </>
          )}
          <div className="mt-3">
            <Button
              size="xs"
              variant="ghost"
              icon={Plus}
              disabled={busy || !free || !ready}
              onClick={addScan}
              title={
                list.length >= MAX_SCANS
                  ? `At most ${MAX_SCANS} scans at once`
                  : !free
                    ? "Every scan of the project with a mesh is in the list. Make the mesh of another scan on the project page to add it."
                    : "Add another scan with a mesh; it is moved onto the first one too"
              }
            >
              Add a scan
            </Button>
          </div>
        </Panel>

        <Panel title={several ? "Between the scans" : "Between the two scans"} icon={Move3d}>
          <MovedQuestion
            id={ALIGN_MOVED_ID}
            value={st.sameSetup === null ? null : !st.sameSetup}
            onChange={(moved) => st.setSameSetup(!moved)}
            several={several}
            disabled={busy}
            texts={several ? MOVED_TEXT.several : MOVED_TEXT.two}
          />
        </Panel>

        <Panel title="Align" icon={Wand}>
          {aligning ? (
            <div role="status" aria-live="polite">
              <div className="mb-1.5 flex items-center gap-2 text-xs">
                <Spinner size={13} className="shrink-0 text-accent" />
                <span className="min-w-0 flex-1 truncate text-muted" title={alignJob?.message}>
                  {!alignJob || alignJob.state === "queued" ? "Starting…" : alignJob.message || "Working…"}
                </span>
                <span className="shrink-0 font-mono text-fg tnum">{alignJob && alignJob.state !== "queued" ? `${Math.round(alignJob.progress * 100)}%` : ""}</span>
              </div>
              <ProgressBar value={alignJob && alignJob.state !== "queued" ? alignJob.progress : null} label="Alignment progress" />
            </div>
          ) : (
            <>
              <Button variant={hasAnswer ? "secondary" : "primary"} icon={Wand} className="w-full" disabled={!ready || merging || !!movedWait} onClick={() => void st.align(false)}>
                {hasAnswer ? "Align again from the shape alone" : several ? "Align the scans" : "Align the two scans"}
              </Button>
              {movedWait ? (
                <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5 text-warn" role="status">
                  <span>{movedWait}</span>
                  <Button size="xs" variant="secondary" onClick={() => focusMovedQuestion(ALIGN_MOVED_ID)}>
                    Answer it
                  </Button>
                </p>
              ) : null}
              {!hasAnswer ? (
                <p className="mt-2 text-xs leading-5 text-subtle">
                  {several
                    ? `Works from the shape of the scans: each of the ${pairsOf(list).length} pairs is aligned, a few seconds each. Nothing is changed yet.`
                    : "Works from the shape of the two scans and takes a few seconds. Nothing is changed yet."}
                </p>
              ) : null}
            </>
          )}
          {st.alignProblem ? <ProblemCallout problem={st.alignProblem} className="mt-3" /> : null}
          {result && !aligning ? (
            <>
              <TwoVerdict result={result} />
              {standingOf(result) === "not_sure" ? (
                <PoseChooser
                  result={result}
                  looking={looking}
                  chosen={chosenPose}
                  disabled={busy}
                  onLook={(i) => {
                    setLooking(i);
                    setView("together");
                  }}
                  onChoose={(i) => {
                    setLooking(null);
                    st.choosePose(i);
                  }}
                />
              ) : null}
              {pairsDisagree(result) ? (
                <Callout tone="warn" icon={TriangleExclamationPoint} title="Two of your points probably do not match" className="mt-3">
                  The picked points disagree by {result.pairs_rms_mm?.toFixed(1)}&#8239;mm. Compare the numbers on the two scans, remove the point that is off, and align from the
                  points again.
                </Callout>
              ) : null}
              {!result.accepted ? (
                <Callout
                  tone="accent"
                  icon={MapPin}
                  title={result.start === "given" ? "Check your points, or add more" : "Next: pick matching points"}
                  className="mt-3"
                  actions={
                    view !== "pick" ? (
                      <Button size="sm" variant="primary" icon={Columns2} onClick={() => setView("pick")}>
                        Pick points
                      </Button>
                    ) : undefined
                  }
                >
                  {result.start === "given"
                    ? "Each number must sit on the same spot of the part in both scans. Remove a point that is off, add points further apart, then align from the points again."
                    : standingOf(result) === "not_sure"
                      ? "The shape alone did not settle it, so the engine does not guess. Choose one of its poses above after looking at it, or click three or more matching spots on both scans, then choose “Align from these points”."
                      : "The shape alone did not settle it. Click three or more matching spots on both scans, then choose “Align from these points”."}
                </Callout>
              ) : view !== "together" && pose ? (
                <div className="mt-3">
                  <Button size="sm" icon={Layers2} onClick={() => setView("together")}>
                    Show both together
                  </Button>
                </div>
              ) : null}
            </>
          ) : null}
          {multi && !aligning ? (
            <>
              <MultiVerdict result={multi} list={list} nameOf={nameOf} colorOf={colorOf} />
              {multi.unplaced.length ? (
                <Callout
                  tone="accent"
                  icon={MapPin}
                  title="Next: pick matching points"
                  className="mt-3"
                  actions={
                    <Button size="sm" variant="primary" icon={Columns2} onClick={() => pickOn(suggestPickPair(multi, list))}>
                      Pick points
                    </Button>
                  }
                >
                  {unplacedNames.length === 1 ? `“${unplacedNames[0]}” shares too little with the placed scans.` : `${namesText(unplacedNames)} share too little with the placed scans.`}{" "}
                  Click three or more matching spots on {unplacedNames.length === 1 ? "it" : "each of them"} and on a placed scan, then choose “Align from these points”.
                  Or remove {unplacedNames.length === 1 ? "it" : "them"} from the list.
                </Callout>
              ) : view !== "together" ? (
                <div className="mt-3">
                  <Button size="sm" icon={Layers2} onClick={() => setView("together")}>
                    Show them together
                  </Button>
                </div>
              ) : null}
            </>
          ) : null}
        </Panel>

        <Panel
          title="Matching points"
          icon={MapPin}
          aside={
            rows.length ? (
              <Button size="xs" variant="ghost" icon={Trash} disabled={busy} onClick={() => st.clearPicks()}>
                {several ? "Clear these" : "Clear all"}
              </Button>
            ) : null
          }
        >
          <p className="text-xs leading-5 text-muted">
            {several
              ? "A rough start for the alignment: two scans at a time, click the same spot of the part on both, three times or more. A millimetre or two off is fine."
              : "A rough start for the alignment: click the same spot of the part on both scans, three times or more. A millimetre or two off is fine."}
          </p>
          {several && pair ? (
            <Field label="Pick on" htmlFor="align-pick-pair" className="mt-3">
              <Select
                id="align-pick-pair"
                value={pairKey(pair[0], pair[1])}
                disabled={busy}
                onChange={(e) => {
                  const p = pairsOf(list).find(([a, b]) => pairKey(a, b) === e.target.value);
                  if (p) pickOn(p);
                }}
              >
                {pairsOf(list).map(([a, b]) => {
                  const n = completePairs(picksFor(st.picked, a, b)).length;
                  return (
                    <option key={pairKey(a, b)} value={pairKey(a, b)}>
                      {nameOf(a)} and {nameOf(b)}
                      {n ? ` · ${n} point${n === 1 ? "" : "s"}` : ""}
                    </option>
                  );
                })}
              </Select>
            </Field>
          ) : null}
          {view === "pick" ? (
            <p className="mt-2 text-xs font-medium leading-5 text-fg" role="status">
              {pickHint(picks, pickNames)}
            </p>
          ) : (
            <div className="mt-2">
              <Button size="sm" icon={Columns2} onClick={() => setView("pick")}>
                Show the scans side by side to pick
              </Button>
            </div>
          )}
          {rows.length && pair ? (
            <ul className="mt-3 divide-y divide-line rounded-lg border border-line bg-surface-2/50" aria-label="Picked points">
              {rows.map((r) => (
                <li key={r.n} className="flex items-center gap-2.5 py-1 pl-2.5 pr-1 text-xs">
                  <span className="grid h-5 min-w-5 place-items-center rounded-full bg-surface-3 px-1 font-mono font-semibold text-fg tnum">{r.n}</span>
                  <ColorDot color={colorOf(pair[0])} hollow={!r.target} />
                  <ColorDot color={colorOf(pair[1])} hollow={!r.source} />
                  <span className={cn("min-w-0 flex-1 truncate", r.target && r.source ? "text-muted" : "text-warn")}>
                    {r.target && r.source ? "On both scans" : `Not yet on “${r.target ? pickNames.source : pickNames.target}”`}
                  </span>
                  <IconButton icon={X} size="xs" label={`Remove point ${r.n}`} disabled={busy} onClick={() => st.removePair(r.n - 1)} />
                </li>
              ))}
            </ul>
          ) : null}
          {several && (toSend.length || unfinished.length) ? (
            <div className="mt-3 space-y-1 text-xs leading-5">
              {toSend.length ? (
                <p className="text-muted">
                  Sent with the alignment: points between {toSend.length} pair{toSend.length === 1 ? "" : "s"} of scans (
                  {toSend.map((x) => `${nameOf(x.a)} and ${nameOf(x.b)}`).join("; ")}).
                </p>
              ) : null}
              {unfinished.map(([a, b]) => (
                <p key={pairKey(a, b)} className="flex items-start gap-1.5 text-warn">
                  <TriangleExclamationPoint size={13} className="mt-1 shrink-0" aria-hidden />
                  <span>
                    {nameOf(a)} and {nameOf(b)}: fewer than {MIN_PAIRS} points on both, so they are not sent. Pick more, or clear them.
                  </span>
                </p>
              ))}
            </div>
          ) : null}
          <Button
            className="mt-3 w-full"
            variant={(result && !result.accepted && canAlignFromPoints) || (multi && !multi.accepted && canAlignFromPoints) ? "primary" : "secondary"}
            icon={MapPin}
            disabled={!ready || busy || !canAlignFromPoints || !!movedWait}
            title={movedWait ?? (!canAlignFromPoints ? (several ? `Pick at least ${MIN_PAIRS} points on both scans of a pair first` : `Pick at least ${MIN_PAIRS} points on both scans first`) : undefined)}
            onClick={() => void st.align(true)}
          >
            Align from these points
          </Button>
        </Panel>

        <Panel title="Merge" icon={Combine}>
          {merged ? (
            <Callout
              tone="ok"
              icon={CircleCheck}
              title={`Merged into “${merged.scan?.name ?? mergeName}”`}
              actions={
                <>
                  <Button size="sm" variant="primary" icon={Eye} onClick={() => navigate(paths.viewer(pid, merged.scan_id))}>
                    Open the merged scan
                  </Button>
                  <Button size="sm" icon={ArrowLeft} onClick={() => navigate(paths.project(pid))}>
                    Back to the project
                  </Button>
                </>
              }
            >
              {(merged.merged_from?.length ?? 2) > 2
                ? "The new scan is in the project. The scans it was made from are unchanged."
                : "The new scan is in the project. The two scans it was made from are unchanged."}
            </Callout>
          ) : merging ? (
            mergeJob ? (
              <JobProgress job={mergeJob} label="Merging progress" cancelTitle="Stop merging. Nothing is added to the project." icon={Combine} />
            ) : (
              <ProgressBar value={null} label="Merging progress" />
            )
          ) : !hasAnswer ? (
            <p className="text-sm text-muted">
              {several
                ? "Align the scans first. The merge puts each scan where the alignment placed it."
                : "Align the two scans first. The merge puts the second scan where the alignment found it."}
            </p>
          ) : noMerge ? (
            <Callout tone="neutral" icon={Combine} title={noMerge.title}>
              {noMerge.text}
            </Callout>
          ) : offer && !offer.offered ? (
            <p className="flex items-start gap-1.5 text-sm leading-5 text-muted">
              <TriangleExclamationPoint size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden />
              {offer.why}
            </p>
          ) : (
            <>
              <Field label="Name of the new scan" htmlFor="merge-name">
                <TextInput
                  id="merge-name"
                  value={mergeName}
                  maxLength={120}
                  placeholder={defaultName}
                  onChange={(e) => setName(e.target.value)}
                  aria-describedby={nameClash ? "merge-name-taken" : undefined}
                />
              </Field>
              {nameClash ? (
                <div id="merge-name-taken" className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-warn" role="status">
                  <span>Another scan of this project is already called “{mergeName.trim()}”.</span>
                  <Button
                    size="xs"
                    variant="secondary"
                    onClick={() => {
                      setName(nameClash);
                      document.getElementById("merge-name")?.focus(); // the button goes away: the keyboard stays in the field
                    }}
                  >
                    Use “{nameClash}”
                  </Button>
                </div>
              ) : null}
              <p className="mt-2 text-xs leading-5 text-subtle">
                {several
                  ? "All the recordings are rebuilt together into one new scan, which takes about as long as making all their meshes. The scans stay as they are."
                  : "Both recordings are rebuilt together into one new scan, which takes about as long as making both meshes. The two scans stay as they are."}
              </p>
              {result && choiceText(result, chosenPose) ? (
                <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-ok">
                  <CircleCheck size={13} className="mt-1 shrink-0" aria-hidden />
                  {choiceText(result, chosenPose)} The merge puts the second scan there.
                </p>
              ) : offer?.confirm ? (
                <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-warn">
                  <TriangleExclamationPoint size={13} className="mt-1 shrink-0" aria-hidden />
                  The shapes did not confirm your points, so the second scan may be in the wrong place. Merging asks you to confirm.
                </p>
              ) : null}
              {multi && unplacedNames.length ? (
                <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-warn">
                  <TriangleExclamationPoint size={13} className="mt-1 shrink-0" aria-hidden />
                  <span>
                    {unplacedNames.length === 1 ? `“${unplacedNames[0]}” is not placed` : `${namesText(unplacedNames)} are not placed`}, so the scans cannot be merged yet.
                    Pick matching points between {unplacedNames.length === 1 ? "it" : "each of them"} and a placed scan and align from these points, or remove{" "}
                    {unplacedNames.length === 1 ? "it" : "them"} from the list.
                  </span>
                </p>
              ) : multi && needsALook(multi) ? (
                <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-warn">
                  <TriangleExclamationPoint size={13} className="mt-1 shrink-0" aria-hidden />A pair disagrees by {multi.worst_link_mm.toFixed(2)}&#8239;mm with where the scans
                  were placed, so a scan may be off. Look at them together first. Merging asks you to confirm.
                </p>
              ) : null}
              <Button
                className="mt-3 w-full"
                variant="primary"
                icon={Combine}
                loading={st.mergeStarting}
                disabled={aligning || (!!multi && unplacedNames.length > 0)}
                onClick={() => {
                  const confirm = result ? !!offer?.confirm : multi ? mergeNeedsConfirm(multi) : false;
                  if (confirm) setConfirming(true);
                  else void st.merge(mergeName);
                }}
              >
                Merge into a new scan
              </Button>
            </>
          )}
          {merged ? <LeftOut merged={merged} /> : null}
          {st.mergeCancelled && !merging && !merged ? (
            <Callout tone="neutral" title="Merging was cancelled" className="mt-3">
              {several
                ? "Nothing was added to the project, and the scans are unchanged. Merge again when you are ready."
                : "Nothing was added to the project, and the two scans are unchanged. Merge again when you are ready."}
            </Callout>
          ) : null}
          {st.mergeProblem && !merging ? <ProblemCallout problem={st.mergeProblem} className="mt-3" /> : null}
        </Panel>
      </aside>

      {several ? (
        <ConfirmDialog
          open={confirming}
          onClose={() => setConfirming(false)}
          title="Merge although a pair disagrees?"
          confirmLabel="Merge anyway"
          tone="danger"
          onConfirm={() => st.merge(mergeName)}
        >
          {multi && !multi.accepted
            ? "Not every scan was placed, so the merge may leave a scan out or put it in the wrong place."
            : `A pair disagrees by ${multi?.worst_link_mm.toFixed(2) ?? "–"}\u202fmm with where the scans were placed, so a scan may be off.`}{" "}
          The merged scan would then show parts twice, or smeared. Look at the scans together first: if one does not sit on the others, pick matching points between it and
          another scan and align again instead.
        </ConfirmDialog>
      ) : (
        <ConfirmDialog
          open={confirming}
          onClose={() => setConfirming(false)}
          title="Merge at your points although the shapes did not confirm them?"
          confirmLabel="Merge anyway"
          tone="danger"
          onConfirm={() => st.merge(mergeName)}
        >
          The second scan may be in the wrong place. The merged scan would then show the part twice, or smeared. Look at both scans together first: if they do not sit on
          each other, check your points (two may be swapped) and align again instead.
        </ConfirmDialog>
      )}
    </div>
  );
}
