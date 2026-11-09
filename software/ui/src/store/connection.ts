/** Engine reachability, the live socket state and the engine's log messages. */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type { Health, LogLevel } from "../api/types";
import type { LinkState } from "../api/socket";

export type LogEntry = { id: number; at: number; level: LogLevel; message: string };

type ConnectionStore = {
  link: LinkState;
  retryInMs: number | null;
  attempts: number;
  /** The socket has been open at least once in this page's life. */
  everOpen: boolean;
  serverVersion: string | null;
  health: Health | null;
  healthError: ApiError | null;
  checking: boolean;
  logs: LogEntry[];
  setLink: (link: LinkState, info: { retryInMs: number | null; attempts: number }) => void;
  checkHealth: () => Promise<boolean>;
  addLog: (level: LogLevel, message: string) => void;
};

let logId = 1;

export const useConnection = create<ConnectionStore>((set, get) => ({
  link: "connecting",
  retryInMs: null,
  attempts: 0,
  everOpen: false,
  serverVersion: null,
  health: null,
  healthError: null,
  checking: false,
  logs: [],
  setLink: (link, info) =>
    set((s) => ({ link, retryInMs: info.retryInMs, attempts: info.attempts, everOpen: s.everOpen || link === "open" })),
  checkHealth: async () => {
    if (get().checking) return get().health !== null;
    set({ checking: true });
    try {
      const health = await api.health();
      set({ health, healthError: null, checking: false });
      return true;
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, String(e), null, "/api/health");
      set({ healthError: err, checking: false });
      return false;
    }
  },
  addLog: (level, message) =>
    set((s) => ({ logs: [...s.logs.slice(-199), { id: logId++, at: Date.now(), level, message }] })),
}));
