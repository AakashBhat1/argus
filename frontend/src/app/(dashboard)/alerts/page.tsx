"use client";

import { useEffect, useState } from "react";
import {
  Bell,
  CheckCircle,
  XCircle,
  AlertTriangle,
  AlertOctagon,
  Info,
  Filter,
  Loader2,
} from "lucide-react";
import { api, type Alert } from "@/lib/api";
import { useWebSocket } from "@/lib/websocket";
import { cn, severityColor, formatTimestamp } from "@/lib/utils";

const severityIcons: Record<string, typeof AlertTriangle> = {
  critical: AlertOctagon,
  high: AlertTriangle,
  medium: Bell,
  low: Info,
};

const severityBar: Record<string, string> = {
  critical: "severity-bar-critical",
  high: "severity-bar-high",
  medium: "severity-bar-medium",
  low: "severity-bar-low",
};

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [filter, setFilter] = useState({ status: "", severity: "" });
  const [stats, setStats] = useState<{
    active_count: number;
    by_severity: Record<string, number>;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { lastMessage } = useWebSocket("alerts");

  useEffect(() => {
    loadAlerts();
    loadStats();
  }, [filter]);

  useEffect(() => {
    if (lastMessage?.type === "alert") {
      loadAlerts();
      loadStats();
    }
  }, [lastMessage]);

  async function loadAlerts() {
    try {
      setError(null);
      const params: Record<string, string> = { limit: "50" };
      if (filter.status) params.status = filter.status;
      if (filter.severity) params.severity = filter.severity;
      const data = await api.alerts.list(params);
      setAlerts(data);
    } catch (err) {
      setError("Failed to load alerts");
      console.error("Failed to load alerts:", err);
    } finally {
      setLoading(false);
    }
  }

  async function loadStats() {
    try {
      const data = await api.alerts.stats();
      setStats(data);
    } catch (err) {
      console.error("Failed to load alert stats:", err);
    }
  }

  async function handleAcknowledge(id: string) {
    try {
      await api.alerts.acknowledge(id);
      loadAlerts();
      loadStats();
    } catch (err) {
      console.error(err);
    }
  }

  async function handleResolve(id: string) {
    try {
      await api.alerts.resolve(id);
      loadAlerts();
      loadStats();
    } catch (err) {
      console.error(err);
    }
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title"><span className="gradient-text-static">Alerts</span></h1>
          <p className="page-subtitle">Security alerts and notifications</p>
        </div>
        {stats && (
          <div className="flex items-center gap-3 text-xs">
            <span className="flex items-center gap-1.5 text-red-400 font-semibold bg-red-500/[0.06] border border-red-500/15 rounded-full px-3 py-1">
              <span className="status-led-error" />
              {stats.active_count} Active
            </span>
            {Object.entries(stats.by_severity).map(([sev, count]) => (
              <span key={sev} className={cn("badge", severityColor(sev))}>
                {sev}: {count}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        <Filter className="w-4 h-4 text-slate-500" />
        <select
          value={filter.status}
          onChange={(e) => setFilter({ ...filter, status: e.target.value })}
          className="select"
          aria-label="Filter by status"
        >
          <option value="">All Status</option>
          <option value="active">Active</option>
          <option value="acknowledged">Acknowledged</option>
          <option value="resolved">Resolved</option>
        </select>
        <select
          value={filter.severity}
          onChange={(e) => setFilter({ ...filter, severity: e.target.value })}
          className="select"
          aria-label="Filter by severity"
        >
          <option value="">All Severity</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-20">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="w-6 h-6 text-blue-400/60 animate-spin" />
            <p className="text-xs text-slate-500">Loading alerts...</p>
          </div>
        </div>
      )}

      {error && !loading && (
        <div className="py-20 text-center">
          <div className="w-14 h-14 rounded-2xl bg-red-500/[0.06] flex items-center justify-center mx-auto mb-4">
            <AlertTriangle className="w-7 h-7 text-red-500/50" />
          </div>
          <p className="text-sm text-slate-400">{error}</p>
          <button onClick={loadAlerts} className="btn-primary mt-4 mx-auto">
            Retry
          </button>
        </div>
      )}

      {!loading && !error && (
        <div className="space-y-3">
          {alerts.map((alert) => {
            const Icon = severityIcons[alert.severity] || Info;
            return (
              <div
                key={alert.id}
                className={cn(
                  "card flex items-start gap-4 transition-all duration-200",
                  severityBar[alert.severity],
                  alert.status === "resolved" && "opacity-40"
                )}
              >
                <div
                  className={cn(
                    "w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 border",
                    severityColor(alert.severity)
                  )}
                >
                  <Icon className="w-4 h-4" />
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-semibold text-slate-100">
                      {alert.type.replace(/_/g, " ")}
                    </span>
                    <span className={cn("badge text-[10px]", severityColor(alert.severity))}>
                      {alert.severity}
                    </span>
                    <span className="badge text-[10px] bg-slate-800/80 text-slate-400 border-slate-600/40">
                      {alert.status}
                    </span>
                  </div>
                  {alert.description && (
                    <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">
                      {alert.description}
                    </p>
                  )}
                  {alert.trigger_condition && (
                    <p className="text-[10px] text-slate-500 mt-1 font-mono bg-slate-800/30 rounded px-2 py-0.5 inline-block">
                      {alert.trigger_condition}
                    </p>
                  )}
                  <p className="text-[10px] text-slate-600 mt-1.5">
                    {formatTimestamp(alert.timestamp)}
                    {alert.resolved_at &&
                      ` — Resolved: ${formatTimestamp(alert.resolved_at)}`}
                  </p>
                </div>

                {alert.status === "active" && (
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      onClick={() => handleAcknowledge(alert.id)}
                      className="p-2 rounded-xl text-amber-400 hover:bg-amber-500/10 transition-colors"
                      title="Acknowledge"
                      aria-label="Acknowledge alert"
                    >
                      <CheckCircle className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleResolve(alert.id)}
                      className="p-2 rounded-xl text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                      title="Resolve"
                      aria-label="Resolve alert"
                    >
                      <XCircle className="w-4 h-4" />
                    </button>
                  </div>
                )}
                {alert.status === "acknowledged" && (
                  <button
                    onClick={() => handleResolve(alert.id)}
                    className="flex-shrink-0 p-2 rounded-xl text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                    title="Resolve"
                    aria-label="Resolve alert"
                  >
                    <XCircle className="w-4 h-4" />
                  </button>
                )}
              </div>
            );
          })}

          {alerts.length === 0 && (
            <div className="py-20 text-center">
              <div className="w-14 h-14 rounded-2xl bg-slate-800/40 flex items-center justify-center mx-auto mb-4">
                <Bell className="w-7 h-7 opacity-30 text-slate-500" />
              </div>
              <p className="text-sm font-medium text-slate-400">No alerts found</p>
              <p className="text-xs mt-1.5 text-slate-600">
                Alerts will appear here when triggered
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
