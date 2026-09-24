"use client";

import { useEffect, useReducer, useRef, useState, useCallback } from "react";
import { Camera as CameraIcon, Map, RefreshCw, Loader2, Video, AlertCircle } from "lucide-react";
import { api, Camera, ParkingSpace, type FeedMessage } from "@/lib/api";
import { containedPointToNormalized } from "@/lib/geometry";
import { useWebSocket } from "@/lib/websocket";
import SlotMapper from "./SlotMapper";
import { cn } from "@/lib/utils";
import WebRTCPlayer from "@/components/WebRTCPlayer";

interface Point {
  x: number;
  y: number;
}

interface SlotPolygon {
  space_id: string;
  polygon: Point[];
  occupied?: boolean;
  score?: number;
}

interface LiveLotViewProps {
  spaces: ParkingSpace[];
  onReleaseSpace: (spaceId: string) => void;
}

export default function LiveLotView({ spaces, onReleaseSpace }: LiveLotViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedCameraId, setSelectedCameraId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Frame & Slots state
  const [currentFrame, setCurrentFrame] = useState<string | null>(null);
  const [mediaTransport, setMediaTransport] = useState<"webrtc" | "websocket_jpeg">("websocket_jpeg");
  const [frameSize, setFrameSize] = useState({ width: 1280, height: 720 });
  const [slotPolygons, setSlotPolygons] = useState<SlotPolygon[]>([]);
  const [occupancyMap, setOccupancyMap] = useState<Record<string, { occupied: boolean; score?: number }>>({});

  // Mapper modal state
  const [showMapper, setShowMapper] = useState(false);

  // Fetch parking cameras once; slots load when a camera is selected.
  useEffect(() => {
    let cancelled = false;
    api.cameras
      .list()
      .then((camList) => {
        if (cancelled) return;
        // Prefer cameras with role === 'parking'; fall back to any active camera.
        let parkingCams = camList.filter((c) => (c.status === "active" || c.is_active) && c.role === "parking");
        if (parkingCams.length === 0) {
          parkingCams = camList.filter((c) => c.status === "active" || c.is_active);
        }
        setCameras(parkingCams);
        if (parkingCams.length > 0) setSelectedCameraId(parkingCams[0].id);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load cameras");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Snapshot + slot polygons for the selected camera; `reloadCameraData`
  // re-runs this after a retry or a slot-mapper save.
  const [cameraDataKey, reloadCameraData] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    if (!selectedCameraId) return;
    let cancelled = false;
    Promise.all([
      api.streams.snapshot(selectedCameraId).catch(() => null),
      api.parking.slots(selectedCameraId).catch(() => []),
    ])
      .then(([snap, slotsList]) => {
        if (cancelled) return;
        if (snap) setCurrentFrame(snap.image);
        setSlotPolygons(
          slotsList.map((s) => ({
            space_id: s.space_id,
            polygon: (s.polygon || []).map((pt) => ({ x: pt[0], y: pt[1] })),
          })),
        );
      })
      .catch((err) => console.error("Error loading camera data:", err));
    return () => {
      cancelled = true;
    };
  }, [selectedCameraId, cameraDataKey]);

  // Real-time messages on the SELECTED CAMERA's channel.
  const handleCameraMessage = useCallback((message: FeedMessage) => {
    const msgType = message.type;
    const data: any = message.data || message;
    const mergeSlots = (slotsArr: unknown) => {
      if (!Array.isArray(slotsArr)) return;
      const map: Record<string, { occupied: boolean; score?: number }> = {};
      slotsArr.forEach((s: any) => {
        map[s.space_id] = { occupied: !!s.occupied, score: s.score };
      });
      setOccupancyMap((prev) => ({ ...prev, ...map }));
    };

    if (msgType === "detections") {
      setMediaTransport(data.media_transport === "webrtc" ? "webrtc" : "websocket_jpeg");
      if (data.frame_width && data.frame_height) {
        setFrameSize({ width: data.frame_width, height: data.frame_height });
      }
      if (data.frame_image) {
        setCurrentFrame(data.frame_image);
      }
      mergeSlots(data.parking_slots || data.slots);
    } else if (msgType === "parking") {
      // Committed transitions.
      mergeSlots(data.slots || data.parking_slots);
    }
  }, []);
  useWebSocket(selectedCameraId || null, handleCameraMessage);

  // Canvas Drawing
  const drawOverlay = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || (!currentFrame && mediaTransport !== "webrtc")) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const drawSlots = () => {
      // Render slots
      slotPolygons.forEach((slot) => {
        if (slot.polygon.length < 4) return;

        // Check occupancy from spaces prop or real-time map
        const spaceMeta = spaces.find((s) => s.space_id === slot.space_id);
        const liveState = occupancyMap[slot.space_id];
        const isOccupied = liveState ? liveState.occupied : spaceMeta?.is_occupied ?? false;

        ctx.beginPath();
        slot.polygon.forEach((p, i) => {
          const px = p.x * canvas.width;
          const py = p.y * canvas.height;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.closePath();

        const strokeColor = isOccupied ? "#ef4444" : "#22c55e";
        const fillColor = isOccupied ? "rgba(239, 68, 68, 0.25)" : "rgba(34, 197, 94, 0.15)";

        ctx.fillStyle = fillColor;
        ctx.fill();
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = 2.5;
        ctx.stroke();

        // Slot Label
        const cx = (slot.polygon.reduce((sum, p) => sum + p.x, 0) / 4) * canvas.width;
        const cy = (slot.polygon.reduce((sum, p) => sum + p.y, 0) / 4) * canvas.height;

        ctx.font = "bold 12px system-ui";
        ctx.fillStyle = "#ffffff";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";

        let label = slot.space_id;
        if (liveState?.score !== undefined) {
          label += ` (${(liveState.score * 100).toFixed(0)}%)`;
        }
        ctx.fillText(label, cx, cy);
      });
    };
    if (mediaTransport === "webrtc") {
      canvas.width = frameSize.width;
      canvas.height = frameSize.height;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drawSlots();
      return;
    }
    const img = new Image();
    img.onload = () => {
      canvas.width = img.width;
      canvas.height = img.height;
      ctx.drawImage(img, 0, 0);
      drawSlots();
    };
    img.src = `data:image/jpeg;base64,${currentFrame}`;
  }, [currentFrame, mediaTransport, frameSize, slotPolygons, spaces, occupancyMap]);

  useEffect(() => {
    drawOverlay();
  }, [drawOverlay]);

  // Click on red/occupied bay triggers checkout
  function handleCanvasClick(e: React.MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // The canvas is drawn with object-fit: contain, so account for the
    // letterbox bars when mapping the click into frame coordinates.
    const point = containedPointToNormalized(
      canvas.getBoundingClientRect(),
      canvas.width,
      canvas.height,
      e.clientX,
      e.clientY,
    );
    if (!point) return;

    for (const slot of slotPolygons) {
      if (pointInPolygon(point, slot.polygon)) {
        const spaceMeta = spaces.find((s) => s.space_id === slot.space_id);
        const liveState = occupancyMap[slot.space_id];
        const isOccupied = liveState ? liveState.occupied : spaceMeta?.is_occupied ?? false;

        if (isOccupied) {
          onReleaseSpace(slot.space_id);
        }
        break;
      }
    }
  }

  function pointInPolygon(pt: Point, poly: Point[]): boolean {
    if (poly.length < 3) return false;
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i].x, yi = poly[i].y;
      const xj = poly[j].x, yj = poly[j].y;
      const intersect = ((yi > pt.y) !== (yj > pt.y)) &&
        (pt.x < (xj - xi) * (pt.y - yi) / (yj - yi) + xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }

  const selectedCam = cameras.find((c) => c.id === selectedCameraId);
  const isFallback = cameras.length > 0 && !cameras.some((c) => c.role === "parking");

  return (
    <div className="flex flex-col gap-3">
      {/* Top Header Bar */}
      <div className="flex items-center justify-between bg-slate-900/60 border border-slate-700/30 px-4 py-3 rounded-2xl">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center">
            <Video className="w-4 h-4 text-emerald-400" />
          </div>
          <div>
            <h3 className="text-xs font-semibold text-slate-200">Live Birdseye Lot Visualizer</h3>
            <p className="text-[10px] text-slate-500">Real-time vision occupancy stream & quad overlays</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Camera Selector */}
          {cameras.length > 0 && (
            <div className="flex items-center gap-2">
              {isFallback && (
                <span className="text-[10px] font-medium text-amber-400 bg-amber-500/10 border border-amber-500/30 px-2 py-1 rounded-md flex items-center gap-1">
                  <AlertCircle className="w-3 h-3 text-amber-400" />
                  Fallback: Non-parking
                </span>
              )}
              <select
                value={selectedCameraId}
                onChange={(e) => setSelectedCameraId(e.target.value)}
                className={cn(
                  "bg-slate-800 border text-xs text-slate-300 rounded-lg px-2.5 py-1.5 focus:outline-none",
                  isFallback ? "border-amber-500/40 text-amber-200" : "border-slate-700"
                )}
                title={isFallback ? "Fallback: No cameras with role='parking' configured" : undefined}
              >
                <optgroup label={isFallback ? "Non-Parking Cameras (Fallback)" : "Parking Feeds"}>
                  {cameras.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} ({c.role === "parking" ? "Parking Feed" : `Fallback: ${c.role || "non-parking"}${c.location ? ` · ${c.location}` : ""}`})
                    </option>
                  ))}
                </optgroup>
              </select>
            </div>
          )}

          {/* Map Bays Button */}
          {selectedCameraId && (
            <button
              onClick={() => setShowMapper(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-300 text-xs font-medium transition-colors"
            >
              <Map className="w-3.5 h-3.5" /> Map Bays
            </button>
          )}
        </div>
      </div>

      {/* Main Stream Canvas Container */}
      <div className="relative w-full h-[500px] bg-slate-950 border border-slate-800 rounded-2xl flex items-center justify-center overflow-hidden">
        {loading ? (
          <div className="flex flex-col items-center gap-2 text-slate-500">
            <Loader2 className="w-6 h-6 animate-spin" />
            <span className="text-xs">Connecting to lot stream...</span>
          </div>
        ) : !currentFrame && mediaTransport !== "webrtc" ? (
          <div className="flex flex-col items-center gap-2 text-slate-500 p-6 text-center">
            <AlertCircle className="w-8 h-8 text-slate-600" />
            <p className="text-xs text-slate-400">No snapshot available for camera</p>
            {selectedCameraId && (
              <button
                onClick={reloadCameraData}
                className="mt-2 text-xs text-violet-400 flex items-center gap-1 hover:underline"
              >
                <RefreshCw className="w-3 h-3" /> Retry capture
              </button>
            )}
          </div>
        ) : (
          <div className="relative w-full h-full">
            {mediaTransport === "webrtc" && selectedCameraId && (
              <WebRTCPlayer cameraId={selectedCameraId} />
            )}
            <canvas
              ref={canvasRef}
              onClick={handleCanvasClick}
              className="absolute inset-0 w-full h-full cursor-pointer rounded-lg"
              style={{ objectFit: "contain" }}
            />
          </div>
        )}

        {/* Overlay Stats Tag */}
        {slotPolygons.length > 0 && (
          <div className="absolute top-4 left-4 bg-slate-900/85 backdrop-blur-md border border-slate-700/40 rounded-xl px-3 py-1.5 flex items-center gap-3 text-xs text-slate-300">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
              <span>{slotPolygons.length} Bays Mapped</span>
            </div>
            <span className="text-slate-600">|</span>
            <span className="text-[10px] text-slate-400">Click red bay to checkout</span>
          </div>
        )}
      </div>

      {/* SlotMapper Modal */}
      {showMapper && selectedCameraId && (
        <SlotMapper
          cameraId={selectedCameraId}
          cameraName={selectedCam ? (isFallback ? `${selectedCam.name} (Non-parking fallback)` : selectedCam.name) : "Parking Camera"}
          onClose={() => setShowMapper(false)}
          onSaved={reloadCameraData}
        />
      )}
    </div>
  );
}
