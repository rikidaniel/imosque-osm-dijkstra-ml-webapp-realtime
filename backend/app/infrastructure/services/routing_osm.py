from __future__ import annotations

import datetime as dt
import copy
import heapq
import math
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import re
import networkx as nx
import numpy as np
import requests
from requests.adapters import HTTPAdapter

from typing import Callable


from .osm_graph import (
    DEFAULT_GRAPHML,
    RoadEdgeSnap,
    astar_path,
    bbox_area_km2,
    bbox_from_points,
    build_osm_graph_for_route,
    graph_bounds,
    graph_covers_points,
    get_road_graph_status,
    dijkstra_path,
    edge_snap_segment_coordinates,
    haversine_km,
    load_road_graph,
    nearest_road_node,
    nearest_road_node_candidates_batch,
    nearest_road_edge_candidates_batch,
    nearest_road_nodes_batch,
    path_length_m,
    path_travel_time_s,
    route_nodes_to_latlon,
)

Coordinate = Tuple[float, float]
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"
MAX_INLINE_AUTO_BUILD_AREA_KM2 = 90.0
ROUTE_CACHE_TTL_SECONDS = 24 * 60 * 60
ROUTE_CACHE_MAX_ENTRIES = 256
CONNECTOR_SPEED_MPS = 1.4  # Walking access from GPS/building coordinates to the drivable road.
EDGE_SNAP_CANDIDATE_COUNT = 4
EDGE_SNAP_BATCH_CANDIDATE_COUNT = 3

_OSRM_SESSION = requests.Session()
_OSRM_ADAPTER = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
_OSRM_SESSION.mount("https://", _OSRM_ADAPTER)
_OSRM_SESSION.mount("http://", _OSRM_ADAPTER)
_ROUTE_CACHE: "OrderedDict[tuple, tuple[float, Dict[str, Any]]]" = OrderedDict()
_ROUTE_CACHE_LOCK = threading.RLock()
_ROUTE_SINGLEFLIGHT_LOCKS = tuple(threading.Lock() for _ in range(32))


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
    if not mosques:
        return []

    # Candidate snapshots commonly contain thousands of mosques. Vectorizing
    # this purely numeric pre-ranking avoids a Python loop on every GPS update
    # while preserving stable input-order tie breaking.
    latitudes = np.fromiter(
        (float(mosque["latitude"]) for mosque in mosques),
        dtype=float,
        count=len(mosques),
    )
    longitudes = np.fromiter(
        (float(mosque["longitude"]) for mosque in mosques),
        dtype=float,
        count=len(mosques),
    )
    priorities = np.fromiter(
        (float(mosque.get("priority_score", 0.5)) for mosque in mosques),
        dtype=float,
        count=len(mosques),
    )
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

    px = longitudes * cos_factor
    py = latitudes * 111.0
    if segment_len_sq == 0:
        line_distances = np.hypot(px - ax, py - ay)
    else:
        fractions = np.clip(((px - ax) * dx + (py - ay) * dy) / segment_len_sq, 0.0, 1.0)
        line_distances = np.hypot(px - (ax + fractions * dx), py - (ay + fractions * dy))

    corridor_mask = (
        (latitudes >= min_lat - buffer_deg)
        & (latitudes <= max_lat + buffer_deg)
        & (longitudes >= min_lon - buffer_deg)
        & (longitudes <= max_lon + buffer_deg)
        & (line_distances <= corridor_km)
    )

    def haversine_many(lat1, lon1, lat2, lon2):
        phi1 = np.radians(lat1)
        phi2 = np.radians(lat2)
        dphi = np.radians(lat2 - lat1)
        dlambda = np.radians(lon2 - lon1)
        value = (
            np.sin(dphi / 2.0) ** 2
            + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2.0) ** 2
        )
        value = np.clip(value, 0.0, 1.0)
        return 2.0 * 6371.0 * np.arctan2(np.sqrt(value), np.sqrt(1.0 - value))

    distances_from_start = haversine_many(
        start[0], start[1], latitudes, longitudes
    )
    candidate_indices = np.flatnonzero(corridor_mask)
    if candidate_indices.size:
        distances_to_end = haversine_many(
            latitudes[candidate_indices],
            longitudes[candidate_indices],
            end[0],
            end[1],
        )
        scores = (
            0.55 * line_distances[candidate_indices]
            + 0.25
            * np.minimum(distances_from_start[candidate_indices], distances_to_end)
            - 2.0 * priorities[candidate_indices]
        )
    else:
        # Fallback remains bounded so a wrong-region dataset cannot look valid.
        candidate_indices = np.flatnonzero(distances_from_start <= fallback_radius_km)
        scores = distances_from_start[candidate_indices]

    order = np.argsort(scores, kind="stable")[: max(0, int(limit))]
    return [mosques[int(candidate_indices[int(index)])] for index in order]


def _safe_shortest_path(G, source, target, algorithm: str, weight: str = "travel_time"):
    if algorithm.lower() in {"astar", "a*"}:
        return astar_path(G, source, target, weight=weight)
    return dijkstra_path(G, source, target, weight=weight)


def _best_snapped_route(
    G,
    start: Coordinate,
    destination: Coordinate,
    algorithm: str,
    candidate_count: int = 3,
    weight: str = "travel_time",
):
    """Choose the best reachable route across nearby snap candidates.

    Connector time is included in the comparison, preventing a geometrically
    close node on the wrong carriageway from winning merely because it is the
    single nearest node.
    """
    candidate_groups = nearest_road_node_candidates_batch(
        G, [start, destination], k=candidate_count
    )
    start_candidates, destination_candidates = candidate_groups
    best = None
    for start_node in dict.fromkeys(start_candidates):
        start_connector_m = 1000.0 * haversine_km(
            start[0], start[1], *_node_coordinate(G, start_node)
        )
        for destination_node in dict.fromkeys(destination_candidates):
            destination_connector_m = 1000.0 * haversine_km(
                destination[0], destination[1], *_node_coordinate(G, destination_node)
            )
            try:
                route_nodes = _safe_shortest_path(
                    G, start_node, destination_node, algorithm=algorithm, weight=weight
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound, ValueError):
                continue
            connector_m = start_connector_m + destination_connector_m
            if weight == "length":
                score = path_length_m(G, route_nodes) + connector_m
            else:
                score = path_travel_time_s(G, route_nodes) + connector_m / CONNECTOR_SPEED_MPS
            candidate = (score, connector_m, start_node, destination_node, route_nodes)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
    if best is None:
        raise nx.NetworkXNoPath("Tidak ada pasangan titik jalan terdekat yang saling terhubung.")
    _, connector_m, start_node, destination_node, route_nodes = best
    return start_node, destination_node, route_nodes, connector_m


def _minimum_edge_weight(G, edge_data: Dict[str, Any], weight: str) -> float:
    """Return the lightest parallel edge, matching MultiDiGraph semantics."""
    candidates = edge_data.values() if G.is_multigraph() else (edge_data,)
    values: List[float] = []
    for attrs in candidates:
        try:
            value = attrs.get(weight)
            if value is None:
                value = float(attrs.get("length", 0.0)) / 8.33
            value = float(value)
            if math.isfinite(value) and value >= 0:
                values.append(value)
        except (AttributeError, TypeError, ValueError):
            continue
    return min(values) if values else math.inf


def _multi_target_dijkstra_paths(
    G,
    source,
    targets: Sequence,
    weight: str = "travel_time",
) -> Tuple[Dict[Any, float], Dict[Any, List[Any]]]:
    """Stop Dijkstra after the requested mosque nodes have been settled."""
    remaining = set(targets)
    if source not in G:
        raise nx.NodeNotFound(f"Source {source!r} is not in G")
    if not remaining:
        return {}, {}

    distances: Dict[Any, float] = {source: 0.0}
    parents: Dict[Any, Any] = {}
    settled: Dict[Any, float] = {}
    found: Dict[Any, float] = {}
    queue: List[Tuple[float, int, Any]] = [(0.0, 0, source)]
    serial = 1

    while queue and remaining:
        distance, _, node = heapq.heappop(queue)
        if node in settled:
            continue
        settled[node] = distance
        if node in remaining:
            found[node] = distance
            remaining.remove(node)
            if not remaining:
                break

        for neighbor, edge_data in G.adj[node].items():
            edge_weight = _minimum_edge_weight(G, edge_data, weight)
            if not math.isfinite(edge_weight):
                continue
            candidate_distance = distance + edge_weight
            if candidate_distance < distances.get(neighbor, math.inf):
                distances[neighbor] = candidate_distance
                parents[neighbor] = node
                heapq.heappush(queue, (candidate_distance, serial, neighbor))
                serial += 1

    paths: Dict[Any, List[Any]] = {}
    for target in found:
        path = [target]
        while path[-1] != source:
            parent = parents.get(path[-1])
            if parent is None:
                path = []
                break
            path.append(parent)
        if path:
            paths[target] = list(reversed(path))
    return found, paths


def _directed_edge_metrics(G, u, v) -> Tuple[float, float]:
    """Return length/time for the directed parallel edge used by pathfinding."""
    data = G.get_edge_data(u, v)
    if not data:
        return math.inf, math.inf
    attributes = data.values() if G.is_multigraph() else (data,)
    candidates = []
    for attrs in attributes:
        try:
            length = float(attrs.get("length", 0.0))
            travel_time = float(attrs.get("travel_time", length / 8.33))
        except (AttributeError, TypeError, ValueError):
            continue
        if math.isfinite(length) and math.isfinite(travel_time):
            candidates.append((travel_time, length))
    if not candidates:
        return math.inf, math.inf
    travel_time, length = min(candidates)
    return length, travel_time


def _dedupe_snap_options(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_node: Dict[Any, Dict[str, Any]] = {}
    for option in options:
        current = best_by_node.get(option["node"])
        if current is None or (option["time_s"], option["length_m"]) < (
            current["time_s"],
            current["length_m"],
        ):
            best_by_node[option["node"]] = option
    return list(best_by_node.values())


def _snap_exit_options(G, snap: RoadEdgeSnap) -> List[Dict[str, Any]]:
    """Ways to leave a projected point while respecting directed edges."""
    fraction = snap.fraction
    options: List[Dict[str, Any]] = []
    if fraction <= 1e-9:
        options.append({"node": snap.u, "time_s": 0.0, "length_m": 0.0, "coordinates": [snap.coordinate], "snap": snap})
    if fraction >= 1.0 - 1e-9:
        options.append({"node": snap.v, "time_s": 0.0, "length_m": 0.0, "coordinates": [snap.coordinate], "snap": snap})
    if fraction < 1.0 - 1e-9 and G.has_edge(snap.u, snap.v):
        length, travel_time = _directed_edge_metrics(G, snap.u, snap.v)
        factor = 1.0 - fraction
        options.append({
            "node": snap.v,
            "time_s": travel_time * factor,
            "length_m": length * factor,
            "coordinates": None,
            "coordinate_range": (fraction, 1.0),
            "snap": snap,
        })
    if fraction > 1e-9 and G.has_edge(snap.v, snap.u):
        length, travel_time = _directed_edge_metrics(G, snap.v, snap.u)
        options.append({
            "node": snap.u,
            "time_s": travel_time * fraction,
            "length_m": length * fraction,
            "coordinates": None,
            "coordinate_range": (fraction, 0.0),
            "snap": snap,
        })
    return _dedupe_snap_options([option for option in options if math.isfinite(option["time_s"])])


def _snap_entry_options(G, snap: RoadEdgeSnap) -> List[Dict[str, Any]]:
    """Ways to reach a projected point while respecting directed edges."""
    fraction = snap.fraction
    options: List[Dict[str, Any]] = []
    if fraction <= 1e-9:
        options.append({"node": snap.u, "time_s": 0.0, "length_m": 0.0, "coordinates": [snap.coordinate], "snap": snap})
    if fraction >= 1.0 - 1e-9:
        options.append({"node": snap.v, "time_s": 0.0, "length_m": 0.0, "coordinates": [snap.coordinate], "snap": snap})
    if fraction > 1e-9 and G.has_edge(snap.u, snap.v):
        length, travel_time = _directed_edge_metrics(G, snap.u, snap.v)
        options.append({
            "node": snap.u,
            "time_s": travel_time * fraction,
            "length_m": length * fraction,
            "coordinates": None,
            "coordinate_range": (0.0, fraction),
            "snap": snap,
        })
    if fraction < 1.0 - 1e-9 and G.has_edge(snap.v, snap.u):
        length, travel_time = _directed_edge_metrics(G, snap.v, snap.u)
        factor = 1.0 - fraction
        options.append({
            "node": snap.v,
            "time_s": travel_time * factor,
            "length_m": length * factor,
            "coordinates": None,
            "coordinate_range": (1.0, fraction),
            "snap": snap,
        })
    return _dedupe_snap_options([option for option in options if math.isfinite(option["time_s"])])


def _connector_segments(*pairs: Tuple[Coordinate, Coordinate]) -> List[List[Coordinate]]:
    segments: List[List[Coordinate]] = []
    for start, end in pairs:
        if 1000.0 * haversine_km(start[0], start[1], end[0], end[1]) > 0.5:
            segments.append([start, end])
    return segments


def _snap_option_coordinates(G, option: Dict[str, Any]) -> List[Coordinate]:
    coordinates = option.get("coordinates")
    if coordinates is not None:
        return coordinates
    start_fraction, end_fraction = option["coordinate_range"]
    return edge_snap_segment_coordinates(
        G,
        option["snap"],
        start_fraction,
        end_fraction,
    )


def _edge_route_result(
    *,
    start: Coordinate,
    destination: Coordinate,
    start_option: Dict[str, Any],
    destination_option: Dict[str, Any],
    route_nodes: Sequence,
) -> Dict[str, Any]:
    start_snap: RoadEdgeSnap = start_option["snap"]
    destination_snap: RoadEdgeSnap = destination_option["snap"]
    network_coordinates = route_nodes_to_latlon(start_option["graph"], route_nodes)
    road_coordinates = _stitch_route_segments(
        _snap_option_coordinates(start_option["graph"], start_option),
        network_coordinates,
        _snap_option_coordinates(start_option["graph"], destination_option),
    )
    network_length_m = path_length_m(start_option["graph"], route_nodes)
    network_time_s = path_travel_time_s(start_option["graph"], route_nodes)
    road_length_m = start_option["length_m"] + network_length_m + destination_option["length_m"]
    road_time_s = start_option["time_s"] + network_time_s + destination_option["time_s"]
    connector_m = start_snap.connector_m + destination_snap.connector_m
    return {
        "route_nodes": list(route_nodes),
        "road_coordinates": road_coordinates,
        "road_length_m": road_length_m,
        "road_time_s": road_time_s,
        "connector_m": connector_m,
        "distance_m": road_length_m + connector_m,
        "time_s": road_time_s + connector_m / CONNECTOR_SPEED_MPS,
        "access_connectors": _connector_segments(
            (start, start_snap.coordinate),
            (destination_snap.coordinate, destination),
        ),
        "start_snap": start_snap,
        "destination_snap": destination_snap,
        "start_option": start_option,
        "destination_option": destination_option,
    }


def _direct_same_edge_route(
    G,
    start: Coordinate,
    destination: Coordinate,
    start_snap: RoadEdgeSnap,
    destination_snap: RoadEdgeSnap,
) -> Optional[Dict[str, Any]]:
    if start_snap.u != destination_snap.u or start_snap.v != destination_snap.v:
        return None
    start_fraction = start_snap.fraction
    destination_fraction = destination_snap.fraction
    direction = None
    if destination_fraction >= start_fraction and G.has_edge(start_snap.u, start_snap.v):
        direction = (start_snap.u, start_snap.v)
    elif destination_fraction <= start_fraction and G.has_edge(start_snap.v, start_snap.u):
        direction = (start_snap.v, start_snap.u)
    if direction is None:
        return None
    edge_length_m, edge_time_s = _directed_edge_metrics(G, *direction)
    factor = abs(destination_fraction - start_fraction)
    road_coordinates = edge_snap_segment_coordinates(
        G, start_snap, start_fraction, destination_fraction
    )
    connector_m = start_snap.connector_m + destination_snap.connector_m
    return {
        "route_nodes": [],
        "road_coordinates": road_coordinates,
        "road_length_m": edge_length_m * factor,
        "road_time_s": edge_time_s * factor,
        "connector_m": connector_m,
        "distance_m": edge_length_m * factor + connector_m,
        "time_s": edge_time_s * factor + connector_m / CONNECTOR_SPEED_MPS,
        "access_connectors": _connector_segments(
            (start, start_snap.coordinate),
            (destination_snap.coordinate, destination),
        ),
        "start_snap": start_snap,
        "destination_snap": destination_snap,
        "start_option": None,
        "destination_option": None,
    }


def _best_edge_snapped_route(
    G,
    start: Coordinate,
    destination: Coordinate,
    algorithm: str,
    candidate_count: int = EDGE_SNAP_CANDIDATE_COUNT,
) -> Dict[str, Any]:
    snap_groups = nearest_road_edge_candidates_batch(
        G, [start, destination], k=candidate_count
    )
    best: Optional[Dict[str, Any]] = None
    for start_snap in snap_groups[0]:
        for destination_snap in snap_groups[1]:
            direct = _direct_same_edge_route(G, start, destination, start_snap, destination_snap)
            if direct is not None and (best is None or (direct["time_s"], direct["connector_m"]) < (best["time_s"], best["connector_m"])):
                best = direct
            for start_option in _snap_exit_options(G, start_snap):
                start_option = dict(start_option, graph=G)
                for destination_option in _snap_entry_options(G, destination_snap):
                    destination_option = dict(destination_option, graph=G)
                    try:
                        route_nodes = (
                            [start_option["node"]]
                            if start_option["node"] == destination_option["node"]
                            else _safe_shortest_path(
                                G,
                                start_option["node"],
                                destination_option["node"],
                                algorithm=algorithm,
                                weight="travel_time",
                            )
                        )
                    except (nx.NetworkXNoPath, nx.NodeNotFound, ValueError):
                        continue
                    candidate = _edge_route_result(
                        start=start,
                        destination=destination,
                        start_option=start_option,
                        destination_option=destination_option,
                        route_nodes=route_nodes,
                    )
                    if best is None or (candidate["time_s"], candidate["connector_m"]) < (
                        best["time_s"], best["connector_m"]
                    ):
                        best = candidate
    if best is None:
        raise nx.NetworkXNoPath("Tidak ada pasangan edge jalan yang saling terhubung.")
    return best


def _multi_source_target_paths(
    G,
    sources: Sequence[Tuple[Any, float, Any]],
    targets: Sequence,
    weight: str = "travel_time",
) -> Dict[Any, Tuple[float, List[Any], Any]]:
    """Dijkstra with per-source initial costs and source-token retention."""
    remaining = set(targets)
    distances: Dict[Any, float] = {}
    parents: Dict[Any, Any] = {}
    source_tokens: Dict[Any, Any] = {}
    settled = set()
    queue: List[Tuple[float, int, Any]] = []
    serial = 0
    for node, initial_cost, token in sources:
        if node not in G or not math.isfinite(initial_cost):
            continue
        if initial_cost < distances.get(node, math.inf):
            distances[node] = initial_cost
            source_tokens[node] = token
            heapq.heappush(queue, (initial_cost, serial, node))
            serial += 1

    found: Dict[Any, Tuple[float, List[Any], Any]] = {}
    is_multigraph = G.is_multigraph()
    while queue and remaining:
        distance, _, node = heapq.heappop(queue)
        if node in settled:
            continue
        settled.add(node)
        if node in remaining:
            path = [node]
            while path[-1] in parents:
                path.append(parents[path[-1]])
            path.reverse()
            found[node] = (distance, path, source_tokens[node])
            remaining.remove(node)
        for neighbor, edge_data in G.adj[node].items():
            if is_multigraph:
                edge_weight = _minimum_edge_weight(G, edge_data, weight)
            else:
                try:
                    raw_weight = edge_data.get(weight)
                    if raw_weight is None:
                        raw_weight = float(edge_data.get("length", 0.0)) / 8.33
                    edge_weight = float(raw_weight)
                    if edge_weight < 0:
                        edge_weight = math.inf
                except (AttributeError, TypeError, ValueError):
                    edge_weight = math.inf
            if not math.isfinite(edge_weight):
                continue
            candidate_distance = distance + edge_weight
            if candidate_distance < distances.get(neighbor, math.inf):
                distances[neighbor] = candidate_distance
                parents[neighbor] = node
                source_tokens[neighbor] = source_tokens[node]
                heapq.heappush(queue, (candidate_distance, serial, neighbor))
                serial += 1
    return found


def _best_batch_candidate(
    current: Optional[Dict[str, Any]], candidate: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if candidate is None:
        return current
    if current is None or (candidate["time_s"], candidate["connector_m"]) < (
        current["time_s"], current["connector_m"]
    ):
        return candidate
    return current


def _batch_edge_routes_from_start(
    G,
    start: Coordinate,
    destinations: Sequence[Coordinate],
    candidate_count: int = EDGE_SNAP_BATCH_CANDIDATE_COUNT,
    snap_groups: Optional[Sequence[Sequence[RoadEdgeSnap]]] = None,
) -> List[Optional[Dict[str, Any]]]:
    if not destinations:
        return []
    if snap_groups is None:
        snap_groups = nearest_road_edge_candidates_batch(
            G, [start, *destinations], k=candidate_count
        )
    if len(snap_groups) != len(destinations) + 1:
        raise ValueError("Jumlah snap group start/tujuan tidak sesuai.")
    source_options = []
    for snap in snap_groups[0]:
        for option in _snap_exit_options(G, snap):
            token = dict(option, graph=G)
            source_options.append(
                (option["node"], option["time_s"] + snap.connector_m / CONNECTOR_SPEED_MPS, token)
            )
    destination_options = [
        [dict(option, graph=G) for snap in group for option in _snap_entry_options(G, snap)]
        for group in snap_groups[1:]
    ]
    targets = [option["node"] for options in destination_options for option in options]
    found = _multi_source_target_paths(G, source_options, targets)
    results: List[Optional[Dict[str, Any]]] = []
    for destination, destination_snaps, options in zip(destinations, snap_groups[1:], destination_options):
        # Dijkstra already gives the scalar cost needed to choose an option.
        # Constructing complete coordinates and metrics for every losing option
        # was substantially more expensive than the search itself.
        best_spec = None
        for option in options:
            match = found.get(option["node"])
            if match is None:
                continue
            match_distance, route_nodes, source_option = match
            connector_m = source_option["snap"].connector_m + option["snap"].connector_m
            score = (
                match_distance
                + option["time_s"]
                + option["snap"].connector_m / CONNECTOR_SPEED_MPS,
                connector_m,
            )
            if best_spec is None or score < best_spec[0]:
                best_spec = (score, source_option, option, route_nodes)
        best = None
        if best_spec is not None:
            _, source_option, destination_option, route_nodes = best_spec
            best = _edge_route_result(
                start=start,
                destination=destination,
                start_option=source_option,
                destination_option=destination_option,
                route_nodes=route_nodes,
            )
        for start_snap in snap_groups[0]:
            for destination_snap in destination_snaps:
                best = _best_batch_candidate(
                    best,
                    _direct_same_edge_route(G, start, destination, start_snap, destination_snap),
                )
        results.append(best)
    return results


def _batch_edge_routes_to_destination(
    G,
    starts: Sequence[Coordinate],
    destination: Coordinate,
    candidate_count: int = EDGE_SNAP_BATCH_CANDIDATE_COUNT,
    snap_groups: Optional[Sequence[Sequence[RoadEdgeSnap]]] = None,
) -> List[Optional[Dict[str, Any]]]:
    if not starts:
        return []
    if snap_groups is None:
        snap_groups = nearest_road_edge_candidates_batch(
            G, [*starts, destination], k=candidate_count
        )
    if len(snap_groups) != len(starts) + 1:
        raise ValueError("Jumlah snap group asal/tujuan tidak sesuai.")
    destination_snaps = snap_groups[-1]
    destination_sources = []
    for snap in destination_snaps:
        for option in _snap_entry_options(G, snap):
            token = dict(option, graph=G)
            destination_sources.append(
                (option["node"], option["time_s"] + snap.connector_m / CONNECTOR_SPEED_MPS, token)
            )
    start_options = [
        [dict(option, graph=G) for snap in group for option in _snap_exit_options(G, snap)]
        for group in snap_groups[:-1]
    ]
    targets = [option["node"] for options in start_options for option in options]
    reversed_graph = G.reverse(copy=False)
    found = _multi_source_target_paths(reversed_graph, destination_sources, targets)
    results: List[Optional[Dict[str, Any]]] = []
    for start, start_snaps, options in zip(starts, snap_groups[:-1], start_options):
        best_spec = None
        for option in options:
            match = found.get(option["node"])
            if match is None:
                continue
            match_distance, reversed_nodes, destination_option = match
            connector_m = option["snap"].connector_m + destination_option["snap"].connector_m
            score = (
                match_distance
                + option["time_s"]
                + option["snap"].connector_m / CONNECTOR_SPEED_MPS,
                connector_m,
            )
            if best_spec is None or score < best_spec[0]:
                best_spec = (score, option, destination_option, reversed_nodes)
        best = None
        if best_spec is not None:
            _, start_option, destination_option, reversed_nodes = best_spec
            best = _edge_route_result(
                start=start,
                destination=destination,
                start_option=start_option,
                destination_option=destination_option,
                route_nodes=list(reversed(reversed_nodes)),
            )
        for start_snap in start_snaps:
            for destination_snap in destination_snaps:
                best = _best_batch_candidate(
                    best,
                    _direct_same_edge_route(G, start, destination, start_snap, destination_snap),
                )
        results.append(best)
    return results


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


def _node_coordinate(G, node) -> Coordinate:
    return float(G.nodes[node]["y"]), float(G.nodes[node]["x"])


def _stitch_route_segments(*segments: Sequence[Coordinate]) -> List[Coordinate]:
    coordinates: List[Coordinate] = []
    for segment in segments:
        for point in segment:
            coordinate = (float(point[0]), float(point[1]))
            if not coordinates or coordinate != coordinates[-1]:
                coordinates.append(coordinate)
    return coordinates


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
    phase_timings_ms: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    results.sort(key=lambda x: x["multi_objective_score"])
    best = results[0]
    best_m = best["mosque"]
    used_osrm_fallback = algorithm_label == "OSRM Road Route"
    is_local_approximation = algorithm_label == "Local Approximation"
    routing_mode = "osrm_fallback" if used_osrm_fallback else "local_approximation" if is_local_approximation else "local_graph"
    graph_source = "osrm_public_api" if used_osrm_fallback else "none" if is_local_approximation else "osm_graphml_cache"
    mosque_summary = {
        key: best_m.get(key)
        for key in (
            "id", "dataset_id", "name", "address", "provinsi", "kabko",
            "kecamatan", "kelurahan", "latitude", "longitude", "rating",
            "review_count", "facilities", "fasilitas", "capacity_proxy",
            "priority_score", "tier",
        )
        if best_m.get(key) is not None
    }
    # Keep disconnected road legs separate from off-road access connectors.
    # This prevents a straight building/river crossing from being painted as a
    # driveable road while preserving a compact polyline representation.
    raw_segments = best.get("route_segments") or [best["route_coordinates"]]
    raw_segments = [list(segment) for segment in raw_segments if segment]
    simplified_segments = [
        _douglas_peucker(segment, 0.01) if len(segment) > 2 else segment
        for segment in raw_segments
    ]
    legacy_coordinates = _stitch_route_segments(*simplified_segments)
    access_connectors = best.get("access_connectors", [])
    geometry = (
        {
            "type": "LineString",
            "coordinates": [[lon, lat] for lat, lon in simplified_segments[0]],
        }
        if len(simplified_segments) == 1
        else {
            "type": "MultiLineString",
            "coordinates": [
                [[lon, lat] for lat, lon in segment]
                for segment in simplified_segments
            ],
        }
    )

    response = {
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
        "recommended_mosque": mosque_summary,
        "encoded_polyline": _encode_polyline(legacy_coordinates),
        "encoded_polylines": [_encode_polyline(segment) for segment in simplified_segments],
        "access_connectors": access_connectors,
        "route_summary": {
            "distance_km": best["distance_km"],
            "road_distance_km": best.get("road_distance_km", best["distance_km"]),
            "access_connector_distance_km": best.get("access_connector_distance_km", 0.0),
            "estimated_time_minutes": best["estimated_time_minutes"],
            "arrival_to_mosque_minutes": best["arrival_to_mosque_minutes"],
            "arrival_status": best.get("arrival_status", "unknown"),
            "minutes_before_prayer": best.get("minutes_before_prayer", 0.0),
            "multi_objective_score": best["multi_objective_score"],
            "route_nodes_count": best["route_nodes_count"],
            "geometry_points_count": sum(len(segment) for segment in simplified_segments),
            "geometry_original_points_count": sum(len(segment) for segment in raw_segments),
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
            "geometry": geometry,
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
    if phase_timings_ms:
        response["timings_ms"] = phase_timings_ms
    return response


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
    response = _OSRM_SESSION.get(
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
        "waypoint_coordinates": [
            (float(location[1]), float(location[0]))
            for waypoint in waypoints
            if isinstance((location := waypoint.get("location")), list) and len(location) >= 2
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
        waypoint_coordinates = route.get("waypoint_coordinates") or []
        access_connectors: List[List[Coordinate]] = []
        if waypoint_coordinates:
            connector_pairs = [(start, waypoint_coordinates[0])]
            if len(waypoint_coordinates) >= 2:
                connector_pairs.append(((mlat, mlon), waypoint_coordinates[1]))
            if len(waypoint_coordinates) >= 3:
                connector_pairs.append((end, waypoint_coordinates[-1]))
            access_connectors = _connector_segments(*connector_pairs)

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
            "route_segments": [coords],
            "access_connectors": access_connectors,
            "road_distance_km": round(dist_km, 3),
            "access_connector_distance_km": round(
                sum(
                    haversine_km(segment[0][0], segment[0][1], segment[1][0], segment[1][1])
                    for segment in access_connectors
                ),
                3,
            ),
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


def _route_to_mosque_uncached(
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
    request_started = time.perf_counter()
    phase_timings_ms: Dict[str, float] = {}
    start = (float(start_lat), float(start_lon))
    mosque_point = (float(mosque["latitude"]), float(mosque["longitude"]))
    requested_candidates = 1

    if start == mosque_point:
        raise ValueError("Titik awal dan masjid tujuan tidak boleh sama.")

    G = None
    graph_phase_started = time.perf_counter()
    graph_runtime = get_road_graph_status(graphml_path)
    if graph_runtime["status"] == "loading" and not graph_runtime["ready"]:
        result = _route_via_osrm_fallback(
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
            fallback_note="Graph OSM lokal sedang dipanaskan. OSRM dipakai agar request tidak menunggu cold-load GraphML.",
        )
        phase_timings_ms["graph_readiness_check"] = round(
            (time.perf_counter() - graph_phase_started) * 1000, 2
        )
        phase_timings_ms["total"] = round((time.perf_counter() - request_started) * 1000, 2)
        result["timings_ms"] = phase_timings_ms
        result["graph_runtime"] = graph_runtime
        return result
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
    phase_timings_ms["graph_load_or_build"] = round(
        (time.perf_counter() - graph_phase_started) * 1000, 2
    )

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

    snap_started = time.perf_counter()
    try:
        snapped_route = _best_edge_snapped_route(
            G,
            start,
            mosque_point,
            algorithm=algorithm,
            candidate_count=EDGE_SNAP_CANDIDATE_COUNT,
        )
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
            fallback_note=f"Titik jalan lokal tidak ditemukan: {exc}",
        )
    phase_timings_ms["snap_to_road"] = round((time.perf_counter() - snap_started) * 1000, 2)

    phase_timings_ms["pathfinding"] = phase_timings_ms["snap_to_road"]

    geometry_started = time.perf_counter()
    dist_m = snapped_route["distance_m"]
    time_s = snapped_route["time_s"]
    road_coords = snapped_route["road_coordinates"]
    connector_m = snapped_route["connector_m"]
    capacity_num = {"large": 1.0, "medium": 0.65, "small": 0.35}.get(mosque.get("capacity_proxy"), 0.5)
    priority = float(mosque.get("priority_score", 0.5))
    result = {
        "mosque": mosque,
        "distance_km": round(dist_m / 1000, 3),
        "estimated_time_minutes": round(time_s / 60, 2),
        "arrival_to_mosque_minutes": round(time_s / 60, 2),
        "route_nodes_count": len(road_coords),
        "capacity_score": capacity_num,
        "priority_score": priority,
        "prayer_penalty": 0.0,
        "route_coordinates": road_coords,
        "route_segments": [road_coords],
        "access_connectors": snapped_route["access_connectors"],
        "road_distance_km": round(snapped_route["road_length_m"] / 1000, 3),
        "access_connector_distance_km": round(connector_m / 1000, 3),
        "snap_diagnostics": {
            "start_connector_m": round(snapped_route["start_snap"].connector_m, 2),
            "destination_connector_m": round(snapped_route["destination_snap"].connector_m, 2),
            "snap_mode": "edge_projection",
        },
        "multi_objective_score": round(0.10 * (1.0 - capacity_num) + 0.10 * (1.0 - priority), 4),
    }
    phase_timings_ms["geometry_and_metrics"] = round(
        (time.perf_counter() - geometry_started) * 1000, 2
    )
    phase_timings_ms["total"] = round((time.perf_counter() - request_started) * 1000, 2)
    response = _format_route_response(
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
        elapsed_ms=phase_timings_ms["total"],
        reason="Rute tercepat menuju masjid terpilih dihitung dengan Dijkstra/A* pada graph jalan OpenStreetMap lokal.",
        phase_timings_ms=phase_timings_ms,
    )
    response["snap_diagnostics"] = result["snap_diagnostics"]
    response["graph_runtime"] = get_road_graph_status(graphml_path)
    return response


def _route_cache_key(
    *,
    start_lat: float,
    start_lon: float,
    mosque: Dict[str, Any],
    algorithm: str,
    graphml_path: Path,
    dataset_id: Optional[str],
) -> tuple:
    try:
        graph_version = graphml_path.stat().st_mtime_ns
    except OSError:
        graph_version = 0
    mosque_id = mosque.get("_key") or mosque.get("id") or mosque.get("name")
    return (
        "edge-snap-geometry-v4",
        str(dataset_id or "latest"),
        str(graphml_path.resolve()),
        graph_version,
        round(float(start_lat), 5),
        round(float(start_lon), 5),
        str(mosque_id),
        round(float(mosque["latitude"]), 5),
        round(float(mosque["longitude"]), 5),
        algorithm.lower(),
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
    key = _route_cache_key(
        start_lat=start_lat,
        start_lon=start_lon,
        mosque=mosque,
        algorithm=algorithm,
        graphml_path=graphml_path,
        dataset_id=dataset_id,
    )
    now = time.monotonic()
    with _ROUTE_CACHE_LOCK:
        cached = _ROUTE_CACHE.get(key)
        if cached and now - cached[0] <= ROUTE_CACHE_TTL_SECONDS:
            _ROUTE_CACHE.move_to_end(key)
            result = copy.deepcopy(cached[1])
            result["cache_hit"] = True
            return result
        if cached:
            _ROUTE_CACHE.pop(key, None)

    # Concurrent double-clicks/share-device requests reuse one computation.
    singleflight_lock = _ROUTE_SINGLEFLIGHT_LOCKS[hash(key) % len(_ROUTE_SINGLEFLIGHT_LOCKS)]
    with singleflight_lock:
        now = time.monotonic()
        with _ROUTE_CACHE_LOCK:
            cached = _ROUTE_CACHE.get(key)
            if cached and now - cached[0] <= ROUTE_CACHE_TTL_SECONDS:
                _ROUTE_CACHE.move_to_end(key)
                result = copy.deepcopy(cached[1])
                result["cache_hit"] = True
                return result

        result = _route_to_mosque_uncached(
            start_lat=start_lat,
            start_lon=start_lon,
            mosque=mosque,
            algorithm=algorithm,
            auto_build_osm=auto_build_osm,
            buffer_km=buffer_km,
            graphml_path=graphml_path,
            dataset_id=dataset_id,
            fetch_mosques_fn=fetch_mosques_fn,
            save_osm_cache_fn=save_osm_cache_fn,
        )
        result["cache_hit"] = False
        with _ROUTE_CACHE_LOCK:
            _ROUTE_CACHE[key] = (time.monotonic(), copy.deepcopy(result))
            _ROUTE_CACHE.move_to_end(key)
            while len(_ROUTE_CACHE) > ROUTE_CACHE_MAX_ENTRIES:
                _ROUTE_CACHE.popitem(last=False)
        return result


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
    request_started = time.perf_counter()
    phase_timings_ms: Dict[str, float] = {}
    start = (float(start_lat), float(start_lon))
    end = (float(end_lat), float(end_lon))
    is_one_way = (start == end)

    # Resolve prayer name to HH:MM time
    prayer_started = time.perf_counter()
    if prayer_time and prayer_time.lower() in {"fajr", "subuh", "dhuhr", "dzuhur", "asr", "ashar", "maghrib", "isha", "isya"}:
        resolved_name = prayer_time.lower()
        name_map = {
            "subuh": "fajr",
            "dzuhur": "dhuhr",
            "ashar": "asr",
            "isya": "isha"
        }
        api_name = name_map.get(resolved_name, resolved_name)
        from app.infrastructure.services.prayer_time import calculate_offline_prayer_times
        import datetime as dt_module
        try:
            timings = calculate_offline_prayer_times(float(start_lat), float(start_lon), dt_module.date.today())
            raw_time = timings.get(api_name)
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
    phase_timings_ms["prayer_resolution"] = round((time.perf_counter() - prayer_started) * 1000, 2)

    requested_candidates = max(1, int(max_candidates))
    effective_corridor_km = max(float(buffer_km), 5.0)

    # Calculate combined bounding box for database query optimization
    min_lat, max_lat = sorted([start[0], end[0]])
    min_lon, max_lon = sorted([start[1], end[1]])
    buffer_deg = effective_corridor_km / 100.0
    fallback_radius_km = max(25.0, effective_corridor_km * 4)
    # Query the actual route corridor first. The repository callback performs a
    # geo-nearest fallback only when this much cheaper indexed query is empty.
    south_combined = min_lat - buffer_deg
    north_combined = max_lat + buffer_deg
    west_combined = min_lon - buffer_deg
    east_combined = max_lon + buffer_deg
    bounds_query = (south_combined, north_combined, west_combined, east_combined)

    mosque_query_started = time.perf_counter()
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
    phase_timings_ms["mosque_query"] = round((time.perf_counter() - mosque_query_started) * 1000, 2)

    if not mosques:
        raise ValueError("Dataset aktif tidak memiliki data masjid yang valid.")

    candidate_selection_started = time.perf_counter()
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
    phase_timings_ms["candidate_selection"] = round(
        (time.perf_counter() - candidate_selection_started) * 1000, 2
    )

    G = None
    graph_phase_started = time.perf_counter()
    graph_runtime = get_road_graph_status(graphml_path)
    if graph_runtime["status"] == "loading" and not graph_runtime["ready"]:
        result = _route_via_osrm_fallback(
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
            fallback_note="Graph OSM lokal sedang dipanaskan. OSRM dipakai agar request tidak menunggu cold-load GraphML.",
            profile=profile,
        )
        phase_timings_ms["graph_readiness_check"] = round(
            (time.perf_counter() - graph_phase_started) * 1000, 2
        )
        phase_timings_ms["total"] = round((time.perf_counter() - request_started) * 1000, 2)
        result["timings_ms"] = phase_timings_ms
        result["graph_runtime"] = graph_runtime
        return result
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
    phase_timings_ms["graph_load_or_build"] = round(
        (time.perf_counter() - graph_phase_started) * 1000, 2
    )
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

    snap_started = time.perf_counter()
    mosque_points = [
        (float(m["latitude"]), float(m["longitude"]))
        for m in candidates_in_graph
    ]
    try:
        shared_snap_groups = nearest_road_edge_candidates_batch(
            G,
            [start, *mosque_points, end],
            k=EDGE_SNAP_BATCH_CANDIDATE_COUNT,
        )
        routes_from_start = _batch_edge_routes_from_start(
            G,
            start,
            mosque_points,
            candidate_count=EDGE_SNAP_BATCH_CANDIDATE_COUNT,
            snap_groups=shared_snap_groups[:-1],
        )
        routes_to_end = (
            [None] * len(mosque_points)
            if is_one_way
            else _batch_edge_routes_to_destination(
                G,
                mosque_points,
                end,
                candidate_count=EDGE_SNAP_BATCH_CANDIDATE_COUNT,
                snap_groups=shared_snap_groups[1:],
            )
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
            fallback_note=f"Edge snapping lokal gagal: {exc}",
            profile=profile,
        )
    phase_timings_ms["snap_to_road"] = round((time.perf_counter() - snap_started) * 1000, 2)

    results: List[Dict[str, Any]] = []
    start_clock = time.perf_counter()
    for mosque, route_1, route_2 in zip(candidates_in_graph, routes_from_start, routes_to_end):
        if route_1 is None or (not is_one_way and route_2 is None):
            continue
        route_segments = [route_1["road_coordinates"]]
        access_connectors = list(route_1["access_connectors"])
        dist_m = route_1["distance_m"]
        road_dist_m = route_1["road_length_m"]
        time_s = route_1["time_s"]
        connector_m = route_1["connector_m"]
        route_nodes = list(route_1["route_nodes"])
        if route_2 is not None:
            route_segments.append(route_2["road_coordinates"])
            access_connectors.extend(route_2["access_connectors"])
            dist_m += route_2["distance_m"]
            road_dist_m += route_2["road_length_m"]
            time_s += route_2["time_s"]
            connector_m += route_2["connector_m"]
            route_nodes.extend(route_2["route_nodes"])

        to_mosque_minutes = route_1["time_s"] / 60.0
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
            "route_segments": route_segments,
            "access_connectors": access_connectors,
            "road_distance_km": round(road_dist_m / 1000, 3),
            "access_connector_distance_km": round(connector_m / 1000, 3),
            "route_nodes": route_nodes,
            "_route_1": route_1,
            "_route_2": route_2,
        })

    phase_timings_ms["candidate_ranking_paths"] = round((time.perf_counter() - start_clock) * 1000, 2)

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
    scoring_started = time.perf_counter()
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
    phase_timings_ms["multi_objective_scoring"] = round(
        (time.perf_counter() - scoring_started) * 1000, 2
    )

    geometry_started = time.perf_counter()
    best_mosque_point = (
        float(best_res["mosque"]["latitude"]),
        float(best_res["mosque"]["longitude"]),
    )
    if algorithm.lower() not in {"astar", "a*"}:
        # Candidate ranking already produced the complete Dijkstra paths and
        # geometries. Re-running the same search added latency without changing
        # the winner or response.
        final_path_algorithm = "dijkstra_edge_batch_reuse"
    else:
        final_path_algorithm = "astar_edge_projection"
        try:
            final_route_1 = _best_edge_snapped_route(
                G,
                start,
                best_mosque_point,
                algorithm=algorithm,
                candidate_count=EDGE_SNAP_CANDIDATE_COUNT,
            )
            final_route_2 = (
                None
                if is_one_way
                else _best_edge_snapped_route(
                    G,
                    best_mosque_point,
                    end,
                    algorithm=algorithm,
                    candidate_count=EDGE_SNAP_CANDIDATE_COUNT,
                )
            )
            final_routes = [route for route in (final_route_1, final_route_2) if route is not None]
            dist_m = sum(route["distance_m"] for route in final_routes)
            road_dist_m = sum(route["road_length_m"] for route in final_routes)
            connector_m = sum(route["connector_m"] for route in final_routes)
            time_s = sum(route["time_s"] for route in final_routes)
            to_mosque_minutes = final_route_1["time_s"] / 60.0
            prayer_penalty, arrival_status, minutes_before_prayer = _prayer_arrival_details(
                to_mosque_minutes, current_time, prayer_time
            )
            best_res.update(
                distance_km=round(dist_m / 1000, 3),
                road_distance_km=round(road_dist_m / 1000, 3),
                access_connector_distance_km=round(connector_m / 1000, 3),
                estimated_time_minutes=round(time_s / 60, 2),
                arrival_to_mosque_minutes=round(to_mosque_minutes, 2),
                prayer_penalty=prayer_penalty,
                arrival_status=arrival_status,
                minutes_before_prayer=round(minutes_before_prayer, 1),
                route_segments=[route["road_coordinates"] for route in final_routes],
                access_connectors=[
                    segment
                    for route in final_routes
                    for segment in route["access_connectors"]
                ],
                route_nodes=[node for route in final_routes for node in route["route_nodes"]],
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound, ValueError):
            final_path_algorithm = "dijkstra_edge_batch_fallback"
    best_res["route_coordinates"] = _stitch_route_segments(*best_res["route_segments"])
    best_res["route_nodes_count"] = sum(len(segment) for segment in best_res["route_segments"])
    phase_timings_ms["geometry"] = round((time.perf_counter() - geometry_started) * 1000, 2)
    phase_timings_ms["total"] = round((time.perf_counter() - request_started) * 1000, 2)
    elapsed_ms = phase_timings_ms["total"]

    response = _format_route_response(
        algorithm_label=(
            "A* (Dijkstra Multi-Destination Ranking)"
            if algorithm.lower() in {"astar", "a*"}
            else "Dijkstra (Multi-Destination)"
        ),
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
        phase_timings_ms=phase_timings_ms,
        reason=(
            "Rute dipilih pada graph jalan OpenStreetMap, lalu dievaluasi dengan skor "
            "multi-objective: waktu tempuh, jarak/biaya proxy, kecocokan waktu shalat, capacity proxy, dan priority score."
        ),
    )
    response["pathfinding"] = {
        "candidate_ranking_algorithm": "dijkstra_edge_projection_batch",
        "final_path_algorithm": final_path_algorithm,
    }
    response["graph_runtime"] = get_road_graph_status(graphml_path)
    return response
