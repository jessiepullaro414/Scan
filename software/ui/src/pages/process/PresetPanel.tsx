/**
 * The scan's preset on the Process page (FEATURE_PLAN P3): which preset the scan carries, the values it acts
 * with at Make mesh (the mesh detail and thin walls always; quality, recipe and the Check tolerance when the
 * preset names them or they were changed), each marked as from the preset, as in General, or changed by hand,
 * and each can be changed here (APP_API.md `PUT .../preset`). A scan made before presets, or whose preset is
 * gone, is made as General, and says so.
 */
import { Boxes } from "lucide-react";
import { useEffect, useRef } from "react";
import type { PresetKey, PresetValues } from "../../api/types";
import { PresetHelp, PresetValueRow } from "../../components/PresetBits";
import { Select } from "../../components/ui/Form";
import { GENERAL_ID, effectiveValue, originText, valueOrigin } from "../../lib/presets";
import { useCheckPrefs } from "../../store/check";
import { presetKey, usePresets } from "../../store/presets";
import { useRecipes } from "../../store/recipes";
import { Help } from "./bits";

const ALWAYS: PresetKey[] = ["voxel_mm", "thin_walls"];
const WHEN_SET: PresetKey[] = ["quality", "recipe", "check_tolerance_mm"];

export function PresetPanel({ pid, sid, busy }: { pid: string; sid: string; busy: boolean }) {
  const view = usePresets((s) => s.scans[presetKey(pid, sid)]);
  const list = usePresets((s) => s.list);
  const fetchList = usePresets((s) => s.fetchList);
  const fetchScan = usePresets((s) => s.fetchScan);
  const setScan = usePresets((s) => s.setScan);
  const recipes = useRecipes((s) => s.list);
  const setTolerance = useCheckPrefs((s) => s.setTolerance);
  const tolFor = useRef<string | null>(null);

  useEffect(() => {
    void fetchScan(pid, sid);
    if (!list) void fetchList();
  }, [pid, sid]); // eslint-disable-line react-hooks/exhaustive-deps

  // a preset that names the Check tolerance: the Check panel starts with it (once per scan)
  const tol = view?.values.check_tolerance_mm ?? null;
  useEffect(() => {
    const key = presetKey(pid, sid);
    if (tol !== null && tolFor.current !== key) {
      tolFor.current = key;
      setTolerance(tol);
    }
  }, [tol, pid, sid, setTolerance]);

  if (!view) return null;
  const preset = list?.find((p) => p.id === view.id);
  const recipeName = (id: string) => recipes?.find((r) => r.id === id)?.name;
  const keys = [...ALWAYS, ...WHEN_SET.filter((k) => valueOrigin(view, k) !== "general")];
  const change = (k: PresetKey, v: PresetValues[PresetKey] | null) => void setScan(pid, sid, { values: { [k]: v } });

  return (
    <div className="mb-3 space-y-2.5 rounded-xl border border-line bg-surface-2/50 p-3" aria-label="The scan's preset">
      <div className="flex items-center gap-2">
        <Boxes size={14} className="shrink-0 text-muted" aria-hidden />
        <span className="text-xs font-medium text-muted">Preset</span>
        {preset ? <PresetHelp preset={preset} side="bottom" /> : null}
        <div className="min-w-0 flex-1">
          <Select
            aria-label="The scan's preset"
            value={view.missing ? "" : view.id}
            disabled={busy}
            onChange={(e) => e.target.value && void setScan(pid, sid, { preset_id: e.target.value })}
          >
            {view.missing ? <option value="">{view.name} (no longer in the app)</option> : null}
            {(list ?? [{ id: GENERAL_ID, name: "General" }]).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </div>
      </div>
      {view.note ? <p className="text-[11.5px] font-medium text-warn">{view.note}</p> : null}
      <div className="space-y-2.5">
        {keys.map((k) => {
          const changed = valueOrigin(view, k) === "changed";
          return (
            <PresetValueRow
              key={k}
              k={k}
              value={effectiveValue(view, k) as PresetValues[PresetKey]}
              origin={originText(view, k, recipeName)}
              onChange={(v) => change(k, v)}
              onReset={changed ? () => change(k, null) : undefined}
              disabled={busy}
            />
          );
        })}
      </div>
      <Help>The next Make mesh and Track use these values; Fuse uses the mesh detail unless you pick another below.</Help>
    </div>
  );
}
