import { create } from "zustand";
import { persist } from "zustand/middleware";
import { debouncedSaveSettings, loadSettingsFromDatabase, saveSettingsToDatabase } from "./settings-sync";

interface SearchSettings {
  algorithm: string;
  profile: string;
  departureMode: "now" | "scheduled";
  currentTime: string;
  prayer: string;
  maxCandidates: string;
  bufferKm: string;
  autoBuild: boolean;
  fuelPricePerLiter: string;
  fuelEfficiencyKmPerLiter: string;
  operatingCostPerKm: string;
  tollCostPerKm: string;
}

type StartPointSource = "gps" | "map";

interface AppState {
  // Datasets
  datasets: any[];
  activeDatasetId: string | null;
  setDatasets: (datasets: any[]) => void;
  setActiveDatasetId: (id: string | null) => void;

  // Mosques
  mosques: any[];
  setMosques: (mosques: any[]) => void;

  // Routing Map State
  startPoint: { lat: number, lng: number } | null;
  startPointUpdatedAt: number | null;
  startPointSource: StartPointSource | null;
  endPoint: { lat: number, lng: number } | null;
  setStartPoint: (
    pt: { lat: number, lng: number } | null,
    updatedAt?: number,
    source?: StartPointSource
  ) => void;
  setEndPoint: (pt: { lat: number, lng: number } | null) => void;
  
  routeData: any | null;
  setRouteData: (data: any | null) => void;

  // Selected Mosque for Details Drawer
  selectedMosque: any | null;
  setSelectedMosque: (m: any | null) => void;

  // Search Settings (Persistent)
  searchSettings: SearchSettings;
  setSearchSettings: (settings: Partial<SearchSettings>) => void;

  // Route Caching for Offline & Performance Optimization
  routeCache: Record<string, any>;
  setRouteCache: (key: string, data: any) => void;
  clearRouteCache: () => void;

  // Prayer Times Persistent Cache for Instant Load
  prayerSchedule: any[] | null;
  hijriDate: string | null;
  masehiDate: string | null;
  setPrayerSchedule: (schedule: any[]) => void;
  setHijriDate: (date: string) => void;
  setMasehiDate: (date: string) => void;
  prayerCacheKey: string | null;
  setPrayerCacheKey: (key: string) => void;
  settingsSyncStatus: "loading" | "loaded" | "offline";
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      datasets: [],
      activeDatasetId: "all",
      setDatasets: (datasets) => set({ datasets }),
      setActiveDatasetId: (id) => set({ activeDatasetId: id }),

      mosques: [],
      setMosques: (mosques) => set({ mosques }),

      startPoint: null,
      startPointUpdatedAt: null,
      startPointSource: null,
      endPoint: null,
      setStartPoint: (pt, updatedAt = Date.now(), source = "map") => set({
        startPoint: pt,
        startPointUpdatedAt: pt ? updatedAt : null,
        startPointSource: pt ? source : null,
      }),
      setEndPoint: (pt) => set({ endPoint: pt }),

      routeData: null,
      setRouteData: (data) => set({ routeData: data }),

      selectedMosque: null,
      setSelectedMosque: (m) => set({ selectedMosque: m }),

      searchSettings: {
        algorithm: "dijkstra",
        profile: "balanced",
        departureMode: "now",
        currentTime: "17:00",
        prayer: "auto",
        maxCandidates: "3",
        bufferKm: "15",
        autoBuild: false,
        fuelPricePerLiter: "10000",
        fuelEfficiencyKmPerLiter: "12",
        operatingCostPerKm: "300",
        tollCostPerKm: "1000",
      },
      setSearchSettings: (settings) => {
        set((state) => ({
          searchSettings: { ...state.searchSettings, ...settings },
        }));
        // Auto-save to database with debounce
        const currentState = get();
        debouncedSaveSettings({
          searchSettings: { ...currentState.searchSettings, ...settings },
          prayerSettings: {
            schedule: currentState.prayerSchedule || [],
            hijriDate: currentState.hijriDate,
            masehiDate: currentState.masehiDate,
          }
        });
      },

      routeCache: {},
      setRouteCache: (key, data) =>
        set((state) => {
          const entries = Object.entries(state.routeCache)
            .filter(([existingKey]) => existingKey !== key)
            .slice(-19);
          return {
            routeCache: Object.fromEntries([...entries, [key, { ...data, _cached_at: Date.now() }]]),
          };
        }),
      clearRouteCache: () => set({ routeCache: {} }),

      prayerSchedule: [
        { name: "Subuh", time: "04:45", isAlarmActive: true },
        { name: "Dzuhur", time: "12:02", isAlarmActive: false },
        { name: "Ashar", time: "15:24", isAlarmActive: false },
        { name: "Maghrib", time: "17:58", isAlarmActive: true },
        { name: "Isya", time: "19:12", isAlarmActive: false },
      ],
      hijriDate: "12 Muharram 1448 H",
      masehiDate: "12 Juli 2026",
      prayerCacheKey: null,
      settingsSyncStatus: "loading",
      setPrayerSchedule: (schedule) => {
        set({ prayerSchedule: schedule });
        // Auto-save to database with debounce
        const currentState = get();
        debouncedSaveSettings({
          searchSettings: currentState.searchSettings,
          prayerSettings: {
            schedule: schedule,
            hijriDate: currentState.hijriDate,
            masehiDate: currentState.masehiDate,
          }
        });
      },
      setHijriDate: (date) => {
        set({ hijriDate: date });
        const state = get();
        debouncedSaveSettings({
          searchSettings: state.searchSettings,
          prayerSettings: { schedule: state.prayerSchedule || [], hijriDate: date, masehiDate: state.masehiDate },
        });
      },
      setMasehiDate: (date) => {
        set({ masehiDate: date });
        const state = get();
        debouncedSaveSettings({
          searchSettings: state.searchSettings,
          prayerSettings: { schedule: state.prayerSchedule || [], hijriDate: state.hijriDate, masehiDate: date },
        });
      },
      setPrayerCacheKey: (key) => set({ prayerCacheKey: key }),
    }),
    {
      name: "imosque-app-store",
      version: 7,
      migrate: (persistedState) => ({
        ...(persistedState as AppState),
        routeCache: {},
        routeData: null,
      }),
      partialize: (state) => ({
        startPoint: state.startPoint,
        startPointUpdatedAt: state.startPointUpdatedAt,
        startPointSource: state.startPointSource,
        endPoint: state.endPoint,
        searchSettings: state.searchSettings,
        activeDatasetId: state.activeDatasetId,
        routeCache: state.routeCache,
        prayerSchedule: state.prayerSchedule,
        hijriDate: state.hijriDate,
        masehiDate: state.masehiDate,
        prayerCacheKey: state.prayerCacheKey,
      }),
      merge: (persistedState: any, currentState) => {
        // Pastikan searchSettings default terbaru dipakai jika version lama
        const merged = {
          ...currentState,
          ...persistedState,
          searchSettings: {
            ...currentState.searchSettings,
            ...(persistedState?.searchSettings || {}),
          },
        };
        // Existing installs used inline Overpass builds. Keep interactive routes fast.
        merged.searchSettings.autoBuild = false;
        
        // Load settings from database on initialization (async, non-blocking)
        if (typeof window !== "undefined") {
          loadSettingsFromDatabase().then((dbSettings) => {
            if (dbSettings) {
              // Merge database settings with current state
              useAppStore.setState((state) => ({
                searchSettings: {
                  ...state.searchSettings,
                  ...(dbSettings.search_settings || {}),
                },
                prayerSchedule: dbSettings.prayer_settings?.schedule || state.prayerSchedule,
                hijriDate: dbSettings.prayer_settings?.hijriDate || state.hijriDate,
                masehiDate: dbSettings.prayer_settings?.masehiDate || state.masehiDate,
                settingsSyncStatus: "loaded",
              }));
              console.log("Settings loaded from database and merged with local state");
            } else {
              // Jika tidak ada di database, simpan setelan default (lokal) ke database secara otomatis
              const state = useAppStore.getState();
              saveSettingsToDatabase({
                searchSettings: state.searchSettings,
                prayerSettings: {
                  schedule: state.prayerSchedule || [],
                  hijriDate: state.hijriDate,
                  masehiDate: state.masehiDate,
                }
              }).then((saved) => {
                if (saved) {
                  console.log("Default settings successfully saved to database on first run");
                }
              }).catch(console.error);

              useAppStore.setState({ settingsSyncStatus: "loaded" });
            }
          }).catch((err) => {
            console.warn("Could not load settings from database:", err);
            useAppStore.setState({ settingsSyncStatus: "offline" });
          });
        }
        
        return merged;
      },
    }
  )
);

if (typeof window !== "undefined") {
  (window as any).useAppStore = useAppStore;
}

