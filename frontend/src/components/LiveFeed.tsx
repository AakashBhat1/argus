"use client";

import { useState, useEffect, useRef } from "react";
import {
  Video, Radio, Pause, Play, Film, Cpu, ChevronDown, Shield, ShieldCheck,
  ShieldAlert, Ruler, UserCheck, Car, Users, Eye, EyeOff, Crosshair,
} from "lucide-react";
import { api, type FeedData, type FeedMessage, type DetectionOverlay } from "@/lib/api";
import { cn, riskHex, riskBadge, zoneTypeHex, armModeBadge } from "@/lib/utils";

interface Props {
  lastMessage: FeedMessage | null;
  /** Optional: only show this camera and hide the selector. */
  cameraId?: string;
  compact?: boolean;
}

const CLASS_COLORS: Record<string, string> = {
  person: "#89b4fa",
  car: "#a6e3a1",
  truck: "#f9e2af",
  bicycle: "#cba6f7",
  motorcycle: "#94e2d5",
  bus: "#fab387",
  dog: "#f38ba8",
  cat: "#74c7ec",
};

function boxColor(det: DetectionOverlay): string {
  if (det.threat) return "#f43f5e";
  if (det.authorized) return "#34d399";
  if (det.risk_level && det.risk_level !== "observe") return riskHex(det.risk_level);
  return CLASS_COLORS[det.class_label] || "#89b4fa";
}

function originLabel(origin?: string): string {
  switch (origin) {
    case "vehicle": return "from vehicle";
    case "entrance": return "via entrance";
    case "perimeter": return "from perimeter";
    case "edge_left": return "from left";
    case "edge_right": return "from right";
    case "edge_top": return "from far side";
    case "edge_bottom": return "from near side";
    case "interior": return "appeared mid-scene";
    default: return origin || "";
  }
}

export default function LiveFeed({ lastMessage, cameraId, compact = false }: Props) {
  const feedsRef = useRef<Record<string, FeedData>>({});
  const [cameraIds, setCameraIds] = useState<string[]>([]);
  const [selectedCamera, setSelectedCamera] = useState<string | null>(cameraId ?? null);
  const [currentFeed, setCurrentFeed] = useState<FeedData | null>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [pauseLoading, setPauseLoading] = useState(false);
  const [showCameraSelect, setShowCameraSelect] = useState(false);
  const [showZones, setShowZones] = useState(true);
  const [showGeometry, setShowGeometry] = useState(true);
  const [grantBusy, setGrantBusy] = useState<number | null>(null);

  useEffect(() => {
    if (lastMessage?.type !== "detections") return;
    const data = lastMessage.data as FeedData;
    const camId = data.camera_id;
    if (cameraId && camId !== cameraId) return;

    feedsRef.current = { ...feedsRef.current, [camId]: data };
    const ids = Object.keys(feedsRef.current);
    setCameraIds(ids);

    if (!selectedCamera) setSelectedCamera(camId);
    if (camId === selectedCamera || (!selectedCamera && ids.length === 1)) {
      setCurrentFeed(data);
      setIsPaused(data.is_paused ?? false);
    }
  }, [lastMessage, selectedCamera, cameraId]);

  function handleSelectCamera(camId: string) {
    setSelectedCamera(camId);
    const feed = feedsRef.current[camId];
    if (feed) {
      setCurrentFeed(feed);
      setIsPaused(feed.is_paused ?? false);
    }
    setShowCameraSelect(false);
  }

  async function handleTogglePause() {
    if (!currentFeed) return;
    setPauseLoading(true);
    try {
      if (isPaused) {
        await api.streams.resume(currentFeed.camera_id);
        setIsPaused(false);
      } else {
        await api.streams.pause(currentFeed.camera_id);
        setIsPaused(true);
      }
    } catch (err) {
      console.error("Failed to toggle pause:", err);
    } finally {
      setPauseLoading(false);
    }
  }

  async function markKnown(det: DetectionOverlay) {
    if (!currentFeed) return;
    setGrantBusy(det.object_id);
    try {
      await api.security.grantTrack({
        camera_id: currentFeed.camera_id,
        track_id: det.object_id,
        label: `Operator-verified #${det.object_id}`,
        minutes: 30,
      });
    } catch (err) {
      console.error("Failed to grant:", err);
    } finally {
      setGrantBusy(null);
    }
  }

  const detections = currentFeed?.detections || [];
  const zones = currentFeed?.zones || [];
  const persons = detections.filter((d) => d.class_label === "person");
  const summary = currentFeed?.risk_summary;
  const W = currentFeed?.frame_width || 1280;
  const H = currentFeed?.frame_height || 720;
  const labelScale = Math.max(1, W / 960);

  return (
    <div className="card">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <div className="card-header mb-0">Live Detection Feed</div>
          {currentFeed?.arm_mode && (
            <span className={cn("badge border text-[10px] uppercase tracking-wider", armModeBadge(currentFeed.arm_mode))}>
              {currentFeed.arm_mode === "armed" ? <ShieldAlert className="w-3 h-3" /> : currentFeed.arm_mode === "disarmed" ? <ShieldCheck className="w-3 h-3" /> : <Shield className="w-3 h-3" />}
              {currentFeed.arm_mode}
            </span>
          )}
          {summary && summary.persons > 0 && (
            <span className={cn("badge border text-[10px] tabular-nums", riskBadge(summary.level))}>
              risk {summary.max_score.toFixed(0)} · {summary.level}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowZones((v) => !v)}
            className={cn("p-1.5 rounded-lg border text-[11px] transition-colors", showZones ? "border-violet-500/30 text-violet-300 bg-violet-500/10" : "border-slate-700/30 text-slate-500")}
            title="Toggle zone overlay"
          >
            <Crosshair className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setShowGeometry((v) => !v)}
            className={cn("p-1.5 rounded-lg border text-[11px] transition-colors", showGeometry ? "border-cyan-500/30 text-cyan-300 bg-cyan-500/10" : "border-slate-700/30 text-slate-500")}
            title="Toggle distance / foot point overlay"
          >
            <Ruler className="w-3.5 h-3.5" />
          </button>

          {!cameraId && cameraIds.length > 1 && (
            <div className="relative">
              <button
                onClick={() => setShowCameraSelect(!showCameraSelect)}
                className="flex items-center gap-1.5 text-[11px] text-slate-400 bg-slate-800/40 border border-slate-700/30 rounded-lg px-2.5 py-1.5 hover:border-slate-600/40 transition-colors"
              >
                {currentFeed?.camera_name || "Select"}
                <ChevronDown className={cn("w-3 h-3 transition-transform", showCameraSelect && "rotate-180")} />
              </button>
              {showCameraSelect && (
                <div className="absolute right-0 top-full mt-1 z-20 w-44 rounded-lg border border-slate-700/40 bg-slate-900/95 backdrop-blur-xl shadow-xl overflow-hidden">
                  {cameraIds.map((id) => {
                    const feed = feedsRef.current[id];
                    return (
                      <button
                        key={id}
                        onClick={() => handleSelectCamera(id)}
                        className={cn(
                          "w-full flex items-center gap-2 px-3 py-2 text-left text-xs transition-colors",
                          id === selectedCamera ? "bg-blue-500/10 text-blue-300" : "text-slate-400 hover:bg-slate-800/60"
                        )}
                      >
                        {feed?.is_video_source ? <Film className="w-3 h-3 text-violet-400" /> : <Radio className="w-3 h-3 text-emerald-400" />}
                        <span className="truncate">{feed?.camera_name || id}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {currentFeed && (
            <div className="flex items-center gap-3 text-xs">
              <span className="flex items-center gap-1.5 text-slate-400">
                <Radio className="w-3 h-3 text-emerald-400 animate-pulse" />
                {(cameraId || cameraIds.length <= 1) && currentFeed.camera_name}
              </span>
              <span className="text-emerald-400 font-semibold tabular-nums">{currentFeed.fps} FPS</span>
              {currentFeed.inference_ms != null && (
                <span className="flex items-center gap-1 text-violet-400 tabular-nums">
                  <Cpu className="w-3 h-3" />
                  {currentFeed.inference_ms}ms
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Video area */}
      <div className="relative bg-slate-950 rounded-xl overflow-hidden aspect-video border border-slate-800/50 scan-line">
        {!currentFeed ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-500">
            <div className="w-16 h-16 rounded-2xl bg-slate-800/50 flex items-center justify-center mb-4">
              <Video className="w-8 h-8 opacity-40" />
            </div>
            <p className="text-sm font-medium text-slate-400">No active stream</p>
            <p className="text-xs mt-1.5 text-slate-600">Start a camera stream to see live detections</p>
          </div>
        ) : (
          <div className="relative w-full h-full text-white">
            {currentFeed.frame_image ? (
              <img
                src={`data:image/jpeg;base64,${currentFeed.frame_image}`}
                alt="Live feed"
                className="absolute inset-0 w-full h-full object-contain"
              />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center bg-slate-900">
                <p className="text-xs text-slate-600">Waiting for frames...</p>
              </div>
            )}

            <svg
              viewBox={`0 0 ${W} ${H}`}
              className="absolute inset-0 w-full h-full pointer-events-none"
              preserveAspectRatio="xMidYMid meet"
            >
              {/* Zones */}
              {showZones && zones.map((zone) => {
                const color = zoneTypeHex(zone.zone_type);
                const pts = zone.points.map((p) => `${p[0] * W},${p[1] * H}`).join(" ");
                const cx = zone.points.reduce((s, p) => s + p[0], 0) / zone.points.length * W;
                const cy = zone.points.reduce((s, p) => s + p[1], 0) / zone.points.length * H;
                return (
                  <g key={zone.zone_id}>
                    <polygon
                      points={pts}
                      fill={color}
                      fillOpacity={zone.armed ? 0.14 : 0.05}
                      stroke={color}
                      strokeWidth={2 * labelScale}
                      strokeDasharray={zone.armed ? undefined : `${8 * labelScale} ${6 * labelScale}`}
                      strokeOpacity={zone.armed ? 0.9 : 0.5}
                    />
                    <text
                      x={cx}
                      y={cy}
                      fill={color}
                      fontSize={12 * labelScale}
                      fontWeight="bold"
                      textAnchor="middle"
                      opacity={0.9}
                      style={{ paintOrder: "stroke", stroke: "#0f172a", strokeWidth: 3 * labelScale }}
                    >
                      {zone.name} · {zone.zone_type}{zone.armed ? "" : " (disarmed)"}
                    </text>
                  </g>
                );
              })}

              {/* Detections */}
              {detections.map((det) => {
                const color = boxColor(det);
                const isPerson = det.class_label === "person";
                const parts: string[] = [`#${det.object_id} ${det.class_label}`];
                if (showGeometry && det.distance_m != null) parts.push(`${det.distance_m.toFixed(1)}m`);
                if (isPerson && det.risk_score != null) parts.push(`R${det.risk_score.toFixed(0)}`);
                if (det.authorized) parts.push("✓");
                const label = parts.join(" · ");
                const fontSize = 11 * labelScale;
                const labelWidth = label.length * fontSize * 0.62 + 10 * labelScale;
                const footX = det.anchor_x ?? det.bbox_x + det.bbox_w / 2;
                const footY = det.anchor_y ?? det.bbox_y + det.bbox_h;
                return (
                  <g key={det.object_id}>
                    <rect
                      x={det.bbox_x}
                      y={det.bbox_y}
                      width={det.bbox_w}
                      height={det.bbox_h}
                      fill={det.intrusion ? color : "none"}
                      fillOpacity={det.intrusion ? 0.12 : 0}
                      stroke={color}
                      strokeWidth={(det.risk_level === "critical" ? 3 : 2) * labelScale}
                      rx="2"
                    />
                    {showGeometry && isPerson && (
                      <>
                        <ellipse cx={footX} cy={footY} rx={det.bbox_w * 0.45} ry={det.bbox_w * 0.16} fill={color} fillOpacity={0.18} stroke={color} strokeOpacity={0.6} strokeWidth={1 * labelScale} />
                        <circle cx={footX} cy={footY} r={3 * labelScale} fill={color} />
                      </>
                    )}
                    {det.close_contacts && det.close_contacts.length > 0 && det.close_contacts.map((other) => {
                      const o = detections.find((d) => d.object_id === other);
                      if (!o || o.object_id < det.object_id) return null;
                      const ox = o.anchor_x ?? o.bbox_x + o.bbox_w / 2;
                      const oy = o.anchor_y ?? o.bbox_y + o.bbox_h;
                      return <line key={other} x1={footX} y1={footY} x2={ox} y2={oy} stroke="#fbbf24" strokeWidth={2 * labelScale} strokeDasharray={`${4 * labelScale} ${4 * labelScale}`} />;
                    })}
                    <rect x={det.bbox_x} y={Math.max(0, det.bbox_y - 20 * labelScale)} width={labelWidth} height={20 * labelScale} fill={color} opacity="0.9" rx="2" />
                    <text x={det.bbox_x + 5 * labelScale} y={Math.max(0, det.bbox_y - 20 * labelScale) + 14 * labelScale} fill="#0b1020" fontSize={fontSize} fontWeight="bold">
                      {label}
                    </text>
                  </g>
                );
              })}
            </svg>

            {isPaused && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/50">
                <div className="flex flex-col items-center gap-2">
                  <Pause className="w-10 h-10 text-white/60" />
                  <span className="text-sm font-semibold text-white/70 uppercase tracking-wider">Paused</span>
                </div>
              </div>
            )}

            {currentFeed.is_video_source && (
              <button
                onClick={handleTogglePause}
                disabled={pauseLoading}
                className={cn(
                  "absolute bottom-3 right-3 flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all duration-200 backdrop-blur-sm",
                  isPaused
                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/30"
                    : "bg-slate-900/70 text-slate-300 border border-slate-700/40 hover:bg-slate-800/80"
                )}
              >
                {isPaused ? <><Play className="w-3.5 h-3.5" /> Resume</> : <><Pause className="w-3.5 h-3.5" /> Pause</>}
              </button>
            )}

            {!isPaused && currentFeed.frame_image && (
              <div className="absolute top-3 left-3 flex items-center gap-2">
                <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-slate-900/70 backdrop-blur-sm border border-slate-700/30">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  <span className="text-[9px] font-bold text-emerald-400/80 uppercase tracking-wider">Processing</span>
                </div>
                {currentFeed.ground_plane_calibrated !== undefined && (
                  <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-slate-900/70 backdrop-blur-sm border border-slate-700/30" title={currentFeed.ground_plane_calibrated ? "Metric ground plane (homography)" : "Pinhole estimate — calibrate ground plane for metric accuracy"}>
                    <Ruler className={cn("w-3 h-3", currentFeed.ground_plane_calibrated ? "text-cyan-400" : "text-slate-500")} />
                    <span className="text-[9px] font-bold uppercase tracking-wider text-slate-400">
                      {currentFeed.ground_plane_calibrated ? "metric" : "pinhole"}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Per-person risk panel */}
      {!compact && persons.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {persons
            .slice()
            .sort((a, b) => (b.risk_score ?? 0) - (a.risk_score ?? 0))
            .map((det) => (
              <div
                key={det.object_id}
                className={cn("flex items-center gap-3 px-3 py-2 rounded-xl border bg-slate-800/30", riskBadge(det.risk_level).split(" ").filter((c) => c.startsWith("border")).join(" ") || "border-slate-700/30")}
              >
                <div className="w-9 text-center">
                  <span className="text-[10px] text-slate-500 block leading-none">#{det.object_id}</span>
                  <span className="text-sm font-bold tabular-nums" style={{ color: boxColor(det) }}>
                    {det.risk_score != null ? det.risk_score.toFixed(0) : "–"}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                    <div className="h-full rounded-full transition-all duration-300" style={{ width: `${Math.min(100, det.risk_score ?? 0)}%`, backgroundColor: boxColor(det) }} />
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-400 flex-wrap">
                    <span className={cn("badge border px-1.5 py-0 text-[9px] uppercase", riskBadge(det.risk_level))}>{det.risk_level || "n/a"}</span>
                    {det.authorized && <span className="flex items-center gap-1 text-emerald-300"><UserCheck className="w-3 h-3" />{det.auth_label || "authorized"}</span>}
                    {det.origin === "vehicle" && <span className="flex items-center gap-1"><Car className="w-3 h-3" />vehicle #{det.linked_vehicle_track}</span>}
                    {det.origin && det.origin !== "vehicle" && <span className="text-slate-500">{originLabel(det.origin)}</span>}
                    {det.distance_m != null && <span className="flex items-center gap-1"><Ruler className="w-3 h-3" />{det.distance_m.toFixed(1)} m</span>}
                    {det.speed_mps != null && <span>{det.speed_mps.toFixed(1)} m/s</span>}
                    {det.close_contacts && det.close_contacts.length > 0 && <span className="flex items-center gap-1 text-amber-300"><Users className="w-3 h-3" />close to #{det.close_contacts.join(", #")}</span>}
                    {det.risk_reasons && det.risk_reasons.length > 0 && <span className="text-slate-500 truncate">{det.risk_reasons.filter((r) => !r.startsWith("authorized")).join(" · ")}</span>}
                  </div>
                </div>
                {!det.authorized && (
                  <button
                    onClick={() => markKnown(det)}
                    disabled={grantBusy === det.object_id}
                    className="text-[10px] px-2 py-1 rounded-lg border border-emerald-500/20 text-emerald-300 hover:bg-emerald-500/10 transition-colors whitespace-nowrap"
                    title="Mark this person as known for 30 minutes"
                  >
                    {grantBusy === det.object_id ? "…" : "Known"}
                  </button>
                )}
              </div>
            ))}
        </div>
      )}

      {/* Detection class summary */}
      {detections.length > 0 && (
        <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
          {Object.entries(
            detections.reduce((acc, d) => {
              acc[d.class_label] = (acc[d.class_label] || 0) + 1;
              return acc;
            }, {} as Record<string, number>)
          ).map(([cls, count]) => (
            <div key={cls} className="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-800/40 border border-slate-700/30 text-xs">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded" style={{ backgroundColor: CLASS_COLORS[cls] || "#89b4fa" }} />
                <span className="capitalize text-slate-300">{cls}</span>
              </div>
              <span className="font-bold tabular-nums" style={{ color: CLASS_COLORS[cls] || "#89b4fa" }}>{count}</span>
            </div>
          ))}
        </div>
      )}
      {!currentFeed && (
        <p className="mt-3 text-[10px] text-slate-600 flex items-center gap-1.5">
          <Eye className="w-3 h-3" /> Zones, distance and risk overlays appear once a stream is running.
          <EyeOff className="w-3 h-3 ml-2" /> Toggle them with the buttons above.
        </p>
      )}
    </div>
  );
}
