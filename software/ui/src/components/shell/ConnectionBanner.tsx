import { CloudOff, FlaskConical, RefreshCw, ServerOff, Terminal } from "lucide-react";
import { useEffect, useState } from "react";
import { MOCK, mockUrl } from "../../api/backend";
import { reconnectLive } from "../../api/live";
import { useConnection } from "../../store/connection";
import { Button } from "../ui/Button";
import { Code } from "../ui/Layout";

/** Thin banner while the live link to the engine is down (after it was up once). */
export function ConnectionBanner() {
  const link = useConnection((s) => s.link);
  const everOpen = useConnection((s) => s.everOpen);
  const retryInMs = useConnection((s) => s.retryInMs);
  const attempts = useConnection((s) => s.attempts);
  const [countdown, setCountdown] = useState<number | null>(null);

  useEffect(() => {
    if (retryInMs === null) {
      setCountdown(null);
      return;
    }
    const until = Date.now() + retryInMs;
    setCountdown(Math.ceil(retryInMs / 1000));
    const id = setInterval(() => setCountdown(Math.max(0, Math.ceil((until - Date.now()) / 1000))), 250);
    return () => clearInterval(id);
  }, [retryInMs, attempts]);

  // A short blip (reconnected on the first retry) is not worth a banner.
  const show = everOpen && link !== "open" && attempts >= 2;
  if (!show) return null;
  return (
    <div
      role="status"
      className="flex h-9 shrink-0 items-center gap-3 border-b border-warn/30 bg-warn-soft px-4 text-sm text-fg"
    >
      <CloudOff size={15} className="text-warn" aria-hidden />
      <span className="font-medium">Lost the live connection to the engine.</span>
      <span className="text-muted">
        {link === "connecting" ? "Reconnecting…" : countdown !== null ? `Retrying in ${countdown} s.` : "Retrying…"} Your
        recording keeps running on the engine.
      </span>
      <div className="flex-1" />
      <Button size="xs" variant="secondary" icon={RefreshCw} onClick={() => reconnectLive()}>
        Retry now
      </Button>
    </div>
  );
}

/** Full-page state when the engine was never reachable. */
export function EngineUnreachable({ onRetry, checking }: { onRetry: () => void; checking: boolean }) {
  return (
    <div className="grid h-full place-items-center overflow-auto p-6">
      <div className="w-full max-w-[560px] animate-pop-in rounded-2xl border border-line bg-surface p-8 shadow-md">
        <div className="mb-5 grid h-12 w-12 place-items-center rounded-xl border border-danger/30 bg-danger-soft text-danger">
          <ServerOff size={22} aria-hidden />
        </div>
        <h1 className="font-display text-xl font-semibold text-fg">Can't reach the OpenHandScan engine</h1>
        <p className="mt-2 text-sm text-muted">
          Studio talks to the <Code>ohs</Code> engine running on this computer at <Code>127.0.0.1:8765</Code>. It isn't
          answering yet.
        </p>
        <ol className="mt-5 space-y-3 text-sm text-fg">
          <li className="flex gap-3">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-surface-3 text-xs font-semibold">1</span>
            <span>
              Start the engine: <Code>ohs app</Code> opens Studio in its own window and serves this page.
            </span>
          </li>
          <li className="flex gap-3">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-surface-3 text-xs font-semibold">2</span>
            <span>Already running? Check that nothing else uses port 8765, then retry. This page also retries on its own.</span>
          </li>
          <li className="flex gap-3">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-surface-3 text-xs font-semibold">3</span>
            <span>No engine or hardware handy? The demo runs a simulated scanner right in this window.</span>
          </li>
        </ol>
        <div className="mt-7 flex flex-wrap gap-2">
          <Button variant="primary" icon={RefreshCw} loading={checking} onClick={onRetry}>
            Retry
          </Button>
          {!MOCK ? (
            <Button variant="secondary" icon={FlaskConical} onClick={() => (location.href = mockUrl())}>
              Open the demo
            </Button>
          ) : null}
          <div className="flex items-center gap-1.5 pl-1 text-xs text-subtle">
            <Terminal size={13} aria-hidden /> Developers: <Code>npm run dev</Code> proxies <Code>/api</Code> to port 8765.
          </div>
        </div>
      </div>
    </div>
  );
}
