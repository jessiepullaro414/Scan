/**
 * Which project a scan goes into when the Scan page opens (UX_PLAN U44). "New scan" on Home opens the page with
 * `choose` set: the project picker is focused and says "Choose the project for this scan", and the first project
 * of the list is no longer picked silently (the current project, named in the top bar, is offered; a single
 * project is that one). Other ways in keep what they did. Pure; `scanProject.test.ts` checks it.
 */

export const CHOOSE_PROJECT_TEXT = "Choose the project for this scan";

export function projectForScan(a: { list: readonly { id: string }[] | null; current: string | null; param: string | null; choose: boolean }): string | null {
  const { list, current, param, choose } = a;
  if (!list) return param ?? current;
  const has = (id: string | null): id is string => !!id && list.some((p) => p.id === id);
  if (has(param)) return param;
  if (has(current)) return current;
  if (choose) return list.length === 1 ? list[0].id : null;
  return list[0]?.id ?? null;
}
