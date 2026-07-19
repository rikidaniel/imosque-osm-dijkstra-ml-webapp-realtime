"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Database, MapPinned, Radio, RefreshCw, Server } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchSystemHealth } from "@/lib/api";
import { useAppStore } from "@/lib/store";

type Health = {
  status?: string;
  database?: { connected?: boolean; empty?: boolean; datasets_count?: number; error?: string };
  graph_status?: string;
  graph_ready?: boolean;
  routing_dispatch?: { remote_enabled?: boolean; configured_workers?: number; local_fallback?: boolean };
  realtime_ingestion?: { enabled?: boolean; topic?: string | null; producer_initialized?: boolean };
  admin_protection_configured?: boolean;
  version?: string;
};

function StatusPill({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-bold ${
      ok
        ? "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400"
        : "bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-400"
    }`}>
      {children}
    </span>
  );
}

export default function AdminOverview() {
  const datasets = useAppStore((state) => state.datasets);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const totalMosques = useMemo(
    () => datasets.reduce((sum, dataset) => sum + (dataset.enriched_rows ?? dataset.profile?.enriched_rows ?? dataset.mosque_count ?? 0), 0),
    [datasets],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setHealth(await fetchSystemHealth());
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
      setHealth(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const kickoff = window.setTimeout(refresh, 0);
    const interval = window.setInterval(refresh, 30_000);
    return () => { window.clearTimeout(kickoff); window.clearInterval(interval); };
  }, [refresh]);

  const apiHealthy = health?.status === "healthy";
  const databaseHealthy = Boolean(health?.database?.connected);
  const remoteRouting = Boolean(health?.routing_dispatch?.remote_enabled);
  const realtimeEnabled = Boolean(health?.realtime_ingestion?.enabled);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-black text-slate-900 dark:text-slate-100">Kesehatan Sistem Nasional</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Data ini dibaca langsung dari API dan diperbarui setiap 30 detik.</p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading}
          className="border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700">
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Segarkan
        </Button>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/20 p-4 text-sm text-rose-700 dark:text-rose-400">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
          <CardContent className="pt-6">
            <Database className="mb-3 h-5 w-5 text-emerald-600 dark:text-emerald-400" />
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">Dataset nasional</p>
            <p className="mt-1 text-3xl font-black text-slate-900 dark:text-slate-100">{datasets.length}</p>
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">Seluruh dataset aktif bersamaan</p>
          </CardContent>
        </Card>
        <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
          <CardContent className="pt-6">
            <MapPinned className="mb-3 h-5 w-5 text-teal-600 dark:text-teal-400" />
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">Masjid tersimpan</p>
            <p className="mt-1 text-3xl font-black text-slate-900 dark:text-slate-100">{totalMosques.toLocaleString("id-ID")}</p>
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">Data masjid yang selesai diproses</p>
          </CardContent>
        </Card>
        <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
          <CardContent className="pt-6">
            <Server className="mb-3 h-5 w-5 text-blue-600 dark:text-blue-400" />
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">API & Database</p>
            <div className="mt-3"><StatusPill ok={apiHealthy && databaseHealthy}>{apiHealthy && databaseHealthy ? "Sehat" : "Perlu diperiksa"}</StatusPill></div>
            <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">Versi API {health?.version || "-"}</p>
          </CardContent>
        </Card>
        <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
          <CardContent className="pt-6">
            <Activity className="mb-3 h-5 w-5 text-violet-600 dark:text-violet-400" />
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">Routing worker</p>
            <div className="mt-3"><StatusPill ok={remoteRouting}>{remoteRouting ? "Remote aktif" : "Fallback lokal"}</StatusPill></div>
            <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">{health?.routing_dispatch?.configured_workers ?? 0} worker terkonfigurasi</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
          <CardHeader>
            <CardTitle className="text-slate-900 dark:text-slate-100">Routing & Graph</CardTitle>
            <CardDescription className="text-slate-500 dark:text-slate-400">Kondisi jalur komputasi rute saat ini.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-slate-700 dark:text-slate-300">
            <div className="flex items-center justify-between"><span>Remote routing</span><StatusPill ok={remoteRouting}>{remoteRouting ? "Aktif" : "Nonaktif"}</StatusPill></div>
            <div className="flex items-center justify-between"><span>Fallback lokal</span><StatusPill ok={Boolean(health?.routing_dispatch?.local_fallback)}>{health?.routing_dispatch?.local_fallback ? "Diizinkan" : "Nonaktif"}</StatusPill></div>
            <div className="flex items-center justify-between"><span>Graph runtime legacy</span><StatusPill ok={Boolean(health?.graph_ready)}>{health?.graph_status || "Belum dimuat"}</StatusPill></div>
          </CardContent>
        </Card>
        <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-slate-900 dark:text-slate-100"><Radio className="h-4 w-4" /> Realtime</CardTitle>
            <CardDescription className="text-slate-500 dark:text-slate-400">Kafka tidak berada pada jalur sinkron pencarian rute.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-slate-700 dark:text-slate-300">
            <div className="flex items-center justify-between"><span>Ingestion Kafka</span><StatusPill ok={realtimeEnabled}>{realtimeEnabled ? "Aktif" : "Belum dikonfigurasi"}</StatusPill></div>
            <div className="flex items-center justify-between"><span>Producer</span><StatusPill ok={Boolean(health?.realtime_ingestion?.producer_initialized)}>{health?.realtime_ingestion?.producer_initialized ? "Siap" : "Belum dimulai"}</StatusPill></div>
            <div className="flex items-center justify-between"><span>Proteksi superadmin</span><StatusPill ok={Boolean(health?.admin_protection_configured)}>{health?.admin_protection_configured ? "Token aktif" : "Mode lokal"}</StatusPill></div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
