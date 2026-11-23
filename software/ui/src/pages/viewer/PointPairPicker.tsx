/**
 * Picking matching points on the scan and on the reference, for the alignment "From points" (the Compare panel): the
 * two meshes side by side over the viewer's 3D view, each in the Align page's own picking view (align/AlignCanvas.tsx,
 * `PickView`: a click without dragging adds a numbered point), the pairs listed, "Refine on the shapes" (a best fit
 * from there, a job) and "Align". Points on the reference are picked on its display mesh, in its own numbers: they are
 * multiplied by the units factor before they are sent (the engine's reference is in mm).
 *
 * The scan's view gets its own geometry object sharing the scan's arrays (nothing is copied): a picking view builds its
 * raycast tree on the geometry it shows and removes it when it closes, and the viewer's tree must stay.
 */
import { MousePointerClick, Undo2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { BufferGeometry } from "three";
import type { PickedPair } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Kbd } from "../../components/ui/Feedback";
import { Switch } from "../../components/ui/Form";
import { addTurnPick, type Picks, type Side } from "../../lib/align";
import { PickView, SCAN_COLOR } from "../align/AlignCanvas";

type Vec3 = [number, number, number];

/** A geometry that shares `g`'s arrays (and its triangle bookkeeping), with no raycast tree of its own yet. */
function sharing(g: BufferGeometry): BufferGeometry {
  const out = new BufferGeometry();
  for (const [name, attr] of Object.entries(g.attributes)) out.setAttribute(name, attr);
  out.setIndex(g.index);
  out.boundingBox = g.boundingBox?.clone() ?? null;
  out.boundingSphere = g.boundingSphere?.clone() ?? null;
  out.userData = { ...g.userData };
  return out;
}

export function PointPairPicker({
  scan,
  reference,
  scale,
  busy,
  onAlign,
  onClose,
}: {
  scan: BufferGeometry;
  reference: BufferGeometry;
  /** The reference's units factor (its picked points times this are mm). */
  scale: number;
  busy: boolean;
  onAlign: (pairs: PickedPair[], refine: boolean) => void;
  onClose: () => void;
}) {
  const scanView = useMemo(() => sharing(scan), [scan]);
  const refView = useMemo(() => sharing(reference), [reference]);
  // one state for both meshes, the scan as `target`: a pick is taken only in its turn (lib/align.ts addTurnPick), seen
  // on the latest picks, so quick clicks never put the pairs out of step
  const [picks, setPicks] = useState<Picks>({ target: [], source: [] });
  const scanPicks = picks.target;
  const refPicks = picks.source;
  const pick = (side: Side) => (p: Vec3) => setPicks((x) => addTurnPick(x, side, p));
  const [refine, setRefine] = useState(true);
  const n = Math.min(scanPicks.length, refPicks.length);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  const undo = () =>
    setPicks((x) =>
      x.target.length > x.source.length
        ? { ...x, target: x.target.slice(0, -1) }
        : x.source.length > x.target.length
          ? { ...x, source: x.source.slice(0, -1) }
          : { target: x.target.slice(0, -1), source: x.source.slice(0, -1) },
    );
  const hint =
    scanPicks.length <= refPicks.length
      ? `Click point ${scanPicks.length + 1} on the scan (left)`
      : `Click the same point, ${scanPicks.length}, on the CAD model (right)`;
  const align = () => {
    const pairs: PickedPair[] = [];
    for (let i = 0; i < n; i++) pairs.push({ scan: scanPicks[i], reference: refPicks[i].map((x) => x * scale) as Vec3 });
    onAlign(pairs, refine);
  };

  return (
    <div className="absolute inset-0 z-[40] flex flex-col bg-panel" role="dialog" aria-label="Pick matching points on the scan and on the CAD model">
      {/* two rows of fixed height, nothing wraps: the views below never move between turns, so a click lands where aimed */}
      <div className="border-b border-line bg-panel px-4 py-2">
        <div className="flex h-6 min-w-0 items-center gap-2">
          <MousePointerClick size={15} className="shrink-0 text-accent" aria-hidden />
          <span className="min-w-0 truncate text-sm font-medium text-fg" title={hint} aria-live="polite">{hint}</span>
        </div>
        <div className="mt-1 flex h-8 min-w-0 items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-xs text-muted" title="Three or more pairs, spread over the part, in the same order on both">
            {n} pair{n === 1 ? "" : "s"}, three or more, spread over the part, in the same order on both
          </span>
          <Switch checked={refine} onChange={setRefine} label="Refine on the shapes" />
          <IconButton icon={Undo2} size="sm" label="Take the last point back" disabled={!scanPicks.length && !refPicks.length} onClick={undo} />
          <Button size="sm" variant="primary" className="shrink-0" disabled={n < 3 || busy} loading={busy} onClick={align} title={refine ? "A best fit from these pairs (a few seconds)" : "The pose from these pairs as they are"}>
            Align from {n} pair{n === 1 ? "" : "s"}
          </Button>
          <IconButton icon={X} size="sm" label="Close (Esc)" onClick={onClose} />
        </div>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-px bg-line">
        <div className="relative min-h-0 viewport-bg">
          <PickView geometry={scanView} picks={scanPicks} color={SCAN_COLOR.target} label="The scan, for picking points" enabled={scanPicks.length <= refPicks.length} listen onPick={pick("target")} />
          <div className="pointer-events-none absolute left-3 top-3 rounded-full border border-line bg-panel/90 px-2.5 py-1 text-xs font-medium text-fg" style={{ borderColor: SCAN_COLOR.target }}>
            The scan
          </div>
        </div>
        <div className="relative min-h-0 viewport-bg">
          <PickView geometry={refView} picks={refPicks} color={SCAN_COLOR.source} label="The CAD model, for picking points" enabled={refPicks.length < scanPicks.length} listen onPick={pick("source")} />
          <div className="pointer-events-none absolute left-3 top-3 rounded-full border border-line bg-panel/90 px-2.5 py-1 text-xs font-medium text-fg" style={{ borderColor: SCAN_COLOR.source }}>
            The CAD model
          </div>
        </div>
      </div>
      <div className="border-t border-line bg-panel px-4 py-1.5 text-[11px] text-subtle">
        Left drag orbits, right drag pans, the wheel zooms. <Kbd>Esc</Kbd> closes without aligning.
      </div>
    </div>
  );
}
