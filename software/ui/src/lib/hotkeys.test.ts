/**
 * Checks for when a page's shortcut may fire (UX_PLAN R3, `npm test`): Enter and Space on a focused button, switch,
 * link or menu item do what that control does, not the page's shortcut (Enter on "Delete view 5" finished the
 * calibration; Space on the Auto-capture switch took a view). Esc and "?" still work outside a text field. Plain
 * TypeScript run by Node, no framework.
 */
import { hotkeyAllowed, targetKind, type FocusTarget } from "./hotkeys.ts";

const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`hotkeys.test: ${what}`);
}

function el(tagName: string, attrs: Record<string, string> = {}, editable = false): FocusTarget {
  return {
    tagName,
    isContentEditable: editable,
    getAttribute: (n) => (n in attrs ? attrs[n] : null),
    hasAttribute: (n) => n in attrs,
  };
}

// ---------------------------------------------------------------- what has the focus
check(targetKind(null) === "none" && targetKind(el("BODY")) === "none" && targetKind(el("DIV")) === "none", "the page itself: none");
check(targetKind(el("CANVAS")) === "none", "a canvas: none");
check(targetKind(el("BUTTON")) === "control", "a button");
check(targetKind(el("BUTTON", { role: "switch" })) === "control", "a switch");
check(targetKind(el("A", { href: "#/x" })) === "control" && targetKind(el("A")) === "none", "a link (with an address)");
check(targetKind(el("DIV", { role: "menuitem" })) === "control", "a menu item");
check(targetKind(el("DIV", { role: "button", tabindex: "0" })) === "control", "a div that is a button");
check(targetKind(el("SUMMARY")) === "control", "a folding group's title");
check(targetKind(el("INPUT", { type: "checkbox" })) === "control" && targetKind(el("INPUT", { type: "range" })) === "control", "a checkbox, a slider");
check(targetKind(el("INPUT", { type: "checkbox", role: "switch" })) === "control", "a checkbox shown as a switch");
check(targetKind(el("INPUT")) === "text" && targetKind(el("INPUT", { type: "number" })) === "text", "a text field, a number field");
check(targetKind(el("TEXTAREA")) === "text" && targetKind(el("SELECT")) === "text", "a text area, a select");
check(targetKind(el("DIV", {}, true)) === "text", "editable text");
check(targetKind(el("input", { type: "Search" })) === "text", "names in any case");

// ---------------------------------------------------------------- nothing focused: every shortcut
for (const k of ["space", "enter", "backspace", "escape", "?", "f", "1", "["]) check(hotkeyAllowed(k, "none", false), `no focus: ${k} fires`);

// ---------------------------------------------------------------- a control reached by the keyboard (Tab)
for (const k of ["enter", "space"]) check(!hotkeyAllowed(k, "control", true), `Tab to a button, ${k}: the button acts, not the page`);
for (const k of ["backspace", "delete", "arrowleft", "arrowdown", "f", "1", "["]) check(!hotkeyAllowed(k, "control", true), `Tab to a button, ${k}: nothing of the page`);
check(hotkeyAllowed("escape", "control", true), "Esc still works on a focused button");
check(hotkeyAllowed("?", "control", true), "“?” still opens the sheet on a focused button");

// ---------------------------------------------------------------- a control that was clicked (no focus ring)
for (const k of ["enter", "space", "backspace", "delete", "arrowup", "home"]) check(!hotkeyAllowed(k, "control", false), `a clicked button keeps ${k}`);
for (const k of ["f", "w", "1", "[", "?", "escape"]) check(hotkeyAllowed(k, "control", false), `after a click on a toolbar button ${k} still fires`);

// ---------------------------------------------------------------- a text field takes every key
for (const k of ["space", "enter", "backspace", "f", "1", "?", "escape"]) {
  check(!hotkeyAllowed(k, "text", false) && !hotkeyAllowed(k, "text", true), `a text field keeps ${k}`);
}

// ---------------------------------------------------------------- useHotkeys holds the rule
const util = fs.readFileSync(new URL("./util.ts", import.meta.url), "utf8");
const body = util.slice(util.indexOf("export function useHotkeys"));
check(/hotkeyAllowed\(/.test(body) && /targetKind\(/.test(body), "useHotkeys asks hotkeyAllowed about the focused element");
check(/addEventListener\("pointerdown"/.test(util) && /e\.key === "Tab"/.test(util), "useHotkeys tells a keyboard focus (Tab) from a click");

console.log(`hotkeys.test: ${checks} checks passed`);
