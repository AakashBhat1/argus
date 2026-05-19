import { formatDistanceToNow } from "date-fns";
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatTimestamp(ts: string): string {
  try {
    const hasTz = /([zZ]|[+-]\d{2}:?\d{2})$/.test(ts);
    const d = new Date(hasTz ? ts : ts + "Z");
    return formatDistanceToNow(d, { addSuffix: true });
  } catch {
    return ts;
  }
}

export function statusColor(status: string): string {
  switch (status?.toLowerCase()) {
    case "active":   return "text-emerald-400";
    case "inactive": return "text-slate-500";
    case "error":    return "text-red-400";
    default:         return "text-slate-400";
  }
}

export function severityColor(severity: string): string {
  switch (severity?.toLowerCase()) {
    case "critical": return "bg-red-500/10 text-red-400 border-red-500/30";
    case "high":     return "bg-orange-500/10 text-orange-400 border-orange-500/30";
    case "medium":   return "bg-amber-500/10 text-amber-400 border-amber-500/30";
    case "low":      return "bg-blue-500/10 text-blue-400 border-blue-500/30";
    default:         return "bg-slate-700/50 text-slate-400 border-slate-600/50";
  }
}

export function latencyColor(ms: number): string {
  if (ms < 30)  return "text-emerald-400 bg-emerald-500/10";
  if (ms < 80)  return "text-blue-400 bg-blue-500/10";
  if (ms < 150) return "text-amber-400 bg-amber-500/10";
  return "text-red-400 bg-red-500/10";
}
