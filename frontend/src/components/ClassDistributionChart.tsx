"use client";

import { Tooltip, ResponsiveContainer, Cell, PieChart, Pie } from "recharts";
import { type ClassDistribution } from "@/lib/api";

interface Props { data: ClassDistribution[] }

const COLORS = ["#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444", "#06b6d4", "#ec4899", "#14b8a6"];

export default function ClassDistributionChart({ data }: Props) {
  const total = data.reduce((s, d) => s + d.count, 0);
  const chartData = data.slice(0, 8).map((d, i) => ({
    name: d.class_label,
    value: d.count,
    pct: total > 0 ? ((d.count / total) * 100).toFixed(1) : "0",
    fill: COLORS[i % COLORS.length],
  }));

  return (
    <div className="card h-full">
      <div className="card-header">Object Classes</div>
      {data.length === 0 ? (
        <div className="h-48 flex items-center justify-center text-slate-600 text-sm">No data</div>
      ) : (
        <>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={chartData}
                  cx="50%"
                  cy="50%"
                  innerRadius={52}
                  outerRadius={74}
                  paddingAngle={3}
                  dataKey="value"
                  strokeWidth={0}
                >
                  {chartData.map((entry, i) => (
                    <Cell key={i} fill={entry.fill} opacity={0.85} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ backgroundColor: "#0f172a", border: "1px solid rgba(148,163,184,0.1)", borderRadius: "12px", fontSize: "12px" }}
                  formatter={(v: number, n: string) => [`${v.toLocaleString()} (${chartData.find(d => d.name === n)?.pct}%)`, n]}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 space-y-2">
            {chartData.slice(0, 5).map((d) => (
              <div key={d.name} className="flex items-center justify-between text-xs group">
                <div className="flex items-center gap-2.5">
                  <span className="w-2.5 h-2.5 rounded" style={{ backgroundColor: d.fill }} />
                  <span className="capitalize text-slate-300 group-hover:text-slate-100 transition-colors">{d.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-slate-500 font-medium tabular-nums">{d.value.toLocaleString()}</span>
                  <span className="text-slate-600 tabular-nums text-[10px] w-10 text-right">{d.pct}%</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
