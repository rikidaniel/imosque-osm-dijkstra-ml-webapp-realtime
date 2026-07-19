"use client";

import { useEffect, useState, useMemo, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useTheme } from "@/components/theme-provider";
import { toast } from "sonner";
import { useAppStore } from "@/lib/store";
import { formatDistance } from "@/lib/utils";
import MapViewer from "@/components/map/MapViewer";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  buildSelectedRouteCacheKey,
  fetchDatasets,
  fetchNearestMosques,
  fetchPrayerTimes,
  routeToMosque,
  fetchMosques,
  isAbortError,
  isRouteCacheFresh,
  prewarmRoutingDataset,
  RECOMMENDATION_CACHE_TTL_MS,
  SELECTED_ROUTE_CACHE_TTL_MS,
} from "@/lib/api";
import { 
  Search, Settings, Clock, Compass, Navigation, Star, RotateCcw, X, MapPin, 
  Locate, Bell, BellOff, Volume2, VolumeX, CheckCircle2, AlertTriangle, AlertCircle, Shield,
  Sun, Moon
} from "lucide-react";
import { saveSettingsToDatabase } from "@/lib/settings-sync";
import { buildNationalDepartureTime, prayerTargetLabel } from "@/lib/prayer-routing";
import { API_BASE } from "@/lib/config";

const RouteResultPanel = dynamic(() => import("@/components/route/RouteResultPanel"), { ssr: false });
const MosqueDetailDrawer = dynamic(() => import("@/components/map/MosqueDetailDrawer"), { ssr: false });

const NEAREST_DEBOUNCE_MS = 180;
const GPS_MIN_MOVEMENT_METERS = 25;
const GPS_MAX_SILENCE_MS = 15 * 1000;
const NEAREST_REFRESH_DISTANCE_METERS = 250;
const NEAREST_REFRESH_INTERVAL_MS = 20 * 1000;
const LIVE_REROUTE_DISTANCE_METERS = 75;
const LIVE_REROUTE_INTERVAL_MS = 5 * 1000;
const REGION_JUMP_RESET_METERS = 10 * 1000;

type LatLngPoint = { lat: number; lng: number };

function distanceMeters(a: LatLngPoint, b: LatLngPoint) {
  const earthRadiusM = 6_371_000;
  const toRadians = (value: number) => value * Math.PI / 180;
  const dLat = toRadians(b.lat - a.lat);
  const dLng = toRadians(b.lng - a.lng);
  const lat1 = toRadians(a.lat);
  const lat2 = toRadians(b.lat);
  const haversine = Math.sin(dLat / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * earthRadiusM * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
}

interface PrayerSchedule {
  name: string;
  time: string;
  isAlarmActive: boolean;
}

function decodePolyline(encoded: string): [number, number][] {
  const points: [number, number][] = [];
  let index = 0;
  let lat = 0;
  let lon = 0;
  while (index < encoded.length) {
    let result = 0;
    let shift = 0;
    let byte: number;
    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20 && index < encoded.length);
    lat += result & 1 ? ~(result >> 1) : result >> 1;
    result = 0;
    shift = 0;
    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20 && index < encoded.length);
    lon += result & 1 ? ~(result >> 1) : result >> 1;
    points.push([lat / 1e5, lon / 1e5]);
  }
  return points;
}

export default function SafarDashboard() {
  const { 
    startPoint, 
    startPointSource,
    endPoint, 
    setStartPoint, 
    setEndPoint, 
    routeData,
    setRouteData,
    activeDatasetId, 
    setActiveDatasetId,
    setDatasets,
    mosques, 
    setMosques,
    selectedMosque,
    setSelectedMosque,
    searchSettings,
    setSearchSettings,
    routeCache,
    setRouteCache,
    prayerSchedule,
    setPrayerSchedule,
    hijriDate,
    setHijriDate,
    masehiDate,
    setMasehiDate,
    prayerCacheKey,
    setPrayerCacheKey,
    settingsSyncStatus,
  } = useAppStore();
  
  const router = useRouter();
  const { theme, setTheme, resolvedTheme } = useTheme();

  // Local States
  const [searchQuery, setSearchQuery] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showPrayer, setShowPrayer] = useState(false);
  const [loading, setLoading] = useState(false);
  const [isLocating, setIsLocating] = useState(false);
  const [isGpsTracking, setIsGpsTracking] = useState(false);
  const [showLocationPopup, setShowLocationPopup] = useState(false);
  const [isGpsChecking, setIsGpsChecking] = useState(() => !startPoint && startPointSource !== "map");
  const [gpsRefreshTick, setGpsRefreshTick] = useState(0);
  const [savingSettings, setSavingSettings] = useState(false);
  const [savingAlarmIndex, setSavingAlarmIndex] = useState<number | null>(null);

  // Quick Filter States
  const [filterRating, setFilterRating] = useState(false);
  const [filterTierA, setFilterTierA] = useState(false);
  const [filterAC, setFilterAC] = useState(false);
  const [filterParking, setFilterParking] = useState(false);
  const [filterWudu, setFilterWudu] = useState(false);
  const [filterToilet, setFilterToilet] = useState(false);

  // Settings Local Sync State (to prevent instant write lag)
  const [localSettings, setLocalSettings] = useState(searchSettings);
  const [nextPrayer, setNextPrayer] = useState({ name: "Maghrib", countdown: "00:00:00" });
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [isRouteExpanded, setIsRouteExpanded] = useState(false);

  const locationRequestRef = useRef(0);
  const nearestRequestRef = useRef(0);
  const selectedRouteRequestRef = useRef(0);
  const routeToMosqueAbortRef = useRef<AbortController | null>(null);
  const recommendationAbortRef = useRef<AbortController | null>(null);
  const liveRerouteAbortRef = useRef<AbortController | null>(null);
  const lastAcceptedGpsRef = useRef<{ point: LatLngPoint; at: number } | null>(null);
  const lastNearestRefreshRef = useRef<{
    point: LatLngPoint;
    at: number;
    requestKey: string;
  } | null>(null);
  const previousStartPointRef = useRef<LatLngPoint | null>(startPoint);
  const liveRerouteOriginRef = useRef<{ point: LatLngPoint; at: number } | null>(null);

  // Sync Local Settings with Store once loaded + auto-migrate nilai lama
  useEffect(() => {
    const settings = { ...searchSettings };
    setLocalSettings(settings);
  }, [searchSettings, setSearchSettings]);

  useEffect(() => {
    if (!routeData) {
      setIsRouteExpanded(false);
    }
  }, [routeData]);

  // Load Datasets
  useEffect(() => {
    fetchDatasets()
      .then(data => {
        if (data.items) {
          setDatasets(data.items);
          
          // Auto-select active dataset from backend if current is NOT 'all' and not found
          const activeBackend = data.items.find((d: any) => d.is_active);
          if (activeBackend && activeDatasetId !== "all") {
            const isCurrentValid = data.items.some((d: any) => d.dataset_id === activeDatasetId);
            if (!isCurrentValid && activeDatasetId) {
              setActiveDatasetId(activeBackend.dataset_id);
            }
          }
        }
      })
      .catch(console.error);
  }, [setDatasets, activeDatasetId, setActiveDatasetId]);

  // Show guide toast on initial mount if start point is not set
  useEffect(() => {
    if (!startPoint) {
      toast.info("Silakan tentukan titik awal perjalanan dengan mengklik peta atau menggunakan tombol Lokasi Saya 📍");
    }
  }, [startPoint]);

  // Track GPS continuously after permission is granted. Distance and time
  // thresholds absorb sensor jitter while still accepting a city jump on the
  // very first callback, so moving Jakarta -> Bandung is detected immediately.
  useEffect(() => {
    let cancelled = false;
    let watchId: number | null = null;
    if (startPointSource === "map") {
      setIsGpsTracking(false);
      return;
    }
    if (!navigator.geolocation || !navigator.permissions) {
      Promise.resolve().then(() => setIsGpsChecking(false));
      return;
    }

    navigator.permissions.query({ name: "geolocation" as PermissionName })
      .then((permission) => {
        if (cancelled || permission.state !== "granted") {
          if (!cancelled) setIsGpsChecking(false);
          return;
        }

        const requestId = ++locationRequestRef.current;
        watchId = navigator.geolocation.watchPosition(
          (position) => {
            if (cancelled || requestId !== locationRequestRef.current) return;
            const point = {
              lat: position.coords.latitude,
              lng: position.coords.longitude,
            };
            const updatedAt = position.timestamp || Date.now();
            const previous = lastAcceptedGpsRef.current;
            if (previous) {
              const movedMeters = distanceMeters(previous.point, point);
              const elapsedMs = Math.max(0, updatedAt - previous.at);
              if (
                movedMeters < GPS_MIN_MOVEMENT_METERS
                && (movedMeters < 10 || elapsedMs < GPS_MAX_SILENCE_MS)
              ) {
                setIsGpsChecking(false);
                setIsGpsTracking(true);
                return;
              }
            }
            lastAcceptedGpsRef.current = { point, at: updatedAt };
            setStartPoint(
              point,
              updatedAt,
              "gps"
            );
            setIsGpsChecking(false);
            setIsGpsTracking(true);
          },
          (error) => {
            if (cancelled || requestId !== locationRequestRef.current) return;
            console.warn("Realtime GPS watch failed:", error);
            setIsGpsChecking(false);
            setIsGpsTracking(false);
          },
          { enableHighAccuracy: true, maximumAge: 3000, timeout: 10000 }
        );
      })
      .catch((error) => {
        if (!cancelled) {
          console.warn("Permissions query not supported or failed:", error);
          setIsGpsChecking(false);
        }
      });

    return () => {
      cancelled = true;
      if (watchId !== null) navigator.geolocation.clearWatch(watchId);
      setIsGpsTracking(false);
    };
  }, [gpsRefreshTick, startPointSource, setStartPoint]);

  // Browsers may suspend GPS callbacks in a background tab. Restart the watch
  // immediately when the app becomes visible again.
  useEffect(() => {
    const restartTracking = () => {
      if (document.visibilityState !== "visible") return;
      if (useAppStore.getState().startPointSource !== "map") {
        setGpsRefreshTick((tick) => tick + 1);
      }
    };
    document.addEventListener("visibilitychange", restartTracking);
    return () => document.removeEventListener("visibilitychange", restartTracking);
  }, []);

  // Fetch Mosques based on startPoint, active dataset, and search settings
  useEffect(() => {
    const requestId = ++nearestRequestRef.current;
    const controller = new AbortController();

    if (isGpsChecking && !startPoint) return () => controller.abort();
    if (!activeDatasetId) {
      setMosques([]);
      return () => controller.abort();
    }

    const settingRadius = parseFloat(searchSettings.bufferKm);
    const radius = isNaN(settingRadius) ? 15 : Math.min(Math.max(settingRadius, 2), 200);
    const limit = parseInt(searchSettings.maxCandidates) || 10;
    const nearestRequestKey = `${activeDatasetId}:${radius}:${limit}`;
    if (startPoint) {
      const previousRefresh = lastNearestRefreshRef.current;
      if (
        previousRefresh
        && previousRefresh.requestKey === nearestRequestKey
        && distanceMeters(previousRefresh.point, startPoint) < NEAREST_REFRESH_DISTANCE_METERS
        && Date.now() - previousRefresh.at < NEAREST_REFRESH_INTERVAL_MS
      ) {
        return () => controller.abort();
      }
      // Do not leave Jakarta results clickable while a Bandung request is in
      // flight after the location changes.
      setMosques([]);
    } else {
      lastNearestRefreshRef.current = null;
    }
    const debounceId = window.setTimeout(async () => {
      try {
        const response = startPoint
          ? await fetchNearestMosques(
              activeDatasetId,
              startPoint.lat,
              startPoint.lng,
              radius,
              limit,
              controller.signal
            )
          : await fetchMosques(activeDatasetId, limit, 0, "", "", controller.signal);

        if (requestId === nearestRequestRef.current && !controller.signal.aborted) {
          const items = response.items || [];
          setMosques(items);
          toast.dismiss("nearest-mosque-error");
          if (startPoint) {
            lastNearestRefreshRef.current = {
              point: startPoint,
              at: Date.now(),
              requestKey: nearestRequestKey,
            };
          }

          // Start preparing the local road graph as soon as the nearest list
          // arrives, before the user opens a mosque drawer or presses Route.
          const prewarmTarget = startPoint && items.find((mosque: any) => (
            Number.isFinite(Number(mosque.latitude))
            && Number.isFinite(Number(mosque.longitude))
            && (activeDatasetId !== "all" || mosque.dataset_id)
          ));
          if (startPoint && prewarmTarget) {
            const routeDataset = activeDatasetId === "all"
              ? String(prewarmTarget.dataset_id)
              : activeDatasetId;
            void prewarmRoutingDataset(routeDataset, {
              start: startPoint,
              end: {
                lat: Number(prewarmTarget.latitude),
                lng: Number(prewarmTarget.longitude),
              },
              bufferKm: Math.min(radius, 10),
            }).catch(() => undefined);
          }
        }
      } catch (error) {
        if (isAbortError(error) || requestId !== nearestRequestRef.current) return;
        const message = error instanceof Error ? error.message : "API masjid tidak dapat dihubungi";
        console.warn(
          startPoint ? "Gagal mengambil masjid terdekat:" : "Gagal mengambil daftar masjid:",
          message
        );
        toast.error(message, { id: "nearest-mosque-error" });
        setMosques([]);
      }
    }, startPoint ? NEAREST_DEBOUNCE_MS : 0);

    return () => {
      window.clearTimeout(debounceId);
      controller.abort();
    };
  }, [activeDatasetId, startPoint, isGpsChecking, searchSettings.bufferKm, searchSettings.maxCandidates, setMosques]);

  // Fetch Prayer Times based on location
  const activeSchedule = useMemo(() => prayerSchedule || [
    { name: "Subuh", time: "04:45", isAlarmActive: true },
    { name: "Dzuhur", time: "12:02", isAlarmActive: false },
    { name: "Ashar", time: "15:24", isAlarmActive: false },
    { name: "Maghrib", time: "17:58", isAlarmActive: true },
    { name: "Isya", time: "19:12", isAlarmActive: false },
  ], [prayerSchedule]);

  // Fetch Prayer Times based on location
  useEffect(() => {
    const controller = new AbortController();
    const loadPrayerTimes = async () => {
      const lat = startPoint?.lat ?? -6.2088;
      const lng = startPoint?.lng ?? 106.8456;
      const date = buildNationalDepartureTime("now", "", lng).localDate;
      const cacheKey = `${date}_${lat.toFixed(2)}_${lng.toFixed(2)}`;
      if (prayerCacheKey === cacheKey && useAppStore.getState().prayerSchedule?.length) return;

      try {
        const data = await fetchPrayerTimes(lat, lng, date, controller.signal);
        const timings = data.timings;
        const dateObject = new Date(`${date}T12:00:00`);
        setHijriDate(`${new Intl.DateTimeFormat("id-ID-u-ca-islamic", {
          day: "numeric", month: "long", year: "numeric",
        }).format(dateObject)} H`);
        setMasehiDate(new Intl.DateTimeFormat("id-ID", {
          day: "numeric", month: "long", year: "numeric",
        }).format(dateObject));

        const latestSchedule = useAppStore.getState().prayerSchedule || [];
        setPrayerSchedule([
          { name: "Subuh", time: timings.Fajr, isAlarmActive: latestSchedule[0]?.isAlarmActive ?? true },
          { name: "Dzuhur", time: timings.Dhuhr, isAlarmActive: latestSchedule[1]?.isAlarmActive ?? false },
          { name: "Ashar", time: timings.Asr, isAlarmActive: latestSchedule[2]?.isAlarmActive ?? false },
          { name: "Maghrib", time: timings.Maghrib, isAlarmActive: latestSchedule[3]?.isAlarmActive ?? true },
          { name: "Isya", time: timings.Isha, isAlarmActive: latestSchedule[4]?.isAlarmActive ?? false },
        ]);
        setPrayerCacheKey(cacheKey);
      } catch (err) {
        if (!controller.signal.aborted) {
          console.warn("Menggunakan jadwal sholat fallback:", err);
        }
      }
    };

    loadPrayerTimes();
    return () => controller.abort();
  }, [startPoint, prayerCacheKey, setPrayerSchedule, setPrayerCacheKey, setHijriDate, setMasehiDate]);

  // Countdown clock to next prayer
  useEffect(() => {
    const interval = setInterval(() => {
      const now = new Date();
      const localClock = buildNationalDepartureTime("now", "", startPoint?.lng ?? 106.8456, now);
      const [currentHour, currentMinute] = localClock.localTime.split(":").map(Number);
      const currentMinutes = currentHour * 60 + currentMinute;

      let target: PrayerSchedule | null = null;
      let minDiff = Infinity;

      activeSchedule.forEach((p) => {
        const [h, m] = p.time.split(":").map(Number);
        const prayerMinutes = h * 60 + m;
        let diff = prayerMinutes - currentMinutes;

        if (diff <= 0) {
          diff += 24 * 60;
        }

        if (diff < minDiff) {
          minDiff = diff;
          target = p;
        }
      });

      if (target) {
        const hours = Math.floor(minDiff / 60);
        const mins = minDiff % 60;
        const secs = 60 - now.getSeconds();
        const countdownStr = `${String(hours).padStart(2, "0")}:${String(mins - 1 >= 0 ? mins - 1 : 59).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
        setNextPrayer({ name: (target as PrayerSchedule).name, countdown: countdownStr });
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [activeSchedule, startPoint?.lng]);

  // Handle manual destination route calculation (Dijkstra/A* using settings)
  const handleLocateMe = useCallback(() => {
    const requestId = ++locationRequestRef.current;
    setIsGpsChecking(false);
    setIsLocating(true);
    if (!navigator.geolocation) {
      toast.error("Browser Anda tidak mendukung Geolocation.");
      setIsLocating(false);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (requestId !== locationRequestRef.current) return;
        const point = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        const updatedAt = pos.timestamp || Date.now();
        lastAcceptedGpsRef.current = { point, at: updatedAt };
        setStartPoint(
          point,
          updatedAt,
          "gps"
        );
        toast.success("Lokasi berhasil diperbarui!");
        setIsLocating(false);
        setIsGpsTracking(true);
        setShowLocationPopup(false);
      },
      async (err) => {
        if (requestId !== locationRequestRef.current) return;
        console.warn("Mendeteksi lokasi gagal:", err);
        setIsLocating(false);
        
        if (err.code === 1) { // PERMISSION_DENIED
          // Cek apakah izin sudah granted tapi GPS OS mati
          if (typeof navigator !== "undefined" && navigator.permissions) {
            try {
              const status = await navigator.permissions.query({ name: 'geolocation' });
              if (requestId !== locationRequestRef.current) return;
              if (status.state === 'granted') {
                // Browser sudah izinkan, tapi GPS OS/Windows mati atau diblokir
                toast.error("Akses lokasi ditolak sistem. Pastikan Windows Geolocation Service (lfsvc) aktif, atau matikan Brave Shield/Adblock yang memblokir lokasi.");
                setShowLocationPopup(false);
                return;
              }
            } catch (e) {
              console.error(e);
            }
          }
          // Izin benar-benar diblokir di browser
          setShowLocationPopup(prev => {
            if (prev) toast.error("Akses lokasi masih terblokir oleh browser. Ikuti panduan di atas 🔒");
            return true;
          });
        } else if (err.code === 2 || err.code === 3) { // POSITION_UNAVAILABLE or TIMEOUT
          toast.error("Sistem tidak dapat menentukan lokasi. Pastikan Wi-Fi komputer Anda aktif (untuk deteksi lokasi PC) atau gunakan klik manual pada peta.");
          setShowLocationPopup(false);
        } else {
          toast.error("Terjadi kesalahan saat mengambil lokasi.");
        }
      },
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 8000 }
    );
  }, [setStartPoint]);

  // Listen for permission changes so we auto-fetch if they manually unblock it
  useEffect(() => {
    let cancelled = false;
    let permissionStatus: PermissionStatus | null = null;
    if (typeof navigator !== "undefined" && navigator.permissions) {
      navigator.permissions.query({ name: 'geolocation' }).then((result) => {
        if (cancelled) return;
        permissionStatus = result;
        result.onchange = () => {
          if (result.state === 'granted') {
            handleLocateMe();
          }
        };
      }).catch(console.error);
    }
    return () => {
      cancelled = true;
      if (permissionStatus) permissionStatus.onchange = null;
    };
  }, [handleLocateMe]);


  const handleRouteToMosque = async (m: any) => {
    const requestedMosqueId = String(m.id || m.mosque_id || m.name);
    if (!startPoint) {
      toast.error("Lokasi awal tidak ditemukan. Silakan izinkan akses lokasi (GPS) terlebih dahulu.");
      return;
    }
    const requestId = ++selectedRouteRequestRef.current;
    routeToMosqueAbortRef.current?.abort();
    const controller = new AbortController();
    routeToMosqueAbortRef.current = controller;
    const algoLabel = searchSettings.algorithm === "astar" ? "A*" : "Dijkstra";
    const toastId = toast.loading(`Mencari rute ${algoLabel} ke ${m.name}...`);
    try {
      // Gunakan dataset_id masjid itu sendiri jika activeDatasetId adalah "all"
      // karena graph OSM dibangun per-dataset, bukan lintas dataset
      const datasetForRoute = (activeDatasetId === "all" || !activeDatasetId)
        ? (m.dataset_id || activeDatasetId!)
        : activeDatasetId!;
      // Batasi bufferKm untuk routing (maks 50km agar tidak overload OSM graph)
      const routeBuffer = Math.min(parseFloat(searchSettings.bufferKm) || 10, 50);
      const departureContext = buildNationalDepartureTime(
        searchSettings.departureMode,
        searchSettings.currentTime,
        startPoint.lng,
      );
      const costFingerprint = `${searchSettings.fuelPricePerLiter}-${searchSettings.fuelEfficiencyKmPerLiter}-${searchSettings.operatingCostPerKm}-${searchSettings.tollCostPerKm}`;
      const temporalFingerprint = `${departureContext.cacheKey}-${searchSettings.prayer}`;
      const routeKey = buildSelectedRouteCacheKey(datasetForRoute, startPoint.lat, startPoint.lng, requestedMosqueId, searchSettings.algorithm, costFingerprint, temporalFingerprint);
      const cached = routeCache?.[routeKey];
      if (isRouteCacheFresh(cached, SELECTED_ROUTE_CACHE_TTL_MS)) {
        setRouteData(cached);
        toast.success(`Rute (${algoLabel}) dimuat dari cache.`);
        return;
      }
      const data = await routeToMosque(
        datasetForRoute,
        startPoint.lat,
        startPoint.lng,
        requestedMosqueId,
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
      if (requestId !== selectedRouteRequestRef.current) return;
      setRouteData(data);
      setRouteCache(routeKey, data);
      toast.success(`Rute (${algoLabel}) ke ${m.name} berhasil ditemukan.`);
    } catch (err: any) {
      if (isAbortError(err)) return;
      toast.error(err.message || "Gagal menghitung rute navigasi.");
    } finally {
      if (requestId === selectedRouteRequestRef.current) {
        if (routeToMosqueAbortRef.current === controller) {
          routeToMosqueAbortRef.current = null;
        }
      }
      toast.dismiss(toastId);
    }
  };

  const handleMapClick = useCallback((e: any) => {
    locationRequestRef.current += 1;
    setIsGpsChecking(false);
    setIsLocating(false);
    setStartPoint({ lat: e.latlng.lat, lng: e.latlng.lng }, Date.now(), "map");
    setSelectedMosque(null);
    setRouteData(null);
    toast.success("Titik awal perjalanan diatur ke koordinat yang diklik.");
  }, [setStartPoint, setSelectedMosque, setRouteData]);

  const validateTravelCostSettings = () => {
    const values = {
      fuelPrice: Number(localSettings.fuelPricePerLiter),
      efficiency: Number(localSettings.fuelEfficiencyKmPerLiter),
      operating: Number(localSettings.operatingCostPerKm),
      toll: Number(localSettings.tollCostPerKm),
    };
    if (
      !Object.values(values).every(Number.isFinite)
      || values.fuelPrice < 0 || values.fuelPrice > 100_000
      || values.efficiency <= 0 || values.efficiency > 100
      || values.operating < 0 || values.operating > 100_000
      || values.toll < 0 || values.toll > 100_000
    ) {
      toast.error("Parameter biaya tidak valid. Periksa harga BBM, efisiensi, operasional, dan tol.");
      return false;
    }
    return true;
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
    if (!validateTravelCostSettings()) return;

    // Simpan localSettings ke store secara otomatis saat mencari rute
    setSearchSettings(localSettings);
    const previousRecommendation = recommendationAbortRef.current;
    previousRecommendation?.abort();
    recommendationAbortRef.current = null;
    if (previousRecommendation) setLoading(false);

    const departureContext = buildNationalDepartureTime(
      localSettings.departureMode,
      localSettings.currentTime,
      startPoint.lng,
    );
    const cacheKey = `edge_v6_${activeDatasetId}_${startPoint.lat.toFixed(5)}_${startPoint.lng.toFixed(5)}_${endPoint ? `${endPoint.lat.toFixed(5)}_${endPoint.lng.toFixed(5)}` : 'none'}_${localSettings.algorithm}_${departureContext.cacheKey}_${localSettings.prayer}_${localSettings.profile}_${localSettings.maxCandidates}_${localSettings.bufferKm}_${localSettings.fuelPricePerLiter}_${localSettings.fuelEfficiencyKmPerLiter}_${localSettings.operatingCostPerKm}_${localSettings.tollCostPerKm}`;

    if (isRouteCacheFresh(routeCache?.[cacheKey], RECOMMENDATION_CACHE_TTL_MS)) {
      setRouteData(routeCache[cacheKey]);
      setShowSettings(false);
      toast.success("Rute teroptimal berhasil ditemukan (dari Cache Lokal).");
      return;
    }

    const controller = new AbortController();
    recommendationAbortRef.current = controller;
    let timedOut = false;
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
        algorithm: localSettings.algorithm,
        departure_time: departureContext.iso,
        prayer: localSettings.prayer,
        profile: localSettings.profile,
        maximum_results: parseInt(localSettings.maxCandidates),
        search_radius_km: parseFloat(localSettings.bufferKm),
        auto_build_osm: localSettings.autoBuild,
        compact_response: true,
        cost_parameters: {
          fuel_price_per_liter: Number(localSettings.fuelPricePerLiter) || 0,
          fuel_efficiency_km_per_liter: Number(localSettings.fuelEfficiencyKmPerLiter) || 12,
          operating_cost_per_km: Number(localSettings.operatingCostPerKm) || 0,
          toll_cost_per_km: Number(localSettings.tollCostPerKm) || 0,
        },
      };

      const timeoutId = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, 12000);
      const res = await fetch(`${API_BASE}/api/v1/routes/recommend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      }).finally(() => window.clearTimeout(timeoutId));

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Gagal mencari rute");
      }

      const data = await res.json();
      if (recommendationAbortRef.current !== controller) return;
      setRouteData(data);
      if (setRouteCache) {
        setRouteCache(cacheKey, data);
      }
      setShowSettings(false);
      toast.success("Rute teroptimal berhasil ditemukan.");
    } catch (err: any) {
      if (isAbortError(err) && !timedOut) return;
      if (isAbortError(err) && timedOut) {
        err = new Error("Pencarian rute melewati batas waktu. Silakan coba lagi.");
      }
      // Offline fallback: cari cache yang mencakup koordinat start dan end yang sama
      const startKey = `${startPoint.lat.toFixed(5)}_${startPoint.lng.toFixed(5)}`;
      const endKey = endPoint ? `${endPoint.lat.toFixed(5)}_${endPoint.lng.toFixed(5)}` : startKey;
      
      const cachedMatch = Object.entries(routeCache || {}).find(([key]) => {
        return !key.startsWith("selected_") && key.includes(startKey) && key.includes(endKey);
      });

      if (cachedMatch) {
        setRouteData(cachedMatch[1]);
        setShowSettings(false);
        toast.info("Koneksi internet terputus (Offline). Menampilkan rute dari cache perjalanan sebelumnya.");
      } else {
        toast.error(err.message || "Gagal mencari rute. Silakan periksa koneksi internet Anda.");
      }
    } finally {
      if (recommendationAbortRef.current === controller) {
        recommendationAbortRef.current = null;
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    return () => {
      locationRequestRef.current += 1;
      nearestRequestRef.current += 1;
      selectedRouteRequestRef.current += 1;
      routeToMosqueAbortRef.current?.abort();
      recommendationAbortRef.current?.abort();
      liveRerouteAbortRef.current?.abort();
    };
  }, []);

  // A dataset change invalidates all dependent route state.
  useEffect(() => {
    selectedRouteRequestRef.current += 1;
    routeToMosqueAbortRef.current?.abort();
    recommendationAbortRef.current?.abort();
    liveRerouteAbortRef.current?.abort();
    liveRerouteOriginRef.current = null;
    setSelectedMosque(null);
    setRouteData(null);
    setEndPoint(null);
    setMosques([]);
    lastNearestRefreshRef.current = null;
  }, [activeDatasetId]);

  // Preserve an active navigation route during normal GPS movement. A large
  // region jump clears the old-city destination, while nearby movement is
  // handled by the live reroute effect below.
  useEffect(() => {
    const previous = previousStartPointRef.current;
    previousStartPointRef.current = startPoint;
    selectedRouteRequestRef.current += 1;
    routeToMosqueAbortRef.current?.abort();
    recommendationAbortRef.current?.abort();
    setSelectedMosque(null);
    if (!startPoint) {
      liveRerouteAbortRef.current?.abort();
      liveRerouteOriginRef.current = null;
      setRouteData(null);
      setEndPoint(null);
      setMosques([]);
      return;
    }
    if (!previous) return;
    if (distanceMeters(previous, startPoint) >= REGION_JUMP_RESET_METERS) {
      liveRerouteAbortRef.current?.abort();
      liveRerouteOriginRef.current = null;
      setRouteData(null);
      setEndPoint(null);
      setMosques([]);
      lastNearestRefreshRef.current = null;
    }
  }, [startPoint?.lat, startPoint?.lng]);

  useEffect(() => {
    recommendationAbortRef.current?.abort();
  }, [endPoint?.lat, endPoint?.lng]);

  // Recompute only after meaningful movement. Both Dijkstra and A* use the
  // same warmed local graph path; the selected setting is forwarded unchanged.
  useEffect(() => {
    const mosque = routeData?.recommended_mosque;
    if (!mosque) {
      liveRerouteAbortRef.current?.abort();
      liveRerouteAbortRef.current = null;
      liveRerouteOriginRef.current = null;
      return;
    }
    if (!startPoint || startPointSource !== "gps") return;
    const mosqueId = String(mosque.id || mosque.mosque_id || "");
    const routeDataset = String(
      mosque.dataset_id
      || routeData?.dataset_id
      || (activeDatasetId !== "all" ? activeDatasetId : "")
    );
    if (!mosqueId || !routeDataset || routeDataset === "all") return;

    const now = Date.now();
    const previousReroute = liveRerouteOriginRef.current;
    if (!previousReroute) {
      liveRerouteOriginRef.current = { point: startPoint, at: now };
      return;
    }
    const movedMeters = distanceMeters(previousReroute.point, startPoint);
    if (
      movedMeters < LIVE_REROUTE_DISTANCE_METERS
      || now - previousReroute.at < LIVE_REROUTE_INTERVAL_MS
      || movedMeters >= REGION_JUMP_RESET_METERS
    ) {
      return;
    }

    liveRerouteOriginRef.current = { point: startPoint, at: now };
    liveRerouteAbortRef.current?.abort();
    const controller = new AbortController();
    liveRerouteAbortRef.current = controller;
    const bufferKm = Math.min(parseFloat(searchSettings.bufferKm) || 10, 50);
    const destination = {
      lat: Number(mosque.latitude),
      lng: Number(mosque.longitude),
    };
    void prewarmRoutingDataset(routeDataset, {
      start: startPoint,
      end: destination,
      bufferKm: Math.min(bufferKm, 10),
    }).catch(() => undefined);

    void routeToMosque(
      routeDataset,
      startPoint.lat,
      startPoint.lng,
      mosqueId,
      searchSettings.algorithm,
      bufferKm,
      false,
      controller.signal,
      {
        fuel_price_per_liter: Number(searchSettings.fuelPricePerLiter),
        fuel_efficiency_km_per_liter: Number(searchSettings.fuelEfficiencyKmPerLiter),
        operating_cost_per_km: Number(searchSettings.operatingCostPerKm),
        toll_cost_per_km: Number(searchSettings.tollCostPerKm),
      },
      buildNationalDepartureTime(
        searchSettings.departureMode,
        searchSettings.currentTime,
        startPoint.lng,
      ).iso,
      searchSettings.prayer
    ).then((data) => {
      if (liveRerouteAbortRef.current !== controller) return;
      const latestPoint = useAppStore.getState().startPoint;
      if (!latestPoint || distanceMeters(latestPoint, startPoint) >= LIVE_REROUTE_DISTANCE_METERS) return;
      const liveData = { ...data, live_reroute: true };
      const liveDeparture = buildNationalDepartureTime(
        searchSettings.departureMode,
        searchSettings.currentTime,
        startPoint.lng,
      );
      const routeKey = buildSelectedRouteCacheKey(
        routeDataset,
        startPoint.lat,
        startPoint.lng,
        mosqueId,
        searchSettings.algorithm,
        `${searchSettings.fuelPricePerLiter}-${searchSettings.fuelEfficiencyKmPerLiter}-${searchSettings.operatingCostPerKm}-${searchSettings.tollCostPerKm}`,
        `${liveDeparture.cacheKey}-${searchSettings.prayer}`,
      );
      setRouteData(liveData);
      setRouteCache(routeKey, liveData);
    }).catch((error) => {
      if (!isAbortError(error)) console.warn("Live reroute failed:", error);
    }).finally(() => {
      if (liveRerouteAbortRef.current === controller) {
        liveRerouteAbortRef.current = null;
      }
    });
  }, [
    activeDatasetId,
    routeData?.dataset_id,
    routeData?.recommended_mosque?.id,
    routeData?.recommended_mosque?.mosque_id,
    searchSettings.algorithm,
    searchSettings.bufferKm,
    searchSettings.departureMode,
    searchSettings.currentTime,
    searchSettings.fuelPricePerLiter,
    searchSettings.fuelEfficiencyKmPerLiter,
    searchSettings.operatingCostPerKm,
    searchSettings.prayer,
    searchSettings.tollCostPerKm,
    startPoint?.lat,
    startPoint?.lng,
    startPointSource,
    setRouteCache,
    setRouteData,
  ]);

  const handleSaveSettings = async () => {
    const bufferKm = Number(localSettings.bufferKm);
    const maxCandidates = Number(localSettings.maxCandidates);
    if (!Number.isFinite(bufferKm) || bufferKm < 2 || bufferKm > 200) {
      toast.error("Radius buffer harus antara 2 dan 200 km.");
      return;
    }
    if (!Number.isInteger(maxCandidates) || maxCandidates < 1 || maxCandidates > 10) {
      toast.error("Jumlah rekomendasi harus antara 1 dan 10.");
      return;
    }
    if (!validateTravelCostSettings()) return;
    setSavingSettings(true);
    setSearchSettings(localSettings);
    const saved = await saveSettingsToDatabase({
      searchSettings: localSettings,
      prayerSettings: { schedule: activeSchedule, hijriDate, masehiDate },
    });
    setSavingSettings(false);
    if (saved) {
      toast.success("Pengaturan pencarian tersimpan di database.");
    } else {
      toast.error("Setelan tersimpan lokal, tetapi gagal disimpan ke database.");
    }
  };

  const toggleAlarm = async (index: number) => {
    const updated = activeSchedule.map((p, idx) => 
      idx === index ? { ...p, isAlarmActive: !p.isAlarmActive } : p
    );
    setSavingAlarmIndex(index);
    setPrayerSchedule(updated);
    const saved = await saveSettingsToDatabase({
      searchSettings,
      prayerSettings: { schedule: updated, hijriDate, masehiDate },
    });
    setSavingAlarmIndex(null);

    const p = updated[index];
    if (!saved) {
      toast.error("Alarm " + p.name + " berubah lokal, tetapi gagal disimpan ke database.");
    } else if (p.isAlarmActive) {
      toast.success(`Alarm sholat ${p.name} diaktifkan pada pukul ${p.time}`);
    } else {
      toast.info(`Alarm sholat ${p.name} dinonaktifkan`);
    }
  };

  // Autocomplete suggestions based on search query
  const filteredMosques = useMemo(() => {
    if (searchQuery.trim() === "") return [];
    const q = searchQuery.toLowerCase();
    return mosques.filter(m => 
      m.name?.toLowerCase().includes(q) ||
      (m.kecamatan && m.kecamatan.toLowerCase().includes(q))
    ).slice(0, 6);
  }, [mosques, searchQuery]);

  // Filtering for map markers
  const filteredMarkers = useMemo(() => {
    const markersList: any[] = [];
    const seenIds = new Set<string>();

    if (startPoint) {
      markersList.push({ id: "start-point", lat: startPoint.lat, lng: startPoint.lng, type: "start", popup: "Titik Awal Anda" });
      seenIds.add("start-point");
    }
    if (endPoint) {
      markersList.push({ id: "end-point", lat: endPoint.lat, lng: endPoint.lng, type: "destination", popup: "Titik Tujuan" });
      seenIds.add("end-point");
    }

    // Saat rute aktif, sembunyikan seluruh masjid lain agar jalur dan tujuan
    // tetap terbaca jelas. Marker biasa kembali otomatis saat routeData ditutup.
    if (!routeData) {
      mosques.forEach(m => {
        if (m.latitude && m.longitude) {
          // Quick Filters check
          if (filterRating) {
            const r = parseFloat(String(m.rating || 0).replace(",", "."));
            if (r < 4.8) return;
          }
          if (filterTierA && m.tier !== "A") return;

          let rawFacs: string[] = [];
          if (m.facilities) {
            if (Array.isArray(m.facilities)) {
              rawFacs = m.facilities.map((f: any) => String(f).toLowerCase());
            } else if (typeof m.facilities === "string") {
              rawFacs = m.facilities.split(/[|,;]+/).map((f: string) => f.trim().toLowerCase()).filter(Boolean);
            }
          }
          if (filterAC && !rawFacs.some(f => f.includes("ac"))) return;
          if (filterParking && !rawFacs.some(f => f.includes("parking"))) return;
          if (filterWudu && !rawFacs.some(f => f.includes("wudu"))) return;
          if (filterToilet && !rawFacs.some(f => f.includes("toilet"))) return;

          // Search Bar filter
          if (searchQuery.trim() !== "") {
            const q = searchQuery.toLowerCase();
            const matchesName = m.name?.toLowerCase().includes(q);
            const matchesAddress = m.address?.toLowerCase().includes(q);
            if (!matchesName && !matchesAddress) return;
          }

          const mosqueId = m.id || m.mosque_id || m.name;
          if (!seenIds.has(mosqueId)) {
            markersList.push({
              id: mosqueId,
              lat: m.latitude,
              lng: m.longitude,
              type: "mosque",
              tier: m.tier,
              rating: m.rating,
              facilities: m.facilities,
              onClick: () => setSelectedMosque(m)
            });
            seenIds.add(mosqueId);
          }
        }
      });
    }

    // Highlight recommended mosque
    if (routeData?.recommended_mosque) {
      const rm = routeData.recommended_mosque;
      const rmId = rm.id || rm.mosque_id || rm.name || "recommended";
      if (!seenIds.has(rmId)) {
        markersList.push({
          id: rmId,
          lat: rm.latitude,
          lng: rm.longitude,
          type: "recommended",
          tier: rm.tier,
          rating: rm.rating,
          facilities: rm.facilities,
          onClick: () => setSelectedMosque(rm)
        });
        seenIds.add(rmId);
      }
    }

    return markersList;
  }, [
    startPoint, endPoint, mosques, routeData, searchQuery,
    filterRating, filterTierA, filterAC, filterParking, filterWudu, filterToilet
  ]);

  // Road geometry and off-road access links are intentionally separate.
  const routeSegments = useMemo(() => {
    const geoJson = routeData?.route_geojson || null;
    if (geoJson?.geometry?.type === "MultiLineString" && geoJson.geometry.coordinates) {
      return geoJson.geometry.coordinates.map((segment: [number, number][]) =>
        segment.map((c: [number, number]) => [c[1], c[0]] as [number, number])
      ) as [number, number][][];
    }
    if (geoJson?.geometry?.type === "LineString" && geoJson.geometry.coordinates) {
      return [geoJson.geometry.coordinates.map((c: [number, number]) => [c[1], c[0]] as [number, number])];
    }
    if (Array.isArray(routeData?.encoded_polylines) && routeData.encoded_polylines.length > 0) {
      return routeData.encoded_polylines.map((encoded: string) => decodePolyline(encoded));
    }
    return routeData?.encoded_polyline ? [decodePolyline(routeData.encoded_polyline)] : [];
  }, [routeData]);
  const routePoints = useMemo(() => routeSegments.flat(), [routeSegments]);
  const accessConnectors = useMemo(() => {
    if (!Array.isArray(routeData?.access_connectors)) return [];
    return routeData.access_connectors
      .filter((segment: unknown) => Array.isArray(segment) && segment.length >= 2)
      .map((segment: [number, number][]) => segment.map(
        (coordinate: [number, number]) => [Number(coordinate[0]), Number(coordinate[1])] as [number, number]
      ));
  }, [routeData]);

  // Memoize map center to prevent MapViewer re-rendering on every Dashboard re-render (like clock tick)
  const mapCenter = useMemo(() => startPoint ? [startPoint.lat, startPoint.lng] as [number, number] : undefined, [startPoint?.lat, startPoint?.lng]);
  const mapSearchRadiusKm = useMemo(() => {
    const parsed = Number(searchSettings.bufferKm);
    return Number.isFinite(parsed) ? Math.min(Math.max(parsed, 2), 200) : 10;
  }, [searchSettings.bufferKm]);
  const settingsDeparturePreview = buildNationalDepartureTime(
    localSettings.departureMode,
    localSettings.currentTime,
    startPoint?.lng ?? 106.8456,
  );

  return (
    <main suppressHydrationWarning className="relative h-screen w-screen overflow-hidden bg-slate-50">
      {/* Map (Background) */}
      <div suppressHydrationWarning className="absolute inset-0 z-0">
        <MapViewer 
          onMapClick={handleMapClick}
          markers={filteredMarkers}
          route={routePoints}
          routeSegments={routeSegments}
          accessConnectors={accessConnectors}
          center={mapCenter}
          searchRadiusKm={mapSearchRadiusKm}
          routingMode={routeData?.routing_mode}
          isRouteExpanded={isRouteExpanded}
          selectedMosque={selectedMosque}
        />
      </div>

      {/* Floating Google Maps Style Search & Navigation Panel (Top-Left) */}
      <div className="absolute left-4 top-4 z-10 w-[calc(100vw-32px)] md:w-[440px] max-w-[400px] md:max-w-none flex flex-col gap-3 pointer-events-none md:max-h-[calc(100vh-2rem)]">
        
        {/* On desktop, show RouteResultPanel here if route is active, otherwise show Search Bar */}
        {routeData ? (
          <div className="hidden md:flex flex-col pointer-events-auto w-full max-h-[calc(100vh-2rem)] overflow-y-auto custom-scrollbar rounded-2xl">
            <RouteResultPanel isExpanded={isRouteExpanded} setIsExpanded={setIsRouteExpanded} />
          </div>
        ) : null}

        {/* Floating Search Bar Card Container */}
        <div className={`relative pointer-events-auto w-full transition-all duration-300 ${
          routeData ? "md:hidden" : "block"
        }`}>
          <div className="bg-white/90 dark:bg-slate-900/90 backdrop-blur-xl border border-slate-200/30 dark:border-slate-800/50 shadow-2xl p-2 rounded-2xl flex items-center gap-2 transition-all duration-300">
            <div className="p-2 rounded-xl bg-emerald-50 dark:bg-emerald-950/50 text-emerald-600 dark:text-emerald-400">
              <Compass className="w-5 h-5 animate-pulse" />
            </div>
            
            <div className="flex-1">
              <input
                type="text"
                placeholder="Cari masjid di perjalanan..."
                value={searchQuery}
                onFocus={() => setIsFocused(true)}
                onBlur={() => setTimeout(() => setIsFocused(false), 250)}
                onChange={e => setSearchQuery(e.target.value)}
                className="w-full bg-transparent border-none outline-none text-xs font-bold text-slate-800 dark:text-slate-100 placeholder-slate-400 dark:placeholder-slate-500 h-9"
              />
            </div>

            {searchQuery && (
              <button 
                onClick={() => { setSearchQuery(""); setEndPoint(null); setRouteData(null); }}
                className="p-2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-350 transition-colors cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            )}

            <div className="h-6 w-[1px] bg-slate-200 dark:bg-slate-800"></div>

            {/* Action buttons (Theme, Prayer & Settings) */}
            <div className="flex gap-0.5">
              <button
                onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
                className="p-2 rounded-xl transition-all cursor-pointer text-slate-400 hover:text-emerald-600 dark:hover:text-emerald-400"
                title={resolvedTheme === "dark" ? "Mode Terang" : "Mode Gelap"}
              >
                {resolvedTheme === "dark" ? <Sun className="w-4.5 h-4.5" /> : <Moon className="w-4.5 h-4.5" />}
              </button>
              <button
                onClick={() => { setShowPrayer(!showPrayer); setShowSettings(false); }}
                className={`p-2 rounded-xl transition-all cursor-pointer ${
                  showPrayer 
                    ? "bg-emerald-500 text-white shadow-md shadow-emerald-500/20" 
                    : "text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                }`}
                title="Jadwal Sholat"
              >
                <Clock className="w-4.5 h-4.5" />
              </button>
              <button
                onClick={() => { setShowSettings(!showSettings); setShowPrayer(false); }}
                className={`p-2 rounded-xl transition-all cursor-pointer ${
                  showSettings 
                    ? "bg-emerald-500 text-white shadow-md shadow-emerald-500/20" 
                    : "text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                }`}
                title="Pengaturan Pencarian"
              >
                <Settings className="w-4.5 h-4.5" />
              </button>
              <button
                onClick={() => router.push('/admin')}
                className="p-2 rounded-xl transition-all cursor-pointer text-slate-400 hover:text-indigo-500 dark:hover:text-indigo-400"
                title="Halaman Admin"
              >
                <Shield className="w-4.5 h-4.5" />
              </button>
            </div>
          </div>

          {/* Autocomplete Dropdown - aligned to the whole card! */}
          {isFocused && filteredMosques.length > 0 && (
            <div className="absolute left-0 right-0 top-full mt-2 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-2xl overflow-hidden divide-y divide-slate-50 dark:divide-slate-800 z-50 animate-in fade-in slide-in-from-top-2 duration-200">
              {filteredMosques.map((m, idx) => {
                const rating = m.rating ? parseFloat(String(m.rating).replace(",", ".")) : 0;
                return (
                  <div 
                    key={`${m.id || 'mosque'}-${idx}`}
                    className="px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/50 cursor-pointer text-xs transition-all flex items-center justify-between gap-3"
                    onMouseDown={() => {
                      setEndPoint({ lat: m.latitude, lng: m.longitude });
                      setSearchQuery(m.name);
                      handleRouteToMosque(m);
                    }}
                  >
                    <div className="flex items-start gap-2.5 min-w-0">
                      <MapPin className="w-4.5 h-4.5 text-emerald-600 dark:text-emerald-400 shrink-0 mt-0.5" />
                      <div className="flex flex-col min-w-0 gap-0.5">
                        <span className="font-extrabold text-slate-800 dark:text-slate-200 truncate">{m.name}</span>
                        <span className="text-[10px] text-slate-400 dark:text-slate-500 font-semibold truncate">
                          {m.kecamatan || "-"}, {m.kabko?.replace("KOTA ADMINISTRASI ", "") || "-"}
                        </span>
                      </div>
                    </div>
                    
                    <div className="flex items-center gap-1.5 shrink-0">
                        <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                          <MapPin className="w-2.5 h-2.5" /> {formatDistance(m.distance_km)}
                        </span>
                      {rating > 0 ? (
                        <span className="text-[10px] font-black text-amber-600 bg-amber-50 dark:bg-amber-950/30 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                          <Star className="w-2.5 h-2.5 fill-amber-500" /> {rating.toFixed(1)}
                        </span>
                      ) : (
                        <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded">
                          T{m.tier || "D"}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Floating Quick Filters Chips (Horizontal Scroll) */}
        <div className={`flex items-center gap-1.5 overflow-x-auto pb-1 pointer-events-auto max-w-full custom-scrollbar transition-all duration-300 ${
          routeData ? "md:hidden" : "flex"
        }`}>
          {[
            { label: <span className="flex items-center gap-1"><Star className="w-3 h-3 fill-amber-500 text-amber-500" /> 4.8+</span>, state: filterRating, setState: setFilterRating },
            { label: "Tier A", state: filterTierA, setState: setFilterTierA },
            { label: "AC", state: filterAC, setState: setFilterAC },
            { label: "Parkir", state: filterParking, setState: setFilterParking },
            { label: "Wudhu", state: filterWudu, setState: setFilterWudu },
            { label: "Toilet", state: filterToilet, setState: setFilterToilet },
          ].map((chip, idx) => (
            <button
              key={idx}
              onClick={() => chip.setState(!chip.state)}
              className={`px-3 py-1.5 rounded-xl border text-[10px] font-extrabold whitespace-nowrap cursor-pointer transition-all duration-300 ${
                chip.state 
                  ? "bg-emerald-600 border-emerald-600 text-white shadow-md shadow-emerald-600/20" 
                  : "bg-white/90 dark:bg-slate-900/90 text-slate-600 dark:text-slate-350 border-slate-200/30 dark:border-slate-800/50 backdrop-blur-md hover:bg-white dark:hover:bg-slate-900"
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>

        {/* Floating Search Settings Popover Card */}
        {showSettings && !routeData && (
          <Card className="bg-white/90 dark:bg-slate-900/90 backdrop-blur-xl border border-slate-200/30 dark:border-slate-800/50 shadow-2xl rounded-2xl pointer-events-auto overflow-hidden animate-in slide-in-from-top duration-300 w-full max-w-[380px] max-h-[80vh] md:max-h-[85vh] flex flex-col">
            <CardHeader className="p-4 pb-3 bg-gradient-to-r from-slate-50 to-slate-100/50 dark:from-slate-800/50 dark:to-slate-850/50 border-b border-slate-100 dark:border-slate-800 flex flex-row items-center justify-between">
              <div>
                <span className="text-[9px] uppercase font-black text-slate-400 dark:text-slate-500 tracking-widest block">Pengaturan</span>
                <CardTitle className="text-sm font-extrabold text-slate-800 dark:text-slate-100 mt-0.5">Konfigurasi Rute</CardTitle>
              </div>
              <button 
                onClick={() => setShowSettings(false)}
                className="p-1.5 rounded-xl text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-200/50 dark:hover:bg-slate-800/50 transition-all cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </CardHeader>
            <CardContent className="p-4 flex-1 overflow-y-auto flex flex-col gap-3.5 custom-scrollbar">
              <div className="grid grid-cols-2 gap-3.5">
                <div className="space-y-1.5">
                  <Label className="text-[9px] font-black text-slate-500 uppercase tracking-wider block">Algoritma</Label>
                  <Select 
                    value={localSettings.algorithm} 
                    onValueChange={(val) => setLocalSettings(prev => ({ ...prev, algorithm: val as string }))}
                  >
                    <SelectTrigger className="w-full h-9.5 bg-slate-50/50 border-slate-200/70 text-xs font-bold text-slate-700 rounded-xl hover:bg-slate-50 transition-all">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="rounded-xl border-slate-200">
                      <SelectItem value="dijkstra">Dijkstra</SelectItem>
                      <SelectItem value="astar">A* (Heuristik)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[9px] font-black text-slate-500 uppercase tracking-wider block">Profil Rute</Label>
                  <Select 
                    value={localSettings.profile} 
                    onValueChange={(val) => setLocalSettings(prev => ({ ...prev, profile: val as string }))}
                  >
                    <SelectTrigger className="w-full h-9.5 bg-slate-50/50 border-slate-200/70 text-xs font-bold text-slate-700 rounded-xl hover:bg-slate-50 transition-all">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="rounded-xl border-slate-200">
                      <SelectItem value="balanced">Seimbang</SelectItem>
                      <SelectItem value="fastest">Cepat</SelectItem>
                      <SelectItem value="prayer_priority">Prioritas Salat</SelectItem>
                      <SelectItem value="low_cost">Biaya Rendah</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="rounded-xl border border-emerald-100 bg-emerald-50/40 p-2.5">
                <span className="text-[10px] font-black uppercase tracking-wider text-emerald-700 block">
                  Asumsi Biaya Rupiah {localSettings.profile === "low_cost" ? "(aktif)" : ""}
                </span>
                <p className="mt-1 text-[9px] text-slate-500">
                  Dipakai untuk menghitung BBM, operasional kendaraan, dan tol.
                </p>
                <div className="mt-2.5 grid grid-cols-2 gap-2.5">
                  <div className="space-y-1">
                    <Label className="text-[9px] font-bold text-slate-500">Harga BBM / liter</Label>
                    <Input
                      type="number"
                      min="0"
                      max="100000"
                      value={localSettings.fuelPricePerLiter}
                      onChange={event => setLocalSettings(previous => ({ ...previous, fuelPricePerLiter: event.target.value }))}
                      className="h-8 rounded-lg bg-white text-[10px]"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[9px] font-bold text-slate-500">Efisiensi km / liter</Label>
                    <Input
                      type="number"
                      min="0.1"
                      max="100"
                      step="0.1"
                      value={localSettings.fuelEfficiencyKmPerLiter}
                      onChange={event => setLocalSettings(previous => ({ ...previous, fuelEfficiencyKmPerLiter: event.target.value }))}
                      className="h-8 rounded-lg bg-white text-[10px]"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[9px] font-bold text-slate-500">Operasional / km</Label>
                    <Input
                      type="number"
                      min="0"
                      max="100000"
                      value={localSettings.operatingCostPerKm}
                      onChange={event => setLocalSettings(previous => ({ ...previous, operatingCostPerKm: event.target.value }))}
                      className="h-8 rounded-lg bg-white text-[10px]"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[9px] font-bold text-slate-500">Estimasi tol / km</Label>
                    <Input
                      type="number"
                      min="0"
                      max="100000"
                      value={localSettings.tollCostPerKm}
                      onChange={event => setLocalSettings(previous => ({ ...previous, tollCostPerKm: event.target.value }))}
                      className="h-8 rounded-lg bg-white text-[10px]"
                    />
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3.5">
                <div className="space-y-1.5">
                  <Label className="text-[9px] font-black text-slate-500 dark:text-slate-400 uppercase tracking-wider block">Waktu Berangkat</Label>
                  <Select
                    value={localSettings.departureMode}
                    onValueChange={(value) => setLocalSettings(previous => ({
                      ...previous,
                      departureMode: value === "scheduled" ? "scheduled" : "now",
                    }))}
                  >
                    <SelectTrigger className="w-full h-9.5 bg-slate-50/50 dark:bg-slate-800/50 border-slate-200/70 dark:border-slate-700/50 text-xs font-bold text-slate-700 dark:text-slate-200 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-800 transition-all">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="rounded-xl border-slate-200 dark:border-slate-800 dark:bg-slate-900">
                      <SelectItem value="now">Sekarang (realtime)</SelectItem>
                      <SelectItem value="scheduled">Jadwalkan manual</SelectItem>
                    </SelectContent>
                  </Select>
                  {localSettings.departureMode === "scheduled" ? (
                    <Input
                      type="time"
                      aria-label="Jam keberangkatan terjadwal"
                      value={localSettings.currentTime}
                      onChange={event => setLocalSettings(previous => ({ ...previous, currentTime: event.target.value }))}
                      className="h-8 bg-white dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-[10px] font-bold rounded-lg dark:text-slate-100"
                    />
                  ) : (
                    <p className="px-1 text-[9px] font-semibold text-emerald-700 dark:text-emerald-400">
                      {settingsDeparturePreview.localTime} {settingsDeparturePreview.abbreviation} · mengikuti lokasi
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[9px] font-black text-slate-500 dark:text-slate-400 uppercase tracking-wider block">Target Salat</Label>
                  <Select 
                    value={localSettings.prayer} 
                    onValueChange={(val) => setLocalSettings(prev => ({ ...prev, prayer: val as string }))}
                  >
                    <SelectTrigger className="w-full h-9.5 bg-slate-50/50 dark:bg-slate-800/50 border-slate-200/70 dark:border-slate-700/50 text-xs font-bold text-slate-700 dark:text-slate-200 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-800 transition-all">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="rounded-xl border-slate-200 dark:border-slate-800 dark:bg-slate-900">
                      <SelectItem value="auto">{prayerTargetLabel("auto", nextPrayer.name)}</SelectItem>
                      <SelectItem value="subuh">Subuh</SelectItem>
                      <SelectItem value="dzuhur">Dzuhur</SelectItem>
                      <SelectItem value="ashar">Ashar</SelectItem>
                      <SelectItem value="maghrib">Maghrib</SelectItem>
                      <SelectItem value="isya">Isya</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3.5">
                <div className="space-y-1.5">
                  <Label className="text-[9px] font-black text-slate-500 dark:text-slate-400 uppercase tracking-wider block">Radius Buffer</Label>
                  <div className="relative flex items-center">
                    <Input
                      type="number"
                      min="2"
                      max="200"
                      value={localSettings.bufferKm}
                      onChange={e => setLocalSettings(prev => ({ ...prev, bufferKm: e.target.value }))}
                      className="h-9.5 bg-slate-50/50 dark:bg-slate-800/50 border-slate-200/70 dark:border-slate-700/50 text-xs font-bold text-slate-700 dark:text-slate-200 rounded-xl pl-2.5 pr-8 hover:bg-slate-50 dark:hover:bg-slate-800 transition-all [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                    />
                    <span className="absolute right-3 text-[10px] font-black text-slate-400 dark:text-slate-500">KM</span>
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[9px] font-black text-slate-500 dark:text-slate-400 uppercase tracking-wider block">Rekomendasi</Label>
                  <div className="relative flex items-center">
                    <Input
                      type="number"
                      min="1"
                      max="10"
                      value={localSettings.maxCandidates}
                      onChange={e => setLocalSettings(prev => ({ ...prev, maxCandidates: e.target.value }))}
                      className="h-9.5 bg-slate-50/50 dark:bg-slate-800/50 border-slate-200/70 dark:border-slate-700/50 text-xs font-bold text-slate-700 dark:text-slate-200 rounded-xl pl-2.5 pr-10 hover:bg-slate-50 dark:hover:bg-slate-800 transition-all [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                    />
                    <span className="absolute right-3 text-[9px] font-extrabold text-slate-400 dark:text-slate-500">Masjid</span>
                  </div>
                </div>
              </div>

              <div className="bg-slate-50/60 dark:bg-slate-800/40 border border-slate-100 dark:border-slate-800/60 p-2.5 rounded-xl flex items-center gap-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/80 transition-colors cursor-pointer" onClick={() => setLocalSettings(prev => ({ ...prev, autoBuild: !prev.autoBuild }))}>
                <Checkbox 
                  id="autoBuild" 
                  checked={localSettings.autoBuild} 
                  onCheckedChange={(c) => setLocalSettings(prev => ({ ...prev, autoBuild: !!c }))}
                  className="rounded border-slate-300 dark:border-slate-700 text-emerald-600 focus:ring-emerald-500 cursor-pointer"
                />
                <label htmlFor="autoBuild" className="text-[10px] font-bold text-slate-600 dark:text-slate-300 leading-tight cursor-pointer select-none">
                  Otomatis bangun peta jika tidak tersedia
                </label>
              </div>

              <div className="flex gap-2.5 pt-3 border-t border-slate-100 dark:border-slate-800/60 shrink-0">
                <Button 
                  onClick={handleSaveSettings}
                  disabled={savingSettings || settingsSyncStatus === "loading"}
                  className="flex-1 h-10 bg-slate-900 dark:bg-slate-800 hover:bg-slate-800 dark:hover:bg-slate-700 text-white font-bold text-xs rounded-xl flex items-center justify-center gap-1.5 transition-all shadow-md shadow-slate-900/10 cursor-pointer border dark:border-slate-700"
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  {savingSettings ? "Menyimpan..." : "Simpan Setelan"}
                </Button>
                <Button 
                  onClick={handleRouteSearch}
                  disabled={loading}

                  className="flex-1 h-10 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs rounded-xl flex items-center justify-center gap-1.5 transition-all shadow-md shadow-emerald-600/15 cursor-pointer"
                >
                  <Navigation className="w-3.5 h-3.5" />
                  {loading ? "Mencari..." : "Cari Rute"}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Floating Prayer Times Popover Card */}
        {showPrayer && !routeData && (
          <Card className="bg-white/90 dark:bg-slate-900/90 backdrop-blur-xl border border-slate-200/30 dark:border-slate-800/50 shadow-2xl rounded-2xl pointer-events-auto overflow-hidden animate-in slide-in-from-top duration-300 w-full max-w-[380px]">
            <CardHeader className="p-4 pb-2 bg-gradient-to-r from-emerald-800 via-emerald-950 to-teal-900 dark:from-emerald-900 dark:via-emerald-950 dark:to-teal-950 text-white flex flex-row items-start justify-between">
              <div>
                <span className="text-[9px] uppercase font-black text-emerald-300 dark:text-emerald-400 tracking-wider block">Jadwal Adzan & Alarm</span>
                <h3 className="text-sm font-black mt-0.5">{hijriDate}</h3>
                <span className="text-[10px] text-emerald-100/70 dark:text-emerald-300/70">{masehiDate}</span>
              </div>
              <button 
                onClick={() => setShowPrayer(false)}
                className="p-1 rounded-lg text-emerald-200 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </CardHeader>
            <CardContent className="p-0">
              {/* Countdown */}
              <div className="bg-emerald-500/10 px-4 py-3 flex items-center justify-between border-b border-slate-100 dark:border-slate-800">
                <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400">Salat Berikutnya: <strong className="text-slate-800 dark:text-slate-200 font-extrabold">{nextPrayer.name}</strong></span>
                <span className="text-sm font-mono font-black text-emerald-600 dark:text-emerald-400 tracking-wider animate-pulse">{nextPrayer.countdown}</span>
              </div>
              
              {/* Timings list */}
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {activeSchedule.map((p, idx) => (
                  <div key={p.name} className="flex items-center justify-between px-4 py-3 hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition-colors">
                    <div className="flex items-center gap-2">
                      <div className={`w-2 h-2 rounded-full ${p.name === nextPrayer.name ? "bg-emerald-500 animate-ping" : "bg-slate-200 dark:bg-slate-700"}`}></div>
                      <span className="text-xs font-bold text-slate-700 dark:text-slate-350">{p.name}</span>
                    </div>
                    <div className="flex items-center gap-4">
                      <span className="font-mono text-xs font-black text-slate-800 dark:text-slate-200">{p.time}</span>
                      <button
                        onClick={() => toggleAlarm(idx)}
                        disabled={savingAlarmIndex !== null || settingsSyncStatus === "loading"}
                        aria-label={(p.isAlarmActive ? "Nonaktifkan" : "Aktifkan") + " alarm " + p.name}
                        className={`p-1.5 rounded-lg border transition-all ${
                          p.isAlarmActive
                            ? "bg-emerald-50 dark:bg-emerald-950/30 text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800/80"
                            : "bg-white dark:bg-slate-800 text-slate-400 dark:text-slate-500 border-slate-200 dark:border-slate-700"
                        }`}
                      >
                        {p.isAlarmActive ? <Bell className="w-3.5 h-3.5 fill-emerald-600/10" /> : <BellOff className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Floating Route Result Panel (Bottom on Mobile only) */}
      {routeData && (
        <div suppressHydrationWarning className="absolute md:hidden left-0 right-0 bottom-0 h-auto max-h-[75vh] z-10 pointer-events-auto transition-all duration-300">
          <RouteResultPanel isExpanded={isRouteExpanded} setIsExpanded={setIsRouteExpanded} />
        </div>
      )}

      {/* Floating My Location Button */}
      <div className={`absolute z-[1000] pointer-events-auto flex items-center gap-2 transition-all duration-300 ${
        selectedMosque ? "hidden md:flex md:bottom-20 md:right-4" : ""
      } ${
        routeData
          ? isRouteExpanded
            ? "hidden md:flex md:bottom-20 md:right-4"
            : "bottom-[140px] right-4 md:bottom-20 md:right-4"
          : "bottom-[90px] right-4 md:bottom-20 md:right-4"
      }`}>
        {isGpsTracking && startPointSource === "gps" && (
          <span className="hidden sm:flex items-center gap-1.5 rounded-full border border-emerald-200 dark:border-emerald-800/80 bg-white/95 dark:bg-slate-900/95 px-2.5 py-1.5 text-[10px] font-black uppercase tracking-wide text-emerald-700 dark:text-emerald-400 shadow-lg backdrop-blur-md">
            <span className="h-2 w-2 rounded-full bg-emerald-500 dark:bg-emerald-450 animate-pulse" />
            GPS realtime
          </span>
        )}
        <button
          onClick={handleLocateMe}
          disabled={isLocating}
          aria-label="Lokasi Saya"
          className="bg-white/95 dark:bg-slate-900/95 backdrop-blur-md text-slate-700 dark:text-slate-350 hover:bg-emerald-50 dark:hover:bg-emerald-950/40 hover:text-emerald-600 dark:hover:text-emerald-400 border border-slate-200/50 dark:border-slate-800/50 w-[42px] h-[42px] rounded-2xl shadow-xl flex items-center justify-center transition-all disabled:opacity-50"
          title="Lokasi Saya"
        >
          {isLocating ? <div className="w-5 h-5 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin"></div> : <Locate className="w-[22px] h-[22px]" />}
        </button>
      </div>

      <Dialog open={showLocationPopup} onOpenChange={setShowLocationPopup}>
        <DialogContent className="sm:max-w-[425px] border-none shadow-2xl bg-white/95 backdrop-blur-xl z-[10000]">
          <DialogHeader>
            <div className="mx-auto w-16 h-16 bg-rose-100 text-rose-600 rounded-full flex items-center justify-center mb-4 ring-8 ring-rose-50">
              <MapPin className="w-8 h-8 animate-bounce" />
            </div>
            <DialogTitle className="text-center text-2xl font-extrabold text-slate-800">
              Yah, Akses Lokasi Mati 😔
            </DialogTitle>
            <DialogDescription className="text-center pt-2 text-slate-600 font-medium">
              Aplikasi butuh lokasi Anda untuk mencari masjid terdekat. Ikuti 3 langkah mudah ini untuk menyalakannya:
            </DialogDescription>
          </DialogHeader>

          <div className="bg-slate-50 rounded-xl p-4 mt-2 space-y-4 border border-slate-100">
            <div className="flex gap-3 items-start">
              <div className="w-7 h-7 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center flex-shrink-0 font-bold text-sm">1</div>
              <p className="text-sm text-slate-700 leading-snug">
                Klik ikon <strong>gembok 🔒</strong> atau <strong>info ⓘ</strong> di pojok kiri atas (sebelah alamat web iMosque).
              </p>
            </div>
            <div className="flex gap-3 items-start">
              <div className="w-7 h-7 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center flex-shrink-0 font-bold text-sm">2</div>
              <p className="text-sm text-slate-700 leading-snug">
                Pilih menu <strong>Izin (Permissions)</strong> atau langsung cari <strong>Lokasi (Location)</strong>.
              </p>
            </div>
            <div className="flex gap-3 items-start">
              <div className="w-7 h-7 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center flex-shrink-0 font-bold text-sm">3</div>
              <p className="text-sm text-slate-700 leading-snug">
                Ubah menjadi <strong>Izinkan (Allow)</strong>, lalu klik tombol Refresh di bawah ini.
              </p>
            </div>
          </div>

          <DialogFooter className="sm:justify-center mt-4 gap-2 flex-col sm:flex-row w-full">
            <Button 
              onClick={handleLocateMe}
              disabled={isLocating}
              className="bg-blue-600 hover:bg-blue-700 text-white w-full sm:flex-1 rounded-xl font-bold h-11 sm:h-12 text-sm flex items-center justify-center gap-2 transition-all"
            >
              {isLocating ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div> : <Locate className="w-4 h-4 sm:w-5 sm:h-5" />}
              Aktifkan Sekarang
            </Button>
            <Button 
              onClick={() => setShowLocationPopup(false)} 
              variant="outline"
              className="w-full sm:flex-1 rounded-xl font-bold h-11 sm:h-12 text-sm border-slate-200 flex items-center justify-center gap-2"
            >
              <X className="w-4 h-4 sm:w-5 sm:h-5 text-slate-500" />
              Nanti Saja
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Mosque Detail Drawer (Slides from bottom/left) */}
      {selectedMosque && <MosqueDetailDrawer />}
    </main>
  );
}
