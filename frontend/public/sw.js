// iMosque Progressive Web App - Service Worker
// Strategi: CacheFirst untuk aset statis & tile peta, NetworkFirst untuk API

const CACHE_VERSION = "imosque-v2";
const DEV_MODE = new URL(self.location.href).searchParams.get("dev") === "1";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const TILE_CACHE = `${CACHE_VERSION}-tiles`;
const API_CACHE = `${CACHE_VERSION}-api`;
const MAX_TILE_ENTRIES = 400;
const MAX_STATIC_ENTRIES = 120;

// Aset yang akan di-precache saat install
const PRECACHE_URLS = DEV_MODE ? [] : ["/"];

// === INSTALL ===
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

// === ACTIVATE ===
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== STATIC_CACHE && key !== TILE_CACHE && key !== API_CACHE)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// === FETCH ===
self.addEventListener("fetch", (event) => {
  // 1. Abaikan skema non-http/https (seperti chrome-extension://, about:, dll)
  if (!event.request.url.startsWith("http://") && !event.request.url.startsWith("https://")) {
    return;
  }

  // 2. Abaikan method non-GET (seperti POST, PUT, DELETE) dari strategi caching.
  // Biarkan browser memproses request tersebut secara langsung secara default.
  if (event.request.method !== "GET") {
    return;
  }

  const url = new URL(event.request.url);

  // 3. Map Tiles (OpenStreetMap, CartoDB, dll) → CacheFirst (30 hari)
  if (
    url.hostname.includes("tile.openstreetmap.org") ||
    url.hostname.includes("basemaps.cartocdn.com")
  ) {
    event.respondWith(cacheFirst(event.request, TILE_CACHE, 30 * 24 * 60 * 60));
    return;
  }

  // 4. API Backend → NetworkFirst (data realtime, fallback ke cache jika offline)
  if (url.pathname.startsWith("/api/") || url.hostname === "127.0.0.1" || url.hostname === "localhost") {
    if (url.port === "8000" || url.pathname.startsWith("/api/")) {
      event.respondWith(networkFirst(event.request, API_CACHE, 6000, event));
      return;
    }
  }

  // In development, leave Next.js/HMR/page requests untouched while still
  // caching map tiles and backend GET APIs above.
  if (DEV_MODE) return;

  // 3. Aset statis Next.js (_next/static/) → CacheFirst
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(cacheFirst(event.request, STATIC_CACHE, 365 * 24 * 60 * 60));
    return;
  }

  // 4. Halaman HTML navigasi → NetworkFirst (agar selalu fresh)
  if (event.request.mode === "navigate") {
    event.respondWith(networkFirst(event.request, STATIC_CACHE, 3000, event));
    return;
  }

  // 5. Aset lainnya (font, gambar, dll) → StaleWhileRevalidate
  event.respondWith(staleWhileRevalidate(event.request, STATIC_CACHE));
});

// === STRATEGI CACHING ===

// CacheFirst: Ambil dari cache dulu, kalau tidak ada baru ke network
async function cacheFirst(request, cacheName, maxAgeSeconds) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);

  if (cached) {
    // Cek apakah masih segar
    const dateHeader = cached.headers.get("sw-cache-date");
    if (dateHeader) {
      const age = (Date.now() - new Date(dateHeader).getTime()) / 1000;
      if (age > maxAgeSeconds) {
        // Expired, fetch baru di background
        fetchAndCache(request, cache);
        return cached; // Tetap kembalikan yang lama dulu
      }
    }
    return cached;
  }

  return fetchAndCache(request, cache, cacheName === TILE_CACHE ? MAX_TILE_ENTRIES : MAX_STATIC_ENTRIES);
}

// NetworkFirst: Coba network dulu dengan timeout, fallback ke cache
function networkFirst(request, cacheName, timeoutMs, event) {
  let finishBackground;
  const backgroundDone = new Promise((resolve) => {
    finishBackground = resolve;
  });
  // Register synchronously while the FetchEvent is active. The response can
  // return immediately after the network succeeds, while the cache write keeps
  // the worker alive in the background.
  event.waitUntil(backgroundDone);

  return (async () => {
    let cache;
    try {
      cache = await caches.open(cacheName);
      const response = await promiseTimeout(fetch(request), timeoutMs);
      if (response.ok) {
        cacheNetworkResponse(cache, request, response.clone())
          .catch(() => undefined)
          .finally(() => finishBackground());
      } else {
        finishBackground();
      }
      return response;
    } catch {
      finishBackground();
      const cached = cache ? await cache.match(request) : null;
      return cached || new Response(JSON.stringify({ error: "Offline" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      });
    }
  })();
}

async function cacheNetworkResponse(cache, request, response) {
  const headers = new Headers(response.headers);
  headers.set("sw-cache-date", new Date().toISOString());
  const body = await response.blob();
  await cache.put(request, new Response(body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  }));
  await trimCache(cache, MAX_STATIC_ENTRIES);
}

// StaleWhileRevalidate: Kembalikan cache langsung, update di background
async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);

  const fetchPromise = fetch(request).then((response) => {
    if (response.ok || response.type === "opaque") {
      cache.put(request, response.clone()).then(() => trimCache(cache, MAX_STATIC_ENTRIES));
    }
    return response;
  }).catch(() => cached);

  return cached || fetchPromise;
}

// Helper: Fetch dan simpan ke cache
async function fetchAndCache(request, cache, maxEntries = MAX_STATIC_ENTRIES) {
  try {
    const response = await fetch(request);
    if (response.ok || response.type === "opaque") {
      // Cross-origin map tiles arrive as opaque responses. They cannot be
      // inspected/reconstructed, but CacheStorage can persist the clone.
      if (response.type === "opaque") {
        await cache.put(request, response.clone());
        await trimCache(cache, maxEntries);
        return response;
      }
      const cloned = response.clone();
      const headers = new Headers(cloned.headers);
      headers.set("sw-cache-date", new Date().toISOString());
      const body = await cloned.blob();
      await cache.put(request, new Response(body, {
        status: cloned.status,
        statusText: cloned.statusText,
        headers: headers,
      }));
      await trimCache(cache, maxEntries);
    }
    return response;
  } catch {
    return new Response("Offline", { status: 503 });
  }
}

async function trimCache(cache, maxEntries) {
  const keys = await cache.keys();
  if (keys.length <= maxEntries) return;
  const overflow = keys.length - maxEntries;
  await Promise.all(keys.slice(0, overflow).map((key) => cache.delete(key)));
}

// Helper: Promise dengan timeout
function promiseTimeout(promise, ms) {
  const timeout = new Promise((_, reject) =>
    setTimeout(() => reject(new Error("Timeout")), ms)
  );
  return Promise.race([promise, timeout]);
}
