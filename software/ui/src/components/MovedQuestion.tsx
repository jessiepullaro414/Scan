/**
 * "Did you move or turn the part between the two scans? Yes / No" (UX_PLAN U15): asked with no answer chosen, on
 * the Align page and for a second pass; aligning and the pass's Start wait for it (lib/moved.ts). Two radio
 * buttons, each with what the merge then does with the table.
 */
import { CircleHelp } from "lucide-react";
import { useRef, type KeyboardEvent } from "react";
import { MOVED_ANSWERS, movedQuestion, type MovedAnswer } from "../lib/moved";
import { cn } from "../lib/util";

export function MovedQuestion({
  id,
  value,
  onChange,
  several = false,
  disabled = false,
  texts,
  question,
}: {
  /** The radio group's id: a "Answer it" button elsewhere focuses its first answer. */
  id: string;
  value: MovedAnswer;
  onChange: (moved: boolean) => void;
  several?: boolean;
  disabled?: boolean;
  /** What each answer does, under its label. */
  texts: { moved: string; same: string };
  /** Another wording of the question (the pass: "since the first scan"). */
  question?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const q = question ?? movedQuestion(several);
  // arrow keys move between the two answers and choose, as a radio group does
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    if (!["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(e.key)) return;
    e.preventDefault();
    const j = (i + 1) % MOVED_ANSWERS.length;
    refs.current[j]?.focus();
    onChange(MOVED_ANSWERS[j].moved);
  };
  return (
    <div>
      <div id={`${id}-label`} className="mb-2 flex items-start gap-1.5 text-sm font-medium text-fg">
        <CircleHelp size={14} className="mt-0.5 shrink-0 text-accent" aria-hidden />
        <span>{q}</span>
      </div>
      <div id={id} role="radiogroup" aria-labelledby={`${id}-label`} className="space-y-2">
        {MOVED_ANSWERS.map((a, i) => {
          const selected = value === a.moved;
          return (
            <button
              key={a.label}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={selected}
              // the first answer takes Tab while none is chosen, the chosen one after
              tabIndex={selected || (value === null && i === 0) ? 0 : -1}
              disabled={disabled}
              onClick={() => onChange(a.moved)}
              onKeyDown={(e) => onKey(e, i)}
              className={cn(
                "flex w-full items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-[border-color,background-color,box-shadow] disabled:opacity-60",
                selected ? "border-accent bg-accent-soft shadow-[0_0_0_3px_var(--c-accent-soft)]" : "border-line bg-surface-2 hover:border-line-strong",
              )}
            >
              <span
                className={cn("mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full border", selected ? "border-accent" : "border-line-strong")}
                aria-hidden
              >
                {selected ? <span className="h-2 w-2 rounded-full bg-accent" /> : null}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-fg">{a.label}</span>
                <span className="block text-xs leading-5 text-muted">{a.moved ? texts.moved : texts.same}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Focus the first answer of a question (the "Answer it" button beside a Start or Align that waits for it). */
export function focusMovedQuestion(id: string): void {
  const group = document.getElementById(id);
  group?.scrollIntoView({ block: "center", behavior: "smooth" });
  group?.querySelector<HTMLButtonElement>('[role="radio"]')?.focus({ preventScroll: true });
}
