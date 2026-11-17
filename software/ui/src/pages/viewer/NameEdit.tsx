/**
 * A kept item's name that the user can change in place: the name as text with a small pencil;
 * a click on the pencil (or a double click on the name) turns it into a field. Enter or leaving the
 * field saves, Escape cancels; an empty or unchanged name saves nothing.
 */
import { Pencil } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { IconButton } from "../../components/ui/Button";
import { cn } from "../../lib/util";

export const NAME_MAX = 80;

export function NameEdit({
  value,
  what,
  onRename,
  className,
}: {
  value: string;
  /** "fit", "measurement": for the labels ("Rename fit Top face"). */
  what: string;
  /** Resolves to false when the engine refused it (the field stays open). */
  onRename: (name: string) => Promise<boolean>;
  className?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);

  const start = () => {
    setDraft(value);
    setEditing(true);
  };
  const save = async () => {
    const name = draft.trim();
    if (!name || name === value) {
      setEditing(false);
      return;
    }
    setSaving(true);
    const ok = await onRename(name);
    setSaving(false);
    if (ok) setEditing(false);
  };

  if (editing)
    return (
      <input
        ref={input}
        value={draft}
        maxLength={NAME_MAX}
        disabled={saving}
        aria-label={`New name of ${what} ${value}`}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => void save()}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") void save();
          else if (e.key === "Escape") setEditing(false);
        }}
        className={cn("h-6 min-w-0 flex-1 rounded border border-accent-line bg-surface px-1.5 text-xs font-semibold text-fg outline-none", className)}
      />
    );
  return (
    <span className={cn("group/name inline-flex min-w-0 items-center gap-0.5", className)}>
      <span className="truncate" title={`${value} (double-click to rename)`} onDoubleClick={(e) => (e.stopPropagation(), start())}>
        {value}
      </span>
      <IconButton
        icon={Pencil}
        size="xs"
        label={`Rename ${what} ${value}`}
        className="opacity-0 group-hover/name:opacity-70 focus-visible:opacity-100"
        onClick={(e) => {
          e.stopPropagation();
          start();
        }}
      />
    </span>
  );
}
