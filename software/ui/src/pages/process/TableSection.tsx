/**
 * "Remove the table" (FEATURE_PLAN X1), at the foot of the Floaters tab. "Find the table" asks the
 * engine which faces are the surface the part stands on (nothing changes yet) and shows them with
 * the view's selection highlight, with their count and area; "Remove" removes them as an edit (undo
 * brings them back), "Cancel" lets them go. A thin part lying flat that the engine kept is said; a
 * raised area it could not tell from the table goes with it, and its warning stands out before Remove
 * (where, how large, how high). When there is no table to stand behind, the engine says
 * so in its own words and nothing is highlighted. The margin is an advanced value.
 */
import { CircleHelp, PanelBottom, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/http";
import type { TablePreview } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Tooltip } from "../../components/ui/Popover";
import { Callout } from "../../components/ui/Layout";
import { decodeFaces } from "../../lib/fits";
import { TABLE_HELP, TABLE_MARGIN_HELP, TABLE_MARGIN_MAX_MM, TABLE_MARGIN_MM, tableEditLabel, tableFacts } from "../../lib/table";
import { processKey, useProcess } from "../../store/process";
import { toast } from "../../store/toasts";
import { Help, NumberField } from "./bits";
import type { LoadedMesh } from "./mesh";

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
}) {
  const edit = useProcess((s) => s.edit);
  const action = useProcess((s) => s.entries[processKey(pid, sid)]?.action);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [margin, setMargin] = useState(TABLE_MARGIN_MM);
  const [advanced, setAdvanced] = useState(false);
  const ours = useRef(false); // the highlight is the table's (not the user's own selection)

  // A new mesh (an edit, undo, redo) or a change of the margin: what was shown no longer holds.
  const version = mesh?.version ?? null;
  useEffect(() => {
    setPhase((p) => (p.kind === "shown" && p.version !== version ? { kind: "idle" } : p.kind === "refused" ? { kind: "idle" } : p));
  }, [version]);
  // Selecting by hand over the shown table: the faces are the user's now (Delete removes them as they are).
  useEffect(() => {
    if (phase.kind !== "shown" || !mesh) return;
    return mesh.selection.onChange(() => {
      if (!ours.current) setPhase({ kind: "idle" });
    });
  }, [phase.kind, mesh]);

  const clearShown = () => {
    if (mesh && phase.kind === "shown") {
      ours.current = true;
      mesh.selection.clear();
      ours.current = false;
      onSelection(0);
    }
    setPhase({ kind: "idle" });
  };

  const find = async () => {
    if (!mesh || busy) return;
    const m = mesh;
    setPhase({ kind: "finding" });
    try {
      const r = await api.processTablePreview(pid, sid, { margin_mm: margin, version: m.version });
      ours.current = true;
      const n = m.selection.selectFaces(decodeFaces(r.faces));
      ours.current = false;
      onSelection(n);
      setPhase({ kind: "shown", preview: r, version: m.version });
    } catch (e) {
      ours.current = false;
      if (e instanceof ApiError && e.status === 422 && !/margin_mm/.test(e.detail)) {
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
    if (await edit(pid, sid, { op: "remove_table", params: { margin_mm: margin } }, tableEditLabel(phase.preview.n_faces))) setPhase({ kind: "idle" });
  };

  const facts = phase.kind === "shown" ? tableFacts(phase.preview) : null;

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
          {phase.preview.warnings.map((w) => (
            // a raised area that may be a thin part, going with the table: said before Remove, and stands out
            <span key={w} className={/goes with the table|go with it/.test(w) ? "mt-1 block font-medium text-warn" : "mt-1 block"}>
              {w}
            </span>
          ))}
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
