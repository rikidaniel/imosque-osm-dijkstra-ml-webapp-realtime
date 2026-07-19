"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchSystemHealth } from "@/lib/api";

export default function AdminApiStatus() {
  const [state, setState] = useState<"checking" | "healthy" | "unhealthy">("checking");

  const refresh = useCallback(async () => {
    try {
      const health = await fetchSystemHealth();
      setState(health.status === "healthy" && health.database?.connected ? "healthy" : "unhealthy");
    } catch {
      setState("unhealthy");
    }
  }, []);

  useEffect(() => {
    const kickoff = window.setTimeout(refresh, 0);
    const interval = window.setInterval(refresh, 15_000);
    return () => { window.clearTimeout(kickoff); window.clearInterval(interval); };
  }, [refresh]);

  const label = state === "healthy" ? "API & Database: Connected" : state === "unhealthy" ? "API: Bermasalah" : "Memeriksa API...";
  const color = state === "healthy" ? "bg-emerald-500" : state === "unhealthy" ? "bg-rose-500" : "bg-amber-400";

  return (
    <div className="hidden md:flex items-center gap-2" role="status" aria-live="polite">
      <span className={`h-2.5 w-2.5 rounded-full ${color} ${state === "checking" ? "animate-pulse" : ""}`} />
      <span className="text-xs font-bold text-slate-500 dark:text-slate-400">{label}</span>
    </div>
  );
}
