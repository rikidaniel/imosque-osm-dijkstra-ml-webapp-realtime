const API_BASE = "http://127.0.0.1:8000";
const UI_STATE_KEY = "imosque-ui-state-v2";

// Inisialisasi Peta Leaflet dengan koordinat Tangerang/Banten
const map = L.map("map").setView([-6.1783, 106.6319], 11);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

// Injeksi CSS Keyframes secara dinamis untuk efek pulse marker rekomendasi
const styleEl = document.createElement("style");
styleEl.innerHTML = `
  @keyframes pulse-purple {
    0% { box-shadow: 0 0 0 0 rgba(124, 58, 237, 0.7); }
    70% { box-shadow: 0 0 0 10px rgba(124, 58, 237, 0); }
    100% { box-shadow: 0 0 0 0 rgba(124, 58, 237, 0); }
  }
`;
document.head.appendChild(styleEl);

// State Aplikasi
let mode = null;
let startMarker = null;
let endMarker = null;
let mosqueLayer = L.layerGroup().addTo(map);
let nearestLayer = L.layerGroup().addTo(map);
let routeLayer = null;
let recommendedMarker = null;
let datasets = [];
let activeDatasetId = null;
let nearestMosques = [];
let isRestoringState = false;

// Elemen DOM
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const datasetSelect = document.getElementById("datasetSelect");
const datasetInfo = document.getElementById("datasetInfo");
const sidebarEl = document.getElementById("sidebar");
const btnToggleSidebar = document.getElementById("btnToggleSidebar");
const toggleIcon = document.getElementById("toggleIcon");
const routeNoticeEl = document.getElementById("routeNotice");
const nearestListEl = document.getElementById("nearestList");

function readUiState() {
  const hashState = readUrlState();
  try {
    const storedState = JSON.parse(localStorage.getItem(UI_STATE_KEY) || "{}");
    return { ...storedState, ...hashState };
  } catch (_) {
    return hashState;
  }
}

function saveUiState(patch = {}) {
  const previous = readUiState();
  const next = { ...previous, ...patch, updated_at: new Date().toISOString() };
  try {
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(next));
  } catch (_) {
    // Browser storage can be disabled/full; the app should keep working.
  }
  writeUrlState(next);
}

function readUrlState() {
  const hash = window.location.hash || "";
  if (!hash.startsWith("#state=")) return {};
  try {
    return JSON.parse(decodeURIComponent(hash.slice("#state=".length)));
  } catch (_) {
    return {};
  }
}

function compactUrlState(state) {
  return {
    dataset_id: state.dataset_id || null,
    active_tab: state.active_tab || "tab-dataset",
    algorithm: state.algorithm || "dijkstra",
    current_time: state.current_time || "",
    prayer_time: state.prayer_time || "",
    max_candidates: state.max_candidates || "6",
    buffer_km: state.buffer_km || "6",
    auto_build: typeof state.auto_build === "boolean" ? state.auto_build : false,
    start: state.start || null,
    end: state.end || null,
    map: state.map || null
  };
}

function writeUrlState(state) {
  try {
    const compact = compactUrlState(state);
    const nextHash = `#state=${encodeURIComponent(JSON.stringify(compact))}`;
    if (window.location.hash !== nextHash) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${nextHash}`);
    }
  } catch (_) {
    // Keep UI state best-effort only.
  }
}

function markerState(marker) {
  if (!marker) return null;
  const latlng = marker.getLatLng();
  return { lat: latlng.lat, lng: latlng.lng };
}

function persistUiState(extra = {}) {
  if (isRestoringState) return;
  const center = map.getCenter();
  saveUiState({
    dataset_id: datasetSelect?.value || activeDatasetId || null,
    active_tab: document.querySelector(".tab-btn.active")?.getAttribute("data-tab") || "tab-dataset",
    algorithm: document.getElementById("algorithm")?.value || "dijkstra",
    current_time: document.getElementById("currentTime")?.value || "",
    prayer_time: document.getElementById("prayerTime")?.value || "",
    max_candidates: document.getElementById("maxCandidates")?.value || "6",
    buffer_km: document.getElementById("bufferKm")?.value || "6",
    auto_build: Boolean(document.getElementById("autoBuild")?.checked),
    start: markerState(startMarker),
    end: markerState(endMarker),
    map: { lat: center.lat, lng: center.lng, zoom: map.getZoom() },
    ...extra
  });
}

// Custom DIV Icon Premium untuk Leaflet
const startIcon = L.divIcon({
  html: `<div style="background-color: #2d6a4f; width: 14px; height: 14px; border: 3px solid white; border-radius: 50%; box-shadow: 0 0 10px rgba(0,0,0,0.35);"></div>`,
  className: "custom-marker-start",
  iconSize: [20, 20],
  iconAnchor: [10, 10]
});

const endIcon = L.divIcon({
  html: `<div style="background-color: #ef4444; width: 14px; height: 14px; border: 3px solid white; border-radius: 50%; box-shadow: 0 0 10px rgba(0,0,0,0.35);"></div>`,
  className: "custom-marker-end",
  iconSize: [20, 20],
  iconAnchor: [10, 10]
});

const recommendedIcon = L.divIcon({
  html: `
    <div style="background-color: #7c3aed; width: 22px; height: 22px; border: 3px solid white; border-radius: 50%; box-shadow: 0 0 12px rgba(124, 58, 237, 0.7); display: flex; align-items: center; justify-content: center; animation: pulse-purple 2s infinite;">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3" width="10" height="10">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
      </svg>
    </div>
  `,
  className: "custom-marker-recommended",
  iconSize: [26, 26],
  iconAnchor: [13, 13]
});

// Helper Fungsi UI
function setStatus(text) { statusEl.textContent = text; }
function setResult(html) { resultEl.classList.remove("empty"); resultEl.innerHTML = html; }
function selectedDatasetId() { return datasetSelect.value || activeDatasetId || "banten"; }
function setRouteNotice(text, type = "") {
  if (!routeNoticeEl) return;
  routeNoticeEl.textContent = text;
  routeNoticeEl.className = `route-notice${type ? ` ${type}` : ""}`;
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

function tierValue(tier) {
  const value = String(tier || "D").toUpperCase();
  return ["A", "B", "C", "D"].includes(value) ? value : "D";
}

function isLocalGraphRoute(data) {
  return data?.algorithm === "Dijkstra" || data?.algorithm === "A*";
}

function routeLineColor(data) {
  if (data?.algorithm === "Dijkstra") return "#2d6a4f";
  if (data?.algorithm === "A*") return "#7c3aed";
  return "#f59e0b";
}

function routingModeLabel(data) {
  if (data?.algorithm === "Dijkstra") return "Dijkstra Lokal";
  if (data?.algorithm === "A*") return "A* Lokal";
  if (data?.algorithm === "OSRM Road Route") return "OSRM Fallback";
  if (data?.algorithm === "Local Approximation") return "Perkiraan Lokal";
  return "Rute Jalan";
}

function routingModeNote(data) {
  if (isLocalGraphRoute(data)) {
    return "Dijkstra/A* lokal aktif: shortest path dihitung pada graph jalan OpenStreetMap dengan bobot travel_time.";
  }
  if (data?.algorithm === "OSRM Road Route") {
    return "Bukan Dijkstra lokal. Backend memakai OSRM fallback karena graph OSM lokal belum tersedia/cocok atau Overpass gagal.";
  }
  return "Bukan Dijkstra lokal. Rute ini adalah perkiraan lokal tanpa graph jalan OSM.";
}

function clearRouteArtifacts() {
  if (routeLayer) {
    routeLayer.remove();
    routeLayer = null;
  }
  if (recommendedMarker) {
    recommendedMarker.remove();
    recommendedMarker = null;
  }
  resultEl.classList.add("empty");
  resultEl.textContent = "Belum ada rute aktif. Silakan tentukan titik awal & tujuan di Tab Rute.";
  if (!isRestoringState) saveUiState({ last_route: null });
}

function saveLastRoute(data, datasetId) {
  const routeState = {
    dataset_id: datasetId,
    algorithm: data.algorithm,
    recommended_mosque: data.recommended_mosque,
    route_summary: data.route_summary,
    route_geojson: data.route_geojson,
    candidate_count: data.candidate_count,
    restored_at: new Date().toISOString()
  };
  try {
    const encoded = JSON.stringify(routeState);
    if (encoded.length < 1000000) {
      saveUiState({ last_route: routeState });
    }
  } catch (_) {
    saveUiState({ last_route: null });
  }
}

function restoreLastRoute() {
  const state = readUiState();
  const data = state.last_route;
  if (!data?.route_geojson?.geometry?.coordinates?.length || !data.recommended_mosque) return;
  if (state.dataset_id && data.dataset_id && state.dataset_id !== data.dataset_id) return;

  isRestoringState = true;
  clearRouteArtifacts();
  isRestoringState = false;
  routeLayer = L.geoJSON(data.route_geojson, {
    style: {
      color: routeLineColor(data),
      weight: 6,
      opacity: 0.85
    }
  }).addTo(map);

  const m = data.recommended_mosque;
  recommendedMarker = L.marker([m.latitude, m.longitude], { icon: recommendedIcon })
    .addTo(map)
    .bindTooltip(`Rekomendasi Utama: ${escapeHtml(m.name || "Masjid")}`, { sticky: true })
    .bindPopup(`<b>Masjid Rekomendasi Terpilih:</b><br>${escapeHtml(m.name || "Masjid")}`);

  resultEl.classList.remove("empty");
  resultEl.innerHTML = `
    <div class="badge-row">
      <span class="badge-algo">${escapeHtml(data.algorithm)}</span>
      <span class="badge-algo">${escapeHtml(routingModeLabel(data))}</span>
      <span class="badge-algo">${escapeHtml((data.dataset_id || selectedDatasetId()).toUpperCase())}</span>
    </div>
    <h3 class="recommendation-title">${escapeHtml(m.name || "Masjid")}</h3>
    <p class="recommendation-meta">${escapeHtml(m.province || "")} ${escapeHtml(m.kabko || "")} - ${escapeHtml(m.kecamatan || "")}</p>
    <div class="recommendation-stats" style="margin-top: 10px;">
      <div class="stat-item">Jarak Total: <strong>${escapeHtml(data.route_summary?.distance_km ?? "-")} km</strong></div>
      <div class="stat-item">Waktu Total: <strong>${escapeHtml(data.route_summary?.estimated_time_minutes ?? "-")} mnt</strong></div>
      <div class="stat-item">Skor Akhir: <strong>${escapeHtml(data.route_summary?.multi_objective_score ?? "-")}</strong></div>
    </div>
    <div class="mode-note ${isLocalGraphRoute(data) ? "success" : "warning"}">${escapeHtml(routingModeNote(data))}</div>
    <p class="hint-text" style="color: var(--text-main); margin-bottom: 12px; font-weight: 500;">Rute terakhir dipulihkan dari browser.</p>
  `;
  setRouteNotice(`Rute terakhir dipulihkan: ${routingModeLabel(data)}.`, isLocalGraphRoute(data) ? "success" : "warning");
}

function clearNearestMosques(message = "Pilih titik awal untuk melihat rekomendasi masjid terdekat.") {
  nearestMosques = [];
  nearestLayer.clearLayers();
  if (nearestListEl) {
    nearestListEl.className = "nearest-list empty";
    nearestListEl.textContent = message;
  }
}

function describePointState() {
  if (startMarker && endMarker) return "Titik awal dan tujuan sudah siap. Klik Cari Rute Teroptimal.";
  if (startMarker) return "Titik awal sudah dipilih. Sekarang pilih titik tujuan.";
  if (endMarker) return "Titik tujuan sudah dipilih. Sekarang pilih titik awal.";
  return "Pilih titik awal dan tujuan di peta sebelum mencari rute.";
}

function getTierColor(tier) {
  switch ((tier || "").toUpperCase()) {
    case "A": return "#2563eb";
    case "B": return "#7c3aed";
    case "C": return "#f97316";
    case "D": return "#334155";
    default: return "#64748b";
  }
}

function createMosqueIcon(tier) {
  const safeTier = tierValue(tier);
  const color = getTierColor(safeTier);
  return L.divIcon({
    html: `
      <div class="mosque-marker" style="--marker-color: ${color};">
        <div class="mosque-marker-symbol">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M3 20h18"/>
            <path d="M5 20v-6a7 7 0 0 1 14 0v6"/>
            <path d="M8 20v-5a4 4 0 0 1 8 0v5"/>
            <path d="M12 3v5"/>
            <path d="m12 3 3 3"/>
            <path d="m12 3-3 3"/>
          </svg>
        </div>
        <div class="mosque-marker-tip"></div>
      </div>
    `,
    className: "mosque-marker-wrapper",
    iconSize: [30, 38],
    iconAnchor: [15, 36],
    popupAnchor: [0, -34],
    tooltipAnchor: [0, -30]
  });
}

// Sidebar Collapse Toggle
btnToggleSidebar.onclick = () => {
  sidebarEl.classList.toggle("collapsed");
  setTimeout(() => map.invalidateSize(), 300); // Sinkronisasi peta Leaflet
};

// Logika Tabs
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.onclick = () => {
    switchTab(btn.getAttribute("data-tab"));
  };
});

function switchTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach(b => {
    if (b.getAttribute("data-tab") === tabId) b.classList.add("active");
    else b.classList.remove("active");
  });
  document.querySelectorAll(".tab-panel").forEach(panel => {
    if (panel.id === tabId) panel.classList.add("active");
    else panel.classList.remove("active");
  });
  persistUiState({ active_tab: tabId });
}

// Helper untuk Loading State pada Button
function setLoading(buttonId, isLoading, originalText) {
  const btn = document.getElementById(buttonId);
  if (!btn) return;
  if (isLoading) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></svg> Memproses...`;
  } else {
    btn.disabled = false;
    btn.innerHTML = originalText;
  }
}

// Client HTTP API Wrappers
function parseApiResponseText(text) {
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (_) {
    return { detail: text };
  }
}

function apiErrorMessage(data, text, status) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  if (Array.isArray(detail)) return detail.map(item => item.msg || item.message || String(item)).join("; ");
  return text || `HTTP ${status}`;
}

async function api(path, options = {}) {
  const { timeoutMs = 120000, ...fetchOptions } = options;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      ...fetchOptions
    }).catch((err) => {
      if (err.name === "AbortError") {
        throw new Error("Request timeout. Overpass/OSM sedang lambat atau area OSM terlalu besar. Coba kecilkan Buffer OSM atau build graph manual lebih dulu.");
      }
      throw err;
    });
    const text = await res.text();
    const data = parseApiResponseText(text);
    if (!res.ok) throw new Error(apiErrorMessage(data, text, res.status));
    return data;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function apiForm(path, formData) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: formData
  });
  const text = await res.text();
  const data = parseApiResponseText(text);
  if (!res.ok) throw new Error(apiErrorMessage(data, text, res.status));
  return data;
}

// Status Refresh & Sinkronisasi
async function refreshStatus() {
  try {
    const health = await api("/api/health");
    const osm = await api("/api/osm/status");
    activeDatasetId = health.active_dataset_id;
    setStatus(
      `● Status Backend: ${health.status.toUpperCase()}\n` +
      `● Dataset Aktif: ${health.active_dataset_id}\n` +
      `● File JSON ML: ${health.enriched_json_exists ? "Tersedia" : "Belum Ada"}\n` +
      `● Cache Graph OSM: ${osm.cache_exists ? "Tersedia" : "Belum Ada"} (${osm.size_mb} MB)\n\n` +
      `Catatan: Jika beralih wilayah, silakan lakukan unduh/build ulang data OSM.`
    );
  } catch (err) {
    setStatus(`Backend offline atau terjadi masalah koneksi:\n${err.message}`);
  }
}

function describeDataset(item) {
  const p = item.profile || {};
  const label = p.dataset_label || item.dataset_id;
  const rows = p.enriched_rows || p.valid_coordinate_rows || "Belum diproses";
  const bbox = p.bbox ? `\nBBox Wilayah:\n - Selatan: ${p.bbox.south.toFixed(4)}\n - Utara: ${p.bbox.north.toFixed(4)}\n - Barat: ${p.bbox.west.toFixed(4)}\n - Timur: ${p.bbox.east.toFixed(4)}` : "";
  return `Dataset ID: ${item.dataset_id}\nProvinsi Mode: ${label}\nJumlah Data Masjid: ${rows}\nStatus ML Pipeline: ${item.processed ? "SELESAI" : "BELUM"}${bbox}`;
}

async function refreshDatasets() {
  try {
    const data = await api("/api/datasets");
    const savedState = readUiState();
    datasets = data.items || [];
    activeDatasetId = data.active_dataset_id;
    datasetSelect.innerHTML = "";
    datasets.forEach(item => {
      const opt = document.createElement("option");
      opt.value = item.dataset_id;
      const p = item.profile || {};
      const label = p.dataset_label || item.dataset_id;
      opt.textContent = `${item.is_active ? "✓ " : ""}${label} (${item.dataset_id})`;
      if (item.dataset_id === activeDatasetId) opt.selected = true;
      datasetSelect.appendChild(opt);
    });
    if (savedState.dataset_id && datasets.some(item => item.dataset_id === savedState.dataset_id)) {
      datasetSelect.value = savedState.dataset_id;
    }
    const current = datasets.find(d => d.dataset_id === selectedDatasetId());
    datasetInfo.textContent = current ? describeDataset(current) : "Belum ada dataset.";
  } catch (err) {
    datasetInfo.textContent = `Gagal sinkronisasi daftar dataset: ${err.message}`;
  }
}

function fitMapToProfile(profile) {
  if (!profile || !profile.bbox) return;
  const b = profile.bbox;
  const bounds = L.latLngBounds([[b.south, b.west], [b.north, b.east]]);
  if (bounds.isValid()) map.flyToBounds(bounds, { padding: [30, 30] });
}

function isValidSavedPoint(point) {
  return point && Number.isFinite(Number(point.lat)) && Number.isFinite(Number(point.lng));
}

function restoreUiState() {
  const state = readUiState();
  isRestoringState = true;
  try {
    if (state.algorithm) document.getElementById("algorithm").value = state.algorithm;
    if (state.current_time) document.getElementById("currentTime").value = state.current_time;
    if (state.prayer_time) document.getElementById("prayerTime").value = state.prayer_time;
    if (state.max_candidates) document.getElementById("maxCandidates").value = state.max_candidates;
    if (state.buffer_km) document.getElementById("bufferKm").value = state.buffer_km;
    if (typeof state.auto_build === "boolean") document.getElementById("autoBuild").checked = state.auto_build;

    if (state.map && Number.isFinite(Number(state.map.lat)) && Number.isFinite(Number(state.map.lng)) && Number.isFinite(Number(state.map.zoom))) {
      map.setView([Number(state.map.lat), Number(state.map.lng)], Number(state.map.zoom), { animate: false });
    }

    if (isValidSavedPoint(state.start)) {
      startMarker = L.marker([Number(state.start.lat), Number(state.start.lng)], { icon: startIcon })
        .addTo(map)
        .bindTooltip("Titik Awal (Start)", { sticky: true })
        .bindPopup("Titik Awal (Start)");
    }
    if (isValidSavedPoint(state.end)) {
      endMarker = L.marker([Number(state.end.lat), Number(state.end.lng)], { icon: endIcon })
        .addTo(map)
        .bindTooltip("Titik Tujuan (Destination)", { sticky: true })
        .bindPopup("Titik Tujuan (Destination)");
    }

    if (state.active_tab) switchTab(state.active_tab);
    setRouteNotice(describePointState(), startMarker || endMarker ? "success" : "");
  } finally {
    isRestoringState = false;
  }
}

// Event Actions
async function activateSelectedDataset({ loadMarkers = true } = {}) {
  const originalText = "Gunakan";
  try {
    setLoading("btnUseDataset", true, originalText);
    const did = selectedDatasetId();
    const form = new FormData();
    form.append("dataset_id", did);
    
    const data = await apiForm("/api/datasets/active", form);
    activeDatasetId = data.active_dataset_id;
    persistUiState({ dataset_id: activeDatasetId });
    fitMapToProfile(data.profile);
    await refreshDatasets();
    await refreshStatus();
    if (loadMarkers) await loadMosques();
  } catch (err) {
    setStatus(`Gagal beralih dataset:\n${err.message}`);
  } finally {
    setLoading("btnUseDataset", false, originalText);
  }
}

async function runPipelineForSelected() {
  const originalText = "Proses ML Ulang";
  try {
    setLoading("btnRunPipeline", true, originalText);
    const did = selectedDatasetId();
    const profile = await api(`/api/pipeline/run?dataset_id=${encodeURIComponent(did)}`, { method: "POST" });
    fitMapToProfile(profile);
    await refreshDatasets();
    await loadMosques();
    setStatus(`ML Enrichment Pipeline berhasil diproses ulang. Total: ${profile.enriched_rows} baris.`);
  } catch (err) {
    setStatus(`Gagal menjalankan ML Enrichment:\n${err.message}`);
  } finally {
    setLoading("btnRunPipeline", false, originalText);
  }
}

async function uploadDataset() {
  const originalText = "Upload & Jalankan ML";
  try {
    const fileInput = document.getElementById("datasetFile");
    if (!fileInput.files.length) throw new Error("Pilih berkas CSV dataset Anda terlebih dahulu.");
    
    setLoading("btnUploadDataset", true, originalText);
    const datasetName = document.getElementById("datasetName").value.trim();
    
    const form = new FormData();
    form.append("file", fileInput.files[0]);
    if (datasetName) form.append("dataset_name", datasetName);
    form.append("process_now", "true");
    form.append("make_active", "true");
    
    const data = await apiForm("/api/datasets/upload", form);
    activeDatasetId = data.dataset_id;
    await refreshDatasets();
    datasetSelect.value = data.dataset_id;
    persistUiState({ dataset_id: data.dataset_id });
    fitMapToProfile(data.profile);
    await loadMosques();
    setStatus(`Dataset berhasil diupload & diperkaya dengan ML. Dataset aktif: ${data.dataset_id}.`);
  } catch (err) {
    setStatus(`Gagal mengunggah dataset:\n${err.message}`);
  } finally {
    setLoading("btnUploadDataset", false, originalText);
  }
}

// Marker Pop-up & Rendering
function getTierLabel(tier) {
  switch ((tier || "").toUpperCase()) {
    case "A": return "Prioritas Utama";
    case "B": return "Prioritas Tinggi";
    case "C": return "Standar";
    case "D": return "Minim Data";
    default: return "Lainnya";
  }
}

function formatCoord(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(6) : "-";
}

function popupField(label, value) {
  const display = value === undefined || value === null || value === "" ? "-" : value;
  return `
    <div class="popup-field">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(display)}</strong>
    </div>
  `;
}

function mosquePopup(m) {
  const tier = tierValue(m.tier);
  const tierName = getTierLabel(tier);
  const facilities = Array.isArray(m.facilities) && m.facilities.length ? m.facilities.join(", ") : "-";
  const region = [m.province, m.kabko, m.kecamatan, m.kelurahan].filter(Boolean).join(" / ") || "-";
  return `
    <div class="mosque-popup">
      <h3>${escapeHtml(m.name || "Masjid")}</h3>
      <p class="popup-address">${escapeHtml(m.address || "Tanpa alamat")}</p>
      <div class="popup-badges">
        <span class="badge-tier tier-${tier.toLowerCase()}" title="${tierName}">Tier ${tier} (${tierName})</span>
        <span class="popup-capacity">Cap: ${escapeHtml(m.capacity_proxy || "unknown")}</span>
      </div>
      <div class="popup-details">
        ${popupField("Nama", m.name || "Masjid")}
        ${popupField("Alamat", m.address || "Tanpa alamat")}
        ${popupField("Wilayah", region)}
        ${popupField("Tipe", m.mosque_type || "-")}
        ${popupField("Rating", m.rating)}
        ${popupField("Jumlah ulasan", m.review_count)}
        ${popupField("Priority score", m.priority_score)}
        ${popupField("Fasilitas", facilities)}
        ${popupField("Latitude", formatCoord(m.latitude))}
        ${popupField("Longitude", formatCoord(m.longitude))}
        ${popupField("ID", m.id || "-")}
      </div>
    </div>
  `;
}

function markerPopup(m) {
  const tier = tierValue(m.tier);
  const tierName = getTierLabel(tier);
  return `
    <div style="font-family: var(--font-main); min-width: 180px;">
      <h3 style="font-size: 13px; font-weight: 800; color: var(--primary-dark); margin-bottom: 4px;">${escapeHtml(m.name || "Masjid")}</h3>
      <p style="font-size: 11px; color: var(--text-muted); margin-bottom: 8px;">${escapeHtml(m.address || "Tanpa alamat")}</p>
      <div style="display: flex; gap: 4px; margin-bottom: 8px;">
        <span class="badge-tier tier-${tier.toLowerCase()}" title="${tierName}">Tier ${tier} (${tierName})</span>
        <span style="font-size: 10px; font-weight: 700; background: #e0f2fe; color: #0369a1; padding: 2px 6px; border-radius: 4px; text-transform: uppercase;">Cap: ${escapeHtml(m.capacity_proxy || "unknown")}</span>
      </div>
      <div style="font-size: 11px; border-top: 1px solid #f1f5f9; padding-top: 6px;">
        ⭐ <b>${m.rating}</b> (${m.review_count} Ulasan)<br>
        ⚙️ Priority Score: <b>${m.priority_score}</b><br>
        <span style="font-size: 10px; color: var(--text-light);">Fasilitas: ${escapeHtml((m.facilities || []).join(", "))}</span>
      </div>
    </div>
  `;
}

function markerTooltip(m) {
  const tier = tierValue(m.tier);
  return `
    <div style="font-family: var(--font-main); font-size: 11px; padding: 2px;">
      <b style="color: var(--primary-dark);">${escapeHtml(m.name || "Masjid")}</b><br>
      <span class="badge-tier tier-${tier.toLowerCase()}" style="padding: 1px 4px; font-size: 8.5px; border-radius: 3px; margin-top: 3px; display: inline-block;">Tier ${tier}</span> 
      ⭐ <b>${m.rating}</b>
    </div>
  `;
}

async function loadMosques() {
  try {
    const did = selectedDatasetId();
    setStatus(`Memuat data koordinat masjid dari dataset ${did}...`);
    const data = await api(`/api/mosques?dataset_id=${encodeURIComponent(did)}&limit=20000`);
    
    mosqueLayer.clearLayers();
    data.items.forEach(m => {
      L.circleMarker([m.latitude, m.longitude], {
        radius: 5,
        fillColor: getTierColor(tierValue(m.tier)),
        color: "#ffffff",
        weight: 1,
        opacity: 0.8,
        fillOpacity: 1,
        keyboard: false,
        title: m.name || "Masjid"
      })
      .bindTooltip(markerTooltip(m), {
        sticky: true,
        direction: "top",
        opacity: 0.95
      })
      .bindPopup(mosquePopup(m), {
        maxWidth: 340,
        minWidth: 260
      })
      .addTo(mosqueLayer);
    });
    
    const profile = await api(`/api/profile?dataset_id=${encodeURIComponent(did)}`);
    fitMapToProfile(profile);
    setStatus(`Data masjid dimuat: ${data.items.length} dari total ${data.total} masjid.`);
  } catch (err) {
    setStatus(`Gagal memuat koordinat masjid:\n${err.message}`);
  }
}

function renderNearestMosques(items) {
  nearestMosques = items || [];
  nearestLayer.clearLayers();

  if (!nearestListEl) return;
  if (!nearestMosques.length) {
    nearestListEl.className = "nearest-list empty";
    nearestListEl.textContent = "Tidak ada masjid terdekat dalam radius pencarian.";
    return;
  }

  nearestListEl.className = "nearest-list";
  nearestListEl.innerHTML = "";
  nearestMosques.forEach((m, index) => {
    const marker = L.marker([m.latitude, m.longitude], {
      icon: createMosqueIcon(m.tier),
      keyboard: false,
      title: m.name || "Masjid terdekat"
    })
      .bindTooltip(`${index + 1}. ${escapeHtml(m.name || "Masjid")} (${escapeHtml(m.distance_km)} km)`, {
        sticky: true,
        direction: "top"
      })
      .bindPopup(mosquePopup(m))
      .on("click", () => routeToNearestMosque(m.id))
      .addTo(nearestLayer);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "nearest-item";
    button.innerHTML = `
      <span class="nearest-name">${index + 1}. ${escapeHtml(m.name || "Masjid")}</span>
      <span class="nearest-meta">
        <span>${escapeHtml(m.distance_km)} km</span>
        <span>Tier ${escapeHtml(tierValue(m.tier))}</span>
        <span>Skor ${escapeHtml(m.priority_score)}</span>
      </span>
    `;
    button.onclick = () => {
      marker.openPopup();
      map.flyTo([m.latitude, m.longitude], 15, { duration: 0.8 });
      routeToNearestMosque(m.id);
    };
    nearestListEl.appendChild(button);
  });
}

async function loadNearestMosques() {
  try {
    if (!startMarker) throw new Error("Pilih titik awal dulu dengan tombol Set Start.");
    const start = startMarker.getLatLng();
    const did = selectedDatasetId();
    setRouteNotice("Mencari masjid terdekat dari titik awal...", "loading");
    if (nearestListEl) {
      nearestListEl.className = "nearest-list empty";
      nearestListEl.textContent = "Memuat rekomendasi masjid terdekat...";
    }
    const data = await api("/api/nearest-mosques", {
      method: "POST",
      body: JSON.stringify({
        dataset_id: did,
        latitude: start.lat,
        longitude: start.lng,
        limit: 6,
        radius_km: 10
      })
    });
    renderNearestMosques(data.items || []);
    setRouteNotice(`Ditemukan ${data.items?.length || 0} masjid terdekat. Klik salah satu untuk membuat rute.`, "success");
  } catch (err) {
    clearNearestMosques(err.message);
    setRouteNotice(err.message, "error");
  }
}

async function routeToNearestMosque(mosqueId) {
  const originalText = "Cari Rute Teroptimal";
  try {
    if (!startMarker) throw new Error("Pilih titik awal dulu dengan tombol Set Start.");
    const start = startMarker.getLatLng();
    const did = selectedDatasetId();
    setLoading("btnRoute", true, originalText);
    setRouteNotice(
      document.getElementById("autoBuild").checked
        ? "Mencoba cache/auto-build cepat; jika area terlalu besar akan fallback ke OSRM."
        : "Menghitung rute jalan OSRM ke masjid terpilih...",
      "loading"
    );
    const data = await api("/api/route/to-mosque", {
      method: "POST",
      timeoutMs: 60000,
      body: JSON.stringify({
        dataset_id: did,
        start_lat: start.lat,
        start_lon: start.lng,
        mosque_id: mosqueId,
        algorithm: document.getElementById("algorithm").value,
        auto_build_osm: document.getElementById("autoBuild").checked,
        buffer_km: Number(document.getElementById("bufferKm").value || 6)
      })
    });

    if (!data.route_geojson?.geometry?.coordinates?.length) {
      throw new Error("Backend tidak mengembalikan geometri rute yang valid.");
    }
    clearRouteArtifacts();
    saveLastRoute(data, did);
    routeLayer = L.geoJSON(data.route_geojson, {
      style: {
        color: routeLineColor(data),
        weight: 6,
        opacity: 0.88
      }
    }).addTo(map);
    map.flyToBounds(routeLayer.getBounds(), { padding: [40, 40] });

    const m = data.recommended_mosque;
    recommendedMarker = L.marker([m.latitude, m.longitude], { icon: recommendedIcon })
      .addTo(map)
      .bindTooltip(`Masjid Terpilih: ${escapeHtml(m.name || "Masjid")}`, { sticky: true })
      .bindPopup(`<b>Masjid Terpilih:</b><br>${escapeHtml(m.name || "Masjid")}`)
      .openPopup();

    setResult(`
      <div class="badge-row">
        <span class="badge-algo">${escapeHtml(data.algorithm)}</span>
        <span class="badge-algo">${escapeHtml(routingModeLabel(data))}</span>
        <span class="badge-algo">${escapeHtml(did.toUpperCase())}</span>
      </div>
      <h3 class="recommendation-title">${escapeHtml(m.name || "Masjid")}</h3>
      <p class="recommendation-meta">${escapeHtml(m.province || "")} ${escapeHtml(m.kabko || "")} - ${escapeHtml(m.kecamatan || "")}</p>
      <div class="recommendation-stats" style="margin-top: 10px;">
        <div class="stat-item">Jarak: <strong>${escapeHtml(data.route_summary.distance_km)} km</strong></div>
        <div class="stat-item">Waktu: <strong>${escapeHtml(data.route_summary.estimated_time_minutes)} mnt</strong></div>
        <div class="stat-item">Node Rute: <strong>${escapeHtml(data.route_summary.route_nodes_count)}</strong></div>
        <div class="stat-item">Skor: <strong>${escapeHtml(data.route_summary.multi_objective_score)}</strong></div>
      </div>
      <div class="mode-note ${isLocalGraphRoute(data) ? "success" : "warning"}">${escapeHtml(routingModeNote(data))}</div>
      <p class="hint-text" style="color: var(--text-main); margin-bottom: 12px; font-weight: 500;">
        ${escapeHtml(data.route_summary.reason)}
      </p>
    `);
    switchTab("tab-result");
    setRouteNotice(`${routingModeLabel(data)} ke ${m.name || "masjid"} berhasil dibuat.`, isLocalGraphRoute(data) ? "success" : "warning");
  } catch (err) {
    setRouteNotice(err.message, "error");
  } finally {
    setLoading("btnRoute", false, originalText);
  }
}

// Penentuan Titik Mulai & Selesai dengan Klik Peta
document.getElementById("btnSetStart").onclick = (e) => {
  mode = "start";
  document.querySelectorAll(".btn-set-point").forEach(b => b.classList.remove("active"));
  document.getElementById("btnSetStart").classList.add("active");
  setRouteNotice("Mode Set Start aktif. Klik lokasi titik awal di peta.", "loading");
  setStatus("Mode 'Set Start' AKTIF. Klik pada lokasi awal di peta.");
};

document.getElementById("btnSetEnd").onclick = (e) => {
  mode = "end";
  document.querySelectorAll(".btn-set-point").forEach(b => b.classList.remove("active"));
  document.getElementById("btnSetEnd").classList.add("active");
  setRouteNotice("Mode Set Tujuan aktif. Klik lokasi tujuan di peta.", "loading");
  setStatus("Mode 'Set Destination' AKTIF. Klik pada lokasi tujuan di peta.");
};

map.on("click", (e) => {
  if (!mode) return;
  const latlng = e.latlng;
  if (mode === "start") {
    if (startMarker) startMarker.remove();
    startMarker = L.marker(latlng, { icon: startIcon })
      .addTo(map)
      .bindTooltip("Titik Awal (Start)", { sticky: true })
      .bindPopup("Titik Awal (Start)")
      .openPopup();
    document.getElementById("btnSetStart").classList.remove("active");
    clearRouteArtifacts();
    clearNearestMosques("Memuat rekomendasi masjid terdekat...");
    setRouteNotice(describePointState(), endMarker ? "success" : "");
    setStatus(`Koordinat Awal: ${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}`);
    persistUiState({ start: markerState(startMarker) });
    loadNearestMosques();
  } else if (mode === "end") {
    if (endMarker) endMarker.remove();
    endMarker = L.marker(latlng, { icon: endIcon })
      .addTo(map)
      .bindTooltip("Titik Tujuan (Destination)", { sticky: true })
      .bindPopup("Titik Tujuan (Destination)")
      .openPopup();
    document.getElementById("btnSetEnd").classList.remove("active");
    clearRouteArtifacts();
    setRouteNotice(describePointState(), startMarker ? "success" : "");
    setStatus(`Koordinat Tujuan: ${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}`);
    persistUiState({ end: markerState(endMarker) });
  }
  mode = null;
});

function requirePoints() {
  if (!startMarker && !endMarker) throw new Error("Titik awal dan tujuan belum dipilih. Klik Set Start, pilih lokasi awal, lalu klik Set Tujuan.");
  if (!startMarker) throw new Error("Titik awal belum dipilih. Klik Set Start lalu klik lokasi awal di peta.");
  if (!endMarker) throw new Error("Titik tujuan belum dipilih. Klik Set Tujuan lalu klik lokasi tujuan di peta.");
  return {
    start: startMarker.getLatLng(),
    end: endMarker.getLatLng()
  };
}

// Eksekusi Pembuatan OSM Graph Manual
document.getElementById("btnBuildOsm").onclick = async () => {
  const originalText = "Build OSM Graph dari Start-End";
  try {
    const { start, end } = requirePoints();
    const bufferKm = Number(document.getElementById("bufferKm").value || 6);
    
    setLoading("btnBuildOsm", true, originalText);
    setRouteNotice("Membangun graph OSM lokal. Jika Overpass lambat, proses akan berhenti dengan pesan singkat.", "loading");
    setStatus("Membangun OSM road graph. Backend mencoba beberapa endpoint Overpass...");
    
    const data = await api("/api/osm/build-route", {
      method: "POST",
      timeoutMs: 180000,
      body: JSON.stringify({
        start_lat: start.lat,
        start_lon: start.lng,
        end_lat: end.lat,
        end_lon: end.lng,
        buffer_km: bufferKm,
        network_type: "drive"
      })
    });
    setRouteNotice(`Graph OSM sukses dibuat. Nodes: ${data.nodes}, edges: ${data.edges}.`, "success");
    setStatus(`OSM Road Graph sukses terbuat.\nNodes: ${data.nodes} | Edges: ${data.edges}`);
    await refreshStatus();
  } catch (err) {
    setRouteNotice(err.message, "error");
    setStatus(`Gagal memproses Graph OSM:\n${err.message}`);
  } finally {
    setLoading("btnBuildOsm", false, originalText);
  }
};

// Fokus Kamera pada Alternatif Masjid Terpilih
function focusOnMosque(lat, lon, mName, tier, cap) {
  map.flyTo([lat, lon], 15, { duration: 1.5 });
  // Cari circleMarker terdekat pada layar dan buka pop-up nya
  mosqueLayer.eachLayer(layer => {
    const pos = layer.getLatLng();
    if (Math.abs(pos.lat - lat) < 0.0001 && Math.abs(pos.lng - lon) < 0.0001) {
      layer.openPopup();
    }
  });
}

// Eksekusi Pencarian Rute
document.getElementById("btnRoute").onclick = async () => {
  const originalText = "Cari Rute Teroptimal";
  try {
    const { start, end } = requirePoints();
    const did = selectedDatasetId();
    const payload = {
      dataset_id: did,
      start_lat: start.lat,
      start_lon: start.lng,
      end_lat: end.lat,
      end_lon: end.lng,
      algorithm: document.getElementById("algorithm").value,
      current_time: document.getElementById("currentTime").value,
      prayer_time: document.getElementById("prayerTime").value,
      max_candidates: Number(document.getElementById("maxCandidates").value || 6),
      auto_build_osm: document.getElementById("autoBuild").checked,
      buffer_km: Number(document.getElementById("bufferKm").value || 6)
    };
    
    setLoading("btnRoute", true, originalText);
    setRouteNotice(
      payload.auto_build_osm
        ? "Mencoba cache/auto-build cepat; jika area terlalu besar akan fallback ke OSRM."
        : "Mengambil kandidat dari SQLite lalu menghitung rute jalan OSRM...",
      "loading"
    );
    setStatus(
      payload.auto_build_osm
        ? "Mencoba Dijkstra lokal jika cache cocok. Area besar akan langsung dialihkan ke OSRM agar tidak loading lama..."
        : "Mengambil kandidat masjid dari SQLite dan menghitung rute jalan via OSRM..."
    );
    
    const data = await api("/api/route", { method: "POST", timeoutMs: 60000, body: JSON.stringify(payload) });
    
    // Gambar ulang rute ke peta Leaflet
    if (!data.route_geojson?.geometry?.coordinates?.length) {
      throw new Error("Backend tidak mengembalikan geometri rute yang valid.");
    }
    clearRouteArtifacts();
    saveLastRoute(data, did);
    routeLayer = L.geoJSON(data.route_geojson, {
      style: {
        color: routeLineColor(data),
        weight: 6,
        opacity: 0.85
      }
    }).addTo(map);
    map.flyToBounds(routeLayer.getBounds(), { padding: [40, 40] });
    
    // Gambar marker masjid rekomendasi
    const m = data.recommended_mosque;
    const recommendedTier = tierValue(m.tier);
    const recommendedTierLabel = getTierLabel(recommendedTier);
    const capacityProxy = String(m.capacity_proxy || "unknown");
    recommendedMarker = L.marker([m.latitude, m.longitude], { icon: recommendedIcon })
      .addTo(map)
      .bindTooltip(`Rekomendasi Utama: ${escapeHtml(m.name || "Masjid")}`, { sticky: true })
      .bindPopup(`<b>Masjid Rekomendasi Terpilih:</b><br>${escapeHtml(m.name || "Masjid")}`)
      .openPopup();
      
    // Render detail hasil di tab-result
    let candidateHtml = "";
    if (data.candidate_mosques && data.candidate_mosques.length > 0) {
      candidateHtml = `
        <div style="margin-top: 16px; border-top: 1px solid var(--border-color); padding-top: 12px;">
          <h4 style="font-size: 12.5px; font-weight: 700; margin-bottom: 6px; color: var(--text-muted);">Alternatif Masjid Lainnya</h4>
          ${data.candidate_mosques.map((c, i) => {
            const cTier = tierValue(c.tier);
            const cTierLabel = getTierLabel(cTier);
            return `
            <div class="candidate-item" onclick="focusOnMosque(${Number(c.latitude)}, ${Number(c.longitude)})">
              <div class="candidate-header">
                <span class="candidate-name">${i + 1}. ${escapeHtml(c.name || "Masjid")}</span>
                <span class="badge-tier tier-${cTier.toLowerCase()}" title="${cTierLabel}">Tier ${cTier} (${cTierLabel})</span>
              </div>
              <div class="candidate-details">
                <span>Jarak: <b>${escapeHtml(c.distance_km)} km</b></span>
                <span>Skor Evaluasi: <b>${escapeHtml(c.multi_objective_score)}</b></span>
              </div>
            </div>
          `;
          }).join("")}
        </div>
      `;
    }
    const networkLabel = routingModeLabel(data);
    const routingModeNoteHtml = data.algorithm === "OSRM Road Route"
      ? `
        <div class="mode-note warning">
          ${escapeHtml(routingModeNote(data))}
        </div>
      `
      : data.algorithm === "Local Approximation"
        ? `
          <div class="mode-note warning">
            ${escapeHtml(routingModeNote(data))}
          </div>
        `
        : `
          <div class="mode-note success">
            ${escapeHtml(routingModeNote(data))}
          </div>
        `;

    setResult(`
      <div class="badge-row">
        <span class="badge-algo">${escapeHtml(data.algorithm)}</span>
        <span class="badge-algo">${escapeHtml(networkLabel)}</span>
        <span class="badge-algo">${escapeHtml(did.toUpperCase())}</span>
      </div>
      <h3 class="recommendation-title">${escapeHtml(m.name || "Masjid")}</h3>
      <p class="recommendation-meta">${escapeHtml(m.province || "")} ${escapeHtml(m.kabko || "")} - ${escapeHtml(m.kecamatan || "")}</p>
      
      <div class="recommendation-stats" style="margin-top: 10px;">
        <div class="stat-item">Jarak Total: <strong>${escapeHtml(data.route_summary.distance_km)} km</strong></div>
        <div class="stat-item">Waktu Total: <strong>${escapeHtml(data.route_summary.estimated_time_minutes)} mnt</strong></div>
        <div class="stat-item">Tiba di Masjid: <strong>${escapeHtml(data.route_summary.arrival_to_mosque_minutes)} mnt</strong></div>
        <div class="stat-item">Skor Akhir: <strong>${escapeHtml(data.route_summary.multi_objective_score)}</strong></div>
      </div>
      
      <div class="badge-row" style="margin-bottom: 10px;">
        <span class="badge-tier tier-${recommendedTier.toLowerCase()}" title="${recommendedTierLabel}">Tier ${recommendedTier} (${recommendedTierLabel})</span>
        <span class="badge-algo" style="background:#f1f5f9; color:#475569;">Kapasitas: ${escapeHtml(capacityProxy.toUpperCase())}</span>
      </div>

      <p class="hint-text" style="color: var(--text-main); margin-bottom: 12px; font-weight: 500;">
        ${escapeHtml(data.route_summary.reason)}
      </p>
      ${routingModeNoteHtml}
      
      <p style="font-size: 10px; color: var(--text-light); border-top: 1px solid var(--border-color); padding-top: 8px;">
        Kualitas Data: Rating=${escapeHtml(m.data_quality?.rating_source || "unknown")}, Fasilitas=${escapeHtml(m.data_quality?.facilities_source || "unknown")}, Kapasitas=${escapeHtml(m.data_quality?.capacity_source || "unknown")}
      </p>
      ${candidateHtml}
    `);
    
    // Alihkan tab sidebar secara otomatis ke "Hasil" agar user langsung melihat rekomendasi
    switchTab("tab-result");
    
    setRouteNotice(`${routingModeLabel(data)} berhasil dibuat. Detail rekomendasi tersedia di tab Hasil.`, isLocalGraphRoute(data) ? "success" : "warning");
    setStatus(`${routingModeLabel(data)} selesai. Mengevaluasi ${data.candidate_count} masjid alternatif.`);
  } catch (err) {
    setRouteNotice(err.message, "error");
    setStatus(`Gagal menghitung rute optimal:\n${err.message}`);
  } finally {
    setLoading("btnRoute", false, originalText);
  }
};

// Bind Event Global Lainnya
document.getElementById("btnLoadMosques").onclick = loadMosques;
document.getElementById("btnFindNearest").onclick = loadNearestMosques;
document.getElementById("btnRefreshDatasets").onclick = refreshDatasets;
document.getElementById("btnUseDataset").onclick = () => activateSelectedDataset({ loadMarkers: true });
document.getElementById("btnRunPipeline").onclick = runPipelineForSelected;
document.getElementById("btnUploadDataset").onclick = uploadDataset;

const datasetFileInput = document.getElementById("datasetFile");
const fileNameDisplay = document.getElementById("fileNameDisplay");
if (datasetFileInput && fileNameDisplay) {
  datasetFileInput.addEventListener("change", () => {
    if (datasetFileInput.files && datasetFileInput.files.length > 0) {
      fileNameDisplay.textContent = datasetFileInput.files[0].name;
    } else {
      fileNameDisplay.textContent = "Pilih Berkas CSV";
    }
  });
}

datasetSelect.onchange = () => {
  const item = datasets.find(d => d.dataset_id === selectedDatasetId());
  datasetInfo.textContent = item ? describeDataset(item) : "Dataset terpilih belum dikenali.";
  persistUiState({ dataset_id: selectedDatasetId() });
};

["algorithm", "currentTime", "prayerTime", "maxCandidates", "bufferKm", "autoBuild"].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("change", () => persistUiState());
  el.addEventListener("input", () => persistUiState());
});

map.on("moveend", () => persistUiState());

// Inisialisasi Awal
(async function init() {
  await refreshDatasets();
  restoreUiState();
  restoreLastRoute();
  if (startMarker && !readUiState().last_route) {
    loadNearestMosques();
  }
  await refreshStatus();
})();
