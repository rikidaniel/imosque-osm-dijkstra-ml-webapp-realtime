const API_BASE = typeof window !== "undefined"
  ? `http://${window.location.hostname}:8000`
  : "http://127.0.0.1:8000";

export async function fetchDatasets() {
  const res = await fetch(`${API_BASE}/api/v1/datasets`);
  if (!res.ok) throw new Error("Failed to fetch datasets");
  return res.json();
}

export async function fetchMosques(datasetId: string, limit = 20, offset = 0, query = "", kabko = "") {
  let url = `${API_BASE}/api/v1/mosques?dataset_id=${datasetId}&limit=${limit}&offset=${offset}`;
  if (kabko) url += `&kabko=${encodeURIComponent(kabko)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Failed to fetch mosques");
  const data = await res.json();
  
  if (query) {
    const q = query.toLowerCase();
    data.items = data.items.filter((m: any) => m.name && m.name.toLowerCase().includes(q));
  }
  return data;
}

export async function deleteMosque(datasetId: string, mosqueId: string) {
  const res = await fetch(`${API_BASE}/api/v1/mosques/${datasetId}/${mosqueId}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete mosque");
  return res.json();
}

export async function saveMosque(datasetId: string, data: any, id?: string) {
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
  limit = 10
) {
  const res = await fetch(`${API_BASE}/api/v1/nearest-mosques`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dataset_id: datasetId,
      latitude: lat,
      longitude: lng,
      radius_km: radiusKm,
      limit: limit
    })
  });
  if (!res.ok) throw new Error("Failed to fetch nearest mosques");
  return res.json();
}

export async function routeToMosque(
  datasetId: string,
  startLat: number,
  startLon: number,
  mosqueId: string,
  algorithm = "dijkstra",
  bufferKm = 6.0,
  autoBuild = true
) {
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
      buffer_km: bufferKm
    })
  });
  if (!res.ok) throw new Error("Failed to route to mosque");
  return res.json();
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



