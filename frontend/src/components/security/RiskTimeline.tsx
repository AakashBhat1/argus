"use client";

import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export interface RiskSample {
  t: number; // epoch ms
  [cameraName: string]: number;
}

interface Props {
  data: RiskSample[];
  cameras: string[];
}

const PALETTE = ["#60a5fa", "#c084fc", "#34d399", "#f472b6", "#fbbf24", "#22d3ee"];

export default function RiskTimeline({ data, cameras }: Props) {
  return (
    <div className="card h-full flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <div className="card-header mb-0">Risk Timeline</div>
        <span className="text-[10px] text-slate-500">max score per camera · last 3 min</span>
      </div>
      <div className="flex-1 min-h-[180px]">
        {data.length < 2 ? (
          <div className="h-full flex items-center justify-center text-[11px] text-slate-600">Waiting for live detections…</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
              <defs>
                {cameras.map((c, i) => (
                  <linearGradient key={c} id={`risk-${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={0.35} />
                    <stop offset="100%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v) => new Date(v).toLocaleTimeString([], { minute: "2-digit", second: "2-digit" })}
                stroke="#475569"
                fontSize={10}
                tickLine={false}
              />
              <YAxis domain={[0, 100]} stroke="#475569" fontSize={10} tickLine={false} axisLine={false} />
              <Tooltip
                contentStyle={{ background: "#0f172a", border: "1px solid #334155", borderRadius: 8, fontSize: 11 }}
                labelFormatter={(v) => new Date(Number(v)).toLocaleTimeString()}
              />
              <ReferenceLine y={25} stroke="#facc15" strokeOpacity={0.35} strokeDasharray="4 4" />
              <ReferenceLine y={50} stroke="#fb923c" strokeOpacity={0.45} strokeDasharray="4 4" />
              <ReferenceLine y={75} stroke="#f87171" strokeOpacity={0.55} strokeDasharray="4 4" />
              {cameras.map((c, i) => (
                <Area
                  key={c}
                  type="monotone"
                  dataKey={c}
                  stroke={PALETTE[i % PALETTE.length]}
                  fill={`url(#risk-${i})`}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
      <div className="mt-2 flex items-center gap-3 text-[10px] text-slate-500">
        <span className="flex items-center gap-1"><span className="w-2 h-0.5 bg-yellow-400/60" />suspicious 25</span>
        <span className="flex items-center gap-1"><span className="w-2 h-0.5 bg-orange-400/70" />alert 50</span>
        <span className="flex items-center gap-1"><span className="w-2 h-0.5 bg-red-400/80" />critical 75</span>
      </div>
    </div>
  );
}
