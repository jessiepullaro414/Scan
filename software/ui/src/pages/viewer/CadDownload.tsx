/**
 * The Download panel's "For CAD" group (FEATURE_PLAN.md C2; APP_API.md "For CAD"): the scan's kept fits as a STEP
 * file and as a FreeCAD macro, in the part's frame when the scan has one. A switch picks each plane's real outline
 * instead of its rectangle, with a hover card that says what it does and what it costs (both files' sizes, the
 * points, the holes left out). A line says what will be exported and in which frame; the warnings (fits made on an
 * earlier mesh, a frame out of date) and the writer's notes are shown under it. Disabled, with the reason, while the
 * scan has no kept fits. The words are cadExport.ts's.
 *
 * Approved nominal values (FEATURE_PLAN.md C3): the files use the approved sizes, and a line says how many, and how
 * many approved relations are only listed in the notes or applied by moving a shape (chosen in the relation's card),
 * with a line per move saying how far its shape was turned and slid. Once anything is approved a second switch,
 * "Measured values only", downloads the files as if nothing were approved.
 */
import { CircleHelp, Download, TriangleExclamationPoint } from "lucide-react";
import { useEffect, useState } from "react";
import { api, sentence } from "../../api/http";
import type { CadBoundary, CadFile, CadSummary, FeatureSet } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Switch } from "../../components/ui/Form";
import { Tooltip } from "../../components/ui/Popover";
import { downloadFile } from "../../lib/download";
import { CAD_MEASURED_HELP, CAD_OUTLINE_HELP, cadDownloadName, cadLine, cadMoveLines, cadNominalLine, cadUnavailable, outlineCost } from "./cadExport";

type Summaries = Record<CadBoundary, CadSummary | null>;

/**
 * The summaries for the kept set on screen, read again when it changes. The rectangle one is cheap and always
 * read. The outline one makes the server simplify every plane's real outline (about a second for a large plane),
 * so it is only read once the user has asked for it (`withOutline`: the switch is on, or its help was opened).
 */
function useCadSummaries(pid: string, sid: string, kept: FeatureSet | null, enabled: boolean, withOutline: boolean, measuredOnly: boolean): { sums: Summaries; error: string | null } {
  const [sums, setSums] = useState<Summaries>({ rectangle: null, outline: null });
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    if (!enabled) {
      setSums({ rectangle: null, outline: null });
      return;
    }
    let live = true;
    for (const b of (withOutline ? ["rectangle", "outline"] : ["rectangle"]) as CadBoundary[]) {
      api.cadSummary(pid, sid, b, measuredOnly).then(
        (s) => live && setSums((x) => ({ ...x, [b]: s })),
        (e: unknown) => live && setError(sentence((e as { detail?: string }).detail ?? String(e))),
      );
    }
    return () => {
      live = false;
    };
  }, [pid, sid, kept, enabled, withOutline, measuredOnly]);
  return { sums, error };
}

export function CadDownload({ pid, sid, stem, kept, hasMesh }: { pid: string; sid: string; stem: string; kept: FeatureSet | null; hasMesh: boolean }) {
  const [outline, setOutline] = useState(false);
  const [asked, setAsked] = useState(false); // the outline's help was opened: its cost is worth reading
  const [measuredOnly, setMeasuredOnly] = useState(false);
  const why = cadUnavailable(kept?.features.length ?? 0, hasMesh);
  const { sums, error } = useCadSummaries(pid, sid, kept, !why, outline || asked, measuredOnly);
  const boundary: CadBoundary = outline ? "outline" : "rectangle";
  const sum = sums[boundary];
  const cost = outlineCost(sums.rectangle, sums.outline);
  const get = (file: CadFile) => void downloadFile(api.cadFileUrl(pid, sid, file, boundary, measuredOnly), cadDownloadName(stem, file));
  // the measured-only switch is there once something is approved (or while it is on)
  const approved = measuredOnly || !!kept && [...kept.features, ...kept.relations].some((x) => (x.nominals ?? []).length > 0);
  const nominalLine = sum ? cadNominalLine(sum.nominal) : null;
  // the moves applied (one not applied is among the warnings, with why)
  const moveLines = sum?.nominal ? cadMoveLines({ ...sum.nominal, moves: (sum.nominal.moves ?? []).filter((m) => m.applied) }) : [];
  const where = sum ? ` (${sum.frame_label})` : "";

  return (
    <div className="mt-3 border-t border-line pt-3" aria-label="For CAD">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-subtle">For CAD</div>
      <div className="grid grid-cols-2 gap-2">
        <Button size="sm" icon={Download} disabled={!!why} onClick={() => get("fits.step")} title={why ?? `The kept fits as a STEP file, in mm${where}`}>
          STEP
        </Button>
        <Button size="sm" icon={Download} disabled={!!why} onClick={() => get("fits.FCMacro")} title={why ?? `A FreeCAD macro that builds the kept fits as named objects${where}`}>
          FreeCAD macro
        </Button>
      </div>
      <div className="mt-2 flex items-start gap-1">
        <div className="min-w-0 flex-1">
          <Switch checked={outline} onChange={setOutline} disabled={!!why} label="Planes with their real outline" />
        </div>
        <Tooltip
          side="top"
          content={
            <div className="max-w-[300px] space-y-1">
              <div className="font-semibold">Real outline</div>
              <div className="text-muted">{CAD_OUTLINE_HELP}</div>
              {cost.map((l) => (
                <div key={l} className="text-muted">
                  {l}
                </div>
              ))}
            </div>
          }
        >
          <button type="button" onMouseEnter={() => setAsked(true)} onFocus={() => setAsked(true)} className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:text-fg" aria-label={`Real outline: ${CAD_OUTLINE_HELP} ${cost.join(" ")}`}>
            <CircleHelp size={13} aria-hidden />
          </button>
        </Tooltip>
      </div>
      {approved ? (
        <div className="mt-1 flex items-start gap-1">
          <div className="min-w-0 flex-1">
            <Switch checked={measuredOnly} onChange={setMeasuredOnly} disabled={!!why} label="Measured values only" />
          </div>
          <Tooltip side="top" content={<div className="max-w-[300px] text-muted">{CAD_MEASURED_HELP}</div>}>
            <button type="button" className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:text-fg" aria-label={`Measured values only: ${CAD_MEASURED_HELP}`}>
              <CircleHelp size={13} aria-hidden />
            </button>
          </Tooltip>
        </div>
      ) : null}
      <p className="mt-2 text-xs text-subtle">{why ?? error ?? (sum ? cadLine(sum) : "Reading the kept fits…")}</p>
      {!why && nominalLine ? <p className="mt-1 text-xs text-subtle" title={sum?.nominal?.relations_note ?? undefined}>{nominalLine}</p> : null}
      {!why && moveLines.length ? (
        <ul className="mt-1 space-y-0.5 text-[11.5px] leading-4 text-subtle" aria-label="Shapes moved in the CAD files">
          {moveLines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      ) : null}
      {!why && sum && sum.warnings.length ? (
        <ul className="mt-1.5 space-y-1 text-[11.5px] leading-4 text-warn">
          {sum.warnings.map((w) => (
            <li key={w} className="flex items-start gap-1.5">
              <TriangleExclamationPoint size={12} className="mt-px shrink-0" aria-hidden />
              <span>{sentence(w)}.</span>
            </li>
          ))}
        </ul>
      ) : null}
      {!why && sum && sum.notes.length ? (
        <details className="mt-1.5 text-[11.5px] leading-4 text-subtle">
          <summary className="cursor-pointer select-none hover:text-fg">Notes ({sum.notes.length}), also in the files</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {sum.notes.map((n) => (
              <li key={n}>{sentence(n)}.</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
