"use client";

import { useEffect, useState } from "react";
import { Bell, AlertOctagon, AlertTriangle, Info, CheckCircle } from "lucide-react";
import { api, type Alert } from "@/lib/api";
import { cn, severityColor, formatTimestamp } from "@/lib/utils";

const icons: Record<string, typeof AlertTriangle> = {
  critical: AlertOctagon, high: AlertTriangle, medium: Bell, low: Info,
};

const severityBar: Record<string, string> = {
  critical: "severity-bar-critical",
  high: "severity-bar-high",
  medium: "severity-bar-medium",
  low: "severity-bar-low",
};

export default function RecentAlerts() {
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    const load = async () => {
      try {
        const data = await api.alerts.list({ limit: "8", status: "active" });
        setAlerts(data);
      } catch {}
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  async function ack(id: string) {
    await api.alerts.acknowledge(id);
    setAlerts((p) => p.filter((a) => a.id !== id));
  }

  return (
    <div className="card h-full flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <div className="card-header mb-0">Active Alerts</div>
        {alerts.length > 0 && (
          <span className="badge bg-red-500/10 text-red-400 border-red-500/20">
            {alerts.length} active
          </span>
        )}
      </div>

      <div className="flex-1 space-y-2 overflow-auto">
        {alerts.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-slate-600">
            <div className="w-12 h-12 rounded-xl bg-slate-800/50 flex items-center justify-center mb-3">
              <Bell className="w-6 h-6 opacity-30" />
            </div>
            <p className="text-xs font-medium text-slate-500">No active alerts</p>
            <p className="text-[10px] text-slate-600 mt-0.5">System is operating normally</p>
          </div>
        ) : (
          alerts.map((a) => {
            const Icon = icons[a.severity] || Info;
            return (
              <div
                key={a.id}
                className={cn(
                  "flex items-start gap-3 p-3 bg-slate-800/30 rounded-xl border border-slate-700/30 group transition-all duration-200 hover:bg-slate-800/50",
                  severityBar[a.severity]
                )}
              >
                <div className={cn("w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 border", severityColor(a.severity))}>
                  <Icon className="w-3.5 h-3.5" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-slate-200 capitalize">{a.type.replace(/_/g," ")}</p>
                  <p className="text-[10px] text-slate-500 mt-0.5 truncate">{a.description}</p>
                  <p className="text-[10px] text-slate-600 mt-0.5">{formatTimestamp(a.timestamp)}</p>
                </div>
                <button
                  onClick={() => ack(a.id)}
                  className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg text-emerald-400 hover:bg-emerald-500/10 transition-all"
                  aria-label="Acknowledge alert"
                >
                  <CheckCircle className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
