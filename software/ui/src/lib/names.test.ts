/**
 * Checks for names that repeat (UX_PLAN R21, `npm test`): the server refuses a rename to a name another scan of the
 * project has (409, with a `suggestion`), and the page offers that suggestion as a button; the Align and merge name
 * field warns the same way, with the next free name, before the merge is made. Plain TypeScript run by Node.
 */
import { freeName, nameTaken, renameRefusal, sameName } from "./names.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`names.test: ${what}`);
}

// the server's rule (projects.free_name): case and extra spaces aside
check(sameName("Bracket", " bracket ") && sameName("Top  +  Side", "top + side") && !sameName("Bracket", "Bracket (2)"), "the same name");
check(freeName("Bracket", ["Side"]) === "Bracket", "free: as it is");
check(freeName("Bracket", ["bracket"]) === "Bracket (2)", "taken: (2)");
check(freeName("Bracket", ["Bracket", "Bracket (2)", "BRACKET (3)"]) === "Bracket (4)", "the first free number");
check(freeName("Top + Side", ["Top + Side", "Top + Side (2)"]) === "Top + Side (3)", "a merge's default name");
check(nameTaken("bracket ", ["Bracket", "Side"]) && !nameTaken("Bracket (2)", ["Bracket"]) && !nameTaken("", ["Bracket"]), "taken or not");

// the refusal: its sentence, and the button's name
const refusal = renameRefusal({ status: 409, detail: "Another scan of this project is already called “Bracket”.", hint: "Pick another name, for example “Bracket (2)”.", extra: { suggestion: "Bracket (2)" } });
check(refusal?.sentence === "Another scan of this project is already called “Bracket”." && refusal.suggestion === "Bracket (2)", JSON.stringify(refusal));
check(refusal!.button === "Use “Bracket (2)”", refusal!.button);
check(renameRefusal({ status: 409, detail: "This scan is busy.", hint: null, extra: {} }) === null, "another refusal: the toast as before");
check(renameRefusal({ status: 422, detail: "x", hint: null, extra: { suggestion: "y" } }) === null, "only a 409");
check(renameRefusal({ status: 409, detail: "x", hint: null, extra: { suggestion: "" } }) === null, "an empty suggestion");
check(renameRefusal(new Error("x")) === null && renameRefusal(null) === null, "not an answer of the engine");

console.log(`names.test: ${checks} checks passed`);
