import { Fragment } from "react";

/** Plain text where `backticked` parts (CLI commands in engine hints) render as inline code. */
export function RichText({ text }: { text: string }) {
  const parts = text.split("`");
  if (parts.length < 3) return <>{text}</>;
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 && i < parts.length - 1 ? (
          <code key={i} className="rounded border border-line bg-surface-3 px-1 py-px font-mono text-[0.92em] text-fg">
            {p}
          </code>
        ) : (
          <Fragment key={i}>{i % 2 === 1 ? `\`${p}` : p}</Fragment>
        ),
      )}
    </>
  );
}
