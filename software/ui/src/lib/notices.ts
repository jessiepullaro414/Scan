/**
 * The engine's notices (the WebSocket `log` messages; UX_PLAN R14): an error notice becomes a toast whose title is the
 * notice's own sentence, with nothing above it (the titles “Engine error” and “Scanner problem” are gone: the sentence
 * says what happened and what to do). Pure; `notices.test.ts` checks it.
 */
import type { ToastKind } from "../store/toasts";

export type NoticeToast = { kind: ToastKind; title: string; detail: null; hint: null; key: string };

/** The toast of a notice, or null: only errors show as a toast (the rest go to the app's log). */
export function engineNoticeToast(level: string, message: string): NoticeToast | null {
  const text = (message ?? "").trim();
  if (level !== "error" || !text) return null;
  return { kind: "error", title: text, detail: null, hint: null, key: `notice:${text}` };
}
