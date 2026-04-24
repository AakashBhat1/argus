"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { Shield } from "lucide-react";

export default function ProtectedRoute({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [authorized, setAuthorized] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/login");
    } else {
      setAuthorized(true);
    }
  }, [router]);

  if (!authorized) {
    return (
      <div className="h-screen flex items-center justify-center bg-slate-950">
        <div className="flex flex-col items-center gap-4 animate-fade-in">
          <div className="relative w-12 h-12 rounded-2xl bg-gradient-to-br from-blue-500/15 to-violet-500/15 border border-blue-500/20 flex items-center justify-center">
            <Shield className="w-6 h-6 text-blue-400" />
            <div className="absolute inset-0 rounded-2xl bg-blue-500/10 animate-glow-pulse" />
          </div>
          <div className="w-6 h-6 border-2 border-blue-500/40 border-t-blue-400 rounded-full animate-spin" />
          <p className="text-sm text-slate-500 font-medium">Authenticating...</p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
