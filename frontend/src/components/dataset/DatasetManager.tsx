"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Trash2, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { API_BASE } from "@/lib/config";
import { deleteDataset, fetchDatasets, fetchDatasetStatus } from "@/lib/api";
import { useAppStore } from "@/lib/store";

const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);

export default function DatasetManager() {
  const { datasets, setDatasets } = useAppStore();
  const [loading, setLoading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [datasetToDelete, setDatasetToDelete] = useState<string | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [uploadingDatasetId, setUploadingDatasetId] = useState<string | null>(null);
  const [progressPercent, setProgressPercent] = useState(0);
  const [progressMessage, setProgressMessage] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadDatasets = useCallback(async () => {
    try {
      const data = await fetchDatasets();
      setDatasets(data.items || []);
    } catch (error) {
      toast.error(`Gagal memuat daftar dataset: ${errorMessage(error)}`);
    }
  }, [setDatasets]);

  useEffect(() => { loadDatasets(); }, [loadDatasets]);

  const resetUpload = () => {
    setUploadingDatasetId(null);
    setLoading(false);
    setProgressPercent(0);
    setProgressMessage("");
  };

  const pollDatasetProcessing = (datasetId: string) => {
    const interval = window.setInterval(async () => {
      try {
        const status = await fetchDatasetStatus(datasetId);
        setProgressPercent(status.progress_percent || 0);
        setProgressMessage(status.message || "");
        if (status.processing_status === "completed") {
          window.clearInterval(interval);
          resetUpload();
          toast.success(`Dataset ${datasetId} berhasil diunggah dan diproses.`);
          await loadDatasets();
        } else if (status.processing_status === "failed") {
          window.clearInterval(interval);
          resetUpload();
          toast.error(`Pemrosesan dataset gagal: ${status.message}`);
          await loadDatasets();
        }
      } catch (error) {
        window.clearInterval(interval);
        resetUpload();
        toast.error(`Gagal mengambil status dataset: ${errorMessage(error)}`);
      }
    }, 1_500);
  };

  const uploadDataset = () => {
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
      if (!event.lengthComputable) return;
      const percent = Math.round((event.loaded / event.total) * 100);
      setProgressPercent(Math.min(percent, 99));
      setProgressMessage(`Mengunggah berkas: ${percent}%`);
    };
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        resetUpload();
        toast.error("Gagal mengunggah berkas.");
        return;
      }
      try {
        const data = JSON.parse(xhr.responseText);
        setSelectedFile(null);
        setUploadingDatasetId(data.dataset_id);
        setProgressPercent(data.progress_percent || 0);
        setProgressMessage(data.message || "Pemrosesan dimulai...");
        pollDatasetProcessing(data.dataset_id);
      } catch {
        resetUpload();
        toast.error("Respons pemrosesan server tidak valid.");
      }
    };
    xhr.onerror = () => {
      resetUpload();
      toast.error("Terjadi kesalahan jaringan saat mengunggah.");
    };
    xhr.send(form);
  };

  const removeDataset = async (datasetId: string) => {
    setLoading(true);
    try {
      await deleteDataset(datasetId);
      toast.success(`Dataset ${datasetId} berhasil dihapus.`);
      setDatasetToDelete(null);
      setDeleteConfirmation("");
      await loadDatasets();
    } catch (error) {
      toast.error(`Gagal menghapus dataset: ${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-black text-slate-900 dark:text-slate-100">Dataset Masjid</h2>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Semua dataset dipakai bersamaan untuk pencarian nasional; tidak ada pemilihan provinsi aktif.</p>
      </div>

      {/* Daftar Dataset */}
      <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
        <CardHeader>
          <CardTitle className="text-slate-900 dark:text-slate-100">Daftar dataset</CardTitle>
          <CardDescription className="text-slate-500 dark:text-slate-400">{datasets.length} dataset tersedia dalam sistem.</CardDescription>
        </CardHeader>
        <CardContent>
          {datasets.length === 0 ? (
            <p className="py-8 text-center text-sm italic text-slate-500 dark:text-slate-400">Belum ada dataset terunggah.</p>
          ) : (
            <div className="space-y-3">
              {datasets.map((dataset) => {
                const label = dataset.dataset_label ?? dataset.profile?.dataset_label ?? dataset.dataset_id;
                const rows = dataset.enriched_rows ?? dataset.profile?.enriched_rows ?? dataset.mosque_count ?? 0;
                return (
                  <div
                    key={dataset.dataset_id}
                    className="flex items-center justify-between rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 p-4 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
                  >
                    <div className="min-w-0 pr-3">
                      <p className="truncate text-sm font-bold text-slate-800 dark:text-slate-100" title={label}>{label}</p>
                      <p className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400" title={dataset.filename || `${dataset.dataset_id}.csv`}>
                        {dataset.filename || `${dataset.dataset_id}.csv`} · {Number(rows).toLocaleString("id-ID")} masjid
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="shrink-0 text-slate-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 hover:text-rose-600 dark:hover:text-rose-400"
                      onClick={() => { setDatasetToDelete(dataset.dataset_id); setDeleteConfirmation(""); }}
                      disabled={loading}
                      aria-label={`Hapus dataset ${label}`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Upload Dataset */}
      <Card className="bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800">
        <CardHeader>
          <CardTitle className="text-slate-900 dark:text-slate-100">Upload dataset baru</CardTitle>
          <CardDescription className="text-slate-500 dark:text-slate-400">Unggah CSV OSM untuk divalidasi dan diproses otomatis.</CardDescription>
        </CardHeader>
        <CardContent>
          <div
            className={`cursor-pointer rounded-xl border-2 border-dashed p-8 text-center transition-colors ${
              dragActive
                ? "border-emerald-500 bg-emerald-50 dark:bg-emerald-950/20"
                : "border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40 hover:bg-slate-100 dark:hover:bg-slate-800"
            }`}
            onDragOver={(event) => { event.preventDefault(); setDragActive(true); }}
            onDragLeave={() => setDragActive(false)}
            onDrop={(event) => { event.preventDefault(); setDragActive(false); if (event.dataTransfer.files[0]) setSelectedFile(event.dataTransfer.files[0]); }}
            onClick={() => fileInputRef.current?.click()}
          >
            <input type="file" className="hidden" ref={fileInputRef} accept=".csv" onChange={(event) => event.target.files?.[0] && setSelectedFile(event.target.files[0])} />
            <UploadCloud className="mx-auto mb-3 h-10 w-10 text-slate-400 dark:text-slate-500" />
            <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
              {selectedFile ? selectedFile.name : "Tarik file CSV ke sini atau klik untuk memilih"}
            </p>
          </div>
          <Button className="mt-4 w-full" onClick={uploadDataset} disabled={!selectedFile || loading}>
            {loading ? "Memproses..." : "Upload & jalankan pemrosesan"}
          </Button>
          {uploadingDatasetId && (
            <div className="mt-4 space-y-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 p-4">
              <div className="flex justify-between gap-3 text-xs font-semibold text-slate-700 dark:text-slate-300">
                <span className="truncate">{progressMessage || "Memproses..."}</span>
                <span className="text-emerald-600 dark:text-emerald-400">{progressPercent}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${progressPercent}%` }} />
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Dialog Konfirmasi Hapus */}
      <Dialog open={Boolean(datasetToDelete)} onOpenChange={(open) => { if (!open) { setDatasetToDelete(null); setDeleteConfirmation(""); } }}>
        <DialogContent className="sm:max-w-[460px] bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800 text-slate-900 dark:text-slate-100">
          <DialogHeader>
            <DialogTitle className="text-slate-900 dark:text-slate-100">Hapus dataset permanen?</DialogTitle>
            <DialogDescription className="text-slate-500 dark:text-slate-400">
              Data masjid dalam dataset akan dihapus. Masukkan ID dataset untuk mengonfirmasi tindakan ini.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 p-3 font-mono text-xs break-all text-slate-800 dark:text-slate-200">
              {datasetToDelete}
            </div>
            <input
              value={deleteConfirmation}
              onChange={(event) => setDeleteConfirmation(event.target.value)}
              placeholder="Ketik ID dataset"
              className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-rose-500/20"
            />
            <p className="text-xs text-amber-700 dark:text-amber-400">
              Jika server memakai proteksi admin, token superadmin harus dibuka terlebih dahulu melalui tab Maintenance.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setDatasetToDelete(null); setDeleteConfirmation(""); }} disabled={loading}
              className="border-slate-200 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
              Batal
            </Button>
            <Button variant="destructive" onClick={() => datasetToDelete && removeDataset(datasetToDelete)} disabled={loading || deleteConfirmation !== datasetToDelete}>
              {loading ? "Menghapus..." : "Hapus dataset"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
