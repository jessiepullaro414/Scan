/** Wizard actions shared by several screens (kept out of the component files for fast refresh). */
import type { CalibAdviceAction, CalibKind } from "../../api/types";
import { navigate, paths } from "../../lib/router";
import { useCalib } from "../../store/calib";
import { useProjects } from "../../store/projects";
import { useUi } from "../../store/ui";

export const FIRST_PROJECT = "My first scan";

/** Create (or reuse) the "My first scan" project and open the Scan page with the coach mark. */
export async function startFirstScan(): Promise<boolean> {
  await useProjects.getState().fetchList();
  const list = useProjects.getState().list ?? [];
  let id = list.find((p) => p.name.trim().toLowerCase() === FIRST_PROJECT.toLowerCase())?.id ?? null;
  if (!id) {
    const p = await useProjects.getState().create(FIRST_PROJECT);
    if (!p) return false;
    id = p.id;
  }
  useUi.getState().setCurrentProject(id);
  navigate(paths.scan(id, { first: true }));
  return true;
}

/**
 * Leave for another step. A result that can't be saved (a check, or a failed solve) is closed
 * first, so the other step isn't blocked by it; a saveable result stays, to come back to.
 */
async function leaveTo(to: string) {
  const s = useCalib.getState().session;
  if (s?.state === "result" && s.result && !s.result.can_save) await useCalib.getState().cancel();
  navigate(to);
}

/** Runs a result's advice action; shared by the result page and the "not yet" banner. */
export function useAdviceAction(kind: CalibKind) {
  const continueCapture = useCalib((s) => s.continueCapture);
  const removeBad = useCalib((s) => s.removeBadViewsAndSolve);
  const startOver = useCalib((s) => s.startOver);
  return (a: CalibAdviceAction) => {
    switch (a) {
      case "add_views":
        void continueCapture();
        break;
      case "remove_bad_views":
        void removeBad();
        break;
      case "start_over":
        void startOver();
        break;
      case "recalibrate_stereo":
        if (kind === "stereo") void startOver();
        else void leaveTo(paths.setup("stereo"));
        break;
      case "reprint":
      case "check_board":
        void leaveTo(paths.setup("print"));
        break;
    }
  };
}
