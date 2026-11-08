import { fetchApi } from "./api";
import { auditQuery, type AuditFilters, type AuditPage, type AuditService } from "./audit-query";

export type { AuditEvent, AuditFilters, AuditPage, AuditService } from "./audit-query";

const PATHS: Record<AuditService, string> = {
  surveillance: "/audit",
  parking: "/parking/audit",
};

export function listAudit(service: AuditService, filters: AuditFilters = {}): Promise<AuditPage> {
  // Parking may be undeployed: do not bounce to /login on its 401.
  return fetchApi<AuditPage>(`${PATHS[service]}${auditQuery(filters)}`, undefined, {
    noAuthRedirect: service === "parking",
  });
}
