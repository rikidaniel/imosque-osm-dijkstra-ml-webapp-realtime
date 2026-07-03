"use client";

import dynamic from "next/dynamic";

const SafarDashboard = dynamic(() => import("@/components/SafarDashboard"), {
  ssr: false,
  loading: () => (
    <div className="h-screen w-screen bg-slate-50 dark:bg-slate-950 flex items-center justify-center">
      <div className="animate-pulse flex flex-col items-center gap-3">
        <div className="w-12 h-12 rounded-full bg-gradient-to-tr from-emerald-500 to-teal-400"></div>
        <span className="text-xs font-semibold text-slate-400 dark:text-slate-500">Memuat iMosque Safar...</span>
      </div>
    </div>
  )
});

export default function Home() {
  return <SafarDashboard />;
}
