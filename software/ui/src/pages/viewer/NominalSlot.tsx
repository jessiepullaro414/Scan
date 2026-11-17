/**
 * A nominal value beside a measured one (FEATURE_PLAN.md C3; APP_API.md "Nominal values"), in a fit's card next to
 * its diameter and in a kept relation's card next to each value:
 *
 * - **an offer:** a small chip such as "Ø 14?"; hovering it shows the engine's sentence (why it is offered, "only
 *   because of a known bias" where that is so, and that the biases are simulator numbers); clicking it gives
 *   "Accept", "Other value…" and "Ignore". Ignoring hides the chip for this fit or relation until its value is
 *   measured again (kept in this browser only: lib/nominal.ts);
 * - **an approved value:** "nominal Ø 14" next to the measured "14.02 ± 0.03", with a cross to remove it, and
 *   "check again" in the warn colour when the engine marked it (measured again, the new value does not allow it);
 * - **no offer:** nothing, but a small pencil when the card is hovered, for "Other value…".
 *
 * The measured value is never changed: an approval is kept beside it and used in the CAD files.
 */
import { Check, EyeOff, Pencil, TriangleExclamationPoint, X } from "lucide-react";
import { useRef, useState } from "react";
import type { ApprovedNominal, NominalAnswer, NominalOffer, NominalValueName } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { TextInput } from "../../components/ui/Form";
import { Menu, Popover, Tooltip } from "../../components/ui/Popover";
import { approvedText, approvedTip, chipText, fmtNominal, measuredText, nominalText, parseNominal, suggestionTip } from "../../lib/nominal";
import { cn } from "../../lib/util";

/** What a slot can do: approve (the value offered, or one typed), remove an approval, ignore an offer. */
export type NominalActions = {
  approve: (id: string, value: NominalValueName, nominal: number, source: "suggested" | "entered") => Promise<boolean>;
  remove: (id: string, value: NominalValueName) => void;
  ignore: (id: string, value: NominalValueName, measured: number) => void;
  ignored: (id: string, value: NominalValueName, measured: number) => boolean;
};

export function NominalSlot({
  id,
  value,
  measuredLabel,
  answer,
  offer,
  approval,
  actions,
}: {
  /** The kept fit's or relation's id. */
  id: string;
  /** The value this slot is beside ("diameter", "angle", ...). */
  value: NominalValueName;
  /** What the value is called in the "Other value…" box, e.g. "Diameter". */
  measuredLabel: string;
  answer: NominalAnswer | null | undefined;
  offer: NominalOffer | null;
  approval: ApprovedNominal | null;
  actions: NominalActions;
}) {
  const anchor = useRef<HTMLSpanElement>(null);
  const [entering, setEntering] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const offered = offer && offer.suggestions.length && !(approval && !approval.check_again) && !actions.ignored(id, value, offer.measured) ? offer : null;
  const unitText = value === "angle" ? "°" : "mm";

  const openEntry = () => {
    setText(approval ? fmtNominal(approval.nominal) : offered ? fmtNominal(offered.suggestions[0].nominal) : "");
    setError(null);
    setEntering(true);
  };
  const submit = async () => {
    const p = parseNominal(text, value);
    if (!p.ok) {
      setError(p.error);
      return;
    }
    setBusy(true);
    const ok = await actions.approve(id, value, p.nominal, "entered");
    setBusy(false);
    if (ok) setEntering(false);
  };

  return (
    <span ref={anchor} className="inline-flex flex-wrap items-center justify-end gap-1 font-sans" onClick={(e) => e.stopPropagation()}>
      {approval ? (
        <span className="inline-flex items-center gap-0.5">
          <Tooltip side="top" content={<div className="leading-4">{approvedTip(approval)}</div>}>
            <span
              tabIndex={0}
              className={cn("rounded px-1 text-[11px] font-semibold tnum", approval.check_again ? "bg-warn-soft text-warn" : "bg-accent-soft text-accent")}
              aria-label={`${approvedText(approval)}${approval.check_again ? ", check again" : ""}`}
            >
              {approvedText(approval)}
            </span>
          </Tooltip>
          {approval.check_again ? (
            <span className="inline-flex items-center gap-0.5 text-[10.5px] font-semibold text-warn" title={approval.check_reason ?? undefined}>
              <TriangleExclamationPoint size={11} aria-hidden />
              check again
            </span>
          ) : null}
          <IconButton icon={X} size="xs" label={`Remove the nominal ${nominalText(approval.value, approval.nominal, approval.word)}`} className="h-4 w-4 opacity-60 hover:opacity-100" onClick={() => actions.remove(id, approval.value)} />
        </span>
      ) : null}
      {offered
        ? offered.suggestions.map((s) => (
            <Menu
              key={s.nominal}
              label={`Nominal ${chipText(value, s)}`}
              width={200}
              items={[
                { label: `Accept ${nominalText(value, s.nominal, s.word)}`, icon: Check, onSelect: () => void actions.approve(id, value, s.nominal, "suggested") },
                { label: "Other value…", icon: Pencil, onSelect: openEntry },
                { label: "Ignore", icon: EyeOff, description: "Until it is measured again", onSelect: () => actions.ignore(id, value, offered.measured) },
              ]}
              renderTrigger={(p) => (
                <Tooltip
                  side="top"
                  content={
                    <div className="space-y-1 leading-4">
                      {suggestionTip(s, answer ?? null).map((l, i) => (
                        <div key={l} className={i ? "text-muted" : undefined}>
                          {l}
                        </div>
                      ))}
                    </div>
                  }
                >
                  <button
                    type="button"
                    {...p}
                    className={cn(
                      "rounded-full border border-dashed px-1.5 text-[11px] font-semibold leading-[18px] tnum hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-accent",
                      s.allowed_by === "bias" ? "border-warn/60 text-warn" : "border-accent/60 text-accent",
                    )}
                    aria-label={`Suggested nominal ${chipText(value, s)}${s.allowed_by === "bias" ? " (only because of a known bias)" : ""}`}
                  >
                    {chipText(value, s)}
                  </button>
                </Tooltip>
              )}
            />
          ))
        : null}
      {!approval && !offered ? (
        <IconButton icon={Pencil} size="xs" label={`Other value for the ${measuredLabel.toLowerCase()}: enter a nominal value`} title="Other value…: enter a nominal value" className="h-4 w-4 opacity-0 group-hover:opacity-60 hover:opacity-100 focus-visible:opacity-100" onClick={openEntry} />
      ) : approval ? (
        <IconButton icon={Pencil} size="xs" label={`Other value for the ${measuredLabel.toLowerCase()}`} title="Other value…" className="h-4 w-4 opacity-0 group-hover:opacity-60 hover:opacity-100 focus-visible:opacity-100" onClick={openEntry} />
      ) : null}
      <Popover open={entering} onClose={() => setEntering(false)} anchor={anchor} width={240} label={`Nominal ${measuredLabel.toLowerCase()}`}>
        <form
          className="space-y-2 p-3 font-sans"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <div className="text-xs font-semibold text-fg">Nominal {measuredLabel.toLowerCase()}</div>
          <div className="flex items-center gap-1.5">
            <TextInput autoFocus mono value={text} invalid={!!error} onChange={(e) => setText(e.target.value)} aria-label={`Nominal ${measuredLabel.toLowerCase()} in ${unitText}`} className="flex-1" />
            <span className="text-xs text-subtle">{unitText}</span>
          </div>
          {error ? <div className="text-[11px] leading-4 text-danger">{error}</div> : null}
          {offer ? <div className="text-[11px] leading-4 text-subtle">Measured {measuredText(offer.measured, offer.sigma, offer.unit)}: it stays as it is.</div> : null}
          <div className="flex justify-end gap-1.5">
            <Button size="xs" variant="ghost" type="button" onClick={() => setEntering(false)}>
              Cancel
            </Button>
            <Button size="xs" variant="primary" type="submit" loading={busy}>
              Approve
            </Button>
          </div>
        </form>
      </Popover>
    </span>
  );
}
