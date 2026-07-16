"use client";

import React, { useEffect, useRef, useState } from "react";
import { Circle, MapContainer, TileLayer, Marker, Popup, Polyline, useMapEvents, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import L from "leaflet";

// Custom Markers
let StartIcon: any = null;
let DestinationIcon: any = null;
let MosqueIcon: any = null;
let RecMosqueIcon: any = null;

if (typeof window !== "undefined") {
  StartIcon = L.divIcon({
    className: "custom-start-marker",
    html: `<div class="w-6 h-6 bg-emerald-500 border-2 border-white rounded-full flex items-center justify-center shadow-lg shadow-emerald-500/50 marker-pulse-green">
             <div class="w-2.5 h-2.5 bg-white rounded-full"></div>
           </div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -12]
  });

  DestinationIcon = L.divIcon({
    className: "custom-dest-marker",
    html: `<div class="w-6 h-6 bg-rose-500 border-2 border-white rounded-full flex items-center justify-center shadow-lg shadow-rose-500/50 marker-pulse-red">
             <div class="w-2.5 h-2.5 bg-white rounded-full"></div>
           </div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -12]
  });

  MosqueIcon = L.divIcon({
    className: "custom-mosque-marker",
    html: `<div class="w-7 h-7 bg-teal-50 border border-teal-200 rounded-full flex items-center justify-center shadow-md text-teal-600 hover:scale-110 transition-all duration-300">
             <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-4 h-4">
               <path d="M12 2c-.5 0-1 .5-1 1v1.1C6.4 4.7 3 8.7 3 13.5v7.5h18v-7.5c0-4.8-3.4-8.8-8-9.3V3c0-.5-.5-1-1-1zm0 3.5c3.6 0 6.5 2.9 6.5 6.5H5.5c0-3.6 2.9-6.5 6.5-6.5zM5.5 14h13v5h-13v-5z"/>
             </svg>
           </div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -14]
  });

  RecMosqueIcon = L.divIcon({
    className: "custom-rec-mosque-marker",
    html: `<div class="w-9 h-9 bg-amber-500 border-2 border-white rounded-full flex items-center justify-center shadow-lg shadow-amber-500/50 text-white animate-bounce">
             <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5">
               <path d="M12 2c-.5 0-1 .5-1 1v1.1C6.4 4.7 3 8.7 3 13.5v7.5h18v-7.5c0-4.8-3.4-8.8-8-9.3V3c0-.5-.5-1-1-1zm0 3.5c3.6 0 6.5 2.9 6.5 6.5H5.5c0-3.6 2.9-6.5 6.5-6.5zM5.5 14h13v5h-13v-5z"/>
             </svg>
           </div>`,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -18]
  });
}

function MapEvents({ onClick }: { onClick?: (e: L.LeafletMouseEvent) => void }) {
  useMapEvents({
    click(e) {
      if (onClick) onClick(e);
    }
  });
  return null;
}

function FitBounds({ bounds, lowDataMode }: { bounds: [number, number][] | null; lowDataMode: boolean }) {
  const map = useMap();
  const previousSignature = useRef<string | null>(null);

  useEffect(() => {
    if (bounds && bounds.length > 0) {
      const first = bounds[0];
      const last = bounds[bounds.length - 1];
      const boundsSignature = `${bounds.length}:${first.join(",")}:${last.join(",")}`;
      if (previousSignature.current !== boundsSignature) {
        const isMobile = typeof window !== "undefined" && window.innerWidth < 768;
        const sharedOptions = {
          maxZoom: lowDataMode ? 15 : 16,
          animate: !lowDataMode,
        };
        if (isMobile) {
          // Berikan padding bawah 200px agar rute berada di paruh atas layar (tidak tertutup panel bawah)
          map.fitBounds(bounds as L.LatLngBoundsExpression, {
            ...sharedOptions,
            paddingTopLeft: [24, 24],
            paddingBottomRight: [24, 200]
          });
        } else {
          map.fitBounds(bounds as L.LatLngBoundsExpression, { ...sharedOptions, padding: [50, 50] });
        }
        previousSignature.current = boundsSignature;
      }
    }
  }, [bounds, lowDataMode, map]);
  return null;
}

function ChangeMapView({ center }: { center: [number, number] | undefined }) {
  const map = useMap();
  const previousCenter = useRef<[number, number] | null>(null);

  useEffect(() => {
    if (center) {
      const hasChanged = !previousCenter.current || previousCenter.current[0] !== center[0] || previousCenter.current[1] !== center[1];
      if (hasChanged) {
        map.setView(center, map.getZoom());
        previousCenter.current = center;
      }
    }
  }, [center, map]);
  return null;
}

function FitSearchRadius({
  center,
  radiusKm,
  enabled,
  lowDataMode,
}: {
  center: [number, number] | undefined;
  radiusKm: number;
  enabled: boolean;
  lowDataMode: boolean;
}) {
  const map = useMap();
  const previousSignature = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || !center || !Number.isFinite(radiusKm) || radiusKm <= 0) return;
    const signature = `${center[0].toFixed(5)}:${center[1].toFixed(5)}:${radiusKm.toFixed(2)}`;
    if (previousSignature.current === signature) return;

    const isMobile = typeof window !== "undefined" && window.innerWidth < 768;
    const latitudeDelta = radiusKm / 111.32;
    const longitudeScale = Math.max(Math.cos(center[0] * Math.PI / 180), 0.01);
    const longitudeDelta = radiusKm / (111.32 * longitudeScale);
    const radiusBounds = L.latLngBounds(
      [center[0] - latitudeDelta, center[1] - longitudeDelta],
      [center[0] + latitudeDelta, center[1] + longitudeDelta]
    );
    map.fitBounds(radiusBounds, {
      paddingTopLeft: isMobile ? [24, 80] : [60, 60],
      paddingBottomRight: isMobile ? [24, 210] : [60, 60],
      maxZoom: lowDataMode ? 15 : 16,
      animate: false,
    });
    previousSignature.current = signature;
  }, [center, enabled, lowDataMode, map, radiusKm]);

  return null;
}

function createDynamicMosqueIcon(marker: any) {
  const tier = marker.tier || "D";
  const ratingStr = String(marker.rating || "0").replace(",", ".");
  const rating = isNaN(parseFloat(ratingStr)) ? 0 : parseFloat(ratingStr);
  const isRecommended = marker.type === "recommended";

  // Desain warna modern dengan gradien mewah berbasis Tier & rekomendasi
  let pinColor = "from-slate-500 to-slate-400 border-slate-300 text-white shadow-slate-500/20";
  let pulseRing = "";
  
  if (isRecommended) {
    // Oranye ke Emas untuk recommended agar serasi dengan rute oranye
    pinColor = "from-orange-500 via-amber-500 to-yellow-400 border-white text-white shadow-orange-500/40";
    pulseRing = "animate-ping absolute inline-flex h-full w-full rounded-full bg-orange-500 opacity-40";
  } else {
    switch (tier) {
      case "A":
        pinColor = "from-amber-500 to-yellow-400 border-amber-200 text-white shadow-amber-500/30";
        pulseRing = "absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-10";
        break;
      case "B":
        pinColor = "from-emerald-500 to-teal-400 border-emerald-200 text-white shadow-emerald-500/30";
        break;
      case "C":
        pinColor = "from-teal-500 to-cyan-400 border-teal-200 text-white shadow-teal-500/20";
        break;
      default:
        pinColor = "from-slate-400 to-slate-300 border-slate-200 text-slate-700 dark:text-slate-300 shadow-slate-400/10";
        break;
    }
  }

  // Parse facilities untuk dots kecil di bawah pin
  let hasAC = false;
  let hasParking = false;
  let hasWudu = false;
  let hasToilet = false;
  if (marker.facilities) {
    let facs: string[] = [];
    if (Array.isArray(marker.facilities)) {
      facs = marker.facilities.map((f: any) => String(f).toLowerCase());
    } else if (typeof marker.facilities === "string") {
      facs = marker.facilities.split(/[|,;]+/).map((f: string) => f.trim().toLowerCase()).filter(Boolean);
    }
    hasAC = facs.some(f => f.includes("ac"));
    hasParking = facs.some(f => f.includes("park"));
    hasWudu = facs.some(f => f.includes("wud"));
    hasToilet = facs.some(f => f.includes("toilet"));
  }

  // Dots fasilitas
  let dotsHtml = "";
  if (hasAC) dotsHtml += `<span class="w-1.5 h-1.5 rounded-full bg-sky-400"></span>`;
  if (hasWudu) dotsHtml += `<span class="w-1.5 h-1.5 rounded-full bg-teal-400"></span>`;
  if (hasParking) dotsHtml += `<span class="w-1.5 h-1.5 rounded-full bg-indigo-400"></span>`;
  if (hasToilet) dotsHtml += `<span class="w-1.5 h-1.5 rounded-full bg-rose-400"></span>`;

  // Badge Rating / Tier di atas
  let badgeHtml = "";
  if (rating > 0) {
    badgeHtml = `
      <div class="absolute -top-3.5 left-1/2 -translate-x-1/2 bg-slate-900/95 dark:bg-slate-950 text-white text-[8px] font-black px-1.5 py-0.5 rounded-full flex items-center gap-0.5 shadow-md border border-slate-700/30 whitespace-nowrap z-10">
        <svg xmlns="http://www.w3.org/2000/svg" width="8" height="8" viewBox="0 0 24 24" fill="currentColor" class="text-amber-400"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
        <span>${rating.toFixed(1)}</span>
      </div>
    `;
  } else {
    badgeHtml = `
      <div class="absolute -top-3.5 left-1/2 -translate-x-1/2 bg-slate-900/95 dark:bg-slate-950 text-white text-[8px] font-extrabold px-1.5 py-0.5 rounded-full shadow-md border border-slate-700/30 z-10">
        T${tier}
      </div>
    `;
  }

  // Teardrop Pin Marker HTML - Desain Kubah Garis Halus Modern (iOS/Android style)
  const htmlContent = `
    <div class="relative flex flex-col items-center select-none">
      <!-- Glow/Pulse Effect behind Recommended -->
      ${isRecommended ? `<span class="${pulseRing} scale-125"></span>` : ""}
      
      <!-- Rating / Tier Badge -->
      ${badgeHtml}

      <!-- Main Marker Pin (Teardrop shape style) -->
      <div class="relative w-8 h-8 rounded-full bg-gradient-to-tr ${pinColor} border-2 flex items-center justify-center shadow-lg hover:scale-125 hover:-translate-y-1 transition-all duration-300 z-0
        after:content-[''] after:absolute after:bottom-[-4px] after:left-1/2 after:-translate-x-1/2 after:w-0 after:h-0 after:border-l-[4px] after:border-l-transparent after:border-r-[4px] after:border-r-transparent after:border-t-[4px]
        ${isRecommended ? "after:border-t-orange-500" : 
          tier === "A" ? "after:border-t-amber-500" : 
          tier === "B" ? "after:border-t-emerald-500" : 
          tier === "C" ? "after:border-t-teal-500" : "after:border-t-slate-500"}
      ">
        <!-- Ikon Masjid Outline Modern & Elegan -->
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="w-4.5 h-4.5 filter drop-shadow-[0_1px_1px_rgba(0,0,0,0.15)]">
          <path d="M12 3v3"/>
          <path d="M12 6a6 6 0 0 0-6 6v7h12v-7a6 6 0 0 0-6-6z"/>
          <path d="M9 19v-4a3 3 0 0 1 6 0v4"/>
        </svg>
      </div>

      <!-- Facility Dots container -->
      <div class="absolute -bottom-2 flex gap-0.5 bg-white/95 dark:bg-slate-900/95 border border-slate-100 dark:border-slate-800 px-1 py-0.5 rounded-full shadow-md z-10 scale-90">
        ${dotsHtml || `<span class="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-700"></span>`}
      </div>
    </div>
  `;

  return L.divIcon({
    className: "custom-dynamic-mosque-marker",
    html: htmlContent,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -18]
  });
}

export default function MapComponent({ 
  center = [-6.2088, 106.8456],
  zoom = 13,
  markers = [],
  route = null,
  routeSegments = [],
  accessConnectors = [],
  routingMode = null,
  searchRadiusKm = 10,
  onMapClick = undefined
}: { 
  center?: [number, number],
  zoom?: number,
  markers?: any[],
  route?: [number, number][] | null,
  routeSegments?: [number, number][][],
  accessConnectors?: [number, number][][],
  routingMode?: string | null,
  searchRadiusKm?: number,
  onMapClick?: (e: L.LeafletMouseEvent) => void
}) {
  const [basemap, setBasemap] = useState("osm");
  const [tilesLoading, setTilesLoading] = useState(false);
  const [lowDataMode] = useState(() => {
    if (typeof navigator === "undefined") return false;
    const connection = (navigator as Navigator & {
      connection?: { saveData?: boolean; effectiveType?: string }
    }).connection;
    return Boolean(connection?.saveData || connection?.effectiveType === "2g" || connection?.effectiveType === "slow-2g" || connection?.effectiveType === "3g");
  });
  const effectiveZoom = lowDataMode ? Math.min(zoom, 12) : zoom;
  const effectiveRouteSegments = routeSegments.length > 0
    ? routeSegments.filter(segment => segment.length > 0)
    : route && route.length > 0 ? [route] : [];
  const routeBounds = effectiveRouteSegments.flat();
  const hasRoute = routeBounds.length > 0;

  const basemaps: Record<string, { url: string, name: string }> = {
    osm: {
      url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      name: "OSM"
    },
    positron: {
      url: "https://{s}.basemaps.cartocdn.com/rastertiles/light_all/{z}/{x}/{y}{r}.png",
      name: "Light"
    },
    voyager: {
      url: "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
      name: "Voyager"
    },
    dark: {
      url: "https://{s}.basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}{r}.png",
      name: "Dark"
    }
  };

  return (
    <div className="relative w-full h-full">
      <MapContainer 
        center={center} 
        zoom={effectiveZoom}
        className="w-full h-full z-0"
        zoomControl={false}
        preferCanvas
        zoomAnimation={!lowDataMode}
        fadeAnimation={!lowDataMode}
        markerZoomAnimation={!lowDataMode}
      >
        <ChangeMapView center={center} />
        <FitSearchRadius
          center={center}
          radiusKm={searchRadiusKm}
          enabled={!hasRoute}
          lowDataMode={lowDataMode}
        />
        <TileLayer
          url={basemaps[basemap].url}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          tileSize={lowDataMode ? 512 : 256}
          zoomOffset={lowDataMode ? -1 : 0}
          updateWhenIdle={lowDataMode}
          updateWhenZooming={!lowDataMode}
          keepBuffer={lowDataMode ? 0 : 2}
          eventHandlers={{
            loading: () => setTilesLoading(true),
            load: () => setTilesLoading(false),
            tileerror: () => setTilesLoading(false),
          }}
        />
        
        {markers.map((marker, idx) => {
          let markerIcon = StartIcon;
          if (marker.type === "destination") markerIcon = DestinationIcon;
          else if (marker.type === "mosque" || marker.type === "recommended") {
            markerIcon = createDynamicMosqueIcon(marker);
          }

          return (
            <Marker 
              key={`${marker.id || 'marker'}-${idx}`} 
              position={[marker.lat, marker.lng]} 
              icon={markerIcon}
              eventHandlers={marker.onClick ? { click: marker.onClick } : undefined}
            >
              {marker.popup && <Popup>{marker.popup}</Popup>}
            </Marker>
          );
        })}

        {!hasRoute && center && searchRadiusKm > 0 && (
          <Circle
            center={center}
            radius={searchRadiusKm * 1000}
            pathOptions={{
              color: "#059669",
              fillColor: "#10b981",
              fillOpacity: 0.035,
              opacity: 0.35,
              weight: 1.5,
              dashArray: "6 8",
            }}
            interactive={false}
          />
        )}

        {hasRoute && (
          <>
            {/* Rute Utama */}
            {effectiveRouteSegments.map((segment, index) => (
              <Polyline
                key={`road-segment-${index}`}
                positions={segment}
                color={routingMode === "local_approximation" ? "#64748b" : "#2563eb"}
                weight={routingMode === "local_approximation" ? 3 : 5}
                opacity={0.85}
                dashArray={routingMode === "local_approximation" ? "8, 8" : undefined}
              />
            ))}
            
            {/* Konektor Putus-putus untuk Snapping Celah (hanya jika mode jalan raya aktif) */}
            {routingMode !== "local_approximation" && accessConnectors.length > 0 && accessConnectors.map((segment, index) => (
              <Polyline
                key={`access-connector-${index}`}
                positions={segment}
                color="#64748b"
                weight={2.5}
                opacity={0.7}
                dashArray="4, 6"
              />
            ))}
            {routingMode !== "local_approximation" && accessConnectors.length === 0 && (() => {
              const startMarker = markers.find(m => m.type === "start");
              const destMarker = markers.find(m => m.type === "destination");
              const recMosqueMarker = markers.find(m => m.type === "recommended");
              
              const connectors = [];
              
              if (startMarker && routeBounds[0]) {
                const dist = Math.hypot(startMarker.lat - routeBounds[0][0], startMarker.lng - routeBounds[0][1]);
                if (dist > 0.0001) {
                  connectors.push(
                    <Polyline 
                      key="start-conn" 
                      positions={[[startMarker.lat, startMarker.lng], routeBounds[0]]}
                      color="#64748b" 
                      weight={2.5} 
                      opacity={0.65} 
                      dashArray="4, 6" 
                    />
                  );
                }
              }
              
              if (destMarker && routeBounds[routeBounds.length - 1]) {
                const dist = Math.hypot(destMarker.lat - routeBounds[routeBounds.length - 1][0], destMarker.lng - routeBounds[routeBounds.length - 1][1]);
                if (dist > 0.0001) {
                  connectors.push(
                    <Polyline 
                      key="dest-conn" 
                      positions={[routeBounds[routeBounds.length - 1], [destMarker.lat, destMarker.lng]]}
                      color="#64748b" 
                      weight={2.5} 
                      opacity={0.65} 
                      dashArray="4, 6" 
                    />
                  );
                }
              }
              
              if (recMosqueMarker) {
                let closestPoint = routeBounds[0];
                let minDistance = Infinity;
                for (const pt of routeBounds) {
                  const dist = Math.hypot(pt[0] - recMosqueMarker.lat, pt[1] - recMosqueMarker.lng);
                  if (dist < minDistance) {
                    minDistance = dist;
                    closestPoint = pt;
                  }
                }
                
                const distToMosque = Math.hypot(closestPoint[0] - recMosqueMarker.lat, closestPoint[1] - recMosqueMarker.lng);
                if (distToMosque > 0.0001) {
                  connectors.push(
                    <Polyline 
                      key="mosque-conn" 
                      positions={[closestPoint, [recMosqueMarker.lat, recMosqueMarker.lng]]} 
                      color="#64748b" 
                      weight={2.5} 
                      opacity={0.65} 
                      dashArray="4, 6" 
                    />
                  );
                }
              }
              
              return connectors;
            })()}
            
            <FitBounds bounds={routeBounds} lowDataMode={lowDataMode} />
          </>
        )}

        <MapEvents onClick={onMapClick} />
      </MapContainer>

      {tilesLoading && hasRoute && (
        <div className="pointer-events-none absolute left-1/2 top-4 z-[1000] -translate-x-1/2 rounded-full border border-emerald-200 bg-white/95 px-3 py-1.5 text-[11px] font-semibold text-emerald-800 shadow-lg backdrop-blur dark:border-emerald-900 dark:bg-slate-900/95 dark:text-emerald-300">
          Rute sudah siap · detail peta masih dimuat
        </div>
      )}

      {/* Floating Basemap Selector */}
      <div className="absolute bottom-4 right-4 z-[1000] bg-white/70 dark:bg-slate-900/75 backdrop-blur-md border border-slate-200/50 dark:border-slate-800/50 p-1.5 rounded-2xl shadow-xl flex gap-1 pointer-events-auto transition-all">
        {Object.entries(basemaps).map(([key, bm]) => (
          <button
            key={key}
            onClick={() => setBasemap(key)}
            className={`px-3 py-1.5 text-[10px] font-bold rounded-xl transition-all duration-300 ${
              basemap === key 
                ? "bg-emerald-600 dark:bg-emerald-500 text-white shadow-md shadow-emerald-600/20" 
                : "text-slate-600 dark:text-slate-400 hover:bg-slate-100/50 dark:hover:bg-slate-800/50 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            {bm.name}
          </button>
        ))}
      </div>
    </div>
  );
}
