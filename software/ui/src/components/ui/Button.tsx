import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "../../lib/util";
import { Spinner } from "./Feedback";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "danger-ghost" | "outline";
export type ButtonSize = "xs" | "sm" | "md" | "lg" | "xl";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-accent-fg hover:bg-accent-hover active:bg-accent-press shadow-[inset_0_1px_0_rgb(255_255_255/0.16),0_1px_2px_rgb(0_0_0/0.3)]",
  secondary: "bg-surface-2 text-fg border border-line hover:bg-surface-3 hover:border-line-strong",
  outline: "bg-transparent text-fg border border-line-strong hover:bg-surface-2",
  ghost: "bg-transparent text-muted hover:text-fg hover:bg-surface-2",
  danger: "bg-danger text-white hover:brightness-110 shadow-[inset_0_1px_0_rgb(255_255_255/0.16)]",
  "danger-ghost": "bg-transparent text-danger hover:bg-danger-soft",
};

const SIZES: Record<ButtonSize, string> = {
  xs: "h-6 px-2 text-xs gap-1 rounded-[5px]",
  sm: "h-7 px-2.5 text-xs gap-1.5 rounded-md",
  md: "h-8 px-3 text-sm gap-2 rounded-md",
  lg: "h-10 px-4 text-sm gap-2 rounded-lg",
  xl: "h-12 px-6 text-base gap-2.5 rounded-xl",
};

const ICON: Record<ButtonSize, number> = { xs: 12, sm: 14, md: 15, lg: 16, xl: 18 };

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: LucideIcon;
  iconRight?: LucideIcon;
  loading?: boolean;
  /** Keyboard shortcut shown at the end of the label. */
  kbd?: string;
  children?: ReactNode;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon: Icon, iconRight: IconRight, loading, kbd, className, children, disabled, type, ...rest },
  ref,
) {
  const s = ICON[size];
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "relative inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap font-medium",
        "transition-[background-color,border-color,color,box-shadow,filter,opacity] duration-150",
        "disabled:opacity-45 active:translate-y-px disabled:active:translate-y-0",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner size={s} /> : Icon ? <Icon size={s} strokeWidth={2} aria-hidden /> : null}
      {children}
      {IconRight && !loading ? <IconRight size={s} strokeWidth={2} aria-hidden /> : null}
      {kbd ? (
        <kbd
          className={cn(
            "ml-1 rounded border px-1 font-mono text-[10px] leading-4",
            variant === "primary" || variant === "danger" ? "border-white/30 text-white/80" : "border-line-strong text-subtle",
          )}
        >
          {kbd}
        </kbd>
      ) : null}
    </button>
  );
});

export type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: LucideIcon;
  label: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  active?: boolean;
  loading?: boolean;
};

const SQUARE: Record<ButtonSize, string> = {
  xs: "h-6 w-6 rounded-[5px]",
  sm: "h-7 w-7 rounded-md",
  md: "h-8 w-8 rounded-md",
  lg: "h-10 w-10 rounded-lg",
  xl: "h-12 w-12 rounded-xl",
};

/** A square icon-only button. `label` is the accessible name and the native tooltip. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon: Icon, label, variant = "ghost", size = "md", active, loading, className, type, disabled, title, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      aria-label={label}
      title={title ?? label}
      aria-pressed={active === undefined ? undefined : active}
      disabled={disabled || loading}
      className={cn(
        "inline-flex shrink-0 items-center justify-center transition-colors duration-150 disabled:opacity-40",
        VARIANTS[variant],
        active && "!bg-accent-soft !text-accent",
        SQUARE[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner size={ICON[size]} /> : <Icon size={ICON[size] + 1} strokeWidth={1.9} aria-hidden />}
    </button>
  );
});
