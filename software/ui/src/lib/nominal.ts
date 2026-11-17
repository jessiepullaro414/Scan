/**
 * Suggested nominal values as the viewer shows them (FEATURE_PLAN.md C3; APP_API.md "Nominal values"): the chip
 * beside a measured size ("Ø 14?"), its hover text, an approved value ("nominal Ø 14") next to the measured one, a
 * value typed by hand checked as the engine checks it, and which offers the user has ignored. After C3 (the owner's
 * decisions): the words of the switch for offers that only a known bias allows (off by default), and of a kept
 * relation's move in the CAD file (listed only, or one of its two fits moves to suit it).
 *
 * **Ignored offers** are kept in this browser only (localStorage, one entry per scan, fit or relation and value),
 * with the measured value they were ignored for: when the fit is measured again (a different value) the offer shows
 * again. They are not kept with the scan, and nothing is sent to the engine: ignoring changes no file. Storage
 * that is not there (a private window) only means an ignored chip comes back after a reload.
 *
 * Pure functions (nominal.test.ts) but `localIgnoreStore`, which wraps the browser's storage.
 */
import type { ApprovedNominal, KeptRelation, MoveChoice, NominalAnswer, NominalOffer, NominalSuggestion, NominalValueName } from "../api/types";

/** What makes sense for each value (as the engine's `ohs.measure.features.NOMINAL_LIMITS`): lowest, highest, and
 * whether the lowest itself is left out. */
export const NOMINAL_LIMITS: Record<NominalValueName, [number, number, boolean]> = {
  diameter: [0, 10_000, true],
  radius: [0, 5_000, true],
  angle: [0, 90, false],
  distance: [0, 10_000, false],
  centre_distance: [0, 10_000, false],
};

/** What every offer's hover text ends with: where the numbers come from. */
export const NOMINAL_NOTE =
  "An offer is only an offer: accept it, enter another value, or ignore it. The measured value stays as it is; an accepted value is kept beside it and used in the CAD files. The known biases are simulator numbers: the real scanner's are still to be measured.";

/** 14, 14.5, 10.25: a nominal value as written (no trailing zeros). */
export function fmtNominal(x: number): string {
  return x.toFixed(6).replace(/\.?0+$/, "");
}

/** "Ø 14", "R 7", "parallel", "90°", "10 mm": a nominal value as the card names it. */
export function nominalText(value: NominalValueName, nominal: number, word: string | null = null): string {
  if (word) return word;
  if (value === "diameter") return `Ø ${fmtNominal(nominal)}`;
  if (value === "radius") return `R ${fmtNominal(nominal)}`;
  if (value === "angle") return `${fmtNominal(nominal)}°`;
  return `${fmtNominal(nominal)} mm`;
}

/** The chip of an offer: "Ø 14?", "parallel?". */
export function chipText(value: NominalValueName, s: Pick<NominalSuggestion, "nominal" | "word">): string {
  return `${nominalText(value, s.nominal, s.word)}?`;
}

/** "14.02 ± 0.03 mm", "24.976 ± 0.001 mm" (at least two decimals, more for a small ± as the relation rows show it): a
 * measured value as an approval shows it. */
export function measuredText(x: number | null, sigma: number | null, unit: "mm" | "deg"): string {
  if (x === null) return "not measured now";
  const u = unit === "deg" ? "°" : " mm";
  const s = sigma === null ? 0.01 : Math.abs(sigma);
  const d = s >= 0.01 || s === 0 ? 2 : s >= 0.001 ? 3 : 4;
  return `${x.toFixed(d)}${sigma === null ? "" : ` ± ${sigma.toFixed(d)}`}${u}`;
}

/** The lines of a chip's hover card: the engine's sentence, what a bias offer means, which poses' biases apply. */
export function suggestionTip(s: NominalSuggestion, answer: Pick<NominalAnswer, "poses" | "poses_reason"> | null): string[] {
  const lines = [s.sentence];
  if (s.allowed_by === "bias")
    lines.push("Offered only because of a known bias: the measured value alone, with its ±, would not allow it. The bias is a simulator number.");
  if (answer) lines.push(`Biases ${answer.poses === "true" ? "at true poses" : "with tracking"}: ${answer.poses_reason}.`);
  lines.push(NOMINAL_NOTE);
  return lines;
}

export type Parsed = { ok: true; nominal: number } | { ok: false; error: string };

/** A value typed for `value`: a number ("14", "14,5", "Ø 14", "90°" are read) that makes sense for that value. */
export function parseNominal(text: string, value: NominalValueName): Parsed {
  const t = text.trim().replace(/^[ØøR⌀]\s*/u, "").replace(/\s*(mm|°|deg)$/i, "").replace(",", ".");
  if (!t) return { ok: false, error: "Enter a number." };
  const x = Number(t);
  if (!Number.isFinite(x)) return { ok: false, error: `“${text.trim()}” is not a number.` };
  const [lo, hi, open] = NOMINAL_LIMITS[value];
  const unit = value === "angle" ? "°" : " mm";
  if (x > hi || x < lo || (open && x <= lo)) {
    const span = open ? `more than ${fmtNominal(lo)}` : `at least ${fmtNominal(lo)}`;
    return { ok: false, error: `A ${value.replace("_", " ")} must be ${span} and at most ${fmtNominal(hi)}${unit}.` };
  }
  return { ok: true, nominal: x };
}

/** The approval of a fit's size (a diameter or a radius: one size), or null. */
export function sizeApproval(nominals: ApprovedNominal[] | undefined): ApprovedNominal | null {
  return (nominals ?? []).find((n) => n.value === "diameter" || n.value === "radius") ?? null;
}

/** The approval of one relation value, or null. */
export function valueApproval(nominals: ApprovedNominal[] | undefined, value: NominalValueName): ApprovedNominal | null {
  return (nominals ?? []).find((n) => n.value === value) ?? null;
}

/** The offer for one value, or null. */
export function offerFor(answer: NominalAnswer | null | undefined, value: NominalValueName): NominalOffer | null {
  return answer?.offers.find((o) => o.value === value) ?? null;
}

/** "nominal Ø 14": an approved value as the card shows it. */
export function approvedText(n: Pick<ApprovedNominal, "value" | "nominal" | "word">): string {
  return `nominal ${nominalText(n.value, n.nominal, n.word)}`;
}

/** The hover text of an approved value: what it is, against which measurement, and why it needs checking. */
export function approvedTip(n: ApprovedNominal): string {
  const how = n.source === "suggested" ? "Accepted as offered" : "Entered by hand";
  const parts = [`${how} on ${n.approved.replace("T", " ")}, against ${measuredText(n.measured, n.sigma, n.unit)}.`];
  if (n.measured_now !== null && n.measured_now !== n.measured) parts.push(`Measured now: ${measuredText(n.measured_now, n.sigma_now, n.unit)}.`);
  if (n.check_again && n.check_reason) parts.push(`${n.check_reason[0].toUpperCase()}${n.check_reason.slice(1)}`);
  if (n.with_bias) parts.push("Approved with the offers a known bias allows shown: when it is measured again, the known bias still counts for it.");
  parts.push("It is used in the CAD files; the measured value stays as it is.");
  return parts.join(" ");
}

// ------------------------------------------------------------------ offers a known bias alone allows (off unless asked)
/** The hover card of the switch "Offers a known bias allows" (a viewer preference, store/ui.ts). */
export const BIAS_SWITCH_HELP =
  "A known bias: on the simulator some features read consistently small or large, the same on every scan (small bosses about 0.3 mm small in diameter, some holes larger or smaller). The ± does not show it. These numbers are not yet measured on the real scanner. Off (the default): a value is offered only where the ± alone allows it, so a Ø 14.3 boss stays Ø 14.3. Turned on: values that only a known bias allows are offered too, in the warning colour, each saying which bias. An approval keeps the rule it was made under.";

/** "Ø 14.5", "0° or 1°": the values a known bias alone would allow, as the card names them. */
function heldBack(offer: NominalOffer): string {
  return (offer.bias_only ?? []).map((x) => nominalText(offer.value, x)).join(" or ");
}

/** A hint where an offer is held back because the bias switch is off: "A known bias alone would allow Ø 14.5: turn on
 * “Offers a known bias allows” to see it."; null when nothing is held back, the switch is on, or the server is older. */
export function biasOnlyHint(offer: NominalOffer | null, withBias: boolean): string | null {
  if (!offer || withBias || !offer.bias_only?.length) return null;
  return `A known bias alone would allow ${heldBack(offer)}: turn on “Offers a known bias allows” to see it.`;
}

// ------------------------------------------------------------------ a relation's move in the CAD file
/** The hover card of a kept relation's "In the CAD file" choice. */
export const MOVE_HELP =
  "Listed only (the default): the approved values are named in the CAD file's notes and no shape moves. Move one of the two: the CAD file gets a copy of that fit turned about its own point and slid, the smallest change that makes the approved angle and distance hold; the other stays as measured. Moves are applied in the order they were chosen: a shape is moved by one relation only, and one that an earlier move was measured against stays where it is (the notes say when a move can't be applied, and why). The measured fits and the part's frame never change.";

/** What a kept relation's move is: "listed" when none is kept. */
export function moveChoice(r: Pick<KeptRelation, "move">): MoveChoice {
  return r.move?.shape ?? "listed";
}

/**
 * A fit's name short enough for a segmented button (the whole name is in its title and in the sentence under
 * the buttons). Ten characters: with two names of sixteen the three buttons ran past the card's edge.
 */
const shortName = (name: string) => (name.length > 10 ? `${name.slice(0, 9).trimEnd()}…` : name);

/** The choices for a kept relation's move: listed only, or move the first or the second fit. */
export function moveOptions(aName: string, bName: string): { value: MoveChoice; label: string; title: string }[] {
  return [
    { value: "listed", label: "Listed only", title: moveText("listed", aName, bName) },
    { value: "a", label: `Move ${shortName(aName)}`, title: moveText("a", aName, bName) },
    { value: "b", label: `Move ${shortName(bName)}`, title: moveText("b", aName, bName) },
  ];
}

/** What a move choice does, in a sentence. */
export function moveText(choice: MoveChoice, aName: string, bName: string): string {
  if (choice === "listed") return "Listed in the CAD file's notes only; no shape moves.";
  const [moved, fixed] = choice === "a" ? [aName, bName] : [bName, aName];
  return `In the CAD file, a copy of ${moved} is turned and slid to suit ${fixed}; the measured fits stay as they are.`;
}

// ------------------------------------------------------------------ ignored offers (this browser only)
export type IgnoreStore = { read: () => Record<string, string>; write: (m: Record<string, string>) => void };

/** The key of one ignored offer: the scan, the fit or relation, the value. */
export const ignoreKey = (scan: string, id: string, value: NominalValueName) => `${scan}/${id}/${value}`;
/** The measured value an offer was ignored for, as kept (to a millionth: a new measurement differs). */
export const measuredKey = (x: number) => x.toFixed(6);

/** Whether the offer for this measured value was ignored. */
export function isIgnored(store: IgnoreStore, key: string, measured: number): boolean {
  return store.read()[key] === measuredKey(measured);
}

/** Ignore the offer for this measured value (until the value is measured again), or show it again (`on` false). */
export function setIgnored(store: IgnoreStore, key: string, measured: number, on = true): void {
  const m = { ...store.read() };
  if (on) m[key] = measuredKey(measured);
  else delete m[key];
  store.write(m);
}

const STORAGE_KEY = "ohs.nominal.ignored";

/** The browser's storage for ignored offers; reads as empty and writes nothing where it is not there. */
export function localIgnoreStore(): IgnoreStore {
  return {
    read: () => {
      try {
        const v = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}");
        return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, string>) : {};
      } catch {
        return {};
      }
    },
    write: (m) => {
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(m));
      } catch {
        /* not kept: the chip comes back after a reload */
      }
    },
  };
}

/** A store in memory (tests, and where the browser keeps none). */
export function memoryIgnoreStore(): IgnoreStore {
  let m: Record<string, string> = {};
  return { read: () => m, write: (x) => void (m = { ...x }) };
}
