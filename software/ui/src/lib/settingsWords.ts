/**
 * The Settings page's words (UX_PLAN U12): the settings a person changes come first (Theme, After a scan, While
 * scanning, Thin walls and edges, Line lasers, then About); everything
 * else the settings file says is shown, read only, under a closed "Advanced: what the settings file says", with
 * labels a person reads ("Camera format", not "FourCC") and a line on what each folder is for. Pure: the page
 * (SettingsPage.tsx) renders it; `settingsWords.test.ts` checks it.
 */

export const ADVANCED_TITLE = "Advanced: what the settings file says";

const ACRONYMS = new Set(["fps", "id", "dpi", "ply", "stl", "obj", "ui", "api", "url", "gpu", "cpu"]);

/** Keys with their own words. */
const SPECIAL: Record<string, string> = {
  fourcc: "Camera format",
  baud: "Serial speed",
  poisson: "Watertight surface (Poisson)",
  dictionary: "Marker pattern",
  swap_lr: "Swap left and right",
  second_index: "Second camera",
  config_file: "Settings file",
  projects_dir: "Projects folder",
  scans_dir: "Recordings folder",
  calibration: "Calibration file",
  square_mm: "Square size",
  marker_mm: "Marker size",
  tag_mm: "Tag size",
  voxel_mm: "Mesh detail",
  thin_walls: "Thin walls and edges",
  markers_dot_level: "Dots in markers mode",
  squares_x: "Squares across",
  squares_y: "Squares down",
  imu_rate_hz: "Motion sensor (IMU) rate",
  heartbeat_s: "Heartbeat",
  frame_step: "Every nth frame",
  frame_motion_deg: "Least turn between frames",
  frame_max_gap: "Largest gap in frames",
  depth_min_mm: "Nearest depth",
  depth_max_mm: "Farthest depth",
  own_channels: "Each laser has its own wire",
  lines: "Line lasers",
  export: "Files written",
  // the lasers and dots' uses (emitters.profiles), by the names of what they light
  stereo: "Cameras",
  imu: "Motion sensor (IMU)",
  check: "Calibration check",
};

/** What a value is for, where the label alone does not say. */
const HELP: Record<string, string> = {
  scans_dir: "Where calibration recordings go, and scans recorded from the command line. The app keeps each scan in its project's folder.",
  projects_dir: "Each project is a folder in here, with its scans.",
  config_file: "The file these values are read from.",
  calibration: "The calibration in use.",
  index: "Which camera of this computer, counted from 0.",
};

/** The sections of the file, by their names in the app. */
const SECTIONS: Record<string, { title: string; help?: string }> = {
  general: { title: "Folders and files" },
  paths: { title: "Folders", help: "Where the calibration and the recordings are stored" },
  camera: { title: "Cameras", help: "Camera mode and exposure" },
  pico: { title: "Control board (Pico or ESP32)", help: "On USB: lasers, dots, motion sensor" },
  emitters: { title: "Lasers and dots", help: "Laser and dot levels for each use: vertical, horizontal, dots (%)" },
  live: { title: "Live preview", help: "The rough mesh shown while scanning" },
  process: { title: "Making the mesh", help: "Defaults for Standard and Best quality" },
  board: { title: "Calibration board", help: "The printed ChArUco board" },
  markers: { title: "Markers", help: "AprilTag stickers" },
  lasers: { title: "Line lasers", help: "The scanner's line lasers, for the laser calibration" },
  server: { title: "The app's engine" },
};

/** "imu_rate_hz" → its own words; another key → its words, a capital first ("depth_min" → "Depth min"). */
export function settingLabel(key: string): string {
  if (SPECIAL[key]) return SPECIAL[key];
  const base = key.replace(/_(mm|s|seconds|hz|px|deg|level)$/i, "");
  if (SPECIAL[base]) return SPECIAL[base];
  const words = base.split(/[_-]+/).filter(Boolean);
  return words.map((w, i) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w)).join(" ");
}

export function sectionTitle(key: string): string {
  return SECTIONS[key]?.title ?? settingLabel(key);
}

export type SettingRow = { key: string; label: string; help?: string; value: unknown };
export type SettingSection = { key: string; title: string; help?: string; rows: SettingRow[] };

const isGroup = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
/** What the engine adds for the pages (the file's path as read), not a section of the file. */
const NOT_SHOWN = new Set(["effective"]);
/** Values of a section not shown (UX_PLAN R12): the markers' dictionary is the program's name for them. */
const NOT_SHOWN_IN: Record<string, Set<string>> = { board: new Set(["dictionary"]) };

/** Every section of the settings file, the loose values first ("Folders and files"). */
export function advancedSections(settings: Record<string, unknown>): SettingSection[] {
  const entries = Object.entries(settings).filter(([k]) => !NOT_SHOWN.has(k));
  const row = ([k, v]: [string, unknown]): SettingRow => ({ key: k, label: settingLabel(k), ...(HELP[k] ? { help: HELP[k] } : {}), value: v });
  const loose = entries.filter(([, v]) => !isGroup(v));
  const out: SettingSection[] = [];
  if (loose.length) out.push({ key: "general", title: SECTIONS.general.title, rows: loose.map(row) });
  for (const [k, v] of entries) if (isGroup(v)) out.push({ key: k, title: sectionTitle(k), ...(SECTIONS[k]?.help ? { help: SECTIONS[k].help } : {}), rows: Object.entries(v).filter(([r]) => !NOT_SHOWN_IN[k]?.has(r)).map(row) });
  return out;
}
