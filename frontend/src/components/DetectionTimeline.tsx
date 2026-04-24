"use client";

import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { type TimelinePoint } from "@/lib/api";
import { format } from "date-fns";

interface Props { data: TimelinePoint[] }

export default function DetectionTimeline({ data }: Props) {
  const formatted = data.map((d) => ({
    ...d,
    label: (() => { try { return format(new Date(d.timestamp), "HH:mm"); } catch { return d.timestamp; } })(),
  }));

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-5">
        <div className="card-header mb-0">Detection Timeline</div>
        <div className="flex items-center gap-4 text-[11px] text-slate-500">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-0.5 rounded-full bg-blue-500" />
            Total
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-0.5 rounded-full bg-violet-500" />
            Unique
          </span>
        </div>
      </div>
      <div className="h-64">
        {data.length === 0 ? (
          <div className="h-full flex items-center justify-center text-slate-600 text-sm">
            No detection data for this period
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={formatted} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
              <defs>
                <linearGradient id="gTotal" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%"  stopColor="#3b82f6" stopOpacity={0.2} />
                  <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gUnique" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%"  stopColor="#8b5cf6" stopOpacity={0.15} />
                  <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 6" stroke="rgba(148,163,184,0.05)" vertical={false} />
              <XAxis dataKey="label" stroke="#334155" fontSize={10} tickLine={false} axisLine={false} dy={8} />
              <YAxis stroke="#334155" fontSize={10} tickLine={false} axisLine={false} />
              <Tooltip
                contentStyle={{ backgroundColor: "#0f172a", border: "1px solid rgba(148,163,184,0.1)", borderRadius: "12px", fontSize: "12px", boxShadow: "0 20px 40px rgba(0,0,0,0.6)" }}
                labelStyle={{ color: "#94a3b8", fontWeight: 600, marginBottom: "4px" }}
              />
              <Area type="monotone" dataKey="count" name="Total" stroke="#3b82f6" strokeWidth={2} fill="url(#gTotal)" dot={false} activeDot={{ r: 4, fill: "#3b82f6", stroke: "#1e3a5f", strokeWidth: 2 }} />
              <Area type="monotone" dataKey="unique_objects" name="Unique Objects" stroke="#8b5cf6" strokeWidth={2} fill="url(#gUnique)" dot={false} activeDot={{ r: 4, fill: "#8b5cf6", stroke: "#3b1e5f", strokeWidth: 2 }} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
