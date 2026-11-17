/**
 * The demo engine's kept fits for CAD (APP_API.md "For CAD"): enough for the viewer's "For CAD" group to work in
 * demo mode. What it does, and what it leaves out:
 *
 * - `GET .../features/cad` answers as the engine does from the kept fits (in memory): how many of each kind, the
 *   frame (the part's when one is kept, out of date or not, else the scan's own), a warning for each fit made on
 *   an earlier mesh and for an out-of-date frame, the download names from the scan's name, and `reason` without
 *   kept fits. The notes are not the engine's writer's (the demo has none): one says so, one says when there is no
 *   part frame, one names each fit kept with no size. With `boundary=outline` it counts the planes that have an
 *   outline, their points (at most 5,000, not simplified) and their holes as left out;
 * - approved nominal values (features.ts, nominal.ts): the summary's `nominal` lists the approved sizes as used and
 *   the approved relations as listed, not applied, each with a note as the engine writes it; with
 *   `measured_only=true` none. A relation whose move is chosen is listed under `moves` as not applied, with the
 *   reason that the demo moves no shape (the engine's rules are ohs/cad/moves.py). The fixed files below do not
 *   change with them;
 * - `GET .../features/cad/fits.step` and `.../fits.FCMacro` serve small fixed texts, not the real files: a STEP
 *   file with an empty DATA section and a macro that only prints a line in FreeCAD, both saying they come from the
 *   demo. No name goes into them. The sizes in the summary are those texts'.
 *
 * Plain functions on the stored records, checked on their own (cad.test.ts).
 */
import type { CadBoundary, CadNominal, CadSummary, KeptFeature, KeptRelation, PartFrameRecord } from "../types";
import type { MockOutline } from "./extent.ts";
import { frameState } from "./frame.ts";
import { HttpError } from "./host.ts";

const NO_FITS = "this scan has no kept fits to export";
const NO_FITS_HINT = "fit planes, cylinders or holes in the viewer first (each fit is kept with the scan), then download them for CAD";
const MAX_POINTS = 5000;

/** The scan's name as the start of a download name (as `ohs/server/cad.py` makes it). */
export function cadFileStem(name: string): string {
  const stem = name.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[._-]+|[._-]+$/g, "");
  return stem.slice(0, 80) || "scan";
}

export function cadBoundary(v: unknown): CadBoundary {
  if (v === undefined || v === null || v === "rectangle") return "rectangle";
  if (v === "outline") return "outline";
  throw new HttpError(422, `boundary must be 'rectangle' or 'outline', got '${String(v)}'`, "Leave it out for each plane's rectangle, or give 'outline'.");
}

const STEP_TEXT = [
  "ISO-10303-21;",
  "HEADER;",
  "FILE_DESCRIPTION(('OpenHandScan demo: not a real STEP model, the demo engine has no STEP writer'),'2;1');",
  "FILE_NAME('demo','',(''),(''),'OpenHandScan demo','OpenHandScan demo','');",
  "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));",
  "ENDSEC;",
  "DATA;",
  "ENDSEC;",
  "END-ISO-10303-21;",
  "",
].join("\n");

function macroText(counts: CadSummary["counts"]): string {
  return [
    "# OpenHandScan demo: the demo engine has no FreeCAD macro writer.",
    `# With the real engine this macro builds the kept fits (${counts.plane} planes, ${counts.cylinder} cylinders, ${counts.hole} holes) as named objects.`,
    "import FreeCAD as App",
    "",
    'App.Console.PrintMessage("OpenHandScan demo macro: nothing to build.\\n")',
    "",
  ].join("\n");
}

/** What the demo keeps of a scan's fits (features.ts). */
export type CadStored = {
  features: Omit<KeptFeature, "stale">[];
  outlines: Map<string, NonNullable<MockOutline>>;
  frame: PartFrameRecord | null;
  relations?: KeptRelation[];
};

/** Whether the query asks for the measured values only (`measured_only=true`). */
export function cadMeasuredOnly(v: unknown): boolean {
  return v === true || v === "true" || v === "1";
}

const num = (x: number) => x.toFixed(6).replace(/\.?0+$/, "");

/** The approved values the demo's summary lists: approved sizes as used, relations listed, not applied. "Check
 * again" is the engine's (features.ts shows it); the demo's summary does not work it out. */
function cadNominal(s: CadStored, measuredOnly: boolean): { nominal: CadNominal; notes: string[] } {
  const nominal: CadNominal = { measured_only: measuredOnly, sizes_used: 0, sizes: [], relations_listed: 0, relations_applied: 0, relations: [], relations_note: null, moves: [] };
  const notes: string[] = [];
  if (measuredOnly) return { nominal, notes };
  for (const f of s.features) {
    const n = (f.nominals ?? []).find((x) => x.value === "diameter" || x.value === "radius");
    if (!n || f.kind === "plane" || f.shape.kind === "plane") continue;
    const measured = n.value === "diameter" ? f.shape.diameter_mm : f.shape.radius_mm;
    const sigma = n.value === "diameter" ? f.shape.diameter_sigma_mm : f.shape.radius_sigma_mm;
    nominal.sizes.push({ id: f.id, name: f.name, value: n.value as "diameter" | "radius", nominal: n.nominal, measured, sigma, check_again: false });
    notes.push(`${f.name}: nominal ${n.value} ${num(n.nominal)} mm (measured ${measured.toFixed(2)} +/- ${sigma.toFixed(2)} mm)`);
  }
  const moving = (s.relations ?? []).filter((r) => r.move && (r.nominals ?? []).length).sort((x, y) => x.move!.order - y.move!.order);
  for (const r of moving) {
    const names = [r.a, r.b].map((id) => s.features.find((f) => f.id === id)?.name ?? null);
    const [moved, fixed] = r.move!.shape === "a" ? names : [names[1], names[0]];
    nominal.moves!.push({
      id: r.id,
      name: r.name,
      shape: r.move!.shape,
      moved_id: r.move!.shape === "a" ? r.a : r.b,
      moved,
      fixed_id: r.move!.shape === "a" ? r.b : r.a,
      to_suit: fixed,
      applied: false,
      turn_deg: 0,
      slide_mm: 0,
      slide: [0, 0, 0],
      values: [],
      holds: false,
      check_again: false,
      reason: "the demo engine moves no shape: its files are small fixed texts",
      warnings: [],
      sentence: "",
    });
  }
  for (const r of s.relations ?? [])
    for (const n of r.nominals ?? []) {
      nominal.relations.push({ id: r.id, name: r.name, value: n.value, nominal: n.nominal, word: n.word, check_again: false, move: r.move?.shape ?? "listed", applied: false });
      notes.push(`${r.name}: approved ${n.word ?? `${n.value.replace("_", " ")} ${num(n.nominal)}${n.unit === "deg" ? " deg" : " mm"}`}; not applied to the geometry`);
    }
  nominal.sizes_used = nominal.sizes.length;
  nominal.relations_listed = nominal.relations.length;
  if (nominal.relations_listed)
    nominal.relations_note =
      "approved relations listed only (the default) are named in the notes and not applied to the geometry: choose which of a relation's two fits moves to suit it, and the export moves a copy of that fit (the measured fits stay as they are)";
  return { nominal, notes };
}

/** `GET .../features/cad`: what the demo's two files would hold. */
export function mockCadSummary(s: CadStored, boundary: CadBoundary, version: number | null, scanName: string, measuredOnly = false): CadSummary {
  const stem = cadFileStem(scanName);
  const counts = { plane: 0, cylinder: 0, hole: 0, point: 0 };
  for (const f of s.features) counts[f.kind] += 1;
  const warnings: string[] = [];
  const stale = s.features.filter((f) => version === null || f.mesh_version !== version);
  for (const f of stale)
    warnings.push(`"${f.name}" was fitted on an earlier mesh (version ${f.mesh_version}${version !== null ? `, the mesh is now version ${version}` : ""}): exported as it was kept; fit it again for this mesh's numbers`);
  let part = false;
  if (s.frame) {
    part = true;
    const st = frameState(s.frame, s.features);
    if (st.out_of_date) warnings.push(`the part's frame is out of date (${st.out_of_date_reasons.join("; ")}): used as it is; build it again for the fits as they are now`);
  }
  const notes = ["the demo engine has no STEP or FreeCAD writer: the files are small fixed texts"];
  for (const f of s.features)
    if (f.extent === null && f.kind !== "hole") notes.push(`${f.name}: kept with no measured size (before extents existed): written at a stated size; fit it again for its real size`);
  if (!part) notes.push("no part frame: the shapes are in the scan's own frame (wherever the scanner started), and no part axes are written");
  const nom = cadNominal(s, measuredOnly);
  notes.push(...nom.notes);
  let outline: CadSummary["outline"] = null;
  if (boundary === "outline") {
    const planes = s.features.filter((f) => f.kind === "plane" && s.outlines.has(f.id));
    let points = 0,
      holes = 0;
    for (const f of planes) {
      const o = s.outlines.get(f.id)!;
      points += o.outer.reduce((n, l) => n + l.length, 0) + o.holes.reduce((n, l) => n + l.length, 0);
      holes += o.holes.length;
    }
    outline = { planes: planes.length, points: Math.min(points, MAX_POINTS), holes_kept: 0, holes_left_out: holes };
  }
  const empty = s.features.length === 0;
  return {
    boundary,
    n_fits: s.features.length,
    counts,
    frame: part ? "part" : "scan",
    frame_label: part ? "the part's frame" : "the scan's own frame",
    notes: empty ? [] : notes,
    warnings,
    stale: stale.map((f) => f.id),
    outline: empty ? null : outline,
    step_bytes: empty ? null : STEP_TEXT.length,
    macro_bytes: empty ? null : macroText(counts).length,
    filenames: { step: `${stem}-fits.step`, macro: `${stem}-fits.FCMacro` },
    mesh_version: version,
    reason: empty ? `${NO_FITS}: ${NO_FITS_HINT}` : null,
    nominal: nom.nominal,
  };
}

/** `GET .../features/cad/{name}`: the demo's fixed text for `fits.step` or `fits.FCMacro`. */
export function mockCadFile(s: CadStored, name: string, boundary: CadBoundary, version: number | null, scanName: string): Response {
  if (name !== "fits.step" && name !== "fits.FCMacro") throw new HttpError(404, `No CAD file “${name}”`, "Available: fits.step, fits.FCMacro.");
  if (!s.features.length) throw new HttpError(409, NO_FITS, NO_FITS_HINT);
  const sum = mockCadSummary(s, boundary, version, scanName);
  const step = name === "fits.step";
  return new Response(step ? STEP_TEXT : macroText(sum.counts), {
    status: 200,
    headers: {
      "content-type": step ? "model/step" : "text/x-python",
      "content-disposition": `attachment; filename="${step ? sum.filenames.step : sum.filenames.macro}"`,
    },
  });
}

