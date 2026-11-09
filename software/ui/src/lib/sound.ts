/**
 * The short "shutter" click played when a calibration view is captured (WebAudio, no file).
 * The AudioContext is created on the first user gesture (`unlockAudio`), as browsers require;
 * without one the click is silently skipped.
 */
let ctx: AudioContext | null = null;
let noise: AudioBuffer | null = null;

function context(): AudioContext | null {
  if (ctx) return ctx;
  const Ctor: typeof AudioContext | undefined =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  try {
    ctx = new Ctor();
  } catch {
    return null;
  }
  return ctx;
}

/** Call from a click or key handler so later (automatic) clicks may play. */
export function unlockAudio(): void {
  const c = context();
  if (c && c.state === "suspended") void c.resume().catch(() => {});
}

function noiseBuffer(c: AudioContext): AudioBuffer {
  if (noise && noise.sampleRate === c.sampleRate) return noise;
  const n = Math.round(c.sampleRate * 0.05);
  const b = c.createBuffer(1, n, c.sampleRate);
  const d = b.getChannelData(0);
  let s = 22222;
  for (let i = 0; i < n; i++) {
    s = (s * 1103515245 + 12345) >>> 0;
    d[i] = (s / 4294967296) * 2 - 1;
  }
  noise = b;
  return b;
}

/** A soft two-part mechanical click with a faint tone, ~90 ms. */
export function playCaptureClick(volume = 0.5): void {
  const c = context();
  if (!c || c.state !== "running") return;
  try {
    const t0 = c.currentTime + 0.005;
    const out = c.createGain();
    out.gain.value = volume;
    out.connect(c.destination);

    const burst = (at: number, dur: number, freq: number, gain: number) => {
      const src = c.createBufferSource();
      src.buffer = noiseBuffer(c);
      const bp = c.createBiquadFilter();
      bp.type = "bandpass";
      bp.frequency.value = freq;
      bp.Q.value = 1.4;
      const g = c.createGain();
      g.gain.setValueAtTime(0, at);
      g.gain.linearRampToValueAtTime(gain, at + 0.002);
      g.gain.exponentialRampToValueAtTime(0.001, at + dur);
      src.connect(bp).connect(g).connect(out);
      src.start(at);
      src.stop(at + dur + 0.01);
    };
    burst(t0, 0.022, 3200, 0.55);
    burst(t0 + 0.045, 0.03, 2100, 0.35);

    const osc = c.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(1480, t0);
    osc.frequency.exponentialRampToValueAtTime(980, t0 + 0.08);
    const og = c.createGain();
    og.gain.setValueAtTime(0, t0);
    og.gain.linearRampToValueAtTime(0.12, t0 + 0.004);
    og.gain.exponentialRampToValueAtTime(0.001, t0 + 0.09);
    osc.connect(og).connect(out);
    osc.start(t0);
    osc.stop(t0 + 0.1);
  } catch {
    /* audio is a nicety: never let it break the capture */
  }
}
