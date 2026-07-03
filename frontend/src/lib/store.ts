import { create } from "zustand";
import { persist } from "zustand/middleware";

interface SearchSettings {
  algorithm: string;
  profile: string;
  currentTime: string;
  prayer: string;
  maxCandidates: string;
  bufferKm: string;
  autoBuild: boolean;
}

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
  endPoint: { lat: number, lng: number } | null;
  setStartPoint: (pt: { lat: number, lng: number } | null) => void;
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
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      datasets: [],
      activeDatasetId: "all",
      setDatasets: (datasets) => set({ datasets }),
      setActiveDatasetId: (id) => set({ activeDatasetId: id }),

      mosques: [],
      setMosques: (mosques) => set({ mosques }),

      startPoint: null,
      endPoint: null,
      setStartPoint: (pt) => set({ startPoint: pt }),
      setEndPoint: (pt) => set({ endPoint: pt }),

      routeData: null,
      setRouteData: (data) => set({ routeData: data }),

      selectedMosque: null,
      setSelectedMosque: (m) => set({ selectedMosque: m }),

      searchSettings: {
        algorithm: "dijkstra",
        profile: "balanced",
        currentTime: "17:00",
        prayer: "maghrib",
        maxCandidates: "3",
        bufferKm: "50",
        autoBuild: true,
      },
      setSearchSettings: (settings) =>
        set((state) => ({
          searchSettings: { ...state.searchSettings, ...settings },
        })),

      routeCache: {},
      setRouteCache: (key, data) =>
        set((state) => ({
          routeCache: { ...state.routeCache, [key]: data },
        })),
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
      setPrayerSchedule: (schedule) => set({ prayerSchedule: schedule }),
      setHijriDate: (date) => set({ hijriDate: date }),
      setMasehiDate: (date) => set({ masehiDate: date }),
    }),
    {
      name: "imosque-app-store",
      version: 2, // Naikkan versi jika ada perubahan default state yang perlu di-reset
      partialize: (state) => ({
        startPoint: state.startPoint,
        endPoint: state.endPoint,
        searchSettings: state.searchSettings,
        activeDatasetId: state.activeDatasetId,
        routeCache: state.routeCache,
        prayerSchedule: state.prayerSchedule,
        hijriDate: state.hijriDate,
        masehiDate: state.masehiDate,
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
        // Force update bufferKm ke 50 jika masih lama (10)
        if (merged.searchSettings.bufferKm === "10") {
          merged.searchSettings.bufferKm = "50";
        }
        return merged;
      },
    }
  )
);
