"use client";

import DatasetManager from "@/components/dataset/DatasetManager";
import BenchmarkPanel from "@/components/route/BenchmarkPanel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Database, Zap, LayoutDashboard, FileText } from "lucide-react";
import { useState } from "react";
import { useAppStore } from "@/lib/store";

export default function AdminDashboardPage() {
  const [activeTab, setActiveTab] = useState("datasets");
  const { datasets } = useAppStore();

  const totalMasjids = datasets.reduce((acc, d) => acc + (d.enriched_rows ?? d.profile?.enriched_rows ?? 0), 0);

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-12">
      {/* Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="p-6 rounded-2xl bg-slate-950/40 border border-slate-800/80 shadow-md">
          <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Total Dataset</div>
          <div className="text-3xl font-black text-emerald-400">{datasets.length}</div>
          <div className="text-xs text-slate-400 mt-2">Dataset wilayah aktif</div>
        </div>
        <div className="p-6 rounded-2xl bg-slate-950/40 border border-slate-800/80 shadow-md">
          <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Total Masjid Tersimpan</div>
          <div className="text-3xl font-black text-emerald-400">{totalMasjids}</div>
          <div className="text-xs text-slate-400 mt-2">Masjid terproses ML & Graph</div>
        </div>
        <div className="p-6 rounded-2xl bg-slate-950/40 border border-slate-800/80 shadow-md">
          <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Status Pipeline</div>
          <div className="text-3xl font-black text-teal-400">Aktif</div>
          <div className="text-xs text-slate-400 mt-2">ML Enrichment Online</div>
        </div>
      </div>

      {/* Tabs Menu */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
        <div className="flex items-center justify-between border-b border-slate-800/60 pb-3">
          <TabsList className="bg-slate-950/60 p-1.5 rounded-xl border border-slate-800/80">
            <TabsTrigger
              value="datasets"
              className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold transition-all data-[state=active]:bg-emerald-600 data-[state=active]:text-white"
            >
              <Database className="w-4 h-4" />
              Kelola Dataset
            </TabsTrigger>
            <TabsTrigger
              value="benchmarks"
              className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold transition-all data-[state=active]:bg-emerald-600 data-[state=active]:text-white"
            >
              <Zap className="w-4 h-4" />
              Uji Performa Rute
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="datasets" className="outline-none">
          <div className="grid grid-cols-1 gap-8">
            <DatasetManager />
          </div>
        </TabsContent>

        <TabsContent value="benchmarks" className="outline-none">
          <div className="grid grid-cols-1 gap-8">
            <div className="p-4 bg-slate-950/30 border border-slate-800/80 rounded-2xl mb-2 text-sm text-slate-400">
              💡 <strong>Tips:</strong> Sebelum menjalankan pengujian performa komparatif, pastikan Anda telah memilih titik awal dan titik tujuan dengan mengklik peta di halaman utama terlebih dahulu.
            </div>
            <BenchmarkPanel />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
