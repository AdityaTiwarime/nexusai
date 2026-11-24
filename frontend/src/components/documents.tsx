// Shared bits for the document pages.

import type { DocRole, DocumentStatus, DocumentSummary, Me } from "../api/client";
import { StatusBadge } from "./StatusBadge";

export const VISIBILITY_LABELS: Record<DocRole, string> = {
  employee: "Everyone",
  manager: "Managers and admins",
  org_admin: "Admins only",
};

const RANK: Record<DocRole, number> = { employee: 1, manager: 2, org_admin: 3 };

/** Visibility options a user may choose: never above their own role (the API enforces this). */
export function visibilityOptions(role: string): DocRole[] {
  const own = RANK[role as DocRole] ?? 0;
  return (Object.keys(RANK) as DocRole[]).filter((r) => RANK[r] <= own);
}

/** Org admins manage everything; managers manage what they uploaded. Mirrors the API. */
export function canManage(me: Me | null, doc: DocumentSummary): boolean {
  if (!me) return false;
  if (me.user.role === "org_admin") return true;
  return me.user.role === "manager" && doc.uploaded_by === me.user.id;
}

const STATUS: Record<DocumentStatus, { tone: "ok" | "error" | "pending"; label: string }> = {
  queued: { tone: "pending", label: "Queued" },
  processing: { tone: "pending", label: "Processing" },
  ready: { tone: "ok", label: "Ready" },
  failed: { tone: "error", label: "Failed" },
};

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  return <StatusBadge tone={STATUS[status].tone} label={STATUS[status].label} />;
}

export function isInProgress(status: DocumentStatus): boolean {
  return status === "queued" || status === "processing";
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const ACCEPTED_FILES = ".pdf,.docx,.txt,.csv,.md";
