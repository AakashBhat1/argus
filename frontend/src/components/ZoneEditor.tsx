"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import {
  X, Save, Trash2, Undo2, MousePointer, Loader2, Pentagon, Ruler, Shield, Clock, Check, ScanLine,
} from "lucide-react";
import { api, GATE_ROLES, type Zone, type ZoneType, type ArmedSchedule, type CameraCalibration, type CameraRole } from "@/lib/api";
import { cn, zoneTypeHex } from "@/lib/utils";

interface Point {
  x: number;
  y: number;
}

interface ZoneEditorProps {
  cameraId: string;
  cameraName: string;
  onClose: () => void;
}

type Tab = "zones" | "calibration" | "gate";

const GATE_COLOR = [251, 146, 60]; // orange-400

const ZONE_TYPES: { value: ZoneType; label: string; hint: string }[] = [
  { value: "restricted", label: "Restricted", hint: "Nobody should be here while armed" },
  { value: "perimeter", label: "Perimeter", hint: "Fence line — appearing here first is suspicious" },
  { value: "entrance", label: "Entrance", hint: "Door / gate — arrivals here are legitimate" },
  { value: "driveway", label: "Driveway", hint: "Cars expected; walking to/from a car is normal" },
  { value: "parking", label: "Parking", hint: "Loitering matters more than presence" },
  { value: "public", label: "Public", hint: "Footpath — presence carries no risk" },
];

const DAY_LABELS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
const CLASS_OPTIONS = ["person", "car", "truck", "bus", "motorcycle", "bicycle"];

const DEFAULT_SCHEDULE: ArmedSchedule = {
  mode: "always",
  windows: [{ start: "22:00", end: "06:00", days: [0, 1, 2, 3, 4, 5, 6] }],
  tz: typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "UTC",
};

function hexToRgb(hex: string): number[] {
  const m = hex.replace("#", "");
  return [parseInt(m.slice(0, 2), 16), parseInt(m.slice(2, 4), 16), parseInt(m.slice(4, 6), 16)];
}

function rgba(color: number[], alpha: number): string {
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`;
}

export default function ZoneEditor({ cameraId, cameraName, onClose }: ZoneEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const loadedSnapshotRef = useRef<string | null>(null);

  const [tab, setTab] = useState<Tab>("zones");
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Zones
  const [zones, setZones] = useState<Zone[]>([]);
  const [currentPoints, setCurrentPoints] = useState<Point[]>([]);
  const [isDrawing, setIsDrawing] = useState(false);
  const [hoverPoint, setHoverPoint] = useState<Point | null>(null);
  const [zoneName, setZoneName] = useState("");
  const [threshold, setThreshold] = useState(5.0);
  const [zoneType, setZoneType] = useState<ZoneType>("restricted");
  const [schedule, setSchedule] = useState<ArmedSchedule>(DEFAULT_SCHEDULE);
  const [allowed, setAllowed] = useState<string[]>([]);

  // Calibration
  const [hfov, setHfov] = useState(84);
  const [calPoints, setCalPoints] = useState<Point[]>([]);
  const [rectWidth, setRectWidth] = useState(2.5);
  const [rectLength, setRectLength] = useState(5.0);
  const [existingCal, setExistingCal] = useState<CameraCalibration | null>(null);

  // Gate (plate OCR)
  const [gatePoints, setGatePoints] = useState<Point[]>([]);
  const [gateDrawing, setGateDrawing] = useState(false);
  const [savedGate, setSavedGate] = useState<Point[]>([]);
  const [cameraRole, setCameraRole] = useState<CameraRole>("surveillance");
  const [gateRole, setGateRole] = useState<"gate_entry" | "gate_exit">("gate_entry");

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraId]);

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const [snap, zoneList, cam] = await Promise.all([
        api.streams.snapshot(cameraId),
        api.zones.list(cameraId),
        api.cameras.get(cameraId),
      ]);
      setSnapshot(snap.image);
      setZones(zoneList);
      const role = (cam.role as CameraRole) || "surveillance";
      setCameraRole(role);
      if (role === "gate_exit") setGateRole("gate_exit");
      if (Array.isArray(cam.gate_roi) && cam.gate_roi.length >= 3) {
        const pts = cam.gate_roi
          .filter((p): p is number[] => Array.isArray(p) && p.length >= 2)
          .map((p) => ({ x: p[0], y: p[1] }));
        setSavedGate(pts);
      }
      if (cam.calibration) {
        setExistingCal(cam.calibration);
        setHfov(cam.calibration.hfov_deg);
        if (cam.calibration.homography_image_points?.length >= 4) {
          setCalPoints(cam.calibration.homography_image_points.slice(0, 4).map((p) => ({ x: p[0], y: p[1] })));
          const wp = cam.calibration.homography_world_points;
          if (wp?.length >= 4) {
            setRectWidth(Math.abs(wp[1][0] - wp[0][0]) || 2.5);
            setRectLength(Math.abs(wp[2][1] - wp[1][1]) || 5.0);
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load snapshot");
    } finally {
      setLoading(false);
    }
  }

  // ── Canvas rendering ────────────────────────────────────────────────────────
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !snapshot) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const render = (img: HTMLImageElement) => {
      canvas.width = img.width;
      canvas.height = img.height;
      ctx.drawImage(img, 0, 0);
      const W = canvas.width;
      const H = canvas.height;

      // Existing zones
      for (const zone of zones) {
        const color = hexToRgb(zoneTypeHex(zone.zone_type));
        ctx.beginPath();
        zone.points.forEach((p, i) => (i === 0 ? ctx.moveTo(p[0] * W, p[1] * H) : ctx.lineTo(p[0] * W, p[1] * H)));
        ctx.closePath();
        ctx.fillStyle = rgba(color, zone.armed_now === false ? 0.06 : 0.15);
        ctx.fill();
        ctx.setLineDash(zone.armed_now === false ? [8, 6] : []);
        ctx.strokeStyle = rgba(color, 0.95);
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.setLineDash([]);
        const cx = (zone.points.reduce((s, p) => s + p[0], 0) / zone.points.length) * W;
        const cy = (zone.points.reduce((s, p) => s + p[1], 0) / zone.points.length) * H;
        ctx.font = "bold 13px system-ui";
        ctx.textAlign = "center";
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(15,23,42,0.9)";
        ctx.strokeText(`${zone.name} · ${zone.zone_type}`, cx, cy);
        ctx.fillStyle = rgba(color, 1);
        ctx.fillText(`${zone.name} · ${zone.zone_type}`, cx, cy);
      }

      if (tab === "zones" && currentPoints.length > 0) {
        const color = hexToRgb(zoneTypeHex(zoneType));
        ctx.beginPath();
        currentPoints.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x * W, p.y * H) : ctx.lineTo(p.x * W, p.y * H)));
        if (hoverPoint && isDrawing) ctx.lineTo(hoverPoint.x * W, hoverPoint.y * H);
        if (currentPoints.length >= 3) {
          ctx.setLineDash([5, 5]);
          ctx.lineTo(currentPoints[0].x * W, currentPoints[0].y * H);
          ctx.setLineDash([]);
        }
        ctx.strokeStyle = rgba(color, 1);
        ctx.lineWidth = 2;
        ctx.stroke();
        if (currentPoints.length >= 3) {
          ctx.beginPath();
          currentPoints.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x * W, p.y * H) : ctx.lineTo(p.x * W, p.y * H)));
          ctx.closePath();
          ctx.fillStyle = rgba(color, 0.12);
          ctx.fill();
        }
        currentPoints.forEach((p, i) => {
          ctx.beginPath();
          ctx.arc(p.x * W, p.y * H, i === 0 ? 7 : 5, 0, Math.PI * 2);
          ctx.fillStyle = rgba(color, i === 0 ? 0.8 : 1);
          ctx.fill();
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        });
      }

      // Saved gate polygon (always visible so zones can be placed relative to it)
      const drawPoly = (pts: Point[], closed: boolean, alpha: number, dash: number[]) => {
        if (pts.length === 0) return;
        ctx.beginPath();
        pts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x * W, p.y * H) : ctx.lineTo(p.x * W, p.y * H)));
        if (closed) ctx.closePath();
        ctx.setLineDash(dash);
        ctx.strokeStyle = rgba(GATE_COLOR, 1);
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.setLineDash([]);
        if (closed && pts.length >= 3) {
          ctx.fillStyle = rgba(GATE_COLOR, alpha);
          ctx.fill();
        }
      };
      if (savedGate.length >= 3 && !(tab === "gate" && gatePoints.length > 0)) {
        drawPoly(savedGate, true, tab === "gate" ? 0.18 : 0.08, tab === "gate" ? [] : [6, 4]);
        const gx = (savedGate.reduce((s, p) => s + p.x, 0) / savedGate.length) * W;
        const gy = (savedGate.reduce((s, p) => s + p.y, 0) / savedGate.length) * H;
        ctx.font = "bold 12px system-ui";
        ctx.textAlign = "center";
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(15,23,42,0.9)";
        ctx.strokeText("GATE · plate OCR", gx, gy);
        ctx.fillStyle = rgba(GATE_COLOR, 1);
        ctx.fillText("GATE · plate OCR", gx, gy);
      }
      if (tab === "gate" && gatePoints.length > 0) {
        drawPoly(gatePoints, !gateDrawing && gatePoints.length >= 3, 0.15, []);
        if (hoverPoint && gateDrawing) {
          const last = gatePoints[gatePoints.length - 1];
          ctx.beginPath();
          ctx.moveTo(last.x * W, last.y * H);
          ctx.lineTo(hoverPoint.x * W, hoverPoint.y * H);
          ctx.strokeStyle = rgba(GATE_COLOR, 0.6);
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
        gatePoints.forEach((p, i) => {
          ctx.beginPath();
          ctx.arc(p.x * W, p.y * H, i === 0 ? 7 : 5, 0, Math.PI * 2);
          ctx.fillStyle = rgba(GATE_COLOR, 1);
          ctx.fill();
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        });
      }

      if (tab === "calibration") {
        const labels = ["near-left", "near-right", "far-right", "far-left"];
        if (calPoints.length >= 2) {
          ctx.beginPath();
          calPoints.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x * W, p.y * H) : ctx.lineTo(p.x * W, p.y * H)));
          if (calPoints.length === 4) ctx.closePath();
          ctx.strokeStyle = "rgba(34,211,238,1)";
          ctx.lineWidth = 2;
          ctx.setLineDash([6, 4]);
          ctx.stroke();
          ctx.setLineDash([]);
          if (calPoints.length === 4) {
            ctx.fillStyle = "rgba(34,211,238,0.12)";
            ctx.fill();
          }
        }
        calPoints.forEach((p, i) => {
          ctx.beginPath();
          ctx.arc(p.x * W, p.y * H, 6, 0, Math.PI * 2);
          ctx.fillStyle = "#22d3ee";
          ctx.fill();
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 1.5;
          ctx.stroke();
          ctx.font = "bold 12px system-ui";
          ctx.textAlign = "left";
          ctx.fillStyle = "#e0f2fe";
          ctx.fillText(`${i + 1} ${labels[i]}`, p.x * W + 9, p.y * H - 6);
        });
      }
    };

    if (imgRef.current && loadedSnapshotRef.current === snapshot) {
      render(imgRef.current);
      return;
    }
    const img = new Image();
    img.onload = () => {
      imgRef.current = img;
      loadedSnapshotRef.current = snapshot;
      render(img);
    };
    img.src = `data:image/jpeg;base64,${snapshot}`;
  }, [snapshot, zones, currentPoints, hoverPoint, isDrawing, tab, calPoints, zoneType, savedGate, gatePoints, gateDrawing]);

  useEffect(() => {
    draw();
  }, [draw]);

  function canvasPoint(e: React.MouseEvent<HTMLCanvasElement>): Point | null {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    };
  }

  function handleCanvasClick(e: React.MouseEvent<HTMLCanvasElement>) {
    const p = canvasPoint(e);
    if (!p) return;
    if (tab === "calibration") {
      setCalPoints((prev) => (prev.length >= 4 ? [p] : [...prev, p]));
      return;
    }
    if (tab === "gate") {
      if (!gateDrawing) {
        setGateDrawing(true);
        setGatePoints([p]);
        return;
      }
      if (gatePoints.length >= 3) {
        const first = gatePoints[0];
        if (Math.hypot(p.x - first.x, p.y - first.y) < 0.02) {
          setGateDrawing(false);
          return;
        }
      }
      setGatePoints([...gatePoints, p]);
      return;
    }
    if (!isDrawing) {
      setIsDrawing(true);
      setCurrentPoints([p]);
      return;
    }
    if (currentPoints.length >= 3) {
      const first = currentPoints[0];
      if (Math.hypot(p.x - first.x, p.y - first.y) < 0.02) {
        setIsDrawing(false);
        return;
      }
    }
    setCurrentPoints([...currentPoints, p]);
  }

  function handleCanvasMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!isDrawing && !gateDrawing) return;
    setHoverPoint(canvasPoint(e));
  }

  async function handleSaveGate() {
    if (gatePoints.length < 3) return;
    setSaving(true);
    setError(null);
    try {
      const gate_roi = gatePoints.map((p) => [Math.round(p.x * 10000) / 10000, Math.round(p.y * 10000) / 10000]);
      const cam = await api.cameras.update(cameraId, { role: gateRole, gate_roi });
      setSavedGate(gatePoints);
      setGatePoints([]);
      setGateDrawing(false);
      setCameraRole((cam.role as CameraRole) || gateRole);
      setNotice(`Gate saved — camera is now ${gateRole.replace("_", " ")}; plate OCR fires when a vehicle enters the polygon`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save gate");
    } finally {
      setSaving(false);
    }
  }

  async function handleClearGate() {
    setSaving(true);
    setError(null);
    try {
      await api.cameras.update(cameraId, { gate_roi: null });
      setSavedGate([]);
      setGatePoints([]);
      setGateDrawing(false);
      setNotice("Gate polygon removed — plate OCR is off for this camera");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove gate");
    } finally {
      setSaving(false);
    }
  }

  async function handleSetRole(role: CameraRole) {
    setSaving(true);
    setError(null);
    try {
      const cam = await api.cameras.update(cameraId, { role });
      setCameraRole((cam.role as CameraRole) || role);
      if (role === "gate_entry" || role === "gate_exit") setGateRole(role);
      setNotice(`Camera role set to ${role.replace("_", " ")}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to change role");
    } finally {
      setSaving(false);
    }
  }

  function handleUndo() {
    if (currentPoints.length > 0) {
      setCurrentPoints(currentPoints.slice(0, -1));
      if (currentPoints.length <= 1) setIsDrawing(false);
    }
  }

  function handleClearDraw() {
    setCurrentPoints([]);
    setIsDrawing(false);
  }

  async function handleSaveZone() {
    if (currentPoints.length < 3) return;
    setSaving(true);
    setError(null);
    try {
      const name = zoneName.trim() || `${ZONE_TYPES.find((z) => z.value === zoneType)?.label}-${zones.length + 1}`;
      await api.zones.create({
        name,
        points: currentPoints.map((p) => ({ x: Math.round(p.x * 10000) / 10000, y: Math.round(p.y * 10000) / 10000 })),
        threshold_sec: threshold,
        color: hexToRgb(zoneTypeHex(zoneType)),
        camera_ids: [cameraId],
        zone_type: zoneType,
        armed_schedule: schedule.mode === "schedule" ? schedule : { mode: schedule.mode, windows: [], tz: schedule.tz },
        allowed_classes: allowed,
      });
      setCurrentPoints([]);
      setIsDrawing(false);
      setZoneName("");
      setZones(await api.zones.list(cameraId));
      setNotice(`Zone "${name}" saved`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save zone");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteZone(zoneId: number) {
    try {
      await api.zones.delete(zoneId);
      setZones(await api.zones.list(cameraId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete zone");
    }
  }

  async function handleSaveCalibration(withHomography: boolean) {
    setSaving(true);
    setError(null);
    try {
      const calibration: CameraCalibration = {
        hfov_deg: hfov,
        homography_image_points: withHomography && calPoints.length === 4 ? calPoints.map((p) => [p.x, p.y]) : [],
        homography_world_points:
          withHomography && calPoints.length === 4
            ? [[0, 0], [rectWidth, 0], [rectWidth, rectLength], [0, rectLength]]
            : [],
      };
      const cam = await api.cameras.update(cameraId, { calibration });
      setExistingCal(cam.calibration ?? calibration);
      setNotice(withHomography && calPoints.length === 4 ? "Ground plane calibrated — distances are now metric" : "Field of view saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save calibration");
    } finally {
      setSaving(false);
    }
  }

  function toggleDay(d: number) {
    setSchedule((s) => {
      const w = s.windows[0] ?? { start: "22:00", end: "06:00", days: [] };
      const days = w.days.includes(d) ? w.days.filter((x) => x !== d) : [...w.days, d].sort();
      return { ...s, windows: [{ ...w, days }] };
    });
  }

  const readyToSave = currentPoints.length >= 3 && !isDrawing;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fade-in">
      <div className="bg-slate-900 border border-slate-700/40 rounded-2xl shadow-2xl w-[96vw] max-w-6xl max-h-[92vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700/30">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-violet-500/10 flex items-center justify-center">
              <Pentagon className="w-4 h-4 text-violet-400" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Scene Setup</h2>
              <p className="text-[10px] text-slate-500">{cameraName}</p>
            </div>
            <div className="ml-4 flex items-center gap-1 bg-slate-800/50 rounded-lg p-0.5">
              {(["zones", "gate", "calibration"] as Tab[]).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs transition-colors",
                    tab === t ? "bg-slate-700 text-slate-100" : "text-slate-500 hover:text-slate-300"
                  )}
                >
                  {t === "zones" ? <Shield className="w-3.5 h-3.5" /> : t === "gate" ? <ScanLine className="w-3.5 h-3.5" /> : <Ruler className="w-3.5 h-3.5" />}
                  {t === "zones" ? "Zones" : t === "gate" ? "Gate / plate OCR" : "Calibration"}
                </button>
              ))}
            </div>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-slate-800 transition-colors text-slate-500 hover:text-slate-300">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 flex overflow-hidden">
          <div className="flex-1 relative bg-black/30 flex items-center justify-center p-4">
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
                  className={cn("max-w-full max-h-full rounded-lg shadow-lg", isDrawing || gateDrawing || tab === "calibration" ? "cursor-crosshair" : "cursor-pointer")}
                  style={{ objectFit: "contain" }}
                />
                <div className="absolute bottom-6 left-1/2 -translate-x-1/2 bg-slate-900/90 backdrop-blur-sm border border-slate-700/30 rounded-xl px-4 py-2.5 flex items-center gap-2">
                  <MousePointer className="w-3.5 h-3.5 text-violet-400" />
                  <span className="text-[11px] text-slate-400">
                    {tab === "gate"
                      ? gatePoints.length === 0
                        ? savedGate.length >= 3
                          ? "Gate polygon is set. Click to draw a new one and replace it"
                          : "Click to outline the strip of road where the vehicle's number plate is visible"
                        : gateDrawing
                          ? gatePoints.length < 3
                            ? `Click to add corners (${gatePoints.length}/3 min)`
                            : "Click the first point to close, or keep adding corners"
                          : "Gate polygon closed — choose entry/exit and save on the right"
                      : tab === "calibration"
                      ? calPoints.length < 4
                        ? `Click ground rectangle corner ${calPoints.length + 1}/4 (near-left → near-right → far-right → far-left)`
                        : "Rectangle set — enter its real size and save"
                      : !isDrawing && currentPoints.length === 0
                        ? "Click on the image to start drawing a zone (use the foot position of people, not their heads)"
                        : isDrawing
                          ? currentPoints.length < 3
                            ? `Click to add points (${currentPoints.length}/3 min)`
                            : "Click first point to close, or keep adding corners"
                          : "Polygon closed — configure and save on the right"}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Sidebar */}
          <div className="w-80 border-l border-slate-700/30 flex flex-col bg-slate-900/50 overflow-y-auto">
            {tab === "zones" ? (
              <>
                <div className="p-4 border-b border-slate-700/30 space-y-3">
                  <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    {readyToSave ? "Configure zone" : "Draw zone"}
                  </h3>

                  <div>
                    <label className="text-[10px] text-slate-500 block mb-1">Zone type</label>
                    <div className="grid grid-cols-2 gap-1.5">
                      {ZONE_TYPES.map((zt) => (
                        <button
                          key={zt.value}
                          onClick={() => setZoneType(zt.value)}
                          title={zt.hint}
                          className={cn(
                            "flex items-center gap-1.5 px-2 py-1.5 rounded-lg border text-[11px] transition-colors text-left",
                            zoneType === zt.value ? "border-slate-500/60 bg-slate-800 text-slate-100" : "border-slate-700/30 text-slate-400 hover:bg-slate-800/60"
                          )}
                        >
                          <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: zoneTypeHex(zt.value) }} />
                          {zt.label}
                        </button>
                      ))}
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">{ZONE_TYPES.find((z) => z.value === zoneType)?.hint}</p>
                  </div>

                  <input className="input w-full text-xs" placeholder="Zone name (optional)" value={zoneName} onChange={(e) => setZoneName(e.target.value)} />

                  <div>
                    <label className="text-[10px] text-slate-500 block mb-1">Dwell threshold (sec)</label>
                    <input type="number" className="input w-full text-xs" value={threshold} min={0.5} step={0.5} onChange={(e) => setThreshold(parseFloat(e.target.value) || 5)} />
                  </div>

                  <div>
                    <label className="text-[10px] text-slate-500 block mb-1 flex items-center gap-1"><Clock className="w-3 h-3" /> Armed</label>
                    <div className="flex gap-1">
                      {(["always", "schedule", "never"] as const).map((m) => (
                        <button
                          key={m}
                          onClick={() => setSchedule((s) => ({ ...s, mode: m }))}
                          className={cn("flex-1 py-1.5 rounded-lg border text-[11px] capitalize transition-colors", schedule.mode === m ? "border-slate-500/60 bg-slate-800 text-slate-100" : "border-slate-700/30 text-slate-400")}
                        >
                          {m}
                        </button>
                      ))}
                    </div>
                    {schedule.mode === "schedule" && (
                      <div className="mt-2 space-y-2">
                        <div className="flex items-center gap-2">
                          <input type="time" className="input text-xs flex-1" value={schedule.windows[0]?.start ?? "22:00"} onChange={(e) => setSchedule((s) => ({ ...s, windows: [{ ...(s.windows[0] ?? { end: "06:00", days: [] }), start: e.target.value }] }))} />
                          <span className="text-slate-600 text-xs">→</span>
                          <input type="time" className="input text-xs flex-1" value={schedule.windows[0]?.end ?? "06:00"} onChange={(e) => setSchedule((s) => ({ ...s, windows: [{ ...(s.windows[0] ?? { start: "22:00", days: [] }), end: e.target.value }] }))} />
                        </div>
                        <div className="flex gap-1">
                          {DAY_LABELS.map((d, i) => (
                            <button key={d} onClick={() => toggleDay(i)} className={cn("flex-1 py-1 rounded text-[10px] border", schedule.windows[0]?.days.includes(i) ? "bg-violet-500/20 border-violet-500/30 text-violet-200" : "border-slate-700/30 text-slate-500")}>
                              {d}
                            </button>
                          ))}
                        </div>
                        <p className="text-[10px] text-slate-600">Timezone: {schedule.tz}</p>
                      </div>
                    )}
                  </div>

                  <div>
                    <label className="text-[10px] text-slate-500 block mb-1">Expected classes (never intrude here)</label>
                    <div className="flex flex-wrap gap-1">
                      {CLASS_OPTIONS.map((c) => (
                        <button
                          key={c}
                          onClick={() => setAllowed((a) => (a.includes(c) ? a.filter((x) => x !== c) : [...a, c]))}
                          className={cn("px-2 py-1 rounded-md text-[10px] border capitalize", allowed.includes(c) ? "bg-emerald-500/15 border-emerald-500/30 text-emerald-200" : "border-slate-700/30 text-slate-500")}
                        >
                          {allowed.includes(c) && <Check className="w-3 h-3 inline mr-0.5" />}{c}
                        </button>
                      ))}
                    </div>
                  </div>

                  <button onClick={handleSaveZone} disabled={!readyToSave || saving} className="btn-primary w-full text-xs justify-center disabled:opacity-40">
                    {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                    {saving ? "Saving..." : "Save Zone"}
                  </button>

                  {currentPoints.length > 0 && (
                    <div className="flex gap-2">
                      <button onClick={handleUndo} className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-[11px] text-slate-400 bg-slate-800/40 hover:bg-slate-800/60">
                        <Undo2 className="w-3 h-3" /> Undo
                      </button>
                      <button onClick={handleClearDraw} className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-[11px] text-red-400 bg-red-500/[0.06] hover:bg-red-500/10">
                        <Trash2 className="w-3 h-3" /> Clear
                      </button>
                    </div>
                  )}
                </div>

                <div className="flex-1 p-4 space-y-2">
                  <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Zones ({zones.length})</h3>
                  {zones.length === 0 && <p className="text-[11px] text-slate-600">No zones defined yet</p>}
                  {zones.map((zone) => (
                    <div key={zone.zone_id} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-800/30 border border-slate-700/20">
                      <div className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: zoneTypeHex(zone.zone_type) }} />
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-slate-300 font-medium truncate">{zone.name}</p>
                        <p className="text-[10px] text-slate-600">
                          {zone.zone_type} · {zone.threshold_sec}s · {zone.armed_schedule?.mode === "schedule" ? `${zone.armed_schedule.windows[0]?.start}–${zone.armed_schedule.windows[0]?.end}` : zone.armed_schedule?.mode}
                          {zone.armed_now === false && <span className="text-slate-500"> · off now</span>}
                          {zone.allowed_classes?.length > 0 && <span> · allows {zone.allowed_classes.join(", ")}</span>}
                        </p>
                      </div>
                      <button onClick={() => handleDeleteZone(zone.zone_id)} className="p-1 rounded text-slate-600 hover:text-red-400 shrink-0">
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  ))}
                </div>
              </>
            ) : tab === "gate" ? (
              <div className="p-4 space-y-4">
                <div>
                  <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Number-plate OCR</h3>
                  <p className="text-[10px] text-slate-500">
                    OCR runs once per vehicle, the first time its centre enters the gate polygon. Draw the polygon where plates are large and readable — the lane just in front of the barrier, not the whole frame.
                  </p>
                </div>

                <div
                  className={cn(
                    "rounded-xl px-3 py-2 border text-[10px]",
                    GATE_ROLES.includes(cameraRole) && savedGate.length >= 3
                      ? "bg-emerald-500/[0.05] border-emerald-500/15 text-emerald-300"
                      : "bg-amber-500/[0.05] border-amber-500/15 text-amber-300"
                  )}
                >
                  {GATE_ROLES.includes(cameraRole) && savedGate.length >= 3
                    ? `OCR active — role ${cameraRole.replace("_", " ")}, gate polygon set`
                    : !GATE_ROLES.includes(cameraRole)
                      ? `OCR inactive — camera role is "${cameraRole}". Saving a gate below sets it to gate entry/exit.`
                      : "OCR inactive — no gate polygon yet. Draw one on the image."}
                </div>

                <div>
                  <label className="text-[10px] text-slate-500 block mb-1">This camera watches the</label>
                  <div className="flex gap-1">
                    {(["gate_entry", "gate_exit"] as const).map((r) => (
                      <button
                        key={r}
                        onClick={() => setGateRole(r)}
                        className={cn(
                          "flex-1 py-1.5 rounded-lg border text-[11px] transition-colors",
                          gateRole === r ? "border-orange-500/50 bg-orange-500/10 text-orange-200" : "border-slate-700/30 text-slate-400 hover:bg-slate-800/60"
                        )}
                      >
                        {r === "gate_entry" ? "Entry gate" : "Exit gate"}
                      </button>
                    ))}
                  </div>
                  <p className="text-[10px] text-slate-600 mt-1">
                    {gateRole === "gate_entry"
                      ? "Entry: plate is recorded and a free space is auto-assigned."
                      : "Exit: plate is matched to its session, the space is released and the fee is computed."}
                  </p>
                </div>

                <button
                  onClick={handleSaveGate}
                  disabled={saving || gateDrawing || gatePoints.length < 3}
                  className="btn-primary w-full text-xs justify-center disabled:opacity-40"
                >
                  {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                  {saving ? "Saving..." : savedGate.length >= 3 ? "Replace gate polygon" : "Save gate polygon"}
                </button>

                {gatePoints.length > 0 && (
                  <div className="flex gap-2">
                    <button
                      onClick={() => {
                        setGatePoints(gatePoints.slice(0, -1));
                        if (gatePoints.length <= 1) setGateDrawing(false);
                      }}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-[11px] text-slate-400 bg-slate-800/40 hover:bg-slate-800/60"
                    >
                      <Undo2 className="w-3 h-3" /> Undo
                    </button>
                    <button
                      onClick={() => { setGatePoints([]); setGateDrawing(false); }}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-[11px] text-red-400 bg-red-500/[0.06] hover:bg-red-500/10"
                    >
                      <Trash2 className="w-3 h-3" /> Discard
                    </button>
                  </div>
                )}

                {savedGate.length >= 3 && gatePoints.length === 0 && (
                  <button onClick={handleClearGate} disabled={saving} className="w-full py-2 rounded-lg text-[11px] text-red-400 border border-red-500/15 hover:bg-red-500/[0.06]">
                    Remove gate polygon (turns OCR off)
                  </button>
                )}

                {!GATE_ROLES.includes(cameraRole) && savedGate.length >= 3 && (
                  <div className="border-t border-slate-700/30 pt-3">
                    <p className="text-[10px] text-slate-500 mb-2">A gate polygon exists but the role is not a gate role.</p>
                    <button onClick={() => handleSetRole(gateRole)} disabled={saving} className="w-full py-2 rounded-lg text-[11px] border border-slate-600/40 text-slate-300 hover:bg-slate-800/60">
                      Set role to {gateRole.replace("_", " ")}
                    </button>
                  </div>
                )}

                <div className="border-t border-slate-700/30 pt-3 text-[10px] text-slate-500 space-y-1">
                  <p className="font-semibold text-slate-400">Tips for readable plates</p>
                  <p>• Camera 1–3 m from the lane, plate at least ~120 px wide in the frame.</p>
                  <p>• Indian format is validated (e.g. KA01AB1234); the state is derived from the first two letters.</p>
                  <p>• Register known plates on the Parking page to have drivers inherit vehicle authorization.</p>
                </div>
              </div>
            ) : (
              <div className="p-4 space-y-4">
                <div>
                  <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Field of view</h3>
                  <label className="text-[10px] text-slate-500 block mb-1">Horizontal FOV (degrees)</label>
                  <input type="number" className="input w-full text-xs" value={hfov} min={10} max={170} step={1} onChange={(e) => setHfov(parseFloat(e.target.value) || 84)} />
                  <p className="text-[10px] text-slate-600 mt-1">
                    Typical: phone/webcam 60–70°, CCTV bullet 80–95°, wide dome 100–120°. Used with known object heights (person 1.7 m, car 1.5 m) for the pinhole distance estimate.
                  </p>
                  <button onClick={() => handleSaveCalibration(false)} disabled={saving} className="mt-2 w-full py-2 rounded-lg text-[11px] border border-slate-600/40 text-slate-300 hover:bg-slate-800/60">
                    Save FOV only
                  </button>
                </div>

                <div className="border-t border-slate-700/30 pt-4">
                  <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Ground plane (metric)</h3>
                  <p className="text-[10px] text-slate-500 mb-2">
                    Click the four corners of a rectangle you can measure on the ground (a parking bay is ≈ 2.5 × 5 m). This gives real distances between people, speeds in m/s and accurate close-contact detection.
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[10px] text-slate-500 block mb-1">Width (m, near edge)</label>
                      <input type="number" className="input w-full text-xs" value={rectWidth} min={0.2} step={0.1} onChange={(e) => setRectWidth(parseFloat(e.target.value) || 2.5)} />
                    </div>
                    <div>
                      <label className="text-[10px] text-slate-500 block mb-1">Length (m, depth)</label>
                      <input type="number" className="input w-full text-xs" value={rectLength} min={0.2} step={0.1} onChange={(e) => setRectLength(parseFloat(e.target.value) || 5)} />
                    </div>
                  </div>
                  <div className="flex gap-2 mt-2">
                    <button onClick={() => setCalPoints([])} className="flex-1 py-2 rounded-lg text-[11px] text-slate-400 bg-slate-800/40 hover:bg-slate-800/60">
                      Reset points ({calPoints.length}/4)
                    </button>
                    <button onClick={() => handleSaveCalibration(true)} disabled={saving || calPoints.length !== 4} className="btn-primary flex-1 text-xs justify-center disabled:opacity-40">
                      {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />} Save
                    </button>
                  </div>
                </div>

                <div className="border-t border-slate-700/30 pt-3 text-[10px] text-slate-500">
                  <p className="font-semibold text-slate-400 mb-1">Current calibration</p>
                  {existingCal ? (
                    <>
                      <p>HFOV {existingCal.hfov_deg}°</p>
                      <p>{existingCal.homography_image_points?.length >= 4 ? "Ground plane: calibrated (metric)" : "Ground plane: not set (pinhole estimate)"}</p>
                    </>
                  ) : (
                    <p>Defaults (84°, pinhole)</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {(error || notice) && snapshot && (
          <div className={cn("px-5 py-2 border-t", error ? "bg-red-500/[0.06] border-red-500/15" : "bg-emerald-500/[0.06] border-emerald-500/15")}>
            <p className={cn("text-[11px]", error ? "text-red-400" : "text-emerald-300")}>{error || notice}</p>
          </div>
        )}
      </div>
    </div>
  );
}
