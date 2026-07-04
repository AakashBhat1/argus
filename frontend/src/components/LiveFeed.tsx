"use client";

import { useState, useEffect, useRef } from "react";
import { Video, Radio, Pause, Play, Film, Cpu, ChevronDown } from "lucide-react";
import { api, type FeedData, type FeedMessage } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  lastMessage: FeedMessage | null;
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

export default function LiveFeed({ lastMessage }: Props) {
  // Store latest feed data per camera
  const feedsRef = useRef<Record<string, FeedData>>({});
  const [cameraIds, setCameraIds] = useState<string[]>([]);
  const [selectedCamera, setSelectedCamera] = useState<string | null>(null);
  const [currentFeed, setCurrentFeed] = useState<FeedData | null>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [pauseLoading, setPauseLoading] = useState(false);
  const [showCameraSelect, setShowCameraSelect] = useState(false);

  useEffect(() => {
    if (lastMessage?.type !== "detections") return;

    const data = lastMessage.data;
    const camId = data.camera_id;

    feedsRef.current = { ...feedsRef.current, [camId]: data };

    // Update camera list if new camera appeared
    const ids = Object.keys(feedsRef.current);
    setCameraIds(ids);

    // Auto-select first camera if none selected
    if (!selectedCamera) {
      setSelectedCamera(camId);
    }

    // Update current feed if this is the selected camera
    if (camId === selectedCamera || (!selectedCamera && ids.length === 1)) {
      setCurrentFeed(data);
      setIsPaused(data.is_paused ?? false);
    }
  }, [lastMessage, selectedCamera]);

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

  const detections = currentFeed?.detections || [];

  return (
    <div className="card">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="card-header mb-0">Live Detection Feed</div>
        <div className="flex items-center gap-2">
          {/* Camera selector */}
          {cameraIds.length > 1 && (
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
                        {feed?.is_video_source ? (
                          <Film className="w-3 h-3 text-violet-400" />
                        ) : (
                          <Radio className="w-3 h-3 text-emerald-400" />
                        )}
                        <span className="truncate">{feed?.camera_name || id}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Feed info */}
          {currentFeed && (
            <div className="flex items-center gap-3 text-xs">
              <span className="flex items-center gap-1.5 text-slate-400">
                <Radio className="w-3 h-3 text-emerald-400 animate-pulse" />
                {cameraIds.length <= 1 && currentFeed.camera_name}
              </span>
              <span className="text-emerald-400 font-semibold tabular-nums">{currentFeed.fps} FPS</span>
              {currentFeed.inference_ms != null && (
                <span className="flex items-center gap-1 text-violet-400 tabular-nums">
                  <Cpu className="w-3 h-3" />
                  {currentFeed.inference_ms}ms
                </span>
              )}
              <span className="text-slate-600 font-mono text-[10px] tabular-nums">#{currentFeed.frame_number}</span>
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
            <p className="text-xs mt-1.5 text-slate-600">
              Start a camera stream to see live detections
            </p>
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

            {/* Bounding box overlays */}
            <svg
              viewBox={`0 0 ${currentFeed.frame_width} ${currentFeed.frame_height}`}
              className="absolute inset-0 w-full h-full pointer-events-none"
              preserveAspectRatio="xMidYMid meet"
            >
              {detections.map((det) => {
                const color = CLASS_COLORS[det.class_label] || "#89b4fa";
                return (
                  <g key={det.object_id}>
                    <rect
                      x={det.bbox_x}
                      y={det.bbox_y}
                      width={det.bbox_w}
                      height={det.bbox_h}
                      fill="none"
                      stroke={color}
                      strokeWidth="2"
                      rx="2"
                    />
                    <rect
                      x={det.bbox_x}
                      y={det.bbox_y - 20}
                      width={det.class_label.length * 8 + 60}
                      height="20"
                      fill={color}
                      opacity="0.85"
                      rx="2"
                    />
                    <text
                      x={det.bbox_x + 4}
                      y={det.bbox_y - 6}
                      fill="#11111b"
                      fontSize="11"
                      fontWeight="bold"
                    >
                      #{det.object_id} {det.class_label}{" "}
                      {(det.confidence * 100).toFixed(0)}%
                    </text>
                  </g>
                );
              })}
            </svg>

            {/* Paused overlay */}
            {isPaused && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/50">
                <div className="flex flex-col items-center gap-2">
                  <Pause className="w-10 h-10 text-white/60" />
                  <span className="text-sm font-semibold text-white/70 uppercase tracking-wider">Paused</span>
                </div>
              </div>
            )}

            {/* Pause/Resume button — only for video sources */}
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
                {isPaused ? (
                  <><Play className="w-3.5 h-3.5" /> Resume</>
                ) : (
                  <><Pause className="w-3.5 h-3.5" /> Pause</>
                )}
              </button>
            )}

            {/* Processing indicator */}
            {!isPaused && currentFeed.frame_image && (
              <div className="absolute top-3 left-3 flex items-center gap-1.5 px-2 py-1 rounded-md bg-slate-900/70 backdrop-blur-sm border border-slate-700/30">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                <span className="text-[9px] font-bold text-emerald-400/80 uppercase tracking-wider">Processing</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Detection class summary */}
      {detections.length > 0 && (
        <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
          {Object.entries(
            detections.reduce(
              (acc, d) => {
                acc[d.class_label] = (acc[d.class_label] || 0) + 1;
                return acc;
              },
              {} as Record<string, number>
            )
          ).map(([cls, count]) => (
            <div
              key={cls}
              className="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-800/40 border border-slate-700/30 text-xs"
            >
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded" style={{ backgroundColor: CLASS_COLORS[cls] || "#89b4fa" }} />
                <span className="capitalize text-slate-300">{cls}</span>
              </div>
              <span
                className="font-bold tabular-nums"
                style={{ color: CLASS_COLORS[cls] || "#89b4fa" }}
              >
                {count}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
