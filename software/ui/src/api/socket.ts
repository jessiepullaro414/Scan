/**
 * Live WebSocket client (`/api/ws/live`) with automatic reconnect.
 *
 * On every (re)connect it sends the subscribe message. Text frames are parsed as JSON and
 * binary frames are handed over as ArrayBuffers (`binaryType = "arraybuffer"`). Reconnects
 * back off from 0.5 s to 5 s and reset after a successful open.
 */
import type { ServerTextMsg, SubscribeMsg } from "./types";

/** The subset of the browser WebSocket used here (the mock backend implements it too). */
export interface SocketLike {
  binaryType: BinaryType;
  readonly readyState: number;
  onopen: ((ev: Event) => void) | null;
  onclose: ((ev: CloseEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export type SocketFactory = (url: string) => SocketLike;

let socketFactory: SocketFactory = (url) => new WebSocket(url);

export function setSocketFactory(f: SocketFactory): void {
  socketFactory = f;
}

export type LinkState = "connecting" | "open" | "closed";

export type LiveHandlers = {
  onText: (msg: ServerTextMsg) => void;
  onBinary: (buf: ArrayBuffer) => void;
  onState: (state: LinkState, info: { retryInMs: number | null; attempts: number }) => void;
};

export function liveUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/ws/live`;
}

export class LiveSocket {
  private ws: SocketLike | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private attempts = 0;
  private stopped = true;
  private readonly url: string;
  private readonly handlers: LiveHandlers;
  private subscribe: SubscribeMsg;

  constructor(url: string, handlers: LiveHandlers, subscribe: SubscribeMsg) {
    this.url = url;
    this.handlers = handlers;
    this.subscribe = subscribe;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.close(1000, "client stopped");
    }
    this.handlers.onState("closed", { retryInMs: null, attempts: this.attempts });
  }

  /** Change what the server streams; sent now when connected, and again after every reconnect. */
  setSubscription(patch: Omit<Partial<SubscribeMsg>, "type">): void {
    const next = { ...this.subscribe, ...patch };
    if (next.preview === this.subscribe.preview && next.mesh === this.subscribe.mesh
        && next.max_preview_hz === this.subscribe.max_preview_hz) return;
    this.subscribe = next;
    const ws = this.ws;
    if (ws && ws.readyState === 1) {
      try {
        ws.send(JSON.stringify(next));
      } catch {
        /* the close handler reconnects, and the reconnect sends it */
      }
    }
  }

  /** Skip the back-off and try again right away. */
  reconnectNow(): void {
    if (this.stopped) return;
    if (this.ws && this.ws.readyState <= 1) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.open();
  }

  private open(): void {
    this.handlers.onState("connecting", { retryInMs: null, attempts: this.attempts });
    let ws: SocketLike;
    try {
      ws = socketFactory(this.url);
    } catch {
      this.scheduleRetry();
      return;
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.attempts = 0;
      try {
        ws.send(JSON.stringify(this.subscribe));
      } catch {
        /* the close handler reconnects */
      }
      this.handlers.onState("open", { retryInMs: null, attempts: 0 });
    };
    ws.onmessage = (ev: MessageEvent) => {
      const data: unknown = ev.data;
      if (typeof data === "string") {
        let msg: ServerTextMsg;
        try {
          msg = JSON.parse(data) as ServerTextMsg;
        } catch {
          return;
        }
        if (msg && typeof msg === "object" && "type" in msg) this.handlers.onText(msg);
      } else if (data instanceof ArrayBuffer) {
        this.handlers.onBinary(data);
      } else if (data instanceof Blob) {
        // binaryType could not be honoured: convert.
        void data.arrayBuffer().then((b) => this.handlers.onBinary(b));
      }
    };
    ws.onerror = () => {
      /* followed by onclose */
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      if (!this.stopped) this.scheduleRetry();
    };
  }

  private scheduleRetry(): void {
    this.attempts += 1;
    const delay = Math.min(5000, Math.round(500 * Math.pow(1.6, this.attempts - 1)));
    this.handlers.onState("closed", { retryInMs: delay, attempts: this.attempts });
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      if (!this.stopped) this.open();
    }, delay);
  }
}

export function createLiveSocket(handlers: LiveHandlers, subscribe: SubscribeMsg): LiveSocket {
  return new LiveSocket(liveUrl(), handlers, subscribe);
}
