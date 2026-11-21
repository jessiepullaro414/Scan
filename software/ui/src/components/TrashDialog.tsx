/**
 * The trash (UX_PLAN U7; APP_API.md "Rename a scan, the trash"): deleted projects and scans, each with Restore, and
 * "Empty trash…", which asks a second time, naming what goes and that it cannot be undone, before anything is
 * deleted for good. The second question is a step of the same dialog (one focus trap), and it opens with the focus on
 * Cancel (U6). Only the items this dialog listed are deleted.
 */
import { FolderOpen, RotateCcw, ScanLine, Trash, TriangleExclamationPoint } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/http";
import type { TrashItem } from "../api/types";
import { emptyTrashQuestion, trashItemLine, trashSummary } from "../lib/scanTrash";
import { fmtDateTime, fmtRelative } from "../lib/format";
import { useProjects } from "../store/projects";
import { toast } from "../store/toasts";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { Skeleton } from "./ui/Feedback";

export function TrashDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [items, setItems] = useState<TrashItem[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [step, setStep] = useState<"list" | "confirm">("list");
  const [busy, setBusy] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const emptyRef = useRef<HTMLButtonElement>(null);
  const fetchList = useProjects((s) => s.fetchList);
  const fetchProject = useProjects((s) => s.fetchProject);

  const load = useCallback(async () => {
    try {
      setItems((await api.trash()).items);
      setFailed(null);
    } catch (e) {
      setFailed(e instanceof Error ? e.message : "The trash could not be read.");
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setItems(null);
    setStep("list");
    void load();
  }, [open, load]);

  // the second question opens with the focus on Cancel (U6); back on the list, on "Empty trash…"
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => (step === "confirm" ? cancelRef.current : emptyRef.current)?.focus(), 0);
    return () => clearTimeout(t);
  }, [step, open]);

  const restore = async (it: TrashItem) => {
    setBusy(it.id);
    try {
      const r = await api.restoreFromTrash(it.id);
      toast.success(`Restored “${it.name}”`, r.renamed ? `Its name was taken, so it is now “${r.name}”.` : "It is back where it was.");
      void fetchList();
      void fetchProject(r.project_id);
      await load();
    } catch (e) {
      toast.error(`Couldn't restore “${it.name}”`, e);
    } finally {
      setBusy(null);
    }
  };

  const empty = async () => {
    if (!items?.length) return;
    setBusy("empty");
    try {
      const r = await api.emptyTrash(items.map((i) => i.id));
      if (r.left.length) toast.error("Some items could not be deleted", "A program may have a file of them open. Close it, then empty the trash again.");
      else toast.success(`Deleted ${r.removed.length} item${r.removed.length === 1 ? "" : "s"} for good`);
      setStep("list");
      await load();
    } catch (e) {
      toast.error("Couldn't empty the trash", e); // the question stays, to try again or cancel
    } finally {
      setBusy(null);
    }
  };

  const q = items ? emptyTrashQuestion(items) : null;
  return (
    <Dialog
      open={open}
      onClose={() => !busy && onClose()}
      title={step === "confirm" && q ? q.title : "Trash"}
      width={560}
      description={
        step === "confirm" && q ? (
          <span className="flex gap-1.5 text-fg">
            <TriangleExclamationPoint size={15} className="mt-0.5 shrink-0 text-danger" aria-hidden />
            <span>{q.body}</span>
          </span>
        ) : (
          "Deleted projects and scans stay here until you empty the trash. Restore puts one back where it was."
        )
      }
      footer={
        step === "confirm" ? (
          <>
            <Button ref={cancelRef} variant="secondary" onClick={() => setStep("list")} disabled={busy === "empty"}>
              Cancel
            </Button>
            <Button variant="danger" icon={Trash} loading={busy === "empty"} onClick={() => void empty()}>
              Delete for good
            </Button>
          </>
        ) : (
          <>
            <Button ref={emptyRef} variant="ghost" icon={Trash} onClick={() => setStep("confirm")} disabled={!items?.length || !!busy}>
              Empty trash…
            </Button>
            <Button variant="secondary" onClick={onClose} disabled={!!busy}>
              Close
            </Button>
          </>
        )
      }
    >
      {step === "list" ? (
        failed ? (
          <p className="text-sm text-danger">{failed}</p>
        ) : items === null ? (
          <div className="space-y-2" aria-busy="true">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : (
          <div>
            <p className="mb-2 text-xs text-muted tnum">{trashSummary(items)}</p>
            {items.length ? (
              <ul className="divide-y divide-line rounded-lg border border-line" aria-label="Items in the trash">
                {items.map((it) => {
                  const Icon = it.kind === "project" ? FolderOpen : ScanLine;
                  const blocked = it.kind === "scan" && it.project_exists === false ? "Restore its project first." : null;
                  return (
                    <li key={it.id} className="flex items-center gap-3 px-3 py-2.5">
                      <Icon size={16} className="shrink-0 text-muted" aria-hidden />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium text-fg">{it.name}</div>
                        <div className="text-xs text-subtle tnum">
                          {trashItemLine(it)}
                          {it.deleted ? <span title={fmtDateTime(it.deleted)}> · deleted {fmtRelative(it.deleted)}</span> : null}
                        </div>
                        {blocked ? <div className="text-xs text-warn">{blocked}</div> : null}
                      </div>
                      <Button
                        size="sm"
                        icon={RotateCcw}
                        loading={busy === it.id}
                        disabled={!!busy || !!blocked}
                        onClick={() => void restore(it)}
                        aria-label={`Restore ${it.name}`}
                      >
                        Restore
                      </Button>
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </div>
        )
      ) : null}
    </Dialog>
  );
}
