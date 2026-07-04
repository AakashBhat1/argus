"use client";

import { useEffect, useState } from "react";
import { Camera, Eye, AlertTriangle, Zap, Cpu } from "lucide-react";
import {
  api,
  type DashboardStats,
  type TimelinePoint,
  type ClassDistribution,
  type PerformanceData,
  type HealthStatus,
} from "@/lib/api";
import { useWebSocket } from "@/lib/websocket";
import StatsCard from "@/components/StatsCard";
import DetectionTimeline from "@/components/DetectionTimeline";
import ClassDistributionChart from "@/components/ClassDistributionChart";
import LiveFeed from "@/components/LiveFeed";
import RecentAlerts from "@/components/RecentAlerts";

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [classDist, setClassDist] = useState<ClassDistribution[]>([]);
  const [performance, setPerformance] = useState<PerformanceData | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const { lastMessage, isConnected } = useWebSocket("global");

  useEffect(() => {
    loadData();
    const i = setInterval(loadData, 30000);
    return () => clearInterval(i);
  }, []);

  async function loadData() {
    try {
      const [s, t, c, p, h] = await Promise.all([
        api.analytics.dashboard(),
        api.analytics.timeline(24),
        api.analytics.classDistribution(24),
        api.analytics.performance(),
        api.health(),
      ]);
      setStats(s); setTimeline(t); setClassDist(c); setPerformance(p); setHealth(h);
    } catch (err) {
      console.error("Dashboard load failed:", err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">
            <span className="gradient-text-static">Dashboard</span>
          </h1>
          <p className="page-subtitle">Real-time multi-camera overview</p>
        </div>
        <div className="flex items-center gap-2">
          {health && (
            <span className="hidden md:flex items-center gap-2 text-[11px] text-slate-400 bg-slate-800/30 border border-slate-700/20 rounded-full px-3.5 py-1.5 backdrop-blur-sm">
              <Cpu className="w-3.5 h-3.5 text-violet-400" />
              {health.inference_device}
            </span>
          )}
          <div className="flex items-center gap-2 text-[11px] bg-slate-800/30 border border-slate-700/20 rounded-full px-3.5 py-1.5 backdrop-blur-sm">
            <span className={isConnected ? "status-led-active" : "status-led-error"} />
            <span className={isConnected ? "text-emerald-400 font-medium" : "text-red-400"}>{isConnected ? "Live" : "Disconnected"}</span>
          </div>
        </div>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger-children">
        <StatsCard title="Total Cameras" value={stats?.total_cameras ?? 0}
          subtitle={`${stats?.active_cameras ?? 0} streaming`} icon={Camera} color="blue" loading={loading} />
        <StatsCard title="Detections Today" value={stats?.detections_today ?? 0}
          subtitle={`${performance?.detections_per_minute ?? 0}/min`} icon={Eye} color="green" loading={loading} />
        <StatsCard title="Active Alerts" value={stats?.active_alerts ?? 0}
          subtitle="requires attention" icon={AlertTriangle} color="red" loading={loading} />
        <StatsCard title="Avg Confidence"
          value={`${((performance?.avg_confidence ?? 0) * 100).toFixed(1)}%`}
          subtitle="model accuracy" icon={Zap} color="purple" loading={loading} />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2">
          <DetectionTimeline data={timeline} />
        </div>
        <div>
          <ClassDistributionChart data={classDist} />
        </div>
      </div>

      {/* Live Feed + Alerts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2">
          <LiveFeed lastMessage={lastMessage} />
        </div>
        <div>
          <RecentAlerts />
        </div>
      </div>
    </div>
  );
}
