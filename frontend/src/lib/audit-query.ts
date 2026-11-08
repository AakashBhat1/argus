/** Audit log types and the query-string builder (pure; unit-tested). */

/** One audited action (GET /audit, GET /parking/audit). */
export interface AuditEvent {
  id: string;
  created_at: string;
  tenant_id: string;
  actor: string;
  actor_id?: string | null;
  action: string;
  target_type?: string | null;
  target_id?: string | null;
  details?: Record<string, unknown> | null;
  source_ip?: string | null;
}

export interface AuditPage {
  items: AuditEvent[];
  next_cursor?: string | null;
}

export type AuditService = "surveillance" | "parking";

export interface AuditFilters {
  /** Exact action, or a prefix ending in ".*" (e.g. "camera.*"). */
  action?: string;
  actor?: string;
  /** ISO timestamps. */
  since?: string;
  until?: string;
  limit?: number;
  cursor?: string;
}

export function auditQuery(filters: AuditFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && String(value).trim() !== "") params.set(key, String(value).trim());
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}
