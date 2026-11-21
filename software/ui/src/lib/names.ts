/**
 * Names that repeat (UX_PLAN R21; APP_API.md "Rename a scan, the trash"): no two scans of a project share a name. The
 * server refuses a rename to a taken name (409, with `suggestion`: the first free "<name> (2)", "<name> (3)", …), and
 * gives a merge without a typed name the next free one. The page offers the suggestion as a button, and warns in the
 * Align and merge name field before the merge is made (it warns, it does not block). Pure; `names.test.ts` checks it.
 */

const norm = (s: string) => s.split(/\s+/).filter(Boolean).join(" ").toLocaleLowerCase();

/** The same name, as the server compares them: case and extra spaces aside. */
export function sameName(a: string, b: string): boolean {
  return norm(a) === norm(b);
}

/** Whether `name` is one of `taken` (an empty name is never taken). */
export function nameTaken(name: string, taken: readonly string[]): boolean {
  return norm(name) !== "" && taken.some((t) => sameName(name, t));
}

/** `wanted`, or the first of "wanted (2)", "wanted (3)", … that none of `taken` is (the server's `free_name`). */
export function freeName(wanted: string, taken: readonly string[]): string {
  if (!nameTaken(wanted, taken)) return wanted;
  let k = 2;
  while (nameTaken(`${wanted} (${k})`, taken)) k++;
  return `${wanted} (${k})`;
}

/** A refusal's parts the page reads (an `ApiError`, or anything shaped like one). */
type Refusal = { status?: unknown; detail?: unknown; extra?: unknown };

/**
 * A rename refused because another scan has the name: its sentence and the free name it suggests, with the button's
 * words ("Use “Bracket (2)”"); null for any other error (shown as before).
 */
export function renameRefusal(err: unknown): { sentence: string; suggestion: string; button: string } | null {
  if (!err || typeof err !== "object") return null;
  const e = err as Refusal;
  const s = e.extra && typeof e.extra === "object" ? (e.extra as { suggestion?: unknown }).suggestion : undefined;
  if (e.status !== 409 || typeof s !== "string" || !s.trim() || typeof e.detail !== "string") return null;
  return { sentence: e.detail, suggestion: s, button: `Use “${s}”` };
}
