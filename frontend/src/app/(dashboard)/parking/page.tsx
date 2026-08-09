"use client";

import { useEffect, useState, useCallback } from "react";
import {
  api,
  type ParkingSpace,
  type ParkingStats,
  type ParkingActivity,
  type ReleaseSpace,
} from "@/lib/api";
import { useWebSocket } from "@/lib/websocket";
import ParkingTower3D from "@/components/ParkingTower3D";
import SlotGrid from "@/components/parking/SlotGrid";
import CheckoutModal from "@/components/parking/CheckoutModal";
import {
  Car,
  RefreshCw,
  Bell,
  Info,
  CheckCircle2,
  AlertTriangle,
  X,
} from "lucide-react";

interface ToastMessage {
  id: string;
  type: "success" | "warning" | "info";
  title: string;
  message: string;
  time: string;
}

export default function ParkingDashboardPage() {
  const [mounted, setMounted] = useState(false);
  const [spaces, setSpaces] = useState<ParkingSpace[]>([]);
  const [stats, setStats] = useState<ParkingStats | null>(null);
  const [activities, setActivities] = useState<ParkingActivity[]>([]);
  const [loading, setLoading] = useState(true);
  const [releasing, setReleasing] = useState(false);

  // Modal / Selected Space State
  const [selectedSpace, setSelectedSpace] = useState<ParkingSpace | null>(null);
  const [showCheckoutModal, setShowCheckoutModal] = useState(false);
  const [checkoutResult, setCheckoutResult] = useState<ReleaseSpace | null>(null);

  // Custom Toast State
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  // Subscribe to tenant-scoped websocket channel "parking"
  const { lastMessage, isConnected } = useWebSocket("parking");

  // Load data from API
  const loadData = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [sList, sStats, act] = await Promise.all([
        api.parking.spaces(),
        api.parking.stats(),
        api.parking.activity(),
      ]);
      setSpaces(sList);
      setStats(sStats);
      setActivities(act);
    } catch (err) {
      console.error("Failed to load parking dashboard data:", err);
      addToast("warning", "Data Error", "Failed to retrieve real-time parking data.");
    } finally {
      setLoading(false);
    }
  }, []);

  // Helper to add toast notification
  const addToast = useCallback((type: "success" | "warning" | "info", title: string, message: string) => {
    const id = Math.random().toString(36).substring(2, 9);
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const newToast = { id, type, title, message, time };
    setToasts((prev) => [newToast, ...prev].slice(0, 5)); // Keep last 5 toasts

    // Auto-dismiss after 6 seconds
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 6000);
  }, []);

  // Set mounted check for Next.js SSR
  useEffect(() => {
    setMounted(true);
    loadData();
    const interval = setInterval(() => loadData(true), 30000); // Poll every 30s as fallback
    return () => clearInterval(interval);
  }, [loadData]);

  // Handle WebSocket updates
  useEffect(() => {
    if (!lastMessage) return;

    if (lastMessage.type === "parking" && lastMessage.data) {
      const { event, space_id, plate_text, assign } = lastMessage.data;

      if (event === "plate_detected") {
        if (assign) {
          addToast(
            assign.profile_type === "blacklist" ? "warning" : "success",
            "Vehicle Ingested",
            `Plate ${plate_text} detected. Assigned space ${assign.space_id}.`
          );
        } else {
          addToast("info", "Plate Recognized", `OCR detected plate: ${plate_text}`);
        }
        loadData(true);
      } else if (event === "exit") {
        addToast(
          "success",
          "Space Released",
          `Space ${space_id} released. Plate ${plate_text || "N/A"} checked out.`
        );
        loadData(true);
      }
    }
  }, [lastMessage, loadData, addToast]);



  // Local helper to calculate live estimated tariff
  const calculateLiveTariff = (entryTimeStr: string | null | undefined) => {
    if (!entryTimeStr) return { durationText: "N/A", cost: 10.0 };

    const entryTime = new Date(entryTimeStr);
    const now = new Date();
    const durationMs = now.getTime() - entryTime.getTime();
    const durationMinutes = Math.max(0, Math.floor(durationMs / 60000));

    // Formatted duration string
    let durationText = "";
    if (durationMinutes < 60) {
      durationText = `${durationMinutes}m`;
    } else {
      const hrs = Math.floor(durationMinutes / 60);
      const mins = durationMinutes % 60;
      durationText = `${hrs}h ${mins}m`;
    }

    // Tariff calculation rules matching backend
    const freeMinutes = 5;
    const shortStayRate = 10.0;
    const hourlyRate = 20.0;

    let cost = shortStayRate;
    if (durationMinutes > freeMinutes) {
      const hours = Math.ceil(durationMinutes / 60.0);
      cost = hours * hourlyRate;
    }

    return { durationText, cost };
  };

  const handleOpenCheckout = (space: ParkingSpace) => {
    if (!space.is_occupied) return;
    setSelectedSpace(space);
    setCheckoutResult(null);
    setShowCheckoutModal(true);
  };

  const handleProcessCheckout = async () => {
    if (!selectedSpace) return;
    setReleasing(true);
    try {
      const result = await api.parking.release(selectedSpace.space_id);
      setCheckoutResult(result);
      addToast(
        "success",
        "Checkout Completed",
        `Released space ${selectedSpace.space_id}. Amount Paid: ₹${result.amount_paid.toFixed(2)}`
      );
      // Reload stats and spaces
      await loadData(true);
    } catch (err) {
      console.error("Checkout failed:", err);
      addToast("warning", "Checkout Failed", "Failed to release the parking space.");
    } finally {
      setReleasing(false);
    }
  };

  const handleCloseModal = () => {
    setShowCheckoutModal(false);
    setSelectedSpace(null);
    setCheckoutResult(null);
  };

  return (
    <div className="space-y-6 animate-fade-in relative min-h-screen pb-16">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">
            <span className="gradient-text-static">Parking Control Tower</span>
          </h1>
          <p className="page-subtitle">Interactive 3D visualizer, slot grid & smart tariff processing</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => loadData()}
            className="hidden md:flex items-center gap-2 text-[11px] text-slate-400 bg-slate-900/60 border border-slate-700/30 rounded-xl px-3.5 py-2 hover:bg-slate-800/60 hover:text-slate-200 transition-all duration-200"
            disabled={loading}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <div className="flex items-center gap-2 text-[11px] bg-slate-900/60 border border-slate-700/30 rounded-xl px-3.5 py-2 backdrop-blur-sm">
            <span className={isConnected ? "status-led-active" : "status-led-error"} />
            <span className={isConnected ? "text-emerald-400 font-medium" : "text-red-400"}>
              {isConnected ? "Live Channel Connected" : "Channel Disconnected"}
            </span>
          </div>
        </div>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger-children">
        <div className="card">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Total Capacity</p>
          <p className="stat-value text-blue-400">{stats?.total ?? 24}</p>
          <p className="text-[10px] text-slate-500 mt-1">Seeded slots in tenant scope</p>
        </div>
        <div className="card">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Occupied Slots</p>
          <p className="stat-value text-indigo-400">{stats?.occupied ?? 0}</p>
          <p className="text-[10px] text-slate-500 mt-1">
            {stats?.free ?? 24} spaces vacant
          </p>
        </div>
        <div className="card">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Occupancy Rate</p>
          <p className="stat-value text-violet-400">
            {stats ? stats.occupancy_pct.toFixed(1) : "0.0"}%
          </p>
          <p className="text-[10px] text-slate-500 mt-1">Capacity utilization index</p>
        </div>
        <div className="card">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Plates Detected Today</p>
          <p className="stat-value text-emerald-400">{stats?.plates_today ?? 0}</p>
          <p className="text-[10px] text-slate-500 mt-1">Ingested via gate cameras</p>
        </div>
      </div>

      {/* Main Split Dashboard: 3D Scene + Flat Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: 3D Visualizer */}
        <div className="lg:col-span-7 flex flex-col gap-4">
          <div className="flex justify-between items-center bg-slate-900/40 border border-slate-700/20 px-4 py-2.5 rounded-xl">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
              <Car className="w-3.5 h-3.5 text-blue-400" /> 3D Digital Twin Visualizer
            </span>
            <span className="text-[10px] text-slate-500 italic">Click occupied vehicle to checkout</span>
          </div>
          {mounted ? (
            <ParkingTower3D spaces={spaces} onReleaseSpace={(id) => {
              const space = spaces.find((s) => s.space_id === id);
              if (space) handleOpenCheckout(space);
            }} />
          ) : (
            <div className="w-full h-[520px] bg-slate-900/20 border border-slate-800 rounded-2xl flex items-center justify-center text-slate-500">
              Initializing WebGL Context...
            </div>
          )}
        </div>

        {/* Right Column: Interactive Grid grouped by Floor */}
        <div className="lg:col-span-5 flex flex-col gap-6">
          <div className="bg-slate-900/40 border border-slate-700/20 px-4 py-2.5 rounded-xl">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
              <Info className="w-3.5 h-3.5 text-indigo-400" /> Space Occupancy Matrix
            </span>
          </div>

          <SlotGrid
            spaces={spaces}
            onSelectSpace={handleOpenCheckout}
            calculateLiveTariff={calculateLiveTariff}
          />
        </div>
      </div>

      {/* Recent Activity Section */}
      <div className="card">
        <div className="card-header flex justify-between items-center">
          <span className="flex items-center gap-1.5"><Bell className="w-3.5 h-3.5 text-blue-400" /> Audit Log & Recent Operations</span>
          <span className="text-[10px] text-slate-500 normal-case font-normal">Updated in real-time</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr>
                <th className="table-header">Timestamp</th>
                <th className="table-header">Event</th>
                <th className="table-header">Plate</th>
                <th className="table-header">Slot</th>
                <th className="table-header">Operator</th>
                <th className="table-header">Details</th>
              </tr>
            </thead>
            <tbody>
              {activities.length > 0 ? (
                activities.slice(0, 10).map((act) => (
                  <tr key={act.id} className="tr-hover text-xs">
                    <td className="py-3 text-slate-400 font-mono">
                      {new Date(act.timestamp).toLocaleTimeString()}
                    </td>
                    <td className="py-3 font-semibold">
                      <span
                        className={`badge ${
                          act.event_type === "entry"
                            ? "bg-blue-500/10 text-blue-400 border-blue-500/20"
                            : act.event_type === "exit"
                            ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                            : "bg-slate-800 text-slate-400 border-slate-700/50"
                        }`}
                      >
                        {act.event_type}
                      </span>
                    </td>
                    <td className="py-3 font-mono font-bold text-slate-300">
                      {act.plate_text || "—"}
                    </td>
                    <td className="py-3 font-mono font-semibold text-slate-300">
                      {act.space_id || "—"}
                    </td>
                    <td className="py-3 text-slate-500 font-mono">
                      {act.actor_user_id ? act.actor_user_id.substring(0, 8) : "System"}
                    </td>
                    <td className="py-3 text-slate-400 truncate max-w-[280px]">
                      {act.description}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-slate-500 text-xs italic">
                    No recent activity recorded
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Floating Custom Toast Notifications */}
      <div className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 w-80 pointer-events-none">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`pointer-events-auto p-4 rounded-xl shadow-2xl border backdrop-blur-md transition-all duration-300 animate-slide-up flex gap-3 ${
              toast.type === "success"
                ? "bg-slate-950/90 border-emerald-500/30 text-slate-200"
                : toast.type === "warning"
                ? "bg-slate-950/90 border-red-500/30 text-slate-200"
                : "bg-slate-950/90 border-blue-500/30 text-slate-200"
            }`}
          >
            <div className="mt-0.5">
              {toast.type === "success" ? (
                <CheckCircle2 className="w-5 h-5 text-emerald-400" />
              ) : toast.type === "warning" ? (
                <AlertTriangle className="w-5 h-5 text-red-400" />
              ) : (
                <Bell className="w-5 h-5 text-blue-400" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between">
                <p className="text-xs font-bold text-slate-100">{toast.title}</p>
                <span className="text-[9px] text-slate-500 font-mono">{toast.time}</span>
              </div>
              <p className="text-[11px] text-slate-400 mt-1 leading-relaxed">{toast.message}</p>
            </div>
            <button
              onClick={() => setToasts((prev) => prev.filter((t) => t.id !== toast.id))}
              className="text-slate-500 hover:text-slate-300 self-start"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>

      {/* Modal - Space Checkout Dialog */}
      <CheckoutModal
        isOpen={showCheckoutModal}
        onClose={handleCloseModal}
        space={selectedSpace}
        onCheckout={handleProcessCheckout}
        releasing={releasing}
        checkoutResult={checkoutResult}
        calculateLiveTariff={calculateLiveTariff}
      />
    </div>
  );
}
