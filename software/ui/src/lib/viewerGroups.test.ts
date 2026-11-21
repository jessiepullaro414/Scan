/**
 * Checks for the viewer's side panel (UX_PLAN U11, `npm test`): four folding groups in the reviewer's order, the
 * first open; a tool turned on opens the group its panel is in; the part's axes are offered only once a plane is
 * saved. Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import { VIEWER_GROUPS, axesEntry, groupOfTool, initialOpen, openFor } from "./viewerGroups.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`viewerGroups.test: ${what}`);
}

// ---------------------------------------------------------------- the groups, in order
check(VIEWER_GROUPS.map((g) => g.id).join(",") === "good,measure,cad,export", `the four groups in order (${VIEWER_GROUPS.map((g) => g.id).join(",")})`);
check(VIEWER_GROUPS.map((g) => g.title).join(" | ") === "Is it good? | Measure | Compare with CAD | Export", "the groups' titles, the plan's words");
const sections = (id: string) => VIEWER_GROUPS.find((g) => g.id === id)!.sections.join(", ");
check(sections("good") === "Scan check, Processing report", `Is it good?: ${sections("good")}`);
check(sections("measure") === "Measurements, Fitted shapes, The part's axes, Sections, Flush along a seam", `Measure: ${sections("measure")}`);
check(sections("cad") === "Compare with the CAD model", `Compare with CAD: ${sections("cad")}`);
check(sections("export") === "Download, For CAD, Report, Edit mesh, Make mesh again", `Export: ${sections("export")}`);
const all = VIEWER_GROUPS.flatMap((g) => g.sections);
check(new Set(all).size === all.length, "each section in one group only");

// ---------------------------------------------------------------- the first open
const o = initialOpen();
check(o.good && !o.measure && !o.cad && !o.export, "only the first group is open at first");

// ---------------------------------------------------------------- a tool opens its group
check(groupOfTool("measure") === "measure" && groupOfTool("fit") === "measure" && groupOfTool("section") === "measure" && groupOfTool("flush") === "measure", "the four tools are in Measure");
check(groupOfTool("reference") === "cad" && groupOfTool("pairs") === "cad", "the CAD model's shading and the picked points are in Compare with CAD");
const after = openFor(o, "fit");
check(after.measure && after.good, "turning a tool on opens its group and leaves the others as they were");
check(openFor(after, "fit") === after, "a group already open: the same object (no re-render)");
check(!o.measure, "openFor does not change the state it was given");

// ---------------------------------------------------------------- the part's axes
check(axesEntry({ planes: 0, hasFrame: false, asked: false }) === "hidden", "no plane saved: the part's axes are not shown");
check(axesEntry({ planes: 0, hasFrame: false, asked: true }) === "hidden", "no plane: not even when asked (nothing to build them from)");
check(axesEntry({ planes: 1, hasFrame: false, asked: false }) === "offer", "a first plane saved: “Set the part's axes…” is offered");
check(axesEntry({ planes: 2, hasFrame: false, asked: true }) === "panel", "asked for: the panel opens");
check(axesEntry({ planes: 0, hasFrame: true, asked: false }) === "panel", "axes already set (even from fits deleted since): the panel shows them");

console.log(`viewerGroups.test: ${checks} checks passed`);
