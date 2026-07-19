import { API_BASE } from "@/lib/config";

export const RECOMMENDATION_CACHE_TTL_MS = 2 * 60 * 1000;
export const SELECTED_ROUTE_CACHE_TTL_MS = 5 * 60 * 1000;
const TRANSITIONAL_ROUTE_CACHE_TTL_MS = 10 * 1000;
const ROUTING_CACHE_VERSION = "v6-prayer-time-aware";
const ADMIN_TOKEN_STORAGE_KEY = "imosque_admin_token";

const NEAREST_CACHE_TTL_MS = 30 * 1000;
const NEAREST_STALE_FALLBACK_TTL_MS = 5 * 60 * 1000;
const MAX_NEAREST_CACHE_ENTRIES = 20;
const NEAREST_ATTEMPT_TIMEOUT_MS = 6_000;
const NEAREST_MAX_ATTEMPTS = 2;
export interface MosqueListItem {
  id?: string | number;
  mosque_id?: string | number;
  dataset_id?: string;
  name: string;
  address?: string;
  kecamatan?: string;
  kabko?: string;
  provinsi?: string;
  latitude: number;
  longitude: number;
  distance_km?: number | string | null;
  rating?: number | string | null;
  tier?: string;
  [key: string]: unknown;
}

interface MosqueListResponse {
  items?: MosqueListItem[];
  total?: number;
  [key: string]: unknown;
}
const nearestMosqueCache = new Map<string, { data: MosqueListResponse; cachedAt: number }>();
let activeRouteToMosqueController: AbortController | null = null;

export function isAbortError(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && "name" in error && error.name === "AbortError");
}

export function getAdminToken(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || "";
}

export function saveAdminToken(token: string): void {
  if (typeof window === "undefined") return;
  const normalized = token.trim();
  if (normalized) window.sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, normalized);
  else window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
  window.dispatchEvent(new CustomEvent("imosque-admin-token-changed"));
}

function adminHeaders(contentType = false): HeadersInit {
  const token = getAdminToken();
  return {
    ...(contentType ? { "Content-Type": "application/json" } : {}),
    ...(token ? { "X-Admin-Token": token } : {}),
  };
}

async function apiError(res: Response, fallback: string): Promise<Error> {
  const data = await res.json().catch(() => ({}));
  return new Error(data.detail || fallback);
}

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = 5_000): Promise<Response> {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

export function isRouteCacheFresh(entry: unknown, ttlMs: number): boolean {
  if (!entry || typeof entry !== "object") return false;
  const route = entry as Record<string, unknown>;
  const cachedAt = Number(route._cached_at || 0);
  const routingMode = String(route.routing_mode || "");
  const effectiveTtl = routingMode && routingMode !== "local_graph"
    ? Math.min(ttlMs, TRANSITIONAL_ROUTE_CACHE_TTL_MS)
    : ttlMs;
  return cachedAt > 0 && Date.now() - cachedAt < effectiveTtl;
}

export function buildSelectedRouteCacheKey(
  datasetId: string,
  startLat: number,
  startLon: number,
  mosqueId: string,
  algorithm: string,
  costFingerprint = "default",
  temporalFingerprint = "default",
) {
  return `selected_${ROUTING_CACHE_VERSION}_${datasetId}_${startLat.toFixed(5)}_${startLon.toFixed(5)}_${mosqueId}_${algorithm}_${costFingerprint}_${temporalFingerprint}`;
}

export async function fetchDatasets() {
  const res = await fetch(`${API_BASE}/api/v1/datasets`);
  if (!res.ok) throw new Error("Failed to fetch datasets");
  return res.json();
}

export async function fetchMosques(
  datasetId: string,
  limit = 20,
  offset = 0,
  query = "",
  kabko = "",
  signal?: AbortSignal
) {
  let url = `${API_BASE}/api/v1/mosques?dataset_id=${datasetId}&limit=${limit}&offset=${offset}`;
  if (kabko) url += `&kabko=${encodeURIComponent(kabko)}`;
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error("Failed to fetch mosques");
  const data = await res.json();
  
  if (query) {
    const q = query.toLowerCase();
    data.items = data.items.filter((m: { name?: string }) => m.name && m.name.toLowerCase().includes(q));
  }
  return data;
}

export async function searchMosques(
  datasetId: string,
  query: string,
  limit = 8,
  origin?: { lat: number; lng: number } | null,
  signal?: AbortSignal
) {
  const params = new URLSearchParams({
    dataset_id: datasetId || "all",
    q: query.trim(),
    limit: String(limit),
  });
  if (origin) {
    params.set("latitude", String(origin.lat));
    params.set("longitude", String(origin.lng));
  }
  const res = await fetch(`${API_BASE}/api/v1/mosques/search?${params}`, { signal });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Gagal mencari masjid");
  }
  return res.json() as Promise<MosqueListResponse>;
}

let activePrewarmDataset = "";
let activePrewarmPromise: Promise<Record<string, unknown>> | null = null;

export interface RoutingCorridorInput {
  start: { lat: number; lng: number };
  end: { lat: number; lng: number };
  bufferKm?: number;
}

export function prewarmRoutingDataset(
  datasetId: string,
  corridor?: RoutingCorridorInput
) {
  if (!datasetId || datasetId === "all") return Promise.resolve({ status: "skipped" });
  const requestKey = corridor
    ? `${datasetId}_${corridor.start.lat.toFixed(3)}_${corridor.start.lng.toFixed(3)}_${corridor.end.lat.toFixed(3)}_${corridor.end.lng.toFixed(3)}`
    : datasetId;
  if (activePrewarmDataset === requestKey && activePrewarmPromise) return activePrewarmPromise;
  activePrewarmDataset = requestKey;
  const params = new URLSearchParams({ dataset_id: datasetId });
  if (corridor) {
    params.set("start_lat", String(corridor.start.lat));
    params.set("start_lon", String(corridor.start.lng));
    params.set("end_lat", String(corridor.end.lat));
    params.set("end_lon", String(corridor.end.lng));
    params.set("buffer_km", String(Math.min(corridor.bufferKm || 8, 10)));
  }
  activePrewarmPromise = fetch(`${API_BASE}/api/v1/routing/prewarm?${params}`, {
    method: "POST",
  })
    .then(async (res) => {
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || "Gagal menyiapkan graph rute");
      }
      return res.json() as Promise<Record<string, unknown>>;
    })
    .finally(() => {
      if (activePrewarmDataset === requestKey) {
        activePrewarmDataset = "";
        activePrewarmPromise = null;
      }
    });
  return activePrewarmPromise;
}

export interface RouteBenchmarkPayload {
  dataset_id: string;
  origin: { latitude: number; longitude: number };
  destination: { latitude: number; longitude: number };
  departure_time: string;
  prayer: string;
  profile: string;
  search_radius_km: number;
}

interface BenchmarkPreparation {
  status?: string;
  message?: string;
  retry_after_ms?: number;
  corridor?: { graph_id?: string; status?: string; [key: string]: unknown };
  [key: string]: unknown;
}

function waitForRetry(milliseconds: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Request dibatalkan", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timeoutId);
      reject(new DOMException("Request dibatalkan", "AbortError"));
    };
    const timeoutId = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function runRouteBenchmark(
  payload: RouteBenchmarkPayload,
  onPreparing?: (state: BenchmarkPreparation) => void,
  signal?: AbortSignal
) {
  const deadline = Date.now() + 3 * 60 * 1000;
  while (Date.now() < deadline) {
    const res = await fetch(`${API_BASE}/api/v1/routes/benchmark`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && res.status !== 202) return data;
    if (res.status === 202) {
      const preparation = data as BenchmarkPreparation;
      onPreparing?.(preparation);
      await waitForRetry(
        Math.max(750, Math.min(Number(preparation.retry_after_ms || 1500), 5000)),
        signal
      );
      continue;
    }
    throw new Error(data.detail || "Gagal menjalankan benchmark");
  }
  throw new Error("Penyiapan graph koridor melewati batas waktu 3 menit. Silakan coba lagi.");
}

export async function deleteMosque(datasetId: string, mosqueId: string) {
  const res = await fetch(`${API_BASE}/api/v1/mosques/${datasetId}/${mosqueId}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete mosque");
  return res.json();
}

export async function saveMosque(datasetId: string, data: Record<string, unknown>, id?: string) {
  const url = id ? `${API_BASE}/api/v1/mosques/${datasetId}/${id}` : `${API_BASE}/api/v1/mosques/${datasetId}`;
  const res = await fetch(url, {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });
  if (!res.ok) throw new Error("Failed to save mosque");
  return res.json();
}

export async function bulkDeleteMosques(datasetId: string, ids: string[]) {
  const res = await fetch(`${API_BASE}/api/v1/mosques/bulk-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset_id: datasetId, mosque_ids: ids })
  });
  if (!res.ok) throw new Error("Failed to bulk delete");
  return res.json();
}

export async function fetchNearestMosques(
  datasetId: string,
  lat: number,
  lng: number,
  radiusKm = 25,
  limit = 10,
  signal?: AbortSignal
) {
  if (signal?.aborted) throw new DOMException("Request dibatalkan", "AbortError");

  const cacheKey = [
    datasetId,
    lat.toFixed(4),
    lng.toFixed(4),
    radiusKm.toFixed(1),
    limit,
  ].join(":");
  const cached = nearestMosqueCache.get(cacheKey);
  if (cached && Date.now() - cached.cachedAt < NEAREST_CACHE_TTL_MS) {
    return cached.data;
  }

  let timedOut = false;
  try {
    let res: Response | null = null;
    for (let attempt = 0; attempt < NEAREST_MAX_ATTEMPTS; attempt += 1) {
      const attemptController = new AbortController();
      const abortFromCaller = () => attemptController.abort();
      let attemptTimedOut = false;
      if (signal?.aborted) attemptController.abort();
      signal?.addEventListener("abort", abortFromCaller, { once: true });
      const timeoutId = globalThis.setTimeout(() => {
        attemptTimedOut = true;
        attemptController.abort();
      }, NEAREST_ATTEMPT_TIMEOUT_MS);
      try {
        res = await fetch(`${API_BASE}/api/v1/nearest-mosques`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            dataset_id: datasetId,
            latitude: lat,
            longitude: lng,
            radius_km: radiusKm,
            limit: limit
          }),
          signal: attemptController.signal
        });
        break;
      } catch (error) {
        if (signal?.aborted) throw error;
        const canRetry = attempt < NEAREST_MAX_ATTEMPTS - 1;
        const isTransientNetworkFailure = error instanceof TypeError;
        if (!canRetry || (!attemptTimedOut && !isTransientNetworkFailure)) {
          timedOut = attemptTimedOut;
          throw error;
        }
        await new Promise(resolve => globalThis.setTimeout(resolve, 400));
      } finally {
        globalThis.clearTimeout(timeoutId);
        signal?.removeEventListener("abort", abortFromCaller);
      }
    }
    if (!res) throw new Error("API pencarian masjid tidak dapat dihubungi");
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      const detail = typeof errorData.detail === "string"
        ? errorData.detail
        : "Gagal mengambil masjid terdekat";
      throw new Error(detail);
    }
    const data = await res.json() as MosqueListResponse;
    nearestMosqueCache.set(cacheKey, { data, cachedAt: Date.now() });
    while (nearestMosqueCache.size > MAX_NEAREST_CACHE_ENTRIES) {
      const oldestKey = nearestMosqueCache.keys().next().value;
      if (oldestKey === undefined) break;
      nearestMosqueCache.delete(oldestKey);
    }
    return data;
  } catch (error) {
    if (timedOut && !signal?.aborted) {
      if (cached && Date.now() - cached.cachedAt < NEAREST_STALE_FALLBACK_TTL_MS) {
        return {
          ...cached.data,
          cache_hit: true,
          cache_stale: true,
        };
      }
      throw new Error("Pencarian masjid terdekat melewati batas waktu");
    }
    throw error;
  }
}

export async function fetchPrayerTimes(lat: number, lng: number, date: string, signal?: AbortSignal) {
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  let timedOut = false;
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeoutId = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 3000);
  try {
    const params = new URLSearchParams({
      latitude: String(lat),
      longitude: String(lng),
      date,
    });
    const res = await fetch(`${API_BASE}/api/v1/prayer-times?${params}`, {
      signal: controller.signal,
      cache: "force-cache",
    });
    if (!res.ok) throw new Error("Gagal mengambil jadwal shalat");
    return res.json();
  } catch (error) {
    if (timedOut) {
      throw new Error("Jadwal shalat melewati batas waktu 3 detik");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeoutId);
    signal?.removeEventListener("abort", abortFromCaller);
  }
}

export interface TravelCostParameters {
  fuel_price_per_liter: number;
  fuel_efficiency_km_per_liter: number;
  operating_cost_per_km: number;
  toll_cost_per_km: number;
}

export async function routeToMosque(
  datasetId: string,
  startLat: number,
  startLon: number,
  mosqueId: string,
  algorithm = "dijkstra",
  bufferKm = 6.0,
  autoBuild = false,
  signal?: AbortSignal,
  costParameters?: TravelCostParameters,
  departureTime?: string,
  prayer?: string
) {
  activeRouteToMosqueController?.abort();
  const controller = new AbortController();
  activeRouteToMosqueController = controller;
  const abortFromCaller = () => controller.abort();
  if (signal?.aborted) controller.abort();
  let timedOut = false;
  const timeoutId = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 8000);
  signal?.addEventListener("abort", abortFromCaller, { once: true });
  try {
    const res = await fetch(`${API_BASE}/api/v1/route/to-mosque`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dataset_id: datasetId,
        start_lat: startLat,
        start_lon: startLon,
        mosque_id: mosqueId,
        algorithm: algorithm,
        auto_build_osm: autoBuild,
        buffer_km: bufferKm,
        compact_response: true,
        cost_parameters: costParameters,
        departure_time: departureTime,
        prayer: prayer,
      }),
      signal: controller.signal
    });
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.detail || "Gagal menghitung rute ke masjid");
    }
    return res.json();
  } catch (error) {
    if (timedOut && !signal?.aborted) {
      throw new Error("Perhitungan rute melewati batas waktu. Silakan coba lagi.");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeoutId);
    signal?.removeEventListener("abort", abortFromCaller);
    if (activeRouteToMosqueController === controller) {
      activeRouteToMosqueController = null;
    }
  }
}

export async function deleteDataset(datasetId: string) {
  const res = await fetch(`${API_BASE}/api/v1/datasets/${datasetId}`, {
    method: "DELETE",
    headers: adminHeaders(),
  });
  if (!res.ok) throw await apiError(res, "Gagal menghapus dataset");
  return res.json();
}

export async function fetchDatasetStatus(datasetId: string) {
  const res = await fetch(`${API_BASE}/api/v1/datasets/status/${datasetId}`);
  if (!res.ok) throw new Error("Failed to fetch dataset status");
  return res.json();
}

export async function fetchDatasetBbox(datasetId: string) {
  const res = await fetch(`${API_BASE}/api/v1/datasets/${encodeURIComponent(datasetId)}/bbox`);
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Gagal menghitung area dataset");
  }
  return res.json();
}

export async function buildOsmBbox(north: number, south: number, east: number, west: number, networkType = "drive", datasetId?: string) {
  const res = await fetch(`${API_BASE}/api/v1/osm/build-bbox`, {
    method: "POST",
    headers: adminHeaders(true),
    body: JSON.stringify({
      north: north,
      south: south,
      east: east,
      west: west,
      network_type: networkType,
      dataset_id: datasetId || null
    })
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Gagal membangun graph OSM");
  }
  return res.json();
}

export async function buildAllOsmGraphs(networkType = "drive", force = false) {
  const res = await fetch(`${API_BASE}/api/v1/osm/build-all`, {
    method: "POST",
    headers: adminHeaders(true),
    body: JSON.stringify({ network_type: networkType, force })
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Gagal memulai build graph semua dataset");
  }
  return res.json();
}

export async function fetchBuildAllOsmStatus() {
  const res = await fetch(`${API_BASE}/api/v1/osm/build-all/status`, { cache: "no-store" });
  if (!res.ok) throw new Error("Gagal membaca progres build graph");
  return res.json();
}

export async function cancelBuildAllOsm() {
  const res = await fetch(`${API_BASE}/api/v1/osm/build-all/cancel`, {
    method: "POST",
    headers: adminHeaders(),
  });
  if (!res.ok) throw await apiError(res, "Gagal membatalkan build graph");
  return res.json();
}

export async function verifyAdminAccess(token = getAdminToken()) {
  const res = await fetchWithTimeout(`${API_BASE}/api/v1/admin/access`, {
    cache: "no-store",
    headers: token ? { "X-Admin-Token": token } : {},
  });
  if (!res.ok) throw await apiError(res, "Akses superadmin ditolak");
  return res.json();
}

export async function fetchCorridorCacheSummary(limit = 50) {
  const res = await fetchWithTimeout(`${API_BASE}/api/v1/routing/corridors?limit=${limit}`, {
    cache: "no-store",
  });
  if (!res.ok) throw await apiError(res, "Gagal membaca cache graph koridor");
  return res.json();
}

export async function fetchSystemHealth() {
  const res = await fetchWithTimeout(`${API_BASE}/api/v1/health`, { cache: "no-store" });
  if (!res.ok) throw await apiError(res, `Health check gagal (HTTP ${res.status})`);
  return res.json();
}

export async function checkSystemHealth() {
  const controller = new AbortController();
  const timeoutId = globalThis.setTimeout(() => controller.abort(), 5000);
  try {
    const res = await fetch(`${API_BASE}/api/v1/health`, {
      signal: controller.signal,
      cache: "no-store",
    });
    if (!res.ok) {
      return {
        backendOnline: true,
        connected: false,
        empty: false,
        error: `HTTP Error: ${res.status}`
      };
    }
    const data = await res.json();
    return {
      backendOnline: true,
      connected: data.database?.connected ?? (data.status === "healthy"),
      empty: data.database?.empty ?? false,
      error: data.database?.error || undefined
    };
  } catch (error) {
    return {
      backendOnline: false,
      connected: false,
      empty: false,
      error: isAbortError(error)
        ? "Pemeriksaan backend melewati batas waktu"
        : (error instanceof Error ? error.message : String(error))
    };
  } finally {
    globalThis.clearTimeout(timeoutId);
  }
}
