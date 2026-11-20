/**
 * Checks for the report's request as the viewer builds it (`report.ts`, `npm test`): the views' sizes and bytes, the
 * limits of the list (how many, how large each, how large in all) refused in words, the default titles, removing, and
 * the request's body (only what is set; the part's frame only with a frame; the pictures in order with their captions).
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import {
  addView,
  base64Bytes,
  cannotAdd,
  dataUrlBase64,
  LIMITS_TEXT,
  MAX_SIDE_PX,
  MAX_TOTAL_BYTES,
  MAX_VIEW_BYTES,
  MAX_VIEWS,
  nextTitle,
  removeView,
  reportRequest,
  viewSize,
  type ReportView,
} from "./report.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`report.test: ${what}`);
}

const b64 = (bytes: number) => btoa(String.fromCharCode(...new Uint8Array(bytes).fill(7)));
const view = (id: string, bytes = 1000, title?: string): Omit<ReportView, "bytes" | "title"> & { title?: string } => ({
  id,
  title,
  caption: "",
  data: b64(bytes),
  width: 1600,
  height: 900,
});

// ---------------------------------------------------------------- bytes and sizes
{
  for (const n of [0, 1, 2, 3, 4, 1000, 1001, 1002]) check(base64Bytes(b64(n)) === n, `the bytes of ${n} in base64`);
  check(dataUrlBase64("data:image/png;base64,QUJD") === "QUJD", "a PNG data URL's base64");
  check(dataUrlBase64("data:image/jpeg;base64,QUJD") === null && dataUrlBase64("http://x/y.png") === null, "nothing else");
  const s = viewSize(3200, 1800);
  check(s.width === MAX_SIDE_PX && s.height === 900, `scaled down to the longer side: ${JSON.stringify(s)}`);
  const t = viewSize(900, 2000);
  check(t.height === MAX_SIDE_PX && t.width === 720, `a tall view: ${JSON.stringify(t)}`);
  const u = viewSize(800, 600);
  check(u.width === 800 && u.height === 600, "a small view stays as it is");
  check(LIMITS_TEXT.includes(String(MAX_VIEWS)) && LIMITS_TEXT.includes("3\u202fMB"), LIMITS_TEXT);
}

// ---------------------------------------------------------------- the list and its limits
{
  let views: ReportView[] = [];
  for (let i = 0; i < MAX_VIEWS; i++) {
    const r = addView(views, view(`v${i}`));
    check(r.error === null && r.views.length === i + 1, `view ${i + 1} added`);
    check(r.views !== views, "a new list");
    views = r.views;
  }
  check(views.map((v) => v.title).join() === "View 1,View 2,View 3,View 4,View 5,View 6", views.map((v) => v.title).join());
  check(views[0].bytes === 1000, "its bytes counted");
  const full = addView(views, view("v7"));
  check(full.views === views && !!full.error && full.error.includes(String(MAX_VIEWS)), `the seventh refused: ${full.error}`);
  // removing one, then a default title that does not repeat the highest
  views = removeView(views, "v2");
  check(views.length === MAX_VIEWS - 1 && !views.some((v) => v.id === "v2"), "removed");
  check(nextTitle(views) === "View 7", `the next title after View 6: ${nextTitle(views)}`);
  const named = addView(views, view("vx", 10, "  Front, from the left "));
  check(named.views[named.views.length - 1].title === "Front, from the left", "a title given is kept, trimmed");
  // one too large, and too much in all
  const big = cannotAdd([], MAX_VIEW_BYTES + 1);
  check(!!big && big.includes("3\u202fMB"), `a view over 3\u202fMB: ${big}`);
  check(cannotAdd([], MAX_VIEW_BYTES) === null, "a view of exactly 3\u202fMB");
  const heavy: ReportView[] = [0, 1, 2, 3].map((i) => ({ ...(view(`h${i}`) as ReportView), title: `View ${i + 1}`, bytes: MAX_VIEW_BYTES }));
  const all = cannotAdd(heavy, 1);
  check(!!all && all.includes(`${MAX_TOTAL_BYTES / 2 ** 20}\u202fMB`), `more than 12\u202fMB in all: ${all}`);
}

// ---------------------------------------------------------------- the request
{
  const v1 = { ...(view("a") as ReportView), title: "Front", caption: "  The bore on the left ", bytes: 3 };
  const v2 = { ...(view("b") as ReportView), title: "Back", caption: "", bytes: 3 };
  check(JSON.stringify(reportRequest({ tolerance: null, inPartFrame: false, hasFrame: false, seam: null, views: [] })) === "{}", "nothing set: an empty body");
  const r = reportRequest({ tolerance: 0.3, inPartFrame: true, hasFrame: true, seam: "sm-1", views: [v1, v2] });
  check(r.tolerance_mm === 0.3 && r.numbers_in_part_frame === true && r.seam === "sm-1", JSON.stringify({ ...r, pictures: undefined }));
  check(r.pictures?.length === 2 && r.pictures[0].title === "Front" && r.pictures[0].caption === "The bore on the left" && r.pictures[0].data === v1.data, "the pictures in order, captions trimmed");
  check(!("caption" in r.pictures![1]), "no caption: none sent");
  check(!("numbers_in_part_frame" in reportRequest({ tolerance: null, inPartFrame: true, hasFrame: false, seam: null, views: [] })), "the part's frame only with a frame");
  check(!("tolerance_mm" in reportRequest({ tolerance: 0, inPartFrame: false, hasFrame: false, seam: null, views: [] })), "a tolerance of 0 is none");
  check(!("tolerance_mm" in reportRequest({ tolerance: Number.NaN, inPartFrame: false, hasFrame: false, seam: null, views: [] })), "nor one that is not a number");
}

console.log(`report.test: ${checks} checks passed`);
