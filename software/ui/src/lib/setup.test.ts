/** Checks for the setup wizard's progress count (UX_PLAN U27, `npm test`): the optional Print step is not counted. */
import type { SetupState } from "../api/types.ts";
import { boardLine, minutesLeft, setupProgress, setupTimeText, STEP_META } from "./setup.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

const ids = ["connect", "selftest", "print", "stereo", "lasers", "imu", "check"] as const;
const setup = (print: "optional" | "done" | "todo", rest: "done" | "todo" = "done") =>
  ({ steps: ids.map((id) => ({ id, state: id === "print" ? print : rest })) }) as unknown as SetupState;

const p = setupProgress(setup("optional"));
check(p.done === 6 && p.total === 6, `everything but the optional print: 6 of 6, not 6 of 7 (${p.done}/${p.total})`);
const q = setupProgress(setup("done"));
check(q.done === 7 && q.total === 7, "the print done too: 7 of 7");
const r = setupProgress(setup("optional", "todo"));
check(r.done === 0 && r.total === 6, "nothing done yet: 0 of 6");

// UX_PLAN R12: the board line in words, without the marker dictionary's name ("DICT_5X5_100")
const N = " ";
const board = { squares_x: 11, squares_y: 8, square_mm: 20, marker_mm: 15, paper: "letter" as const, scale: 1 };
check(boardLine(board) === `11${N}×${N}8 squares of 20${N}mm, 15${N}mm markers, on Letter`, boardLine(board));
check(boardLine({ ...board, paper: "a4", square_mm: 12.5, scale: 0.9985 }) === `11${N}×${N}8 squares of 12.5${N}mm, 15${N}mm markers, on A4, measured scale 99.85${N}%`, boardLine({ ...board, paper: "a4", square_mm: 12.5, scale: 0.9985 }));
check(!/DICT|ChArUco/.test(boardLine(board)), "no program words");

// R20: one number for the setup's time on Home, Device and Scan; the motion sensor step is about 2 minutes, as it says
check(STEP_META.imu.minutes === 2 && STEP_META.imu.time === `2${N}min`, "the motion sensor step: 2 minutes");
check(setupTimeText(setup("optional", "todo")) === `about ${minutesLeft(setup("optional", "todo"))} minutes`, setupTimeText(setup("optional", "todo")));
check(setupTimeText(setup("optional")) === "", "nothing left: nothing said");
const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };
for (const page of ["../pages/DevicePage.tsx", "../pages/scan/SidePanel.tsx"]) {
  const src = fs.readFileSync(new URL(page, import.meta.url), "utf8");
  check(!/about \d+ minutes/.test(src) && /setupTimeText\(/.test(src), `${page}: the setup's time from setupTimeText, not a number of its own`);
}
const overview = fs.readFileSync(new URL("../pages/setup/Overview.tsx", import.meta.url), "utf8");
check(!/\b(Six|Seven|Eight) short steps\b/.test(overview), "the overview does not count the steps in words beside the ring's count");

console.log(`setup.test: ${checks} checks passed`);
