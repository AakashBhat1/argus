"use client";

import type { AuditEvent } from "@/lib/audit";
import { formatTimestamp } from "@/lib/utils";

interface Props {
  items: AuditEvent[];
}

function actionTone(action: string): string {
  if (action.includes("failure") || action.includes("lockout") || action.includes("reuse") || action.includes("refused")) {
    return "text-red-300 bg-red-500/10 border-red-500/20";
  }
  if (action.endsWith(".delete") || action.includes("revoke") || action.includes("release")) {
    return "text-amber-300 bg-amber-500/10 border-amber-500/20";
  }
  return "text-slate-300 bg-slate-500/10 border-slate-500/20";
}

export default function AuditTable({ items }: Props) {
  if (items.length === 0) {
    return <p className="text-sm text-slate-500 py-8 text-center">No audit events match these filters.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-slate-500 border-b border-slate-800/60">
            <th className="py-2 pr-3 font-medium">Time</th>
            <th className="py-2 pr-3 font-medium">Actor</th>
            <th className="py-2 pr-3 font-medium">Action</th>
            <th className="py-2 pr-3 font-medium">Target</th>
            <th className="py-2 pr-3 font-medium">Source IP</th>
            <th className="py-2 font-medium">Details</th>
          </tr>
        </thead>
        <tbody>
          {items.map((event) => (
            <tr key={event.id} className="border-b border-slate-800/30 align-top">
              <td className="py-2 pr-3 whitespace-nowrap text-slate-400">{formatTimestamp(event.created_at)}</td>
              <td className="py-2 pr-3 text-slate-200 break-all">{event.actor}</td>
              <td className="py-2 pr-3">
                <span className={`badge border ${actionTone(event.action)}`}>{event.action}</span>
              </td>
              <td className="py-2 pr-3 text-slate-400 break-all">
                {event.target_type ? `${event.target_type}:` : ""}
                {event.target_id ?? "—"}
              </td>
              <td className="py-2 pr-3 text-slate-500">{event.source_ip ?? "—"}</td>
              <td className="py-2">
                {event.details ? (
                  <details>
                    <summary className="cursor-pointer text-slate-400 hover:text-slate-200">view</summary>
                    {/* Rendered as text: details are data, never markup. */}
                    <pre className="mt-1 max-w-md whitespace-pre-wrap break-all text-[10px] text-slate-400">
                      {JSON.stringify(event.details, null, 2)}
                    </pre>
                  </details>
                ) : (
                  <span className="text-slate-600">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
