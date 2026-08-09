"use client";

import { useEffect, useRef } from "react";
import { Terminal } from "lucide-react";

export interface LogEntry {
  id: string;
  timestamp: string;
  type: "info" | "success" | "warn" | "system";
  message: string;
}

interface SecurityConsoleProps {
  logs: LogEntry[];
}

export default function SecurityConsole({ logs }: SecurityConsoleProps) {
  const logEndRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom when logs update
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  return (
    <div className="card">
      <div className="flex items-center justify-between border-b border-slate-800/50 pb-3 mb-3">
        <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
          <Terminal className="w-4.5 h-4.5 text-emerald-400" /> Security Channel Activity Console
        </span>
        <div className="flex items-center gap-2 text-[9px] text-slate-500 font-mono">
          <span className="live-dot" /> Telemetry Feed Live
        </div>
      </div>

      <div className="h-44 bg-slate-950/95 border border-slate-800 rounded-xl p-3.5 font-mono text-[11px] overflow-y-auto leading-relaxed shadow-inner">
        <div className="space-y-1">
          {logs.map((log) => {
            let typeClass = "text-slate-400";
            let typePrefix = "[INFO]";

            if (log.type === "success") {
              typeClass = "text-emerald-400 font-semibold";
              typePrefix = "[SUCCESS]";
            } else if (log.type === "warn") {
              typeClass = "text-red-400 font-semibold";
              typePrefix = "[WARNING]";
            } else if (log.type === "system") {
              typeClass = "text-violet-400 font-bold";
              typePrefix = "[SYSTEM]";
            }

            return (
              <div key={log.id} className="flex gap-2.5 border-b border-slate-900/40 pb-1">
                <span className="text-slate-600 select-none">[{log.timestamp}]</span>
                <span className={typeClass}>{typePrefix}</span>
                <span className="text-slate-300">{log.message}</span>
              </div>
            );
          })}
          {logs.length === 0 && (
            <div className="text-slate-600 italic py-2">Listening to vehicle ingress and operations traffic...</div>
          )}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  );
}
