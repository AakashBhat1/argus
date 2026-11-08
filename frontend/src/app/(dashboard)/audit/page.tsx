"use client";

import { useEffect, useState } from "react";
import { Loader2, ScrollText, ShieldOff } from "lucide-react";
import AuditFilterBar, { type AuditQueryState } from "@/components/audit/AuditFilterBar";
import AuditTable from "@/components/audit/AuditTable";
import { listAudit, type AuditEvent } from "@/lib/audit";
import { useIsAdmin, useMounted } from "@/lib/hooks";

const PAGE_SIZE = 100;
const INITIAL_QUERY: AuditQueryState = { service: "surveillance", filters: {} };

export default function AuditPage() {
  const mounted = useMounted();
  const isAdmin = useIsAdmin();
  const [query, setQuery] = useState<AuditQueryState>(INITIAL_QUERY);
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // First page whenever the query changes.
  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    listAudit(query.service, { ...query.filters, limit: PAGE_SIZE })
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setCursor(page.next_cursor ?? null);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setItems([]);
        setCursor(null);
        setError(err instanceof Error ? err.message : "Failed to load the audit log");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [query, isAdmin]);

  function apply(next: AuditQueryState) {
    setLoading(true);
    setQuery(next);
  }

  async function loadMore() {
    if (!cursor) return;
    setLoading(true);
    try {
      const page = await listAudit(query.service, { ...query.filters, limit: PAGE_SIZE, cursor });
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.next_cursor ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load more events");
    } finally {
      setLoading(false);
    }
  }

  if (!mounted) return null;
  if (!isAdmin) {
    return (
      <div className="card flex items-center gap-3 text-sm text-slate-400">
        <ShieldOff className="w-5 h-5 text-slate-500" /> The audit log is available to administrators only.
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="page-title flex items-center gap-2">
          <ScrollText className="w-6 h-6 text-blue-400" />
          <span className="gradient-text-static">Audit log</span>
        </h1>
        <p className="page-subtitle">Who changed what, newest first. Credentials are never recorded.</p>
      </div>

      <AuditFilterBar initial={INITIAL_QUERY} onApply={apply} />

      <div className="card">
        {error && <p className="text-sm text-red-400 mb-3">{error}</p>}
        <AuditTable items={items} />
        <div className="flex justify-center pt-4">
          {loading ? (
            <Loader2 className="w-5 h-5 animate-spin text-slate-500" />
          ) : cursor ? (
            <button type="button" className="btn-ghost" onClick={loadMore}>
              Load older events
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
