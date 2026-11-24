import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { DocumentDetail, DocumentSummary } from "../api/client";
import { jsonResponse, makeMe, mockApi, renderApp, signedInRoutes } from "./helpers";

function makeDoc(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: "d-1",
    title: "Remote work policy",
    file_type: "pdf",
    min_role: "employee",
    status: "ready",
    current_version: 1,
    chunk_count: 4,
    page_count: 3,
    error: null,
    metadata: { characters: 4210 },
    uploaded_by: "u-1",
    uploaded_by_email: "asha@acme.com",
    created_at: "2026-11-24T10:00:00Z",
    updated_at: "2026-11-24T10:00:00Z",
    processed_at: "2026-11-24T10:00:05Z",
    versions: [
      {
        id: "v-1",
        version: 1,
        original_filename: "remote-work.pdf",
        size_bytes: 20480,
        sha256: "abc",
        uploaded_by: "u-1",
        created_at: "2026-11-24T10:00:00Z",
      },
    ],
    ...overrides,
  };
}

function page(items: DocumentSummary[], counts: Record<string, number>) {
  return jsonResponse({ items, total: items.length, limit: 20, offset: 0, status_counts: counts });
}

describe("Documents page", () => {
  it("lists documents with status, visibility and failure reasons", async () => {
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /documents": page(
        [
          makeDoc(),
          makeDoc({ id: "d-2", title: "Scanned contract", status: "failed", error: "No text could be extracted", min_role: "org_admin" }),
        ],
        { ready: 1, failed: 1 },
      ),
    });
    renderApp("/documents");
    expect(await screen.findByRole("link", { name: "Remote work policy" })).toBeInTheDocument();
    expect(screen.getByText("No text could be extracted")).toBeInTheDocument();
    expect(screen.getByText("Admins only", { selector: "td" })).toBeInTheDocument();
    expect(screen.getByText("Failed", { selector: "span" })).toBeInTheDocument();
    const tabs = screen.getByRole("tablist", { name: "Filter by status" });
    expect(within(tabs).getByRole("tab", { name: "All 2" })).toBeInTheDocument();
    expect(within(tabs).getByRole("tab", { name: "Failed 1" })).toBeInTheDocument();
  });

  it("uploads a file as multipart form data", async () => {
    let sent: FormData | null = null;
    mockApi({
      ...signedInRoutes("manager"),
      "GET /documents": page([], {}),
      "POST /documents": (init) => {
        sent = init?.body as FormData;
        return jsonResponse(makeDoc({ status: "queued" }), 201);
      },
    });
    renderApp("/documents");

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(await screen.findByLabelText("File"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText(/Title/), { target: { value: "Team notes" } });
    fireEvent.change(screen.getByLabelText("Visible to"), { target: { value: "manager" } });
    // A manager can't restrict a document to admins only.
    expect(screen.queryByRole("option", { name: "Admins only" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByText(/Uploaded. Processing has started/)).toBeInTheDocument();
    const form = sent as unknown as FormData;
    expect((form.get("file") as File).name).toBe("notes.txt");
    expect(form.get("title")).toBe("Team notes");
    expect(form.get("min_role")).toBe("manager");
  });

  it("shows the API's reason when an upload is rejected, with a link to a duplicate", async () => {
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /documents": page([], {}),
      "POST /documents": jsonResponse(
        { detail: { message: "This file has already been uploaded", document_id: "d-9" } },
        409,
      ),
    });
    renderApp("/documents");
    fireEvent.change(await screen.findByLabelText("File"), {
      target: { files: [new File(["x"], "a.txt")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This file has already been uploaded");
    expect(screen.getByRole("link", { name: "Open the existing document" })).toHaveAttribute("href", "/documents/d-9");
  });

  it("does not offer uploads to employees", async () => {
    mockApi({ ...signedInRoutes("employee"), "GET /documents": page([makeDoc()], { ready: 1 }) });
    renderApp("/documents");
    expect(await screen.findByRole("link", { name: "Remote work policy" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upload" })).not.toBeInTheDocument();
  });
});

describe("Document detail page", () => {
  const chunks = jsonResponse({
    items: [
      { chunk_index: 0, content: "Employees may work from home on Fridays.", page_start: 1, page_end: 1, char_count: 41 },
      { chunk_index: 1, content: "Equipment is provided.", page_start: 2, page_end: 3, char_count: 22 },
    ],
    total: 2,
    limit: 10,
    offset: 0,
  });

  it("shows facts, versions and extracted chunks with page numbers", async () => {
    mockApi({
      ...signedInRoutes("employee"),
      "GET /documents/d-1": jsonResponse(makeDoc({ uploaded_by: "someone-else" })),
      "GET /documents/d-1/chunks": chunks,
    });
    renderApp("/documents/d-1");
    expect(await screen.findByRole("heading", { name: "Remote work policy" })).toBeInTheDocument();
    expect(await screen.findByText("Employees may work from home on Fridays.")).toBeInTheDocument();
    expect(screen.getByText("Page 1")).toBeInTheDocument();
    expect(screen.getByText("Pages 2–3")).toBeInTheDocument();
    expect(screen.getByText("remote-work.pdf")).toBeInTheDocument();
    expect(screen.getByText("20.0 KB")).toBeInTheDocument();
    expect(screen.getByText("4,210")).toBeInTheDocument();
    // Employees can read but not manage.
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("lets the uploader reprocess a failed document", async () => {
    let reprocessed = false;
    const failed = makeDoc({ status: "failed", error: "The PDF is corrupt or unreadable", chunk_count: 0 });
    mockApi({
      ...signedInRoutes("manager"),
      "GET /auth/me": jsonResponse(makeMe("manager")),
      "GET /documents/d-1": jsonResponse(failed),
      "POST /documents/d-1/reprocess": () => {
        reprocessed = true;
        return jsonResponse({ ...failed, status: "queued", error: null });
      },
    });
    renderApp("/documents/d-1");
    expect(await screen.findByText("Processing failed: The PDF is corrupt or unreadable")).toBeInTheDocument();
    expect(screen.getByText("No chunks: processing failed.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reprocess" }));
    await waitFor(() => expect(reprocessed).toBe(true));
    expect(await screen.findByText("Queued")).toBeInTheDocument();
  });

  it("asks for confirmation before deleting", async () => {
    let deleted = false;
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /documents/d-1": jsonResponse(makeDoc()),
      "GET /documents/d-1/chunks": chunks,
      "GET /documents": page([], {}),
      "DELETE /documents/d-1": () => {
        deleted = true;
        return jsonResponse(null, 204);
      },
    });
    renderApp("/documents/d-1");
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(deleted).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(deleted).toBe(true));
    expect(await screen.findByRole("heading", { name: "Documents" })).toBeInTheDocument();
  });

  it("treats a hidden or foreign document as not found", async () => {
    mockApi({
      ...signedInRoutes("employee"),
      "GET /documents/d-x": jsonResponse({ detail: "Document not found" }, 404),
    });
    renderApp("/documents/d-x");
    expect(await screen.findByText(/Document not found, or you don't have access/)).toBeInTheDocument();
  });
});
