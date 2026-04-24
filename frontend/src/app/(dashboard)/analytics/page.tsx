"use client";

import { useEffect, useState } from "react";
import { BarChart3, TrendingUp, Clock, Target } from "lucide-react";
import {
  api,
  type Camera,
  type TimelinePoint,
  type ClassDistribution,
  type PerformanceData,
} from "@/lib/api";
import StatsCard from "@/components/StatsCard";
import DetectionTimeline from "@/components/DetectionTimeline";
import ClassDistributionChart from "@/components/ClassDistributionChart";

export default function AnalyticsPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedCamera, setSelectedCamera] = useState<string>("");
  const [hours, setHours] = useState(24);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [classDist, setClassDist] = useState<ClassDistribution[]>([]);
  const [performance, setPerformance] = useState<PerformanceData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.cameras.list().then(setCameras).catch(console.error);
  }, []);

  useEffect(() => {
    loadAnalytics();
  }, [selectedCamera, hours]);

  async function loadAnalytics() {
    try {
      setLoading(true);
      const cid = selectedCamera || undefined;
      const [t, c, p] = await Promise.all([
        api.analytics.timeline(hours, cid),
        api.analytics.classDistribution(hours, cid),
        api.analytics.performance(),
      ]);
      setTimeline(t);
      setClassDist(c);
      setPerformance(p);
    } catch (err) {
      console.error("Failed to load analytics:", err);
    } finally {
      setLoading(false);
    }
  }

  const totalDetections = classDist.reduce((sum, c) => sum + c.count, 0);
  const avgConf =
    classDist.length > 0
      ? classDist.reduce((sum, c) => sum + c.avg_confidence * c.count, 0) /
        totalDetections
      : 0;

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title"><span className="gradient-text-static">Analytics</span></h1>
          <p className="page-subtitle">Detection analytics and insights</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={selectedCamera}
            onChange={(e) => setSelectedCamera(e.target.value)}
            className="select"
            aria-label="Select camera"
          >
            <option value="">All Cameras</option>
            {cameras.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <select
            value={hours}
            onChange={(e) => setHours(Number(e.target.value))}
            className="select"
            aria-label="Select time range"
          >
            <option value={1}>Last 1 Hour</option>
            <option value={6}>Last 6 Hours</option>
            <option value={24}>Last 24 Hours</option>
            <option value={72}>Last 3 Days</option>
            <option value={168}>Last 7 Days</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 stagger-children">
        <StatsCard
          title="Total Detections"
          value={totalDetections}
          subtitle={`in last ${hours}h`}
          icon={BarChart3}
          color="blue"
          loading={loading}
        />
        <StatsCard
          title="Object Classes"
          value={classDist.length}
          subtitle="unique types"
          icon={Target}
          color="green"
          loading={loading}
        />
        <StatsCard
          title="Avg Confidence"
          value={`${(avgConf * 100).toFixed(1)}%`}
          subtitle="detection accuracy"
          icon={TrendingUp}
          color="purple"
          loading={loading}
        />
        <StatsCard
          title="Detections/min"
          value={performance?.detections_per_minute ?? 0}
          subtitle="current rate"
          icon={Clock}
          color="teal"
          loading={loading}
        />
      </div>

      <DetectionTimeline data={timeline} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ClassDistributionChart data={classDist} />
        <div className="card">
          <div className="card-header">Detection Details</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-slate-700/50">
                  <th className="table-header">Class</th>
                  <th className="table-header">Count</th>
                  <th className="table-header">Avg Confidence</th>
                  <th className="table-header">% Share</th>
                </tr>
              </thead>
              <tbody>
                {classDist.map((item) => (
                  <tr key={item.class_label} className="tr-hover">
                    <td className="py-3 capitalize text-slate-100 font-medium">
                      {item.class_label}
                    </td>
                    <td className="py-3 text-blue-400 font-semibold tabular-nums">
                      {item.count.toLocaleString()}
                    </td>
                    <td className="py-3 text-slate-300 tabular-nums">
                      {(item.avg_confidence * 100).toFixed(1)}%
                    </td>
                    <td className="py-3">
                      <div className="flex items-center gap-2.5">
                        <div className="flex-1 h-1.5 bg-slate-800/80 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-blue-500 to-blue-400 rounded-full transition-all duration-500"
                            style={{
                              width: `${
                                totalDetections > 0
                                  ? (item.count / totalDetections) * 100
                                  : 0
                              }%`,
                            }}
                          />
                        </div>
                        <span className="text-[10px] text-slate-500 w-10 text-right tabular-nums font-medium">
                          {totalDetections > 0
                            ? ((item.count / totalDetections) * 100).toFixed(1)
                            : 0}%
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {classDist.length === 0 && (
              <div className="py-10 text-center text-slate-500 text-sm">
                No detection data for the selected period
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
