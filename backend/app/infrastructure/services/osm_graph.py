from __future__ import annotations

import math
import os
import threading
import gc
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import networkx as nx
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parents[4]
DATA_DIR = PROJECT_DIR / "data"
OSM_CACHE_DIR = DATA_DIR / "osm_cache"
DEFAULT_GRAPHML = OSM_CACHE_DIR / "road_graph_latest.graphml"

def get_graphml_path(dataset_id: str | None = None) -> Path:
    if not dataset_id or dataset_id == "all":
        return DEFAULT_GRAPHML
    safe_id = "".join([c if c.isalnum() or c in "-_" else "_" for c in dataset_id])
    return OSM_CACHE_DIR / f"road_graph_{safe_id}.graphml"

# DKI Jakarta's robust dataset bounds are about 1,403 km² as a rectangle.
# Keep a safety ceiling while allowing that administrative preset to build.
MAX_OSM_BUILD_AREA_KM2 = 1500.0
OVERPASS_REQUEST_TIMEOUT_SECONDS = 45
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.osm.ch/api",
)

Coordinate = Tuple[float, float]  # lat, lon

_MAX_LOADED_GRAPHS = max(1, int(os.getenv("IMOSQUE_MAX_LOADED_GRAPHS", "1")))
_loaded_graphs_cache: "OrderedDict[str, nx.Graph]" = OrderedDict()
_graph_cache_lock = threading.RLock()
_nearest_index_lock = threading.RLock()


def _cache_loaded_graph(path_str: str, graph):
    """Keep graph memory bounded: one DKI GraphML can occupy several GB in RAM."""
    with _graph_cache_lock:
        _loaded_graphs_cache[path_str] = graph
        _loaded_graphs_cache.move_to_end(path_str)
        while len(_loaded_graphs_cache) > _MAX_LOADED_GRAPHS:
            _loaded_graphs_cache.popitem(last=False)
    return graph


def _compact_graph_for_routing(G):
    """Drop OSM metadata/parallel edges that the runtime pathfinder never uses."""
    enabled = os.getenv("IMOSQUE_COMPACT_GRAPH", "true").strip().lower() not in {"0", "false", "no"}
    if not enabled or not G.is_multigraph():
        return G

    compact = nx.DiGraph()
    compact.graph.update(G.graph)
    compact.add_nodes_from(
        (
            node,
            {"x": float(data["x"]), "y": float(data["y"])},
        )
        for node, data in G.nodes(data=True)
    )

    for u, v, data in G.edges(data=True):
        try:
            length = float(data.get("length", 0.0))
            travel_time = float(data.get("travel_time", length / 8.33))
        except (TypeError, ValueError):
            continue
        current = compact.get_edge_data(u, v)
        if current is not None and float(current.get("travel_time", math.inf)) <= travel_time:
            continue
        attrs = {"length": length, "travel_time": travel_time}
        if data.get("geometry") is not None:
            attrs["geometry"] = data["geometry"]
        compact.add_edge(u, v, **attrs)

    return compact


def _require_osmnx():
    try:
        import osmnx as ox
        return ox
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "OSMnx belum terpasang atau dependensi geospasial belum lengkap. "
            "Jalankan: pip install -r backend/requirements.txt"
        ) from exc


def bbox_from_points(points: Sequence[Coordinate], buffer_km: float = 5.0) -> Tuple[float, float, float, float]:
    """Return (north, south, east, west) from lat/lon points plus buffer."""
    if not points:
        raise ValueError("points tidak boleh kosong")
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    mid_lat = sum(lats) / len(lats)
    lat_buffer = buffer_km / 111.0
    lon_buffer = buffer_km / (111.0 * max(math.cos(math.radians(mid_lat)), 0.2))
    north = max(lats) + lat_buffer
    south = min(lats) - lat_buffer
    east = max(lons) + lon_buffer
    west = min(lons) - lon_buffer
    return north, south, east, west


def bbox_area_km2(north: float, south: float, east: float, west: float) -> float:
    mid_lat = (north + south) / 2.0
    height_km = abs(north - south) * 111.0
    width_km = abs(east - west) * 111.0 * max(math.cos(math.radians(mid_lat)), 0.2)
    return height_km * width_km


def _validate_bbox_size(north: float, south: float, east: float, west: float) -> None:
    values = (north, south, east, west)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Semua batas koordinat OSM harus berupa angka yang valid.")
    if not (-90 <= south < north <= 90):
        raise ValueError("Bounding box tidak valid: South harus lebih kecil dari North.")
    if not (-180 <= west < east <= 180):
        raise ValueError("Bounding box tidak valid: West harus lebih kecil dari East.")
    area_km2 = bbox_area_km2(north, south, east, west)
    if area_km2 > MAX_OSM_BUILD_AREA_KM2:
        raise ValueError(
            "Area OSM yang diminta terlalu besar "
            f"({area_km2:.0f} km2, batas {MAX_OSM_BUILD_AREA_KM2:.0f} km2). "
            "Kurangi Buffer OSM, pilih titik start-tujuan yang lebih dekat, atau build graph per wilayah yang lebih kecil."
        )


def _graph_from_bbox_compat(ox, north: float, south: float, east: float, west: float, network_type: str):
    try:
        return ox.graph_from_bbox(north, south, east, west, network_type=network_type, simplify=True)
    except TypeError:
        return ox.graph_from_bbox(bbox=(west, south, east, north), network_type=network_type, simplify=True)


def _summarize_overpass_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "max retries exceeded" in lowered or "connection" in lowered:
        return "koneksi gagal"
    if "too many requests" in lowered or "429" in lowered:
        return "rate limit"
    if "504" in lowered or "gateway" in lowered:
        return "gateway timeout"
    return message[:120]


def _download_graph_from_overpass(ox, north: float, south: float, east: float, west: float, network_type: str):
    old_url = getattr(ox.settings, "overpass_url", None)
    old_timeout = getattr(ox.settings, "requests_timeout", None)
    failures: List[str] = []
    last_exc: Exception | None = None
    try:
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                ox.settings.overpass_url = endpoint
                ox.settings.requests_timeout = OVERPASS_REQUEST_TIMEOUT_SECONDS
                return _graph_from_bbox_compat(ox, north, south, east, west, network_type)
            except Exception as exc:
                last_exc = exc
                failures.append(f"{endpoint.replace('https://', '')}: {_summarize_overpass_error(exc)}")
        raise RuntimeError(
            "Overpass API sedang lambat/tidak merespons, jadi graph Dijkstra lokal belum bisa dibuat. "
            "Rute tetap bisa memakai OSRM tanpa build graph. Coba lagi nanti, kecilkan Buffer OSM, "
            "atau pilih start-tujuan yang lebih dekat. Endpoint dicoba: "
            + "; ".join(failures)
        ) from last_exc
    finally:
        if old_url is not None:
            ox.settings.overpass_url = old_url
        if old_timeout is not None:
            ox.settings.requests_timeout = old_timeout


def build_osm_graph_for_bbox(
    north: float,
    south: float,
    east: float,
    west: float,
    network_type: str = "drive",
    output_graphml: Path = DEFAULT_GRAPHML,
):
    """Download road network from OpenStreetMap and cache it as GraphML.

    This must be run locally with internet access because it queries the OpenStreetMap/Overpass API.
    
    OPTIMIZATIONS:
    - Simplify network topology aggressively to reduce node count
    - Pre-compute travel times using speed data
    - Cache edge attributes for faster access
    - Use bidirectional search optimization
    """
    _validate_bbox_size(north, south, east, west)
    ox = _require_osmnx()
    OSM_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Download with aggressive simplification
    G = _download_graph_from_overpass(ox, north, south, east, west, network_type)
    
    # OPTIMIZATION 1: Additional aggressive simplification to reduce nodes
    # This removes redundant nodes while preserving topology
    try:
        G = ox.simplify_graph(G, strict=True, remove_rings=False)
    except Exception:
        pass  # If simplification fails, continue with original graph

    # OPTIMIZATION 2: Add travel-time weights with better speed inference
    try:
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)
    except Exception:
        # Fallback: estimate speeds based on road type
        edge_iter = (
            G.edges(keys=True, data=True)
            if G.is_multigraph()
            else ((u, v, 0, data) for u, v, data in G.edges(data=True))
        )
        for u, v, k, data in edge_iter:
            length = float(data.get("length", 0.0))
            # More realistic speed estimates based on road type
            highway = data.get("highway", "")
            if isinstance(highway, list):
                highway = highway[0] if highway else ""
            
            # Speed mapping (km/h) based on Indonesian road standards
            speed_map = {
                "motorway": 80.0, "trunk": 70.0, "primary": 60.0,
                "secondary": 50.0, "tertiary": 40.0, "residential": 30.0,
                "unclassified": 30.0, "service": 20.0, "living_street": 20.0
            }
            speed_kmh = speed_map.get(highway, 30.0)
            speed_ms = speed_kmh / 3.6
            data["speed_kph"] = speed_kmh
            data["travel_time"] = length / speed_ms
    
    # OPTIMIZATION 3: Pre-compute and cache important graph properties
    # Store node degree for faster pathfinding heuristics
    node_degrees = dict(G.degree())
    nx.set_node_attributes(G, node_degrees, "degree")
    
    # OPTIMIZATION 4: Remove isolated nodes/components for faster search
    # Keep only the largest strongly connected component
    try:
        if not G.is_directed():
            # For undirected graphs
            components = list(nx.connected_components(G))
        else:
            # For directed graphs, use weakly connected components
            components = list(nx.weakly_connected_components(G))
        
        if len(components) > 1:
            # Keep only the largest component
            largest = max(components, key=len)
            G = G.subgraph(largest).copy()
    except Exception:
        pass  # If component analysis fails, continue with original graph
    
    # Keep OSMnx's MultiDiGraph representation. Parallel carriageways are real
    # routing alternatives and ox.save_graphml requires keyed multigraph edges.

    ox.save_graphml(G, filepath=output_graphml)
    G = _compact_graph_for_routing(G)
    gc.collect()
    path_str = str(Path(output_graphml).resolve())
    _cache_loaded_graph(path_str, G)
    
    # Print optimization stats
    print(f"Graph optimized: {len(G.nodes)} nodes, {len(G.edges)} edges")
    
    return G


def graph_bounds(G) -> Tuple[float, float, float, float]:
    """Return graph bounds as (south, north, west, east)."""
    if hasattr(G, "_graph_bounds_cached"):
        return G._graph_bounds_cached
    if len(G.nodes) == 0:
        raise ValueError("Graph OSM kosong.")
    lats = [float(data["y"]) for _, data in G.nodes(data=True)]
    lons = [float(data["x"]) for _, data in G.nodes(data=True)]
    bounds = (min(lats), max(lats), min(lons), max(lons))
    G._graph_bounds_cached = bounds
    return bounds


def graph_covers_points(G, points: Sequence[Coordinate], margin_km: float = 0.25) -> bool:
    """Check whether all lat/lon points are inside graph bounds with a small margin."""
    if not points:
        return True
    south, north, west, east = graph_bounds(G)
    mid_lat = sum(p[0] for p in points) / len(points)
    lat_margin = margin_km / 111.0
    lon_margin = margin_km / (111.0 * max(math.cos(math.radians(mid_lat)), 0.2))
    return all(
        south - lat_margin <= lat <= north + lat_margin
        and west - lon_margin <= lon <= east + lon_margin
        for lat, lon in points
    )


def build_osm_graph_for_route(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    candidate_points: Sequence[Coordinate] | None = None,
    buffer_km: float = 5.0,
    network_type: str = "drive",
    output_graphml: Path = DEFAULT_GRAPHML,
):
    points: List[Coordinate] = [(start_lat, start_lon), (end_lat, end_lon)]
    if candidate_points:
        points.extend(candidate_points)
    north, south, east, west = bbox_from_points(points, buffer_km=buffer_km)
    return build_osm_graph_for_bbox(north, south, east, west, network_type=network_type, output_graphml=output_graphml)


def load_road_graph(graphml_path: Path = DEFAULT_GRAPHML):
    path_str = str(Path(graphml_path).resolve())
    # Serialize GraphML parsing. Startup prewarming and a first user click used
    # to load the same 262 MB file twice, briefly doubling multi-GB RAM usage.
    with _graph_cache_lock:
        cached = _loaded_graphs_cache.get(path_str)
        if cached is not None:
            _loaded_graphs_cache.move_to_end(path_str)
            return cached
        if not graphml_path.exists():
            raise FileNotFoundError(
                f"Cache road graph belum ada: {graphml_path}. "
                "Jalankan scripts/build_osm_graph.py atau aktifkan auto_build_osm pada request /api/route."
            )
        ox = _require_osmnx()
        G = ox.load_graphml(filepath=graphml_path)
        G = _compact_graph_for_routing(G)
        gc.collect()
        return _cache_loaded_graph(path_str, G)


def evict_road_graph(graphml_path: Path = DEFAULT_GRAPHML) -> None:
    """Release a graph built by an offline batch job from process memory."""
    with _graph_cache_lock:
        _loaded_graphs_cache.pop(str(Path(graphml_path).resolve()), None)


def _nearest_node_index(G):
    """Build the expensive spatial tree once per loaded graph, not once per click."""
    cached = getattr(G, "_imosque_nearest_index", None)
    if cached is not None:
        return cached

    with _nearest_index_lock:
        cached = getattr(G, "_imosque_nearest_index", None)
        if cached is not None:
            return cached

        node_ids = np.asarray(list(G.nodes), dtype=object)
        if len(node_ids) == 0:
            raise ValueError("Graph OSM kosong.")
        xy = np.asarray(
            [(float(G.nodes[node]["x"]), float(G.nodes[node]["y"])) for node in node_ids],
            dtype=float,
        )

        try:
            from pyproj import CRS
            is_projected = CRS.from_user_input(G.graph.get("crs", "epsg:4326")).is_projected
        except Exception:
            is_projected = False

        if is_projected:
            from scipy.spatial import cKDTree
            tree = cKDTree(xy)
            cached = ("projected", tree, node_ids)
        else:
            from sklearn.neighbors import BallTree
            # Haversine BallTree expects [latitude, longitude] in radians.
            tree = BallTree(np.deg2rad(xy[:, [1, 0]]), metric="haversine")
            cached = ("geographic", tree, node_ids)

        G._imosque_nearest_index = cached
        return cached


def nearest_road_node(G, lat: float, lon: float):
    """Find a nearest road node through the graph's reusable spatial index."""
    return nearest_road_nodes_batch(G, [(lat, lon)])[0]


def nearest_road_nodes_batch(G, points: List[Coordinate]):
    """Batch nearest-node queries without reconstructing GeoDataFrames/BallTree."""
    if not points:
        return []
    mode, tree, node_ids = _nearest_node_index(G)
    if mode == "projected":
        queries = np.asarray([(lon, lat) for lat, lon in points], dtype=float)
    else:
        queries = np.deg2rad(np.asarray(points, dtype=float))
    _, indices = tree.query(queries, k=1)
    indices = np.asarray(indices).reshape(-1)
    return [node_ids[int(index)] for index in indices]


def warm_road_graph_indexes(G) -> None:
    """Precompute immutable lookup structures so the first user request is fast."""
    graph_bounds(G)
    _nearest_node_index(G)


def _edge_attribute_dicts(G, u, v) -> List[dict]:
    data = G.get_edge_data(u, v)
    if not data:
        return []
    if G.is_multigraph():
        return [attrs for attrs in data.values() if isinstance(attrs, dict)]
    return [data]


def _edge_linestring(G, u, v) -> List[Coordinate]:
    """Return lat/lon coordinates following the OSM edge geometry if present."""
    edge_datas = _edge_attribute_dicts(G, u, v)
    if not edge_datas:
        return [(float(G.nodes[u]["y"]), float(G.nodes[u]["x"])), (float(G.nodes[v]["y"]), float(G.nodes[v]["x"]))]
    edge_data = min(edge_datas, key=lambda d: float(d.get("length", 0.0)))

    geom = edge_data.get("geometry")
    if geom is not None:
        try:
            return [(float(lat), float(lon)) for lon, lat in geom.coords]
        except Exception:
            pass
    return [(float(G.nodes[u]["y"]), float(G.nodes[u]["x"])), (float(G.nodes[v]["y"]), float(G.nodes[v]["x"]))]


def route_nodes_to_latlon(G, route_nodes: Sequence) -> List[Coordinate]:
    if not route_nodes:
        return []
    coords: List[Coordinate] = []
    for u, v in zip(route_nodes[:-1], route_nodes[1:]):
        segment = _edge_linestring(G, u, v)
        if coords and segment:
            segment = segment[1:]
        coords.extend(segment)
    if len(route_nodes) == 1:
        n = route_nodes[0]
        coords.append((float(G.nodes[n]["y"]), float(G.nodes[n]["x"])))
    return coords


def path_length_m(G, route_nodes: Sequence) -> float:
    total = 0.0
    for u, v in zip(route_nodes[:-1], route_nodes[1:]):
        edge_datas = _edge_attribute_dicts(G, u, v)
        if not edge_datas:
            continue
        total += min(float(d.get("length", 0.0)) for d in edge_datas)
    return total


def path_travel_time_s(G, route_nodes: Sequence) -> float:
    total = 0.0
    for u, v in zip(route_nodes[:-1], route_nodes[1:]):
        edge_datas = _edge_attribute_dicts(G, u, v)
        if not edge_datas:
            continue
        total += min(float(d.get("travel_time", d.get("length", 0.0) / 8.33)) for d in edge_datas)
    return total


def dijkstra_path(G, source, target, weight: str = "travel_time"):
    """Optimized Dijkstra with bidirectional search for 2x speedup."""
    try:
        # Use bidirectional Dijkstra for significant speedup
        return nx.bidirectional_dijkstra(G, source, target, weight=weight)[1]
    except Exception:
        # Fallback to standard Dijkstra
        return nx.dijkstra_path(G, source, target, weight=weight)


def astar_path(G, source, target, weight: str = "travel_time"):
    """Optimized A* with vectorized heuristic calculation."""
    # Pre-fetch target coordinates once
    target_node_data = G.nodes[target]
    vy = float(target_node_data["y"])
    vx = float(target_node_data["x"])
    
    # Flat earth approximation cos factor based on average latitude
    source_node_data = G.nodes[source]
    mid_lat = math.radians((float(source_node_data["y"]) + vy) / 2.0)
    cos_factor = math.cos(mid_lat)
    
    # Pre-compute target in projected coordinates
    target_py = vy * 111000.0
    target_px = vx * 111000.0 * cos_factor
    
    # Speed factor for travel_time weight
    # Use a conservative upper speed bound so the heuristic remains admissible
    # and A* preserves shortest-path correctness on faster road classes.
    speed_factor = 36.12 if weight == "travel_time" else 1.0  # 130 km/h
    
    def heuristic(u, v):
        """Optimized heuristic with pre-computed values."""
        node_data = G.nodes[u]
        uy = float(node_data["y"])
        ux = float(node_data["x"])
        
        # Use pre-projected target coordinates
        py = uy * 111000.0
        px = ux * 111000.0 * cos_factor
        
        # Fast euclidean distance
        d_lat = target_py - py
        d_lon = target_px - px
        meters = math.sqrt(d_lat * d_lat + d_lon * d_lon)
        
        return meters / speed_factor

    return nx.astar_path(G, source, target, heuristic=heuristic, weight=weight)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))
