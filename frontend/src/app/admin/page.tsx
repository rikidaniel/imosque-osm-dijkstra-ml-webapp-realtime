"use client";

import { useEffect, useState } from "react";
import { Activity, Database, Gauge, Route, Wrench } from "lucide-react";
import AdminOverview from "@/components/admin/AdminOverview";
import AdvancedMaintenancePanel from "@/components/admin/AdvancedMaintenancePanel";
import RoutingCachePanel from "@/components/admin/RoutingCachePanel";
import DatasetManager from "@/components/dataset/DatasetManager";
import BenchmarkPanel from "@/components/route/BenchmarkPanel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fetchDatasets } from "@/lib/api";
import { useAppStore } from "@/lib/store";

export default function AdminDashboardPage() {
  const [activeTab, setActiveTab] = useState("dashboard");
  const setDatasets = useAppStore((state) => state.setDatasets);

  useEffect(() => {
    let mounted = true;
    fetchDatasets().then((result) => {
      if (mounted) setDatasets(result.items || []);
    }).catch(() => {
      // Masing-masing panel menampilkan error operasionalnya sendiri.
    });
    return () => { mounted = false; };
  }, [setDatasets]);

  return (
    <div className="mx-auto max-w-7xl space-y-6 pb-12">
      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
        <div className="overflow-x-auto border-b border-slate-200 dark:border-slate-800 pb-3">
          <TabsList className="h-auto min-w-max gap-1 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-1.5 shadow-sm">
            <TabsTrigger value="dashboard" className="gap-2 px-4 py-2.5 data-[state=active]:bg-slate-100 dark:data-[state=active]:bg-slate-800 dark:text-slate-300 dark:data-[state=active]:text-slate-100">
              <Activity className="h-4 w-4" /> Dashboard
            </TabsTrigger>
            <TabsTrigger value="datasets" className="gap-2 px-4 py-2.5 data-[state=active]:bg-slate-100 dark:data-[state=active]:bg-slate-800 dark:text-slate-300 dark:data-[state=active]:text-slate-100">
              <Database className="h-4 w-4" /> Dataset Masjid
            </TabsTrigger>
            <TabsTrigger value="routing" className="gap-2 px-4 py-2.5 data-[state=active]:bg-slate-100 dark:data-[state=active]:bg-slate-800 dark:text-slate-300 dark:data-[state=active]:text-slate-100">
              <Route className="h-4 w-4" /> Routing & Cache
            </TabsTrigger>
            <TabsTrigger value="evaluation" className="gap-2 px-4 py-2.5 data-[state=active]:bg-slate-100 dark:data-[state=active]:bg-slate-800 dark:text-slate-300 dark:data-[state=active]:text-slate-100">
              <Gauge className="h-4 w-4" /> Evaluasi Algoritma
            </TabsTrigger>
            <TabsTrigger value="maintenance" className="gap-2 px-4 py-2.5 data-[state=active]:bg-slate-100 dark:data-[state=active]:bg-slate-800 dark:text-slate-300 dark:data-[state=active]:text-slate-100">
              <Wrench className="h-4 w-4" /> Maintenance Lanjutan
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="dashboard"><AdminOverview /></TabsContent>
        <TabsContent value="datasets"><DatasetManager /></TabsContent>
        <TabsContent value="routing"><RoutingCachePanel /></TabsContent>
        <TabsContent value="evaluation" className="space-y-4">
          <div className="rounded-xl border border-blue-200 dark:border-blue-900/50 bg-blue-50 dark:bg-blue-950/20 p-4 text-sm text-blue-900 dark:text-blue-300">
            <strong>Evaluasi Kelompok 2:</strong> pilih titik awal dan tujuan pada peta utama, lalu bandingkan Dijkstra dan A* pada graph lokal yang sama.
          </div>
          <BenchmarkPanel />
        </TabsContent>
        <TabsContent value="maintenance"><AdvancedMaintenancePanel /></TabsContent>
      </Tabs>
    </div>
  );
}
