"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { removeToken } from "@/lib/auth";
import {
  LayoutDashboard,
  Camera,
  BarChart3,
  Bell,
  Activity,
  Cpu,
  Eye,
  LogOut,
  Menu,
  X,
} from "lucide-react";

const navSections = [
  {
    label: "Overview",
    items: [
      { href: "/", label: "Dashboard", icon: LayoutDashboard },
      { href: "/cameras", label: "Cameras", icon: Camera },
    ],
  },
  {
    label: "Analysis",
    items: [
      { href: "/analytics", label: "Analytics", icon: BarChart3 },
      { href: "/alerts", label: "Alerts", icon: Bell },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/performance", label: "Performance", icon: Activity },
      { href: "/live-metrics", label: "Inference", icon: Cpu },
    ],
  },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);

  function handleLogout() {
    removeToken();
    router.push("/login");
  }

  const sidebarContent = (
    <>
      {/* Logo */}
      <div className="px-5 py-5 border-b border-slate-800/40">
        <div className="flex items-center gap-3">
          <div className="relative w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500/20 to-violet-600/20 border border-blue-500/15 flex items-center justify-center overflow-hidden">
            <Eye className="w-5 h-5 text-blue-400 relative z-10" />
            <div className="absolute inset-0 rounded-xl bg-blue-500/10 animate-glow-pulse" />
            <div className="absolute inset-0 bg-gradient-to-t from-violet-500/10 to-transparent" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight">
              <span className="gradient-text">Argus</span>
            </h1>
            <p className="text-[10px] text-slate-500/80 tracking-wider font-medium uppercase">
              OpenVINO Edition
            </p>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-3 overflow-y-auto" role="navigation">
        {navSections.map((section) => (
          <div key={section.label} className="mb-4">
            <p className="px-3 mb-2 text-[9px] font-bold text-slate-600 uppercase tracking-[0.16em]">
              {section.label}
            </p>
            <div className="space-y-0.5">
              {section.items.map((item) => {
                const Icon = item.icon;
                const isActive = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setMobileOpen(false)}
                    className={cn(
                      "group relative flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px] transition-all duration-200",
                      isActive
                        ? "text-blue-400 font-medium bg-blue-500/[0.08]"
                        : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
                    )}
                    aria-current={isActive ? "page" : undefined}
                  >
                    {isActive && (
                      <span className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-gradient-to-b from-blue-400 to-violet-500" />
                    )}
                    <Icon
                      className={cn(
                        "w-[18px] h-[18px] transition-colors",
                        isActive
                          ? "text-blue-400"
                          : "text-slate-500 group-hover:text-slate-300"
                      )}
                    />
                    {item.label}
                    {item.label === "Inference" && (
                      <span className="ml-auto text-[8px] font-bold bg-violet-500/15 text-violet-400 border border-violet-500/25 rounded px-1.5 py-0.5 tracking-wider">
                        OV
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-3 py-4 border-t border-slate-800/40">
        <button
          onClick={handleLogout}
          className="flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px] text-slate-500 hover:text-red-400 hover:bg-red-500/[0.06] transition-all duration-200 w-full group"
          aria-label="Log out"
        >
          <LogOut className="w-[18px] h-[18px] transition-transform duration-200 group-hover:rotate-[-12deg]" />
          Log Out
        </button>
        <div className="mt-3 mx-3 pt-3 border-t border-slate-800/30">
          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            <span className="live-dot" />
            <span className="font-medium">System Online</span>
          </div>
          <p className="text-[9px] text-slate-600 mt-2 font-mono tracking-wide">
            Intel Arc GPU &middot; NPU &middot; CPU
          </p>
        </div>
      </div>
    </>
  );

  return (
    <>
      {/* Mobile toggle */}
      <button
        onClick={() => setMobileOpen(true)}
        className="md:hidden fixed top-3 left-3 z-50 p-2 rounded-xl bg-slate-900/90 border border-slate-700/50 text-slate-400 hover:text-slate-100 backdrop-blur-sm"
        aria-label="Open navigation menu"
      >
        <Menu className="w-5 h-5" />
      </button>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/60 backdrop-blur-sm z-40"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Mobile sidebar */}
      <aside
        className={cn(
          "md:hidden fixed inset-y-0 left-0 w-60 bg-slate-950/95 border-r border-slate-800/60 flex flex-col z-50 backdrop-blur-md transition-transform duration-300 ease-out",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <button
          onClick={() => setMobileOpen(false)}
          className="absolute top-3 right-3 p-1.5 rounded-lg text-slate-500 hover:text-slate-300"
          aria-label="Close navigation menu"
        >
          <X className="w-4 h-4" />
        </button>
        {sidebarContent}
      </aside>

      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-[240px] bg-slate-950/70 border-r border-slate-800/40 flex-col shrink-0 backdrop-blur-xl z-10 relative">
        {sidebarContent}
        {/* Subtle right edge glow */}
        <div className="pointer-events-none absolute top-0 right-0 w-[1px] h-full bg-gradient-to-b from-transparent via-slate-700/20 to-transparent" />
      </aside>
    </>
  );
}
