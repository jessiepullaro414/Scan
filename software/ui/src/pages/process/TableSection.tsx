/**
 * "Remove the table" (FEATURE_PLAN X1), at the foot of the Floaters tab. "Find the table" asks the
 * engine which faces are the surface the part stands on (nothing changes yet) and shows them with
 * the view's selection highlight, with their count and area; "Remove" removes them as an edit (undo
 * brings them back), "Cancel" lets them go.
 *
 * Raised areas: a thin part lying flat that the engine keeps is drawn green; a raised area it is not
 * sure of goes with the table, drawn amber, listed (where, how large, how high), each with Look (the
 * view turns to it) and Keep this (it is taken out of what goes: the preview is asked again with a
 * point on it, and the same points go with the edit, so undo and redo stay exact). The scan's own limit
 * is said. When there is no table to stand behind, the engine says so in its own words and nothing is
 * highlighted. The margin is an advanced value.
 */
import { CircleHelp, Eye, PanelBottom, Pin, PinOff, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Vector3 } from "three";
import { ApiError, api } from "../../api/http";
import type { TablePreview } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Callout } from "../../components/ui/Layout";
import { Tooltip } from "../../components/ui/Popover";
import { decodeFaces } from "../../lib/fits";
import {
  areaText,
  drawnAreas,
  TABLE_HELP,
  TABLE_MARGIN_HELP,
  TABLE_MARGIN_MAX_MM,
  TABLE_MARGIN_MM,
  tableEditLabel,
  tableFacts,
  toggleKeep,
} from "../../lib/table";
import { processKey, useProcess } from "../../store/process";
import { toast } from "../../store/toasts";
import { cvToThree } from "../../three/coords";
import { viewBus } from "../../three/viewBus";
import { Help, NumberField } from "./bits";
import type { LoadedMesh } from "./mesh";
import type { MarkedArea } from "./ProcessCanvas";

/** The colours of the raised areas on the model: kept (green, as the holes a fill would close) and going (amber). */
export const AREA_COLORS = { kept: "#3fb950", warned: "#f5a524" } as const;

type Phase =
  | { kind: "idle" }
  | { kind: "finding" }
  | { kind: "shown"; preview: TablePreview; version: number }
  | { kind: "refused"; detail: string; hint: string | null };

function TableHelp() {
  return (
    <div className="space-y-1.5">
      <div className="font-semibold">{TABLE_HELP.title}</div>
      {TABLE_HELP.lines.map((l) => (
        <div key={l.label}>
          <span className="font-medium text-fg">{l.label}: </span>
          <span className="text-muted">{l.text}</span>
        </div>
      ))}
    </div>
  );
}

export function TableSection({
  pid,
  sid,
  busy,
  mesh,
  onSelection,
  onUseSelection,
  onAreas,
}: {
  pid: string;
  sid: string;
  busy: boolean;
  /** The current mesh while it can be selected on (null while comparing, loading, or without a mesh). */
  mesh: LoadedMesh | null;
  /** The page's selected-face count follows the highlight. */
  onSelection: (n: number) => void;
  /** Tell the page the highlighted faces are used (the next mesh then clears them without a word). */
  onUseSelection: () => void;
  /** The raised areas to draw on the model (empty: none). */
  onAreas: (areas: MarkedArea[]) => void;
}) {
  const edit = useProcess((s) => s.edit);
  const action = useProcess((s) => s.entries[processKey(pid, sid)]?.action);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [margin, setMargin] = useState(TABLE_MARGIN_MM);
  const [keep, setKeep] = useState<number[][]>([]);
  const [advanced, setAdvanced] = useState(false);
  const ours = useRef(false); // the highlight is the table's (not the user's own selection)
  const onAreasRef = useRef(onAreas);
  onAreasRef.current = onAreas;

  // A new mesh (an edit, undo, redo): what was shown, and the areas kept on it, no longer hold.
  const version = mesh?.version ?? null;
  useEffect(() => {
    setPhase((p) => (p.kind === "shown" && p.version !== version ? { kind: "idle" } : p.kind === "refused" ? { kind: "idle" } : p));
    setKeep([]);
  }, [version]);
  // Selecting by hand over the shown table: the faces are the user's now (Delete removes them as they are).
  useEffect(() => {
    if (phase.kind !== "shown" || !mesh) return;
    return mesh.selection.onChange(() => {
      if (!ours.current) setPhase({ kind: "idle" });
    });
  }, [phase.kind, mesh]);
  // The raised areas on the model, while the preview is shown.
  useEffect(() => {
    if (phase.kind !== "shown") {
      onAreasRef.current([]);
      return;
    }
    onAreasRef.current(
      drawnAreas(phase.preview).map((a) => ({ key: `${phase.version}:${a.index}:${a.verdict}`, faces: a.faces, color: AREA_COLORS[a.verdict] })),
    );
  }, [phase]);
  useEffect(() => () => onAreasRef.current([]), []);

  const clearShown = () => {
    if (mesh && phase.kind === "shown") {
      ours.current = true;
      mesh.selection.clear();
      ours.current = false;
      onSelection(0);
    }
    setPhase({ kind: "idle" });
  };

  const find = async (points: number[][] = keep) => {
    if (!mesh || busy) return;
    const m = mesh;
    setPhase({ kind: "finding" });
    try {
      const r = await api.processTablePreview(pid, sid, { margin_mm: margin, version: m.version, ...(points.length ? { keep: points } : {}) });
      ours.current = true;
      const n = m.selection.selectFaces(decodeFaces(r.faces));
      ours.current = false;
      onSelection(n);
      setPhase({ kind: "shown", preview: r, version: m.version });
    } catch (e) {
      ours.current = false;
      if (e instanceof ApiError && e.status === 422 && !/margin_mm|keep/.test(e.detail)) {
        setPhase({ kind: "refused", detail: e.detail, hint: e.hint ?? null });
        return;
      }
      setPhase({ kind: "idle" });
      if (e instanceof ApiError && e.status === 409) toast.warn("The mesh changed", "Find the table again on the current mesh.");
      else toast.error("Couldn't find the table", e);
    }
  };

  const remove = async () => {
    if (phase.kind !== "shown" || busy) return;
    onUseSelection();
    const body = { op: "remove_table" as const, params: { margin_mm: margin, ...(keep.length ? { keep } : {}) } };
    if (await edit(pid, sid, body, tableEditLabel(phase.preview.n_faces))) setPhase({ kind: "idle" });
  };

  const look = (centre: [number, number, number], radius: number) => {
    const c = cvToThree(new Vector3(centre[0], centre[1], centre[2]));
    viewBus.emit({ type: "frame", animate: true, sphere: { center: [c.x, c.y, c.z], radius } });
  };

  const facts = phase.kind === "shown" ? tableFacts(phase.preview) : null;
  const drawn = phase.kind === "shown" ? drawnAreas(phase.preview) : [];
  const areaWarnings = (w: string) => /^A raised area .* goes with the table|^A thin part lying flat is kept|^\d+ more raised areas like these/.test(w);

  return (
    <div className="space-y-2.5 border-t border-line pt-3">
      <div className="flex items-center gap-1 text-xs">
        <span className="font-medium text-fg">Remove the table</span>
        <Tooltip side="top" content={<TableHelp />}>
          <button
            type="button"
            className="grid h-5 w-5 place-items-center rounded text-subtle hover:text-fg"
            aria-label={`${TABLE_HELP.title}: ${TABLE_HELP.lines.map((l) => `${l.label}: ${l.text}`).join(" ")}`}
          >
            <CircleHelp size={13} aria-hidden />
          </button>
        </Tooltip>
      </div>

      {phase.kind === "shown" && facts ? (
        <Callout
          tone="accent"
          icon={PanelBottom}
          title={facts.head}
          actions={
            <>
              <Button size="xs" variant="primary" icon={PanelBottom} disabled={busy} loading={action === "edit"} onClick={() => void remove()}>
                Remove
              </Button>
              <Button size="xs" variant="ghost" disabled={action === "edit"} onClick={clearShown}>
                Cancel
              </Button>
            </>
          }
        >
          <span aria-live="polite">
            Highlighted in the view. {facts.detail}
          </span>
          {drawn.length ? (
            <ul className="mt-2 space-y-1.5" aria-label="Raised areas on the table">
              {drawn.map((a) => {
                const r = phase.preview.raised[a.index];
                const going = a.verdict === "warned";
                return (
                  <li key={a.index} className="rounded-md border border-line bg-surface-2 px-2 py-1.5">
                    <div className="flex items-start gap-1.5">
                      <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: AREA_COLORS[a.verdict] }} aria-hidden />
                      <span className={going ? "font-medium text-warn" : "text-fg"}>
                        {going ? "Goes with the table, may be a thin part: " : a.byUser ? "Kept, as you chose: " : "A thin part, kept: "}
                        <span className="font-normal text-muted">{areaText(r)}</span>
                      </span>
                    </div>
                    <div className="mt-1 flex gap-1.5 pl-4">
                      <Button size="xs" variant="ghost" icon={Eye} onClick={() => look(a.centre, a.radius)}>
                        Look
                      </Button>
                      {going || a.byUser ? (
                        <Button
                          size="xs"
                          variant={going ? "secondary" : "ghost"}
                          icon={going ? Pin : PinOff}
                          disabled={busy || phase.kind !== "shown"}
                          onClick={() => {
                            const next = toggleKeep(keep, r);
                            setKeep(next);
                            void find(next);
                          }}
                        >
                          {going ? "Keep this" : "Let it go"}
                        </Button>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : null}
          {phase.preview.warnings
            .filter((w) => !areaWarnings(w))
            .map((w) => (
              <span key={w} className={/where it is uneven itself/.test(w) ? "mt-1 block font-medium text-warn" : "mt-1 block"}>
                {w}
              </span>
            ))}
          {phase.preview.limit ? <span className="mt-1 block text-subtle">{phase.preview.limit}</span> : null}
        </Callout>
      ) : phase.kind === "refused" ? (
        <Callout
          tone="warn"
          icon={TriangleAlert}
          title="No table to remove"
          actions={
            <Button size="xs" variant="ghost" onClick={() => setPhase({ kind: "idle" })}>
              OK
            </Button>
          }
        >
          {phase.detail}
        </Callout>
      ) : (
        <>
          <Help>Finds the flat surface the part stands on and highlights it, so you see what would go before it goes.</Help>
          <Button
            variant="secondary"
            size="sm"
            icon={PanelBottom}
            className="w-full"
            disabled={busy || !mesh}
            loading={phase.kind === "finding"}
            onClick={() => void find()}
          >
            Find the table
          </Button>
        </>
      )}

      <div>
        <button
          type="button"
          className="text-[11.5px] text-subtle underline-offset-2 hover:text-fg hover:underline"
          aria-expanded={advanced}
          onClick={() => setAdvanced((v) => !v)}
        >
          {advanced ? "Hide the margin" : `Margin: ${margin} mm`}
        </button>
        {advanced ? (
          <div className="mt-1.5 space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted">Margin</span>
              <NumberField
                label="Margin above the table, mm"
                value={margin}
                onChange={(v) => {
                  setMargin(v);
                  if (phase.kind === "shown") clearShown();
                }}
                min={0}
                max={TABLE_MARGIN_MAX_MM}
                step={0.5}
                unit="mm"
                className="w-24"
              />
            </div>
            <Help>{TABLE_MARGIN_HELP}</Help>
          </div>
        ) : null}
      </div>
    </div>
  );
}
