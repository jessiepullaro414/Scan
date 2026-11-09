/** Commands to the 3D view from outside the canvas (keyboard shortcuts, toolbar buttons). */
export type FrameSphere = { center: [number, number, number]; radius: number };

export type ViewCommand =
  | {
      type: "frame";
      animate?: boolean;
      direction?: [number, number, number];
      /** Frame this sphere (three.js coordinates) instead of the view's default bounds. */
      sphere?: FrameSphere;
      /** Extra room around the bounds (1 = tight). */
      margin?: number;
    }
  | { type: "view"; direction: [number, number, number] };

type Listener = (c: ViewCommand) => void;

const listeners = new Set<Listener>();

export const viewBus = {
  emit(c: ViewCommand): void {
    for (const l of listeners) l(c);
  },
  on(l: Listener): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};
