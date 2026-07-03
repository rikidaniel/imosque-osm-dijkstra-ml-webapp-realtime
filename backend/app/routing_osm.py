from __future__ import annotations

import datetime as dt
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import networkx as nx
import requests

from . import local_db
from .ml_enrichment import load_enriched_mosques
from .osm_graph import (
    DEFAULT_GRAPHML,
    astar_path,
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
) -> List[Dict[str, Any]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    min_lat, max_lat = sorted([start[0], end[0]])
    min_lon, max_lon = sorted([start[1], end[1]])
    buffer_deg = corridor_km / 100.0

    for m in mosques:
        lat, lon = float(m["latitude"]), float(m["longitude"])
        if not (min_lat - buffer_deg <= lat <= max_lat + buffer_deg and min_lon - buffer_deg <= lon <= max_lon + buffer_deg):
            continue
        d_line = _distance_point_to_segment_km((lat, lon), start, end)
        if d_line > corridor_km:
            continue
        d_start = haversine_km(start[0], start[1], lat, lon)
        d_end = haversine_km(lat, lon, end[0], end[1])
        priority = float(m.get("priority_score", 0.5))
        # Smaller is better: near route, not too far from start/end, high quality.
        rank_score = 0.55 * d_line + 0.25 * min(d_start, d_end) - 2.0 * priority
        scored.append((rank_score, m))

    if not scored:
        # Fallback: nearest mosque to start if corridor has no candidates.
        for m in mosques:
            d_start = haversine_km(start[0], start[1], float(m["latitude"]), float(m["longitude"]))
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


def _prayer_penalty(arrival_minutes: float, current_time: Optional[str], prayer_time: Optional[str]) -> float:
    current_dt = _parse_hhmm(current_time)
    prayer_dt = _parse_hhmm(prayer_time)
    if current_dt is None or prayer_dt is None:
        return 0.3  # neutral when no prayer-time context is available
    if prayer_dt < current_dt:
        prayer_dt += dt.timedelta(days=1)
    arrival_dt = current_dt + dt.timedelta(minutes=arrival_minutes)
    if arrival_dt > prayer_dt:
        late = (arrival_dt - prayer_dt).total_seconds() / 60
        return min(1.0, 0.6 + late / 30.0)
    before = (prayer_dt - arrival_dt).total_seconds() / 60
    # Best if user reaches mosque 0-25 minutes before prayer. Too early is not bad, just less optimal.
    if 0 <= before <= 25:
        return 0.0
    return min(0.5, before / 90.0)


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

    return {
        "algorithm": algorithm_label,
        "dataset_id": dataset_id,
        "road_network": road_network,
        "routing_weight": routing_weight,
        "candidate_count": len(results),
        "execution_time_ms": elapsed_ms,
        "start": {"latitude": start_lat, "longitude": start_lon},
        "destination": {"latitude": end_lat, "longitude": end_lon},
        "recommended_mosque": best_m,
        "route_summary": {
            "distance_km": best["distance_km"],
            "estimated_time_minutes": best["estimated_time_minutes"],
            "arrival_to_mosque_minutes": best["arrival_to_mosque_minutes"],
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
                "coordinates": [[lon, lat] for lat, lon in best["route_coordinates"]],
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
        prayer = _prayer_penalty(to_mosque_minutes, current_time, prayer_time)
        coords = _local_route_coordinates(start, (mlat, mlon), end)

        results.append({
            "mosque": mosque,
            "distance_km": round(dist_km, 3),
            "estimated_time_minutes": round(time_minutes, 2),
            "arrival_to_mosque_minutes": round(to_mosque_minutes, 2),
            "route_nodes_count": len(coords),
            "capacity_score": capacity_num,
            "priority_score": priority,
            "prayer_penalty": prayer,
            "route_coordinates": coords,
        })

    if not results:
        raise RuntimeError("Tidak ada kandidat masjid lokal yang dapat dievaluasi.")

    time_norm = _normalise_values([r["estimated_time_minutes"] for r in results])
    dist_norm = _normalise_values([r["distance_km"] for r in results])
    for i, r in enumerate(results):
        capacity_penalty = 1.0 - r["capacity_score"]
        priority_penalty = 1.0 - r["priority_score"]
        r["multi_objective_score"] = round(
            0.40 * time_norm[i]
            + 0.20 * dist_norm[i]
            + 0.20 * r["prayer_penalty"]
            + 0.10 * capacity_penalty
            + 0.10 * priority_penalty,
            4,
        )

    elapsed_ms = round((time.perf_counter() - start_clock) * 1000, 2)
    return _format_route_response(
        algorithm_label="Local Approximation",
        road_network="SQLite local mosque data + straight-line fallback",
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
) -> Dict[str, Any]:
    start_clock = time.perf_counter()
    results: List[Dict[str, Any]] = []
    candidate_pool = candidates[: min(len(candidates), max(requested_candidates, 6))]
    last_error = ""

    for mosque in candidate_pool:
        mlat, mlon = float(mosque["latitude"]), float(mosque["longitude"])
        try:
            route = _osrm_route(start, (mlat, mlon), end)
        except Exception as exc:
            last_error = str(exc)
            continue

        dist_km = route["distance_m"] / 1000.0
        time_minutes = route["duration_s"] / 60.0
        if route["duration_to_mosque_s"] is not None:
            to_mosque_minutes = route["duration_to_mosque_s"] / 60.0
        else:
            d1 = haversine_km(start[0], start[1], mlat, mlon) * 1.25
            to_mosque_minutes = (d1 / 30.0) * 60.0

        capacity_num = {"large": 1.0, "medium": 0.65, "small": 0.35}.get(mosque.get("capacity_proxy"), 0.5)
        priority = float(mosque.get("priority_score", 0.5))
        prayer = _prayer_penalty(to_mosque_minutes, current_time, prayer_time)
        coords = route["coordinates"] or _local_route_coordinates(start, (mlat, mlon), end)

        results.append({
            "mosque": mosque,
            "distance_km": round(dist_km, 3),
            "estimated_time_minutes": round(time_minutes, 2),
            "arrival_to_mosque_minutes": round(to_mosque_minutes, 2),
            "route_nodes_count": len(coords),
            "capacity_score": capacity_num,
            "priority_score": priority,
            "prayer_penalty": prayer,
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
        )

    time_norm = _normalise_values([r["estimated_time_minutes"] for r in results])
    dist_norm = _normalise_values([r["distance_km"] for r in results])
    for i, r in enumerate(results):
        capacity_penalty = 1.0 - r["capacity_score"]
        priority_penalty = 1.0 - r["priority_score"]
        r["multi_objective_score"] = round(
            0.40 * time_norm[i]
            + 0.20 * dist_norm[i]
            + 0.20 * r["prayer_penalty"]
            + 0.10 * capacity_penalty
            + 0.10 * priority_penalty,
            4,
        )

    elapsed_ms = round((time.perf_counter() - start_clock) * 1000, 2)
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
        reason=(
            "Rute mengikuti jalan menggunakan OSRM karena graph jalan OSM lokal belum tersedia atau Overpass gagal. "
            "Masjid tetap dipilih dari SQLite lokal dan dievaluasi dengan skor multi-objective. "
            f"Catatan teknis: {fallback_note}"
        ),
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
) -> Dict[str, Any]:
    start = (float(start_lat), float(start_lon))
    mosque_point = (float(mosque["latitude"]), float(mosque["longitude"]))
    requested_candidates = 1

    if start == mosque_point:
        raise ValueError("Titik awal dan masjid tujuan tidak boleh sama.")

    if not graphml_path.exists():
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
            G = load_road_graph(graphml_path)
            cache_ready = graph_covers_points(G, [start, mosque_point], margin_km=0.5)
        except FileNotFoundError:
            G = None
            cache_ready = False
        if not cache_ready:
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
            local_db.save_osm_graph_cache(
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
) -> Dict[str, Any]:
    start = (float(start_lat), float(start_lon))
    end = (float(end_lat), float(end_lon))
    if start == end:
        raise ValueError("Titik awal dan titik tujuan tidak boleh sama.")

    mosques = load_enriched_mosques(dataset_id=dataset_id)
    if not mosques:
        raise ValueError("Dataset aktif tidak memiliki data masjid yang valid.")

    requested_candidates = max(1, int(max_candidates))
    effective_corridor_km = max(float(buffer_km), 5.0)
    evaluation_limit = min(len(mosques), max(12, requested_candidates * 3))
    candidates = select_candidate_mosques(
        mosques,
        start,
        end,
        limit=evaluation_limit,
        corridor_km=effective_corridor_km,
    )
    build_candidate_limit = min(len(candidates), max(requested_candidates, 3))
    build_candidate_points = [
        (float(m["latitude"]), float(m["longitude"]))
        for m in candidates[:build_candidate_limit]
    ]
    points_to_cover = [start, end] + build_candidate_points

    if not graphml_path.exists():
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
        )

    if auto_build_osm:
        # Reuse a matching cache. Rebuilding on every click makes routing feel
        # stuck because OSMnx must query Overpass and simplify a fresh graph.
        try:
            G = load_road_graph(graphml_path)
            cache_ready = graph_covers_points(G, points_to_cover, margin_km=0.5)
        except FileNotFoundError:
            G = None
            cache_ready = False

        if not cache_ready:
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
                )
            local_db.save_osm_graph_cache(
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
        )

    start_node = nearest_road_node(G, start_lat, start_lon)
    end_node = nearest_road_node(G, end_lat, end_lon)

    results: List[Dict[str, Any]] = []
    start_clock = time.perf_counter()

    for mosque in candidates_in_graph:
        mlat, mlon = float(mosque["latitude"]), float(mosque["longitude"])
        try:
            mosque_node = nearest_road_node(G, mlat, mlon)
            path_1 = _safe_shortest_path(G, start_node, mosque_node, algorithm=algorithm, weight="travel_time")
            path_2 = _safe_shortest_path(G, mosque_node, end_node, algorithm=algorithm, weight="travel_time")
        except (nx.NetworkXNoPath, nx.NodeNotFound, ValueError):
            continue

        dist_m = path_length_m(G, path_1) + path_length_m(G, path_2)
        time_s = path_travel_time_s(G, path_1) + path_travel_time_s(G, path_2)
        to_mosque_minutes = path_travel_time_s(G, path_1) / 60.0
        capacity_num = {"large": 1.0, "medium": 0.65, "small": 0.35}.get(mosque.get("capacity_proxy"), 0.5)
        priority = float(mosque.get("priority_score", 0.5))
        prayer = _prayer_penalty(to_mosque_minutes, current_time, prayer_time)

        # route geometry follows OpenStreetMap edge geometry.
        route_nodes = path_1 + path_2[1:]
        coords = route_nodes_to_latlon(G, route_nodes)

        results.append({
            "mosque": mosque,
            "distance_km": round(dist_m / 1000, 3),
            "estimated_time_minutes": round(time_s / 60, 2),
            "arrival_to_mosque_minutes": round(to_mosque_minutes, 2),
            "route_nodes_count": len(route_nodes),
            "capacity_score": capacity_num,
            "priority_score": priority,
            "prayer_penalty": prayer,
            "route_coordinates": coords,
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
        )

    time_norm = _normalise_values([r["estimated_time_minutes"] for r in results])
    dist_norm = _normalise_values([r["distance_km"] for r in results])
    for i, r in enumerate(results):
        capacity_penalty = 1.0 - r["capacity_score"]
        priority_penalty = 1.0 - r["priority_score"]
        r["multi_objective_score"] = round(
            0.40 * time_norm[i]
            + 0.20 * dist_norm[i]
            + 0.20 * r["prayer_penalty"]
            + 0.10 * capacity_penalty
            + 0.10 * priority_penalty,
            4,
        )

    return _format_route_response(
        algorithm_label="A*" if algorithm.lower() in {"astar", "a*"} else "Dijkstra",
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
