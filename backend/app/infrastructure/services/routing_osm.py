from __future__ import annotations

import datetime as dt
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import re
import networkx as nx
import requests

from typing import Callable


from .osm_graph import (
    DEFAULT_GRAPHML,
    astar_path,
    bbox_area_km2,
    bbox_from_points,
    build_osm_graph_for_route,
    graph_bounds,
    graph_covers_points,
    dijkstra_path,
    haversine_km,
    load_road_graph,
    nearest_road_node,
    path_length_m,
    path_travel_time_s,
    route_nodes_to_latlon,
)

Coordinate = Tuple[float, float]
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"
MAX_INLINE_AUTO_BUILD_AREA_KM2 = 90.0


def _parse_hhmm(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        t = dt.datetime.strptime(value, "%H:%M").time()
        today = dt.date.today()
        return dt.datetime.combine(today, t)
    except Exception:
        return None


def _distance_point_to_segment_km(p: Coordinate, a: Coordinate, b: Coordinate) -> float:
    """Approximate distance from point p to segment a-b in km using local equirectangular projection."""
    lat0 = math.radians((a[0] + b[0] + p[0]) / 3)
    def project(x: Coordinate):
        return (x[1] * math.cos(lat0) * 111.0, x[0] * 111.0)
    px, py = project(p)
    ax, ay = project(a)
    bx, by = project(b)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def select_candidate_mosques(
    mosques: Sequence[Dict[str, Any]],
    start: Coordinate,
    end: Coordinate,
    limit: int = 8,
    corridor_km: float = 8.0,
    fallback_radius_km: float = 50.0,
) -> List[Dict[str, Any]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    min_lat, max_lat = sorted([start[0], end[0]])
    min_lon, max_lon = sorted([start[1], end[1]])
    buffer_deg = corridor_km / 100.0

    # Pre-project start and end coordinates
    lat0 = math.radians((start[0] + end[0]) / 2.0)
    cos_factor = math.cos(lat0) * 111.0
    
    ax = start[1] * cos_factor
    ay = start[0] * 111.0
    bx = end[1] * cos_factor
    by = end[0] * 111.0
    
    dx = bx - ax
    dy = by - ay
    segment_len_sq = dx * dx + dy * dy

    for m in mosques:
        lat, lon = float(m["latitude"]), float(m["longitude"])
        if not (min_lat - buffer_deg <= lat <= max_lat + buffer_deg and min_lon - buffer_deg <= lon <= max_lon + buffer_deg):
            continue
            
        # Fast distance to segment calculation using precomputed projection
        px = lon * cos_factor
        py = lat * 111.0
        
        if segment_len_sq == 0:
            d_line = math.hypot(px - ax, py - ay)
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / segment_len_sq))
            cx = ax + t * dx
            cy = ay + t * dy
            d_line = math.hypot(px - cx, py - cy)

        if d_line > corridor_km:
            continue
            
        d_start = haversine_km(start[0], start[1], lat, lon)
        d_end = haversine_km(lat, lon, end[0], end[1])
        priority = float(m.get("priority_score", 0.5))
        # Smaller is better: near route, not too far from start/end, high quality.
        rank_score = 0.55 * d_line + 0.25 * min(d_start, d_end) - 2.0 * priority
        scored.append((rank_score, m))

    if not scored:
        # Fallback: nearest mosque to start, but keep it inside a reasonable
        # dataset radius so wrong-region selections do not look authoritative.
        for m in mosques:
            d_start = haversine_km(start[0], start[1], float(m["latitude"]), float(m["longitude"]))
            if d_start <= fallback_radius_km:
                scored.append((d_start, m))

    scored.sort(key=lambda x: x[0])
    return [m for _, m in scored[:limit]]


def _safe_shortest_path(G, source, target, algorithm: str, weight: str = "travel_time"):
    if algorithm.lower() in {"astar", "a*"}:
        return astar_path(G, source, target, weight=weight)
    return dijkstra_path(G, source, target, weight=weight)


def _normalise_values(values: List[float]) -> List[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _inline_auto_build_skip_note(points: Sequence[Coordinate], buffer_km: float) -> Optional[str]:
    north, south, east, west = bbox_from_points(points, buffer_km=buffer_km)
    area_km2 = bbox_area_km2(north, south, east, west)
    if area_km2 <= MAX_INLINE_AUTO_BUILD_AREA_KM2:
        return None
    return (
        "Auto-build graph dilewati agar tombol Cari Rute tidak loading terlalu lama "
        f"(estimasi area {area_km2:.0f} km2, batas route cepat {MAX_INLINE_AUTO_BUILD_AREA_KM2:.0f} km2). "
        "Gunakan tombol Bangun Graph OSM Manual untuk membuat cache Dijkstra lokal area ini."
    )


def _prayer_arrival_details(arrival_minutes: float, current_time: Optional[str], prayer_time: Optional[str]) -> Tuple[float, str, float]:
    current_dt = _parse_hhmm(current_time)
    prayer_dt = _parse_hhmm(prayer_time)
    if current_dt is None or prayer_dt is None:
        return 0.3, "unknown", 0.0
    if prayer_dt < current_dt:
        prayer_dt += dt.timedelta(days=1)
    arrival_dt = current_dt + dt.timedelta(minutes=arrival_minutes)
    if arrival_dt > prayer_dt:
        late = (arrival_dt - prayer_dt).total_seconds() / 60
        penalty = min(1.0, 0.6 + late / 30.0)
        return penalty, "after_prayer", -late
    before = (prayer_dt - arrival_dt).total_seconds() / 60
    if 0 <= before <= 25:
        penalty = 0.0
    else:
        penalty = min(0.5, before / 90.0)
    return penalty, "before_prayer", before


def _prayer_penalty(arrival_minutes: float, current_time: Optional[str], prayer_time: Optional[str]) -> float:
    penalty, _, _ = _prayer_arrival_details(arrival_minutes, current_time, prayer_time)
    return penalty



def _interpolate_segment(a: Coordinate, b: Coordinate, steps: int = 12) -> List[Coordinate]:
    return [
        (
            a[0] + (b[0] - a[0]) * i / steps,
            a[1] + (b[1] - a[1]) * i / steps,
        )
        for i in range(steps + 1)
    ]


def _local_route_coordinates(start: Coordinate, mosque: Coordinate, end: Coordinate) -> List[Coordinate]:
    first = _interpolate_segment(start, mosque)
    second = _interpolate_segment(mosque, end)
    return first + second[1:]


def _douglas_peucker(points: List[Coordinate], epsilon_km: float) -> List[Coordinate]:
    if len(points) < 3:
        return points
    dmax = 0.0
    index = 0
    end = len(points) - 1
    for i in range(1, end):
        d = _distance_point_to_segment_km(points[i], points[0], points[end])
        if d > dmax:
            index = i
            dmax = d
    if dmax > epsilon_km:
        left = _douglas_peucker(points[:index+1], epsilon_km)
        right = _douglas_peucker(points[index:], epsilon_km)
        return left[:-1] + right
    else:
        return [points[0], points[end]]


def _encode_polyline(points: List[Coordinate]) -> str:
    """Encode a list of (latitude, longitude) coordinates into a Google Polyline string."""
    encoded = []
    last_lat = 0
    last_lon = 0
    
    for lat, lon in points:
        lat_val = int(round(lat * 1e5))
        lon_val = int(round(lon * 1e5))
        
        delta_lat = lat_val - last_lat
        delta_lon = lon_val - last_lon
        
        last_lat = lat_val
        last_lon = lon_val
        
        for val in (delta_lat, delta_lon):
            val = ~(val << 1) if val < 0 else (val << 1)
            while val >= 0x20:
                encoded.append(chr((0x20 | (val & 0x1f)) + 63))
                val >>= 5
            encoded.append(chr(val + 63))
            
    return "".join(encoded)


def _format_route_response(
    *,
    algorithm_label: str,
    road_network: str,
    routing_weight: str,
    dataset_id: Optional[str],
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    requested_candidates: int,
    results: List[Dict[str, Any]],
    elapsed_ms: float,
    reason: str,
) -> Dict[str, Any]:
    results.sort(key=lambda x: x["multi_objective_score"])
    best = results[0]
    best_m = best["mosque"]
    used_osrm_fallback = algorithm_label == "OSRM Road Route"
    is_local_approximation = algorithm_label == "Local Approximation"
    routing_mode = "osrm_fallback" if used_osrm_fallback else "local_approximation" if is_local_approximation else "local_graph"
    graph_source = "osrm_public_api" if used_osrm_fallback else "none" if is_local_approximation else "osm_graphml_cache"

    return {
        "algorithm": algorithm_label,
        "dataset_id": dataset_id,
        "routing_mode": routing_mode,
        "graph_source": graph_source,
        "used_osrm_fallback": used_osrm_fallback,
        "road_network": road_network,
        "routing_weight": routing_weight,
        "candidate_count": len(results),
        "execution_time_ms": elapsed_ms,
        "start": {"latitude": start_lat, "longitude": start_lon},
        "destination": {"latitude": end_lat, "longitude": end_lon},
        "recommended_mosque": best_m,
        "encoded_polyline": _encode_polyline(best["route_coordinates"]),
        "route_summary": {
            "distance_km": best["distance_km"],
            "estimated_time_minutes": best["estimated_time_minutes"],
            "arrival_to_mosque_minutes": best["arrival_to_mosque_minutes"],
            "arrival_status": best.get("arrival_status", "unknown"),
            "minutes_before_prayer": best.get("minutes_before_prayer", 0.0),
            "multi_objective_score": best["multi_objective_score"],
            "route_nodes_count": best["route_nodes_count"],
            "reason": reason,
        },
        "route_geojson": {
            "type": "Feature",
            "properties": {
                "algorithm": algorithm_label,
                "mosque_name": best_m.get("name"),
                "distance_km": best["distance_km"],
                "estimated_time_minutes": best["estimated_time_minutes"],
            },
            "geometry": {
                "type": "LineString",
                # Simplification dengan toleransi 15 meter (0.015 km) untuk optimasi jaringan 3G/4G
                "coordinates": [[lon, lat] for lat, lon in _douglas_peucker(best["route_coordinates"], 0.015)],
            },
        },
        "candidate_mosques": [
            {
                "id": r["mosque"]["id"],
                "name": r["mosque"]["name"],
                "latitude": r["mosque"]["latitude"],
                "longitude": r["mosque"]["longitude"],
                "tier": r["mosque"].get("tier"),
                "capacity_proxy": r["mosque"].get("capacity_proxy"),
                "priority_score": r["priority_score"],
                "distance_km": r["distance_km"],
                "estimated_time_minutes": r["estimated_time_minutes"],
                "arrival_status": r.get("arrival_status", "unknown"),
                "minutes_before_prayer": r.get("minutes_before_prayer", 0.0),
                "multi_objective_score": r["multi_objective_score"],
            }
            for r in results[:requested_candidates]
        ],
    }


def _route_via_local_approximation(
    *,
    start: Coordinate,
    end: Coordinate,
    candidates: Sequence[Dict[str, Any]],
    requested_candidates: int,
    current_time: Optional[str],
    prayer_time: Optional[str],
    dataset_id: Optional[str],
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    fallback_note: str,
    profile: str = "balanced",
) -> Dict[str, Any]:
    start_clock = time.perf_counter()
    results: List[Dict[str, Any]] = []
    detour_factor = 1.25
    average_speed_kmh = 30.0

    for mosque in candidates:
        mlat, mlon = float(mosque["latitude"]), float(mosque["longitude"])
        d1 = haversine_km(start[0], start[1], mlat, mlon) * detour_factor
        d2 = haversine_km(mlat, mlon, end[0], end[1]) * detour_factor
        dist_km = d1 + d2
        time_minutes = (dist_km / average_speed_kmh) * 60.0
        to_mosque_minutes = (d1 / average_speed_kmh) * 60.0
        capacity_num = {"large": 1.0, "medium": 0.65, "small": 0.35}.get(mosque.get("capacity_proxy"), 0.5)
        priority = float(mosque.get("priority_score", 0.5))
        prayer_penalty, arrival_status, minutes_before_prayer = _prayer_arrival_details(to_mosque_minutes, current_time, prayer_time)
        coords = _local_route_coordinates(start, (mlat, mlon), end)

        results.append({
            "mosque": mosque,
            "distance_km": round(dist_km, 3),
            "estimated_time_minutes": round(time_minutes, 2),
            "arrival_to_mosque_minutes": round(to_mosque_minutes, 2),
            "route_nodes_count": len(coords),
            "capacity_score": capacity_num,
            "priority_score": priority,
            "prayer_penalty": prayer_penalty,
            "arrival_status": arrival_status,
            "minutes_before_prayer": round(minutes_before_prayer, 1),
            "route_coordinates": coords,
        })

    if not results:
        raise RuntimeError("Tidak ada kandidat masjid lokal yang dapat dievaluasi.")

    # Dynamic weighting based on profile
    weights = {
        "fastest": {
            "time": 0.70, "dist": 0.10, "prayer": 0.10, "capacity": 0.05, "priority": 0.05
        },
        "prayer_priority": {
            "time": 0.20, "dist": 0.10, "prayer": 0.60, "capacity": 0.05, "priority": 0.05
        },
        "low_cost": {
            "time": 0.20, "dist": 0.60, "prayer": 0.10, "capacity": 0.05, "priority": 0.05
        },
        "balanced": {
            "time": 0.40, "dist": 0.20, "prayer": 0.20, "capacity": 0.10, "priority": 0.10
        }
    }
    
    prof_weights = weights.get(profile.lower(), weights["balanced"])

    time_norm = _normalise_values([r["estimated_time_minutes"] for r in results])
    dist_norm = _normalise_values([r["distance_km"] for r in results])
    for i, r in enumerate(results):
        capacity_penalty = 1.0 - r["capacity_score"]
        priority_penalty = 1.0 - r["priority_score"]
        r["multi_objective_score"] = round(
            prof_weights["time"] * time_norm[i]
            + prof_weights["dist"] * dist_norm[i]
            + prof_weights["prayer"] * r["prayer_penalty"]
            + prof_weights["capacity"] * capacity_penalty
            + prof_weights["priority"] * priority_penalty,
            4,
        )

    elapsed_ms = round((time.perf_counter() - start_clock) * 1000, 2)
    return _format_route_response(
        algorithm_label="Local Approximation",
        road_network="ArangoDB local mosque data + straight-line fallback",
        routing_weight="estimated_distance_time_proxy",
        dataset_id=dataset_id,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        requested_candidates=requested_candidates,
        results=results,
        elapsed_ms=elapsed_ms,
        reason=(
            "Rute dibuat lokal tanpa download Overpass karena graph jalan OSM belum tersedia atau gagal dibangun. "
            "Garis rute adalah estimasi start -> masjid -> tujuan, bukan turn-by-turn jalan OSM. "
            f"Catatan teknis: {fallback_note}"
        ),
    )


def _osrm_route(start: Coordinate, mosque: Coordinate, end: Coordinate) -> Dict[str, Any]:
    direct_to_mosque = haversine_km(mosque[0], mosque[1], end[0], end[1]) < 0.01
    if direct_to_mosque:
        coords = f"{start[1]},{start[0]};{mosque[1]},{mosque[0]}"
    else:
        coords = (
            f"{start[1]},{start[0]};"
            f"{mosque[1]},{mosque[0]};"
            f"{end[1]},{end[0]}"
        )
    url = f"{OSRM_ROUTE_URL}/{coords}"
    response = requests.get(
        url,
        params={
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
            "annotations": "false",
        },
        timeout=6,
    )
    response.raise_for_status()
    payload = response.json()
    routes = payload.get("routes") or []
    if not routes:
        raise RuntimeError(payload.get("message") or "OSRM tidak mengembalikan rute.")
    route = routes[0]
    waypoints = payload.get("waypoints") or []
    distance_to_mosque_m = None
    duration_to_mosque_s = None
    if direct_to_mosque:
        distance_to_mosque_m = float(route.get("distance", 0.0))
        duration_to_mosque_s = float(route.get("duration", 0.0))
    elif len(waypoints) >= 3:
        legs = route.get("legs") or []
        if legs:
            distance_to_mosque_m = float(legs[0].get("distance", 0.0))
            duration_to_mosque_s = float(legs[0].get("duration", 0.0))
    return {
        "distance_m": float(route.get("distance", 0.0)),
        "duration_s": float(route.get("duration", 0.0)),
        "distance_to_mosque_m": distance_to_mosque_m,
        "duration_to_mosque_s": duration_to_mosque_s,
        "coordinates": [
            (float(lat), float(lon))
            for lon, lat in route.get("geometry", {}).get("coordinates", [])
        ],
    }


def _route_via_osrm_fallback(
    *,
    start: Coordinate,
    end: Coordinate,
    candidates: Sequence[Dict[str, Any]],
    requested_candidates: int,
    current_time: Optional[str],
    prayer_time: Optional[str],
    dataset_id: Optional[str],
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    fallback_note: str,
    profile: str = "balanced",
) -> Dict[str, Any]:
    start_clock = time.perf_counter()
    results: List[Dict[str, Any]] = []
    candidate_pool = candidates[: min(len(candidates), max(requested_candidates, 6))]
    last_error = ""

    from concurrent.futures import ThreadPoolExecutor

    def _fetch_route_safe(mosque):
        try:
            mlat, mlon = float(mosque["latitude"]), float(mosque["longitude"])
            route = _osrm_route(start, (mlat, mlon), end)
            return mosque, route, None
        except Exception as exc:
            return mosque, None, str(exc)

    with ThreadPoolExecutor(max_workers=len(candidate_pool)) as executor:
        fetched = list(executor.map(_fetch_route_safe, candidate_pool))

    for mosque, route, err in fetched:
        if err:
            last_error = err
            continue
        if not route:
            continue

        mlat, mlon = float(mosque["latitude"]), float(mosque["longitude"])
        dist_km = route["distance_m"] / 1000.0
        time_minutes = route["duration_s"] / 60.0
        if route["duration_to_mosque_s"] is not None:
            to_mosque_minutes = route["duration_to_mosque_s"] / 60.0
        else:
            d1 = haversine_km(start[0], start[1], mlat, mlon) * 1.25
            to_mosque_minutes = (d1 / 30.0) * 60.0

        capacity_num = {"large": 1.0, "medium": 0.65, "small": 0.35}.get(mosque.get("capacity_proxy"), 0.5)
        priority = float(mosque.get("priority_score", 0.5))
        prayer_penalty, arrival_status, minutes_before_prayer = _prayer_arrival_details(to_mosque_minutes, current_time, prayer_time)
        coords = route["coordinates"] or _local_route_coordinates(start, (mlat, mlon), end)

        results.append({
            "mosque": mosque,
            "distance_km": round(dist_km, 3),
            "estimated_time_minutes": round(time_minutes, 2),
            "arrival_to_mosque_minutes": round(to_mosque_minutes, 2),
            "route_nodes_count": len(coords),
            "capacity_score": capacity_num,
            "priority_score": priority,
            "prayer_penalty": prayer_penalty,
            "arrival_status": arrival_status,
            "minutes_before_prayer": round(minutes_before_prayer, 1),
            "route_coordinates": coords,
        })

    if not results:
        return _route_via_local_approximation(
            start=start,
            end=end,
            candidates=candidates,
            requested_candidates=requested_candidates,
            current_time=current_time,
            prayer_time=prayer_time,
            dataset_id=dataset_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            fallback_note=f"OSRM juga gagal ({last_error or 'tidak ada rute'}). {fallback_note}",
            profile=profile,
        )

    # Dynamic weighting based on profile
    weights = {
        "fastest": {
            "time": 0.70, "dist": 0.10, "prayer": 0.10, "capacity": 0.05, "priority": 0.05
        },
        "prayer_priority": {
            "time": 0.20, "dist": 0.10, "prayer": 0.60, "capacity": 0.05, "priority": 0.05
        },
        "low_cost": {
            "time": 0.20, "dist": 0.60, "prayer": 0.10, "capacity": 0.05, "priority": 0.05
        },
        "balanced": {
            "time": 0.40, "dist": 0.20, "prayer": 0.20, "capacity": 0.10, "priority": 0.10
        }
    }
    
    prof_weights = weights.get(profile.lower(), weights["balanced"])

    time_norm = _normalise_values([r["estimated_time_minutes"] for r in results])
    dist_norm = _normalise_values([r["distance_km"] for r in results])
    for i, r in enumerate(results):
        capacity_penalty = 1.0 - r["capacity_score"]
        priority_penalty = 1.0 - r["priority_score"]
        r["multi_objective_score"] = round(
            prof_weights["time"] * time_norm[i]
            + prof_weights["dist"] * dist_norm[i]
            + prof_weights["prayer"] * r["prayer_penalty"]
            + prof_weights["capacity"] * capacity_penalty
            + prof_weights["priority"] * priority_penalty,
            4,
        )

    elapsed_ms = round((time.perf_counter() - start_clock) * 1000, 2)
    # Formulasi alasan (reason) secara dinamis agar lebih akurat & ramah pengguna
    if "dilewati" in fallback_note or "batas" in fallback_note:
        reason_text = (
            "Rute perjalanan dihitung menggunakan OSRM karena rute berada di luar cakupan peta jalan lokal yang aktif, "
            "dan pembuatan peta otomatis dilewati agar waktu muat pencarian rute tetap instan."
        )
    elif "tidak menemukan path" in fallback_note or "No path" in fallback_note:
        reason_text = (
            "Rute dihitung menggunakan OSRM karena algoritma Dijkstra lokal tidak menemukan jalur terhubung "
            "pada peta jalan lokal yang aktif."
        )
    else:
        reason_text = "Rute dihitung menggunakan OSRM karena peta jalan OpenStreetMap lokal belum tersedia untuk dataset ini."

    reason_text += f" Masjid tetap dipilih dari database ArangoDB lokal dan dievaluasi dengan skor multi-objective ({profile}). Catatan teknis: {fallback_note}"

    return _format_route_response(
        algorithm_label="OSRM Road Route",
        road_network="OSRM public road routing",
        routing_weight="osrm_duration_seconds",
        dataset_id=dataset_id,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        requested_candidates=requested_candidates,
        results=results,
        elapsed_ms=elapsed_ms,
        reason=reason_text,
    )


def route_to_mosque(
    *,
    start_lat: float,
    start_lon: float,
    mosque: Dict[str, Any],
    algorithm: str = "dijkstra",
    auto_build_osm: bool = False,
    buffer_km: float = 6.0,
    graphml_path: Path = DEFAULT_GRAPHML,
    dataset_id: Optional[str] = None,
    fetch_mosques_fn: Optional[Callable] = None,
    save_osm_cache_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    start = (float(start_lat), float(start_lon))
    mosque_point = (float(mosque["latitude"]), float(mosque["longitude"]))
    requested_candidates = 1

    if start == mosque_point:
        raise ValueError("Titik awal dan masjid tujuan tidak boleh sama.")

    G = None
    if not graphml_path.exists() and not auto_build_osm:
        return _route_via_osrm_fallback(
            start=start,
            end=mosque_point,
            candidates=[mosque],
            requested_candidates=requested_candidates,
            current_time=None,
            prayer_time=None,
            dataset_id=dataset_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=mosque_point[0],
            end_lon=mosque_point[1],
            fallback_note="Cache graph OSM lokal belum ada, sehingga Dijkstra lokal belum dapat dijalankan.",
        )

    if auto_build_osm:
        try:
            G = load_road_graph(graphml_path) if graphml_path.exists() else None
            cache_ready = G is not None and graph_covers_points(G, [start, mosque_point], margin_km=0.5)
        except FileNotFoundError:
            G = None
            cache_ready = False
        if not cache_ready:
            skip_note = _inline_auto_build_skip_note([start, mosque_point], max(float(buffer_km), 5.0))
            if skip_note:
                return _route_via_osrm_fallback(
                    start=start,
                    end=mosque_point,
                    candidates=[mosque],
                    requested_candidates=requested_candidates,
                    current_time=None,
                    prayer_time=None,
                    dataset_id=dataset_id,
                    start_lat=start_lat,
                    start_lon=start_lon,
                    end_lat=mosque_point[0],
                    end_lon=mosque_point[1],
                    fallback_note=skip_note,
                )
            try:
                G = build_osm_graph_for_route(
                    start_lat=start_lat,
                    start_lon=start_lon,
                    end_lat=mosque_point[0],
                    end_lon=mosque_point[1],
                    buffer_km=max(float(buffer_km), 5.0),
                    output_graphml=graphml_path,
                )
            except Exception as exc:
                return _route_via_osrm_fallback(
                    start=start,
                    end=mosque_point,
                    candidates=[mosque],
                    requested_candidates=requested_candidates,
                    current_time=None,
                    prayer_time=None,
                    dataset_id=dataset_id,
                    start_lat=start_lat,
                    start_lon=start_lon,
                    end_lat=mosque_point[0],
                    end_lon=mosque_point[1],
                    fallback_note=f"Build/download OSM gagal: {exc}",
                )
            if save_osm_cache_fn:
                save_osm_cache_fn(
                graphml_path=graphml_path,
                bounds=graph_bounds(G),
                buffer_km=max(float(buffer_km), 5.0),
                network_type="drive",
                nodes=len(G.nodes),
                edges=len(G.edges),
            )
    else:
        G = load_road_graph(graphml_path)

    if not graph_covers_points(G, [start, mosque_point], margin_km=0.5):
        return _route_via_osrm_fallback(
            start=start,
            end=mosque_point,
            candidates=[mosque],
            requested_candidates=requested_candidates,
            current_time=None,
            prayer_time=None,
            dataset_id=dataset_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=mosque_point[0],
            end_lon=mosque_point[1],
            fallback_note="Cache graph OSM lokal belum mencakup titik awal atau masjid tujuan.",
        )

    start_clock = time.perf_counter()
    try:
        import osmnx as ox
        try:
            nearest_nodes = ox.distance.nearest_nodes(G, X=[start_lon, mosque_point[1]], Y=[start_lat, mosque_point[0]])
            start_node = nearest_nodes[0]
            mosque_node = nearest_nodes[1]
        except Exception:
            start_node = nearest_road_node(G, start_lat, start_lon)
            mosque_node = nearest_road_node(G, mosque_point[0], mosque_point[1])
        route_nodes = _safe_shortest_path(G, start_node, mosque_node, algorithm=algorithm, weight="travel_time")
    except (nx.NetworkXNoPath, nx.NodeNotFound, ValueError) as exc:
        return _route_via_osrm_fallback(
            start=start,
            end=mosque_point,
            candidates=[mosque],
            requested_candidates=requested_candidates,
            current_time=None,
            prayer_time=None,
            dataset_id=dataset_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=mosque_point[0],
            end_lon=mosque_point[1],
            fallback_note=f"Dijkstra lokal tidak menemukan path: {exc}",
        )

    dist_m = path_length_m(G, route_nodes)
    time_s = path_travel_time_s(G, route_nodes)
    coords = route_nodes_to_latlon(G, route_nodes)
    capacity_num = {"large": 1.0, "medium": 0.65, "small": 0.35}.get(mosque.get("capacity_proxy"), 0.5)
    priority = float(mosque.get("priority_score", 0.5))
    result = {
        "mosque": mosque,
        "distance_km": round(dist_m / 1000, 3),
        "estimated_time_minutes": round(time_s / 60, 2),
        "arrival_to_mosque_minutes": round(time_s / 60, 2),
        "route_nodes_count": len(route_nodes),
        "capacity_score": capacity_num,
        "priority_score": priority,
        "prayer_penalty": 0.0,
        "route_coordinates": coords,
        "multi_objective_score": round(0.10 * (1.0 - capacity_num) + 0.10 * (1.0 - priority), 4),
    }
    elapsed_ms = round((time.perf_counter() - start_clock) * 1000, 2)
    return _format_route_response(
        algorithm_label="A*" if algorithm.lower() in {"astar", "a*"} else "Dijkstra",
        road_network="OpenStreetMap via OSMnx/NetworkX",
        routing_weight="travel_time_seconds",
        dataset_id=dataset_id,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=mosque_point[0],
        end_lon=mosque_point[1],
        requested_candidates=requested_candidates,
        results=[result],
        elapsed_ms=elapsed_ms,
        reason="Rute tercepat menuju masjid terpilih dihitung dengan Dijkstra/A* pada graph jalan OpenStreetMap lokal.",
    )


def route_via_osm_dijkstra(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    algorithm: str = "dijkstra",
    current_time: Optional[str] = None,
    prayer_time: Optional[str] = None,
    max_candidates: int = 6,
    auto_build_osm: bool = False,
    buffer_km: float = 6.0,
    graphml_path: Path = DEFAULT_GRAPHML,
    dataset_id: Optional[str] = None,
    fetch_mosques_fn: Optional[Callable] = None,
    save_osm_cache_fn: Optional[Callable] = None,
    profile: str = "balanced",
) -> Dict[str, Any]:
    start = (float(start_lat), float(start_lon))
    end = (float(end_lat), float(end_lon))
    is_one_way = (start == end)

    # Resolve prayer name to HH:MM time
    if prayer_time and prayer_time.lower() in {"fajr", "subuh", "dhuhr", "dzuhur", "asr", "ashar", "maghrib", "isha", "isya"}:
        resolved_name = prayer_time.lower()
        name_map = {
            "subuh": "fajr",
            "dzuhur": "dhuhr",
            "ashar": "asr",
            "isya": "isha"
        }
        api_name = name_map.get(resolved_name, resolved_name)
        from app.infrastructure.services.prayer_time import get_prayer_times
        import datetime as dt_module
        try:
            pt_data = get_prayer_times(float(start_lat), float(start_lon), dt_module.date.today())
            raw_time = pt_data["timings"].get(api_name)
            if raw_time:
                match = re.search(r"\d{2}:\d{2}", raw_time)
                prayer_time = match.group(0) if match else raw_time
            else:
                prayer_time = None
        except Exception:
            fallback_map = {
                "fajr": "04:45",
                "dhuhr": "12:00",
                "asr": "15:15",
                "maghrib": "18:00",
                "isha": "19:15"
            }
            prayer_time = fallback_map.get(api_name, "18:00")

    requested_candidates = max(1, int(max_candidates))
    effective_corridor_km = max(float(buffer_km), 5.0)

    # Calculate combined bounding box for database query optimization
    min_lat, max_lat = sorted([start[0], end[0]])
    min_lon, max_lon = sorted([start[1], end[1]])
    buffer_deg = effective_corridor_km / 100.0
    fallback_radius_km = max(25.0, effective_corridor_km * 4)
    fallback_buffer_lat = fallback_radius_km / 111.0
    fallback_buffer_lon = fallback_radius_km / (111.0 * max(math.cos(math.radians(start[0])), 0.2))

    south_combined = min(min_lat - buffer_deg, start[0] - fallback_buffer_lat)
    north_combined = max(max_lat + buffer_deg, start[0] + fallback_buffer_lat)
    west_combined = min(min_lon - buffer_deg, start[1] - fallback_buffer_lon)
    east_combined = max(max_lon + buffer_deg, start[1] + fallback_buffer_lon)
    bounds_query = (south_combined, north_combined, west_combined, east_combined)

    mosques = []
    if fetch_mosques_fn:
        import inspect
        try:
            sig = inspect.signature(fetch_mosques_fn)
            if "bounds" in sig.parameters:
                mosques = fetch_mosques_fn(dataset_id, bounds=bounds_query)
            else:
                mosques = fetch_mosques_fn(dataset_id)
        except Exception:
            mosques = fetch_mosques_fn(dataset_id)
    else:
        mosques = []

    if not mosques:
        raise ValueError("Dataset aktif tidak memiliki data masjid yang valid.")

    evaluation_limit = min(len(mosques), max(12, requested_candidates * 3))
    candidates = select_candidate_mosques(
        mosques,
        start,
        end,
        limit=evaluation_limit,
        corridor_km=effective_corridor_km,
        fallback_radius_km=fallback_radius_km,
    )
    if not candidates:
        raise ValueError(
            "Tidak ada kandidat masjid yang masuk koridor/radius pencarian. "
            "Pastikan dataset aktif sesuai wilayah titik awal dan tujuan, atau perbesar Buffer OSM."
        )
    build_candidate_limit = min(len(candidates), max(requested_candidates, 3))
    build_candidate_points = [
        (float(m["latitude"]), float(m["longitude"]))
        for m in candidates[:build_candidate_limit]
    ]
    points_to_cover = [start, end] + build_candidate_points

    G = None
    if not graphml_path.exists() and not auto_build_osm:
        return _route_via_osrm_fallback(
            start=start,
            end=end,
            candidates=candidates,
            requested_candidates=requested_candidates,
            current_time=current_time,
            prayer_time=prayer_time,
            dataset_id=dataset_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            fallback_note="Cache graph OSM lokal belum ada. Gunakan tombol Bangun Graph OSM Manual untuk membuat rute jalan OSM saat koneksi Overpass stabil.",
            profile=profile,
        )

    if auto_build_osm:
        # Reuse a matching cache. Rebuilding on every click makes routing feel
        # stuck because OSMnx must query Overpass and simplify a fresh graph.
        try:
            G = load_road_graph(graphml_path) if graphml_path.exists() else None
            cache_ready = G is not None and graph_covers_points(G, points_to_cover, margin_km=0.5)
        except FileNotFoundError:
            G = None
            cache_ready = False

        if not cache_ready:
            skip_note = _inline_auto_build_skip_note(points_to_cover, effective_corridor_km)
            if skip_note:
                return _route_via_osrm_fallback(
                    start=start,
                    end=end,
                    candidates=candidates,
                    requested_candidates=requested_candidates,
                    current_time=current_time,
                    prayer_time=prayer_time,
                    dataset_id=dataset_id,
                    start_lat=start_lat,
                    start_lon=start_lon,
                    end_lat=end_lat,
                    end_lon=end_lon,
                    fallback_note=skip_note,
                    profile=profile,
                )
            try:
                G = build_osm_graph_for_route(
                    start_lat=start_lat,
                    start_lon=start_lon,
                    end_lat=end_lat,
                    end_lon=end_lon,
                    candidate_points=build_candidate_points,
                    buffer_km=effective_corridor_km,
                    output_graphml=graphml_path,
                )
            except Exception as exc:
                return _route_via_osrm_fallback(
                    start=start,
                    end=end,
                    candidates=candidates,
                    requested_candidates=requested_candidates,
                    current_time=current_time,
                    prayer_time=prayer_time,
                    dataset_id=dataset_id,
                    start_lat=start_lat,
                    start_lon=start_lon,
                    end_lat=end_lat,
                    end_lon=end_lon,
                    fallback_note=f"Build/download OSM gagal: {exc}",
                    profile=profile,
                )
            if save_osm_cache_fn:
                save_osm_cache_fn(
                graphml_path=graphml_path,
                bounds=graph_bounds(G),
                buffer_km=effective_corridor_km,
                network_type="drive",
                nodes=len(G.nodes),
                edges=len(G.edges),
            )
    else:
        G = load_road_graph(graphml_path)
    if not graph_covers_points(G, [start, end], margin_km=0.5):
        south, north, west, east = graph_bounds(G)
        return _route_via_osrm_fallback(
            start=start,
            end=end,
            candidates=candidates,
            requested_candidates=requested_candidates,
            current_time=current_time,
            prayer_time=prayer_time,
            dataset_id=dataset_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            fallback_note=(
                "Cache graph OSM tidak mencakup titik awal/tujuan saat ini "
                f"(bounds cache: S {south:.4f}, N {north:.4f}, W {west:.4f}, E {east:.4f})."
            ),
            profile=profile,
        )
    south, north, west, east = graph_bounds(G)
    mid_lat = (start[0] + end[0]) / 2
    lat_margin = 0.5 / 111.0
    lon_margin = 0.5 / (111.0 * max(math.cos(math.radians(mid_lat)), 0.2))

    def candidate_is_inside_graph(m: Dict[str, Any]) -> bool:
        lat, lon = float(m["latitude"]), float(m["longitude"])
        return (
            south - lat_margin <= lat <= north + lat_margin
            and west - lon_margin <= lon <= east + lon_margin
        )

    candidates_in_graph = [m for m in candidates if candidate_is_inside_graph(m)]
    if not candidates_in_graph:
        return _route_via_osrm_fallback(
            start=start,
            end=end,
            candidates=candidates,
            requested_candidates=requested_candidates,
            current_time=current_time,
            prayer_time=prayer_time,
            dataset_id=dataset_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            fallback_note="Graph OSM ada, tetapi kandidat masjid di koridor belum masuk area graph.",
            profile=profile,
        )

    import osmnx as ox
    # Query nearest nodes in a single vectorized call
    coords_lats = [start_lat, end_lat]
    coords_lons = [start_lon, end_lon]
    for m in candidates_in_graph:
        coords_lats.append(float(m["latitude"]))
        coords_lons.append(float(m["longitude"]))

    try:
        nearest_nodes = ox.distance.nearest_nodes(G, X=coords_lons, Y=coords_lats)
        start_node = nearest_nodes[0]
        end_node = nearest_nodes[1] if not is_one_way else start_node
        mosque_nodes = nearest_nodes[2:]
    except Exception:
        # Fallback to sequential node queries
        start_node = nearest_road_node(G, start_lat, start_lon)
        end_node = nearest_road_node(G, end_lat, end_lon) if not is_one_way else start_node
        mosque_nodes = [nearest_road_node(G, float(m["latitude"]), float(m["longitude"])) for m in candidates_in_graph]

    results: List[Dict[str, Any]] = []
    start_clock = time.perf_counter()

    # Precompute paths using single-source Dijkstra
    try:
        lengths_from_start, paths_from_start = nx.single_source_dijkstra(G, source=start_node, weight="travel_time")
        if is_one_way:
            lengths_to_end, paths_to_end = {}, {}
        else:
            if not hasattr(G, "_reversed_graph_cached"):
                G._reversed_graph_cached = G.reverse(copy=True)
            G_rev = G._reversed_graph_cached
            lengths_to_end, paths_to_end = nx.single_source_dijkstra(G_rev, source=end_node, weight="travel_time")
    except Exception as exc:
        return _route_via_osrm_fallback(
            start=start,
            end=end,
            candidates=candidates,
            requested_candidates=requested_candidates,
            current_time=current_time,
            prayer_time=prayer_time,
            dataset_id=dataset_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            fallback_note=f"Dijkstra gagal menginisialisasi single-source: {exc}",
            profile=profile,
        )

    for i, mosque in enumerate(candidates_in_graph):
        mosque_node = mosque_nodes[i]
        if mosque_node not in paths_from_start:
            continue
        if not is_one_way and mosque_node not in paths_to_end:
            continue

        path_1 = paths_from_start[mosque_node]
        if is_one_way:
            path_2 = [mosque_node]
        else:
            path_2 = paths_to_end[mosque_node][::-1]

        dist_m = path_length_m(G, path_1)
        time_s = path_travel_time_s(G, path_1)
        if not is_one_way:
            dist_m += path_length_m(G, path_2)
            time_s += path_travel_time_s(G, path_2)
            route_nodes = path_1 + path_2[1:]
        else:
            route_nodes = path_1

        to_mosque_minutes = path_travel_time_s(G, path_1) / 60.0
        capacity_num = {"large": 1.0, "medium": 0.65, "small": 0.35}.get(mosque.get("capacity_proxy"), 0.5)
        priority = float(mosque.get("priority_score", 0.5))
        prayer_penalty, arrival_status, minutes_before_prayer = _prayer_arrival_details(to_mosque_minutes, current_time, prayer_time)

        results.append({
            "mosque": mosque,
            "distance_km": round(dist_m / 1000, 3),
            "estimated_time_minutes": round(time_s / 60, 2),
            "arrival_to_mosque_minutes": round(to_mosque_minutes, 2),
            "route_nodes_count": len(route_nodes),
            "capacity_score": capacity_num,
            "priority_score": priority,
            "prayer_penalty": prayer_penalty,
            "arrival_status": arrival_status,
            "minutes_before_prayer": round(minutes_before_prayer, 1),
            "route_coordinates": [],
            "route_nodes": route_nodes,
        })

    elapsed_ms = round((time.perf_counter() - start_clock) * 1000, 2)

    if not results:
        return _route_via_osrm_fallback(
            start=start,
            end=end,
            candidates=candidates,
            requested_candidates=requested_candidates,
            current_time=current_time,
            prayer_time=prayer_time,
            dataset_id=dataset_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            fallback_note=f"Tidak ada kandidat masjid yang dapat dirutekan pada graph OSM dari {len(candidates_in_graph)} kandidat.",
            profile=profile,
        )

    # Dynamic weighting based on profile
    weights = {
        "fastest": {
            "time": 0.70, "dist": 0.10, "prayer": 0.10, "capacity": 0.05, "priority": 0.05
        },
        "prayer_priority": {
            "time": 0.20, "dist": 0.10, "prayer": 0.60, "capacity": 0.05, "priority": 0.05
        },
        "low_cost": {
            "time": 0.20, "dist": 0.60, "prayer": 0.10, "capacity": 0.05, "priority": 0.05
        },
        "balanced": {
            "time": 0.40, "dist": 0.20, "prayer": 0.20, "capacity": 0.10, "priority": 0.10
        }
    }
    
    prof_weights = weights.get(profile.lower(), weights["balanced"])

    time_norm = _normalise_values([r["estimated_time_minutes"] for r in results])
    dist_norm = _normalise_values([r["distance_km"] for r in results])
    for i, r in enumerate(results):
        capacity_penalty = 1.0 - r["capacity_score"]
        priority_penalty = 1.0 - r["priority_score"]
        r["multi_objective_score"] = round(
            prof_weights["time"] * time_norm[i]
            + prof_weights["dist"] * dist_norm[i]
            + prof_weights["prayer"] * r["prayer_penalty"]
            + prof_weights["capacity"] * capacity_penalty
            + prof_weights["priority"] * priority_penalty,
            4,
        )

    results.sort(key=lambda x: x["multi_objective_score"])
    best_res = results[0]
    best_res["route_coordinates"] = route_nodes_to_latlon(G, best_res["route_nodes"])

    return _format_route_response(
        algorithm_label="Dijkstra (Multi-Destination)",
        road_network="OpenStreetMap via OSMnx/NetworkX",
        routing_weight="travel_time_seconds",
        dataset_id=dataset_id,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        requested_candidates=requested_candidates,
        results=results,
        elapsed_ms=elapsed_ms,
        reason=(
            "Rute dipilih pada graph jalan OpenStreetMap, lalu dievaluasi dengan skor "
            "multi-objective: waktu tempuh, jarak/biaya proxy, kecocokan waktu shalat, capacity proxy, dan priority score."
        ),
    )
