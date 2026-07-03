import { useState, useEffect } from "react";
import { useAppStore } from "@/lib/store";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { MapPin, Navigation, Clock, Locate } from "lucide-react";

const API_BASE = typeof window !== "undefined"
  ? `http://${window.location.hostname}:8000`
  : "http://127.0.0.1:8000";

export default function RouteManager() {
  const { activeDatasetId, startPoint, endPoint, mosques, setRouteData, setStartPoint, setEndPoint, routeCache, setRouteCache } = useAppStore();
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  
  const [algorithm, setAlgorithm] = useState("dijkstra");
  const [currentTime, setCurrentTime] = useState("17:00");
  const [prayer, setPrayer] = useState("maghrib");
  const [profile, setProfile] = useState("balanced");
  const [maxCandidates, setMaxCandidates] = useState("3");
  const [bufferKm, setBufferKm] = useState("10");
  const [autoBuild, setAutoBuild] = useState(true);

  // Sync searchQuery with endPoint (e.g. if set from map pins)
  useEffect(() => {
    if (!endPoint) {
      setSearchQuery("");
    } else {
      const match = mosques.find(
        m => Math.abs(m.latitude - endPoint.lat) < 0.0001 && Math.abs(m.longitude - endPoint.lng) < 0.0001
      );
      if (match && searchQuery !== match.name) {
        setSearchQuery(match.name);
      }
    }
  }, [endPoint, mosques]);

  const filteredMosques = searchQuery.trim() === ""
    ? []
    : mosques.filter(m => 
        m.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (m.kecamatan && m.kecamatan.toLowerCase().includes(searchQuery.toLowerCase()))
      ).slice(0, 6);



  const handleGetLocation = () => {
    if (!navigator.geolocation) {
      toast.error("Geolocation tidak didukung oleh browser Anda.");
      return;
    }
    
    const toastId = toast.loading("Mendeteksi lokasi saat ini...");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        toast.dismiss(toastId);
        setStartPoint({
          lat: position.coords.latitude,
          lng: position.coords.longitude
        });
        toast.success("Lokasi terdeteksi.");
      },
      (error) => {
        toast.dismiss(toastId);
        toast.error("Gagal mendeteksi lokasi. Pastikan izin GPS aktif.");
      },
      { enableHighAccuracy: true, timeout: 10000 }
    );
  };

  const handleRouteSearch = async () => {
    if (!activeDatasetId) {
      toast.error("Pilih dataset terlebih dahulu.");
      return;
    }
    if (!startPoint) {
      toast.error("Pilih setidaknya titik awal di peta.");
      return;
    }

    const cacheKey = `${activeDatasetId}_${startPoint.lat.toFixed(5)}_${startPoint.lng.toFixed(5)}_${endPoint ? `${endPoint.lat.toFixed(5)}_${endPoint.lng.toFixed(5)}` : 'none'}_${algorithm}_${currentTime}_${prayer}_${profile}_${maxCandidates}_${bufferKm}`;

    if (routeCache && routeCache[cacheKey]) {
      setRouteData(routeCache[cacheKey]);
      toast.success("Rute berhasil ditemukan (dari Cache Lokal).");
      return;
    }

    setLoading(true);
    try {
      const payload = {
        dataset_id: activeDatasetId,
        origin: {
          latitude: startPoint.lat,
          longitude: startPoint.lng
        },
        destination: endPoint ? {
          latitude: endPoint.lat,
          longitude: endPoint.lng
        } : {
          latitude: startPoint.lat,
          longitude: startPoint.lng
        },
        algorithm: algorithm,
        departure_time: `2026-07-11T${currentTime || '17:00'}:00+07:00`,
        prayer: prayer,
        profile: profile,
        maximum_results: parseInt(maxCandidates),
        search_radius_km: parseFloat(bufferKm),
        auto_build_osm: autoBuild,
      };

      const res = await fetch(`${API_BASE}/api/v1/routes/recommend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Gagal mencari rute");
      }

      const data = await res.json();
      setRouteData(data);
      if (setRouteCache) {
        setRouteCache(cacheKey, data);
      }
      toast.success("Rute berhasil ditemukan.");
    } catch (err: any) {
      // Offline fallback: cari cache yang mencakup koordinat start dan end yang sama
      const startKey = `${startPoint.lat.toFixed(5)}_${startPoint.lng.toFixed(5)}`;
      const endKey = endPoint ? `${endPoint.lat.toFixed(5)}_${endPoint.lng.toFixed(5)}` : startKey;
      
      const cachedMatch = Object.entries(routeCache || {}).find(([key]) => {
        return key.includes(startKey) && key.includes(endKey);
      });

      if (cachedMatch) {
        setRouteData(cachedMatch[1]);
        toast.info("Koneksi internet terputus (Offline). Menampilkan rute dari cache perjalanan sebelumnya.");
      } else {
        toast.error(err.message || "Gagal mencari rute. Silakan periksa koneksi internet Anda.");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleClearPoints = () => {
    setStartPoint(null);
    setEndPoint(null);
    setRouteData(null);
  };

  return (
    <div suppressHydrationWarning className="flex flex-col gap-6">
      <Card className="border-slate-100 dark:border-slate-800/80 shadow-md rounded-2xl bg-white dark:bg-slate-900/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-extrabold text-slate-800 dark:text-slate-100 flex items-center gap-1.5">
            <Navigation className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
            Navigasi Perjalanan
          </CardTitle>
          <CardDescription className="text-[11px] font-medium text-slate-500 dark:text-slate-400">
            Tentukan titik awal dan cari masjid tujuan Anda secara langsung.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3.5 pb-4">
          
          {/* Start Point Selection */}
          <div className="space-y-1.5">
            <Label className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Titik Awal (Posisi Anda)</Label>
            <div className="flex items-center gap-2 p-3 bg-slate-50 dark:bg-slate-950/80 border border-slate-200 dark:border-slate-800 rounded-xl relative">
              <div className="w-2.5 h-2.5 rounded-full bg-emerald-600 dark:bg-emerald-400 shrink-0"></div>
              <div className="flex-1 text-xs font-semibold text-slate-700 dark:text-slate-300 truncate">
                {startPoint ? `${startPoint.lat.toFixed(5)}, ${startPoint.lng.toFixed(5)}` : "Mencari lokasi..."}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <Button 
                  variant="ghost" 
                  size="icon" 
                  onClick={handleGetLocation} 
                  className="h-7 w-7 rounded-lg text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/40"
                  title="Deteksi Lokasi GPS"
                >
                  <Locate className="w-3.5 h-3.5" />
                </Button>
                {startPoint && (
                  <button 
                    onClick={() => setStartPoint(null)} 
                    className="text-[10px] font-bold text-slate-400 hover:text-slate-600 px-1"
                  >
                    Atur Ulang
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Destination Search Bar (Google Maps style) */}
          <div className="space-y-1.5 relative">
            <Label className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Cari Masjid Tujuan</Label>
            <div className="relative">
              <MapPin className="absolute left-3 top-3 w-4 h-4 text-rose-500 dark:text-rose-400" />
              <Input 
                placeholder="Ketik nama masjid..."
                value={searchQuery}
                onFocus={() => setIsFocused(true)}
                onBlur={() => setTimeout(() => setIsFocused(false), 250)} // delay to allow clicks
                onChange={e => {
                  setSearchQuery(e.target.value);
                  if (e.target.value === "") {
                    setEndPoint(null);
                  }
                }}
                className="pl-9 pr-14 bg-slate-50 dark:bg-slate-950/80 border border-slate-200 dark:border-slate-800 rounded-xl text-xs font-semibold h-10"
              />
              {searchQuery && (
                <button 
                  onClick={() => { setSearchQuery(""); setEndPoint(null); }}
                  className="absolute right-3 top-3 text-[10px] font-bold text-slate-400 hover:text-slate-600 transition-colors"
                >
                  Hapus
                </button>
              )}
            </div>

            {/* Autocomplete Dropdown list */}
            {isFocused && filteredMosques.length > 0 && (
              <div className="absolute left-0 right-0 top-full mt-1.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-xl z-[100] overflow-hidden divide-y divide-slate-100 dark:divide-slate-800 animate-in fade-in slide-in-from-top-2 duration-200">
                {filteredMosques.map((m, idx) => (
                  <div 
                    key={`${m.id || 'mosque'}-${idx}`}
                    className="px-4 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/80 cursor-pointer text-xs transition-colors flex flex-col"
                    onMouseDown={() => {
                      setEndPoint({ lat: m.latitude, lng: m.longitude });
                      setSearchQuery(m.name);
                      toast.success(`Tujuan diset ke: ${m.name}`);
                    }}
                  >
                    <span className="font-bold text-slate-800 dark:text-slate-200">{m.name}</span>
                    <span className="text-[10px] text-slate-400 dark:text-slate-500 mt-0.5">{m.kecamatan || "-"}, {m.kabko || "-"}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pengaturan Pencarian</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Algoritma</Label>
              <Select value={algorithm} onValueChange={(val) => setAlgorithm(val || "dijkstra")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="dijkstra">Dijkstra</SelectItem>
                  <SelectItem value="astar">A* (Heuristik)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Profil Rute</Label>
              <Select value={profile} onValueChange={(val) => setProfile(val || "balanced")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="balanced">Balanced (Seimbang)</SelectItem>
                  <SelectItem value="fastest">Fastest (Waktu)</SelectItem>
                  <SelectItem value="prayer_priority">Prayer (Waktu Salat)</SelectItem>
                  <SelectItem value="low_cost">Low Cost (Biaya)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Waktu Berangkat</Label>
              <div className="relative">
                <Clock className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-500" />
                <Input type="time" value={currentTime} onChange={e => setCurrentTime(e.target.value)} className="pl-9" />
              </div>
            </div>
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
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Rekomendasi</Label>
              <Input type="number" min="1" max="10" value={maxCandidates} onChange={e => setMaxCandidates(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>Radius (km)</Label>
              <Input type="number" min="2" max="50" step="0.5" value={bufferKm} onChange={e => setBufferKm(e.target.value)} />
            </div>
          </div>

          <div className="flex items-center space-x-2 pt-2">
            <Checkbox id="autoBuild" checked={autoBuild} onCheckedChange={(c) => setAutoBuild(!!c)} />
            <label htmlFor="autoBuild" className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">
              Otomatis fetch map (Overpass) jika tidak ada
            </label>
          </div>
          
          <Button className="w-full mt-2 bg-emerald-600 hover:bg-emerald-700 text-white" onClick={handleRouteSearch} disabled={loading || !startPoint}>
            <Navigation className="w-4 h-4 mr-2" />
            {loading ? "Mencari Rute..." : "Cari Rute Teroptimal"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
