"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import { useAppStore } from "@/lib/store";
import { buildAllOsmGraphs, cancelBuildAllOsm, fetchBuildAllOsmStatus, fetchDatasets, deleteDataset, fetchDatasetStatus, buildOsmBbox, fetchDatasetBbox } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { UploadCloud, CheckCircle2, Trash2 } from "lucide-react";
import { toast } from "sonner";

const API_BASE = typeof window !== "undefined"
  ? `http://${window.location.hostname}:8000`
  : "http://127.0.0.1:8000";

const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);

type GraphBuildItem = {
  dataset_id: string;
  label: string;
  status: string;
  message: string;
  size_mb?: number;
};

type GraphBuildStatus = {
  status: string;
  total: number;
  completed: number;
  succeeded: number;
  failed: number;
  skipped: number;
  available_graphs?: number;
  total_datasets?: number;
  items: GraphBuildItem[];
};

export default function DatasetManager() {
  const { datasets, setDatasets } = useAppStore();
  const [loading, setLoading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [datasetToDelete, setDatasetToDelete] = useState<string | null>(null);

  const [uploadingDatasetId, setUploadingDatasetId] = useState<string | null>(null);
  const [progressPercent, setProgressPercent] = useState<number>(0);
  const [progressMessage, setProgressMessage] = useState<string>("");

  const [osmLoading, setOsmLoading] = useState(false);
  const [bulkGraphStatus, setBulkGraphStatus] = useState<GraphBuildStatus | null>(null);
  const [north, setNorth] = useState("-6.08");
  const [south, setSouth] = useState("-6.37");
  const [east, setEast] = useState("106.97");
  const [west, setWest] = useState("106.68");
  const [networkType, setNetworkType] = useState("drive");

  const [selectedDatasetPreset, setSelectedDatasetPreset] = useState<string>("");
  const [detectingBbox, setDetectingBbox] = useState(false);
  const bulkGraphRunning = bulkGraphStatus?.status === "running" || bulkGraphStatus?.status === "starting" || bulkGraphStatus?.status === "cancelling";

  const calculateAreaKm2 = (n: number, s: number, e: number, w: number): number => {
    const midLat = (n + s) / 2.0;
    const heightKm = Math.abs(n - s) * 111.0;
    const widthKm = Math.abs(e - w) * 111.0 * Math.max(Math.cos(midLat * Math.PI / 180.0), 0.2);
    return heightKm * widthKm;
  };

  const handleDatasetPresetChange = async (datasetId: string) => {
    setSelectedDatasetPreset(datasetId);
    if (!datasetId) return;

    setDetectingBbox(true);
    const loadingToast = toast.loading("Menganalisis koordinat geospasial masjid pada dataset untuk menentukan Bounding Box otomatis...");
    try {
      const res = await fetchDatasetBbox(datasetId);
      const bbox = res.bbox;
      setNorth(bbox.north.toFixed(4));
      setSouth(bbox.south.toFixed(4));
      setEast(bbox.east.toFixed(4));
      setWest(bbox.west.toFixed(4));
      toast.dismiss(loadingToast);
      const detail = `${res.used_rows} dari ${res.valid_rows} koordinat dipakai; ${res.ignored_outliers} pencilan diabaikan.`;
      if (res.adjusted_to_area_limit) {
        toast.warning(`Dataset terlalu luas (${Math.round(res.raw_area_km2)} km²). Area dipusatkan dan dibatasi agar aman diunduh. ${detail}`);
      } else {
        toast.success(`Bounding box dihitung dari seluruh dataset. ${detail}`);
      }
    } catch (err: unknown) {
      toast.dismiss(loadingToast);
      toast.error(`Gagal menghitung bounding box: ${errorMessage(err)}`);
    } finally {
      setDetectingBbox(false);
    }
  };

  const handleBuildOsm = async () => {
    if (!selectedDatasetPreset) {
      toast.error("Pilih dataset terlebih dahulu agar graph disimpan dengan ID dataset yang benar.");
      return;
    }
    const n = parseFloat(north);
    const s = parseFloat(south);
    const e = parseFloat(east);
    const w = parseFloat(west);

    if (isNaN(n) || isNaN(s) || isNaN(e) || isNaN(w)) {
      toast.error("Semua koordinat bounding box harus diisi berupa angka.");
      return;
    }

    if (s >= n || w >= e || s < -90 || n > 90 || w < -180 || e > 180) {
      toast.error("Bounding box tidak valid: pastikan South < North dan West < East.");
      return;
    }

    const area = calculateAreaKm2(n, s, e, w);
    if (area > 1500) {
      toast.error(`Area Bounding Box terlalu besar (${Math.round(area)} km²). Batas maksimal satu build adalah 1500 km². Silakan kecilkan area koordinat Anda.`);
      return;
    }

    setOsmLoading(true);
    const loadingToast = toast.loading("Sedang mengunduh dan membangun graph jalan raya dari OpenStreetMap... Proses ini membutuhkan koneksi internet stabil dan bisa memakan waktu 30-60 detik.");
    try {
      const res = await buildOsmBbox(n, s, e, w, networkType, selectedDatasetPreset);
      toast.dismiss(loadingToast);
      toast.success(res.message || "OSM road graph lokal berhasil dibangun!");
    } catch (err: unknown) {
      toast.dismiss(loadingToast);
      toast.error(errorMessage(err) || "Gagal membangun graph OSM.");
    } finally {
      setOsmLoading(false);
    }
  };

  const loadDatasets = useCallback(async () => {
    try {
      const data = await fetchDatasets();
      setDatasets(data.items || []);
    } catch (err: unknown) {
      toast.error(`Gagal memuat daftar dataset: ${errorMessage(err)}`);
    }
  }, [setDatasets]);

  useEffect(() => {
    loadDatasets();
  }, [loadDatasets]);

  useEffect(() => {
    let mounted = true;
    const refresh = async () => {
      try {
        const status = await fetchBuildAllOsmStatus() as GraphBuildStatus;
        if (mounted) setBulkGraphStatus(status);
      } catch {
        // Status build bukan dependency kritis halaman admin.
      }
    };
    refresh();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!bulkGraphRunning) return;
    const interval = window.setInterval(async () => {
      try {
        setBulkGraphStatus(await fetchBuildAllOsmStatus());
      } catch {
        // Pertahankan progres terakhir jika polling sementara gagal.
      }
    }, 2000);
    return () => window.clearInterval(interval);
  }, [bulkGraphRunning]);

  const handleBuildAllGraphs = async () => {
    try {
      await buildAllOsmGraphs(networkType, false);
      toast.success("Build graph semua dataset dimulai di background.");
      setBulkGraphStatus(await fetchBuildAllOsmStatus());
    } catch (err: unknown) {
      toast.error(errorMessage(err));
    }
  };

  const handleCancelBuildAll = async () => {
    try {
      const result = await cancelBuildAllOsm();
      toast.info(result.message);
      setBulkGraphStatus(await fetchBuildAllOsmStatus());
    } catch (err: unknown) {
      toast.error(errorMessage(err));
    }
  };

  const handleDeleteDataset = async (datasetId: string) => {
    setLoading(true);
    setDatasetToDelete(null);
    try {
      await deleteDataset(datasetId);
      toast.success(`Dataset ${datasetId} berhasil dihapus.`);
      await loadDatasets();
    } catch (err: unknown) {
      toast.error(`Gagal menghapus dataset: ${errorMessage(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setSelectedFile(e.dataTransfer.files[0]);
    }
  };

  const handleUpload = () => {
    if (bulkGraphRunning) {
      toast.error("Tunggu build graph selesai atau batalkan antreannya sebelum upload.");
      return;
    }
    if (!selectedFile) return;
    setLoading(true);
    setUploadingDatasetId("uploading");
    setProgressPercent(0);
    setProgressMessage("Mengunggah berkas ke server...");

    const form = new FormData();
    form.append("file", selectedFile);
    form.append("process_now", "true");
    form.append("make_active", "false");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/v1/datasets/upload`, true);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const percent = Math.round((event.loaded / event.total) * 100);
        setProgressPercent(Math.min(percent, 99)); // Batasi 99% sampai ada respon backend
        setProgressMessage(`Mengunggah berkas: ${percent}%`);
      }
    };

    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          setSelectedFile(null);
          setUploadingDatasetId(data.dataset_id);
          setProgressPercent(data.progress_percent || 0);
          setProgressMessage(data.message || "Tugas ML dimulai...");

          const datasetId = data.dataset_id;
          const interval = setInterval(async () => {
            try {
              const statusRes = await fetchDatasetStatus(datasetId);
              setProgressPercent(statusRes.progress_percent || 0);
              setProgressMessage(statusRes.message || "");

              if (statusRes.processing_status === "completed") {
                clearInterval(interval);
                setUploadingDatasetId(null);
                setLoading(false);
                setProgressPercent(0);
                setProgressMessage("");
                toast.success(`Dataset ${datasetId} berhasil diunggah dan diproses ML!`);
                await loadDatasets();
              } else if (statusRes.processing_status === "failed") {
                clearInterval(interval);
                setUploadingDatasetId(null);
                setLoading(false);
                setProgressPercent(0);
                setProgressMessage("");
                toast.error(`Gagal memproses dataset: ${statusRes.message}`);
                await loadDatasets();
              }
            } catch (err: unknown) {
              clearInterval(interval);
              setUploadingDatasetId(null);
              setLoading(false);
              toast.error(`Gagal mengambil status: ${errorMessage(err)}`);
            }
          }, 1500);
        } catch {
          toast.error("Gagal membaca respon pemrosesan server.");
          setLoading(false);
          setUploadingDatasetId(null);
        }
      } else {
        toast.error("Gagal mengunggah berkas.");
        setLoading(false);
        setUploadingDatasetId(null);
      }
    };

    xhr.onerror = () => {
      toast.error("Terjadi kesalahan jaringan saat mengunggah.");
      setLoading(false);
      setUploadingDatasetId(null);
    };

    xhr.send(form);
  };

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Status Sistem</CardTitle>
          <CardDescription>Semua dataset terunggah aktif secara bersamaan.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="p-4 bg-slate-50 dark:bg-slate-900/60 rounded-lg text-sm border border-slate-200 dark:border-slate-800/80 space-y-2">
            <div className="font-semibold text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4" /> Sistem Siap (Multi-Dataset)
            </div>
             <div>Total Dataset Terunggah: <span className="font-bold text-slate-800 dark:text-slate-200">{datasets.length}</span></div>
            <div>Total Masjid Tersimpan: <span className="font-bold text-slate-800 dark:text-slate-200">{datasets.reduce((acc, d) => acc + (d.enriched_rows ?? d.profile?.enriched_rows ?? 0), 0)}</span></div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 leading-relaxed mt-2 pt-1.5 border-t border-slate-200/50 dark:border-slate-800/50">
              Sistem secara otomatis mencari masjid terdekat lintas wilayah berdasarkan lokasi GPS perangkat Anda. Tidak perlu memilih wilayah atau dataset secara manual.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Daftar Dataset Terunggah</CardTitle>
          <CardDescription>Semua wilayah/dataset yang tersimpan dalam sistem.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {datasets.length === 0 ? (
            <p className="text-sm text-slate-500 italic text-center py-4">Belum ada dataset terunggah.</p>
          ) : (
            <div className="space-y-3">
              {datasets.map((d) => {
                const label = d.dataset_label ?? d.profile?.dataset_label ?? d.dataset_id;
                const rows = d.enriched_rows ?? d.profile?.enriched_rows ?? 0;
                return (
                  <div 
                    key={d.dataset_id} 
                    className="p-3.5 rounded-xl border flex items-center justify-between transition-all duration-300 bg-emerald-50/25 dark:bg-emerald-950/20 border-emerald-100/50 dark:border-emerald-900/30 shadow-sm"
                  >
                    <div className="flex-1 min-w-0 pr-3">
                      <div className="flex items-start gap-2">
                        <span 
                          className="text-xs font-bold text-slate-800 dark:text-slate-200 break-all line-clamp-2"
                          title={label}
                        >
                          {label}
                        </span>
                        <span className="bg-emerald-600 text-white text-[9px] font-extrabold px-1.5 py-0.5 rounded-full shrink-0 mt-0.5">
                          Aktif
                        </span>
                      </div>
                      <span 
                        className="text-[10px] text-slate-500 dark:text-slate-400 block break-all line-clamp-2 mt-1 leading-tight"
                        title={d.filename || `${d.dataset_id}.csv`}
                      >
                        File: {d.filename || `${d.dataset_id}.csv`} • {rows} baris
                      </span>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      <Button 
                        variant="ghost" 
                        size="icon" 
                        className="h-7 w-7 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg"
                        onClick={() => setDatasetToDelete(d.dataset_id)}
                        disabled={loading || bulkGraphRunning}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Upload Dataset Baru</CardTitle>
          <CardDescription>Unggah file CSV OSM untuk diproses ML otomatis.</CardDescription>
        </CardHeader>
        <CardContent>
          <div 
            className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${dragActive ? 'border-primary bg-primary/5' : 'border-slate-300 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/40 hover:bg-slate-100 dark:hover:bg-slate-900/60'}`}
            onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
            onDragLeave={() => setDragActive(false)}
            onDrop={handleFileDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <input 
              type="file" 
              className="hidden" 
              ref={fileInputRef} 
              accept=".csv"
              onChange={(e) => { if (e.target.files?.[0]) setSelectedFile(e.target.files[0]); }}
            />
            <UploadCloud className="w-10 h-10 mx-auto text-slate-400 dark:text-slate-500 mb-2" />
            {selectedFile ? (
              <p className="font-medium text-primary">{selectedFile.name}</p>
            ) : (
              <p className="text-slate-500 dark:text-slate-400">Tarik file CSV ke sini atau klik untuk memilih file</p>
            )}
          </div>
          
          <Button 
            className="w-full mt-4" 
            disabled={!selectedFile || loading || bulkGraphRunning}
            onClick={handleUpload}
          >
            {loading ? (uploadingDatasetId ? "Memproses..." : "Mengunggah...") : "Upload & Jalankan ML"}
          </Button>

          {uploadingDatasetId && (
            <div className="mt-4 p-4 rounded-xl border bg-slate-50/50 dark:bg-slate-900/40 border-slate-200 dark:border-slate-800/80 backdrop-blur-sm space-y-2">
              <div className="flex justify-between items-center text-xs font-semibold">
                <span className="text-slate-600 dark:text-slate-400 truncate max-w-[200px]" title={progressMessage}>
                  {progressMessage || "Memproses..."}
                </span>
                <span className="text-emerald-600 dark:text-emerald-400 font-bold">{progressPercent}%</span>
              </div>
              <div className="w-full bg-slate-200 dark:bg-slate-800 rounded-full h-2 overflow-hidden">
                <div 
                  className="bg-emerald-500 h-2 rounded-full transition-all duration-300 ease-out"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bangun Graph OSM Lokal</CardTitle>
          <CardDescription>
            Bangun cache jalan OpenStreetMap per dataset. Area di luar cache tetap dilayani melalui fallback OSRM.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-4 dark:border-emerald-900/60 dark:bg-emerald-950/20">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-bold text-emerald-900 dark:text-emerald-200">Graph Area Prioritas Semua Dataset</p>
                <p className="mt-1 text-xs leading-relaxed text-emerald-800/80 dark:text-emerald-300/70">
                  Setiap dataset diproses satu per satu hingga batas area aman; ini bukan seluruh provinsi untuk dataset yang sangat luas.
                  {bulkGraphStatus ? ` ${bulkGraphStatus.available_graphs ?? 0} dari ${bulkGraphStatus.total_datasets ?? datasets.length} graph dataset tersedia saat ini.` : ""}
                </p>
              </div>
              {bulkGraphRunning ? (
                <Button className="shrink-0" variant="outline" size="sm" onClick={handleCancelBuildAll}>Batalkan Antrean</Button>
              ) : (
                <Button className="shrink-0" size="sm" onClick={handleBuildAllGraphs} disabled={osmLoading || loading || datasets.length === 0}>
                  Bangun Semua Area Prioritas
                </Button>
              )}
            </div>

            {bulkGraphStatus && bulkGraphStatus.total > 0 && (
              <div className="mt-4 space-y-2">
                <div className="flex justify-between gap-3 text-xs font-semibold text-slate-600 dark:text-slate-300">
                  <span>{bulkGraphStatus.completed}/{bulkGraphStatus.total} dataset · {bulkGraphStatus.succeeded} berhasil · {bulkGraphStatus.skipped} tersedia · {bulkGraphStatus.failed} gagal</span>
                  <span>{Math.round((bulkGraphStatus.completed / bulkGraphStatus.total) * 100)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
                  <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${(bulkGraphStatus.completed / bulkGraphStatus.total) * 100}%` }} />
                </div>
                <div className="max-h-32 space-y-1 overflow-y-auto text-[11px] text-slate-600 dark:text-slate-400">
                  {bulkGraphStatus.items.map((item) => (
                    <div key={item.dataset_id} className="flex items-center justify-between gap-3">
                      <span className="truncate" title={item.label}>{item.label}</span>
                      <span className={item.status === "failed" ? "text-rose-600" : item.status === "completed" ? "text-emerald-600" : ""}>
                        {item.message}{item.size_mb ? ` · ${item.size_mb} MB` : ""}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-bold text-slate-500 dark:text-slate-400">Dataset Tujuan dan Deteksi Area Otomatis</label>
            <select
              value={selectedDatasetPreset}
              onChange={(e) => handleDatasetPresetChange(e.target.value)}
              disabled={detectingBbox || loading}
              className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
            >
              <option value="">-- Pilih Dataset Terunggah untuk Deteksi Area Otomatis --</option>
              {datasets.map((d) => {
                const label = d.dataset_label ?? d.profile?.dataset_label ?? d.dataset_id;
                return (
                  <option key={d.dataset_id} value={d.dataset_id}>
                    {label} ({d.enriched_rows ?? d.profile?.enriched_rows ?? d.mosque_count ?? 0} masjid)
                  </option>
                );
              })}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-500 dark:text-slate-400">North (Lat Maks)</label>
              <input 
                type="text" 
                value={north} 
                onChange={(e) => setNorth(e.target.value)}
                className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                placeholder="-6.08"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-500 dark:text-slate-400">South (Lat Min)</label>
              <input 
                type="text" 
                value={south} 
                onChange={(e) => setSouth(e.target.value)}
                className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                placeholder="-6.37"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-500 dark:text-slate-400">East (Lon Maks)</label>
              <input 
                type="text" 
                value={east} 
                onChange={(e) => setEast(e.target.value)}
                className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                placeholder="106.97"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-500 dark:text-slate-400">West (Lon Min)</label>
              <input 
                type="text" 
                value={west} 
                onChange={(e) => setWest(e.target.value)}
                className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                placeholder="106.68"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-bold text-slate-500 dark:text-slate-400">Tipe Jaringan Transportasi</label>
            <select
              value={networkType}
              onChange={(e) => setNetworkType(e.target.value)}
              className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
            >
              <option value="drive">Mobil / Motor (Drive)</option>
              <option value="walk">Jalan Kaki (Walk)</option>
              <option value="bike">Sepeda (Bike)</option>
              <option value="all">Semua Tipe Jalan (All)</option>
            </select>
          </div>

          <Button 
            className="w-full mt-2" 
            disabled={osmLoading || loading || bulkGraphRunning}
            onClick={handleBuildOsm}
          >
            {osmLoading ? "Membangun Graph..." : "Download & Bangun Graph Jalan"}
          </Button>
        </CardContent>
      </Card>

      <Dialog open={!!datasetToDelete} onOpenChange={(open) => !open && setDatasetToDelete(null)}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Konfirmasi Hapus Dataset</DialogTitle>
            <DialogDescription className="pt-2 leading-relaxed">
              Apakah Anda yakin ingin menghapus dataset ini?
            </DialogDescription>
            <div className="my-3 p-2 bg-slate-100 dark:bg-slate-950 rounded-md border border-slate-200 dark:border-slate-800">
              <span className="font-mono text-xs text-slate-800 dark:text-slate-200 break-all">
                {datasetToDelete}
              </span>
            </div>
            <DialogDescription className="leading-relaxed">
              Semua data masjid di dalamnya akan dihapus secara permanen.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-4 gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setDatasetToDelete(null)} disabled={loading}>
              Batal
            </Button>
            <Button variant="destructive" onClick={() => datasetToDelete && handleDeleteDataset(datasetToDelete)} disabled={loading || bulkGraphRunning}>
              {loading ? "Menghapus..." : "Hapus Dataset"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
