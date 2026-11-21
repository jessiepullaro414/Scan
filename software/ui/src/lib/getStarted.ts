/**
 * Home's card for a computer without projects, "From part to mesh in three steps" (UX_PLAN U26). While it is the
 * first run the setup card above it has the buttons, so this card has none, and its step 1 is the setup itself:
 * "Set up the scanner (about N minutes, once)". After the first run step 1 says connect and calibrate, and the
 * card has its own buttons. Pure; `getStarted.test.ts` checks it.
 */

export type GetStartedStep = { id: "setup" | "scan" | "mesh"; title: string; text: string };

export function getStarted(a: { firstRun: boolean; minutes: number }): { steps: GetStartedStep[]; buttons: boolean } {
  const mins = a.minutes > 0 ? `about ${a.minutes} minute${a.minutes === 1 ? "" : "s"}, once` : "once";
  const first: GetStartedStep = a.firstRun
    ? {
        id: "setup",
        title: `Set up the scanner (${mins})`,
        text: "Connect the cameras and the control board (Pico or ESP32), test them and calibrate: the setup card above leads the way. Or try the simulator: no hardware needed.",
      }
    : {
        id: "setup",
        title: "Connect and calibrate the scanner",
        text: "Plug in the scanner's cameras and its control board (Pico or ESP32); Setup calibrates it once. Or try the simulator: no hardware needed.",
      };
  return {
    buttons: !a.firstRun,
    steps: [
      first,
      { id: "scan", title: "Scan the part", text: "Sweep slowly 200–350 mm from the surface. The model grows live; the raw stream is always recorded." },
      { id: "mesh", title: "Make the mesh and measure", text: "One click builds the mesh at full resolution. Measure in mm, then export STL or PLY for CAD." },
    ],
  };
}
