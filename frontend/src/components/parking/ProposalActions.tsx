"use client";

import { Check, Loader2, X } from "lucide-react";

export type ProposalState = "pending" | "confirming" | "confirmed" | "cancelled" | "failed";

interface Props {
  state: ProposalState;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Confirm / cancel row under a ParkBot proposal. Nothing runs until Confirm. */
export default function ProposalActions({ state, onConfirm, onCancel }: Props) {
  if (state === "confirmed") {
    return <p className="mt-2 text-[10px] text-emerald-400 font-semibold uppercase tracking-wider">Confirmed</p>;
  }
  if (state === "cancelled") {
    return <p className="mt-2 text-[10px] text-slate-500 font-semibold uppercase tracking-wider">Cancelled: nothing was changed</p>;
  }
  if (state === "failed") {
    return <p className="mt-2 text-[10px] text-red-400 font-semibold uppercase tracking-wider">Not run (expired or already used)</p>;
  }
  const busy = state === "confirming";
  return (
    <div className="mt-2 pt-2 border-t border-slate-700/40 space-y-2">
      <p className="text-[10px] text-amber-300 font-semibold uppercase tracking-wider">Review before it runs</p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          className="flex items-center gap-1 text-[10px] font-bold px-2.5 py-1 rounded-md border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />} Confirm
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="flex items-center gap-1 text-[10px] font-bold px-2.5 py-1 rounded-md border border-slate-600/40 text-slate-300 hover:bg-slate-800/60 disabled:opacity-50"
        >
          <X className="w-3 h-3" /> Cancel
        </button>
      </div>
    </div>
  );
}
