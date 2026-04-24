"use client";

import { useEffect, useState } from "react";
import { Activity, Gauge, Wifi, Cpu } from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar,
} from "recharts";
import { api, type PerformanceData, type StreamStatus } from "@/lib/api";
import StatsCard from "@/components/StatsCard";

const tooltipStyle = {
  backgroundColor: "#0f172a",
  border: "1px solid rgba(148,163,184,0.1)",
  borderRadius: "12px",
  fontSize: "12px",
  boxShadow: "0 20px 40px rgba(0,0,0,0.6)",
};

export default function PerformancePage() {
  const [perf, setPerf] = useState<PerformanceData | null>(null);
  const [streams, setStreams] = useState<StreamStatus[]>([]);
  const [history, setHistory] = useState<{ time: string; dpm: number; conf: number }[]>([]);

  useEffect(() => {
    loadData();
    const id = setInterval(loadData, 5000);
    return () => clearInterval(id);
  }, []);

  async function loadData() {
    try {
      const [p, s] = await Promise.all([api.analytics.performance(), api.streams.status()]);
      setPerf(p);
      setStreams(s.streams || []);
      setHistory((prev) => {
        const next = [...prev, { time: new Date().toLocaleTimeString("en", { hour12: false }), dpm: p.detections_per_minute, conf: Math.round(p.avg_confidence * 100) }];
        return next.slice(-30);
      });
    } catch {}
  }

  const running = streams.filter((stream) => stream.is_running);
  const avgFps = running.length > 0
    ? running.reduce((sum, stream) => sum + (stream.fps || 0), 0) / running.length
    : 0;

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="page-title"><span className="gradient-text-static">Performance</span></h1>
        <p className="page-subtitle">Stream and detection pipeline metrics</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger-children">
        <StatsCard title="Active Streams" value={running.length} subtitle={`of ${streams.length} total`} icon={Wifi} color="blue" />
        <StatsCard title="Avg FPS" value={avgFps.toFixed(1)} subtitle="across running streams" icon={Gauge} color="green" />
        <StatsCard title="Detections/min" value={perf?.detections_per_minute ?? 0} subtitle="current rate" icon={Activity} color="purple" />
        <StatsCard title="Avg Confidence" value={`${((perf?.avg_confidence ?? 0) * 100).toFixed(1)}%`} subtitle="model accuracy" icon={Cpu} color="teal" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="card">
          <div className="card-header">Detection Rate (Live)</div>
          <div className="h-64">
            {history.length < 2 ? (
              <div className="h-full flex items-center justify-center text-slate-600 text-sm">Collecting data...</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={history} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 6" stroke="rgba(148,163,184,0.05)" vertical={false} />
                  <XAxis dataKey="time" stroke="#334155" fontSize={10} tickLine={false} axisLine={false} dy={8} />
                  <YAxis stroke="#334155" fontSize={10} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#94a3b8", fontWeight: 600 }} />
                  <Line type="monotone" dataKey="dpm" name="Detect/min" stroke="#3b82f6" strokeWidth={2} dot={false} activeDot={{ r: 4, fill: "#3b82f6", stroke: "#1e3a5f", strokeWidth: 2 }} />
                  <Line type="monotone" dataKey="conf" name="Confidence%" stroke="#10b981" strokeWidth={2} dot={false} activeDot={{ r: 4, fill: "#10b981", stroke: "#064e3b", strokeWidth: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-header">Stream FPS Breakdown</div>
          {streams.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-slate-600 text-sm">No active streams</div>
          ) : (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={streams.map((stream) => ({ name: stream.camera_name?.split(" ")[0] || stream.camera_id.slice(0, 6), fps: stream.fps, tracks: stream.active_tracks }))}
                  margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 6" stroke="rgba(148,163,184,0.05)" vertical={false} />
                  <XAxis dataKey="name" stroke="#334155" fontSize={10} tickLine={false} axisLine={false} dy={8} />
                  <YAxis stroke="#334155" fontSize={10} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar dataKey="fps" name="FPS" fill="#3b82f6" radius={[6, 6, 0, 0]} />
                  <Bar dataKey="tracks" name="Tracks" fill="#8b5cf6" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-header">Stream Details</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                {["Camera", "Status", "FPS", "Frames", "Tracks", "Frame Skip", "Uptime"].map((h) => (
                  <th key={h} className="table-header pr-4">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {streams.map((stream) => (
                <tr key={stream.camera_id} className="tr-hover">
                  <td className="py-3 pr-4 text-slate-200 font-medium">{stream.camera_name}</td>
                  <td className="py-3 pr-4">
                    <span className={`inline-flex items-center gap-1.5 text-xs font-medium`}>
                      <span className={stream.is_running ? "status-led-active" : "status-led-inactive"} />
                      <span className={stream.is_running ? "text-emerald-400" : "text-slate-500"}>
                        {stream.is_running ? "Running" : "Stopped"}
                      </span>
                    </span>
                  </td>
                  <td className="py-3 pr-4 text-blue-400 font-bold tabular-nums">{stream.fps}</td>
                  <td className="py-3 pr-4 text-slate-400 tabular-nums">{stream.frame_count?.toLocaleString()}</td>
                  <td className="py-3 pr-4 text-violet-400 font-bold tabular-nums">{stream.active_tracks}</td>
                  <td className="py-3 pr-4 text-amber-400 font-bold tabular-nums">&divide;{stream.current_frame_skip ?? 2}</td>
                  <td className="py-3 text-slate-500 tabular-nums">
                    {stream.uptime_seconds > 0 ? `${Math.floor(stream.uptime_seconds / 60)}m ${Math.floor(stream.uptime_seconds % 60)}s` : "-"}
                  </td>
                </tr>
              ))}
              {streams.length === 0 && (
                <tr><td colSpan={7} className="py-12 text-center text-slate-600 text-sm">No streams to display</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
