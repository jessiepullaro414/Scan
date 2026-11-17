/**
 * Checks for the demo engine's report (`npm test`; `report.ts`): its small HTML builder holds the scan's names, the
 * kept fits, sections and seam, the pictures sent (embedded, nothing fetched), the line on what a ± is and the limits'
 * refusals; the features as CSV start with a byte-order mark and read back; the JSON parses. Plain TypeScript that
 * Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import { HttpError } from "./host.ts";
import { MockReport, type ReportSource } from "./report.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`report.test (mock): ${what}`);
}

const PNG = btoa(String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3));

const source: ReportSource = {
  names: () => ({ part: "Door <bracket>", scan: "Scan 1" }),
  hasMesh: () => true,
  features: () => ({
    format_version: 1,
    frame: null,
    mesh_version: 2,
    features: [
      {
        id: "f-1",
        name: "Top & bottom",
        kind: "plane",
        shape: { kind: "plane", point: [1, 2, 3], normal: [0, 0, 1], rms_mm: 0.02, flatness_mm: 0.1, n: 100, normal_sigma_deg: 0.05, offset_sigma_mm: 0.004 },
        seed: { kind: "plane", face: 0, point: [1, 2, 3], tolerance_mm: null },
        n_faces: 100,
        tolerance_mm: null,
        mean_sigma_mm: null,
        correlated_points: null,
        warnings: [],
        mesh_version: 1,
        created: "2026-11-17T12:00:00",
        stale: true,
        extent: null,
        nominals: [],
      },
    ],
    measurements: [],
    relations: [],
  }),
  sections: () => [{ name: "Section A-A", description: "Through the middle" }],
  seam: () => ({ name: "Door seam", mean: 0.84, n_trusted: 30, n_stations: 32 }),
};

const r = new MockReport();
const call = async (name: string, body: Record<string, unknown>) => {
  try {
    return await r.route("POST", [name], body, source);
  } catch (e) {
    if (e instanceof HttpError) return new Response(JSON.stringify({ detail: e.detail, hint: e.hint }), { status: e.status });
    throw e;
  }
};

{
  const res = await call("report.html", { tolerance_mm: 0.3, pictures: [{ title: "Front <view>", caption: "the left side", data: PNG }] });
  const html = await res!.text();
  check(res!.status === 200 && (res!.headers.get("content-type") ?? "").startsWith("text/html"), "an HTML page");
  check((res!.headers.get("content-disposition") ?? "").includes("Scan_1-report.html"), "named after the scan");
  check(html.includes("Door &lt;bracket&gt;") && !html.includes("Door <bracket>"), "names are escaped");
  check(html.includes("Top &amp; bottom") && html.includes("Section A-A") && html.includes("Door seam"), "the kept things by name");
  check(html.includes(`data:image/png;base64,${PNG}`) && html.includes("Front &lt;view&gt;"), "the picture inside the page");
  check(html.includes("not the accuracy"), "what a ± is");
  check(html.includes("earlier mesh"), "a fit made on an earlier mesh is a warning");
  check(!/(src|href)="(https?:)?\/\//.test(html) && !html.toLowerCase().includes("<script"), "nothing fetched, no script");
  check(html.includes("demo"), "says it is the demo's");
  const csv = await call("features.csv", {});
  const text = new TextDecoder("utf-8", { ignoreBOM: true }).decode(await csv!.arrayBuffer()); // text() would drop the mark
  check(text.charCodeAt(0) === 0xfeff, "the CSV starts with a byte-order mark");
  const rows = text.slice(1).trim().split("\r\n");
  check(rows.length === 2 && rows[0].startsWith("record,id,name,kind") && rows[1].includes('"Top & bottom"') === false && rows[1].includes("Top & bottom"), rows.join(" / "));
  const json = JSON.parse(await (await call("features.json", {}))!.text()) as { format: string; features: unknown[]; scan: string };
  check(json.format === "OpenHandScan kept features" && json.features.length === 1 && json.scan === "Scan 1", "the JSON");
  check((await call("report.html", { pictures: Array.from({ length: 7 }, (_, i) => ({ title: `V${i}`, data: PNG })) }))!.status === 422, "seven pictures: 422");
  check((await call("report.html", { pictures: [{ title: "", data: PNG }] }))!.status === 422, "a picture without a title: 422");
  check((await call("report.pdf", {}))!.status === 404, "another file: 404");
  const odd = await (await call("report.html", { pictures: [{ title: "Not a picture", data: btoa("hello") }] }))!.text();
  check(odd.includes("Not shown") && odd.includes("Not a picture"), "a picture that is not a PNG is left out with a note");
}

console.log(`report.test (mock): ${checks} checks passed`);
