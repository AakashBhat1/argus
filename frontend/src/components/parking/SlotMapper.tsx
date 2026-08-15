"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import {
  X,
  Save,
  Trash2,
  Undo2,
  MousePointer,
  Loader2,
  Grid,
  Eye,
  AlertTriangle,
  ParkingSquare,
  Sparkles,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Point {
  x: number;
  y: number;
}

export interface SlotItem {
  space_id: string;
  polygon: Point[]; // Exactly 4 points normalized [0, 1]
}

interface SlotPreviewResult {
  space_id: string;
  occupied: boolean;
  score: number;
  source: string;
}

interface SlotMapperProps {
  cameraId: string;
  cameraName: string;
  onClose: () => void;
  onSaved?: () => void;
}

export default function SlotMapper({ cameraId, cameraName, onClose, onSaved }: SlotMapperProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [snapshotSize, setSnapshotSize] = useState({ width: 0, height: 0 });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [conflictBays, setConflictBays] = useState<string[]>([]);

  // Slot collection state
  const [slots, setSlots] = useState<SlotItem[]>([]);
  const [selectedSlotIndex, setSelectedSlotIndex] = useState<number | null>(null);
  const [previewResults, setPreviewResults] = useState<Record<string, SlotPreviewResult>>({});

  // Drawing state (for current quad being drawn)
  const [currentPoints, setCurrentPoints] = useState<Point[]>([]);
  const [isDrawing, setIsDrawing] = useState(false);
  const [hoverPoint, setHoverPoint] = useState<Point | null>(null);

  // Grid replication state
  const [prefix, setPrefix] = useState("P-");
  const [replicateCount, setReplicateCount] = useState(5);
  const [stepDirection, setStepDirection] = useState<"horizontal" | "vertical">("horizontal");

  // Load snapshot & existing slots
  useEffect(() => {
    loadData();
  }, [cameraId]);

  async function loadData() {
    setLoading(true);
    setError(null);
    setConflictBays([]);
    try {
      const [snap, slotList] = await Promise.all([
        api.streams.snapshot(cameraId),
        api.parking.slots(cameraId),
      ]);
      setSnapshot(snap.image);
      setSnapshotSize({ width: snap.width, height: snap.height });

      const loadedSlots: SlotItem[] = slotList.map((s) => ({
        space_id: s.space_id,
        polygon: (s.polygon || []).map((pt) => ({ x: pt[0], y: pt[1] })),
      }));
      setSlots(loadedSlots);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load snapshot or slots";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  // Draw canvas
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

      // Render existing slots
      slots.forEach((slot, index) => {
        if (slot.polygon.length < 4) return;

        const isSelected = selectedSlotIndex === index;
        const hasConflict = conflictBays.includes(slot.space_id);
        const preview = previewResults[slot.space_id];

        ctx.beginPath();
        slot.polygon.forEach((p, i) => {
          const px = p.x * canvas.width;
          const py = p.y * canvas.height;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.closePath();

        // Color coding
        let strokeColor = "#3b82f6"; // blue default
        let fillColor = "rgba(59, 130, 246, 0.15)";

        if (hasConflict) {
          strokeColor = "#ef4444";
          fillColor = "rgba(239, 68, 68, 0.3)";
        } else if (isSelected) {
          strokeColor = "#eab308"; // yellow
          fillColor = "rgba(234, 179, 8, 0.25)";
        } else if (preview) {
          if (preview.occupied) {
            strokeColor = "#ef4444"; // red for occupied
            fillColor = "rgba(239, 68, 68, 0.2)";
          } else {
            strokeColor = "#22c55e"; // green for free
            fillColor = "rgba(34, 197, 94, 0.15)";
          }
        }

        ctx.fillStyle = fillColor;
        ctx.fill();
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = isSelected ? 3 : 2;
        ctx.stroke();

        // Label
        const cx = (slot.polygon.reduce((sum, p) => sum + p.x, 0) / 4) * canvas.width;
        const cy = (slot.polygon.reduce((sum, p) => sum + p.y, 0) / 4) * canvas.height;

        ctx.font = "bold 12px system-ui";
        ctx.fillStyle = "#ffffff";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";

        let label = slot.space_id;
        if (preview) {
          label += ` (${(preview.score * 100).toFixed(0)}%)`;
        }
        ctx.fillText(label, cx, cy);
      });

      // Render currently drawn quad
      if (currentPoints.length > 0) {
        ctx.beginPath();
        currentPoints.forEach((p, i) => {
          const px = p.x * canvas.width;
          const py = p.y * canvas.height;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });

        if (hoverPoint && isDrawing && currentPoints.length < 4) {
          ctx.lineTo(hoverPoint.x * canvas.width, hoverPoint.y * canvas.height);
        }

        if (currentPoints.length === 4) {
          ctx.closePath();
          ctx.fillStyle = "rgba(168, 85, 247, 0.2)"; // purple fill
          ctx.fill();
        }

        ctx.strokeStyle = "#a855f7";
        ctx.lineWidth = 2;
        ctx.stroke();

        // Draw vertex dots
        currentPoints.forEach((p, i) => {
          const px = p.x * canvas.width;
          const py = p.y * canvas.height;
          ctx.beginPath();
          ctx.arc(px, py, 5, 0, Math.PI * 2);
          ctx.fillStyle = "#a855f7";
          ctx.fill();
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        });
      }
    };
    img.src = `data:image/jpeg;base64,${snapshot}`;
  }, [snapshot, slots, currentPoints, hoverPoint, isDrawing, selectedSlotIndex, conflictBays, previewResults]);

  useEffect(() => {
    draw();
  }, [draw]);

  function handleCanvasClick(e: React.MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const x = Math.round(((e.clientX - rect.left) * scaleX / canvas.width) * 10000) / 10000;
    const y = Math.round(((e.clientY - rect.top) * scaleY / canvas.height) * 10000) / 10000;

    if (!isDrawing) {
      // Check if clicking inside an existing slot to select it
      let clickedIdx: number | null = null;
      for (let i = 0; i < slots.length; i++) {
        if (pointInPolygon({ x, y }, slots[i].polygon)) {
          clickedIdx = i;
          break;
        }
      }

      if (clickedIdx !== null) {
        setSelectedSlotIndex(clickedIdx);
      } else {
        setSelectedSlotIndex(null);
        setIsDrawing(true);
        setCurrentPoints([{ x, y }]);
      }
      return;
    }

    const nextPoints = [...currentPoints, { x, y }];
    if (nextPoints.length === 4) {
      // Auto-add slot with generated space_id
      const nextNum = getNextSlotNumber();
      const newSpaceId = `${prefix}${String(nextNum).padStart(2, "0")}`;
      const newSlot: SlotItem = { space_id: newSpaceId, polygon: nextPoints };
      setSlots([...slots, newSlot]);
      setSelectedSlotIndex(slots.length);
      setCurrentPoints([]);
      setIsDrawing(false);
    } else {
      setCurrentPoints(nextPoints);
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

  function handleCanvasMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!isDrawing) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const x = Math.round(((e.clientX - rect.left) * scaleX / canvas.width) * 10000) / 10000;
    const y = Math.round(((e.clientY - rect.top) * scaleY / canvas.height) * 10000) / 10000;
    setHoverPoint({ x, y });
  }

  function getNextSlotNumber(): number {
    let maxNum = 0;
    slots.forEach((s) => {
      const match = s.space_id.match(/\d+/);
      if (match) {
        const n = parseInt(match[0], 10);
        if (n > maxNum) maxNum = n;
      }
    });
    return maxNum + 1;
  }

  function handleReplicate() {
    const baseIdx = selectedSlotIndex !== null ? selectedSlotIndex : slots.length - 1;
    if (baseIdx < 0 || baseIdx >= slots.length) return;

    const baseSlot = slots[baseIdx];
    const poly = baseSlot.polygon;
    if (poly.length !== 4) return;

    // Vector calculation: horizontal (P0 -> P1) or vertical (P0 -> P3)
    let dx = 0;
    let dy = 0;
    if (stepDirection === "horizontal") {
      dx = poly[1].x - poly[0].x;
      dy = poly[1].y - poly[0].y;
    } else {
      dx = poly[3].x - poly[0].x;
      dy = poly[3].y - poly[0].y;
    }

    let startNum = getNextSlotNumber();
    const newSlots: SlotItem[] = [];

    for (let k = 1; k <= replicateCount; k++) {
      const newSpaceId = `${prefix}${String(startNum++).padStart(2, "0")}`;
      const newPoly = poly.map((p) => ({
        x: Math.round((p.x + dx * k) * 10000) / 10000,
        y: Math.round((p.y + dy * k) * 10000) / 10000,
      }));
      newSlots.push({ space_id: newSpaceId, polygon: newPoly });
    }

    setSlots([...slots, ...newSlots]);
  }

  function handleDeleteSelected() {
    if (selectedSlotIndex === null) return;
    setSlots(slots.filter((_, idx) => idx !== selectedSlotIndex));
    setSelectedSlotIndex(null);
  }

  function handleUndo() {
    if (currentPoints.length > 0) {
      setCurrentPoints(currentPoints.slice(0, -1));
    }
  }

  function handleClearAll() {
    setSlots([]);
    setCurrentPoints([]);
    setIsDrawing(false);
    setSelectedSlotIndex(null);
    setPreviewResults({});
    setConflictBays([]);
  }

  async function handlePreview() {
    if (slots.length === 0) return;
    setPreviewing(true);
    setError(null);
    try {
      const payload = slots.map((s) => ({
        space_id: s.space_id,
        polygon: s.polygon.map((p) => [p.x, p.y]),
      }));
      const res = await api.parking.previewSlots(cameraId, payload);
      const dict: Record<string, SlotPreviewResult> = {};
      (res.slots || []).forEach((item) => {
        dict[item.space_id] = item;
      });
      setPreviewResults(dict);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to generate live preview";
      setError(message);
    } finally {
      setPreviewing(false);
    }
  }

  async function handleSave() {
    if (slots.length === 0) return;
    setSaving(true);
    setError(null);
    setConflictBays([]);

    try {
      const payload = slots.map((s) => ({
        space_id: s.space_id,
        polygon: s.polygon.map((p) => [p.x, p.y]),
      }));

      await api.parking.saveSlots(cameraId, payload);
      if (onSaved) onSaved();
      onClose();
    } catch (err: any) {
      let msg = "Failed to save slots";
      if (err instanceof Error) msg = err.message;
      setError(msg);

      // Handle 409 responses gracefully according to contract addendum
      if (msg.includes("409") || msg.includes("Cannot replace slots") || msg.includes("Space IDs already belong")) {
        const matches = msg.match(/[A-Z0-9_-]{2,}/g);
        if (matches) {
          const matchedBays = matches.filter((b) => slots.some((s) => s.space_id === b));
          setConflictBays(matchedBays);
        }
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="bg-slate-900 border border-slate-700/40 rounded-2xl shadow-2xl w-[95vw] max-w-6xl max-h-[90vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-700/30">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-violet-500/10 flex items-center justify-center">
              <ParkingSquare className="w-4 h-4 text-violet-400" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Parking Slot Mapper</h2>
              <p className="text-[10px] text-slate-500">{cameraName} ({slots.length} bays mapped)</p>
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
          {/* Canvas Viewport */}
          <div className="flex-1 relative bg-black/40 flex items-center justify-center p-4" ref={containerRef}>
            {loading ? (
              <div className="flex flex-col items-center gap-3">
                <Loader2 className="w-6 h-6 text-slate-500 animate-spin" />
                <p className="text-xs text-slate-600">Capturing camera snapshot...</p>
              </div>
            ) : error && !snapshot ? (
              <div className="text-center">
                <p className="text-sm text-red-400">{error}</p>
                <p className="text-xs text-slate-600 mt-1">Check camera connectivity</p>
              </div>
            ) : (
              <div className="relative w-full h-full flex items-center justify-center">
                <canvas
                  ref={canvasRef}
                  onClick={handleCanvasClick}
                  onMouseMove={handleCanvasMouseMove}
                  className={cn(
                    "max-w-full max-h-full rounded-lg shadow-lg border border-slate-800",
                    isDrawing ? "cursor-crosshair" : "cursor-pointer"
                  )}
                  style={{ objectFit: "contain" }}
                />

                {/* Instruction banner */}
                {!isDrawing && (
                  <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-slate-900/90 backdrop-blur-sm border border-slate-700/30 rounded-xl px-4 py-2 flex items-center gap-2">
                    <MousePointer className="w-3.5 h-3.5 text-violet-400" />
                    <span className="text-[11px] text-slate-300">
                      Click 4 points on the snapshot to map a bay quad, or click an existing bay to select
                    </span>
                  </div>
                )}

                {isDrawing && (
                  <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-slate-900/90 backdrop-blur-sm border border-slate-700/30 rounded-xl px-4 py-2 flex items-center gap-2">
                    <span className="text-[11px] text-violet-300 font-medium">
                      Drawing bay ({currentPoints.length}/4 corners placed)
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Sidebar Controls */}
          <div className="w-80 border-l border-slate-700/30 flex flex-col bg-slate-900/60 overflow-y-auto">
            {/* Action Bar */}
            <div className="p-4 border-b border-slate-700/30 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                  Toolbar
                </h3>
                {slots.length > 0 && (
                  <button
                    onClick={handlePreview}
                    disabled={previewing}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-violet-500/10 text-violet-300 border border-violet-500/20 text-xs hover:bg-violet-500/20 transition-colors"
                  >
                    {previewing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Eye className="w-3 h-3" />}
                    Preview Scores
                  </button>
                )}
              </div>

              {/* Grid Replicate Tool */}
              <div className="p-3 bg-slate-800/40 rounded-xl border border-slate-700/20 space-y-2.5">
                <div className="flex items-center gap-2">
                  <Grid className="w-4 h-4 text-violet-400" />
                  <span className="text-xs font-semibold text-slate-200">Grid Replicate Tool</span>
                </div>
                <p className="text-[10px] text-slate-400 leading-relaxed">
                  Select a bay and duplicate it along its edge vector to generate a row.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-slate-500 block mb-1">Prefix</label>
                    <input
                      type="text"
                      value={prefix}
                      onChange={(e) => setPrefix(e.target.value)}
                      className="input w-full text-xs py-1"
                      placeholder="P-"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-slate-500 block mb-1">Count</label>
                    <input
                      type="number"
                      value={replicateCount}
                      min={1}
                      max={30}
                      onChange={(e) => setReplicateCount(parseInt(e.target.value, 10) || 1)}
                      className="input w-full text-xs py-1"
                    />
                  </div>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => setStepDirection("horizontal")}
                    className={cn(
                      "flex-1 py-1 rounded text-[11px] font-medium border transition-colors",
                      stepDirection === "horizontal"
                        ? "bg-violet-500/20 text-violet-300 border-violet-500/40"
                        : "bg-slate-800 text-slate-400 border-slate-700"
                    )}
                  >
                    Horizontal
                  </button>
                  <button
                    onClick={() => setStepDirection("vertical")}
                    className={cn(
                      "flex-1 py-1 rounded text-[11px] font-medium border transition-colors",
                      stepDirection === "vertical"
                        ? "bg-violet-500/20 text-violet-300 border-violet-500/40"
                        : "bg-slate-800 text-slate-400 border-slate-700"
                    )}
                  >
                    Vertical
                  </button>
                </div>

                <button
                  onClick={handleReplicate}
                  disabled={slots.length === 0}
                  className="btn-primary w-full text-xs justify-center py-1.5"
                >
                  <Sparkles className="w-3.5 h-3.5" /> Replicate Bays
                </button>
              </div>

              {/* Edit Operations */}
              <div className="flex items-center gap-2">
                {isDrawing && currentPoints.length > 0 && (
                  <button
                    onClick={handleUndo}
                    className="flex-1 flex items-center justify-center gap-1 py-1.5 rounded text-xs bg-slate-800 text-slate-300 hover:bg-slate-700"
                  >
                    <Undo2 className="w-3 h-3" /> Undo Point
                  </button>
                )}
                {selectedSlotIndex !== null && (
                  <button
                    onClick={handleDeleteSelected}
                    className="flex-1 flex items-center justify-center gap-1 py-1.5 rounded text-xs bg-red-500/10 text-red-400 hover:bg-red-500/20"
                  >
                    <Trash2 className="w-3 h-3" /> Delete Selected
                  </button>
                )}
                {slots.length > 0 && (
                  <button
                    onClick={handleClearAll}
                    className="px-2 py-1.5 rounded text-xs text-slate-500 hover:text-red-400"
                  >
                    Clear All
                  </button>
                )}
              </div>
            </div>

            {/* Mapped Slot List */}
            <div className="flex-1 p-4 space-y-2 overflow-y-auto">
              <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
                Mapped Bays ({slots.length})
              </h3>
              {slots.length === 0 ? (
                <p className="text-xs text-slate-500 italic py-2">No bays mapped yet. Click on the snapshot to start.</p>
              ) : (
                slots.map((s, idx) => {
                  const isSelected = selectedSlotIndex === idx;
                  const isConflict = conflictBays.includes(s.space_id);
                  const preview = previewResults[s.space_id];

                  return (
                    <div
                      key={s.space_id + idx}
                      onClick={() => setSelectedSlotIndex(idx)}
                      className={cn(
                        "p-2.5 rounded-lg border text-xs flex items-center justify-between cursor-pointer transition-all",
                        isConflict
                          ? "bg-red-500/10 border-red-500/40 text-red-300"
                          : isSelected
                          ? "bg-violet-500/10 border-violet-500/40 text-violet-200"
                          : "bg-slate-800/30 border-slate-700/20 text-slate-300 hover:bg-slate-800/60"
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{s.space_id}</span>
                        {preview && (
                          <span
                            className={cn(
                              "text-[10px] px-1.5 py-0.5 rounded font-mono font-semibold",
                              preview.occupied
                                ? "bg-red-500/20 text-red-400"
                                : "bg-emerald-500/20 text-emerald-400"
                            )}
                          >
                            {(preview.score * 100).toFixed(0)}% {preview.occupied ? "Occupied" : "Free"}
                          </span>
                        )}
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setSlots(slots.filter((_, i) => i !== idx));
                        }}
                        className="p-1 text-slate-500 hover:text-red-400"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  );
                })
              )}
            </div>

            {/* Error & Save Bar */}
            <div className="p-4 border-t border-slate-700/30 bg-slate-900 space-y-2">
              {error && (
                <div className="p-2.5 rounded-lg bg-red-500/10 border border-red-500/20 text-red-300 text-xs flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                  <div className="flex-1">
                    <p className="font-semibold text-[11px]">Save Rejection (409 / Conflict)</p>
                    <p className="text-[10px] text-red-300/80 leading-tight mt-0.5">{error}</p>
                    {conflictBays.length > 0 && (
                      <p className="text-[10px] text-red-200 font-mono mt-1">
                        Offending bays: {conflictBays.join(", ")}
                      </p>
                    )}
                  </div>
                </div>
              )}

              <div className="flex gap-2">
                <button
                  onClick={onClose}
                  className="flex-1 py-2 rounded-lg border border-slate-700 text-xs text-slate-300 hover:bg-slate-800 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving || slots.length === 0}
                  className="flex-1 btn-primary text-xs justify-center py-2"
                >
                  {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                  {saving ? "Saving..." : "Save Layout"}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
