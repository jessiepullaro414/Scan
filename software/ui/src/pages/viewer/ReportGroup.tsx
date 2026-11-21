/**
 * The "Report" group of the viewer's Download panel (FEATURE_PLAN.md I3; APP_API.md "The report"): what the report
 * holds beyond the scan's own data (the tolerance for the Check answer, taken from the Check panel's field; numbers in
 * the part's frame when the scan has one; which kept seam's flush), the views of the model the user adds ("Add this
 * view to the report": a picture of the 3D view as it is, ViewCapture.tsx), shown as small pictures that can be
 * removed and kept in the page's memory only, and the three downloads: the report as one HTML page, the kept features
 * as CSV and as JSON. The engine builds them on request and writes nothing.
 */
import { CircleHelp, FileJson, FileSpreadsheet, FileText, ImagePlus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/http";
import type { KeptSeam, ReportFile } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Select, Switch, TextInput } from "../../components/ui/Form";
import { Tooltip } from "../../components/ui/Popover";
import { saveBlob } from "../../lib/download";
import { fmtSize } from "../../lib/format";
import { addView, LIMITS_TEXT, MAX_VIEWS, removeView, reportRequest, type ReportView } from "../../lib/report";
import { toast } from "../../store/toasts";
import { captureView } from "./ViewCapture";

const HELP = {
  report:
    "One page with everything saved with this scan: how it was made, the calibration, the Scan check, the fitted shapes with their ±, relations, sections, the comparison with the CAD model, the flush along a seam, and every warning that was not resolved. Pictures and drawings are inside the page; it fetches nothing.",
  pm: "Every ± in the report is the spread from scan to scan (1σ), not the accuracy. No accuracy figure is claimed until the scanner has been measured against reference parts.",
  pdf: "Open the page in any browser and print it: “Save as PDF” gives a PDF, page numbers included (Chrome and Edge).",
  views: "A picture of the 3D view as it is now, with the fitted shapes' numbers and names marked. Turn and zoom the mesh first, then add the view.",
} as const;

function Help({ text, label }: { text: string; label: string }) {
  return (
    <Tooltip side="top" content={<div className="max-w-[280px] text-muted">{text}</div>}>
      <button type="button" className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:text-fg" aria-label={`${label}: ${text}`}>
        <CircleHelp size={13} aria-hidden />
      </button>
    </Tooltip>
  );
}

let viewSeq = 0;

export function ReportGroup({
  pid,
  sid,
  checkTolerance,
  hasFrame,
  seams,
  views,
  onViews,
  ready,
}: {
  pid: string;
  sid: string;
  /** The Check panel's tolerance (± mm). */
  checkTolerance: number;
  hasFrame: boolean;
  seams: KeptSeam[];
  /** The views added (the page's memory). */
  views: ReportView[];
  onViews: (v: ReportView[]) => void;
  /** The mesh is on screen (a view can be added). */
  ready: boolean;
}) {
  const [withCheck, setWithCheck] = useState(true);
  const [tolerance, setTolerance] = useState<string>(String(checkTolerance));
  const [edited, setEdited] = useState(false);
  const [inPart, setInPart] = useState(false);
  const [seam, setSeam] = useState<string>("");
  const [busy, setBusy] = useState<ReportFile | null>(null);
  // the Check panel's field, until a value is typed here
  useEffect(() => {
    if (!edited) setTolerance(String(checkTolerance));
  }, [checkTolerance, edited]);
  useEffect(() => {
    if (seam && !seams.some((s) => s.id === seam)) setSeam("");
  }, [seams, seam]);
  const tol = Number(tolerance.replace(",", "."));
  const tolOk = Number.isFinite(tol) && tol > 0 && tol <= 100;

  const addThisView = () => {
    try {
      const shot = captureView();
      if (!shot) {
        toast.warn("No view to add", "Open the mesh first.");
        return;
      }
      const r = addView(views, { id: `v${++viewSeq}`, caption: "", data: shot.data, width: shot.width, height: shot.height });
      if (r.error) toast.warn("Couldn't add the view", r.error);
      else onViews(r.views);
    } catch (e) {
      toast.error("Couldn't make a picture of the view", e);
    }
  };

  const get = async (name: ReportFile) => {
    setBusy(name);
    try {
      const body = reportRequest({ tolerance: withCheck && tolOk ? tol : null, inPartFrame: inPart, hasFrame, seam: seam || null, views: name === "report.html" ? views : [] });
      const { blob, filename } = await api.reportFile(pid, sid, name, body);
      saveBlob(blob, filename);
    } catch (e) {
      toast.error(name === "report.html" ? "Couldn't make the report" : "Couldn't make the features file", e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mt-4 space-y-2.5 border-t border-line pt-3">
      <div className="flex items-center gap-1">
        <FileText size={13} className="text-muted" aria-hidden />
        <span className="flex-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">Report</span>
        <Help text={HELP.report} label="The report" />
      </div>

      <div className="flex items-center gap-1.5">
        <div className="min-w-0 flex-1">
          <Switch checked={withCheck} onChange={setWithCheck} label="Tolerance for the report's Scan check, ± mm" />
        </div>
        <div className="relative w-[84px] shrink-0">
          <TextInput
            mono
            inputMode="decimal"
            aria-label="Tolerance for the report's Scan check, ± mm"
            value={tolerance}
            disabled={!withCheck}
            onChange={(e) => {
              setTolerance(e.target.value);
              setEdited(true);
            }}
            onKeyDown={(e) => e.stopPropagation()}
            className={tolOk || !withCheck ? "pr-8 text-right" : "border-danger pr-8 text-right"}
          />
          <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-subtle">mm</span>
        </div>
      </div>
      {hasFrame ? <Switch checked={inPart} onChange={setInPart} label="Numbers along the part's axes" /> : null}
      {seams.length > 1 ? (
        <div className="flex items-center gap-1.5">
          <span className="shrink-0 text-xs text-muted">Seam</span>
          <Select className="min-w-0 flex-1" value={seam} aria-label="The kept seam whose flush the report shows" onChange={(e) => setSeam(e.target.value)}>
            <option value="">{seams[0].name} (the first)</option>
            {seams.slice(1).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </div>
      ) : null}

      <div>
        <div className="mb-1 flex items-center gap-1">
          <span className="flex-1 text-xs text-muted">
            Views of the model{views.length ? ` (${views.length} of ${MAX_VIEWS})` : ""}
          </span>
          <Help text={`${HELP.views} ${LIMITS_TEXT}`} label="Views of the model" />
        </div>
        {views.length ? (
          <ul className="mb-2 grid grid-cols-3 gap-1.5">
            {views.map((v) => (
              <li key={v.id} className="group relative overflow-hidden rounded-md border border-line bg-surface-2/60" title={`${v.title}: ${fmtSize(v.width, v.height, "px")}, ${(v.bytes / 2 ** 20).toFixed(1)}\u202fMB`}>
                <img src={`data:image/png;base64,${v.data}`} alt={v.title} className="block aspect-[4/3] w-full object-cover" />
                <div className="truncate px-1 py-0.5 text-[10px] text-subtle">{v.title}</div>
                <IconButton icon={X} size="xs" label={`Remove ${v.title} from the report`} className="absolute right-0.5 top-0.5 bg-panel/80 opacity-70 group-hover:opacity-100" onClick={() => onViews(removeView(views, v.id))} />
              </li>
            ))}
          </ul>
        ) : null}
        <Button size="sm" className="w-full" icon={ImagePlus} disabled={!ready || views.length >= MAX_VIEWS} onClick={addThisView} title={views.length >= MAX_VIEWS ? `A report takes at most ${MAX_VIEWS} views` : "A picture of the 3D view as it is now"}>
          Add this view to the report
        </Button>
      </div>

      <Button size="sm" variant="primary" className="w-full" icon={FileText} loading={busy === "report.html"} disabled={!!busy || (withCheck && !tolOk)} onClick={() => void get("report.html")}>
        Report (HTML)
      </Button>
      <div className="grid grid-cols-2 gap-2">
        <Button size="sm" icon={FileSpreadsheet} loading={busy === "features.csv"} disabled={!!busy} onClick={() => void get("features.csv")} title="The saved fitted shapes and relations, one row each, values and ± in separate columns (opens in a spreadsheet)">
          Features (CSV)
        </Button>
        <Button size="sm" icon={FileJson} loading={busy === "features.json"} disabled={!!busy} onClick={() => void get("features.json")} title="The kept features as JSON, with what the report worked out beside them">
          Features (JSON)
        </Button>
      </div>
      <div className="flex items-start gap-1 text-[11px] leading-4 text-subtle">
        <span className="flex-1">The HTML page prints to PDF from any browser. Every ± in it is the spread from scan to scan, not the accuracy.</span>
        <Help text={`${HELP.pdf} ${HELP.pm}`} label="Printing the report" />
      </div>
    </div>
  );
}
