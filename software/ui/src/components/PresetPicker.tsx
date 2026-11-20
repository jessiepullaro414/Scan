/**
 * "What are you scanning?" in the Scan page's setup (FEATURE_PLAN P3): one choice per preset, each with a card
 * on hover that says what it changes, why and what it costs. Every value the chosen preset sets is shown and
 * can be changed here; a value changed by hand is marked as changed from the preset and goes with Start
 * (APP_API.md `POST /api/scan/start`, `preset` and `preset_values`). The mesh detail acts at Make mesh: the
 * Process page shows the scan's preset and can change the value again.
 */
import { Boxes, Check } from "lucide-react";
import { useEffect, useId } from "react";
import type { PresetKey, PresetValues, ScanMode } from "../api/types";
import { GENERAL_ID, PRESET_KEYS, cleanHand, presetSummary, valueText } from "../lib/presets";
import { cn } from "../lib/util";
import { usePresets } from "../store/presets";
import { PresetCardContent, PresetHelp, PresetValueRow } from "./PresetBits";
import { Tooltip } from "./ui/Popover";

export function PresetPicker({ mode, setMode }: { mode: ScanMode; setMode: (m: ScanMode) => void }) {
  const list = usePresets((s) => s.list);
  const fetchList = usePresets((s) => s.fetchList);
  const chosen = usePresets((s) => s.chosen);
  const hand = usePresets((s) => s.hand);
  const choose = usePresets((s) => s.choose);
  const setHand = usePresets((s) => s.setHand);
  const titleId = useId();

  useEffect(() => {
    if (!list) void fetchList();
  }, [list, fetchList]);

  const preset = list?.find((p) => p.id === chosen) ?? list?.find((p) => p.id === GENERAL_ID) ?? null;
  // a preset that names a mode sets it on the Scan page; changing it there is a change by hand
  useEffect(() => {
    if (preset?.values.mode && preset.values.mode !== mode) setMode(preset.values.mode);
  }, [preset?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!list || !list.length || !preset) return null;

  const named = preset.values;
  const shown = PRESET_KEYS.filter((k) => k !== "mode" && (named[k] !== undefined || hand[k] !== undefined));
  const change = (k: PresetKey, v: PresetValues[PresetKey] | null) => {
    const next = { ...hand, [k]: v ?? undefined } as PresetValues;
    setHand(cleanHand(named, next));
  };

  return (
    <section className="border-b border-line" aria-labelledby={titleId}>
      <div className="flex h-11 items-center gap-2 px-4">
        <Boxes size={14} className="text-muted" aria-hidden />
        <h2 id={titleId} className="flex-1 text-xs font-semibold uppercase tracking-wider text-muted">
          What are you scanning?
        </h2>
      </div>
      <div className="space-y-3 px-4 pb-4">
        <div role="radiogroup" aria-label="What are you scanning?" className="space-y-1.5">
          {list.map((p) => {
            const on = p.id === preset.id;
            return (
              <div key={p.id} className="flex items-start gap-1">
                <Tooltip side="top" content={<PresetCardContent preset={p} />} className="min-w-0 flex-1">
                  <button
                    type="button"
                    role="radio"
                    aria-checked={on}
                    aria-label={`${p.name}: ${presetSummary(p)}`}
                    onClick={() => choose(p.id)}
                    className={cn(
                      "flex w-full items-start gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
                      on ? "border-accent-line bg-accent-soft/40" : "border-line bg-surface-2/50 hover:border-line-strong",
                    )}
                  >
                    <span className={cn("mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full border", on ? "border-accent bg-accent text-white" : "border-line-strong")}>
                      {on ? <Check size={10} strokeWidth={3} aria-hidden /> : null}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-xs font-medium text-fg">{p.name}</span>
                      <span className="block text-[11px] text-subtle">{presetSummary(p)}</span>
                    </span>
                  </button>
                </Tooltip>
                <PresetHelp preset={p} side="top" />
              </div>
            );
          })}
        </div>

        {named.mode ? (
          <p className="text-[11px] text-subtle">
            Tracking mode {valueText("mode", named.mode)} from the preset
            {mode !== named.mode ? <span className="font-medium text-warn">; changed by hand to {valueText("mode", mode)}</span> : null}. Choose it under Tracking mode.
          </p>
        ) : null}

        {shown.length ? (
          <div className="space-y-2.5 rounded-lg border border-line bg-surface-2/60 p-3" aria-label="What the preset sets">
            {shown.map((k) => {
              const isChanged = hand[k] !== undefined;
              const named_ = named[k];
              return (
                <PresetValueRow
                  key={k}
                  k={k}
                  value={(isChanged ? hand[k] : named_) as PresetValues[PresetKey]}
                  origin={isChanged ? (named_ !== undefined ? `Changed by hand from ${valueText(k, named_)}` : "Changed by hand") : "From the preset"}
                  onChange={(v) => change(k, v)}
                  onReset={isChanged ? () => change(k, null) : undefined}
                  note={k === "voxel_mm" ? "Used when the mesh is made. The Process page shows it and can change it again." : undefined}
                />
              );
            })}
          </div>
        ) : (
          <p className="text-[11px] text-subtle">Every setting stays as it is in the app.</p>
        )}
      </div>
    </section>
  );
}
