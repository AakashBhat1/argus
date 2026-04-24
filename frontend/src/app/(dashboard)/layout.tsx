"use client";

import Sidebar from "@/components/Sidebar";
import Chatbot from "@/components/Chatbot";
import ProtectedRoute from "@/components/ProtectedRoute";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="relative flex-1 overflow-y-auto">
          {/* Ambient background gradients */}
          <div className="pointer-events-none fixed inset-0 z-0">
            <div className="absolute top-[-10%] left-[15%] w-[700px] h-[500px] bg-blue-500/[0.035] rounded-full blur-[140px] animate-float" />
            <div className="absolute bottom-[-5%] right-[10%] w-[600px] h-[400px] bg-violet-500/[0.03] rounded-full blur-[120px] animate-float" style={{ animationDelay: "3s" }} />
            <div className="absolute top-[50%] left-[50%] w-[400px] h-[300px] bg-teal-500/[0.02] rounded-full blur-[100px] animate-float" style={{ animationDelay: "1.5s" }} />
          </div>
          {/* Noise texture overlay */}
          <div className="noise-overlay" />
          <div className="relative z-10 p-4 md:p-6 lg:p-8 max-w-[1600px] mx-auto">
            {children}
          </div>
        </main>
      </div>
      <Chatbot />
    </ProtectedRoute>
  );
}
