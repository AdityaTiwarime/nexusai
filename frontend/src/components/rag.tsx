// Shared pieces for chat and search: citation links, page labels, highlighting.

import { Fragment } from "react";
import { Link } from "react-router-dom";

import type { Citation } from "../api/client";

export function pagesLabel(start: number | null, end: number | null): string | null {
  if (start === null) return null;
  return start === end ? `p. ${start}` : `pp. ${start}–${end}`;
}

/** Link to the exact chunk inside the document page. */
export function chunkLink(documentId: string, chunkIndex: number): string {
  return `/documents/${documentId}?chunk=${chunkIndex}`;
}

/** Answer text with every [n] turned into a link to source n. */
export function AnswerText({ text, citations }: { text: string; citations: Citation[] }) {
  const byNumber = new Map(citations.map((c) => [c.number, c]));
  const parts = text.split(/(\[\d{1,2}\])/g);
  return (
    <p className="whitespace-pre-wrap leading-relaxed">
      {parts.map((part, i) => {
        const match = /^\[(\d{1,2})\]$/.exec(part);
        if (!match) return <Fragment key={i}>{part}</Fragment>;
        const citation = byNumber.get(Number(match[1]));
        if (!citation?.available || !citation.document_id || citation.chunk_index === null) {
          return (
            <sup key={i} className="mx-0.5 text-xs text-slate-400" title="Source no longer available">
              {part}
            </sup>
          );
        }
        return (
          <Link
            key={i}
            to={chunkLink(citation.document_id, citation.chunk_index)}
            title={citation.document_title ?? undefined}
            className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded bg-brand-50 px-1 align-text-top text-[11px] font-semibold text-brand-700 ring-1 ring-inset ring-brand-600/20 hover:bg-brand-100"
          >
            {match[1]}
          </Link>
        );
      })}
    </p>
  );
}

/**
 * The part of a passage that best matches `query`: the sentence with the most
 * query words, extended with its neighbours up to `maxChars`. Markdown heading
 * markers are dropped. Falls back to the start of the passage.
 */
export function bestExcerpt(content: string, query: string, maxChars = 300): string {
  const words = new Set(query.toLowerCase().match(/[a-z0-9]{3,}/g) ?? []);
  const sentences = content
    .replace(/^#{1,6}\s+/gm, "")
    .split(/(?<=[.!?])\s+|\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (sentences.length === 0) return "";
  const hits = (s: string) => (s.toLowerCase().match(/[a-z0-9]{3,}/g) ?? []).filter((w) => words.has(w)).length;
  let best = 0;
  sentences.forEach((s, i) => {
    if (hits(s) > hits(sentences[best])) best = i;
  });
  let start = best;
  let end = best + 1;
  const length = () => sentences.slice(start, end).join(" ").length;
  while (length() < maxChars && (start > 0 || end < sentences.length)) {
    if (end < sentences.length) end += 1;
    else start -= 1;
  }
  let text = sentences.slice(start, end).join(" ");
  if (text.length > maxChars) text = `${text.slice(0, maxChars).trimEnd()}…`;
  return `${start > 0 ? "…" : ""}${text}`;
}

const ESCAPE = /[.*+?^${}()|[\]\\]/g;

/** Wraps the query's words (3+ letters) in <mark>. Plain text otherwise. */
export function Highlight({ text, query }: { text: string; query: string }) {
  const words = Array.from(new Set(query.toLowerCase().match(/[a-z0-9]{3,}/g) ?? []));
  if (words.length === 0) return <>{text}</>;
  const pattern = new RegExp(`(${words.map((w) => w.replace(ESCAPE, "\\$&")).join("|")})`, "gi");
  return (
    <>
      {text.split(pattern).map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-sm bg-amber-100 text-slate-900">
            {part}
          </mark>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}
