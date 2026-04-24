"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import {
  X,
  Save,
  Trash2,
  Undo2,
  MousePointer,
  Loader2,
  Plus,
  Pentagon,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Point {
  x: number;
  y: number;
}

interface Zone {
  zone_id: number;
  name: string;
  points: number[][];
  threshold_sec: number;
  color: number[];
  camera_ids: string[] | null;
}

interface ZoneEditorProps {
  cameraId: string;
  cameraName: string;
  onClose: () => void;
}

const ZONE_COLORS = [
  [0, 255, 255],
  [255, 100, 100],
  [100, 255, 100],
  [255, 180, 50],
  [180, 100, 255],
  [255, 255, 100],
];

function rgbToHex(color: number[]): string {
  return `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
}

function rgbToRgba(color: number[], alpha: number): string {
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`;
}

export default function ZoneEditor({ cameraId, cameraName, onClose }: ZoneEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [snapshotSize, setSnapshotSize] = useState({ width: 0, height: 0 });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Existing zones
  const [zones, setZones] = useState<Zone[]>([]);

  // Drawing state
  const [currentPoints, setCurrentPoints] = useState<Point[]>([]);
  const [isDrawing, setIsDrawing] = useState(false);
  const [zoneName, setZoneName] = useState("");
  const [threshold, setThreshold] = useState(5.0);
  const [hoverPoint, setHoverPoint] = useState<Point | null>(null);

  // Load snapshot and existing zones
  useEffect(() => {
    loadData();
  }, [cameraId]);

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const [snap, zoneList] = await Promise.all([
        api.streams.snapshot(cameraId),
        api.zones.list(cameraId),
      ]);
      setSnapshot(snap.image);
      setSnapshotSize({ width: snap.width, height: snap.height });
      setZones(zoneList);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load snapshot";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  // Canvas rendering
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !snapshot) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const img = new Image();
    img.onload = () => {
      canvas.width = img.width;
      canvas.height = img.height;
      ctx.drawImage(img, 0, 0);

      // Draw existing zones
      for (const zone of zones) {
        const color = zone.color || [0, 255, 255];
        ctx.beginPath();
        zone.points.forEach((p, i) => {
          const px = p[0] * canvas.width;
          const py = p[1] * canvas.height;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.closePath();
        ctx.fillStyle = rgbToRgba(color, 0.15);
        ctx.fill();
        ctx.strokeStyle = rgbToHex(color);
        ctx.lineWidth = 2;
        ctx.stroke();

        // Zone label
        if (zone.points.length > 0) {
          const cx = zone.points.reduce((s, p) => s + p[0], 0) / zone.points.length * canvas.width;
          const cy = zone.points.reduce((s, p) => s + p[1], 0) / zone.points.length * canvas.height;
          ctx.font = "bold 13px system-ui";
          ctx.fillStyle = rgbToHex(color);
          ctx.textAlign = "center";
          ctx.fillText(zone.name, cx, cy);
        }
      }

      // Draw current polygon being drawn
      if (currentPoints.length > 0) {
        const drawColor = ZONE_COLORS[zones.length % ZONE_COLORS.length];
        ctx.beginPath();
        currentPoints.forEach((p, i) => {
          const px = p.x * canvas.width;
          const py = p.y * canvas.height;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });

        // Show line to hover position
        if (hoverPoint && isDrawing) {
          ctx.lineTo(hoverPoint.x * canvas.width, hoverPoint.y * canvas.height);
        }

        if (currentPoints.length >= 3) {
          // Show closing line hint
          ctx.setLineDash([5, 5]);
          ctx.lineTo(currentPoints[0].x * canvas.width, currentPoints[0].y * canvas.height);
          ctx.setLineDash([]);
        }

        ctx.strokeStyle = rgbToHex(drawColor);
        ctx.lineWidth = 2;
        ctx.stroke();

        // Fill if we have enough points
        if (currentPoints.length >= 3) {
          ctx.beginPath();
          currentPoints.forEach((p, i) => {
            const px = p.x * canvas.width;
            const py = p.y * canvas.height;
            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
          });
          ctx.closePath();
          ctx.fillStyle = rgbToRgba(drawColor, 0.1);
          ctx.fill();
        }

        // Draw vertices
        currentPoints.forEach((p, i) => {
          const px = p.x * canvas.width;
          const py = p.y * canvas.height;
          ctx.beginPath();
          ctx.arc(px, py, i === 0 ? 7 : 5, 0, Math.PI * 2);
          ctx.fillStyle = i === 0 ? rgbToRgba(drawColor, 0.8) : rgbToHex(drawColor);
          ctx.fill();
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        });

        // "Click first point to close" hint
        if (currentPoints.length >= 3) {
          const fp = currentPoints[0];
          ctx.beginPath();
          ctx.arc(fp.x * canvas.width, fp.y * canvas.height, 10, 0, Math.PI * 2);
          ctx.strokeStyle = rgbToRgba(drawColor, 0.5);
          ctx.lineWidth = 2;
          ctx.setLineDash([3, 3]);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    };
    img.src = `data:image/jpeg;base64,${snapshot}`;
  }, [snapshot, zones, currentPoints, hoverPoint, isDrawing]);

  useEffect(() => {
    draw();
  }, [draw]);

  function handleCanvasClick(e: React.MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const x = (e.clientX - rect.left) * scaleX / canvas.width;
    const y = (e.clientY - rect.top) * scaleY / canvas.height;

    if (!isDrawing) {
      setIsDrawing(true);
      setCurrentPoints([{ x, y }]);
      return;
    }

    // Check if clicking near first point to close
    if (currentPoints.length >= 3) {
      const first = currentPoints[0];
      const dist = Math.sqrt((x - first.x) ** 2 + (y - first.y) ** 2);
      if (dist < 0.02) {
        // Close the polygon — ready to save
        setIsDrawing(false);
        return;
      }
    }

    setCurrentPoints([...currentPoints, { x, y }]);
  }

  function handleCanvasMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!isDrawing) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const x = (e.clientX - rect.left) * scaleX / canvas.width;
    const y = (e.clientY - rect.top) * scaleY / canvas.height;
    setHoverPoint({ x, y });
  }

  function handleUndo() {
    if (currentPoints.length > 0) {
      setCurrentPoints(currentPoints.slice(0, -1));
      if (currentPoints.length <= 1) {
        setIsDrawing(false);
      }
    }
  }

  function handleClearDraw() {
    setCurrentPoints([]);
    setIsDrawing(false);
  }

  async function handleSaveZone() {
    if (currentPoints.length < 3) return;
    if (!zoneName.trim()) {
      setZoneName(`Zone-${zones.length + 1}`);
    }

    setSaving(true);
    try {
      const name = zoneName.trim() || `Zone-${zones.length + 1}`;
      const color = ZONE_COLORS[zones.length % ZONE_COLORS.length];
      await api.zones.create({
        name,
        points: currentPoints.map((p) => ({ x: Math.round(p.x * 10000) / 10000, y: Math.round(p.y * 10000) / 10000 })),
        threshold_sec: threshold,
        color,
        camera_ids: [cameraId],
      });
      setCurrentPoints([]);
      setIsDrawing(false);
      setZoneName("");
      // Reload zones
      const zoneList = await api.zones.list(cameraId);
      setZones(zoneList);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to save zone";
      setError(message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteZone(zoneId: number) {
    try {
      await api.zones.delete(zoneId);
      const zoneList = await api.zones.list(cameraId);
      setZones(zoneList);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to delete zone";
      setError(message);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fade-in">
      <div className="bg-slate-900 border border-slate-700/40 rounded-2xl shadow-2xl w-[95vw] max-w-5xl max-h-[90vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-700/30">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-violet-500/10 flex items-center justify-center">
              <Pentagon className="w-4 h-4 text-violet-400" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-slate-100">ROI Zone Editor</h2>
              <p className="text-[10px] text-slate-500">{cameraName}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-slate-800 transition-colors text-slate-500 hover:text-slate-300"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 flex overflow-hidden">
          {/* Canvas area */}
          <div className="flex-1 relative bg-black/30 flex items-center justify-center p-4" ref={containerRef}>
            {loading ? (
              <div className="flex flex-col items-center gap-3">
                <Loader2 className="w-6 h-6 text-slate-500 animate-spin" />
                <p className="text-xs text-slate-600">Capturing frame...</p>
              </div>
            ) : error && !snapshot ? (
              <div className="text-center">
                <p className="text-sm text-red-400">{error}</p>
                <p className="text-xs text-slate-600 mt-1">Make sure the camera source is accessible</p>
              </div>
            ) : (
              <div className="relative w-full h-full flex items-center justify-center">
                <canvas
                  ref={canvasRef}
                  onClick={handleCanvasClick}
                  onMouseMove={handleCanvasMouseMove}
                  className={cn(
                    "max-w-full max-h-full rounded-lg shadow-lg",
                    isDrawing ? "cursor-crosshair" : "cursor-pointer"
                  )}
                  style={{ objectFit: "contain" }}
                />

                {/* Drawing instructions overlay */}
                {!isDrawing && currentPoints.length === 0 && (
                  <div className="absolute bottom-6 left-1/2 -translate-x-1/2 bg-slate-900/90 backdrop-blur-sm border border-slate-700/30 rounded-xl px-4 py-2.5 flex items-center gap-2">
                    <MousePointer className="w-3.5 h-3.5 text-violet-400" />
                    <span className="text-[11px] text-slate-400">
                      Click on the image to start drawing a zone
                    </span>
                  </div>
                )}

                {isDrawing && (
                  <div className="absolute bottom-6 left-1/2 -translate-x-1/2 bg-slate-900/90 backdrop-blur-sm border border-slate-700/30 rounded-xl px-4 py-2.5 flex items-center gap-2">
                    <span className="text-[11px] text-slate-400">
                      {currentPoints.length < 3
                        ? `Click to add points (${currentPoints.length}/3 min)`
                        : "Click first point to close, or keep adding corners"}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Sidebar */}
          <div className="w-64 border-l border-slate-700/30 flex flex-col bg-slate-900/50">
            {/* Drawing controls */}
            <div className="p-4 border-b border-slate-700/30 space-y-3">
              <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                {currentPoints.length >= 3 && !isDrawing ? "Save Zone" : "Draw Zone"}
              </h3>

              {currentPoints.length >= 3 && !isDrawing && (
                <>
                  <input
                    className="input w-full text-xs"
                    placeholder="Zone name"
                    value={zoneName}
                    onChange={(e) => setZoneName(e.target.value)}
                  />
                  <div>
                    <label className="text-[10px] text-slate-500 block mb-1">Dwell threshold (sec)</label>
                    <input
                      type="number"
                      className="input w-full text-xs"
                      value={threshold}
                      min={0.5}
                      step={0.5}
                      onChange={(e) => setThreshold(parseFloat(e.target.value) || 5)}
                    />
                  </div>
                  <button
                    onClick={handleSaveZone}
                    disabled={saving}
                    className="btn-primary w-full text-xs justify-center"
                  >
                    {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                    {saving ? "Saving..." : "Save Zone"}
                  </button>
                </>
              )}

              <div className="flex gap-2">
                {currentPoints.length > 0 && (
                  <>
                    <button
                      onClick={handleUndo}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-[11px] text-slate-400 bg-slate-800/40 hover:bg-slate-800/60 transition-colors"
                    >
                      <Undo2 className="w-3 h-3" /> Undo
                    </button>
                    <button
                      onClick={handleClearDraw}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-[11px] text-red-400 bg-red-500/[0.06] hover:bg-red-500/10 transition-colors"
                    >
                      <Trash2 className="w-3 h-3" /> Clear
                    </button>
                  </>
                )}
              </div>

              {currentPoints.length > 0 && (
                <p className="text-[10px] text-slate-600">
                  {currentPoints.length} point{currentPoints.length !== 1 ? "s" : ""} placed
                </p>
              )}
            </div>

            {/* Existing zones */}
            <div className="flex-1 overflow-y-auto p-4 space-y-2">
              <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">
                Zones ({zones.length})
              </h3>
              {zones.length === 0 && (
                <p className="text-[11px] text-slate-600">No zones defined yet</p>
              )}
              {zones.map((zone) => (
                <div
                  key={zone.zone_id}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-800/30 border border-slate-700/20"
                >
                  <div
                    className="w-3 h-3 rounded-full shrink-0"
                    style={{ backgroundColor: rgbToHex(zone.color || [0, 255, 255]) }}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-slate-300 font-medium truncate">{zone.name}</p>
                    <p className="text-[10px] text-slate-600">
                      {zone.points.length} pts · {zone.threshold_sec}s
                    </p>
                  </div>
                  <button
                    onClick={() => handleDeleteZone(zone.zone_id)}
                    className="p-1 rounded text-slate-600 hover:text-red-400 transition-colors shrink-0"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>

        {error && snapshot && (
          <div className="px-5 py-2 bg-red-500/[0.06] border-t border-red-500/15">
            <p className="text-[11px] text-red-400">{error}</p>
          </div>
        )}
      </div>
    </div>
  );
}
