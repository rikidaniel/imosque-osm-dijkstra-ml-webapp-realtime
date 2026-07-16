"use client";

import { useAppStore } from "@/lib/store";
import { formatDistance } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useState } from "react";
import { 
  X, Navigation, Clock, Star, Info, CheckCircle2, AlertTriangle, AlertCircle, Compass, ChevronDown,
  Car, Droplets, Users, User, Snowflake, Volume2, Wifi, Utensils, BookOpen
} from "lucide-react";

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

export default function RouteResultPanel() {
  const { routeData, setRouteData } = useAppStore();
  const [isExpanded, setIsExpanded] = useState(false);

  if (!routeData) return null;

  const summary = routeData.route_summary;
  const recommended = routeData.recommended_mosque;
  const candidates = routeData.candidate_mosques || [];
  const algorithm = routeData.algorithm;
  const executionTime = routeData.execution_time_ms;

  const handleClose = () => {
    setRouteData(null);
  };

  // Determine prayer status and colors
  // minutes_before_prayer represents minutes remaining until prayer when user arrives
  const minBefore = summary.minutes_before_prayer;
  const arrivalStatus = summary.arrival_status;

  let statusColor = "bg-emerald-500";
  let statusText = "Aman";
  let statusDesc = `Estimasi tiba ${Math.round(minBefore)} menit sebelum adzan.`;
  let StatusIcon = CheckCircle2;
  let progressPercent = 100;

  if (arrivalStatus === "after_prayer" || minBefore < 0) {
    statusColor = "bg-rose-500";
    statusText = "Terlambat";
    statusDesc = `Estimasi tiba ${Math.round(Math.abs(minBefore))} menit setelah adzan.`;
    StatusIcon = AlertCircle;
    progressPercent = 0;
  } else if (minBefore <= 15) {
    statusColor = "bg-amber-500";
    statusText = "Mepet";
    statusDesc = `Tiba hanya ${Math.round(minBefore)} menit sebelum adzan.`;
    StatusIcon = AlertTriangle;
    progressPercent = Math.min(100, Math.max(10, (minBefore / 15) * 100));
  } else {
    // minutes_before_prayer > 15
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
    ? recommended.facilities.map((f: any) => String(f).trim())
    : typeof recommended.facilities === "string"
      ? recommended.facilities.split(",").map((f: string) => f.trim())
      : Array.isArray(recommended.fasilitas)
        ? recommended.fasilitas.map((f: any) => String(f).trim())
        : typeof recommended.fasilitas === "string"
          ? recommended.fasilitas.split(",").map((f: string) => f.trim())
          : [];

  return (
    <Card className="w-full max-h-[90vh] md:max-h-[90vh] flex flex-col bg-white/97 text-slate-900 backdrop-blur-lg border border-slate-200/80 rounded-t-3xl rounded-b-none md:rounded-2xl shadow-xl overflow-hidden animate-in slide-in-from-bottom duration-300 md:animate-in md:slide-in-from-right">
      
      {/* Mobile Drag Pill Handle */}
      <div 
        className="md:hidden w-12 h-1 bg-slate-300 rounded-full mx-auto my-2.5 shrink-0 cursor-pointer"
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
                arrivalStatus === "after_prayer" || minBefore < 0
                  ? "text-rose-600 dark:text-rose-400"
                  : minBefore <= 15
                    ? "text-amber-600 dark:text-amber-400"
                    : "text-emerald-600 dark:text-emerald-400"
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
      <div className={`${!isExpanded ? "hidden md:flex md:flex-col" : "flex flex-col"}`}>
        {/* Header */}
        <CardHeader className="pb-3 border-b border-slate-200/70 flex flex-row items-center justify-between bg-gradient-to-r from-emerald-50/80 to-teal-50/50 p-4">
          <div className="flex-1">
            <CardTitle className="text-sm font-bold text-slate-800 dark:text-slate-100 flex items-center gap-1.5">
              <Compass className="w-4 h-4 text-emerald-600 dark:text-emerald-400 animate-spin-slow" />
              Rute Teroptimal Ditemukan
            </CardTitle>
            <CardDescription className="text-[10px] font-medium text-slate-500 dark:text-slate-400">
              Dihitung menggunakan {algorithm} ({executionTime} ms)
            </CardDescription>
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
              className="h-7 w-7 rounded-full text-slate-400 hover:text-slate-600 hover:bg-slate-100/80 dark:hover:bg-slate-800"
            >
              <X className="w-4 h-4" />
            </Button>
          </div>
        </CardHeader>

        <CardContent className="flex-1 overflow-y-auto p-4 space-y-4 custom-scrollbar max-h-[50vh] md:max-h-none">
          {/* Dynamic Route Info */}
          <div className="grid grid-cols-3 gap-2.5">
            <div className="bg-slate-50/90 p-2.5 rounded-xl border border-slate-200/70 text-center flex flex-col items-center">
              <Navigation className="w-4 h-4 text-emerald-600 mb-1" />
              <span className="text-[10px] text-slate-400 font-medium">Jarak</span>
              <span className="text-xs font-bold text-slate-800 dark:text-slate-200">{formatDistance(summary.distance_km)}</span>
            </div>
            <div className="bg-slate-50/90 p-2.5 rounded-xl border border-slate-200/70 text-center flex flex-col items-center">
              <Clock className="w-4 h-4 text-emerald-600 mb-1" />
              <span className="text-[10px] text-slate-400 font-medium">Total Waktu</span>
              <span className="text-xs font-bold text-slate-800 dark:text-slate-200">{Math.round(summary.estimated_time_minutes)} mnt</span>
            </div>
            <div className="bg-slate-50/90 p-2.5 rounded-xl border border-slate-200/70 text-center flex flex-col items-center">
              <Clock className="w-4 h-4 text-emerald-600 mb-1" />
              <span className="text-[10px] text-slate-400 font-medium">Ke Masjid</span>
              <span className="text-xs font-bold text-slate-800 dark:text-slate-200">{Math.round(summary.arrival_to_mosque_minutes)} mnt</span>
            </div>
          </div>

          {/* Dynamic Prayer Timer Bar */}
          <div className="bg-slate-50/70 border border-slate-200 rounded-xl p-3.5 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">Waktu Kedatangan Shalat</span>
              <Badge className={`${statusColor} border-none text-white text-[10px] px-2 py-0.5 shadow-sm`}>
                <StatusIcon className="w-3.5 h-3.5 mr-1 inline" />
                {statusText}
              </Badge>
            </div>
            
            {/* Progress Bar */}
            <div className="w-full bg-slate-200/80 rounded-full h-2 overflow-hidden">
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
                <p className="text-[10px] text-slate-400 dark:text-slate-500 font-medium mt-0.5">
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
                <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider block">Fasilitas Masjid:</span>
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
            <div className="space-y-2 pt-2 border-t border-slate-200">
              <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider block">Alternatif Masjid Lainnya:</span>
              <div className="space-y-2">
                {candidates.slice(1).map((cand: any, idx: number) => (
                  <div key={idx} className="flex items-center justify-between p-2.5 bg-slate-50/70 hover:bg-slate-100 border border-slate-200/70 rounded-xl transition-all duration-300">
                    <div className="flex-1 min-w-0 pr-2">
                      <span className="text-xs font-bold text-slate-700 dark:text-slate-300 truncate block">{cand.name}</span>
                      <span className="text-[9px] text-slate-400 dark:text-slate-500 font-medium block mt-0.5">
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
        </CardContent>
      </div>
    </Card>
  );
}
