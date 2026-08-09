"use client";

import { X, CircleDollarSign, Clock, RefreshCw, CheckCircle2, Check } from "lucide-react";
import { type ParkingSpace, type ReleaseSpace } from "@/lib/api";

interface CheckoutModalProps {
  isOpen: boolean;
  onClose: () => void;
  space: ParkingSpace | null;
  onCheckout: () => Promise<void>;
  releasing: boolean;
  checkoutResult: ReleaseSpace | null;
  calculateLiveTariff: (entryTimeStr: string | null | undefined) => { durationText: string; cost: number };
}

export default function CheckoutModal({
  isOpen,
  onClose,
  space,
  onCheckout,
  releasing,
  checkoutResult,
  calculateLiveTariff,
}: CheckoutModalProps) {
  if (!isOpen || !space) return null;

  return (
    <div className="fixed inset-0 bg-slate-950/75 backdrop-blur-sm z-[9990] flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 max-w-md w-full shadow-2xl relative animate-scale-up">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-500 hover:text-slate-300"
          aria-label="Close checkout modal"
        >
          <X className="w-5 h-5" />
        </button>

        {!checkoutResult ? (
          // Step 1: Pre-Checkout Details
          <div className="space-y-6">
            <div>
              <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                <CircleDollarSign className="w-5 h-5 text-indigo-400" /> Release Space {space.space_id}
              </h2>
              <p className="text-xs text-slate-400 mt-1">Verify vehicle and process parking fee checkout</p>
            </div>

            <div className="bg-slate-950/40 border border-slate-800 rounded-2xl p-4 space-y-3 font-medium">
              <div className="flex justify-between items-center text-xs">
                <span className="text-slate-500">Occupant Plate</span>
                <span className="font-mono text-sm font-bold text-blue-400 bg-blue-500/5 px-2 py-0.5 rounded">
                  {space.plate_text}
                </span>
              </div>
              <div className="flex justify-between items-center text-xs">
                <span className="text-slate-500">Tier Profile</span>
                <span
                  className={`badge text-[9px] font-extrabold uppercase ${
                    space.profile_type === "vip"
                      ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                      : space.profile_type === "blacklist"
                      ? "bg-red-500/20 text-red-400 border-red-500/30"
                      : "bg-slate-850 text-slate-400 border-slate-700/50"
                  }`}
                >
                  {space.profile_type || "Standard"}
                </span>
              </div>
              <div className="flex justify-between items-center text-xs">
                <span className="text-slate-500">Arrival Time</span>
                <span className="text-slate-300 font-mono">
                  {space.entry_time
                    ? new Date(space.entry_time).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : "—"}
                </span>
              </div>
              <div className="flex justify-between items-center text-xs">
                <span className="text-slate-500">Duration Parked</span>
                <span className="text-slate-300 font-semibold flex items-center gap-1">
                  <Clock className="w-3.5 h-3.5 text-slate-500" />
                  {calculateLiveTariff(space.entry_time).durationText}
                </span>
              </div>
              <div className="border-t border-slate-800 pt-3 flex justify-between items-center">
                <span className="text-xs text-slate-400">Total Tariff Due</span>
                <span className="text-lg font-bold text-indigo-400">
                  ₹{calculateLiveTariff(space.entry_time).cost.toFixed(2)}
                </span>
              </div>
            </div>

            <div className="flex gap-3 pt-2">
              <button onClick={onClose} className="btn-ghost flex-1 justify-center">
                Cancel
              </button>
              <button
                onClick={onCheckout}
                disabled={releasing}
                className="btn-gradient flex-1 justify-center flex items-center gap-2"
              >
                {releasing ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" /> Processing
                  </>
                ) : (
                  <>
                    <Check className="w-4 h-4" /> Process Checkout
                  </>
                )}
              </button>
            </div>
          </div>
        ) : (
          // Step 2: Checkout Receipt Summary
          <div className="space-y-6 text-center py-4">
            <div className="mx-auto w-12 h-12 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mb-2">
              <CheckCircle2 className="w-7 h-7 text-emerald-400" />
            </div>

            <div>
              <h2 className="text-lg font-bold text-slate-100">Space Released Successfully</h2>
              <p className="text-xs text-slate-400 mt-1">Transaction recorded & slot marked vacant</p>
            </div>

            <div className="bg-slate-950/40 border border-slate-800 rounded-2xl p-4 text-xs font-medium space-y-2.5 max-w-sm mx-auto text-left">
              <div className="flex justify-between">
                <span className="text-slate-500">Slot ID</span>
                <span className="font-mono font-bold text-slate-200">{checkoutResult.space_id}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Vehicle Plate</span>
                <span className="font-mono font-bold text-blue-400">{checkoutResult.plate_text || "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Duration</span>
                <span className="text-slate-200 font-semibold">{checkoutResult.duration_minutes} Minutes</span>
              </div>
              <div className="flex justify-between border-t border-slate-800 pt-2.5">
                <span className="text-slate-400 font-bold">Total Paid</span>
                <span className="text-emerald-400 font-extrabold text-sm">₹{checkoutResult.amount_paid.toFixed(2)}</span>
              </div>
            </div>

            <button onClick={onClose} className="btn-primary w-full justify-center">
              Close Receipt
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
