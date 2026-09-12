import type { ReactNode } from "react";

/*
 * Safe markdown renderer for UNTRUSTED LLM report text.
 * Parses a tiny subset (headings, bold, inline code, lists) into React
 * nodes. No dangerouslySetInnerHTML, no raw HTML pass-through — React
 * escaping applies to every rendered string. Unknown syntax renders as
 * plain text.
 */

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  // Split on **bold** and `code` tokens; escape-safe (no HTML produced).
  const nodes: ReactNode[] = [];
  const pattern = /\*\*([^*]+)\*\*|`([^`\n]+)`/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    if (match[1] !== undefined) {
      nodes.push(
        <strong key={`${keyPrefix}-b${i}`} className="font-semibold text-terminal-text">
          {match[1]}
        </strong>,
      );
    } else if (match[2] !== undefined) {
      nodes.push(
        <code
          key={`${keyPrefix}-c${i}`}
          className="numeric rounded bg-slate-800/80 px-1 py-0.5 text-[0.85em] text-emerald-300"
        >
          {match[2]}
        </code>,
      );
    }
    last = match.index + match[0].length;
    i += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function SafeMarkdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];

  const flushList = (key: string) => {
    if (listItems.length === 0) return;
    blocks.push(
      <ul key={key} className="my-2 list-disc space-y-1 pl-5 text-sm text-slate-300">
        {listItems.map((item, idx) => (
          <li key={idx}>{renderInline(item, `${key}-${idx}`)}</li>
        ))}
      </ul>,
    );
    listItems = [];
  };

  lines.forEach((line, idx) => {
    const trimmed = line.trimEnd();

    const heading = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (heading) {
      flushList(`ul-${idx}`);
      const level = heading[1].length;
      const sizeCls =
        level === 1
          ? "text-lg"
          : level === 2
            ? "text-base"
            : "text-sm";
      blocks.push(
        <p
          key={`h-${idx}`}
          className={`numeric mt-4 mb-1 font-semibold uppercase tracking-wide text-emerald-300/90 ${sizeCls}`}
        >
          {renderInline(heading[2], `h-${idx}`)}
        </p>,
      );
      return;
    }

    const bullet = /^[-*\u2022]\s+(.*)$/.exec(trimmed);
    if (bullet) {
      listItems.push(bullet[1]);
      return;
    }

    flushList(`ul-${idx}`);
    if (trimmed.length === 0) return;
    blocks.push(
      <p key={`p-${idx}`} className="my-1.5 text-sm leading-relaxed text-slate-300">
        {renderInline(trimmed, `p-${idx}`)}
      </p>,
    );
  });
  flushList("ul-final");

  return <div className="max-w-none">{blocks}</div>;
}
