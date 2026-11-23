/**
 * Checks for the Settings page (UX_PLAN U12, `npm test`): the settings a person changes on top, the rest under a
 * closed "Advanced: what the settings file says", in words a person reads ("Camera format", not "FourCC"); the
 * Scans folder explained; no milestone names. Plain TypeScript run by Node, no framework.
 */
import { ADVANCED_TITLE, advancedSections, settingLabel, sectionTitle } from "./settingsWords.ts";

const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`settingsWords.test: ${what}`);
}

// the page's order: the settings a person changes, About, then the closed Advanced (read from the page's source)
const page = fs.readFileSync(new URL("../pages/SettingsPage.tsx", import.meta.url), "utf8");
const body = page.slice(page.indexOf("export function SettingsPage"));
const order = ['<Section title="Appearance"', '<Section title="After a scan"', '<Section title="While scanning"', "<MeshingSection", "<LasersSection", '<Section title="About"', "<AdvancedSettings"];
const at = order.map((o) => body.indexOf(o));
check(at.every((x, i) => x > 0 && (i === 0 || x > at[i - 1])), `the page's order: Theme, After a scan, While scanning, Thin walls, Line lasers, About, Advanced (${at.join(" ")})`);
check(page.slice(page.indexOf("function AdvancedSettings"), page.indexOf("function AdvancedSettings") + 400).includes("const [open, setOpen] = useState(false);"), "Advanced starts closed");
check(!page.includes("ohs config --init") && !page.includes("(M3)"), "no command line and no milestone on the page");
check(ADVANCED_TITLE === "Advanced: what the settings file says", "the closed section's title");

// labels a person reads
check(settingLabel("fourcc") === "Camera format", `fourcc: ${settingLabel("fourcc")}`);
check(settingLabel("baud") === "Serial speed", `baud: ${settingLabel("baud")}`);
check(settingLabel("poisson") === "Watertight surface (Poisson)", `poisson: ${settingLabel("poisson")}`);
check(settingLabel("dictionary") === "Marker pattern", `dictionary: ${settingLabel("dictionary")}`);
check(settingLabel("scans_dir") === "Recordings folder", `scans_dir: ${settingLabel("scans_dir")}`);
check(settingLabel("imu_rate_hz") === "Motion sensor (IMU) rate", `imu_rate_hz: ${settingLabel("imu_rate_hz")}`);
check(settingLabel("swap_lr") === "Swap left and right", `swap_lr: ${settingLabel("swap_lr")}`);
check(settingLabel("squares_x") === "Squares across", "squares_x as before");
check(settingLabel("depth_min") === "Depth min", "an ordinary key: its words, a capital first");
// the lasers and dots' uses, by the names of the calibration steps (UX_PLAN, One name for each thing)
check(settingLabel("stereo") === "Cameras" && settingLabel("imu") === "Motion sensor (IMU)" && settingLabel("check") === "Calibration check", `profiles: ${settingLabel("stereo")}, ${settingLabel("imu")}, ${settingLabel("check")}`);
check(sectionTitle("pico") === "Control board (Pico or ESP32)" && sectionTitle("emitters") === "Lasers and dots", "the sections by their names");
check(sectionTitle("live") === "Live preview" && sectionTitle("process") === "Making the mesh", "live and process");

// the advanced sections: every section of the file, the scans folder with what it is for
const settings = {
  projects_dir: "~/OpenHandScan/projects",
  config_file: "~/.ohs/config.toml",
  paths: { calibration: "~/.ohs/calibration.json", scans_dir: "~/OpenHandScan/scans" },
  camera: { fourcc: "MJPG", index: 1 },
  pico: { baud: 115200 },
  board: { dictionary: "DICT_5X5_100", squares_x: 11 },
};
const adv = advancedSections(settings);
check(adv.map((s) => s.key).join(",") === "general,paths,camera,pico,board", `the sections: ${adv.map((s) => s.key).join(",")}`);
// UX_PLAN R12: the markers' dictionary is the program's name for them: not shown, also not here
const board = adv.find((s) => s.key === "board")!;
check(!board.rows.some((r) => r.key === "dictionary") && board.rows.some((r) => r.key === "squares_x"), `the board without its dictionary: ${board.rows.map((r) => r.key).join(",")}`);
check(!JSON.stringify(adv).includes("DICT_"), "no dictionary name anywhere in Advanced");
const paths = adv.find((s) => s.key === "paths")!;
const scans = paths.rows.find((r) => r.key === "scans_dir")!;
check(scans.label === "Recordings folder" && /calibration/i.test(scans.help ?? "") && /project/i.test(scans.help ?? ""), `the scans folder is explained: ${scans.help}`);
const general = adv.find((s) => s.key === "general")!;
check(general.title === "Folders and files" && general.rows.some((r) => r.key === "config_file" && r.label === "Settings file"), "the loose values: the folders and the settings file");
const all = adv.flatMap((s) => [s.title, s.help ?? "", ...s.rows.flatMap((r) => [r.label, r.help ?? ""])]);
check(all.every((t) => !/\(M\d\)|\bM\d\b/.test(t)), "no milestone names");
check(all.every((t) => !/\s[-–—]\s/.test(t)), "no dash as punctuation");

// the live preview's mesh detail and the mesh's are told apart (both are `voxel_mm`)
const two = advancedSections({ live: { voxel_mm: 2 }, process: { voxel_mm: 1 } });
const liveVoxel = two.find((s) => s.key === "live")!.rows[0];
const meshVoxel = two.find((s) => s.key === "process")!.rows[0];
check(meshVoxel.label === "Mesh detail" && liveVoxel.label === "Preview mesh detail", `${liveVoxel.label} / ${meshVoxel.label}`);
check(/while scanning/.test(liveVoxel.help ?? ""), `the preview's detail says what it is for: ${liveVoxel.help}`);

console.log(`settingsWords.test: ${checks} checks passed`);
