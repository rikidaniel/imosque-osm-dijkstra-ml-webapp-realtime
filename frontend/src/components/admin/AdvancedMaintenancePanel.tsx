"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, KeyRound, LockKeyhole, ShieldCheck, Wrench } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  buildAllOsmGraphs,
  buildOsmBbox,
  cancelBuildAllOsm,
  fetchBuildAllOsmStatus,
  fetchDatasetBbox,
  getAdminToken,
  saveAdminToken,
  verifyAdminAccess,
} from "@/lib/api";
import { useAppStore } from "@/lib/store";

type GraphBuildItem = { dataset_id: string; label: string; status: string; message: string; size_mb?: number };
type GraphBuildStatus = {
  status: string;
  total: number;
  completed: number;
  succeeded: number;
  failed: number;
  skipped: number;
  items: GraphBuildItem[];
};

const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);

export default function AdvancedMaintenancePanel() {
  const datasets = useAppStore((state) => state.datasets);
  const [tokenInput, setTokenInput] = useState("");
  const [authorized, setAuthorized] = useState(false);
  const [protectionConfigured, setProtectionConfigured] = useState<boolean | null>(null);
  const [checkingAccess, setCheckingAccess] = useState(true);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [north, setNorth] = useState("-6.08");
  const [south, setSouth] = useState("-6.37");
  const [east, setEast] = useState("106.97");
  const [west, setWest] = useState("106.68");
  const [networkType, setNetworkType] = useState("drive");
  const [loading, setLoading] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [bulkStatus, setBulkStatus] = useState<GraphBuildStatus | null>(null);
  const bulkRunning = useMemo(() => ["starting", "running", "cancelling"].includes(bulkStatus?.status || ""), [bulkStatus]);

  const checkAccess = useCallback(async (candidate = getAdminToken()) => {
    setCheckingAccess(true);
    try {
      const result = await verifyAdminAccess(candidate);
      if (candidate) saveAdminToken(candidate);
      setAuthorized(true);
      setProtectionConfigured(Boolean(result.protection_configured));
      setTokenInput("");
    } catch (error) {
      setAuthorized(false);
      setProtectionConfigured(true);
      if (candidate) toast.error(errorMessage(error));
    } finally {
      setCheckingAccess(false);
    }
  }, []);

  useEffect(() => {
    const kickoff = window.setTimeout(checkAccess, 0);
    return () => window.clearTimeout(kickoff);
  }, [checkAccess]);

  const refreshBulkStatus = useCallback(async () => {
    try { setBulkStatus(await fetchBuildAllOsmStatus()); } catch { /* status legacy bukan dependency utama */ }
  }, []);

  useEffect(() => {
    if (!authorized) return;
    const kickoff = window.setTimeout(refreshBulkStatus, 0);
    return () => window.clearTimeout(kickoff);
  }, [authorized, refreshBulkStatus]);

  useEffect(() => {
    if (!authorized || !bulkRunning) return;
    const interval = window.setInterval(refreshBulkStatus, 2_000);
    return () => window.clearInterval(interval);
  }, [authorized, bulkRunning, refreshBulkStatus]);

  const detectBounds = async (datasetId: string) => {
    setSelectedDataset(datasetId);
    if (!datasetId) return;
    setDetecting(true);
    try {
      const result = await fetchDatasetBbox(datasetId);
      setNorth(Number(result.bbox.north).toFixed(4));
      setSouth(Number(result.bbox.south).toFixed(4));
      setEast(Number(result.bbox.east).toFixed(4));
      setWest(Number(result.bbox.west).toFixed(4));
      toast.success("Bounding box maintenance berhasil dideteksi.");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setDetecting(false);
    }
  };

  const buildManualGraph = async () => {
    if (!selectedDataset) return toast.error("Pilih dataset tujuan.");
    const values = [north, south, east, west].map(Number);
    const [n, s, e, w] = values;
    if (values.some((value) => !Number.isFinite(value)) || s >= n || w >= e) return toast.error("Bounding box tidak valid.");
    const heightKm = Math.abs(n - s) * 111;
    const widthKm = Math.abs(e - w) * 111 * Math.max(Math.cos(((n + s) / 2) * Math.PI / 180), 0.2);
    if (heightKm * widthKm > 1500) return toast.error("Area melebihi batas aman 1.500 km².");

    setLoading(true);
    const id = toast.loading("Membangun artifact graph manual...");
    try {
      const result = await buildOsmBbox(n, s, e, w, networkType, selectedDataset);
      toast.success(result.message || "Graph selesai dibangun.", { id });
    } catch (error) {
      toast.error(errorMessage(error), { id });
    } finally {
      setLoading(false);
    }
  };

  const buildAll = async () => {
    setLoading(true);
    try {
      await buildAllOsmGraphs(networkType, false);
      toast.success("Prewarm legacy dimulai di background.");
      await refreshBulkStatus();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setLoading(false);
    }
  };

  const cancelAll = async () => {
    try {
      const result = await cancelBuildAllOsm();
      toast.info(result.message);
      await refreshBulkStatus();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  if (checkingAccess) {
    return (
      <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
        <CardContent className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500 dark:text-slate-400">
          <LockKeyhole className="h-4 w-4" /> Memeriksa akses maintenance...
        </CardContent>
      </Card>
    );
  }

  if (!authorized) {
    return (
      <Card className="mx-auto max-w-xl bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-slate-900 dark:text-slate-100">
            <LockKeyhole className="h-5 w-5 text-amber-600 dark:text-amber-400" /> Maintenance terkunci
          </CardTitle>
          <CardDescription className="text-slate-500 dark:text-slate-400">
            Masukkan token superadmin. Token hanya disimpan pada session browser dan diverifikasi oleh backend.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <label className="block text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">Token superadmin</label>
          <input
            type="password"
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && checkAccess(tokenInput)}
            className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
            autoComplete="current-password"
          />
          <Button className="w-full" onClick={() => checkAccess(tokenInput)} disabled={!tokenInput.trim()}>
            <KeyRound className="mr-2 h-4 w-4" /> Buka Maintenance
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className={`rounded-xl border p-4 text-sm ${
        protectionConfigured
          ? "border-emerald-200 dark:border-emerald-900/50 bg-emerald-50 dark:bg-emerald-950/20 text-emerald-800 dark:text-emerald-400"
          : "border-amber-300 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/20 text-amber-900 dark:text-amber-400"
      }`}>
        <div className="flex items-start gap-3">
          {protectionConfigured ? <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" /> : <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />}
          <div className="flex-1">
            <p className="font-bold">{protectionConfigured ? "Akses superadmin terverifikasi" : "Mode lokal tanpa proteksi token"}</p>
            <p className="mt-1 text-xs leading-relaxed">
              {protectionConfigured
                ? "Tindakan build dan penghapusan dataset divalidasi backend menggunakan IMOSQUE_ADMIN_TOKEN."
                : "Tetapkan IMOSQUE_ADMIN_TOKEN sebelum deployment agar tindakan maintenance tidak dapat dijalankan tanpa autentikasi."}
            </p>
          </div>
          {protectionConfigured && (
            <Button variant="outline" size="sm"
              className="border-emerald-300 dark:border-emerald-800/60 bg-white/50 dark:bg-slate-900/50 text-emerald-800 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-950/40"
              onClick={() => { saveAdminToken(""); setAuthorized(false); }}>
              Kunci
            </Button>
          )}
        </div>
      </div>

      <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-slate-900 dark:text-slate-100">
            <Wrench className="h-5 w-5 text-amber-600 dark:text-amber-400" /> Prewarm graph dataset lama
          </CardTitle>
          <CardDescription className="text-slate-500 dark:text-slate-400">
            Bukan jalur routing nasional utama. Gunakan hanya untuk pemulihan atau pengujian artifact lama.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/20 p-3 text-xs leading-relaxed text-amber-900 dark:text-amber-400">
            Routing interaktif menggunakan graph koridor otomatis. Menjalankan tugas ini dapat memakai bandwidth Overpass, disk, CPU, dan RAM dalam jumlah besar.
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between text-sm text-slate-700 dark:text-slate-300">
            <div><span className="font-bold">Status:</span> {bulkStatus?.status || "idle"}{bulkStatus?.total ? ` · ${bulkStatus.completed}/${bulkStatus.total} dataset` : ""}</div>
            {bulkRunning
              ? <Button variant="outline" onClick={cancelAll} className="border-slate-200 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Batalkan antrean</Button>
              : <Button variant="outline" onClick={buildAll} disabled={loading || datasets.length === 0} className="border-slate-200 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Jalankan prewarm legacy</Button>
            }
          </div>
          {bulkStatus?.items?.length ? (
            <div className="max-h-36 space-y-1 overflow-y-auto rounded-lg bg-slate-50 dark:bg-slate-800 border dark:border-slate-700 p-3 text-xs text-slate-700 dark:text-slate-300">
              {bulkStatus.items.map((item) => (
                <div key={item.dataset_id} className="flex justify-between gap-4">
                  <span className="truncate">{item.label}</span>
                  <span>{item.message}{item.size_mb ? ` · ${item.size_mb} MB` : ""}</span>
                </div>
              ))}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
        <CardHeader>
          <CardTitle className="text-slate-900 dark:text-slate-100">Bangun graph bounding box manual</CardTitle>
          <CardDescription className="text-slate-500 dark:text-slate-400">
            Alat darurat untuk diagnosis atau rebuild satu area kecil. Graph koridor otomatis tetap menjadi default aplikasi.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-bold text-slate-500 dark:text-slate-400">Dataset tujuan</label>
            <select
              value={selectedDataset}
              onChange={(event) => detectBounds(event.target.value)}
              disabled={detecting || loading}
              className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
            >
              <option value="">Pilih dataset untuk mendeteksi area</option>
              {datasets.map((dataset) => (
                <option key={dataset.dataset_id} value={dataset.dataset_id}>
                  {dataset.dataset_label ?? dataset.profile?.dataset_label ?? dataset.dataset_id}
                </option>
              ))}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {[["North", north, setNorth], ["South", south, setSouth], ["East", east, setEast], ["West", west, setWest]].map(([label, value, setter]) => (
              <label key={String(label)} className="text-xs font-bold text-slate-500 dark:text-slate-400">
                {String(label)}
                <input
                  value={value as string}
                  onChange={(event) => (setter as (value: string) => void)(event.target.value)}
                  className="mt-1.5 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm font-normal text-slate-900 dark:text-slate-100"
                />
              </label>
            ))}
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-bold text-slate-500 dark:text-slate-400">Tipe jaringan</label>
            <select
              value={networkType}
              onChange={(event) => setNetworkType(event.target.value)}
              className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
            >
              <option value="drive">Mobil / Motor</option>
              <option value="walk">Jalan kaki</option>
              <option value="bike">Sepeda</option>
              <option value="all">Semua tipe jalan</option>
            </select>
          </div>
          <Button className="w-full" onClick={buildManualGraph} disabled={loading || detecting || !selectedDataset}>
            {loading ? "Membangun graph..." : "Download & bangun graph manual"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
