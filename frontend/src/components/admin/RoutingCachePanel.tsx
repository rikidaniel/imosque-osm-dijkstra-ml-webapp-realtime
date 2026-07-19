"use client";

import { useCallback, useEffect, useState } from "react";
import { HardDrive, LoaderCircle, RefreshCw, Route } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchCorridorCacheSummary } from "@/lib/api";

type CorridorItem = {
  graph_id: string;
  dataset_id?: string;
  status: string;
  artifact_ready: boolean;
  area_km2?: number;
  nodes?: number;
  edges?: number;
  size_mb?: number;
  created_at?: number;
  error?: string;
};

type CorridorSummary = {
  total: number;
  ready: number;
  building: number;
  failed: number;
  total_size_mb: number;
  max_graphs: number;
  max_concurrent_builds: number;
  items: CorridorItem[];
};

export default function RoutingCachePanel() {
  const [summary, setSummary] = useState<CorridorSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setSummary(await fetchCorridorCacheSummary(100));
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const kickoff = window.setTimeout(refresh, 0);
    const interval = window.setInterval(refresh, 10_000);
    return () => { window.clearTimeout(kickoff); window.clearInterval(interval); };
  }, [refresh]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-black text-slate-900 dark:text-slate-100">Routing & Cache Koridor</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Graph dibuat otomatis dari titik awal dan tujuan; tidak perlu membangun graph provinsi secara manual.</p>
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

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          ["Graph tersedia", summary?.ready ?? 0],
          ["Sedang dibangun", summary?.building ?? 0],
          ["Gagal/hilang", summary?.failed ?? 0],
          ["Ukuran cache", `${summary?.total_size_mb ?? 0} MB`],
        ].map(([label, value]) => (
          <Card key={String(label)} className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
            <CardContent className="pt-6">
              <p className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">{label}</p>
              <p className="mt-2 text-2xl font-black text-slate-900 dark:text-slate-100">{value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-slate-900 dark:text-slate-100">
            <HardDrive className="h-5 w-5 text-emerald-600 dark:text-emerald-400" /> Artifact graph koridor
          </CardTitle>
          <CardDescription className="text-slate-500 dark:text-slate-400">
            Maksimum {summary?.max_graphs ?? "-"} artifact dengan {summary?.max_concurrent_builds ?? "-"} build bersamaan. Cache lama dipangkas otomatis.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading && !summary ? (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-slate-500 dark:text-slate-400">
              <LoaderCircle className="h-4 w-4 animate-spin" /> Memuat cache...
            </div>
          ) : !summary?.items.length ? (
            <div className="rounded-xl border border-dashed border-slate-300 dark:border-slate-700 py-12 text-center text-sm text-slate-500 dark:text-slate-400">
              <Route className="mx-auto mb-3 h-7 w-7" /> Belum ada graph koridor. Artifact pertama akan dibuat otomatis ketika rute lokal dibutuhkan.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="border-b border-slate-200 dark:border-slate-700 text-xs uppercase tracking-wider text-slate-500 dark:text-slate-400">
                  <tr>
                    <th className="p-3">Graph</th>
                    <th className="p-3">Dataset</th>
                    <th className="p-3">Status</th>
                    <th className="p-3">Area</th>
                    <th className="p-3">Node / Edge</th>
                    <th className="p-3">Ukuran</th>
                    <th className="p-3">Dibuat</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {summary.items.map((item) => (
                    <tr key={item.graph_id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50 text-slate-700 dark:text-slate-300">
                      <td className="max-w-[190px] truncate p-3 font-mono text-xs" title={item.graph_id}>{item.graph_id}</td>
                      <td className="p-3">{item.dataset_id || "-"}</td>
                      <td className="p-3">
                        <span className={`rounded-full px-2 py-1 text-xs font-bold ${
                          item.artifact_ready
                            ? "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400"
                            : item.status === "error"
                              ? "bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-400"
                              : "bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-400"
                        }`}>{item.status}</span>
                      </td>
                      <td className="p-3">{item.area_km2 != null ? `${item.area_km2} km²` : "-"}</td>
                      <td className="p-3">{item.nodes?.toLocaleString("id-ID") ?? "-"} / {item.edges?.toLocaleString("id-ID") ?? "-"}</td>
                      <td className="p-3">{item.size_mb ?? 0} MB</td>
                      <td className="p-3">{item.created_at ? new Date(item.created_at * 1000).toLocaleString("id-ID") : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
