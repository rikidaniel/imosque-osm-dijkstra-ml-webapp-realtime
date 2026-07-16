const API_BASE = typeof window !== "undefined"
  ? `http://${window.location.hostname}:8000`
  : "http://127.0.0.1:8000";

export const RECOMMENDATION_CACHE_TTL_MS = 2 * 60 * 1000;
export const SELECTED_ROUTE_CACHE_TTL_MS = 5 * 60 * 1000;
const ROUTING_CACHE_VERSION = "v4-edge-snap-segments";

const NEAREST_CACHE_TTL_MS = 30 * 1000;
const MAX_NEAREST_CACHE_ENTRIES = 20;
interface MosqueListResponse {
  items?: Record<string, unknown>[];
  total?: number;
  [key: string]: unknown;
}
const nearestMosqueCache = new Map<string, { data: MosqueListResponse; cachedAt: number }>();
let activeRouteToMosqueController: AbortController | null = null;

export function isAbortError(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && "name" in error && error.name === "AbortError");
}

export function isRouteCacheFresh(entry: unknown, ttlMs: number): boolean {
  if (!entry || typeof entry !== "object") return false;
  const cachedAt = Number((entry as Record<string, unknown>)._cached_at || 0);
  return cachedAt > 0 && Date.now() - cachedAt < ttlMs;
}

export function buildSelectedRouteCacheKey(
  datasetId: string,
  startLat: number,
  startLon: number,
  mosqueId: string,
  algorithm: string
) {
  return `selected_${ROUTING_CACHE_VERSION}_${datasetId}_${startLat.toFixed(5)}_${startLon.toFixed(5)}_${mosqueId}_${algorithm}`;
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

  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abortFromCaller, { once: true });
  let timedOut = false;
  const timeoutId = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 12000);
  try {
    let res: Response | null = null;
    for (let attempt = 0; attempt < 2; attempt += 1) {
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
          signal: controller.signal
        });
        break;
      } catch (error) {
        const isTransientNetworkFailure = error instanceof TypeError && !controller.signal.aborted;
        if (!isTransientNetworkFailure || attempt === 1) throw error;
        await new Promise(resolve => globalThis.setTimeout(resolve, 400));
      }
    }
    if (!res) throw new Error("API pencarian masjid tidak dapat dihubungi");
    if (!res.ok) throw new Error("Failed to fetch nearest mosques");
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
      throw new Error("Pencarian masjid terdekat melewati batas waktu");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeoutId);
    signal?.removeEventListener("abort", abortFromCaller);
  }
}

export async function fetchPrayerTimes(lat: number, lng: number, date: string, signal?: AbortSignal) {
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeoutId = globalThis.setTimeout(() => controller.abort(), 3000);
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
  } finally {
    globalThis.clearTimeout(timeoutId);
    signal?.removeEventListener("abort", abortFromCaller);
  }
}

export async function routeToMosque(
  datasetId: string,
  startLat: number,
  startLon: number,
  mosqueId: string,
  algorithm = "dijkstra",
  bufferKm = 6.0,
  autoBuild = false,
  signal?: AbortSignal
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
  }, 15000);
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
        compact_response: true
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
  const res = await fetch(`${API_BASE}/api/v1/datasets/${datasetId}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete dataset");
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
    headers: { "Content-Type": "application/json" },
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
    headers: { "Content-Type": "application/json" },
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
  const res = await fetch(`${API_BASE}/api/v1/osm/build-all/cancel`, { method: "POST" });
  if (!res.ok) throw new Error("Gagal membatalkan build graph");
  return res.json();
}
