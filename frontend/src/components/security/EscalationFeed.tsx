"use client";

import { AlertOctagon, AlertTriangle, Car, Ruler, UserX, Users } from "lucide-react";
import { cn, riskBadge, formatTimestamp } from "@/lib/utils";

export interface Escalation {
  key: string;
  alert_id?: string;
  camera_id: string;
  camera_name?: string;
  type: string;
  severity: string;
  object_id?: number;
  risk_score?: number;
  risk_level?: string;
  reasons?: string[];
  zone_names?: string[];
  distance_m?: number | null;
  timestamp: string;
}

interface Props {
  items: Escalation[];
}

function typeLabel(type: string): string {
  switch (type) {
    case "intrusion_detected": return "Intrusion";
    case "suspicious_activity": return "Suspicious activity";
    case "blacklisted_vehicle_contact": return "Blacklisted vehicle";
    case "crime_detected": return "Crime classifier";
    case "crowd_detected": return "Crowd";
    default: return type.replace(/_/g, " ");
  }
}

export default function EscalationFeed({ items }: Props) {
  return (
    <div className="card h-full flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <div className="card-header mb-0">Escalations</div>
        <span className="text-[10px] text-slate-500">live · alert &amp; critical only</span>
      </div>
      <div className="flex-1 space-y-2 overflow-auto">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-slate-600">
            <Users className="w-6 h-6 opacity-30 mb-2" />
            <p className="text-[11px]">Nothing needs attention</p>
            <p className="text-[10px] text-slate-700 mt-0.5">Presence, passers-by and authorized people never appear here</p>
          </div>
        ) : (
          items.map((e) => {
            const critical = e.severity === "critical" || e.risk_level === "critical";
            const Icon = e.type === "blacklisted_vehicle_contact" ? Car : critical ? AlertOctagon : e.type === "suspicious_activity" ? UserX : AlertTriangle;
            return (
              <div key={e.key} className={cn("flex items-start gap-3 p-3 rounded-xl border bg-slate-800/30", critical ? "border-red-500/30" : "border-orange-500/25")}>
                <div className={cn("w-8 h-8 rounded-lg flex items-center justify-center shrink-0 border", riskBadge(critical ? "critical" : "alert"))}>
                  <Icon className="w-4 h-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-xs font-semibold text-slate-100">{typeLabel(e.type)}</p>
                    {e.object_id != null && <span className="text-[10px] text-slate-500">#{e.object_id}</span>}
                    {e.risk_score != null && (
                      <span className={cn("badge border px-1.5 py-0 text-[9px] tabular-nums", riskBadge(e.risk_level))}>{e.risk_score.toFixed(0)}</span>
                    )}
                    {e.zone_names && e.zone_names.length > 0 && <span className="text-[10px] text-slate-400">in {e.zone_names.join(", ")}</span>}
                    {e.distance_m != null && <span className="text-[10px] text-slate-500 flex items-center gap-0.5"><Ruler className="w-3 h-3" />{e.distance_m.toFixed(0)} m</span>}
                  </div>
                  {e.reasons && e.reasons.length > 0 && (
                    <p className="text-[10px] text-slate-400 mt-0.5 leading-snug">{e.reasons.join(" · ")}</p>
                  )}
                  <p className="text-[10px] text-slate-600 mt-0.5">{e.camera_name || e.camera_id} · {formatTimestamp(e.timestamp)}</p>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
