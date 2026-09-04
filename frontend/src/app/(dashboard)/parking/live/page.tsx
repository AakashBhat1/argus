"use client";

import { useState, useEffect, useCallback } from "react";
import {
  api,
  GATE_ROLES,
  type DetectedPlate,
  type Camera,
  type GateOcrStatus,
} from "@/lib/api";
import Link from "next/link";
import { useWebSocket } from "@/lib/websocket";
import ParkBotChat from "@/components/parking/ParkBotChat";
import SecurityConsole, { type LogEntry } from "@/components/parking/SecurityConsole";
import {
  Radio,
  Video,
  Clock,
  Zap,
} from "lucide-react";

const CLASS_COLORS: Record<string, string> = {
  person: "#89b4fa",
  car: "#a6e3a1",
  truck: "#f9e2af",
  bicycle: "#cba6f7",
  motorcycle: "#94e2d5",
  bus: "#fab387",
};

export default function ParkingLivePage() {
  const [mounted, setMounted] = useState(false);
  const [userRole, setUserRole] = useState<string>("operator");
  const [isAdmin, setIsAdmin] = useState(false);

  // Live stream state
  const [gateCameras, setGateCameras] = useState<Camera[]>([]);
  const [allCameras, setAllCameras] = useState<Camera[]>([]);
  const [selectedCameraId, setSelectedCameraId] = useState<string | null>(null);
  const [streamFrame, setStreamFrame] = useState<any>(null);
  const [ocrStatus, setOcrStatus] = useState<GateOcrStatus | null>(null);
  const [streamRunning, setStreamRunning] = useState<boolean | null>(null);

  // Plate crops (recent plates list)
  const [recentPlates, setRecentPlates] = useState<DetectedPlate[]>([]);

  // Scrolling terminal logs state
  const [logs, setLogs] = useState<LogEntry[]>([]);

  // WebSocket subscriptions
  const { lastMessage: parkingWsMessage, isConnected: isParkingConnected } = useWebSocket("parking");
  const { lastMessage: globalWsMessage, isConnected: isGlobalConnected } = useWebSocket("global");

  const addLog = useCallback((type: "info" | "success" | "warn" | "system", message: string) => {
    const id = Math.random().toString(36).substring(2, 9);
    const timestamp = new Date().toLocaleTimeString([], { hour12: false });
    const newEntry = { id, timestamp, type, message };
    setLogs((prev) => [...prev, newEntry].slice(-50)); // Keep last 50 logs
  }, []);

  // Fetch initial data
  const loadInitialData = useCallback(async () => {
    try {
      // Get user info and role
      const profile = await api.auth.me();
      setUserRole(profile.role);
      setIsAdmin(profile.role === "admin");

      // Get cameras
      const camList = await api.cameras.list(true);
      // Gate cameras are the ones with a gate role (that is what makes OCR
      // fire). Fall back to name/location matching for legacy setups.
      const byRole = camList.filter((c) => GATE_ROLES.includes(c.role || ""));
      const gateCams = byRole.length > 0
        ? byRole
        : camList.filter((c) => c.location?.toLowerCase().includes("gate") || c.name?.toLowerCase().includes("gate"));
      setGateCameras(gateCams.length > 0 ? gateCams : camList);
      setAllCameras(camList);
      if (gateCams.length > 0) {
        setSelectedCameraId(gateCams[0].id);
      } else if (camList.length > 0) {
        setSelectedCameraId(camList[0].id);
      }

      // Get recent plates
      const plates = await api.parking.plates();
      setRecentPlates(plates.slice(0, 8));

      // Initial system logs
      addLog("system", "Argus Parking Intelligence initialized.");
      addLog("system", `User profile loaded. Operator scope: ${profile.tenant_id}.`);
    } catch (err) {
      console.error("Failed to load initial live data:", err);
      addLog("warn", "System warning: failed to retrieve full environment settings.");
    }
  }, [addLog]);

  // Mount logic
  useEffect(() => {
    setMounted(true);
    loadInitialData();
  }, [loadInitialData]);

  // Poll the selected camera's stream status: tells us whether plate OCR is
  // actually armed (gate role + gate polygon) rather than assuming it is.
  useEffect(() => {
    if (!selectedCameraId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const st = await api.streams.cameraStatus(selectedCameraId);
        if (cancelled) return;
        setStreamRunning(!!st?.is_running);
        setOcrStatus(st?.gate_ocr ?? null);
      } catch {
        if (cancelled) return;
        setStreamRunning(false);
        setOcrStatus(null);
      }
    };
    tick();
    const id = setInterval(tick, 4000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [selectedCameraId]);

  // Handle global WebSocket (contains frame image data and YOLO detections)
  useEffect(() => {
    if (!globalWsMessage) return;

    if (globalWsMessage.type === "detections" && globalWsMessage.data) {
      const data = globalWsMessage.data;
      if (data.camera_id === selectedCameraId) {
        setStreamFrame(data);
      }
    }
  }, [globalWsMessage, selectedCameraId]);

  // Handle parking WebSocket (events like entry/exit)
  useEffect(() => {
    if (!parkingWsMessage) return;

    if (parkingWsMessage.type === "parking" && parkingWsMessage.data) {
      const { event, space_id, plate_text, assign, amount_paid, duration_minutes } = parkingWsMessage.data;

      if (event === "plate_detected") {
        if (assign) {
          addLog("success", `Space ${assign.space_id} assigned to vehicle: ${plate_text}.`);
          // Prepend new plate
          setRecentPlates((prev) => {
            const exists = prev.find((p) => p.plate_text === plate_text);
            if (exists) return prev;
            const newPlate: DetectedPlate = {
              id: Math.random().toString(),
              tenant_id: "",
              plate_text,
              confidence: parkingWsMessage.data.confidence || 0.9,
              timestamp: new Date().toISOString(),
              is_parked: true,
              amount_paid: 0,
            };
            return [newPlate, ...prev].slice(0, 8);
          });
        } else {
          addLog("info", `OCR recognized plate: ${plate_text} (confidence: ${((parkingWsMessage.data.confidence || 0.8) * 100).toFixed(0)}%).`);
        }
      } else if (event === "exit") {
        addLog("info", `Vehicle ${plate_text || "N/A"} released from Space ${space_id}. Paid: ₹${amount_paid}. Duration: ${duration_minutes} mins.`);
        // Mark plate as exited
        setRecentPlates((prev) =>
          prev.map((p) => (p.plate_text === plate_text ? { ...p, is_parked: false } : p))
        );
      }
    }
  }, [parkingWsMessage]);

  const handleCommandExecuted = useCallback(async (command: Record<string, any>) => {
    addLog("success", `Command executed successfully: ${JSON.stringify(command)}`);
    try {
      const plates = await api.parking.plates();
      setRecentPlates(plates.slice(0, 8));
    } catch (err) {
      console.error("Failed to refresh plates after command execution:", err);
    }
  }, [addLog]);

  return (
    <div className="space-y-6 animate-fade-in pb-16">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">
            <span className="gradient-text-static">Parking Live Control</span>
          </h1>
          <p className="page-subtitle">Real-time gate telemetry streams, plate scanning, and command AI</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-[11px] bg-slate-900/60 border border-slate-700/30 rounded-xl px-3.5 py-2 backdrop-blur-sm">
            <span className={isGlobalConnected ? "status-led-active" : "status-led-error"} />
            <span className={isGlobalConnected ? "text-emerald-400 font-medium" : "text-red-400"}>
              {isGlobalConnected ? "Global Video Live" : "Video Offline"}
            </span>
          </div>
          <div className="flex items-center gap-2 text-[11px] bg-slate-900/60 border border-slate-700/30 rounded-xl px-3.5 py-2 backdrop-blur-sm">
            <span className={isParkingConnected ? "status-led-active" : "status-led-error"} />
            <span className={isParkingConnected ? "text-emerald-400 font-medium" : "text-red-400"}>
              {isParkingConnected ? "Telemetry Stream Connected" : "Telemetry Offline"}
            </span>
          </div>
        </div>
      </div>

      {/* Main Split: Left (Feed + Plate Crops), Right (Chat) */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        {/* Left Side: YOLO feed (col-span-7) */}
        <div className="xl:col-span-7 flex flex-col gap-6">
          {/* Live YOLO Video Frame */}
          <div className="card !p-0">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800/40">
              <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Video className="w-4 h-4 text-blue-400" /> Gate Surveillance Video Feed
              </span>

              {gateCameras.length > 1 && (
                <select
                  value={selectedCameraId || ""}
                  onChange={(e) => {
                    setSelectedCameraId(e.target.value);
                    setStreamFrame(null);
                  }}
                  className="select !py-1 !text-xs !bg-slate-950/60 !border-slate-800"
                >
                  {gateCameras.map((cam) => (
                    <option key={cam.id} value={cam.id}>
                      {cam.name}
                    </option>
                  ))}
                </select>
              )}
            </div>

            <div className="relative bg-slate-950 aspect-video scan-line overflow-hidden rounded-b-2xl">
              {!streamFrame ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-600">
                  <div className="w-14 h-14 rounded-2xl bg-slate-900/60 flex items-center justify-center mb-3">
                    <Radio className="w-6 h-6 text-slate-500 animate-pulse" />
                  </div>
                  <p className="text-xs font-semibold text-slate-400">Waiting for stream telemetry...</p>
                  <p className="text-[10px] text-slate-600 mt-1">Select an active gate stream to display YOLO overlays</p>
                </div>
              ) : (
                <div className="relative w-full h-full">
                  {streamFrame.frame_image ? (
                    <img
                      src={`data:image/jpeg;base64,${streamFrame.frame_image}`}
                      alt="Live feed"
                      className="absolute inset-0 w-full h-full object-contain"
                    />
                  ) : (
                    <div className="absolute inset-0 flex items-center justify-center bg-slate-900/40">
                      <p className="text-xs text-slate-500">Retrieving stream frames...</p>
                    </div>
                  )}

                  {/* SVG Bounding Boxes */}
                  <svg
                    viewBox={`0 0 ${streamFrame.frame_width} ${streamFrame.frame_height}`}
                    className="absolute inset-0 w-full h-full pointer-events-none"
                    preserveAspectRatio="xMidYMid meet"
                  >
                    {(streamFrame.detections || []).map((det: any) => {
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
                            y={det.bbox_y - 18}
                            width={det.class_label.length * 7 + 45}
                            height="18"
                            fill={color}
                            opacity="0.8"
                            rx="2"
                          />
                          <text
                            x={det.bbox_x + 4}
                            y={det.bbox_y - 5}
                            fill="#11111b"
                            fontSize="9"
                            fontWeight="bold"
                          >
                            {det.class_label} {(det.confidence * 100).toFixed(0)}%
                          </text>
                        </g>
                      );
                    })}
                  </svg>

                  {/* OCR status badge — reflects the real gate configuration */}
                  <div
                    className={`absolute top-3 left-3 flex items-center gap-1.5 px-2 py-1 rounded-md bg-slate-900/70 border backdrop-blur-sm ${
                      ocrStatus?.active ? "border-emerald-500/30" : "border-amber-500/30"
                    }`}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full ${ocrStatus?.active ? "bg-emerald-400 animate-pulse" : "bg-amber-400"}`} />
                    <span className={`text-[9px] font-bold uppercase tracking-wider ${ocrStatus?.active ? "text-emerald-400" : "text-amber-300"}`}>
                      {ocrStatus?.active
                        ? `OCR active · ${ocrStatus.plates_read} plates / ${ocrStatus.vehicles_in_gate} vehicles`
                        : "OCR inactive"}
                    </span>
                  </div>
                </div>
              )}
            </div>

            {/* Why OCR is not firing, with a direct fix */}
            {selectedCameraId && ocrStatus && !ocrStatus.active && (
              <div className="mt-3 flex items-start gap-3 rounded-xl border border-amber-500/20 bg-amber-500/[0.05] px-4 py-3">
                <div className="flex-1 text-[11px] text-amber-200">
                  <p className="font-semibold">Number-plate OCR is not running on this camera</p>
                  <p className="text-amber-200/80 mt-0.5">{ocrStatus.reason}</p>
                  {ocrStatus.last_error && <p className="text-amber-200/60 mt-0.5">Last attempt: {ocrStatus.last_error}</p>}
                </div>
                <Link
                  href="/cameras"
                  className="shrink-0 px-3 py-1.5 rounded-lg text-[11px] font-medium bg-amber-500/15 text-amber-200 border border-amber-500/30 hover:bg-amber-500/25"
                >
                  Open Scene Setup → Gate
                </Link>
              </div>
            )}
            {selectedCameraId && streamRunning === false && (
              <div className="mt-3 rounded-xl border border-slate-700/40 bg-slate-900/40 px-4 py-3 text-[11px] text-slate-400">
                Stream is not running for this camera — start it from the Cameras page.
                {allCameras.length > 0 && !allCameras.some((c) => GATE_ROLES.includes(c.role || "")) && (
                  <span> No camera has a gate role yet; OCR only runs on <span className="text-slate-200">gate entry</span> / <span className="text-slate-200">gate exit</span> cameras.</span>
                )}
              </div>
            )}
          </div>

          {/* Plate Crops (License Plate Cards) */}
          <div className="space-y-3">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
              <Zap className="w-3.5 h-3.5 text-amber-400" /> Recent Ingestion Plate Crops
            </span>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {recentPlates.map((plate) => (
                <div
                  key={plate.id}
                  className={`card-sm flex flex-col gap-2 relative overflow-hidden group border-slate-800 ${
                    plate.is_parked
                      ? "bg-slate-900/40 hover:border-slate-700"
                      : "bg-slate-950/20 opacity-60 hover:opacity-100"
                  }`}
                >
                  {/* Status Indicator */}
                  <span
                    className={`absolute top-2 right-2 w-1.5 h-1.5 rounded-full ${
                      plate.is_parked ? "bg-emerald-400 animate-pulse" : "bg-slate-600"
                    }`}
                  />

                  {/* Simulated License Plate */}
                  <div className="border-2 border-slate-950 rounded bg-amber-400 text-slate-950 px-2 py-1 flex items-center font-bold font-mono tracking-wider shadow-inner select-none relative min-h-[38px] justify-center text-xs">
                    {/* Left blue strip mimicking IND label */}
                    <div className="absolute left-0 top-0 bottom-0 w-2.5 bg-blue-700 flex flex-col items-center justify-center text-[5px] text-white select-none">
                      <span>I</span>
                      <span>N</span>
                      <span>D</span>
                    </div>
                    <span className="ml-1 leading-none">{plate.plate_text}</span>
                  </div>

                  <div className="flex flex-col gap-0.5 mt-1">
                    <div className="flex justify-between items-center text-[9px] text-slate-500">
                      <span className="flex items-center gap-0.5"><Clock className="w-2.5 h-2.5" /> {new Date(plate.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                      <span className="font-bold text-emerald-400/90">{((plate.confidence || 0.9) * 100).toFixed(0)}% Match</span>
                    </div>
                  </div>
                </div>
              ))}
              {recentPlates.length === 0 && (
                <div className="col-span-4 py-8 text-center text-xs text-slate-500 italic">
                  Waiting for plate detections...
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right Side: Chatbot interface (col-span-5) */}
        <div className="xl:col-span-5 flex flex-col gap-4">
          <ParkBotChat
            isAdmin={isAdmin}
            onCommandExecuted={handleCommandExecuted}
            addLog={addLog}
          />
        </div>
      </div>

      {/* Bottom Row: Scrolling Security Operation Logging Terminal */}
      <SecurityConsole logs={logs} />
    </div>
  );
}
