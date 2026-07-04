"use client";

import { useEffect, useState } from "react";
import { Cpu, Zap, Layers, Timer, AlertCircle, RefreshCw } from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { api, type InferenceMetrics, type LatencyStats, type ModelInfo } from "@/lib/api";
import { latencyColor, cn } from "@/lib/utils";

function MetricCard({ label, value, unit = "", color = "" }: { label: string; value: string | number; unit?: string; color?: string }) {
  return (
    <div className="bg-slate-800/40 rounded-xl p-3.5 border border-slate-700/30 transition-all duration-200 hover:border-slate-600/40 hover:bg-slate-800/50">
      <p className="text-[9px] text-slate-500 uppercase tracking-[0.14em] font-semibold mb-1.5">{label}</p>
      <p className={cn("text-xl font-bold tabular-nums tracking-tight", color || "text-slate-100")}>
        {value}<span className="text-[10px] ml-1 text-slate-500 font-normal">{unit}</span>
      </p>
    </div>
  );
}

interface MetricsHistoryPoint {
  t: string;
  fps: number;
  latency: number;
  queue: number;
}

function LatencyRow({ label, stats }: { label: string; stats: LatencyStats }) {
  if (!stats || stats.count === 0) return null;
  return (
    <tr className="tr-hover">
      <td className="py-3 pr-4 text-slate-300 text-xs font-medium capitalize">{label}</td>
      <td className="py-3 pr-4">
        <span className={cn("latency-label", latencyColor(stats.avg))}>{stats.avg} ms</span>
      </td>
      <td className="py-3 pr-4 text-slate-500 text-xs tabular-nums">{stats.p50} ms</td>
      <td className="py-3 pr-4 text-slate-500 text-xs tabular-nums">{stats.p95} ms</td>
      <td className="py-3 text-slate-600 text-xs tabular-nums">{stats.min} / {stats.max} ms</td>
    </tr>
  );
}

/** The API reports the model as a filesystem path with backend-specific
 *  suffixes (e.g. "models/yolo11n_openvino_model/yolo11n.xml"); show only
 *  the clean model name in the UI. */
function displayModelName(modelPath: string): string {
  const base = modelPath.split(/[\\/]/).pop() ?? modelPath;
  return base.replace(/\.(xml|onnx|pt|bin)$/i, "").replace(/_openvino(_model)?$/i, "");
}

const tooltipStyle = {
  backgroundColor: "#0f172a",
  border: "1px solid rgba(148,163,184,0.1)",
  borderRadius: "12px",
  fontSize: "12px",
  boxShadow: "0 20px 40px rgba(0,0,0,0.6)",
};

export default function LiveMetricsPage() {
  const [metrics, setMetrics] = useState<InferenceMetrics | null>(null);
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [history, setHistory] = useState<MetricsHistoryPoint[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadMetrics();
    const id = setInterval(loadMetrics, 3000);
    return () => clearInterval(id);
  }, []);

  async function loadMetrics() {
    try {
      const [m, mo] = await Promise.all([api.metrics.get(), api.metrics.model()]);
      setMetrics(m);
      setModel(mo);
      setHistory((prev) => {
        const next = [...prev, {
          t: new Date().toLocaleTimeString("en", { hour12: false }),
          fps: m.throughput.global_fps,
          latency: m.latency.inference.avg,
          queue: m.queue.depth,
        }];
        return next.slice(-40);
      });
      setError(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load metrics";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  if (error) return (
    <div className="flex flex-col items-center justify-center h-64 gap-4 animate-fade-in">
      <div className="w-14 h-14 rounded-2xl bg-red-500/[0.06] flex items-center justify-center">
        <AlertCircle className="w-7 h-7 text-red-500/50" />
      </div>
      <p className="text-sm text-slate-400">Failed to fetch inference metrics</p>
      <p className="text-xs text-slate-600">{error}</p>
      <button onClick={loadMetrics} className="btn-primary"><RefreshCw className="w-4 h-4" /> Retry</button>
    </div>
  );

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3 mb-0.5">
            <h1 className="page-title"><span className="gradient-text-static">Inference</span></h1>
          </div>
          <p className="page-subtitle">Real-time pipeline observability &mdash; refreshes every 3s</p>
        </div>
        {model && (
          <div className="hidden md:flex flex-col items-end gap-1">
            <span className="text-xs font-semibold text-violet-400">{model.device_actual}</span>
            <span className="text-[10px] text-slate-600 font-mono">{displayModelName(model.model)}</span>
          </div>
        )}
      </div>

      {/* Stat Row */}
      {metrics && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger-children">
          <div className="card flex flex-col gap-1">
            <div className="w-10 h-10 rounded-xl bg-blue-500/10 flex items-center justify-center mb-2">
              <Zap className="w-5 h-5 text-blue-400" />
            </div>
            <p className="text-[10px] text-slate-500 uppercase tracking-[0.12em] font-semibold">Throughput</p>
            <p className="text-3xl font-bold text-slate-50 tabular-nums">{metrics.throughput.global_fps}</p>
            <p className="text-[11px] text-slate-600">global FPS</p>
          </div>
          <div className="card flex flex-col gap-1">
            <div className="w-10 h-10 rounded-xl bg-emerald-500/10 flex items-center justify-center mb-2">
              <Timer className="w-5 h-5 text-emerald-400" />
            </div>
            <p className="text-[10px] text-slate-500 uppercase tracking-[0.12em] font-semibold">Avg Latency</p>
            <p className={cn("text-3xl font-bold tabular-nums", latencyColor(metrics.latency.inference.avg).split(" ")[0])}>
              {metrics.latency.inference.avg}
            </p>
            <p className="text-[11px] text-slate-600">ms avg &middot; P95 {metrics.latency.inference.p95} ms</p>
          </div>
          <div className="card flex flex-col gap-1">
            <div className="w-10 h-10 rounded-xl bg-amber-500/10 flex items-center justify-center mb-2">
              <Layers className="w-5 h-5 text-amber-400" />
            </div>
            <p className="text-[10px] text-slate-500 uppercase tracking-[0.12em] font-semibold">Queue Depth</p>
            <p className={cn("text-3xl font-bold tabular-nums", metrics.queue.depth > 20 ? "text-red-400" : "text-slate-50")}>
              {metrics.queue.depth}
            </p>
            <p className="text-[11px] text-slate-600">{metrics.queue.frames_dropped} frames dropped</p>
          </div>
          <div className="card flex flex-col gap-1">
            <div className="w-10 h-10 rounded-xl bg-violet-500/10 flex items-center justify-center mb-2">
              <Cpu className="w-5 h-5 text-violet-400" />
            </div>
            <p className="text-[10px] text-slate-500 uppercase tracking-[0.12em] font-semibold">Frames</p>
            <p className="text-3xl font-bold text-slate-50 tabular-nums">{metrics.throughput.total_frames_processed.toLocaleString()}</p>
            <p className="text-[11px] text-slate-600">total processed</p>
          </div>
        </div>
      )}

      {/* Live Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="card">
          <div className="card-header">Throughput &middot; FPS (Live)</div>
          <div className="h-56">
            {history.length < 2 ? (
              <div className="h-full flex items-center justify-center text-slate-600 text-sm">Collecting...</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={history} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 6" stroke="rgba(148,163,184,0.05)" vertical={false} />
                  <XAxis dataKey="t" stroke="#334155" fontSize={9} tickLine={false} axisLine={false} interval="preserveStartEnd" dy={8} />
                  <YAxis stroke="#334155" fontSize={9} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#94a3b8" }} />
                  <Line type="monotone" dataKey="fps" name="FPS" stroke="#3b82f6" strokeWidth={2} dot={false} activeDot={{ r: 3, fill: "#3b82f6", stroke: "#1e3a5f", strokeWidth: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-header">Inference Latency &middot; ms (Live)</div>
          <div className="h-56">
            {history.length < 2 ? (
              <div className="h-full flex items-center justify-center text-slate-600 text-sm">Collecting...</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={history} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 6" stroke="rgba(148,163,184,0.05)" vertical={false} />
                  <XAxis dataKey="t" stroke="#334155" fontSize={9} tickLine={false} axisLine={false} interval="preserveStartEnd" dy={8} />
                  <YAxis stroke="#334155" fontSize={9} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#94a3b8" }} />
                  <Line type="monotone" dataKey="latency" name="Latency ms" stroke="#10b981" strokeWidth={2} dot={false} activeDot={{ r: 3, fill: "#10b981", stroke: "#064e3b", strokeWidth: 2 }} />
                  <Line type="monotone" dataKey="queue" name="Queue Depth" stroke="#f59e0b" strokeWidth={2} dot={false} activeDot={{ r: 3 }} strokeDasharray="4 2" />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>

      {/* Latency Breakdown Table */}
      {metrics && (
        <div className="card">
          <div className="card-header">Latency Breakdown (last 60s window)</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  {["Stage", "Avg", "P50", "P95", "Min / Max"].map((h) => (
                    <th key={h} className="table-header pr-4">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <LatencyRow label="Inference" stats={metrics.latency.inference} />
                <LatencyRow label="Batch" stats={metrics.latency.batch} />
                <LatencyRow label="Preprocess" stats={metrics.latency.preprocess} />
                <LatencyRow label="Postprocess" stats={metrics.latency.postprocess} />
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Per-Camera FPS */}
      {metrics && Object.keys(metrics.throughput.per_camera_fps).length > 0 && (
        <div className="card">
          <div className="card-header">Per-Camera FPS</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
            {Object.entries(metrics.throughput.per_camera_fps).map(([cam, fps]) => (
              <div key={cam} className="bg-slate-800/40 rounded-xl p-3 border border-slate-700/30 text-center transition-colors hover:border-slate-600/40">
                <p className="text-[10px] text-slate-500 truncate font-mono">{cam.slice(0, 8)}</p>
                <p className="text-xl font-bold text-blue-400 tabular-nums mt-1">{fps}</p>
                <p className="text-[9px] text-slate-600 uppercase tracking-wider font-semibold">fps</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Model Info */}
      {model && (
        <div className="card">
          <div className="card-header">Model & Device Info</div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            {[
              { label: "Device", value: model.device_actual },
              { label: "Precision", value: model.precision || "FP16" },
              { label: "Classes", value: model.num_classes },
              { label: "Input Size", value: model.input_size?.join("\u00d7") },
              { label: "Conf. Threshold", value: `${(model.confidence_threshold * 100).toFixed(0)}%` },
              { label: "Total Inferences", value: model.total_inferences?.toLocaleString() },
            ].map(({ label, value }) => (
              <MetricCard key={label} label={label} value={value ?? "\u2014"} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
