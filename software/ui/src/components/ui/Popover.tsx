import type { LucideIcon } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/util";

type Align = "start" | "end" | "center";

function usePosition(anchor: RefObject<HTMLElement | null>, open: boolean, align: Align, width: number | undefined) {
  const [pos, setPos] = useState<{ top: number; left: number; maxH: number; up: boolean } | null>(null);
  const update = useCallback(() => {
    const el = anchor.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const w = width ?? 240;
    let left = align === "end" ? r.right - w : align === "center" ? r.left + r.width / 2 - w / 2 : r.left;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    const below = window.innerHeight - r.bottom - 8;
    const above = r.top - 8;
    const up = below < 220 && above > below;
    setPos({ top: up ? r.top - 6 : r.bottom + 6, left, maxH: Math.max(120, (up ? above : below) - 6), up });
  }, [anchor, align, width]);
  useLayoutEffect(() => {
    if (!open) return;
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, update]);
  return pos;
}

/** An anchored floating panel (portal), closed by outside click or Esc. */
export function Popover({
  open,
  onClose,
  anchor,
  children,
  align = "end",
  width = 280,
  className,
  label,
}: {
  open: boolean;
  onClose: () => void;
  anchor: RefObject<HTMLElement | null>;
  children: ReactNode;
  align?: Align;
  width?: number;
  className?: string;
  label?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const pos = usePosition(anchor, open, align, width);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (panel.current?.contains(t) || anchor.current?.contains(t)) return;
      closeRef.current();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
        anchor.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, anchor]);

  if (!open || !pos) return null;
  return createPortal(
    <div
      ref={panel}
      role="dialog"
      aria-label={label}
      className={cn(
        "fixed z-[90] animate-pop-in overflow-auto rounded-xl border border-line-strong bg-panel shadow-lg",
        className,
      )}
      style={{
        left: pos.left,
        width,
        maxHeight: pos.maxH,
        ...(pos.up ? { bottom: window.innerHeight - pos.top } : { top: pos.top }),
      }}
    >
      {children}
    </div>,
    document.body,
  );
}

export type MenuItem =
  | { label: string; icon?: LucideIcon; onSelect: () => void; danger?: boolean; disabled?: boolean; hint?: string }
  | "separator";

/** A dropdown menu with keyboard navigation. */
export function Menu({
  items,
  align = "end",
  width = 220,
  label,
  renderTrigger,
}: {
  items: MenuItem[];
  align?: Align;
  width?: number;
  label: string;
  renderTrigger: (p: {
    ref: RefObject<HTMLButtonElement | null>;
    onClick: () => void;
    "aria-haspopup": "menu";
    "aria-expanded": boolean;
  }) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => list.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  const move = (dir: 1 | -1) => {
    const btns = Array.from(list.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? []);
    if (!btns.length) return;
    const i = btns.indexOf(document.activeElement as HTMLButtonElement);
    btns[(i + dir + btns.length) % btns.length].focus();
  };

  return (
    <>
      {renderTrigger({ ref: trigger, onClick: () => setOpen((o) => !o), "aria-haspopup": "menu", "aria-expanded": open })}
      <Popover open={open} onClose={() => setOpen(false)} anchor={trigger} align={align} width={width} label={label}>
        <div
          ref={list}
          role="menu"
          aria-label={label}
          className="p-1"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              move(1);
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              move(-1);
            } else if (e.key === "Tab") {
              setOpen(false);
            }
          }}
        >
          {items.map((it, i) =>
            it === "separator" ? (
              <div key={`sep${i}`} role="separator" className="my-1 h-px bg-line" />
            ) : (
              <button
                key={it.label}
                type="button"
                role="menuitem"
                disabled={it.disabled}
                onClick={() => {
                  setOpen(false);
                  it.onSelect();
                }}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-sm outline-none transition-colors",
                  "disabled:opacity-40 focus-visible:bg-surface-2",
                  it.danger ? "text-danger hover:bg-danger-soft focus:bg-danger-soft" : "text-fg hover:bg-surface-2 focus:bg-surface-2",
                )}
              >
                {it.icon ? <it.icon size={15} className={it.danger ? "" : "text-muted"} aria-hidden /> : null}
                <span className="flex-1">{it.label}</span>
                {it.hint ? <span className="text-xs text-subtle">{it.hint}</span> : null}
              </button>
            ),
          )}
        </div>
      </Popover>
    </>
  );
}

/** A hover / focus tooltip with rich content. */
export function Tooltip({
  content,
  children,
  side = "bottom",
  className,
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom";
  className?: string;
}) {
  const [show, setShow] = useState(false);
  const anchor = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const open = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setRect(anchor.current?.getBoundingClientRect() ?? null);
      setShow(true);
    }, 380);
  };
  const close = () => {
    if (timer.current) clearTimeout(timer.current);
    setShow(false);
  };
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  return (
    <span
      ref={anchor}
      className={cn("inline-flex", className)}
      onMouseEnter={open}
      onMouseLeave={close}
      onFocus={open}
      onBlur={close}
    >
      {children}
      {show && rect
        ? createPortal(
            <div
              role="tooltip"
              className="pointer-events-none fixed z-[95] max-w-xs animate-fade-in rounded-lg border border-line-strong bg-panel px-3 py-2 text-xs text-fg shadow-lg"
              style={{
                left: Math.max(8, Math.min(rect.left + rect.width / 2 - 140, window.innerWidth - 288)),
                width: 280,
                ...(side === "bottom" ? { top: rect.bottom + 8 } : { bottom: window.innerHeight - rect.top + 8 }),
              }}
            >
              {content}
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
