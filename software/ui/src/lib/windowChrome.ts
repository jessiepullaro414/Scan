/**
 * The desktop window's title bar follows the page's theme (engine: ohs/server/title_bar.py).
 * In the app's own window pywebview injects `window.pywebview.api` a moment after the page loads
 * and fires "pywebviewready"; in a browser there is neither, and this does nothing.
 */
type WindowApi = { set_theme?: (theme: string) => Promise<unknown> | unknown };

function api(): WindowApi | undefined {
  return (window as unknown as { pywebview?: { api?: WindowApi } }).pywebview?.api;
}

let current: string | null = null;
let listening = false;

/** Tell the window the page's theme (now, or as soon as the window's bridge is ready). */
export function syncWindowTheme(theme: string): void {
  current = theme;
  const a = api();
  if (a?.set_theme) {
    try {
      void Promise.resolve(a.set_theme(theme)).catch(() => undefined);
    } catch {
      /* a window without the call: nothing to do */
    }
    return;
  }
  if (!listening && typeof window !== "undefined") {
    listening = true;
    window.addEventListener("pywebviewready", () => {
      if (current) syncWindowTheme(current);
    });
  }
}
