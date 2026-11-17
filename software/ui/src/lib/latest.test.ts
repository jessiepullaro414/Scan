/**
 * Checks for the requests a dragged slider sends (`latest.ts`, `npm test`): one at a time, the newest waiting one sent
 * next (the ones in between dropped), an answer never shown after a newer one, an error shown only for the newest
 * request, and nothing shown after `cancel`. Plain TypeScript that Node runs as it is, no test framework: a failed
 * check throws.
 */
import { latestOnly } from "./latest.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`latest.test: ${what}`);
}

/** A request the test answers by hand. */
type Pending = { q: number; resolve: (a: string) => void; reject: (e: unknown) => void };
const tick = () => new Promise((r) => setTimeout(r, 0));

async function main(): Promise<void> {
  const sent: Pending[] = [];
  const shown: string[] = [];
  const errors: string[] = [];
  const run = (q: number) => new Promise<string>((resolve, reject) => sent.push({ q, resolve, reject }));
  let idles = 0;
  const l = latestOnly<number, string>(run, (a) => shown.push(a), (e) => errors.push(String(e)), () => idles++);

  // a drag: 1 is sent; 2, 3, 4 come while it runs: only 4 waits
  l.ask(1);
  l.ask(2);
  l.ask(3);
  l.ask(4);
  check(sent.length === 1 && sent[0].q === 1, "one request at a time");
  check(l.busy(), "busy while one runs");
  sent[0].resolve("cut 1");
  await tick();
  check(shown.join() === "cut 1", "the answer to 1 is shown while the drag goes on (the cut follows)");
  check(sent.length === 2 && sent[1].q === 4, "then the newest waiting request, the ones in between dropped");
  sent[1].resolve("cut 4");
  await tick();
  check(shown.join() === "cut 1,cut 4" && !l.busy(), "and its answer; nothing left to send");
  check(idles === 1, "idle once, when nothing more waits (not between 1 and 4)");
  check(sent.length === 2, "2 and 3 were never sent");

  // an error of a request that is no longer the newest is not shown; one of the newest is
  l.ask(5);
  l.ask(6);
  sent[2].reject(new Error("bad 5"));
  await tick();
  check(errors.length === 0 && sent[3].q === 6, "an older request's error is not shown; the newest goes out");
  sent[3].reject(new Error("bad 6"));
  await tick();
  check(errors.length === 1 && errors[0].includes("bad 6"), "the newest request's error is shown");

  // cancel (leaving the tool): what is running is not shown, what waits is not sent
  l.ask(7);
  l.ask(8);
  l.cancel();
  sent[4].resolve("cut 7");
  await tick();
  check(shown.length === 2 && sent.length === 5, "after cancel nothing is shown and nothing more is sent");
  l.ask(9);
  check(sent.length === 6 && sent[5].q === 9, "asking again after cancel works");
  sent[5].resolve("cut 9");
  await tick();
  check(shown[shown.length - 1] === "cut 9", "and shows its answer");

  console.log(`latest.test: ${checks} checks passed`);
}

await main();
