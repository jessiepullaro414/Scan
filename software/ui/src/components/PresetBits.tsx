/**
 * Presets (FEATURE_PLAN P3), the parts the Scan page and the Process page share: the hover card of a preset
 * (what it changes, why, what it costs; as "Where to smooth" does it) and one value's row, which shows the
 * value, where it comes from (the preset, General, or changed by hand) and lets the user change it.
 */
import { CircleHelp, RotateCcw } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import type { Preset, PresetKey, PresetValues, ProcessQuality, ThinWalls } from "../api/types";
import { NumberField } from "../pages/process/bits";
import { PRESET_LABELS, VOXEL_MAX_MM, VOXEL_MIN_MM, valueText } from "../lib/presets";
import { QUALITIES, QUALITY_TEXT } from "../lib/quality";
import { cn } from "../lib/util";
import { useRecipes } from "../store/recipes";
import { Segmented, Select } from "./ui/Form";
import { Tooltip } from "./ui/Popover";

/** The card of one preset: its name, then what it changes, why, and what it costs. */
export function PresetCardContent({ preset }: { preset: Preset }) {
  const card = preset.card;
  return (
    <div className="max-w-[320px] space-y-1.5">
      <div className="font-semibold">{preset.name}</div>
      {card ? (
        <>
          <div>
            <span className="font-medium text-fg">What it changes: </span>
            <span className="text-muted">{card.changes}</span>
          </div>
          <div>
            <span className="font-medium text-fg">Why: </span>
            <span className="text-muted">{card.why}</span>
          </div>
          {card.costs ? (
            <div>
              <span className="font-medium text-fg">What it costs: </span>
              <span className="text-muted">{card.costs}</span>
            </div>
          ) : null}
        </>
      ) : (
        <div className="text-muted">{preset.description || "Your own preset."}</div>
      )}
    </div>
  );
}

/** The plain text of a card, for screen readers and titles. */
export function presetCardText(p: Preset): string {
  const c = p.card;
  if (!c) return `${p.name}: ${p.description || "your own preset"}`;
  return `${p.name}. What it changes: ${c.changes} Why: ${c.why}${c.costs ? ` What it costs: ${c.costs}` : ""}`;
}

/** A small "?" that shows the preset's card on hover or focus. */
export function PresetHelp({ preset, side = "top" }: { preset: Preset; side?: "top" | "bottom" | "right" }) {
  return (
    <Tooltip side={side} content={<PresetCardContent preset={preset} />}>
      <button type="button" className="grid h-5 w-5 place-items-center rounded text-subtle hover:text-fg" aria-label={presetCardText(preset)}>
        <CircleHelp size={13} aria-hidden />
      </button>
    </Tooltip>
  );
}

const THIN_WALLS: { value: ThinWalls; label: string; title: string }[] = [
  { value: "auto", label: "Auto", title: "On for scans without markers, as the app's setting does" },
  { value: "on", label: "On", title: "Keeps thin walls and edges from swelling" },
  { value: "off", label: "Off", title: "The mesh model without the thin-wall band" },
];

/** One value: its label, a control to change it, where it comes from, and a way back to the preset's value. */
export function PresetValueRow({
  k,
  value,
  origin,
  onChange,
  onReset,
  disabled,
  note,
}: {
  k: PresetKey;
  value: PresetValues[PresetKey];
  /** "From the preset", "Changed by hand from 0.5 mm", "As in General". */
  origin: string;
  onChange: (v: PresetValues[PresetKey]) => void;
  /** Back to the preset's value; absent when the value is not changed. */
  onReset?: () => void;
  disabled?: boolean;
  note?: string;
}) {
  const recipes = useRecipes((s) => s.list);
  const fetchRecipes = useRecipes((s) => s.fetch);
  useEffect(() => {
    if (k === "recipe" && !recipes) void fetchRecipes();
  }, [k, recipes, fetchRecipes]);
  const changed = !!onReset;
  const label = PRESET_LABELS[k];
  let control: ReactNode;
  if (k === "voxel_mm" || k === "check_tolerance_mm") {
    const tol = k === "check_tolerance_mm";
    control = (
      <NumberField
        label={label}
        value={typeof value === "number" ? value : tol ? 0.3 : 1}
        onChange={(v) => onChange(v)}
        min={tol ? 0.01 : VOXEL_MIN_MM}
        max={tol ? 100 : VOXEL_MAX_MM}
        step={tol ? 0.05 : 0.25}
        unit="mm"
        disabled={disabled}
        className="w-28"
      />
    );
  } else if (k === "quality") {
    control = (
      <Segmented<ProcessQuality>
        label={label}
        size="sm"
        value={(value as ProcessQuality) ?? "standard"}
        onChange={(v) => onChange(v)}
        disabled={disabled}
        options={QUALITIES.map((q) => ({ value: q, label: QUALITY_TEXT[q].short, title: `${QUALITY_TEXT[q].label}: ${QUALITY_TEXT[q].help}` }))}
      />
    );
  } else if (k === "thin_walls") {
    control = <Segmented<ThinWalls> label={label} size="sm" value={(value as ThinWalls) ?? "auto"} onChange={(v) => onChange(v)} disabled={disabled} options={THIN_WALLS} />;
  } else if (k === "recipe") {
    control = (
      <Select aria-label={label} value={String(value ?? "default")} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {(recipes ?? [{ id: String(value ?? "default"), name: String(value ?? "Default") }]).map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </Select>
    );
  } else {
    control = <span className="text-xs text-fg">{valueText(k, value)}</span>;
  }
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-medium text-muted">{label}</span>
        <span className={cn("text-[11px]", changed ? "font-medium text-warn" : "text-subtle")}>{origin}</span>
      </div>
      <div className="flex items-center gap-2">
        {control}
        {onReset ? (
          <button
            type="button"
            className="inline-flex items-center gap-1 text-[11px] font-medium text-accent hover:underline disabled:opacity-50"
            onClick={onReset}
            disabled={disabled}
            title="Back to the preset's value"
          >
            <RotateCcw size={11} aria-hidden /> Preset's value
          </button>
        ) : null}
      </div>
      {note ? <p className="text-[11px] text-subtle">{note}</p> : null}
    </div>
  );
}
