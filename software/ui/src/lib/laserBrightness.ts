/**
 * Each laser's brightness on the Settings page (config `[lasers] own_channels` and `brightness`).
 *
 * A laser's brightness is a percent of its family's level (the emitter sliders); a laser missing
 * from `brightness` gets 100. Only a build with two lasers in a family has anything to set: the
 * order within a family is the config order, vertical lasers from right to left, horizontal ones
 * from top to bottom. When each pair shares one wire (`own_channels` false) only the first laser of
 * a pair counts, so the page shows one slider for the pair.
 */
import type { LaserSettings } from "../api/types";
import { laserFamilies, laserLabel, type LaserFamily } from "./laserSet.ts";

/** The `lasers` section of a settings reply, or null when it has no list of lasers. */
export function laserSettingsOf(settings: Record<string, unknown> | null | undefined): LaserSettings | null {
  const l = settings?.lasers;
  if (!l || typeof l !== "object") return null;
  const o = l as Record<string, unknown>;
  if (!Array.isArray(o.lines) || !o.lines.every((x) => typeof x === "string")) return null;
  const out: LaserSettings = { lines: o.lines as string[] };
  if (typeof o.own_channels === "boolean") out.own_channels = o.own_channels;
  if (o.brightness && typeof o.brightness === "object" && !Array.isArray(o.brightness)) {
    const b: Record<string, number> = {};
    for (const [k, v] of Object.entries(o.brightness as Record<string, unknown>)) if (typeof v === "number" && Number.isFinite(v)) b[k] = v;
    out.brightness = b;
  }
  return out;
}

const PLACES: Record<LaserFamily, [string, string]> = { vertical: ["right", "left"], horizontal: ["top", "low"] };
const FAMILY: Record<LaserFamily, string> = { vertical: "Vertical", horizontal: "Horizontal" };

export type BrightnessRow = {
  name: string; // the laser whose brightness the slider sets
  label: string; // "Vertical, right"; "Vertical lasers" for a pair on one wire
  value: number; // percent, 0..100
  pair: boolean; // the slider sets both lasers of a pair (one wire)
};

/**
 * The brightness sliders for a laser set: null when no family has two lasers (nothing to set), or
 * when the engine is too old to keep a brightness per laser (no `own_channels`). A third laser of a
 * family has no channel of its own and gets no slider.
 */
export function brightnessRows(l: LaserSettings | null): BrightnessRow[] | null {
  if (!l || typeof l.own_channels !== "boolean") return null;
  const groups = laserFamilies(l.lines);
  if (groups.vertical.length < 2 && groups.horizontal.length < 2) return null;
  const level = (n: string) => Math.min(100, Math.max(0, l.brightness?.[n] ?? 100));
  const rows: BrightnessRow[] = [];
  for (const f of ["vertical", "horizontal"] as const) {
    const members = groups[f].slice(0, 2);
    if (members.length === 2 && !l.own_channels) {
      rows.push({ name: members[0], label: `${FAMILY[f]} lasers`, value: level(members[0]), pair: true });
      continue;
    }
    members.forEach((n, i) =>
      rows.push({ name: n, label: members.length === 2 ? `${FAMILY[f]}, ${PLACES[f][i]}` : laserLabel(n), value: level(n), pair: false }),
    );
  }
  return rows;
}
