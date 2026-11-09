import { create } from "zustand";
import { describeError } from "../api/http";

export type ToastKind = "error" | "warn" | "success" | "info";
export type ToastAction = { label: string; run: () => void };
export type Toast = {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string | null;
  hint?: string | null;
  action?: ToastAction;
  /** ms; 0 keeps it until dismissed. */
  timeout: number;
  /** Toasts with the same key replace each other instead of stacking. */
  key?: string;
};

type ToastStore = {
  toasts: Toast[];
  push: (t: Omit<Toast, "id" | "timeout"> & { timeout?: number }) => number;
  dismiss: (id: number) => void;
  clear: () => void;
};

let nextId = 1;

const DEFAULT_TIMEOUT: Record<ToastKind, number> = { error: 9000, warn: 7000, success: 4500, info: 4500 };

export const useToasts = create<ToastStore>((set) => ({
  toasts: [],
  push: (t) => {
    const id = nextId++;
    const toast: Toast = { ...t, id, timeout: t.timeout ?? DEFAULT_TIMEOUT[t.kind] };
    set((s) => {
      const rest = t.key ? s.toasts.filter((x) => x.key !== t.key) : s.toasts;
      return { toasts: [...rest, toast].slice(-5) };
    });
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  clear: () => set({ toasts: [] }),
}));

export const toast = {
  success: (title: string, detail?: string, action?: ToastAction) =>
    useToasts.getState().push({ kind: "success", title, detail, action }),
  info: (title: string, detail?: string, action?: ToastAction) =>
    useToasts.getState().push({ kind: "info", title, detail, action }),
  warn: (title: string, detail?: string, hint?: string) => useToasts.getState().push({ kind: "warn", title, detail, hint }),
  /** An error toast from anything thrown, using the API's `detail` and `hint`. */
  error: (title: string, err?: unknown, action?: ToastAction) => {
    const { detail, hint } = err === undefined ? { detail: null, hint: null } : describeError(err);
    return useToasts.getState().push({ kind: "error", title, detail, hint, action, key: `err:${title}` });
  },
};
