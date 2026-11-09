import { ChevronDown, type LucideIcon } from "lucide-react";
import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { cn } from "../../lib/util";

const FIELD =
  "h-8 w-full rounded-md border bg-surface-2 px-2.5 text-sm text-fg placeholder:text-subtle transition-[border-color,box-shadow] duration-150 " +
  "hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--c-accent-soft)] focus:outline-none disabled:opacity-50";

export const TextInput = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean; icon?: LucideIcon; mono?: boolean }
>(function TextInput({ className, invalid, icon: Icon, mono, ...rest }, ref) {
  const input = (
    <input
      ref={ref}
      spellCheck={false}
      className={cn(
        FIELD,
        invalid ? "border-danger" : "border-line",
        Icon && "pl-8",
        mono && "font-mono text-[12.5px]",
        !Icon && className,
      )}
      {...rest}
    />
  );
  if (!Icon) return input;
  return (
    <div className={cn("relative", className)}>
      <Icon size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" aria-hidden />
      {input}
    </div>
  );
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <div className={cn("relative", className)}>
      <select ref={ref} className={cn(FIELD, "appearance-none border-line pr-8")} {...rest}>
        {children}
      </select>
      <ChevronDown size={15} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-subtle" aria-hidden />
    </div>
  );
});

/** Label + control + help text, with the label wired to the control. */
export function Field({
  label,
  help,
  children,
  htmlFor,
  className,
  aside,
}: {
  label: string;
  help?: ReactNode;
  children: ReactNode;
  htmlFor?: string;
  className?: string;
  aside?: ReactNode;
}) {
  return (
    <div className={className}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="text-xs font-medium text-muted">
          {label}
        </label>
        {aside}
      </div>
      {children}
      {help ? <div className="mt-1.5 text-xs text-subtle">{help}</div> : null}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  description,
  id,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
  description?: ReactNode;
  id?: string;
}) {
  const auto = useId();
  const sid = id ?? auto;
  return (
    <div className={cn("flex items-start justify-between gap-3", disabled && "opacity-50")}>
      <div className="min-w-0">
        <label htmlFor={sid} className="block cursor-pointer text-sm text-fg">
          {label}
        </label>
        {description ? <div className="mt-0.5 text-xs text-subtle">{description}</div> : null}
      </div>
      <button
        id={sid}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors duration-150",
          checked ? "border-accent bg-accent" : "border-line-strong bg-surface-3",
        )}
      >
        <span
          className={cn(
            "inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform duration-150",
            checked ? "translate-x-[18px]" : "translate-x-[2px]",
          )}
        />
      </button>
    </div>
  );
}

export type SegmentOption<T extends string> = { value: T; label: string; icon?: LucideIcon; title?: string; disabled?: boolean };

/** A segmented control (radio group semantics). */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  disabled,
  size = "md",
  className,
}: {
  value: T;
  options: SegmentOption<T>[];
  onChange: (v: T) => void;
  label: string;
  disabled?: boolean;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn("inline-flex rounded-lg border border-line bg-surface-2 p-0.5", disabled && "opacity-50", className)}
      onKeyDown={(e) => {
        if (disabled) return;
        const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        // the next option that isn't disabled
        const i = options.findIndex((o) => o.value === value);
        for (let k = 1; k < options.length; k++) {
          const o = options[(i + step * k + options.length * k) % options.length];
          if (!o.disabled) return onChange(o.value);
        }
      }}
    >
      {options.map((o) => {
        const active = o.value === value;
        const Icon = o.icon;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            title={o.title}
            disabled={disabled || o.disabled}
            onClick={() => onChange(o.value)}
            className={cn(
              "inline-flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors duration-150",
              size === "sm" ? "h-6 px-2 text-xs" : "h-7 px-3 text-sm",
              active ? "bg-surface-3 text-fg shadow-sm ring-1 ring-line-strong" : "text-muted hover:text-fg",
              o.disabled && !disabled && "cursor-not-allowed opacity-45 hover:text-muted",
            )}
          >
            {Icon ? <Icon size={size === "sm" ? 13 : 14} aria-hidden /> : null}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Slider({
  value,
  onChange,
  onCommit,
  min = 0,
  max = 100,
  step = 1,
  label,
  disabled,
  id,
}: {
  value: number;
  onChange: (v: number) => void;
  onCommit?: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  label: string;
  disabled?: boolean;
  id?: string;
}) {
  const fill = ((value - min) / (max - min)) * 100;
  return (
    <input
      id={id}
      type="range"
      className="slider"
      aria-label={label}
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      style={{ ["--fill" as string]: `${fill}%` }}
      onChange={(e) => onChange(Number(e.target.value))}
      onPointerUp={(e) => onCommit?.(Number((e.target as HTMLInputElement).value))}
      onKeyUp={(e) => onCommit?.(Number((e.target as HTMLInputElement).value))}
    />
  );
}
