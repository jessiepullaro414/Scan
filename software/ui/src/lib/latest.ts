/**
 * Requests that follow a dragged control without piling up (the Section tool's offset slider): at most one request
 * runs at a time; asked again while it runs, only the newest request waits (the ones in between are dropped, never
 * sent), and goes out as soon as the running one ends. An answer is shown only when no newer one has been shown, so the
 * view follows the drag one round trip behind and never steps back. An error is shown only for the newest request
 * asked. `cancel` (leaving the tool) drops the waiting request and ignores the running one's answer.
 */
export type Latest<Q> = {
  /** Ask for `q`: sent now when nothing runs, else kept as the one to send next (replacing any other waiting). */
  ask: (q: Q) => void;
  /** Forget the waiting request and ignore what is running. */
  cancel: () => void;
  /** A request is running. */
  busy: () => boolean;
};

/** `idle`: called when a request ends and none waits (to show that nothing is on its way). */
export function latestOnly<Q, A>(run: (q: Q) => Promise<A>, deliver: (a: A, q: Q) => void, fail?: (e: unknown, q: Q) => void, idle?: () => void): Latest<Q> {
  let asked = 0; // the number of the newest request asked
  let shown = 0; // the newest request whose answer (or error) was shown, or the last cancel
  let running = false;
  let waiting: { q: Q; n: number } | null = null;

  const start = (q: Q, n: number) => {
    running = true;
    run(q)
      .then(
        (a) => {
          if (n > shown) {
            shown = n;
            deliver(a, q);
          }
        },
        (e: unknown) => {
          if (n === asked && n > shown) {
            shown = n;
            fail?.(e, q);
          }
        },
      )
      .finally(() => {
        running = false;
        const next = waiting;
        waiting = null;
        if (next) start(next.q, next.n);
        else idle?.();
      });
  };

  return {
    ask: (q) => {
      const n = ++asked;
      if (running) waiting = { q, n };
      else start(q, n);
    },
    cancel: () => {
      waiting = null;
      shown = asked;
    },
    busy: () => running,
  };
}
