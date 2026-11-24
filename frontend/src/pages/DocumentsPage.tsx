import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, type DocRole, type DocumentStatus } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  ACCEPTED_FILES,
  DocumentStatusBadge,
  isInProgress,
  VISIBILITY_LABELS,
  visibilityOptions,
} from "../components/documents";
import { Button, ErrorAlert, Field, inputClass } from "../components/ui";

const PAGE_SIZE = 20;
const POLL_MS = 3_000;
const TABS: { label: string; status?: DocumentStatus }[] = [
  { label: "All" },
  { label: "Ready", status: "ready" },
  { label: "Processing", status: "processing" },
  { label: "Queued", status: "queued" },
  { label: "Failed", status: "failed" },
];

function UploadForm() {
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [minRole, setMinRole] = useState<DocRole>("employee");

  const upload = useMutation({
    mutationFn: (selected: File) => api.uploadDocument(selected, { title: title || undefined, min_role: minRole }),
    onSuccess: (_, uploaded) => {
      // Only clear the form if the user hasn't already picked the next file meanwhile.
      setFile((current) => (current === uploaded ? null : current));
      if (fileInput.current?.files?.[0] === uploaded) {
        fileInput.current.value = "";
        setTitle("");
      }
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const error = upload.error instanceof ApiError ? upload.error.detail : upload.error ? "Upload failed" : null;
  const duplicateOf =
    upload.error instanceof ApiError && upload.error.status === 409
      ? ((upload.error.body as { detail?: { document_id?: string } })?.detail?.document_id ?? null)
      : null;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (file) upload.mutate(file);
      }}
      className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
    >
      <h2 className="text-base font-semibold">Upload a document</h2>
      <p className="mt-1 text-sm text-slate-500">
        PDF, Word, text, CSV or Markdown. Files are processed in the background and become searchable
        once they are ready.
      </p>
      <div className="mt-4 grid gap-4 md:grid-cols-3">
        <Field label="File">
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPTED_FILES}
            className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium hover:file:bg-slate-200"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </Field>
        <Field label="Title" hint="Optional. Defaults to the file name.">
          <input className={inputClass} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label="Visible to">
          <select className={inputClass} value={minRole} onChange={(e) => setMinRole(e.target.value as DocRole)}>
            {visibilityOptions(me?.user.role ?? "employee").map((r) => (
              <option key={r} value={r}>
                {VISIBILITY_LABELS[r]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <Button type="submit" disabled={!file || upload.isPending}>
          {upload.isPending ? "Uploading…" : "Upload"}
        </Button>
        {upload.isSuccess && <span className="text-sm text-emerald-700">Uploaded. Processing has started.</span>}
      </div>
      <div className="mt-3 space-y-1">
        <ErrorAlert message={error} />
        {duplicateOf && (
          <Link to={`/documents/${duplicateOf}`} className="text-sm text-brand-700 hover:underline">
            Open the existing document
          </Link>
        )}
      </div>
    </form>
  );
}

export function DocumentsPage() {
  const { hasRole } = useAuth();
  const [tab, setTab] = useState(0);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const status = TABS[tab].status;

  const documents = useQuery({
    queryKey: ["documents", status, search, page],
    queryFn: () => api.listDocuments({ limit: PAGE_SIZE, offset: page * PAGE_SIZE, status, q: search || undefined }),
    placeholderData: keepPreviousData,
    // Keep polling while something is still being processed, then stop.
    refetchInterval: (query) =>
      query.state.data?.items.some((d) => isInProgress(d.status)) ? POLL_MS : false,
  });
  const counts = documents.data?.status_counts ?? {};
  const allCount = Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0);
  const total = documents.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Documents</h1>
        <p className="mt-1 text-sm text-slate-500">
          Your organization's knowledge base. You only see documents your role is allowed to see.
        </p>
      </header>

      {hasRole("org_admin", "manager") && <UploadForm />}

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div role="tablist" aria-label="Filter by status" className="flex flex-wrap gap-1">
            {TABS.map((t, i) => {
              const count = t.status ? (counts[t.status] ?? 0) : allCount;
              return (
                <button
                  key={t.label}
                  role="tab"
                  aria-selected={tab === i}
                  onClick={() => {
                    setTab(i);
                    setPage(0);
                  }}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                    tab === i ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {t.label} <span className="tabular-nums text-slate-400">{count}</span>
                </button>
              );
            })}
          </div>
          <input
            type="search"
            aria-label="Search by title"
            placeholder="Search by title…"
            className={`${inputClass} w-64`}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(0);
            }}
          />
        </div>

        <div className="mt-4">
          {documents.isPending ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : documents.isError ? (
            <ErrorAlert message={documents.error instanceof ApiError ? documents.error.detail : "Failed to load"} />
          ) : documents.data.items.length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-500">
              {search || status ? "No documents match." : "No documents yet."}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="pb-2 pr-4 font-medium">Title</th>
                    <th className="pb-2 pr-4 font-medium">Type</th>
                    <th className="pb-2 pr-4 font-medium">Visible to</th>
                    <th className="pb-2 pr-4 font-medium">Status</th>
                    <th className="pb-2 pr-4 text-right font-medium">Chunks</th>
                    <th className="pb-2 pr-4 font-medium">Uploaded by</th>
                    <th className="pb-2 font-medium">Uploaded</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {documents.data.items.map((doc) => (
                    <tr key={doc.id}>
                      <td className="py-2.5 pr-4">
                        <Link to={`/documents/${doc.id}`} className="font-medium text-slate-900 hover:text-brand-700">
                          {doc.title}
                        </Link>
                        {doc.current_version > 1 && (
                          <span className="ml-2 text-xs text-slate-400">v{doc.current_version}</span>
                        )}
                        {doc.status === "failed" && doc.error && (
                          <p className="mt-0.5 text-xs text-rose-600">{doc.error}</p>
                        )}
                      </td>
                      <td className="py-2.5 pr-4 uppercase text-slate-500">{doc.file_type}</td>
                      <td className="py-2.5 pr-4 text-slate-600">{VISIBILITY_LABELS[doc.min_role]}</td>
                      <td className="py-2.5 pr-4">
                        <DocumentStatusBadge status={doc.status} />
                      </td>
                      <td className="py-2.5 pr-4 text-right tabular-nums text-slate-600">
                        {doc.status === "ready" ? doc.chunk_count : "—"}
                      </td>
                      <td className="py-2.5 pr-4 text-slate-600">{doc.uploaded_by_email ?? "—"}</td>
                      <td className="py-2.5 whitespace-nowrap text-xs text-slate-500">
                        {new Date(doc.created_at).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
          <span>
            {total} document{total === 1 ? "" : "s"}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="secondary" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span>
              Page {page + 1} of {pages}
            </span>
            <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
