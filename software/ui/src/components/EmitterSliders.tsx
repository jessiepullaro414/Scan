import { useEffect, useRef, useState } from "react";
import type { DeviceState, EmitterName, Emitters } from "../api/types";
import { cn } from "../lib/util";
import { useDevice } from "../store/device";
import { Button } from "./ui/Button";
import { Slider } from "./ui/Form";

const EMITTERS: { key: EmitterName; label: string; short: string; help: string }[] = [
  { key: "vertical", label: "Vertical line laser", short: "Vertical laser", help: "Laser points on the near-vertical stripe" },
  { key: "horizontal", label: "Horizontal line laser", short: "Horizontal laser", help: "Laser points on the near-horizontal stripe" },
  { key: "dots", label: "Dot projector", short: "Dot projector", help: "Texture for stereo depth on plain surfaces" },
];

const PRESETS: { label: string; levels: Emitters; title: string }[] = [
  { label: "Scan", levels: { vertical: 100, horizontal: 100, dots: 100 }, title: "Everything on: dots for depth, lines for laser points" },
  { label: "Lines only", levels: { vertical: 100, horizontal: 100, dots: 0 }, title: "Laser lines only" },
  { label: "Dots only", levels: { vertical: 0, horizontal: 0, dots: 100 }, title: "Dot projector only" },
  { label: "All off", levels: { vertical: 0, horizontal: 0, dots: 0 }, title: "Switch every emitter off" },
];

/** Emitter levels (camera + Pico only), throttled to ~8 updates per second while dragging. */
export function EmitterSliders({ device, compact }: { device: DeviceState; compact?: boolean }) {
  const setEmitters = useDevice((s) => s.setEmitters);
  const server = device.pico?.emitters ?? { vertical: 0, horizontal: 0, dots: 0 };
  const [local, setLocal] = useState<Emitters>(server);
  const dragging = useRef(false);
  const lastSent = useRef(0);
  const pending = useRef<Partial<Emitters>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!dragging.current) setLocal(server);
  }, [server.vertical, server.horizontal, server.dots]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const flush = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const p = pending.current;
    pending.current = {};
    if (Object.keys(p).length) {
      lastSent.current = performance.now();
      void setEmitters(p);
    }
  };
  const queue = (p: Partial<Emitters>) => {
    pending.current = { ...pending.current, ...p };
    const wait = 120 - (performance.now() - lastSent.current);
    if (wait <= 0) flush();
    else if (!timer.current) timer.current = setTimeout(flush, wait);
  };

  const enabled = device.source === "camera" && !!device.pico?.connected;
  return (
    <div className={compact ? "space-y-3" : "space-y-5"}>
      {EMITTERS.map((e) => (
        <div key={e.key}>
          <div className="mb-1 flex items-center justify-between">
            <label htmlFor={`em-${e.key}${compact ? "-c" : ""}`} className={cn("text-fg", compact ? "text-xs" : "text-sm")}>
              {compact ? e.short : e.label}
            </label>
            <span className={cn("w-12 text-right font-mono text-fg tnum", compact ? "text-xs" : "text-sm")}>{Math.round(local[e.key])}%</span>
          </div>
          <Slider
            id={`em-${e.key}${compact ? "-c" : ""}`}
            label={e.label}
            value={local[e.key]}
            disabled={!enabled}
            onChange={(v) => {
              dragging.current = true;
              setLocal((l) => ({ ...l, [e.key]: v }));
              queue({ [e.key]: v });
            }}
            onCommit={(v) => {
              dragging.current = false;
              queue({ [e.key]: v });
              flush();
            }}
          />
          {!compact ? <div className="mt-0.5 text-xs text-subtle">{e.help}</div> : null}
        </div>
      ))}
      <div className={cn("flex flex-wrap gap-2", !compact && "border-t border-line pt-4")}>
        {PRESETS.map((p) => (
          <Button
            key={p.label}
            size={compact ? "xs" : "sm"}
            variant={p.label === "All off" ? "danger-ghost" : "secondary"}
            disabled={!enabled}
            title={p.title}
            onClick={() => {
              setLocal(p.levels);
              void setEmitters(p.levels);
            }}
          >
            {p.label}
          </Button>
        ))}
      </div>
    </div>
  );
}
