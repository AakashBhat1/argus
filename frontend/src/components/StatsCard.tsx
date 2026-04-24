"use client";

import { type LucideIcon, TrendingUp, TrendingDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: LucideIcon;
  color?: "blue" | "green" | "red" | "purple" | "teal" | "amber";
  trend?: { value: number; label: string };
  loading?: boolean;
}

const colorMap = {
  blue:   { icon: "bg-blue-500/12 text-blue-400",       ring: "group-hover:ring-blue-500/15", accent: "from-blue-500/20", glow: "group-hover:shadow-blue-500/[0.06]" },
  green:  { icon: "bg-emerald-500/12 text-emerald-400",  ring: "group-hover:ring-emerald-500/15", accent: "from-emerald-500/20", glow: "group-hover:shadow-emerald-500/[0.06]" },
  red:    { icon: "bg-red-500/12 text-red-400",          ring: "group-hover:ring-red-500/15", accent: "from-red-500/20", glow: "group-hover:shadow-red-500/[0.06]" },
  purple: { icon: "bg-violet-500/12 text-violet-400",    ring: "group-hover:ring-violet-500/15", accent: "from-violet-500/20", glow: "group-hover:shadow-violet-500/[0.06]" },
  teal:   { icon: "bg-teal-500/12 text-teal-400",        ring: "group-hover:ring-teal-500/15", accent: "from-teal-500/20", glow: "group-hover:shadow-teal-500/[0.06]" },
  amber:  { icon: "bg-amber-500/12 text-amber-400",      ring: "group-hover:ring-amber-500/15", accent: "from-amber-500/20", glow: "group-hover:shadow-amber-500/[0.06]" },
};

export default function StatsCard({ title, value, subtitle, icon: Icon, color = "blue", trend, loading }: Props) {
  const c = colorMap[color];
  return (
    <div className={cn(
      "group card cursor-default transition-all duration-300",
      "ring-1 ring-transparent shadow-lg shadow-transparent",
      c.ring,
      c.glow
    )}>
      {/* Top accent gradient */}
      <div className={cn(
        "absolute top-0 left-4 right-4 h-[1px] bg-gradient-to-r to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500",
        c.accent
      )} />

      <div className="flex items-start justify-between mb-3">
        <div className={cn("w-10 h-10 rounded-xl flex items-center justify-center transition-transform duration-300 group-hover:scale-105", c.icon)}>
          <Icon className="w-5 h-5" />
        </div>
        {trend && (
          <div className={cn(
            "flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md",
            trend.value >= 0
              ? "text-emerald-400 bg-emerald-500/10"
              : "text-red-400 bg-red-500/10"
          )}>
            {trend.value >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
            {Math.abs(trend.value)}%
          </div>
        )}
      </div>
      {loading ? (
        <div className="space-y-2.5">
          <div className="h-8 bg-slate-800/80 rounded-lg shimmer w-24" />
          <div className="h-3 bg-slate-800/80 rounded shimmer w-16" />
        </div>
      ) : (
        <>
          <p className="stat-value text-slate-50 leading-none">{value}</p>
          {subtitle && (
            <p className="text-[11px] text-slate-500 mt-1.5 font-medium">{subtitle}</p>
          )}
        </>
      )}
      <p className="text-[10px] text-slate-600 mt-3 uppercase tracking-[0.12em] font-semibold">{title}</p>
    </div>
  );
}
