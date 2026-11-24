import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../api/client";
import { bestExcerpt, chunkLink, Highlight, pagesLabel } from "../components/rag";
import { Button, ErrorAlert, inputClass } from "../components/ui";

export function SearchPage() {
  const [query, setQuery] = useState("");
  const search = useMutation({ mutationFn: (q: string) => api.search(q, 10) });
  const searched = search.variables ?? "";

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Search</h1>
        <p className="mt-1 text-sm text-slate-500">
          Hybrid search across your documents: meaning (vector similarity) and exact words (full-text), combined
          and re-ranked. Only documents you can access are searched.
        </p>
      </header>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) search.mutate(query.trim());
        }}
      >
        <input
          type="search"
          aria-label="Search query"
          placeholder="e.g. password rotation, travel booking, on-call…"
          className={inputClass}
          maxLength={1000}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button type="submit" disabled={!query.trim() || search.isPending}>
          {search.isPending ? "Searching…" : "Search"}
        </Button>
      </form>

      <ErrorAlert message={search.error instanceof ApiError ? search.error.detail : search.error ? "Search failed" : null} />

      {search.data && (
        <section aria-label="Results" className="space-y-3">
          <p className="text-sm text-slate-500">
            {search.data.results.length === 0
              ? "No matching passages in documents you can access."
              : `${search.data.results.length} passage${search.data.results.length === 1 ? "" : "s"} (from ${search.data.candidates} candidates)`}
          </p>
          {search.data.results.map((r) => (
            <article
              key={`${r.document_id}-${r.chunk_index}`}
              className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Link to={chunkLink(r.document_id, r.chunk_index)} className="font-medium text-slate-900 hover:text-brand-700">
                  {r.document_title}
                  <span className="ml-2 text-xs font-normal text-slate-500">
                    passage #{r.chunk_index + 1}
                    {pagesLabel(r.page_start, r.page_end) && ` · ${pagesLabel(r.page_start, r.page_end)}`}
                  </span>
                </Link>
                <span className="flex items-center gap-2 text-xs text-slate-500">
                  {r.keyword_match && (
                    <span className="rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
                      keyword match
                    </span>
                  )}
                  <span title="Cosine similarity of the embeddings">similarity {r.similarity.toFixed(2)}</span>
                  <span className="flex items-center gap-1" title="Relevance after re-ranking">
                    <span className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                      <span className="block h-full bg-brand-500" style={{ width: `${Math.round(r.score * 100)}%` }} />
                    </span>
                    {Math.round(r.score * 100)}%
                  </span>
                </span>
              </div>
              <p className="mt-2 text-sm leading-relaxed text-slate-700">
                <Highlight text={bestExcerpt(r.content, searched)} query={searched} />
              </p>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
