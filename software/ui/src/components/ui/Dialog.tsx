import { X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/util";
import { Button } from "./Button";
import { TextInput } from "./Form";

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** Where the focus goes when a dialog opens: the first `data-autofocus` control that can take it, else the first control. */
function initialFocus(p: HTMLElement): HTMLElement | null {
  const usable = (el: HTMLElement) => el.matches(FOCUSABLE) && !el.hasAttribute("disabled") && el.getAttribute("aria-disabled") !== "true";
  const marked = Array.from(p.querySelectorAll<HTMLElement>("[data-autofocus]")).find(usable);
  return marked ?? p.querySelector<HTMLElement>(FOCUSABLE);
}

/** Accessible modal: focus trap, Esc / backdrop to close, focus restored on close. */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = 440,
  dismissible = true,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  width?: number;
  dismissible?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descId = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const dismissRef = useRef(dismissible);
  dismissRef.current = dismissible;

  useEffect(() => {
    if (!open) return;
    const before = document.activeElement as HTMLElement | null;
    const p = panel.current;
    // UX_PLAN R4: a disabled "autofocus" button (an action waiting for its numbers) can't take the focus, which then
    // stayed on the page behind; take the first one that can, else the first control, else the panel itself
    const first = p ? initialFocus(p) : null;
    (first ?? p)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        e.preventDefault();
        if (dismissRef.current) closeRef.current();
      } else if (e.key === "Tab" && p) {
        const items = Array.from(p.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
        if (!items.length) {
          e.preventDefault();
          return;
        }
        const firstEl = items[0];
        const lastEl = items[items.length - 1];
        if (!p.contains(document.activeElement)) {
          // the focus is outside (a control it was on went away): bring it back in, never to the page behind
          e.preventDefault();
          (e.shiftKey ? lastEl : firstEl).focus();
        } else if (e.shiftKey && (document.activeElement === firstEl || document.activeElement === p)) {
          e.preventDefault();
          lastEl.focus();
        } else if (!e.shiftKey && document.activeElement === lastEl) {
          e.preventDefault();
          firstEl.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      before?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[100] grid place-items-center p-4">
      <div
        className="absolute inset-0 animate-fade-in bg-overlay backdrop-blur-[2px]"
        onMouseDown={() => dismissible && onClose()}
        aria-hidden
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className="relative flex max-h-[calc(100vh-2rem)] w-full animate-pop-in flex-col rounded-xl border border-line-strong bg-panel shadow-lg outline-none"
        style={{ maxWidth: width }}
      >
        <div className="flex items-start gap-3 px-5 pt-5">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-semibold text-fg">
              {title}
            </h2>
            {description ? (
              <div id={descId} className="mt-1 text-sm text-muted">
                {description}
              </div>
            ) : null}
          </div>
          {dismissible ? (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="-mr-1.5 -mt-1 grid h-7 w-7 place-items-center rounded-md text-subtle hover:bg-surface-2 hover:text-fg"
            >
              <X size={16} aria-hidden />
            </button>
          ) : null}
        </div>
        {children ? <div className="min-h-0 overflow-auto px-5 pt-4">{children}</div> : null}
        {footer ? <div className="flex items-center justify-end gap-2 px-5 pb-5 pt-5">{footer}</div> : <div className="pb-5" />}
      </div>
    </div>,
    document.body,
  );
}

/**
 * Did a confirm action fail? `false` or `null` (what the stores return when they could not do it,
 * after their own toast), or a thrown error: then the dialog stays open, to try again or cancel.
 */
export function confirmFailed(result: unknown): boolean {
  return result === false || result === null;
}

/**
 * Ask before an action. With tone "danger" the focus starts on Cancel, so Enter never destroys
 * by accident (UX_PLAN U6); with tone "primary" on the action. `onConfirm` returns false (or null)
 * when it failed: the dialog then stays open.
 */
export function ConfirmDialog({
  open,
  onClose,
  title,
  children,
  confirmLabel,
  tone = "primary",
  onConfirm,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  tone?: "primary" | "danger";
  onConfirm: () => Promise<unknown> | unknown;
}) {
  const [busy, setBusy] = useState(false);
  const danger = tone === "danger";
  return (
    <Dialog
      open={open}
      onClose={() => !busy && onClose()}
      title={title}
      description={children}
      footer={
        <>
          <Button data-autofocus={danger || undefined} variant={danger ? "secondary" : "ghost"} onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            data-autofocus={danger ? undefined : true}
            variant={danger ? "danger" : "primary"}
            loading={busy}
            onClick={async () => {
              setBusy(true);
              let failed = false;
              try {
                failed = confirmFailed(await onConfirm());
              } catch {
                failed = true; // the action says why (its toast); the dialog stays, to try again
              } finally {
                setBusy(false);
              }
              if (!failed) onClose();
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    />
  );
}

/** Ask for a name (create / rename). `onSubmit` returns false to keep the dialog open. */
export function PromptDialog({
  open,
  onClose,
  title,
  description,
  label,
  initial = "",
  placeholder,
  confirmLabel,
  onSubmit,
  maxLength = 80,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  label: string;
  initial?: string;
  placeholder?: string;
  confirmLabel: string;
  onSubmit: (value: string) => Promise<boolean | void> | boolean | void;
  maxLength?: number;
}) {
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const inputId = useId();

  useEffect(() => {
    if (open) {
      setValue(initial);
      setTouched(false);
      setBusy(false);
    }
  }, [open, initial]);

  const trimmed = value.trim();
  const error = !trimmed ? "Enter a name." : /[\\/:*?"<>|]/.test(trimmed) ? 'Avoid \\ / : * ? " < > |' : null;

  const submit = async () => {
    setTouched(true);
    if (error || busy) return;
    setBusy(true);
    try {
      const ok = await onSubmit(trimmed);
      if (ok !== false) onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={() => !busy && onClose()}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" loading={busy} onClick={submit} disabled={touched && !!error}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label htmlFor={inputId} className="mb-1.5 block text-xs font-medium text-muted">
          {label}
        </label>
        <TextInput
          id={inputId}
          data-autofocus
          value={value}
          maxLength={maxLength}
          placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)}
          onFocus={(e) => e.currentTarget.select()}
          invalid={touched && !!error}
          aria-invalid={touched && !!error}
          aria-describedby={touched && error ? `${inputId}-err` : undefined}
        />
        <div id={`${inputId}-err`} className={cn("mt-1.5 h-4 text-xs", touched && error ? "text-danger" : "text-transparent")}>
          {touched && error ? error : "."}
        </div>
      </form>
    </Dialog>
  );
}
