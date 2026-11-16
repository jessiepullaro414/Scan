/**
 * Checks for the window's title bar following the theme (`npm test`): with pywebview's bridge the
 * theme goes to `set_theme` at once; before the bridge is there it goes as soon as "pywebviewready"
 * fires, with the latest theme; in a plain browser nothing is called. A failed check throws.
 */
import { syncWindowTheme } from "./windowChrome.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`windowChrome.test: ${what}`);
}

const target = new EventTarget() as EventTarget & { pywebview?: { api?: { set_theme?: (t: string) => unknown } } };
(globalThis as unknown as { window: unknown }).window = target;

// a browser: no bridge, nothing to call, and nothing breaks
syncWindowTheme("dark");
check(target.pywebview === undefined, "no bridge in a browser");

// the bridge arrives later: the latest theme is sent once it is ready
syncWindowTheme("light");
const got: string[] = [];
target.pywebview = { api: { set_theme: (t: string) => got.push(t) } };
target.dispatchEvent(new Event("pywebviewready"));
check(got.length === 1 && got[0] === "light", `sent on pywebviewready: ${JSON.stringify(got)}`);

// with the bridge there, a switch goes at once
syncWindowTheme("dark");
check(got.length === 2 && got[1] === "dark", `sent at once: ${JSON.stringify(got)}`);

// a bridge that throws or rejects does not break the page
target.pywebview = { api: { set_theme: () => { throw new Error("no"); } } };
syncWindowTheme("light");
target.pywebview = { api: { set_theme: () => Promise.reject(new Error("no")) } };
syncWindowTheme("dark");
check(true, "a failing bridge is ignored");

console.log(`windowChrome.test: ${checks} checks passed`);
