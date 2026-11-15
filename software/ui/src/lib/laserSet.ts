/**
 * The build's line lasers (config `[lasers] lines`): names, plain labels and overlay colours.
 *
 * A laser's name is its family, "vertical" or "horizontal" (how its line runs in the image),
 * optionally followed by "_" and anything ("vertical_2"). The standard scanner has two lasers; the
 * 4-laser build adds "vertical_2" (left of the cameras) and "horizontal_2" (low on the right).
 * Any other name still gets a label and a colour of its family.
 */

export type LaserFamily = "vertical" | "horizontal";

export const TWO_LASERS = ["vertical", "horizontal"] as const;
export const FOUR_LASERS = ["vertical", "horizontal", "vertical_2", "horizontal_2"] as const;

/** Bright colours that read on a grey camera image, one per laser; the second pair differs from the first. */
export const LASER_COLOR: Record<string, string> = {
  vertical: "#ff5a4f", // red
  horizontal: "#3ad3ff", // cyan
  vertical_2: "#ff6ad5", // magenta
  horizontal_2: "#e6ff4d", // yellow
};
const FAMILY_EXTRA: Record<LaserFamily, string[]> = {
  vertical: ["#ff9a3d", "#ffb3a8"], // orange, light red
  horizontal: ["#8f9bff", "#a8f0ff"], // periwinkle, light cyan
};

const ORDINAL: Record<string, string> = { "2": "second", "3": "third", "4": "fourth", "5": "fifth", "6": "sixth" };

/** The family of a laser name; null for a name that is not a laser line. */
export function laserFamily(name: string): LaserFamily | null {
  for (const f of ["vertical", "horizontal"] as const) if (name === f || name.startsWith(`${f}_`)) return f;
  return null;
}

/** "vertical" → "Vertical", "vertical_2" → "Vertical 2", "vertical_left" → "Vertical left". */
export function laserLabel(name: string): string {
  const fam = laserFamily(name);
  if (!fam) return name;
  const rest = name.slice(fam.length + 1).replace(/_/g, " ");
  const head = fam.charAt(0).toUpperCase() + fam.slice(1);
  return rest ? `${head} ${rest}` : head;
}

/** In a sentence: "the vertical", "the second vertical" (as the engine's guidance says it). */
export function laserWords(name: string): string {
  const fam = laserFamily(name);
  if (!fam) return name;
  const rest = name.slice(fam.length + 1);
  if (!rest) return fam;
  return ORDINAL[rest] ? `${ORDINAL[rest]} ${fam}` : `${fam} (${rest.replace(/_/g, " ")})`;
}

/** Short tag for tight spots: "V", "H", "V2", "H2". */
export function laserShort(name: string): string {
  const fam = laserFamily(name);
  if (!fam) return name.slice(0, 3);
  const rest = name.slice(fam.length + 1);
  return (fam === "vertical" ? "V" : "H") + (rest.length <= 2 ? rest : rest.charAt(0).toUpperCase());
}

/** The overlay colour of a laser (also for names this app does not know). */
export function laserColor(name: string, names: readonly string[] = FOUR_LASERS): string {
  if (LASER_COLOR[name]) return LASER_COLOR[name];
  const fam = laserFamily(name) ?? "horizontal";
  const others = names.filter((n) => !LASER_COLOR[n] && (laserFamily(n) ?? "horizontal") === fam);
  const k = Math.max(0, others.indexOf(name));
  return FAMILY_EXTRA[fam][k % FAMILY_EXTRA[fam].length];
}

/** The lasers of each family, in the given order. */
export function laserFamilies(names: readonly string[]): Record<LaserFamily, string[]> {
  const out: Record<LaserFamily, string[]> = { vertical: [], horizontal: [] };
  for (const n of names) {
    const f = laserFamily(n);
    if (f) out[f].push(n);
  }
  return out;
}

/** Margin points a line needs in a laser view (the engine's LINE_MIN_POINTS). */
export const LINE_MIN_POINTS = 15;

export type LaserRow = {
  key: string;
  label: string; // "Vertical 2 line", or "Vertical line 1 of 2" while the lines cannot be named
  color: string;
  points: number; // margin points
  ok: boolean;
  unnamed: boolean;
};

type LinesLike = {
  vertical: number;
  horizontal: number;
  vertical_uv?: number[];
  horizontal_uv?: number[];
  points?: Record<string, number | null>;
  found?: Record<string, number[]>;
};

/**
 * One row per configured laser for the live view, by family. Without per-laser counts (an older
 * server) the two families' counts; while a family's lasers cannot be named yet (a first
 * calibration of two lasers in a family) its lines found, "Vertical line 1 of 2", largest first.
 */
export function laserRows(lines: LinesLike, names: readonly string[]): LaserRow[] {
  const out: LaserRow[] = [];
  const pts = lines.points;
  const groups = laserFamilies(names.length ? names : TWO_LASERS);
  for (const f of ["vertical", "horizontal"] as const) {
    const members = groups[f];
    if (!members.length) continue;
    const famLabel = f === "vertical" ? "Vertical" : "Horizontal";
    if (!pts) {
      const n = f === "vertical" ? lines.vertical : lines.horizontal;
      out.push({ key: f, label: `${famLabel} line`, color: laserColor(f, names), points: n, ok: n >= LINE_MIN_POINTS, unnamed: false });
      continue;
    }
    if (members.every((m) => typeof pts[m] === "number")) {
      for (const m of members) {
        const n = pts[m] as number;
        out.push({ key: m, label: `${laserLabel(m)} line`, color: laserColor(m, names), points: n, ok: n >= LINE_MIN_POINTS, unnamed: false });
      }
      continue;
    }
    const found = lines.found?.[f] ?? [];
    members.forEach((_, k) => {
      const n = found[k] ?? 0;
      out.push({
        key: `${f}#${k}`,
        label: `${famLabel} line ${k + 1} of ${members.length}`,
        color: laserColor(members[0], names),
        points: n,
        ok: n >= LINE_MIN_POINTS,
        unnamed: true,
      });
    });
  }
  return out;
}

type ViewLike = {
  lines: { vertical: number; horizontal: number } | null;
  line_points?: Record<string, number | null> | null;
  lines_found?: Record<string, number[]> | null;
};

/** The laser counts of a captured view in a few characters ("V40 H33", "V40 V2 40 H60 H2 2", "V 40/30 H 40/20"). */
export function viewLaserSummary(view: ViewLike, names: readonly string[]): { text: string; ok: boolean; title: string } | null {
  if (!view.lines) return null;
  const pts = view.line_points;
  const groups = laserFamilies(names.length ? names : TWO_LASERS);
  const parts: string[] = [];
  const titles: string[] = [];
  let ok = true;
  for (const f of ["vertical", "horizontal"] as const) {
    const members = groups[f];
    if (!members.length) continue;
    if (!pts || (members.length === 1 && typeof pts[members[0]] !== "number")) {
      const n = f === "vertical" ? view.lines.vertical : view.lines.horizontal;
      parts.push(`${f === "vertical" ? "V" : "H"}${n}`);
      titles.push(`${f}: ${n}`);
      ok &&= n >= LINE_MIN_POINTS;
    } else if (members.every((m) => typeof pts[m] === "number")) {
      for (const m of members) {
        const n = pts[m] as number;
        const s = laserShort(m);
        parts.push(/\d$/.test(s) ? `${s} ${n}` : `${s}${n}`);
        titles.push(`${laserWords(m)}: ${n}`);
        ok &&= n >= LINE_MIN_POINTS;
      }
    } else {
      const found = view.lines_found?.[f] ?? [];
      const shown = members.map((_, k) => found[k] ?? 0);
      parts.push(`${f === "vertical" ? "V" : "H"} ${shown.join("/")}`);
      titles.push(`${f} lines (not named yet): ${shown.join(" and ")}`);
      ok &&= shown.every((n) => n >= LINE_MIN_POINTS);
    }
  }
  return { text: parts.join(" "), ok, title: `Laser points on the margin: ${titles.join(", ")}` };
}

export type LaserChoice = "two" | "four" | "custom";

function same(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

/** Which choice of the Settings page a configured list is: exactly the two or the four lasers, else custom. */
export function laserChoice(lines: readonly string[] | null | undefined): LaserChoice {
  if (!lines) return "two";
  if (same(lines, TWO_LASERS)) return "two";
  if (same(lines, FOUR_LASERS)) return "four";
  return "custom";
}
