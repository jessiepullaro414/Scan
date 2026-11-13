import { useEffect, useRef, useState } from "react";

/** Join class names, skipping falsy values. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function clamp(x: number, lo: number, hi: number): number {
  return x < lo ? lo : x > hi ? hi : x;
}

/** Read/write a JSON value in localStorage (never throws). */
export const storage = {
  get<T>(key: string, fallback: T): T {
    try {
      const v = localStorage.getItem(key);
      return v === null ? fallback : (JSON.parse(v) as T);
    } catch {
      return fallback;
    }
  },
  set(key: string, value: unknown): void {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private mode or quota: preferences just don't persist */
    }
  },
};

/** Re-render every `ms` (for relative times and elapsed clocks). */
export function useTicker(ms: number): number {
  const [t, setT] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setT(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return t;
}

/** True while an element matching the selector exists (e.g. an open modal). */
export function isModalOpen(): boolean {
  return !!document.querySelector('[aria-modal="true"]');
}

export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable;
}

export type HotkeyMap = Record<string, (e: KeyboardEvent) => void>;

/** Normalised key name: "space", "escape", "r", "f", "?", "arrowup"… */
export function keyName(e: KeyboardEvent): string {
  if (e.key === " " || e.code === "Space") return "space";
  if (e.key === "?") return "?";
  return e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase();
}

/**
 * Global keyboard shortcuts. Ignored while typing in a field, while a modal dialog is
 * open, and with Ctrl/Alt/Meta held (those belong to the browser and the OS).
 */
export function useHotkeys(map: HotkeyMap, enabled = true): void {
  const ref = useRef(map);
  ref.current = map;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
      if (isTypingTarget(e.target) || isModalOpen()) return;
      const fn = ref.current[keyName(e)];
      if (!fn) return;
      e.preventDefault();
      fn(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

/** Copy text to the clipboard; resolves to false when not allowed. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

/** Deterministic small hash (for placeholder colours). */
export function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
