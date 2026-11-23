/**
 * Checks for the brightness sliders of the Settings page (`npm test`): which lasers get one, their
 * labels and values, and reading the `lasers` settings of new and older engines. Plain TypeScript
 * that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import { brightnessRows, laserSettingsOf } from "./laserBrightness.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`laserBrightness.test: ${what}`);
}

const FOUR = ["vertical", "horizontal", "vertical_2", "horizontal_2"];

// reading the settings
check(laserSettingsOf(null) === null && laserSettingsOf({}) === null, "no lasers section");
check(laserSettingsOf({ lasers: { lines: "vertical" } }) === null, "lines not a list");
const old = laserSettingsOf({ lasers: { lines: FOUR } });
check(!!old && old.own_channels === undefined && old.brightness === undefined, "an older engine: lines only");
const cur = laserSettingsOf({ lasers: { lines: FOUR, own_channels: true, brightness: { vertical_2: 85, horizontal_2: "x" } } });
check(!!cur && cur.own_channels === true && JSON.stringify(cur.brightness) === '{"vertical_2":85}', "own_channels and numeric brightness");

// nothing to set
check(brightnessRows(null) === null, "no settings");
check(brightnessRows({ lines: ["vertical", "horizontal"], own_channels: true, brightness: {} }) === null, "two lasers: nothing new");
check(brightnessRows({ lines: FOUR }) === null, "an older engine: no sliders");

// four lasers, each on its own wire
const rows = brightnessRows({ lines: FOUR, own_channels: true, brightness: { vertical_2: 85, horizontal: 120 } })!;
check(rows.map((r) => r.label).join("|") === "Vertical|Vertical 2|Horizontal|Horizontal 2", "labels in config order, by the names the chips use");
check(rows.map((r) => r.name).join("|") === "vertical|vertical_2|horizontal|horizontal_2", "names");
check(rows.map((r) => r.value).join("|") === "100|85|100|100", "values: missing is 100, clamped to 100");
check(rows.every((r) => !r.pair), "one slider per laser");

// each pair on one wire: one slider per pair, the first laser's brightness
const pairs = brightnessRows({ lines: FOUR, own_channels: false, brightness: { vertical: 70, vertical_2: 40 } })!;
check(pairs.length === 2 && pairs.every((r) => r.pair), "one slider per pair");
check(pairs[0].name === "vertical" && pairs[0].value === 70 && pairs[0].label === "Vertical lasers", "the first laser counts");
check(pairs[1].name === "horizontal" && pairs[1].value === 100, "horizontal pair");

// three lasers: the single one keeps its plain label
const three = brightnessRows({ lines: ["vertical", "horizontal", "vertical_2"], own_channels: true })!;
check(three.map((r) => r.label).join("|") === "Vertical|Vertical 2|Horizontal", "three lasers");
const threeShared = brightnessRows({ lines: ["vertical", "horizontal", "vertical_2"], own_channels: false })!;
check(threeShared.map((r) => `${r.label}:${r.pair}`).join("|") === "Vertical lasers:true|Horizontal:false", "three lasers, one wire per pair");

console.log(`laserBrightness.test: ${checks} checks passed`);
