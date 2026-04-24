"use client";

import { useEffect, useState, useRef } from "react";
import {
  Camera,
  Plus,
  Play,
  Square,
  Trash2,
  MapPin,
  Loader2,
  X,
  Film,
  ChevronDown,
  FileVideo,
  HardDrive,
  Pentagon,
} from "lucide-react";
import { api, type Camera as CameraType, type StreamStatus } from "@/lib/api";
import { cn, statusColor } from "@/lib/utils";
import ZoneEditor from "@/components/ZoneEditor";

interface VideoFile {
  filename: string;
  path: string;
  size_mb: number;
  extension: string;
}

export default function CamerasPage() {
  const [cameras, setCameras] = useState<CameraType[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({
    name: "",
    location: "",
    stream_url: "0",
    resolution: "1280x720",
    fps: 30,
  });
  const [streamStatus, setStreamStatus] = useState<
    Record<string, StreamStatus>
  >({});

  // Video selector state
  const [videos, setVideos] = useState<VideoFile[]>([]);
  const [showVideoSelector, setShowVideoSelector] = useState(false);
  const [loadingVideos, setLoadingVideos] = useState(false);
  const videoSelectorRef = useRef<HTMLDivElement>(null);

  // Zone editor state
  const [zoneEditorCamera, setZoneEditorCamera] = useState<CameraType | null>(null);

  useEffect(() => {
    loadCameras();
    loadStreamStatus();
    const interval = setInterval(loadStreamStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  // Close video selector when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        videoSelectorRef.current &&
        !videoSelectorRef.current.contains(event.target as Node)
      ) {
        setShowVideoSelector(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function loadCameras() {
    try {
      const data = await api.cameras.list();
      setCameras(data);
    } catch (err) {
      console.error("Failed to load cameras:", err);
    } finally {
      setLoading(false);
    }
  }

  async function loadStreamStatus() {
    try {
      const data = await api.streams.status();
      const statusMap: Record<string, StreamStatus> = {};
      data.streams.forEach((s) => {
        statusMap[s.camera_id] = s;
      });
      setStreamStatus(statusMap);
    } catch {
      // stream endpoint might not be available yet
    }
  }

  async function loadVideos() {
    setLoadingVideos(true);
    try {
      const data = await api.videos.list();
      setVideos(data.videos);
    } catch (err) {
      console.error("Failed to load videos:", err);
      setVideos([]);
    } finally {
      setLoadingVideos(false);
    }
  }

  function handleVideoSelect(video: VideoFile) {
    setForm({ ...form, stream_url: video.path });
    setShowVideoSelector(false);
  }

  function toggleVideoSelector() {
    if (!showVideoSelector) {
      loadVideos();
    }
    setShowVideoSelector(!showVideoSelector);
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.cameras.create(form);
      setShowAdd(false);
      setForm({
        name: "",
        location: "",
        stream_url: "0",
        resolution: "1280x720",
        fps: 30,
      });
      loadCameras();
    } catch (err) {
      console.error("Failed to add camera:", err);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this camera?")) return;
    try {
      await api.cameras.delete(id);
      loadCameras();
    } catch (err) {
      console.error("Failed to delete camera:", err);
    }
  }

  async function handleToggleStream(id: string, isRunning: boolean) {
    try {
      if (isRunning) {
        await api.streams.stop(id);
      } else {
        await api.streams.start(id);
      }
      loadStreamStatus();
      loadCameras();
    } catch (err) {
      console.error("Failed to toggle stream:", err);
    }
  }

  // Check if current stream_url is a video file
  const isVideoSource = form.stream_url.startsWith("video://");
  const selectedVideoName = isVideoSource
    ? form.stream_url.replace("video://", "")
    : null;

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title"><span className="gradient-text-static">Cameras</span></h1>
          <p className="page-subtitle">Manage surveillance cameras and streams</p>
        </div>
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="btn-primary"
        >
          {showAdd ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
          {showAdd ? "Cancel" : "Add Camera"}
        </button>
      </div>

      {showAdd && (
        <form onSubmit={handleAdd} className="card space-y-4 animate-slide-up">
          <h3 className="text-sm font-semibold text-slate-200">New Camera</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <input
              className="input"
              placeholder="Camera Name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
            <input
              className="input"
              placeholder="Location"
              value={form.location}
              onChange={(e) => setForm({ ...form, location: e.target.value })}
              required
            />
            <div className="md:col-span-2 space-y-3">
              <input
                className="input w-full"
                placeholder="Stream URL (RTSP/HTTP) or local webcam index (0)"
                value={form.stream_url}
                onChange={(e) => setForm({ ...form, stream_url: e.target.value })}
                required
              />

              {/* Video File Selector */}
              <div className="relative" ref={videoSelectorRef}>
                <button
                  type="button"
                  onClick={toggleVideoSelector}
                  className={cn(
                    "w-full flex items-center gap-3 px-4 py-3 rounded-xl border transition-all duration-200 text-left",
                    isVideoSource
                      ? "bg-violet-500/[0.06] border-violet-500/20 hover:border-violet-500/30"
                      : "bg-slate-800/30 border-slate-700/30 hover:border-slate-600/40 hover:bg-slate-800/50"
                  )}
                >
                  <div className={cn(
                    "w-9 h-9 rounded-lg flex items-center justify-center shrink-0",
                    isVideoSource
                      ? "bg-violet-500/10"
                      : "bg-slate-700/30"
                  )}>
                    <Film className={cn(
                      "w-4.5 h-4.5",
                      isVideoSource ? "text-violet-400" : "text-slate-500"
                    )} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className={cn(
                      "text-xs font-medium",
                      isVideoSource ? "text-violet-300" : "text-slate-400"
                    )}>
                      {isVideoSource ? "Video File Selected" : "Or use a video file"}
                    </p>
                    <p className={cn(
                      "text-[11px] mt-0.5 truncate",
                      isVideoSource ? "text-violet-400/70" : "text-slate-600"
                    )}>
                      {selectedVideoName || "Click to browse available videos"}
                    </p>
                  </div>
                  <ChevronDown className={cn(
                    "w-4 h-4 shrink-0 transition-transform duration-200",
                    showVideoSelector ? "rotate-180" : "",
                    isVideoSource ? "text-violet-400/50" : "text-slate-600"
                  )} />
                </button>

                {/* Dropdown */}
                {showVideoSelector && (
                  <div className="absolute z-50 w-full mt-2 rounded-xl border border-slate-700/40 bg-slate-900/95 backdrop-blur-xl shadow-2xl shadow-black/40 overflow-hidden animate-slide-up">
                    <div className="px-3 py-2.5 border-b border-slate-700/30 flex items-center gap-2">
                      <HardDrive className="w-3.5 h-3.5 text-slate-500" />
                      <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                        Available Videos
                      </span>
                    </div>

                    {loadingVideos ? (
                      <div className="flex items-center justify-center py-8">
                        <Loader2 className="w-4 h-4 text-slate-500 animate-spin" />
                      </div>
                    ) : videos.length === 0 ? (
                      <div className="py-6 text-center">
                        <FileVideo className="w-6 h-6 text-slate-700 mx-auto mb-2" />
                        <p className="text-xs text-slate-600">No videos found</p>
                        <p className="text-[10px] text-slate-700 mt-1">
                          Place video files in the video/ folder
                        </p>
                      </div>
                    ) : (
                      <div className="max-h-48 overflow-y-auto">
                        {videos.map((video) => (
                          <button
                            key={video.filename}
                            type="button"
                            onClick={() => handleVideoSelect(video)}
                            className={cn(
                              "w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors duration-150",
                              selectedVideoName === video.filename
                                ? "bg-violet-500/10"
                                : "hover:bg-slate-800/60"
                            )}
                          >
                            <div className={cn(
                              "w-8 h-8 rounded-lg flex items-center justify-center shrink-0",
                              selectedVideoName === video.filename
                                ? "bg-violet-500/15"
                                : "bg-slate-800/50"
                            )}>
                              <FileVideo className={cn(
                                "w-4 h-4",
                                selectedVideoName === video.filename
                                  ? "text-violet-400"
                                  : "text-slate-500"
                              )} />
                            </div>
                            <div className="flex-1 min-w-0">
                              <p className={cn(
                                "text-xs font-medium truncate",
                                selectedVideoName === video.filename
                                  ? "text-violet-300"
                                  : "text-slate-300"
                              )}>
                                {video.filename}
                              </p>
                              <p className="text-[10px] text-slate-600 mt-0.5">
                                {video.size_mb} MB · {video.extension.replace(".", "").toUpperCase()}
                              </p>
                            </div>
                            {selectedVideoName === video.filename && (
                              <div className="w-1.5 h-1.5 rounded-full bg-violet-400 shrink-0" />
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
            <input
              className="input"
              placeholder="Resolution (e.g. 1280x720)"
              value={form.resolution}
              onChange={(e) =>
                setForm({ ...form, resolution: e.target.value })
              }
            />
            <input
              type="number"
              className="input"
              placeholder="FPS"
              value={form.fps}
              onChange={(e) =>
                setForm({ ...form, fps: parseInt(e.target.value) })
              }
            />
          </div>
          <div className="flex gap-3">
            <button type="submit" className="btn-primary">
              Save Camera
            </button>
            <button
              type="button"
              onClick={() => setShowAdd(false)}
              className="px-4 py-2 text-sm text-slate-400 hover:text-slate-200 transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {loading && (
        <div className="flex items-center justify-center py-20">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="w-6 h-6 text-blue-400/60 animate-spin" />
            <p className="text-xs text-slate-500">Loading cameras...</p>
          </div>
        </div>
      )}

      {!loading && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 stagger-children">
          {cameras.map((cam) => {
            const ss = streamStatus[cam.id];
            const isRunning = ss?.is_running ?? false;
            const isVideo = cam.stream_url.startsWith("video://");
            return (
              <div key={cam.id} className="card group">
                {/* Preview area */}
                <div className={cn(
                  "h-32 rounded-xl mb-4 flex items-center justify-center border transition-colors duration-300",
                  isRunning
                    ? "bg-gradient-to-br from-emerald-500/[0.04] to-blue-500/[0.04] border-emerald-500/15"
                    : "bg-slate-800/30 border-slate-700/30"
                )}>
                  <div className="flex flex-col items-center gap-2">
                    {isVideo ? (
                      <Film className={cn("w-8 h-8 transition-colors", isRunning ? "text-violet-400/50" : "text-slate-600")} />
                    ) : (
                      <Camera className={cn("w-8 h-8 transition-colors", isRunning ? "text-emerald-400/50" : "text-slate-600")} />
                    )}
                    {isRunning && (
                      <span className="text-[9px] font-bold text-emerald-400/70 uppercase tracking-wider flex items-center gap-1">
                        <span className="status-led-active" />
                        {isVideo ? "Playing" : "Streaming"}
                      </span>
                    )}
                    {isVideo && !isRunning && (
                      <span className="text-[9px] font-medium text-violet-400/50 uppercase tracking-wider">
                        {cam.stream_url.replace("video://", "")}
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-100">
                      {cam.name}
                    </h3>
                    <div className="flex items-center gap-1.5 text-xs text-slate-500 mt-1">
                      <MapPin className="w-3 h-3" />
                      {cam.location}
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className={isRunning ? "status-led-active" : "status-led-inactive"} />
                    <span className={cn("text-[10px] font-medium", statusColor(cam.status))}>
                      {cam.status}
                    </span>
                  </div>
                </div>

                {ss && isRunning && (
                  <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                    <div className="bg-slate-800/40 rounded-xl py-2 border border-slate-700/20">
                      <p className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold">FPS</p>
                      <p className="text-sm font-bold text-blue-400 tabular-nums mt-0.5">
                        {ss.fps}
                      </p>
                    </div>
                    <div className="bg-slate-800/40 rounded-xl py-2 border border-slate-700/20">
                      <p className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold">Frames</p>
                      <p className="text-sm font-bold text-emerald-400 tabular-nums mt-0.5">
                        {ss.frame_count.toLocaleString()}
                      </p>
                    </div>
                    <div className="bg-slate-800/40 rounded-xl py-2 border border-slate-700/20">
                      <p className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold">Tracks</p>
                      <p className="text-sm font-bold text-violet-400 tabular-nums mt-0.5">
                        {ss.active_tracks}
                      </p>
                    </div>
                  </div>
                )}

                <div className="mt-4 flex items-center gap-2">
                  <button
                    onClick={() => handleToggleStream(cam.id, isRunning)}
                    className={cn(
                      "flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-xs font-medium transition-all duration-200",
                      isRunning
                        ? "bg-red-500/[0.06] text-red-400 border border-red-500/15 hover:bg-red-500/15"
                        : "bg-emerald-500/[0.06] text-emerald-400 border border-emerald-500/15 hover:bg-emerald-500/15"
                    )}
                    aria-label={isRunning ? "Stop stream" : "Start stream"}
                  >
                    {isRunning ? (
                      <>
                        <Square className="w-3.5 h-3.5" /> Stop Stream
                      </>
                    ) : (
                      <>
                        <Play className="w-3.5 h-3.5" /> {isVideo ? "Play Video" : "Start Stream"}
                      </>
                    )}
                  </button>
                  <button
                    onClick={() => setZoneEditorCamera(cam)}
                    className="p-2.5 rounded-xl text-slate-600 hover:text-violet-400 hover:bg-violet-500/[0.06] transition-all duration-200"
                    aria-label="Draw zones"
                    title="Draw ROI Zones"
                  >
                    <Pentagon className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleDelete(cam.id)}
                    className="p-2.5 rounded-xl text-slate-600 hover:text-red-400 hover:bg-red-500/[0.06] transition-all duration-200"
                    aria-label="Delete camera"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })}

          {cameras.length === 0 && (
            <div className="col-span-full py-20 text-center">
              <div className="w-14 h-14 rounded-2xl bg-slate-800/40 flex items-center justify-center mx-auto mb-4">
                <Camera className="w-7 h-7 opacity-30 text-slate-500" />
              </div>
              <p className="text-sm font-medium text-slate-400">No cameras configured</p>
              <p className="text-xs mt-1.5 text-slate-600">
                Click &ldquo;Add Camera&rdquo; to get started
              </p>
            </div>
          )}
        </div>
      )}

      {zoneEditorCamera && (
        <ZoneEditor
          cameraId={zoneEditorCamera.id}
          cameraName={zoneEditorCamera.name}
          onClose={() => setZoneEditorCamera(null)}
        />
      )}
    </div>
  );
}
