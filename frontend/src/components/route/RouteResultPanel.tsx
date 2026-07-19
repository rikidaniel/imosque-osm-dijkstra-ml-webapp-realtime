"use client";

import { useAppStore } from "@/lib/store";
import { runRouteBenchmark } from "@/lib/api";
import { formatDistance } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useEffect, useRef, useState } from "react";
import { 
  X, Navigation, Clock, Star, Info, CheckCircle2, AlertTriangle, AlertCircle, Compass, ChevronDown,
  Car, Droplets, Users, User, Snowflake, Volume2, Wifi, Utensils, BookOpen, BarChart3, Loader2, Wallet
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface AlternativeCandidate {
  name: string;
  distance_km: number;
  estimated_time_minutes: number;
  multi_objective_score: number;
}

interface AlgorithmBenchmark {
  execution_time_ms: number;
  explored_nodes: number;
  examined_edges: number;
  route_distance_km: number;
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

// Helper function to map facilities to icons/labels
const getFacilityBadge = (fac: string) => {
  const f = fac.trim().toLowerCase();
  const facilityMap: Record<string, { label: string; icon: LucideIcon; color: string }> = {
    parking: { label: "Tempat Parkir", icon: Car, color: "bg-blue-50 text-blue-700 border-blue-100 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-800/40" },
    wudu_area: { label: "Tempat Wudhu", icon: Droplets, color: "bg-teal-50 text-teal-700 border-teal-100 dark:bg-teal-950/40 dark:text-teal-300 dark:border-teal-800/40" },
    toilet: { label: "Toilet Bersih", icon: Users, color: "bg-indigo-50 text-indigo-700 border-indigo-100 dark:bg-indigo-950/40 dark:text-indigo-300 dark:border-indigo-800/40" },
    women_area: { label: "Area Wanita", icon: User, color: "bg-pink-50 text-pink-700 border-pink-100 dark:bg-pink-950/40 dark:text-pink-300 dark:border-pink-800/40" },
    ac: { label: "AC", icon: Snowflake, color: "bg-cyan-50 text-cyan-700 border-cyan-100 dark:bg-cyan-950/40 dark:text-cyan-300 dark:border-cyan-800/40" },
    sound_system: { label: "Sound System", icon: Volume2, color: "bg-amber-50 text-amber-700 border-amber-100 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800/40" },
    wifi: { label: "WiFi Gratis", icon: Wifi, color: "bg-purple-50 text-purple-700 border-purple-100 dark:bg-purple-950/40 dark:text-purple-300 dark:border-purple-800/40" },
    canteen: { label: "Kantin", icon: Utensils, color: "bg-orange-50 text-orange-700 border-orange-100 dark:bg-orange-950/40 dark:text-orange-300 dark:border-orange-800/40" },
    library: { label: "Perpustakaan", icon: BookOpen, color: "bg-emerald-50 text-emerald-700 border-emerald-100 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800/40" },
  };

  return facilityMap[f] || { label: fac, icon: Info, color: "bg-slate-50 text-slate-700 border-slate-200 dark:bg-slate-800/40 dark:text-slate-300 dark:border-slate-800/40" };
};

interface RouteResultPanelProps {
  isExpanded: boolean;
  setIsExpanded: (val: boolean) => void;
}

export default function RouteResultPanel({ isExpanded, setIsExpanded }: RouteResultPanelProps) {
  const {
    routeData,
    setRouteData,
    startPoint,
    endPoint,
    activeDatasetId,
    searchSettings,
  } = useAppStore();
  const [benchmarkResult, setBenchmarkResult] = useState<BenchmarkResult | null>(null);
  const [benchmarkLoading, setBenchmarkLoading] = useState(false);
  const [benchmarkStatus, setBenchmarkStatus] = useState("");
  const [benchmarkError, setBenchmarkError] = useState("");
  const benchmarkAbortRef = useRef<AbortController | null>(null);
  const routeIdentity = routeData
    ? [
        routeData.dataset_id,
        routeData.start?.latitude,
        routeData.start?.longitude,
        routeData.recommended_mosque?.id || routeData.recommended_mosque?.mosque_id,
      ].join(":")
    : "";

  useEffect(() => {
    benchmarkAbortRef.current?.abort();
    benchmarkAbortRef.current = null;
    setBenchmarkResult(null);
    setBenchmarkLoading(false);
    setBenchmarkStatus("");
    setBenchmarkError("");
  }, [routeIdentity]);

  useEffect(() => () => benchmarkAbortRef.current?.abort(), []);

  if (!routeData) return null;

  const summary = routeData.route_summary;
  const recommended = routeData.recommended_mosque;
  const candidates = routeData.candidate_mosques || [];
  const algorithm = routeData.algorithm;
  const executionTime = routeData.execution_time_ms;
  const pathfinding = routeData.pathfinding;
  const astarFallbackUsed = Boolean(pathfinding?.fallback_used);
  const estimatedCost = Number(summary.estimated_cost_rupiah || 0);
  const costBreakdown = summary.cost_breakdown || {};
  const formatRupiah = (value: number) => new Intl.NumberFormat("id-ID", {
    style: "currency",
    currency: "IDR",
    maximumFractionDigits: 0,
  }).format(value || 0);

  const handleClose = () => {
    benchmarkAbortRef.current?.abort();
    setRouteData(null);
  };

  const handleRunBenchmark = async () => {
    const origin = startPoint || (
      Number.isFinite(Number(routeData.start?.latitude))
      && Number.isFinite(Number(routeData.start?.longitude))
        ? {
            lat: Number(routeData.start.latitude),
            lng: Number(routeData.start.longitude),
          }
        : null
    );
    const destination = Number.isFinite(Number(recommended.latitude))
      && Number.isFinite(Number(recommended.longitude))
      ? {
          lat: Number(recommended.latitude),
          lng: Number(recommended.longitude),
        }
      : endPoint;
    const datasetId = String(
      routeData.dataset_id
      || recommended.dataset_id
      || (activeDatasetId !== "all" ? activeDatasetId : "")
      || ""
    );

    if (!origin || !destination || !datasetId || datasetId === "all") {
      setBenchmarkError("Dataset atau koordinat rute belum lengkap. Jalankan rute terlebih dahulu.");
      return;
    }

    benchmarkAbortRef.current?.abort();
    const controller = new AbortController();
    benchmarkAbortRef.current = controller;
    setBenchmarkLoading(true);
    setBenchmarkResult(null);
    setBenchmarkError("");
    setBenchmarkStatus("Membandingkan kedua algoritma pada graph yang sama...");

    try {
      const radius = Math.min(Math.max(Number(searchSettings.bufferKm) || 10, 2), 50);
      const data = await runRouteBenchmark({
        dataset_id: datasetId,
        origin: { latitude: origin.lat, longitude: origin.lng },
        destination: { latitude: destination.lat, longitude: destination.lng },
        departure_time: new Date().toISOString(),
        prayer: searchSettings.prayer,
        profile: searchSettings.profile,
        search_radius_km: radius,
      }, (preparation) => {
        setBenchmarkStatus(preparation.message || "Menyiapkan graph koridor rute...");
      }, controller.signal);

      if (benchmarkAbortRef.current !== controller) return;
      setBenchmarkResult(data.benchmark as BenchmarkResult);
      setBenchmarkStatus("");
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (benchmarkAbortRef.current !== controller) return;
      setBenchmarkError(error instanceof Error ? error.message : "Gagal membandingkan algoritma.");
      setBenchmarkStatus("");
    } finally {
      if (benchmarkAbortRef.current === controller) {
        benchmarkAbortRef.current = null;
        setBenchmarkLoading(false);
      }
    }
  };

  // Determine prayer status and colors
  // minutes_before_prayer represents minutes remaining until prayer when user arrives
  const prayerContext = routeData.prayer_context || {};
  const parsedMinutesBefore = Number(summary.minutes_before_prayer);
  const minBefore = Number.isFinite(parsedMinutesBefore) ? parsedMinutesBefore : null;
  const arrivalStatus = String(summary.arrival_status || "unknown");
  const prayerTimingAvailable = minBefore !== null && ["before_prayer", "after_prayer"].includes(arrivalStatus);
  const isLate = prayerTimingAvailable && (arrivalStatus === "after_prayer" || minBefore < 0);
  const isClose = prayerTimingAvailable && !isLate && minBefore <= 15;

  let statusColor = "bg-slate-400";
  let statusText = "Tidak tersedia";
  let statusDesc = "Jadwal salat belum tersedia. Muat ulang rute untuk memperbarui perhitungan.";
  let StatusIcon = Info;
  let progressPercent = 0;

  if (isLate && minBefore !== null) {
    statusColor = "bg-rose-500";
    statusText = "Terlambat";
    statusDesc = `Estimasi tiba ${Math.round(Math.abs(minBefore))} menit setelah adzan.`;
    StatusIcon = AlertCircle;
    progressPercent = 0;
  } else if (isClose && minBefore !== null) {
    statusColor = "bg-amber-500";
    statusText = "Mepet";
    statusDesc = `Tiba hanya ${Math.round(minBefore)} menit sebelum adzan.`;
    StatusIcon = AlertTriangle;
    progressPercent = Math.min(100, Math.max(10, (minBefore / 15) * 100));
  } else if (prayerTimingAvailable && minBefore !== null) {
    statusColor = "bg-emerald-500";
    statusText = "Aman";
    statusDesc = `Estimasi tiba ${Math.round(minBefore)} menit sebelum adzan.`;
    StatusIcon = CheckCircle2;
    progressPercent = Math.min(100, (minBefore / 30) * 100);
  }

  // Determine Mosque Tier styling
  const tier = recommended.tier || "D";
  const tierColors: Record<string, string> = {
    "A": "bg-amber-500 text-white font-bold",
    "B": "bg-indigo-500 text-white font-bold",
    "C": "bg-emerald-600 text-white font-bold",
    "D": "bg-slate-400 text-white"
  };

  // Handle facilities as string or array safely to avoid .split is not a function TypeError
  const facilitiesList = Array.isArray(recommended.facilities)
    ? recommended.facilities.map((f: unknown) => String(f).trim())
    : typeof recommended.facilities === "string"
      ? recommended.facilities.split(",").map((f: string) => f.trim())
      : Array.isArray(recommended.fasilitas)
        ? recommended.fasilitas.map((f: unknown) => String(f).trim())
        : typeof recommended.fasilitas === "string"
          ? recommended.fasilitas.split(",").map((f: string) => f.trim())
          : [];

  return (
    <Card className="w-full max-h-[90vh] md:max-h-[calc(100vh-2rem)] flex flex-col bg-white/90 dark:bg-slate-900/90 text-slate-900 dark:text-slate-100 backdrop-blur-xl border border-slate-200/30 dark:border-slate-800/50 rounded-t-3xl rounded-b-none md:rounded-2xl shadow-2xl overflow-hidden animate-in slide-in-from-bottom duration-300 md:animate-in md:slide-in-from-left">
      
      {/* Mobile Drag Pill Handle */}
      <div 
        className="md:hidden w-12 h-1 bg-slate-300 dark:bg-slate-700 rounded-full mx-auto my-2.5 shrink-0 cursor-pointer"
        onClick={() => setIsExpanded(!isExpanded)}
      />

      {/* 1. COLLAPSED VIEW (Mobile only, shown when not expanded) */}
      {!isExpanded ? (
        <div 
          className="md:hidden flex items-center justify-between px-5 pb-5 pt-0.5 cursor-pointer"
          onClick={() => setIsExpanded(true)}
        >
          <div className="flex-1 min-w-0 pr-3">
            <div className="flex items-center gap-1.5 mb-1.5">
              <Compass className="w-4.5 h-4.5 text-emerald-600 dark:text-emerald-400 animate-spin-slow shrink-0" />
              <h3 className="text-sm font-extrabold text-slate-800 dark:text-slate-100 truncate">
                {recommended.name}
              </h3>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 font-semibold flex items-center gap-1">
              <span>{Math.round(summary.estimated_time_minutes)} mnt</span>
              <span className="text-slate-300 dark:text-slate-700">•</span>
              <span>{formatDistance(summary.distance_km)}</span>
              <span className="text-slate-300 dark:text-slate-700">•</span>
              <span className={`${
                isLate
                  ? "text-rose-600 dark:text-rose-400"
                  : isClose
                    ? "text-amber-600 dark:text-amber-400"
                    : prayerTimingAvailable
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-slate-500 dark:text-slate-400"
              } font-bold`}>
                {statusText}
              </span>
            </p>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <Button 
              variant="outline" 
              size="sm" 
              className="h-8 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white hover:text-white border-none text-[11px] font-bold px-3 flex items-center gap-1"
              onClick={(e) => { e.stopPropagation(); setIsExpanded(true); }}
            >
              <Navigation className="w-3.5 h-3.5" />
              Detail
            </Button>
            <Button 
              variant="ghost" 
              size="icon" 
              aria-label="Batalkan rute"
              onClick={(e) => { e.stopPropagation(); handleClose(); }} 
              className="h-8 w-8 rounded-full text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              <X className="w-4 h-4" />
            </Button>
          </div>
        </div>
      ) : null}

      {/* 2. FULL EXPANDED VIEW (Desktop always, mobile when isExpanded) */}
      <div className={`flex-1 min-h-0 ${!isExpanded ? "hidden md:flex md:flex-col" : "flex flex-col"}`}>
        {/* Header */}
        <CardHeader className="pb-3 border-b border-slate-200/70 dark:border-slate-800/50 flex flex-row items-center justify-between bg-gradient-to-r from-emerald-50/80 to-teal-50/50 dark:from-emerald-950/20 dark:to-teal-950/20 p-4">
          <div className="flex-1">
            <CardTitle className="text-sm font-bold text-slate-800 dark:text-slate-100 flex items-center gap-1.5">
              <Compass className="w-4 h-4 text-emerald-600 dark:text-emerald-400 animate-spin-slow" />
              Rute Teroptimal Ditemukan
            </CardTitle>
            <CardDescription className="text-[10px] font-medium text-slate-500 dark:text-slate-400">
              Dihitung menggunakan {algorithm} ({executionTime} ms)
              {routeData.live_reroute ? " • diperbarui dari GPS realtime" : ""}
            </CardDescription>
            {astarFallbackUsed && (
              <p className="mt-1 text-[10px] font-semibold text-amber-700 dark:text-amber-400">
                A* tidak menemukan rute final; hasil aman dari Dijkstra digunakan.
              </p>
            )}
          </div>
          <div className="flex items-center gap-1">
            <Button 
              variant="ghost" 
              size="icon" 
              aria-label="Ciutkan detail rute"
              onClick={() => setIsExpanded(false)} 
              className="md:hidden h-7 w-7 rounded-full text-slate-400 hover:text-slate-600 hover:bg-slate-100/80 dark:hover:bg-slate-800"
            >
              <ChevronDown className="w-4 h-4" />
            </Button>
            <Button 
              variant="ghost" 
              size="icon" 
              aria-label="Batalkan rute"
              onClick={handleClose} 
              className="h-7 w-7 rounded-full text-slate-400 hover:text-slate-650 hover:bg-slate-100/80 dark:hover:bg-slate-800"
            >
              <X className="w-4 h-4" />
            </Button>
          </div>
        </CardHeader>

        <CardContent className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4 custom-scrollbar">
          <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
            <div className="bg-slate-50/90 dark:bg-slate-800/80 p-2.5 rounded-xl border border-slate-200/70 dark:border-slate-800/50 text-center flex flex-col items-center">
              <Navigation className="w-4 h-4 text-emerald-600 dark:text-emerald-400 mb-1" />
              <span className="text-[10px] text-slate-400 dark:text-slate-500 font-medium">Jarak</span>
              <span className="text-xs font-bold text-slate-800 dark:text-slate-200">{formatDistance(summary.distance_km)}</span>
            </div>
            <div className="bg-slate-50/90 dark:bg-slate-800/80 p-2.5 rounded-xl border border-slate-200/70 dark:border-slate-800/50 text-center flex flex-col items-center">
              <Clock className="w-4 h-4 text-emerald-600 dark:text-emerald-400 mb-1" />
              <span className="text-[10px] text-slate-400 dark:text-slate-500 font-medium">Total Waktu</span>
              <span className="text-xs font-bold text-slate-800 dark:text-slate-200">{Math.round(summary.estimated_time_minutes)} mnt</span>
            </div>
            <div className="bg-slate-50/90 dark:bg-slate-800/80 p-2.5 rounded-xl border border-slate-200/70 dark:border-slate-800/50 text-center flex flex-col items-center">
              <Clock className="w-4 h-4 text-emerald-600 dark:text-emerald-400 mb-1" />
              <span className="text-[10px] text-slate-400 dark:text-slate-500 font-medium">Ke Masjid</span>
              <span className="text-xs font-bold text-slate-800 dark:text-slate-200">{Math.round(summary.arrival_to_mosque_minutes)} mnt</span>
            </div>
            <div className="bg-slate-50/90 dark:bg-slate-800/80 p-2.5 rounded-xl border border-slate-200/70 dark:border-slate-800/50 text-center flex flex-col items-center">
              <Wallet className="w-4 h-4 text-emerald-600 dark:text-emerald-400 mb-1" />
              <span className="text-[10px] text-slate-400 dark:text-slate-500 font-medium">Estimasi Biaya</span>
              <span className="text-xs font-bold text-slate-800 dark:text-slate-200">
                {formatRupiah(estimatedCost)}
              </span>
            </div>
          </div>

          {estimatedCost > 0 && (
            <div className="rounded-xl border border-emerald-200 dark:border-emerald-900/40 bg-emerald-50/60 dark:bg-emerald-950/10 px-3 py-2.5">
              <p className="text-[10px] font-bold text-emerald-800 dark:text-emerald-400">Rincian estimasi biaya perjalanan</p>
              <p className="mt-1 text-[9px] leading-relaxed text-emerald-700 dark:text-emerald-350">
                BBM {formatRupiah(Number(costBreakdown.fuel_cost_rupiah || 0))}
                {" · "}operasional {formatRupiah(Number(costBreakdown.operating_cost_rupiah || 0))}
                {" · "}tol {formatRupiah(Number(costBreakdown.toll_cost_rupiah || 0))}
              </p>
            </div>
          )}

          {/* Dynamic Prayer Timer Bar */}
          <div className="bg-slate-50/70 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800/60 rounded-xl p-3.5 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-700 dark:text-slate-350">Waktu Kedatangan Shalat</span>
              <Badge className={`${statusColor} border-none text-white text-[10px] px-2 py-0.5 shadow-sm`}>
                <StatusIcon className="w-3.5 h-3.5 mr-1 inline" />
                {statusText}
              </Badge>
            </div>

            {prayerContext.departure_time && prayerContext.target_prayer_time && (
              <div className="grid grid-cols-2 gap-2 rounded-lg border border-slate-200/80 dark:border-slate-800 bg-white/80 dark:bg-slate-900/80 p-2 text-[9px] text-slate-600 dark:text-slate-300">
                <div>
                  <span className="block text-slate-400 dark:text-slate-500">Berangkat</span>
                  <strong>{prayerContext.departure_time} {prayerContext.timezone_abbreviation}</strong>
                </div>
                <div>
                  <span className="block text-slate-400 dark:text-slate-500">Estimasi tiba</span>
                  <strong>{prayerContext.arrival_time || "—"} {prayerContext.timezone_abbreviation}</strong>
                </div>
                <div>
                  <span className="block text-slate-400 dark:text-slate-500">Target</span>
                  <strong>{prayerContext.target_prayer_label || "Salat"}</strong>
                </div>
                <div>
                  <span className="block text-slate-400 dark:text-slate-500">Adzan</span>
                  <strong>
                    {prayerContext.target_prayer_time} {prayerContext.timezone_abbreviation}
                    {Number(prayerContext.target_day_offset) > 0 ? " · besok" : ""}
                  </strong>
                </div>
              </div>
            )}
            
            {/* Progress Bar */}
            <div className="w-full bg-slate-200/80 dark:bg-slate-800 rounded-full h-2 overflow-hidden">
              <div 
                className={`h-full ${statusColor} rounded-full transition-all duration-500`}
                style={{ width: `${progressPercent}%` }}
              ></div>
            </div>
            <p className="text-[10px] text-slate-500 dark:text-slate-400 font-medium text-center">{statusDesc}</p>
          </div>

          {/* Recommended Mosque Details */}
          <div className="space-y-2.5">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-sm font-extrabold text-slate-800 dark:text-slate-100 leading-tight">
                  {recommended.name}
                </h3>
                <p className="text-[10px] text-slate-400 dark:text-slate-550 font-medium mt-0.5">
                  {recommended.kecamatan || "-"}, {recommended.kabko || "-"}
                </p>
              </div>
              <Badge className={`border-none ${tierColors[tier] || tierColors["D"]} text-[10px] px-2 py-0.5`}>
                Tier {tier}
              </Badge>
            </div>

            {/* Rating, Capacity, dll */}
            <div className="flex items-center gap-4 text-xs font-semibold text-slate-600 dark:text-slate-400">
              <div className="flex items-center gap-1">
                <Star className="w-3.5 h-3.5 text-amber-500 fill-amber-500" />
                <span>{recommended.rating || "4.5"}</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="text-[10px] text-slate-400 dark:text-slate-500">Kapasitas:</span>
                <span className="capitalize">{recommended.capacity_proxy || "Sedang"}</span>
              </div>
            </div>

            {/* Facilities Badges */}
            {facilitiesList.length > 0 && (
              <div className="space-y-1.5 pt-1">
                <span className="text-[10px] text-slate-400 dark:text-slate-500 font-bold uppercase tracking-wider block">Fasilitas Masjid:</span>
                <div className="flex flex-wrap gap-1">
                  {facilitiesList.map((fac: string, idx: number) => {
                    const badgeInfo = getFacilityBadge(fac);
                    const FacIcon = badgeInfo.icon;
                    return (
                      <Badge key={idx} variant="outline" className={`text-[10px] font-semibold border px-2 py-0.5 rounded-lg shadow-sm flex items-center gap-1 ${badgeInfo.color}`}>
                        {FacIcon && <FacIcon className="w-3 h-3 shrink-0" />}
                        <span>{badgeInfo.label}</span>
                      </Badge>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Alternative Candidates */}
          {candidates.length > 1 && (
            <div className="space-y-2 pt-2 border-t border-slate-200 dark:border-slate-800">
              <span className="text-[10px] text-slate-400 dark:text-slate-500 font-bold uppercase tracking-wider block">Alternatif Masjid Lainnya:</span>
              <div className="space-y-2">
                {candidates.slice(1).map((cand: AlternativeCandidate, idx: number) => (
                  <div key={idx} className="flex items-center justify-between p-2.5 bg-slate-50/70 dark:bg-slate-850 hover:bg-slate-100 dark:hover:bg-slate-800 border border-slate-200/70 dark:border-slate-800/60 rounded-xl transition-all duration-300">
                    <div className="flex-1 min-w-0 pr-2">
                      <span className="text-xs font-bold text-slate-700 dark:text-slate-350 truncate block">{cand.name}</span>
                      <span className="text-[9px] text-slate-450 dark:text-slate-500 font-medium block mt-0.5">
                        Jarak: {formatDistance(cand.distance_km)} • Tiba: {Math.round(cand.estimated_time_minutes)} mnt
                      </span>
                    </div>
                    <Badge variant="secondary" className="text-[9px] font-bold text-slate-600 dark:text-slate-400 bg-slate-200/50 dark:bg-slate-800/50 border-none shrink-0">
                      Skor: {cand.multi_objective_score.toFixed(3)}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Compare both pathfinding algorithms on the exact active route. */}
          <div className="space-y-3 rounded-xl border border-indigo-200 dark:border-indigo-900/40 bg-indigo-50/60 dark:bg-indigo-950/10 p-3.5">
            <div className="flex items-start gap-2">
              <BarChart3 className="mt-0.5 h-4 w-4 shrink-0 text-indigo-600 dark:text-indigo-400" />
              <div>
                <p className="text-xs font-bold text-slate-800 dark:text-slate-200">Perbandingan Algoritma</p>
                <p className="mt-0.5 text-[10px] leading-relaxed text-slate-500 dark:text-slate-400">
                  Uji Dijkstra dan A* (heuristik) dari lokasi Anda ke masjid ini pada graph yang sama.
                </p>
              </div>
            </div>

            <Button
              type="button"
              size="sm"
              onClick={handleRunBenchmark}
              disabled={benchmarkLoading}
              className="h-9 w-full bg-indigo-600 hover:bg-indigo-500 dark:bg-indigo-650 dark:hover:bg-indigo-600 text-[11px] font-bold text-white"
            >
              {benchmarkLoading ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <BarChart3 className="mr-1.5 h-3.5 w-3.5" />
              )}
              {benchmarkLoading ? "Sedang Membandingkan..." : "Bandingkan Dijkstra vs A* (Heuristik)"}
            </Button>

            {benchmarkStatus && (
              <p role="status" className="text-center text-[10px] font-medium text-indigo-700">
                {benchmarkStatus}
              </p>
            )}
            {benchmarkError && (
              <p role="alert" className="rounded-lg bg-rose-50 px-2.5 py-2 text-[10px] font-semibold text-rose-700">
                {benchmarkError}
              </p>
            )}

            {benchmarkResult && (
              <div className="space-y-2" data-testid="route-algorithm-comparison">
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-2.5">
                    <p className="text-[10px] font-bold text-slate-500">Dijkstra</p>
                    <p className="mt-1 text-sm font-extrabold text-slate-800 dark:text-slate-100">
                      {benchmarkResult.dijkstra.execution_time_ms.toFixed(2)} ms
                    </p>
                    <p className="text-[9px] text-slate-500">
                      {benchmarkResult.dijkstra.explored_nodes.toLocaleString("id-ID")} node dijelajahi
                    </p>
                  </div>
                  <div className="rounded-lg border border-emerald-200 dark:border-emerald-800/80 bg-emerald-50/70 dark:bg-emerald-950/20 p-2.5">
                    <p className="text-[10px] font-bold text-emerald-700 dark:text-emerald-450">A* (Heuristik)</p>
                    <p className="mt-1 text-sm font-extrabold text-emerald-800 dark:text-emerald-100">
                      {benchmarkResult.astar.execution_time_ms.toFixed(2)} ms
                    </p>
                    <p className="text-[9px] text-emerald-750 dark:text-emerald-400">
                      {benchmarkResult.astar.explored_nodes.toLocaleString("id-ID")} node dijelajahi
                    </p>
                  </div>
                </div>

                <div className="rounded-lg bg-white dark:bg-slate-900 px-2.5 py-2 text-[10px] text-slate-600 dark:text-slate-300 border dark:border-slate-800/60">
                  <p>
                    Tercepat: <span className="font-bold text-indigo-700 dark:text-indigo-400">{benchmarkResult.comparison.faster_algorithm}</span>
                    {" · "}selisih {benchmarkResult.comparison.time_difference_ms.toFixed(2)} ms
                    {" · "}efisiensi {benchmarkResult.comparison.efficiency_gain_percent.toFixed(1)}%
                  </p>
                  <p className={`mt-1 font-semibold ${benchmarkResult.comparison.optimal_cost_match ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-450"}`}>
                    {benchmarkResult.comparison.optimal_cost_match
                      ? "✓ Bobot waktu tempuh A* sama optimalnya dengan Dijkstra."
                      : "⚠ Bobot waktu tempuh kedua algoritma berbeda."}
                  </p>
                </div>
              </div>
            )}
          </div>
        </CardContent>
      </div>
    </Card>
  );
}
