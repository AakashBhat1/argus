"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, Camera as CameraIcon, Ruler, ShieldAlert, Users, Wifi, WifiOff } from "lucide-react";
import { api, type ArmMode, type Camera, type FeedData, type SecurityState, type Alert } from "@/lib/api";
import { useWebSocket } from "@/lib/websocket";
import { cn, riskBadge, riskHex } from "@/lib/utils";
import LiveFeed from "@/components/LiveFeed";
import ArmControl from "@/components/security/ArmControl";
import GrantsPanel from "@/components/security/GrantsPanel";
import RiskTimeline, { type RiskSample } from "@/components/security/RiskTimeline";
import EscalationFeed, { type Escalation } from "@/components/security/EscalationFeed";

const TIMELINE_WINDOW_MS = 3 * 60 * 1000;
const ESCALATION_TYPES = new Set(["intrusion_detected", "suspicious_activity", "blacklisted_vehicle_contact", "crime_detected", "crowd_detected"]);

interface CameraLive {
  camera_id: string;
  camera_name: string;
  max_score: number;
  level: string;
  persons: number;
  authorized: number;
  ground_plane_calibrated: boolean;
  zones: number;
  armed_zones: number;
  updated: number;
}

export default function SecurityPage() {
  const { lastMessage, isConnected } = useWebSocket("global");
  const [state, setState] = useState<SecurityState | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [armBusy, setArmBusy] = useState(false);
  const [live, setLive] = useState<Record<string, CameraLive>>({});
  const [timeline, setTimeline] = useState<RiskSample[]>([]);
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const lastSampleRef = useRef<number>(0);
  const liveRef = useRef<Record<string, CameraLive>>({});

  const load = useCallback(async () => {
    try {
      const [s, cams] = await Promise.all([api.security.state(), api.cameras.list()]);
      setState(s);
      setCameras(cams);
    } catch (err) {
      console.error("Security state load failed:", err);
    }
  }, []);

  useEffect(() => {
    load();
    const i = setInterval(load, 15000);
    // Seed the escalation feed with recent persisted alerts.
    api.alerts
      .list({ limit: "20" })
      .then((alerts: Alert[]) =>
        setEscalations(
          alerts
            .filter((a) => ESCALATION_TYPES.has(a.type))
            .map((a) => ({
              key: a.id,
              alert_id: a.id,
              camera_id: a.camera_id,
              type: a.type,
              severity: a.severity,
              timestamp: a.timestamp,
              reasons: a.trigger_condition ? [a.trigger_condition.replace(/^risk \d+\/100 \([a-z]+\): /, "")] : undefined,
            }))
        )
      )
      .catch(() => {});
    return () => clearInterval(i);
  }, [load]);

  // Live WS processing
  useEffect(() => {
    if (!lastMessage) return;
    if (lastMessage.type === "detections") {
      const d = lastMessage.data as FeedData;
      const now = Date.now();
      const zones = d.zones ?? [];
      liveRef.current = {
        ...liveRef.current,
        [d.camera_id]: {
          camera_id: d.camera_id,
          camera_name: d.camera_name,
          max_score: d.risk_summary?.max_score ?? 0,
          level: d.risk_summary?.level ?? "observe",
          persons: d.risk_summary?.persons ?? 0,
          authorized: d.risk_summary?.authorized ?? 0,
          ground_plane_calibrated: !!d.ground_plane_calibrated,
          zones: zones.length,
          armed_zones: zones.filter((z) => z.armed).length,
          updated: now,
        },
      };
      setLive(liveRef.current);
      // Sample the timeline at most every 500 ms.
      if (now - lastSampleRef.current >= 500) {
        lastSampleRef.current = now;
        const sample: RiskSample = { t: now };
        for (const cam of Object.values(liveRef.current)) {
          // Cameras that stopped streaming fall back to 0 after 5 s.
          sample[cam.camera_name] = now - cam.updated > 5000 ? 0 : cam.max_score;
        }
        setTimeline((prev) => [...prev, sample].filter((s) => now - s.t <= TIMELINE_WINDOW_MS));
      }
    } else if (lastMessage.type === "alert") {
      const a = lastMessage.data;
      if (!ESCALATION_TYPES.has(a.type)) return;
      setEscalations((prev) =>
        [
          {
            key: a.alert_id || `${a.camera_id}-${a.timestamp}`,
            alert_id: a.alert_id,
            camera_id: a.camera_id,
            camera_name: a.camera_name,
            type: a.type,
            severity: a.severity,
            object_id: a.object_id,
            risk_score: a.risk_score,
            risk_level: a.risk_level,
            reasons: a.reasons,
            zone_names: a.zone_names,
            distance_m: a.distance_m,
            timestamp: a.timestamp,
          },
          ...prev,
        ].slice(0, 50)
      );
    } else if (lastMessage.type === "security") {
      const s = lastMessage.data?.state;
      if (s) setState((prev) => (prev ? { ...prev, arm_mode: s.arm_mode, grants: s.grants } : prev));
    }
  }, [lastMessage]);

  async function handleArm(mode: ArmMode) {
    setArmBusy(true);
    try {
      const res = await api.security.arm(mode);
      setState((prev) => (prev ? { ...prev, arm_mode: res.arm_mode } : prev));
    } catch (err) {
      console.error("Arm failed:", err);
    } finally {
      setArmBusy(false);
    }
  }

  const cameraCards = useMemo(() => {
    const byId: Record<string, CameraLive> = { ...live };
    for (const c of state?.cameras ?? []) {
      if (!byId[c.camera_id]) {
        byId[c.camera_id] = {
          camera_id: c.camera_id,
          camera_name: c.camera_name,
          max_score: c.risk.max_score,
          level: c.risk.level,
          persons: c.risk.persons,
          authorized: c.risk.authorized ?? 0,
          ground_plane_calibrated: c.ground_plane_calibrated,
          zones: 0,
          armed_zones: 0,
          updated: 0,
        };
      }
    }
    return Object.values(byId).sort((a, b) => b.max_score - a.max_score);
  }, [live, state]);

  const siteMax = cameraCards.reduce((m, c) => Math.max(m, c.max_score), 0);
  const siteLevel = siteMax >= 75 ? "critical" : siteMax >= 50 ? "alert" : siteMax >= 25 ? "suspicious" : "observe";
  const totalPersons = cameraCards.reduce((s, c) => s + c.persons, 0);
  const totalAuthorized = cameraCards.reduce((s, c) => s + c.authorized, 0);
  const timelineCameras = useMemo(() => Array.from(new Set(cameraCards.map((c) => c.camera_name))), [cameraCards]);

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="page-title"><span className="gradient-text-static">Security Console</span></h1>
          <p className="page-subtitle">Contextual intrusion detection — who is here, where they came from, and whether it matters</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-[11px] bg-slate-800/30 border border-slate-700/20 rounded-full px-3.5 py-1.5">
            {isConnected ? <Wifi className="w-3.5 h-3.5 text-emerald-400" /> : <WifiOff className="w-3.5 h-3.5 text-red-400" />}
            <span className={isConnected ? "text-emerald-400 font-medium" : "text-red-400"}>{isConnected ? "Live" : "Disconnected"}</span>
          </div>
          <ArmControl mode={state?.arm_mode ?? "auto"} busy={armBusy} onChange={handleArm} />
        </div>
      </div>

      {/* Site summary */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger-children">
        <div className={cn("card border", riskBadge(siteLevel).split(" ").filter((c) => c.startsWith("border")).join(" "))}>
          <div className="flex items-center justify-between">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">Site risk</p>
            <ShieldAlert className="w-4 h-4" style={{ color: riskHex(siteLevel) }} />
          </div>
          <p className="text-3xl font-bold tabular-nums mt-2" style={{ color: riskHex(siteLevel) }}>{siteMax.toFixed(0)}</p>
          <p className="text-[11px] text-slate-500 capitalize">{siteLevel}</p>
        </div>
        <div className="card">
          <div className="flex items-center justify-between">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">People on site</p>
            <Users className="w-4 h-4 text-blue-400" />
          </div>
          <p className="text-3xl font-bold tabular-nums mt-2 text-slate-100">{totalPersons}</p>
          <p className="text-[11px] text-slate-500">{totalAuthorized} authorized · {totalPersons - totalAuthorized} unknown</p>
        </div>
        <div className="card">
          <div className="flex items-center justify-between">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">Active grants</p>
            <Activity className="w-4 h-4 text-emerald-400" />
          </div>
          <p className="text-3xl font-bold tabular-nums mt-2 text-slate-100">{state?.grants.length ?? 0}</p>
          <p className="text-[11px] text-slate-500">{state?.grants.filter((g) => g.kind === "plate").length ?? 0} vehicles · {state?.grants.filter((g) => g.threat).length ?? 0} threats</p>
        </div>
        <div className="card">
          <div className="flex items-center justify-between">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">Cameras</p>
            <CameraIcon className="w-4 h-4 text-violet-400" />
          </div>
          <p className="text-3xl font-bold tabular-nums mt-2 text-slate-100">{cameraCards.length}</p>
          <p className="text-[11px] text-slate-500">{cameraCards.filter((c) => c.ground_plane_calibrated).length} metric-calibrated</p>
        </div>
      </div>

      {/* Camera risk strip */}
      {cameraCards.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
          {cameraCards.map((c) => (
            <div key={c.camera_id} className="card-sm rounded-xl border border-slate-700/30 bg-slate-800/30 p-3">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-slate-200 truncate">{c.camera_name}</p>
                <span className={cn("badge border px-1.5 py-0 text-[9px] uppercase", riskBadge(c.level))}>{c.level}</span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                <div className="h-full rounded-full transition-all duration-300" style={{ width: `${Math.min(100, c.max_score)}%`, backgroundColor: riskHex(c.level) }} />
              </div>
              <div className="mt-1.5 flex items-center justify-between text-[10px] text-slate-500">
                <span>{c.persons} person{c.persons === 1 ? "" : "s"}{c.authorized > 0 ? ` · ${c.authorized} ok` : ""}</span>
                <span className="flex items-center gap-1">
                  {c.zones > 0 && <span>{c.armed_zones}/{c.zones} armed</span>}
                  <Ruler className={cn("w-3 h-3", c.ground_plane_calibrated ? "text-cyan-400" : "text-slate-600")} />
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Live feed + timeline/escalations */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <div className="xl:col-span-2">
          <LiveFeed lastMessage={lastMessage} />
        </div>
        <div className="flex flex-col gap-5">
          <div className="h-64"><RiskTimeline data={timeline} cameras={timelineCameras} /></div>
          <div className="flex-1 min-h-[280px]"><EscalationFeed items={escalations} /></div>
        </div>
      </div>

      {/* Grants */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <div className="xl:col-span-2">
          <GrantsPanel grants={state?.grants ?? []} cameras={cameras} onChanged={load} />
        </div>
        <div className="card">
          <div className="card-header">How the score works</div>
          <ul className="space-y-2 text-[11px] text-slate-400 leading-relaxed">
            <li><span className="text-slate-200 font-medium">Zone semantics.</span> Standing in an armed <span className="text-red-300">restricted</span> zone starts at 40; a <span className="text-orange-300">perimeter</span> at 35; <span className="text-emerald-300">entrance</span>/<span className="text-blue-300">driveway</span> at 10; public at 0. Disarmed zones count a fraction.</li>
            <li><span className="text-slate-200 font-medium">Dwell.</span> Crossing the zone threshold adds +20 — that is the moment an intrusion alert fires.</li>
            <li><span className="text-slate-200 font-medium">Origin.</span> Stepping out of a vehicle −15, entering via an entrance zone −20, first appearing on the perimeter +15.</li>
            <li><span className="text-slate-200 font-medium">Behaviour.</span> Loitering +15, erratic/casing +10, steadily passing through −10.</li>
            <li><span className="text-slate-200 font-medium">Context.</span> Quiet hours +10, group of 3+ +10, sustained close contact (&lt;1.5 m, pre-fight) +10/+15, crime classifier hit up to +30, blacklisted vehicle +40.</li>
            <li><span className="text-slate-200 font-medium">Authorization</span> (plate, visitor window, operator “Known”) multiplies the score by 0.2.</li>
            <li className="pt-1 border-t border-slate-700/30 text-slate-500">Ladder: 0–24 observe · 25–49 suspicious · 50–74 <span className="text-orange-300">alert</span> · 75+ <span className="text-red-300">critical</span>. Only alert/critical page an operator; one alert per incident, plus escalations.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
