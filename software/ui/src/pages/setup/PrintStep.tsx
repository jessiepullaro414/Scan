/** Step 3, Print targets: make the board, tags and projector mask; the printing rules; the 100 mm scale check. */
import {
  CircleCheck,
  Download,
  FileText,
  FolderOpen,
  Layers,
  Printer,
  Ruler,
  Scaling,
  Sparkles,
  SquareDashed,
  TriangleExclamationPoint,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { Paper, PrintFile } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ProgressBar, Spinner } from "../../components/ui/Feedback";
import { Segmented, TextInput } from "../../components/ui/Form";
import { Callout, Code, Section } from "../../components/ui/Layout";
import { sentence } from "../../api/http";
import { downloadFile } from "../../lib/download";
import { fmtBytes, fmtRelative } from "../../lib/format";
import { cn, copyText } from "../../lib/util";
import { isActive, useJobs } from "../../store/jobs";
import { useSetup } from "../../store/setup";
import { toast } from "../../store/toasts";
import { StepFooter, StepHeader, StepPage } from "./common";

const KIND_LABEL: Record<PrintFile["kind"], string> = { board: "Calibration", tags: "Marker mode", mask: "Option B projector" };

function FileCard({ f }: { f: PrintFile }) {
  const [failed, setFailed] = useState(false);
  const isMask = f.kind === "mask";
  return (
    <li className="flex flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-sm">
      <div className="relative aspect-[1.294] w-full overflow-hidden border-b border-line bg-[repeating-conic-gradient(var(--c-surface-2)_0%_25%,var(--c-surface-3)_0%_50%)] [background-size:16px_16px]">
        {f.preview_url && !failed ? (
          <img src={f.preview_url} alt={`Preview of ${f.title}`} className="absolute inset-0 h-full w-full object-contain p-3 drop-shadow-md" onError={() => setFailed(true)} draggable={false} />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-muted">
            <div className="flex flex-col items-center gap-1.5 text-xs">
              <FileText size={26} aria-hidden />
              {f.name.split(".").pop()?.toUpperCase()} file
            </div>
          </div>
        )}
        <span className="absolute left-2.5 top-2.5">
          <Badge tone={isMask ? "accent" : "neutral"}>{KIND_LABEL[f.kind]}</Badge>
        </span>
      </div>
      <div className="flex flex-1 flex-col p-4">
        <div className="text-sm font-semibold text-fg">{f.title}</div>
        <p className="mt-1 flex-1 text-xs leading-5 text-muted">{f.description}</p>
        <div className="mt-3 flex items-center gap-2">
          <Button size="sm" variant={f.kind === "board" ? "primary" : "secondary"} icon={Download} onClick={() => void downloadFile(f.url, f.name)}>
            Download
          </Button>
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-subtle" title={f.name}>
            {f.name} · {fmtBytes(f.bytes)}
          </span>
        </div>
      </div>
    </li>
  );
}

function Rules() {
  const rules = [
    {
      icon: Printer,
      title: "Print at 100 %, “actual size”",
      text: "Never “fit to page” or “shrink to fit”: a scaled print makes every measurement wrong by the same amount.",
    },
    {
      icon: Sparkles,
      title: "Matte paper",
      text: "Glossy paper throws the lamp's glare into the camera. Use plain matte paper, and matte label paper for the tags.",
    },
    {
      icon: Layers,
      title: "Glue the board flat",
      text: "Spray-glue it to glass (a picture-frame glass) or a flat tile, with no bubbles. A wavy sheet gives a bad calibration.",
    },
  ];
  return (
    <ul className="grid gap-3 md:grid-cols-3">
      {rules.map((r) => (
        <li key={r.title} className="flex gap-3 rounded-xl border border-line bg-surface p-4 shadow-sm">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-warn/30 bg-warn-soft text-warn">
            <r.icon size={17} aria-hidden />
          </span>
          <div>
            <div className="text-sm font-semibold text-fg">{r.title}</div>
            <p className="mt-0.5 text-xs leading-5 text-muted">{r.text}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

/** The 100 mm bar with calipers on it (illustration). */
function CalipersArt() {
  return (
    <svg viewBox="0 0 260 96" className="h-auto w-full max-w-[260px]" aria-hidden>
      <rect x="2" y="2" width="256" height="92" rx="10" fill="var(--c-surface-2)" stroke="var(--c-line)" />
      <rect x="30" y="58" width="200" height="4" fill="var(--c-fg)" />
      {Array.from({ length: 11 }, (_, i) => (
        <rect key={i} x={30 + i * 20 - 0.6} y={i % 5 === 0 ? 50 : 54} width="1.2" height={i % 5 === 0 ? 12 : 8} fill="var(--c-fg)" />
      ))}
      <text x="130" y="80" textAnchor="middle" fontSize="10" fill="var(--c-fg-muted)">
        100 mm
      </text>
      <g fill="var(--c-accent)" opacity="0.9">
        <rect x="18" y="14" width="224" height="10" rx="2" opacity="0.35" />
        <path d="M30 24 L30 50 L26 50 L22 24 Z" />
        <path d="M230 24 L230 50 L234 50 L238 24 Z" />
        <rect x="200" y="10" width="44" height="18" rx="3" />
      </g>
      <text x="222" y="23" textAnchor="middle" fontSize="9" fontWeight="600" fill="#fff" fontFamily="monospace">
        99.8
      </text>
    </svg>
  );
}

function ScaleCheck({ onReprint }: { onReprint: () => void }) {
  const setup = useSetup((s) => s.setup)!;
  const pc = useSetup((s) => s.printCheck);
  const pending = useSetup((s) => s.printCheckPending);
  const check = useSetup((s) => s.checkPrint);
  const [value, setValue] = useState(() => (pc ? pc.measured_mm.toFixed(1) : ""));
  const [touched, setTouched] = useState(false);
  const id = useId();
  const num = Number(value.trim().replace(",", "."));
  const invalid = !value.trim() || !Number.isFinite(num) || num < 50 || num > 150;
  const sim = setup.target === "sim";
  const scale = setup.board.scale;
  const submit = () => {
    setTouched(true);
    if (!invalid) void check(num);
  };
  return (
    <Section title="Check the print with calipers" icon={Ruler} description="The 100 mm scale bar under the board">
      <div className="grid items-start gap-6 md:grid-cols-[280px_1fr]">
        <CalipersArt />
        <div>
          <p className="text-sm text-muted">
            Measure the bar between its end marks, as exactly as you can. Within ±0.3 % (99.7–100.3 mm) the print is fine.
            {sim ? " It's optional here: the simulator's board is virtual and exact." : ""}
          </p>
          <form
            className="mt-3 flex flex-wrap items-start gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div>
              <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
                Your calipers read
              </label>
              <div className="relative w-36">
                <TextInput
                  id={id}
                  inputMode="decimal"
                  placeholder="100.0"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  invalid={touched && invalid}
                  aria-invalid={touched && invalid}
                  aria-describedby={`${id}-err`}
                  className="pr-10 font-mono tnum"
                />
                <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-subtle">mm</span>
              </div>
              <div id={`${id}-err`} className={cn("mt-1 h-4 text-xs", touched && invalid ? "text-danger" : "text-transparent")}>
                {touched && invalid ? "Type the length in mm, for example 99.6" : "."}
              </div>
            </div>
            <Button type="submit" variant="primary" className="mt-5" loading={pending === "check"}>
              Check
            </Button>
          </form>
          {pc ? (
            <div className="mt-1 animate-fade-in">
              <Callout
                tone={pc.ok ? "ok" : pc.can_apply ? "warn" : "danger"}
                icon={pc.ok ? CircleCheck : TriangleExclamationPoint}
                title={
                  pc.ok
                    ? scale !== 1
                      ? "Using your measurement"
                      : "The print is the right size"
                    : `${pc.error_pct > 0 ? "+" : "−"}${Math.abs(pc.error_pct).toFixed(1)} % off`
                }
                actions={
                  !pc.ok ? (
                    <>
                      {pc.can_apply ? (
                        <Button size="sm" variant="primary" icon={Scaling} loading={pending === "apply"} onClick={() => void check(pc.measured_mm, true)}>
                          Use my measurement
                        </Button>
                      ) : null}
                      <Button size="sm" icon={Printer} onClick={onReprint}>
                        Reprint at 100 %
                      </Button>
                    </>
                  ) : undefined
                }
              >
                <span className="text-sm text-fg">{sentence(pc.verdict)}</span>
                {pc.can_apply ? (
                  <span className="mt-1 block text-xs text-muted">
                    “Use my measurement” scales the board for calibration only; printing stays as it is. Reprinting at 100 % is the cleaner fix.
                  </span>
                ) : null}
              </Callout>
            </div>
          ) : scale !== 1 ? (
            <p className="mt-1 text-xs text-muted">Calibration uses your measured scale: {(scale * 100).toFixed(2)} % of the nominal board.</p>
          ) : null}
        </div>
      </div>
    </Section>
  );
}

export function PrintStep() {
  const setup = useSetup((s) => s.setup)!;
  const printables = useSetup((s) => s.printables);
  const printablesError = useSetup((s) => s.printablesError);
  const jobId = useSetup((s) => s.printJobId);
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const starting = useSetup((s) => s.printStarting);
  const generate = useSetup((s) => s.generatePrintables);
  const openFolder = useSetup((s) => s.openFolder);
  const [paper, setPaper] = useState<Paper>(setup.board.paper);
  const [folder, setFolder] = useState<{ dir: string } | null>(null);
  const filesRef = useRef<HTMLDivElement>(null);
  const running = isActive(job);
  const files = printables?.files ?? [];
  const sim = setup.target === "sim";

  useEffect(() => {
    void useSetup.getState().fetchPrintables();
  }, []);
  useEffect(() => {
    if (printables?.paper) setPaper(printables.paper);
  }, [printables?.paper]);

  const open = async () => {
    const r = await openFolder();
    if (!r) return;
    if (r.opened) toast.success("Opened the printables folder", r.dir);
    else setFolder({ dir: r.dir });
  };

  return (
    <StepPage wide>
      <StepHeader page="print" />
      {sim ? (
        <Callout tone="accent" icon={SquareDashed} title="Optional with the simulator" className="mb-5">
          The simulator's board is virtual and exactly the right size. Make the files now anyway, to have them ready for the real scanner.
        </Callout>
      ) : null}

      <div className="mb-5 flex flex-wrap items-center gap-4 rounded-xl border border-line bg-surface p-5 shadow-sm">
        <div className="min-w-[240px] flex-1">
          <div className="text-sm font-semibold text-fg">
            {printables?.generated ? `Made ${fmtRelative(printables.generated)} for ${printables.paper === "a4" ? "A4" : "Letter"}` : "One click makes everything to print"}
          </div>
          <p className="mt-0.5 text-sm text-muted">The calibration board, the AprilTag stickers and the LED projector mask, as PDFs at their true size.</p>
        </div>
        <Segmented<Paper>
          label="Paper size"
          value={paper}
          onChange={setPaper}
          disabled={running}
          options={[
            { value: "letter", label: "Letter", title: "8.5 × 11 in" },
            { value: "a4", label: "A4", title: "210 × 297 mm" },
          ]}
        />
        <Button variant="primary" size="lg" icon={Printer} loading={starting} disabled={running} onClick={() => void generate(paper)}>
          {files.length ? (printables?.paper !== paper ? `Make for ${paper === "a4" ? "A4" : "Letter"}` : "Make again") : "Make the printables"}
        </Button>
        {files.length ? (
          <Button size="lg" icon={FolderOpen} onClick={() => void open()}>
            Open folder
          </Button>
        ) : null}
        {running && job ? (
          <div className="w-full animate-fade-in" role="status">
            <div className="mb-1.5 flex items-center gap-2 text-xs">
              <Spinner size={13} className="text-accent" />
              <span className="flex-1 text-muted">{job.message}</span>
              <span className="font-mono text-fg tnum">{Math.round(job.progress * 100)}%</span>
            </div>
            <ProgressBar value={job.progress} label="Making the printables" />
          </div>
        ) : null}
        {folder ? (
          <div className="flex w-full flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-xs text-muted">
            <FolderOpen size={14} aria-hidden />
            Studio couldn't open a file manager from here. The files are in
            <Code>{folder.dir}</Code>
            <Button
              size="xs"
              variant="ghost"
              onClick={async () => {
                if (await copyText(folder.dir)) toast.success("Copied the folder path");
              }}
            >
              Copy path
            </Button>
          </div>
        ) : null}
        {setup.board.paper !== paper && !running ? (
          <p className="w-full text-xs text-subtle">Making them for {paper === "a4" ? "A4" : "Letter"} also sets the board's paper size for the laser calibration.</p>
        ) : null}
      </div>

      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-subtle">Before you print</h2>
      <Rules />

      <div ref={filesRef} className="mt-6">
        {files.length ? (
          <>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-subtle">The files</h2>
            <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {files.map((f) => (
                <FileCard key={f.name} f={f} />
              ))}
            </ul>
            <p className="mt-3 text-xs text-subtle">
              The mask is only for option B, the LED + printed-mask dot projector: another option to the laser dot projector, and the software treats
              both the same.
            </p>
          </>
        ) : printablesError ? (
          <Callout tone="danger" icon={TriangleExclamationPoint} title="Couldn't read the printables">
            {printablesError.detail}
          </Callout>
        ) : null}
      </div>

      <div className="mt-6">
        <ScaleCheck
          onReprint={() => {
            filesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
            toast.info("Print the board again at 100 %", "In the print dialog choose “Actual size” and turn off “Fit to page”, then measure the bar again.");
          }}
        />
      </div>
      <StepFooter page="print" />
    </StepPage>
  );
}
