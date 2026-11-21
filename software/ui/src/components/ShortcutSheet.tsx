/**
 * The keyboard shortcut sheet of a page (UX_PLAN U25): a dialog opened with "?" or the page's keyboard button,
 * closed with Esc, the close button or a click outside. The lines come from `lib/shortcuts.ts`.
 */
import { Keyboard } from "lucide-react";
import { Fragment } from "react";
import type { Sheet } from "../lib/shortcuts";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { Kbd } from "./ui/Feedback";

/** "Ctrl+Shift+A", "Esc Esc", "1–7", "[ ]" as key caps. */
export function KeyCaps({ keys }: { keys: string }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {keys.split(" ").map((press, i) => (
        <Fragment key={i}>
          {press.split("+").map((k, j) => {
            const bare = k.replace(/,$/, "");
            const span = /^(\d+)–(\d+)$/.exec(bare);
            return (
              <Fragment key={j}>
                {j ? <span className="text-[10.5px] text-subtle">+</span> : null}
                {span ? (
                  <>
                    <Kbd>{span[1]}</Kbd>
                    <span className="text-[10.5px] text-subtle">–</span>
                    <Kbd>{span[2]}</Kbd>
                  </>
                ) : (
                  <Kbd>{bare}</Kbd>
                )}
                {bare !== k ? <span className="text-[10.5px] text-subtle">,</span> : null}
              </Fragment>
            );
          })}
        </Fragment>
      ))}
    </span>
  );
}

export function ShortcutSheet({ open, onClose, sheet }: { open: boolean; onClose: () => void; sheet: Sheet }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={sheet.title}
      width={sheet.groups.length > 1 ? 640 : 420}
      footer={
        <Button variant="primary" data-autofocus onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className={sheet.groups.length > 1 ? "grid gap-x-6 gap-y-4 sm:grid-cols-2" : ""}>
        {sheet.groups.map((g) => (
          <section key={g.title} aria-label={g.title}>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-subtle">{g.title}</h3>
            <dl className="space-y-1.5">
              {g.items.map((i) => (
                <div key={i.keys} className="grid grid-cols-[minmax(84px,auto)_1fr] items-start gap-3 text-sm">
                  <dt className="pt-px">
                    <KeyCaps keys={i.keys} />
                  </dt>
                  <dd className="text-muted">{i.text}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
      {sheet.note ? <p className="mt-4 text-xs text-subtle">{sheet.note}</p> : null}
    </Dialog>
  );
}

/** The page's way into its sheet: a small keyboard button saying "?" (the key works too). */
export function ShortcutButton({ onClick, className }: { onClick: () => void; className?: string }) {
  return (
    <Button size="xs" variant="ghost" icon={Keyboard} onClick={onClick} className={className} title="All keyboard shortcuts (?)">
      Keyboard shortcuts <Kbd className="ml-1">?</Kbd>
    </Button>
  );
}
