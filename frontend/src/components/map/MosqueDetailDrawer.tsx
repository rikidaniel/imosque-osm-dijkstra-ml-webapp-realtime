"use client";

import { useEffect, useRef, useState } from "react";
import { useAppStore } from "@/lib/store";
import { formatDistance } from "@/lib/utils";
import { 
  X, Navigation, Award, Star, Compass, MapPin, Heart, Info,
  Car, Droplets, Users, User, Snowflake, Volume2, Wifi, Utensils, BookOpen
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import {
  buildSelectedRouteCacheKey,
  isAbortError,
  isRouteCacheFresh,
  prewarmRoutingDataset,
  routeToMosque,
  SELECTED_ROUTE_CACHE_TTL_MS,
} from "@/lib/api";
import { buildNationalDepartureTime } from "@/lib/prayer-routing";

// Helper function to map facilities to icons/labels
const getFacilityBadge = (fac: string) => {
  const f = fac.trim().toLowerCase();
  const facilityMap: Record<string, { label: string; icon: any; color: string }> = {
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

export default function MosqueDetailDrawer() {
  const { selectedMosque, setSelectedMosque, startPoint, activeDatasetId, setRouteData, searchSettings, routeCache, setRouteCache, setEndPoint } = useAppStore();
  const [isRouting, setIsRouting] = useState(false);
  const routeAbortRef = useRef<AbortController | null>(null);

  useEffect(() => () => routeAbortRef.current?.abort(), []);

  useEffect(() => {
    if (!selectedMosque || !startPoint) return;
    const datasetForRoute = (activeDatasetId === "all" || !activeDatasetId)
      ? selectedMosque.dataset_id
      : activeDatasetId;
    void prewarmRoutingDataset(datasetForRoute || "", {
      start: startPoint,
      end: {
        lat: Number(selectedMosque.latitude),
        lng: Number(selectedMosque.longitude),
      },
      bufferKm: Math.min(parseFloat(searchSettings.bufferKm) || 8, 10),
    }).catch(() => undefined);
  }, [activeDatasetId, searchSettings.bufferKm, selectedMosque, startPoint]);

  if (!selectedMosque) return null;

  const m = selectedMosque;
  
  // Parse facilities list
  let rawFacs: string[] = [];
  if (m.facilities) {
    if (Array.isArray(m.facilities)) {
      rawFacs = m.facilities;
    } else if (typeof m.facilities === "string") {
      rawFacs = m.facilities.split(/[|,;]+/).map((f: string) => f.trim()).filter(Boolean);
    }
  }

  const handleRouteToMosqueAction = async () => {
    if (isRouting) return;
    if (!startPoint) {
      toast.error("Lokasi awal Anda belum terdeteksi. Izinkan GPS terlebih dahulu.");
      return;
    }
    const algoLabel = searchSettings.algorithm === "astar" ? "A*" : "Dijkstra";
    const toastId = toast.loading(`Menghitung rute optimal (${algoLabel}) ke ${m.name}...`);
    routeAbortRef.current?.abort();
    const controller = new AbortController();
    routeAbortRef.current = controller;
    setIsRouting(true);
    try {
      // Gunakan dataset_id masjid itu sendiri jika activeDatasetId adalah "all"
      const datasetForRoute = (activeDatasetId === "all" || !activeDatasetId)
        ? (m.dataset_id || activeDatasetId || "all")
        : activeDatasetId;
      // Batasi bufferKm untuk routing (maks 50km)
      const routeBuffer = Math.min(parseFloat(searchSettings.bufferKm) || 10, 50);
      const mosqueId = String(m.id || m.mosque_id || m.name);
      const departureContext = buildNationalDepartureTime(
        searchSettings.departureMode,
        searchSettings.currentTime,
        startPoint.lng,
      );
      const costFingerprint = `${searchSettings.fuelPricePerLiter}-${searchSettings.fuelEfficiencyKmPerLiter}-${searchSettings.operatingCostPerKm}-${searchSettings.tollCostPerKm}`;
      const routeKey = buildSelectedRouteCacheKey(
        datasetForRoute,
        startPoint.lat,
        startPoint.lng,
        mosqueId,
        searchSettings.algorithm,
        costFingerprint,
        `${departureContext.cacheKey}-${searchSettings.prayer}`,
      );
      const cached = routeCache?.[routeKey];
      if (isRouteCacheFresh(cached, SELECTED_ROUTE_CACHE_TTL_MS)) {
        setEndPoint({ lat: m.latitude, lng: m.longitude });
        setRouteData(cached);
        setSelectedMosque(null);
        toast.success(`Rute (${algoLabel}) dimuat dari cache.`);
        return;
      }
      const data = await routeToMosque(
        datasetForRoute,
        startPoint.lat,
        startPoint.lng,
        mosqueId,
        searchSettings.algorithm,
        routeBuffer,
        false,
        controller.signal,
        {
          fuel_price_per_liter: Number(searchSettings.fuelPricePerLiter),
          fuel_efficiency_km_per_liter: Number(searchSettings.fuelEfficiencyKmPerLiter),
          operating_cost_per_km: Number(searchSettings.operatingCostPerKm),
          toll_cost_per_km: Number(searchSettings.tollCostPerKm),
        },
        departureContext.iso,
        searchSettings.prayer
      );
      setEndPoint({ lat: m.latitude, lng: m.longitude });
      setRouteData(data);
      setRouteCache(routeKey, data);
      setSelectedMosque(null); // Close drawer on route search success
      toast.success(`Rute (${algoLabel}) ke ${m.name} berhasil ditemukan.`);
    } catch (err: any) {
      if (isAbortError(err)) return;
      toast.error(err.message || "Gagal menghitung rute navigasi.");
    } finally {
      if (routeAbortRef.current === controller) {
        routeAbortRef.current = null;
        setIsRouting(false);
      }
      toast.dismiss(toastId);
    }
  };

  // Safe formatting for numeric strings/numbers
  const formatRating = (val: any) => {
    if (val === undefined || val === null) return "0.0";
    const num = parseFloat(String(val).replace(",", "."));
    return isNaN(num) ? "0.0" : num.toFixed(1);
  };

  const formatReviewCount = (val: any) => {
    if (val === undefined || val === null) return "0";
    const num = parseInt(String(val).replace(/[^0-9]/g, ""));
    return isNaN(num) ? "0" : num.toLocaleString("id-ID");
  };

  return (
    <div className="fixed inset-x-0 bottom-0 md:left-4 md:right-auto md:top-4 md:bottom-4 md:w-[440px] z-50 animate-in slide-in-from-bottom duration-300 pointer-events-auto">
      {/* Background glass card */}
      <div className="bg-white/90 dark:bg-slate-900/90 backdrop-blur-xl border border-slate-200/30 dark:border-slate-800/50 rounded-t-3xl md:rounded-3xl shadow-2xl overflow-hidden max-h-[85vh] md:max-h-full md:h-full flex flex-col">
        
        {/* Decorative drag handle for mobile */}
        <div className="w-12 h-1.5 bg-slate-300 dark:bg-slate-700 rounded-full mx-auto my-3 md:hidden"></div>

        {/* Cover image area */}
        <div className="relative h-36 md:h-44 bg-gradient-to-r from-emerald-800 via-emerald-950 to-teal-900 dark:from-emerald-900 dark:via-emerald-950 dark:to-teal-950 shrink-0">
          {m.image_url ? (
            <img 
              src={m.image_url} 
              alt={m.name} 
              className="w-full h-full object-cover opacity-80" 
            />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center text-emerald-300/40">
              <Compass className="w-16 h-16 animate-spin-slow" />
            </div>
          )}

          {/* Close button */}
          <button 
            aria-label="Tutup detail masjid"
            onClick={() => setSelectedMosque(null)}
            className="absolute top-4 right-4 p-2 rounded-full bg-slate-950/40 hover:bg-slate-950/60 text-white backdrop-blur-sm transition-colors border-0 cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>

          {/* Floating Tier Badges */}
          <div className="absolute bottom-4 left-4 flex gap-2">
            <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white border-0 text-[10px] font-bold px-2 py-0.5 shadow-md">
              <Award className="w-3.5 h-3.5 mr-1" />
              Tier {m.tier || "D"}
            </Badge>
            {m.mosque_type && (
              <Badge className="bg-slate-900/60 hover:bg-slate-900/60 text-white border-0 text-[10px] font-bold px-2 py-0.5 backdrop-blur-sm">
                {String(m.mosque_type).replace("_", " ").toUpperCase()}
              </Badge>
            )}
          </div>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 min-h-0 overflow-y-auto p-6 space-y-5 custom-scrollbar">
          {/* Mosque Name & Address */}
          <div>
            <h2 className="text-xl font-black text-slate-800 dark:text-slate-100 tracking-tight leading-tight">{m.name}</h2>
            <div className="flex items-start gap-1.5 mt-2.5 text-slate-500 dark:text-slate-400">
              <MapPin className="w-4 h-4 text-slate-400 shrink-0 mt-0.5" />
              <p className="text-xs leading-relaxed">{m.address || `${m.kelurahan || ""}, ${m.kecamatan || ""}, ${m.kabko || ""}, ${m.provinsi || ""}`}</p>
            </div>
          </div>

          {/* Rating, Distance, Priority */}
          <div className="grid grid-cols-3 gap-2.5 py-4 border-y border-slate-100 dark:border-slate-800/80">
            <div className="text-center">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Rating</span>
              <div className="flex items-center justify-center gap-1 mt-1 text-slate-800 dark:text-slate-200">
                <Star className="w-4 h-4 text-amber-500 fill-amber-500" />
                <span className="text-sm font-black">{formatRating(m.rating)}</span>
              </div>
              <span className="text-[9px] text-slate-400 block mt-0.5">{formatReviewCount(m.review_count)} ulasan</span>
            </div>

            <div className="text-center border-x border-slate-100 dark:border-slate-800/80">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Jarak</span>
              <div className="flex items-center justify-center gap-0.5 mt-1 text-emerald-600 dark:text-emerald-400">
                <span className="text-sm font-black">
                  {m.distance_km !== undefined ? formatDistance(m.distance_km) : "N/A"}
                </span>
              </div>
              <span className="text-[9px] text-slate-400 block mt-0.5">dari GPS Anda</span>
            </div>

            <div className="text-center">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Kunjungan</span>
              <div className="flex items-center justify-center gap-1 mt-1 text-slate-800 dark:text-slate-200">
                <Heart className="w-4 h-4 text-rose-500 fill-rose-500/10" />
                <span className="text-sm font-black">{formatReviewCount(m.checkin_count)}</span>
              </div>
              <span className="text-[9px] text-slate-400 block mt-0.5">check-in</span>
            </div>
          </div>

          {/* Facilities Section */}
          <div className="space-y-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">Fasilitas Masjid</h3>
            {rawFacs.length > 0 ? (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {rawFacs.map((fac, idx) => {
                  const badge = getFacilityBadge(fac);
                  const FacIcon = badge.icon;
                  return (
                    <Badge 
                      key={idx} 
                      variant="outline" 
                      className={`text-[10px] py-1 px-2.5 rounded-lg border font-medium flex items-center gap-1.5 ${badge.color}`}
                    >
                      {FacIcon && <FacIcon className="w-3 h-3 shrink-0" />}
                      <span>{badge.label}</span>
                    </Badge>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-slate-400 italic">Informasi fasilitas belum diunggah.</p>
            )}
          </div>
        </div>

        {/* Footer Actions */}
        <div className="p-6 bg-slate-50/90 dark:bg-slate-900/90 border-t border-slate-200/70 dark:border-slate-800/60 shrink-0 flex gap-3">
          <Button 
            variant="outline" 
            className="flex-1 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-750 rounded-xl"
            onClick={() => setSelectedMosque(null)}
          >
            Batal
          </Button>
          <Button 
            className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl font-bold flex items-center justify-center gap-2 border dark:border-emerald-700"
            onClick={handleRouteToMosqueAction}
            disabled={isRouting}
          >
            <Navigation className="w-4 h-4" />
            {isRouting ? "Menghitung..." : "Mulai Rute"}
          </Button>
        </div>

      </div>
    </div>
  );
}
