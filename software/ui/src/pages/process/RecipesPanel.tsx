/**
 * Recipes: named stage settings plus edits that apply in one click. Apply "just the edits"
 * (keep the mesh, replace the edits) or "everything" (re-run the stages, then the edits).
 * "Save as recipe…" stores this scan's steps; user recipes can be deleted, built-ins can't.
 */
import { BookmarkPlus, ChevronDown, Play, RefreshCw, ScrollText, Trash } from "lucide-react";
import { useId, useState } from "react";
import type { ProcessState, Recipe, RecipeScope, Scan } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button, IconButton } from "../../components/ui/Button";
import { ConfirmDialog, Dialog } from "../../components/ui/Dialog";
import { Skeleton } from "../../components/ui/Feedback";
import { TextInput } from "../../components/ui/Form";
import { Callout } from "../../components/ui/Layout";
import { Menu } from "../../components/ui/Popover";
import { fmtDetailMm, fmtInt } from "../../lib/format";
import { historyLabel, opChip, settingsChips } from "../../lib/recipes";
import { stageBlock } from "../../lib/keepMode";
import { qualityName } from "../../lib/quality";
import { cn } from "../../lib/util";
import { useProcess } from "../../store/process";
import { useRecipes } from "../../store/recipes";
import { toast } from "../../store/toasts";
import { Help, Panel } from "./bits";

// U8 / U13: the chips' words live in lib/recipes.ts (tested)

function RecipeRow({
  recipe,
  canEdits,
  allBlocked,
  busy,
  onApply,
  onDelete,
}: {
  recipe: Recipe;
  canEdits: boolean;
  /** Why "Everything" cannot run on this scan (R2), or null. */
  allBlocked: string | null;
  busy: boolean;
  onApply: (scope: RecipeScope) => void;
  onDelete: () => void;
}) {
  return (
    <li className="rounded-lg border border-line bg-surface-2/50 px-3 py-2.5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-fg" title={recipe.name}>
              {recipe.name}
            </span>
            {recipe.builtin ? (
              <Badge tone="neutral" className="h-[18px] px-1.5 text-[10px]">
                Built in
              </Badge>
            ) : null}
          </div>
          {recipe.description ? <p className="mt-0.5 line-clamp-2 text-[11.5px] leading-[1.45] text-muted">{recipe.description}</p> : null}
        </div>
        {!recipe.builtin ? <IconButton icon={Trash} size="xs" label={`Delete the recipe “${recipe.name}”`} onClick={onDelete} /> : null}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {[...settingsChips(recipe), ...recipe.ops.map(opChip)].map((c, i) => (
          <span key={i} className={cn("rounded-full border px-1.5 py-px text-[10.5px]", i < 3 ? "border-line text-subtle" : "border-accent-line/60 bg-accent-soft text-accent")}>
            {c}
          </span>
        ))}
        {!recipe.ops.length ? <span className="rounded-full border border-line px-1.5 py-px text-[10.5px] text-subtle">No edits</span> : null}
      </div>
      <div className="mt-2.5">
        <Menu
          label={`Apply “${recipe.name}”`}
          align="start"
          width={286}
          items={[
            { label: "Just the edits", icon: Play, hint: "keeps the mesh", disabled: !canEdits, onSelect: () => onApply("edits") },
            allBlocked
              ? { label: "Everything", icon: RefreshCw, hint: "not for this scan", disabled: true, description: allBlocked, onSelect: () => undefined }
              : { label: "Everything", icon: RefreshCw, hint: "runs the stages again", onSelect: () => onApply("all") },
          ]}
          renderTrigger={(t) => (
            <Button
              ref={t.ref}
              onClick={t.onClick}
              aria-haspopup={t["aria-haspopup"]}
              aria-expanded={t["aria-expanded"]}
              // a screen reader hears which recipe it applies; the name starts with the visible word
              aria-label={`Apply “${recipe.name}”`}
              size="xs"
              variant="secondary"
              iconRight={ChevronDown}
              disabled={busy}
            >
              Apply
            </Button>
          )}
        />
      </div>
    </li>
  );
}

function SaveRecipeDialog({ open, onClose, pid, sid, state }: { open: boolean; onClose: () => void; pid: string; sid: string; state: ProcessState }) {
  const create = useRecipes((s) => s.create);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const nameId = useId();
  const descId = useId();
  const active = state.edits.slice(0, state.cursor);
  const kept = active.filter((e) => e.op !== "delete_faces" && e.op !== "keep_faces");
  const dropped = active.length - kept.length;
  const error = !name.trim() ? "Give the recipe a name." : null;

  const submit = async () => {
    setTouched(true);
    if (error || busy) return;
    setBusy(true);
    const r = await create({ name: name.trim(), description: description.trim() || undefined, from_scan: { project_id: pid, scan_id: sid } });
    setBusy(false);
    if (r) {
      toast.success(`Saved “${r.name}”`, "It's in the recipe list for every scan.");
      setName("");
      setDescription("");
      setTouched(false);
      onClose();
    }
  };

  return (
    <Dialog
      open={open}
      onClose={() => !busy && onClose()}
      title="Save as recipe"
      description="Stores this scan's stage settings and its edits under a name, to apply to other scans in one click."
      width={480}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" icon={BookmarkPlus} loading={busy} onClick={() => void submit()} disabled={touched && !!error}>
            Save recipe
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="space-y-3"
      >
        <div>
          <label htmlFor={nameId} className="mb-1.5 block text-xs font-medium text-muted">
            Name
          </label>
          <TextInput
            id={nameId}
            data-autofocus
            value={name}
            maxLength={60}
            placeholder="e.g. Shiny brackets"
            onChange={(e) => setName(e.target.value)}
            invalid={touched && !!error}
            aria-invalid={touched && !!error}
          />
          <div className={cn("mt-1 h-4 text-xs", touched && error ? "text-danger" : "text-transparent")}>{touched && error ? error : "."}</div>
        </div>
        <div>
          <label htmlFor={descId} className="mb-1.5 block text-xs font-medium text-muted">
            Description <span className="font-normal text-subtle">(optional)</span>
          </label>
          <textarea
            id={descId}
            value={description}
            maxLength={240}
            rows={2}
            placeholder="What it's good for"
            onChange={(e) => setDescription(e.target.value)}
            className="w-full resize-none rounded-md border border-line bg-surface-2 px-2.5 py-1.5 text-sm text-fg placeholder:text-subtle hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--c-accent-soft)] focus:outline-none"
          />
        </div>
        <div className="rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-xs text-muted">
          <div className="mb-1 font-medium text-fg">What it keeps</div>
          <ul className="list-disc space-y-0.5 pl-4">
            <li>
              Stages: {qualityName(state.stages.track.quality) ?? "standard quality"},{" "}
              {state.stages.straighten.state === "skipped" ? "no straightening" : "straighten"},{" "}
              {state.stages.refine.state === "done" || state.stages.refine.state === "skipped" ? "refine with lasers" : "no laser refine"}
              {state.stages.fuse.voxel_mm !== null ? `, mesh detail ${fmtDetailMm(state.stages.fuse.voxel_mm)}` : ""}
            </li>
            <li>{kept.length ? `${kept.length} edit${kept.length === 1 ? "" : "s"}: ${kept.map((e) => historyLabel(e.label)).join(", ")}` : "No edits"}</li>
          </ul>
          {dropped ? (
            <p className="mt-1.5 text-subtle">
              {dropped === 1 ? "One selection edit is" : `${dropped} selection edits are`} left out: deleting or keeping faces only fits this scan.
            </p>
          ) : null}
        </div>
      </form>
    </Dialog>
  );
}

export function RecipesPanel({
  pid,
  sid,
  state,
  busy,
  scan,
}: {
  pid: string;
  sid: string;
  state: ProcessState;
  busy: boolean;
  scan?: Pick<Scan, "keep" | "freed" | "has_recording" | "merged_from">;
}) {
  // R2: "Everything" reads the recording again (lib/keepMode.ts)
  const allBlocked = scan ? stageBlock(scan, state, "recipe_all") : null;
  const list = useRecipes((s) => s.list);
  const error = useRecipes((s) => s.error);
  const loading = useRecipes((s) => s.loading);
  const fetch = useRecipes((s) => s.fetch);
  const remove = useRecipes((s) => s.remove);
  const applyRecipe = useProcess((s) => s.applyRecipe);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<Recipe | null>(null);
  const [confirm, setConfirm] = useState<{ recipe: Recipe; scope: RecipeScope } | null>(null);
  const edits = state.edits.length;

  const apply = (recipe: Recipe, scope: RecipeScope) => {
    if (edits > 0) setConfirm({ recipe, scope });
    else void applyRecipe(pid, sid, recipe, scope);
  };

  return (
    <Panel
      title="Recipes"
      icon={ScrollText}
      aside={
        <Button size="xs" variant="ghost" icon={BookmarkPlus} onClick={() => setSaving(true)} disabled={!state.base} title={state.base ? "Save this scan's steps" : "Make the mesh first"}>
          Save as recipe…
        </Button>
      }
    >
      {error && !list ? (
        <Callout
          tone="danger"
          title="Couldn't load the recipes"
          actions={
            <Button size="xs" icon={RefreshCw} loading={loading} onClick={() => void fetch()}>
              Try again
            </Button>
          }
        >
          {error.detail}
          {error.hint ? <div className="mt-0.5 text-subtle">{error.hint}</div> : null}
        </Callout>
      ) : !list ? (
        <div className="space-y-2" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[92px]" />
          ))}
        </div>
      ) : list.length === 0 ? (
        <div className="text-sm text-muted">No recipes yet. Save this scan's steps to make one.</div>
      ) : (
        <ul className="space-y-2" aria-label="Recipes">
          {list.map((r) => (
            <RecipeRow key={r.id} recipe={r} canEdits={!!state.base} allBlocked={allBlocked} busy={busy} onApply={(scope) => apply(r, scope)} onDelete={() => setDeleting(r)} />
          ))}
        </ul>
      )}
      <Help className="mt-2.5">“Just the edits” keeps the mesh and replaces the edits. “Everything” runs Follow the camera, Straighten and Refine with lasers again with the recipe's settings first.</Help>
      {allBlocked ? <Help className="mt-1">“Everything”: {allBlocked}</Help> : null}

      <SaveRecipeDialog open={saving} onClose={() => setSaving(false)} pid={pid} sid={sid} state={state} />
      <ConfirmDialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={deleting ? `Delete the recipe “${deleting.name}”?` : ""}
        confirmLabel="Delete recipe"
        tone="danger"
        onConfirm={async () => {
          if (!deleting) return;
          const ok = await remove(deleting);
          if (ok) toast.success(`Deleted “${deleting.name}”`);
          return ok; // U6: a failed delete keeps the dialog open
        }}
      >
        Scans you already processed with it keep their meshes and history.
      </ConfirmDialog>
      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        title={confirm ? (confirm.scope === "all" ? `Run everything again with “${confirm.recipe.name}”?` : `Replace the edits with “${confirm.recipe.name}”?`) : ""}
        confirmLabel={confirm?.scope === "all" ? "Run everything again" : "Replace the edits"}
        onConfirm={async () => {
          if (confirm) return applyRecipe(pid, sid, confirm.recipe, confirm.scope);
        }}
      >
        {confirm?.scope === "all"
          ? `This starts the edits over: ${edits === 1 ? "your edit is" : `your ${fmtInt(edits)} edits are`} cleared, then the stages run with the recipe's settings and its edits follow.`
          : `The recipe's steps replace ${edits === 1 ? "your edit" : `your ${fmtInt(edits)} edits`}, including any deleted or kept selection. The mesh goes back to the starting mesh first.`}
      </ConfirmDialog>
    </Panel>
  );
}
