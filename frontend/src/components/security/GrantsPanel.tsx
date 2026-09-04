"use client";

import { useState } from "react";
import { UserPlus, Car, Trash2, KeyRound, Loader2, ShieldOff } from "lucide-react";
import { api, type Grant, type Camera } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  grants: Grant[];
  cameras: Camera[];
  onChanged: () => void;
}

function remaining(seconds: number): string {
  if (seconds <= 0) return "expired";
  if (seconds < 90) return `${seconds}s`;
  const m = Math.round(seconds / 60);
  if (m < 90) return `${m} min`;
  return `${(m / 60).toFixed(1)} h`;
}

export default function GrantsPanel({ grants, cameras, onChanged }: Props) {
  const [minutes, setMinutes] = useState(15);
  const [label, setLabel] = useState("Expected visitor");
  const [busy, setBusy] = useState<string | null>(null);
  const [plate, setPlate] = useState("KA01AB1234");
  const [profile, setProfile] = useState("resident");
  const [plateCamera, setPlateCamera] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card h-full flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <div className="card-header mb-0">Access &amp; Authorization</div>
        <span className="badge bg-slate-800/60 text-slate-400 border-slate-700/40">{grants.length} active</span>
      </div>

      {/* Expect visitor */}
      <div className="rounded-xl border border-slate-700/30 bg-slate-800/20 p-3 space-y-2">
        <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
          <UserPlus className="w-3 h-3" /> Expect a visitor
        </p>
        <div className="flex gap-2">
          <input className="input flex-1 text-xs" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Who / why" />
          <input type="number" className="input w-20 text-xs" value={minutes} min={1} max={1440} onChange={(e) => setMinutes(parseInt(e.target.value) || 15)} />
          <button
            onClick={() => run("site", () => api.security.grantSite({ minutes, label }))}
            disabled={busy !== null}
            className="btn-primary text-xs px-3"
          >
            {busy === "site" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <KeyRound className="w-3.5 h-3.5" />}
            Grant
          </button>
        </div>
        <p className="text-[10px] text-slate-600">Anyone seen on site during this window is treated as authorized (risk × 0.2). Use for deliveries, contractors, guests.</p>
      </div>

      {/* Simulate gate plate */}
      <div className="rounded-xl border border-slate-700/30 bg-slate-800/20 p-3 space-y-2 mt-3">
        <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
          <Car className="w-3 h-3" /> Vehicle arrival (gate plate read)
        </p>
        <div className="grid grid-cols-3 gap-2">
          <input className="input text-xs col-span-1" value={plate} onChange={(e) => setPlate(e.target.value.toUpperCase())} placeholder="Plate" />
          <select className="input text-xs" value={profile} onChange={(e) => setProfile(e.target.value)}>
            <option value="resident">resident</option>
            <option value="vip">vip</option>
            <option value="staff">staff</option>
            <option value="normal">visitor (normal)</option>
            <option value="blacklist">blacklist</option>
          </select>
          <select className="input text-xs" value={plateCamera} onChange={(e) => setPlateCamera(e.target.value)}>
            <option value="">gate camera…</option>
            {cameras.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>
        <button
          onClick={() =>
            run("plate", () =>
              api.security.registerVehicle({
                camera_id: plateCamera || cameras[0]?.id || "gate",
                track_id: Math.floor(Math.random() * 1000),
                plate_text: plate,
                profile_type: profile,
              })
            )
          }
          disabled={busy !== null || !plate}
          className={cn("w-full py-2 rounded-lg text-xs border transition-colors", profile === "blacklist" ? "border-red-500/30 text-red-300 hover:bg-red-500/10" : "border-slate-600/40 text-slate-300 hover:bg-slate-800/60")}
        >
          {busy === "plate" ? <Loader2 className="w-3.5 h-3.5 animate-spin inline" /> : null} Register arrival
        </button>
        <p className="text-[10px] text-slate-600">The gate OCR does this automatically on <code>gate_entry</code> cameras. People stepping out of an authorized vehicle inherit its status; a blacklisted plate marks them as a threat.</p>
      </div>

      {/* Active grants */}
      <div className="flex-1 mt-3 space-y-1.5 overflow-auto">
        {grants.length === 0 && (
          <div className="flex flex-col items-center justify-center py-6 text-slate-600">
            <ShieldOff className="w-6 h-6 opacity-30 mb-2" />
            <p className="text-[11px]">No active grants — everyone is a stranger</p>
          </div>
        )}
        {grants.map((g) => (
          <div
            key={g.grant_id}
            className={cn(
              "flex items-center gap-3 px-3 py-2 rounded-xl border bg-slate-800/30",
              g.threat ? "border-red-500/30" : "border-slate-700/30"
            )}
          >
            <div className={cn("w-7 h-7 rounded-lg flex items-center justify-center shrink-0 border", g.threat ? "bg-red-500/10 border-red-500/30 text-red-300" : g.kind === "plate" ? "bg-violet-500/10 border-violet-500/30 text-violet-300" : "bg-emerald-500/10 border-emerald-500/30 text-emerald-300")}>
              {g.kind === "plate" ? <Car className="w-3.5 h-3.5" /> : <KeyRound className="w-3.5 h-3.5" />}
            </div>
            <div className="flex-1 min-w-0">
              <p className={cn("text-xs font-medium truncate", g.threat ? "text-red-200" : "text-slate-200")}>{g.label}</p>
              <p className="text-[10px] text-slate-500">
                {g.scope}{g.track_id != null ? ` · track #${g.track_id}` : ""} · {remaining(g.seconds_remaining)} left
              </p>
            </div>
            <button
              onClick={() => run(g.grant_id, () => api.security.revoke(g.grant_id))}
              className="p-1.5 rounded-lg text-slate-600 hover:text-red-400 hover:bg-red-500/10"
              aria-label="Revoke"
            >
              {busy === g.grant_id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
            </button>
          </div>
        ))}
      </div>
      {error && <p className="mt-2 text-[11px] text-red-400">{error}</p>}
    </div>
  );
}
