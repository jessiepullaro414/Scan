import { useState } from "react";
import { cn, hashString } from "../lib/util";

/**
 * Project thumbnail: the engine's image when there is one, otherwise a generated
 * placeholder (contour lines and a wireframe part, tinted per project).
 */
export function ProjectThumb({
  id,
  name,
  url,
  scans,
  className,
}: {
  id: string;
  name: string;
  url: string | null;
  scans: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const h = hashString(id);
  const hue = (h % 360 + 360) % 360;
  const tilt = (h >> 9) % 30 - 15;
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");

  if (url && !failed) {
    return (
      <div className={cn("relative overflow-hidden bg-surface-3", className)}>
        <img src={url} alt="" className="h-full w-full object-cover" onError={() => setFailed(true)} loading="lazy" />
      </div>
    );
  }
  return (
    <div
      className={cn("relative overflow-hidden", className)}
      style={{
        background: `radial-gradient(120% 120% at 20% 0%, hsl(${hue} 38% 26% / 0.9), transparent 60%), linear-gradient(160deg, hsl(${hue} 22% 16%), hsl(${(hue + 40) % 360} 18% 9%))`,
      }}
      aria-hidden
    >
      <svg viewBox="0 0 320 200" className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMid slice">
        <g fill="none" stroke={`hsl(${hue} 60% 75%)`} strokeOpacity="0.09" strokeWidth="1">
          {Array.from({ length: 9 }, (_, i) => (
            <ellipse key={i} cx="250" cy="170" rx={40 + i * 26} ry={22 + i * 15} transform={`rotate(${tilt} 250 170)`} />
          ))}
        </g>
        <g transform={`translate(160 102) rotate(${tilt / 3})`} stroke={`hsl(${hue} 70% 80%)`} fill="none" strokeLinejoin="round">
          <g strokeOpacity="0.55" strokeWidth="1.3">
            <path d="M-52 -8 L0 -34 L52 -8 L0 18 Z" fill={`hsl(${hue} 60% 70% / 0.08)`} />
            <path d="M-52 -8 L-52 26 L0 52 L0 18" fill={`hsl(${hue} 60% 70% / 0.04)`} />
            <path d="M52 -8 L52 26 L0 52" />
          </g>
          <g strokeOpacity="0.22" strokeWidth="0.8">
            <path d="M-26 -21 L26 5 M26 -21 L-26 5 M-52 9 L0 35 M52 9 L0 35 M-26 13 L-26 47 M26 13 L26 47" />
          </g>
        </g>
      </svg>
      <div className="absolute bottom-2 left-2.5 flex items-center gap-1.5">
        <span
          className="grid h-6 min-w-6 place-items-center rounded-md px-1 text-[10px] font-semibold text-white/90"
          style={{ background: `hsl(${hue} 45% 45% / 0.55)` }}
        >
          {initials || "P"}
        </span>
        {scans > 0 ? <span className="text-[10.5px] font-medium text-white/60">{scans} scan{scans === 1 ? "" : "s"}</span> : null}
      </div>
    </div>
  );
}
