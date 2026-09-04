"use client";

import { Shield, ShieldAlert, ShieldCheck, Loader2 } from "lucide-react";
import { type ArmMode } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  mode: ArmMode;
  busy: boolean;
  onChange: (mode: ArmMode) => void;
}

const OPTIONS: { value: ArmMode; label: string; hint: string; icon: typeof Shield; active: string }[] = [
  { value: "armed", label: "Armed", hint: "Every zone armed regardless of schedule", icon: ShieldAlert, active: "bg-red-500/15 text-red-200 border-red-500/40" },
  { value: "auto", label: "Auto", hint: "Each zone follows its own schedule", icon: Shield, active: "bg-blue-500/15 text-blue-200 border-blue-500/40" },
  { value: "disarmed", label: "Disarmed", hint: "Presence is logged, nobody is paged", icon: ShieldCheck, active: "bg-emerald-500/15 text-emerald-200 border-emerald-500/40" },
];

export default function ArmControl({ mode, busy, onChange }: Props) {
  return (
    <div className="flex items-center gap-1 bg-slate-900/60 border border-slate-700/40 rounded-xl p-1">
      {OPTIONS.map((opt) => {
        const Icon = opt.icon;
        const active = mode === opt.value;
        return (
          <button
            key={opt.value}
            onClick={() => onChange(opt.value)}
            disabled={busy}
            title={opt.hint}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all",
              active ? opt.active : "border-transparent text-slate-500 hover:text-slate-300 hover:bg-slate-800/60"
            )}
          >
            {busy && active ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Icon className="w-3.5 h-3.5" />}
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
