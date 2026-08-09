"use client";

import { useMemo } from "react";
import { Clock } from "lucide-react";
import { type ParkingSpace } from "@/lib/api";

interface SlotGridProps {
  spaces: ParkingSpace[];
  onSelectSpace: (space: ParkingSpace) => void;
  calculateLiveTariff: (entryTimeStr: string | null | undefined) => { durationText: string; cost: number };
}

export default function SlotGrid({ spaces, onSelectSpace, calculateLiveTariff }: SlotGridProps) {
  // Group spaces by floor
  const groupedSpaces = useMemo(() => {
    const floors: Record<string, ParkingSpace[]> = { G: [], "1": [], "2": [] };
    spaces.forEach((s) => {
      const floor = s.floor || "G";
      if (floors[floor]) {
        floors[floor].push(s);
      }
    });

    // Sort spaces by ID (e.g. G-01, G-02...)
    Object.keys(floors).forEach((floor) => {
      floors[floor].sort((a, b) => a.space_id.localeCompare(b.space_id));
    });

    return floors;
  }, [spaces]);

  return (
    <div className="space-y-6 max-h-[520px] overflow-y-auto pr-1">
      {["2", "1", "G"].map((floorName) => {
        const floorSpaces = groupedSpaces[floorName] || [];
        return (
          <div key={floorName} className="bg-slate-900/30 border border-slate-800/40 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3 border-b border-slate-800 pb-2">
              <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                {floorName === "G" ? "Ground Floor" : `Floor ${floorName}`}
              </h3>
              <span className="text-[10px] text-slate-500">
                {floorSpaces.filter((s) => s.is_occupied).length} / {floorSpaces.length} Occupied
              </span>
            </div>

            <div className="grid grid-cols-4 gap-2.5">
              {floorSpaces.map((space) => {
                const isOccupied = space.is_occupied;
                const profile = space.profile_type || "normal";
                const liveTariff = isOccupied ? calculateLiveTariff(space.entry_time) : null;

                // Determine card style based on state
                let cardClass = "border-slate-800 bg-slate-950/20 hover:border-slate-700";
                let badgeClass = "bg-slate-900 text-slate-500 border-slate-800";
                let badgeText = "Vacant";

                if (isOccupied) {
                  if (profile === "vip") {
                    cardClass = "border-emerald-500/20 bg-emerald-500/[0.02] hover:border-emerald-500/45 cursor-pointer";
                    badgeClass = "bg-emerald-500/10 text-emerald-400 border-emerald-500/25";
                    badgeText = "VIP";
                  } else if (profile === "blacklist") {
                    cardClass = "border-red-500/30 bg-red-500/[0.03] hover:border-red-500/60 cursor-pointer animate-pulse";
                    badgeClass = "bg-red-500/20 text-red-400 border-red-500/30";
                    badgeText = "ALERT";
                  } else {
                    cardClass = "border-blue-500/20 bg-blue-500/[0.02] hover:border-blue-500/45 cursor-pointer";
                    badgeClass = "bg-blue-500/10 text-blue-400 border-blue-500/25";
                    badgeText = "Visitor";
                  }
                }

                return (
                  <div
                    key={space.id}
                    onClick={() => isOccupied && onSelectSpace(space)}
                    className={`border rounded-xl p-2.5 flex flex-col gap-1.5 transition-all duration-200 ${cardClass}`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-bold text-slate-300">
                        {space.space_id}
                      </span>
                      <span className={`inline-flex items-center text-[8px] font-bold uppercase px-1 rounded border ${badgeClass}`}>
                        {badgeText}
                      </span>
                    </div>

                    {isOccupied ? (
                      <div className="space-y-1">
                        <p className="font-mono text-[10px] font-extrabold text-slate-100 tracking-wider truncate">
                          {space.plate_text}
                        </p>
                        <div className="flex items-center justify-between text-[9px] text-slate-500">
                          <span className="flex items-center gap-0.5">
                            <Clock className="w-2.5 h-2.5 text-slate-600" /> {liveTariff?.durationText}
                          </span>
                          <span className="font-bold text-slate-400">₹{liveTariff?.cost}</span>
                        </div>
                      </div>
                    ) : (
                      <div className="flex-1 flex items-center justify-center py-2">
                        <span className="text-[10px] font-semibold text-slate-700 tracking-wider uppercase">Open</span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
