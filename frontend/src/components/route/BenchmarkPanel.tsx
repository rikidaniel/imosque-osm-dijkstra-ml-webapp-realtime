"use client";

import { useState } from "react";
import { useAppStore } from "@/lib/store";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import { formatDistance } from "@/lib/utils";
import { Zap, ShieldAlert, BarChart3, Download, Clock } from "lucide-react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Cell } from "recharts";
import { runRouteBenchmark } from "@/lib/api";

interface AlgorithmBenchmark {
  execution_time_ms: number;
  explored_nodes: number;
  examined_edges: number;
  route_distance_km: number;
  memory_usage_kb: number;
}

interface BenchmarkResult {
  dijkstra: AlgorithmBenchmark;
  astar: AlgorithmBenchmark;
  comparison: {
    faster_algorithm: string;
    time_difference_ms: number;
    efficiency_gain_percent: number;
    fewer_explored_algorithm: string;
    explored_nodes_difference: number;
    optimal_cost_match: boolean;
  };
}

export default function BenchmarkPanel() {
  const { activeDatasetId, startPoint, endPoint } = useAppStore();
  const [loading, setLoading] = useState(false);
  const [benchmarkResult, setBenchmarkResult] = useState<BenchmarkResult | null>(null);

  const [currentTime] = useState("17:00");
  const [prayer, setPrayer] = useState("maghrib");
  const [profile] = useState("balanced");
  const [bufferKm, setBufferKm] = useState("10");

  const handleRunBenchmark = async () => {
    if (!activeDatasetId) {
      toast.error("Pilih dataset terlebih dahulu.");
      return;
    }
    if (!startPoint || !endPoint) {
      toast.error("Pilih titik awal dan tujuan di peta terlebih dahulu.");
      return;
    }

    setLoading(false);
    setLoading(true);
    try {
      const payload = {
        dataset_id: activeDatasetId,
        origin: {
          latitude: startPoint.lat,
          longitude: startPoint.lng
        },
        destination: {
          latitude: endPoint.lat,
          longitude: endPoint.lng
        },
        departure_time: `${new Date().toLocaleDateString("en-CA")}T${currentTime}:00+07:00`,
        prayer: prayer,
        profile: profile,
        search_radius_km: parseFloat(bufferKm)
      };

      const data = await runRouteBenchmark(payload, (preparation) => {
        toast.loading(
          preparation.message || "Menyiapkan graph jalan wilayah rute...",
          { id: "benchmark-corridor" }
        );
      });
      setBenchmarkResult(data.benchmark);
      toast.success("Benchmark selesai.");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Gagal menjalankan benchmark");
    } finally {
      toast.dismiss("benchmark-corridor");
      setLoading(false);
    }
  };

  const handleExportJSON = () => {
    if (!benchmarkResult) return;
    const blob = new Blob([JSON.stringify(benchmarkResult, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `benchmark_result_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success("Benchmark berhasil diekspor ke JSON.");
  };

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-amber-500" />
            Uji Performa Komparatif
          </CardTitle>
          <CardDescription>
            Bandingkan pathfinding murni Dijkstra bidirectional dan A* pada graph lokal yang sama.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Target Salat</Label>
              <Select value={prayer} onValueChange={(val) => setPrayer(val || "maghrib")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="subuh">Subuh</SelectItem>
                  <SelectItem value="dzuhur">Dzuhur</SelectItem>
                  <SelectItem value="ashar">Ashar</SelectItem>
                  <SelectItem value="maghrib">Maghrib</SelectItem>
                  <SelectItem value="isya">Isya</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Radius (km)</Label>
              <Input type="number" min="2" max="50" value={bufferKm} onChange={e => setBufferKm(e.target.value)} />
            </div>
          </div>

          <Button 
            className="w-full bg-slate-900 hover:bg-slate-800 text-white" 
            onClick={handleRunBenchmark} 
            disabled={loading || !startPoint || !endPoint}
          >
            <BarChart3 className="w-4 h-4 mr-2" />
            {loading ? "Menghitung..." : "Mulai Bandingkan Algoritma"}
          </Button>

          {(!startPoint || !endPoint) && (
            <div className="flex items-center gap-2 text-xs text-amber-600 bg-amber-50 p-2.5 rounded-lg border border-amber-200">
              <ShieldAlert className="w-4 h-4 flex-shrink-0" />
              <span>Harap klik peta untuk menentukan titik Awal & Tujuan terlebih dahulu.</span>
            </div>
          )}
        </CardContent>
      </Card>

      {benchmarkResult && (
        <Card>
          <CardHeader className="pb-3 flex flex-row items-center justify-between">
            <CardTitle>Hasil Perbandingan</CardTitle>
            <Button size="sm" variant="outline" onClick={handleExportJSON}>
              <Download className="w-3.5 h-3.5 mr-1" />
              Ekspor JSON
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Efficiency Box */}
            <div className="p-3 bg-emerald-50 border border-emerald-100 rounded-lg text-emerald-800 text-sm">
              {benchmarkResult.comparison.faster_algorithm === "Sama" ? (
                <>Waktu eksekusi kedua algoritma sama.</>
              ) : (
                <>Algoritma <strong>{benchmarkResult.comparison.faster_algorithm}</strong> lebih cepat{" "}
                <strong>{benchmarkResult.comparison.time_difference_ms} ms</strong> (selisih{" "}
                <strong>{benchmarkResult.comparison.efficiency_gain_percent}%</strong>).</>
              )}{" "}
              {benchmarkResult.comparison.fewer_explored_algorithm === "Sama" ? (
                <>Keduanya mengeksplorasi jumlah node yang sama.</>
              ) : (
                <><strong>{benchmarkResult.comparison.fewer_explored_algorithm}</strong> mengeksplorasi{" "}
                <strong>{benchmarkResult.comparison.explored_nodes_difference}</strong> node lebih sedikit.</>
              )}
              {benchmarkResult.comparison.optimal_cost_match && " Bobot waktu tempuh keduanya terverifikasi sama optimal."}
            </div>

            {/* Comparison Table */}
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 dark:bg-slate-900/50 border-b">
                    <th className="p-2.5 text-left font-semibold">Metrik</th>
                    <th className="p-2.5 text-center font-semibold bg-blue-50/50 dark:bg-blue-950/20 text-blue-800 dark:text-blue-400">Dijkstra</th>
                    <th className="p-2.5 text-center font-semibold bg-emerald-50/50 dark:bg-emerald-950/20 text-emerald-800 dark:text-emerald-400">A*</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  <tr>
                    <td className="p-2.5 font-medium">Lama Eksekusi</td>
                    <td className="p-2.5 text-center bg-blue-50/20 dark:bg-blue-950/10">{benchmarkResult.dijkstra.execution_time_ms} ms</td>
                    <td className="p-2.5 text-center bg-emerald-50/20 dark:bg-emerald-950/10">{benchmarkResult.astar.execution_time_ms} ms</td>
                  </tr>
                  <tr>
                    <td className="p-2.5 font-medium">Nodes Dieksplorasi</td>
                    <td className="p-2.5 text-center bg-blue-50/20 dark:bg-blue-950/10">{benchmarkResult.dijkstra.explored_nodes} nodes</td>
                    <td className="p-2.5 text-center bg-emerald-50/20 dark:bg-emerald-950/10">{benchmarkResult.astar.explored_nodes} nodes</td>
                  </tr>
                  <tr>
                    <td className="p-2.5 font-medium">Edge Diperiksa</td>
                    <td className="p-2.5 text-center bg-blue-50/20 dark:bg-blue-950/10">{benchmarkResult.dijkstra.examined_edges}</td>
                    <td className="p-2.5 text-center bg-emerald-50/20 dark:bg-emerald-950/10">{benchmarkResult.astar.examined_edges}</td>
                  </tr>
                  <tr>
                    <td className="p-2.5 font-medium">Jarak Terpendek</td>
                    <td className="p-2.5 text-center bg-blue-50/20 dark:bg-blue-950/10">{formatDistance(benchmarkResult.dijkstra.route_distance_km)}</td>
                    <td className="p-2.5 text-center bg-emerald-50/20 dark:bg-emerald-950/10">{formatDistance(benchmarkResult.astar.route_distance_km)}</td>
                  </tr>
                  <tr>
                    <td className="p-2.5 font-medium">Estimasi Memori</td>
                    <td className="p-2.5 text-center bg-blue-50/20 dark:bg-blue-950/10">{benchmarkResult.dijkstra.memory_usage_kb} KB</td>
                    <td className="p-2.5 text-center bg-emerald-50/20 dark:bg-emerald-950/10">{benchmarkResult.astar.memory_usage_kb} KB</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Interactive Charts (Recharts) */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              {/* Execution Time Chart */}
              <div className="bg-slate-50/50 dark:bg-slate-950/30 p-4 rounded-xl border border-slate-100 dark:border-slate-800/80">
                <h4 className="text-xs font-bold text-slate-700 dark:text-slate-300 mb-3 flex items-center gap-1.5">
                  <Clock className="w-3.5 h-3.5 text-blue-500" />
                  Waktu Eksekusi (ms)
                </h4>
                <div className="h-40 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart 
                      data={[
                        { name: "Dijkstra", value: benchmarkResult.dijkstra.execution_time_ms, color: "#3b82f6" },
                        { name: "A* (Heuristik)", value: benchmarkResult.astar.execution_time_ms, color: "#10b981" }
                      ]} 
                      margin={{ top: 10, right: 10, left: -20, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" className="dark:stroke-slate-800/40" />
                      <XAxis dataKey="name" tick={{ fontSize: 10, fontWeight: 600 }} stroke="#94a3b8" />
                      <YAxis tick={{ fontSize: 10, fontWeight: 600 }} stroke="#94a3b8" />
                      <Tooltip 
                        contentStyle={{ backgroundColor: "rgba(15, 23, 42, 0.9)", border: "none", borderRadius: "8px", color: "#fff", fontSize: "11px" }}
                        cursor={{ fill: "transparent" }}
                      />
                      <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={45}>
                        <Cell fill="#3b82f6" />
                        <Cell fill="#10b981" />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Explored Nodes Chart */}
              <div className="bg-slate-50/50 dark:bg-slate-950/30 p-4 rounded-xl border border-slate-100 dark:border-slate-800/80">
                <h4 className="text-xs font-bold text-slate-700 dark:text-slate-300 mb-3 flex items-center gap-1.5">
                  <BarChart3 className="w-3.5 h-3.5 text-emerald-500" />
                  Node yang Dieksplorasi
                </h4>
                <div className="h-40 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart 
                      data={[
                        { name: "Dijkstra", value: benchmarkResult.dijkstra.explored_nodes, color: "#3b82f6" },
                        { name: "A* (Heuristik)", value: benchmarkResult.astar.explored_nodes, color: "#10b981" }
                      ]} 
                      margin={{ top: 10, right: 10, left: -20, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" className="dark:stroke-slate-800/40" />
                      <XAxis dataKey="name" tick={{ fontSize: 10, fontWeight: 600 }} stroke="#94a3b8" />
                      <YAxis tick={{ fontSize: 10, fontWeight: 600 }} stroke="#94a3b8" />
                      <Tooltip 
                        contentStyle={{ backgroundColor: "rgba(15, 23, 42, 0.9)", border: "none", borderRadius: "8px", color: "#fff", fontSize: "11px" }}
                        cursor={{ fill: "transparent" }}
                      />
                      <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={45}>
                        <Cell fill="#3b82f6" />
                        <Cell fill="#10b981" />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
