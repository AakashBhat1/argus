"use client";

import { useState } from "react";
import { Filter, Search } from "lucide-react";
import type { AuditFilters, AuditService } from "@/lib/audit";

export interface AuditQueryState {
  service: AuditService;
  filters: AuditFilters;
}

interface Props {
  initial: AuditQueryState;
  onApply: (query: AuditQueryState) => void;
}

/** datetime-local value -> ISO timestamp (local time), or undefined. */
function toIso(local: string): string | undefined {
  if (!local) return undefined;
  const parsed = new Date(local);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

export default function AuditFilterBar({ initial, onApply }: Props) {
  const [service, setService] = useState<AuditService>(initial.service);
  const [action, setAction] = useState(initial.filters.action ?? "");
  const [actor, setActor] = useState(initial.filters.actor ?? "");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    onApply({ service, filters: { action, actor, since: toIso(since), until: toIso(until) } });
  }

  return (
    <form onSubmit={submit} className="card-sm flex flex-wrap items-end gap-3">
      <Filter className="w-4 h-4 text-slate-500 mb-2" aria-hidden />
      <label className="flex flex-col gap-1 text-[11px] text-slate-400">
        Service
        <select className="select" value={service} onChange={(e) => setService(e.target.value as AuditService)}>
          <option value="surveillance">Surveillance</option>
          <option value="parking">Parking</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-slate-400">
        Action
        <input
          className="input w-44"
          placeholder="camera.* or auth.login.failure"
          value={action}
          maxLength={64}
          onChange={(e) => setAction(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-slate-400">
        Actor
        <input className="input w-36" placeholder="username" value={actor} maxLength={160} onChange={(e) => setActor(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-slate-400">
        From
        <input className="input" type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-slate-400">
        To
        <input className="input" type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
      </label>
      <button type="submit" className="btn-primary flex items-center gap-1.5">
        <Search className="w-3.5 h-3.5" /> Apply
      </button>
    </form>
  );
}
