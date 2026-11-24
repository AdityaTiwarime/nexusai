import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError, type DocRole, type DocumentDetail } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  ACCEPTED_FILES,
  canManage,
  DocumentStatusBadge,
  formatBytes,
  isInProgress,
  VISIBILITY_LABELS,
  visibilityOptions,
} from "../components/documents";
import { Button, ErrorAlert } from "../components/ui";

const CHUNK_PAGE = 10;
const POLL_MS = 3_000;

function errorText(err: unknown): string | null {
  if (!err) return null;
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 text-sm text-slate-800">{children}</dd>
    </div>
  );
}

function pages(start: number | null, end: number | null): string | null {
  if (start === null) return null;
  return start === end ? `Page ${start}` : `Pages ${start}–${end}`;
}

function Actions({ doc }: { doc: DocumentDetail }) {
  const { me } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const versionInput = useRef<HTMLInputElement>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refresh = (updated?: DocumentDetail) => {
    if (updated) queryClient.setQueryData(["document", doc.id], updated);
    queryClient.invalidateQueries({ queryKey: ["documents"] });
    queryClient.invalidateQueries({ queryKey: ["document-chunks", doc.id] });
  };
  const reprocess = useMutation({ mutationFn: () => api.reprocessDocument(doc.id), onSuccess: refresh });
  const newVersion = useMutation({
    mutationFn: (file: File) => api.uploadVersion(doc.id, file),
    onSuccess: refresh,
    onSettled: () => {
      if (versionInput.current) versionInput.current.value = "";
    },
  });
  const visibility = useMutation({
    mutationFn: (minRole: DocRole) => api.updateDocument(doc.id, { min_role: minRole }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteDocument(doc.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      navigate("/documents", { replace: true });
    },
  });

  if (!canManage(me, doc)) return null;
  const busy = reprocess.isPending || newVersion.isPending || remove.isPending;

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold">Manage</h2>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label className="text-sm">
          <span className="mr-2 text-slate-600">Visible to</span>
          <select
            aria-label="Visible to"
            className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
            value={doc.min_role}
            disabled={visibility.isPending}
            onChange={(e) => visibility.mutate(e.target.value as DocRole)}
          >
            {visibilityOptions(me?.user.role ?? "employee").map((r) => (
              <option key={r} value={r}>
                {VISIBILITY_LABELS[r]}
              </option>
            ))}
          </select>
        </label>

        <Button variant="secondary" disabled={busy} onClick={() => versionInput.current?.click()}>
          {newVersion.isPending ? "Uploading…" : "Upload new version"}
        </Button>
        <input
          ref={versionInput}
          type="file"
          aria-label="New version file"
          accept={ACCEPTED_FILES}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) newVersion.mutate(file);
          }}
        />

        <Button variant="secondary" disabled={busy || isInProgress(doc.status)} onClick={() => reprocess.mutate()}>
          {reprocess.isPending ? "Queuing…" : "Reprocess"}
        </Button>

        {confirmDelete ? (
          <span className="flex items-center gap-2">
            <span className="text-sm text-rose-700">Delete permanently?</span>
            <Button
              className="bg-rose-600 hover:bg-rose-700"
              disabled={busy}
              onClick={() => remove.mutate()}
            >
              {remove.isPending ? "Deleting…" : "Yes, delete"}
            </Button>
            <Button variant="secondary" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
          </span>
        ) : (
          <Button variant="secondary" className="text-rose-700" disabled={busy} onClick={() => setConfirmDelete(true)}>
            Delete
          </Button>
        )}
      </div>
      <div className="mt-3">
        <ErrorAlert
          message={
            errorText(reprocess.error) ??
            errorText(newVersion.error) ??
            errorText(visibility.error) ??
            errorText(remove.error)
          }
        />
      </div>
    </section>
  );
}

function Chunks({ doc }: { doc: DocumentDetail }) {
  const [page, setPage] = useState(0);
  const chunks = useQuery({
    queryKey: ["document-chunks", doc.id, doc.processed_at, page],
    queryFn: () => api.documentChunks(doc.id, { limit: CHUNK_PAGE, offset: page * CHUNK_PAGE }),
    enabled: doc.status === "ready",
    placeholderData: keepPreviousData,
  });
  const total = chunks.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / CHUNK_PAGE));

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold">Extracted chunks</h2>
      <p className="mt-1 text-sm text-slate-500">
        The text the AI will search over, split into overlapping passages.
      </p>
      {doc.status !== "ready" ? (
        <p className="mt-4 text-sm text-slate-500">
          {doc.status === "failed" ? "No chunks: processing failed." : "Chunks appear once processing finishes."}
        </p>
      ) : chunks.isPending ? (
        <p className="mt-4 text-sm text-slate-500">Loading…</p>
      ) : chunks.isError ? (
        <ErrorAlert message={errorText(chunks.error)} />
      ) : (
        <>
          <ol className="mt-4 space-y-3">
            {chunks.data.items.map((chunk) => (
              <li key={chunk.chunk_index} className="rounded-lg bg-slate-50 p-3 ring-1 ring-inset ring-slate-200">
                <div className="mb-1.5 flex gap-3 text-xs text-slate-500">
                  <span className="font-medium">#{chunk.chunk_index + 1}</span>
                  {pages(chunk.page_start, chunk.page_end) && <span>{pages(chunk.page_start, chunk.page_end)}</span>}
                  <span>{chunk.char_count} characters</span>
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{chunk.content}</p>
              </li>
            ))}
          </ol>
          <div className="mt-4 flex items-center justify-end gap-2 text-sm text-slate-500">
            <Button variant="secondary" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span>
              {page + 1} / {pageCount}
            </span>
            <Button variant="secondary" disabled={page + 1 >= pageCount} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </div>
        </>
      )}
    </section>
  );
}

export function DocumentDetailPage() {
  const { id = "" } = useParams();
  const document = useQuery({
    queryKey: ["document", id],
    queryFn: () => api.getDocument(id),
    refetchInterval: (query) => (query.state.data && isInProgress(query.state.data.status) ? POLL_MS : false),
  });
  const [downloadError, setDownloadError] = useState<string | null>(null);

  if (document.isPending) return <p className="text-sm text-slate-500">Loading…</p>;
  if (document.isError) {
    const notFound = document.error instanceof ApiError && document.error.status === 404;
    return (
      <div className="mx-auto max-w-3xl space-y-3">
        <ErrorAlert message={notFound ? "Document not found, or you don't have access to it." : errorText(document.error)} />
        <Link to="/documents" className="text-sm text-brand-700 hover:underline">
          ← Back to documents
        </Link>
      </div>
    );
  }
  const doc = document.data;
  const characters = typeof doc.metadata.characters === "number" ? doc.metadata.characters : null;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <Link to="/documents" className="text-sm text-slate-500 hover:text-slate-700">
          ← Documents
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{doc.title}</h1>
          <DocumentStatusBadge status={doc.status} />
        </div>
      </div>

      {doc.status === "failed" && doc.error && <ErrorAlert message={`Processing failed: ${doc.error}`} />}

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Fact label="Type">{doc.file_type.toUpperCase()}</Fact>
          <Fact label="Visible to">{VISIBILITY_LABELS[doc.min_role]}</Fact>
          <Fact label="Chunks">{doc.status === "ready" ? doc.chunk_count : "—"}</Fact>
          <Fact label="Pages">{doc.page_count ?? "—"}</Fact>
          <Fact label="Characters">{characters?.toLocaleString() ?? "—"}</Fact>
          <Fact label="Uploaded by">{doc.uploaded_by_email ?? "—"}</Fact>
          <Fact label="Uploaded">{new Date(doc.created_at).toLocaleString()}</Fact>
          <Fact label="Processed">{doc.processed_at ? new Date(doc.processed_at).toLocaleString() : "—"}</Fact>
        </dl>
      </section>

      <Actions doc={doc} />

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold">Versions</h2>
        <ul className="mt-3 divide-y divide-slate-100">
          {doc.versions.map((version) => (
            <li key={version.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-sm">
              <span>
                <span className="font-medium text-slate-900">v{version.version}</span>
                {version.version === doc.current_version && (
                  <span className="ml-2 rounded bg-brand-50 px-1.5 py-0.5 text-xs text-brand-700">current</span>
                )}
                <span className="ml-3 text-slate-600">{version.original_filename}</span>
                <span className="ml-3 text-slate-400">{formatBytes(version.size_bytes)}</span>
              </span>
              <span className="flex items-center gap-3">
                <span className="text-xs text-slate-500">{new Date(version.created_at).toLocaleString()}</span>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setDownloadError(null);
                    api.downloadDocument(doc.id, version).catch((err: unknown) => setDownloadError(errorText(err)));
                  }}
                >
                  Download
                </Button>
              </span>
            </li>
          ))}
        </ul>
        <ErrorAlert message={downloadError} />
      </section>

      <Chunks doc={doc} />
    </div>
  );
}
