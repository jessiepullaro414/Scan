/**
 * When a page's keyboard shortcut may fire (UX_PLAN R3). A focused control keeps its own keys: Enter and Space press
 * a focused button, switch, link or menu item; arrows move a slider or a menu; a text field takes every key. So:
 *
 * - in a text field (a text input, a text area, a select, an editable text) no shortcut fires, not even Esc (the
 *   field may use Esc itself, to cancel an edit);
 * - on any other control Esc and "?" still fire; a control reached by the keyboard (Tab) keeps
 *   every other key; a control that only has the focus because it was clicked keeps Enter, Space, Backspace, Delete
 *   and the moving keys, and lets the letters and digits through (so a click on a toolbar button does not turn the
 *   page's letter keys off);
 * - with nothing of that focused (the page, a canvas) every shortcut fires.
 *
 * Plain functions, no DOM in `hotkeyAllowed`, so a test can hold the rule (`hotkeys.test.ts`); `targetKind` reads
 * an element.
 */

/** What has the focus, as far as the shortcuts care. */
export type FocusKind = "none" | "text" | "control";

/** The keys a focused control uses itself even when it was only clicked (`keyName` values). */
const CONTROL_OWN_KEYS = new Set([
  "enter",
  "space",
  "backspace",
  "delete",
  "arrowup",
  "arrowdown",
  "arrowleft",
  "arrowright",
  "home",
  "end",
  "pageup",
  "pagedown",
  "tab",
]);

/** The keys that work on every control (not in a text field): Esc closes or stops, "?" opens the shortcut sheet. */
const EVERYWHERE = new Set(["escape", "?"]);

/**
 * May the page's shortcut for `key` (a `keyName` value) fire? `kind` is what has the focus; `keyboardFocus` is true
 * when that control was reached by the keyboard (Tab), not by a click.
 */
export function hotkeyAllowed(key: string, kind: FocusKind, keyboardFocus: boolean): boolean {
  if (kind === "none") return true;
  if (kind === "text") return false;
  if (EVERYWHERE.has(key)) return true;
  if (keyboardFocus) return false;
  return !CONTROL_OWN_KEYS.has(key);
}

const TEXT_INPUTS = new Set(["", "text", "search", "email", "url", "tel", "password", "number", "date", "time", "datetime-local", "month", "week"]);
const TEXT_ROLES = new Set(["textbox", "searchbox", "combobox", "spinbutton"]);
const CONTROL_ROLES = new Set([
  "button",
  "switch",
  "link",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
  "checkbox",
  "radio",
  "tab",
  "option",
  "slider",
  "treeitem",
  "listbox",
  "menu",
  "radiogroup",
]);

/** The parts of an element the rule reads (an `HTMLElement` has them all). */
export interface FocusTarget {
  tagName: string;
  isContentEditable?: boolean;
  getAttribute(name: string): string | null;
  hasAttribute(name: string): boolean;
}

/** What kind of thing `t` is for the shortcuts (`null` and the page itself are "none"). */
export function targetKind(t: FocusTarget | null | undefined): FocusKind {
  if (!t || typeof t.tagName !== "string") return "none";
  const tag = t.tagName.toUpperCase();
  if (tag === "BODY" || tag === "HTML") return "none";
  if (t.isContentEditable) return "text";
  const role = (t.getAttribute("role") ?? "").trim().toLowerCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return "text";
  if (tag === "INPUT") {
    const type = (t.getAttribute("type") ?? "").trim().toLowerCase();
    if (role === "switch" || role === "checkbox") return "control";
    return TEXT_INPUTS.has(type) ? "text" : "control";
  }
  if (TEXT_ROLES.has(role)) return "text";
  if (CONTROL_ROLES.has(role)) return "control";
  if (tag === "BUTTON" || tag === "SUMMARY") return "control";
  if (tag === "A" && t.hasAttribute("href")) return "control";
  return "none";
}
