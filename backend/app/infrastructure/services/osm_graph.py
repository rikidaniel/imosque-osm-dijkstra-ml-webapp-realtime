from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import networkx as nx

PROJECT_DIR = Path(__file__).resolve().parents[4]
DATA_DIR = PROJECT_DIR / "data"
OSM_CACHE_DIR = DATA_DIR / "osm_cache"
DEFAULT_GRAPHML = OSM_CACHE_DIR / "road_graph_latest.graphml"

def get_graphml_path(dataset_id: str | None = None) -> Path:
    if not dataset_id or dataset_id == "all":
        return DEFAULT_GRAPHML
    safe_id = "".join([c if c.isalnum() or c in "-_" else "_" for c in dataset_id])
    return OSM_CACHE_DIR / f"road_graph_{safe_id}.graphml"

MAX_OSM_BUILD_AREA_KM2 = 1200.0
OVERPASS_REQUEST_TIMEOUT_SECONDS = 45
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.osm.ch/api",
)

Coordinate = Tuple[float, float]  # lat, lon

_loaded_graphs_cache = {}


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
    """
    _validate_bbox_size(north, south, east, west)
    ox = _require_osmnx()
    OSM_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    G = _download_graph_from_overpass(ox, north, south, east, west, network_type)

    # Add travel-time weights if possible. If speeds cannot be inferred, length remains available.
    try:
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)
    except Exception:
        for _, _, _, data in G.edges(keys=True, data=True):
            length = float(data.get("length", 0.0))
            data["travel_time"] = length / 8.33  # conservative fallback: 30 km/h ~= 8.33 m/s

    ox.save_graphml(G, filepath=output_graphml)
    path_str = str(Path(output_graphml).resolve())
    _loaded_graphs_cache[path_str] = G
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
    if path_str in _loaded_graphs_cache:
        return _loaded_graphs_cache[path_str]

    if not graphml_path.exists():
        raise FileNotFoundError(
            f"Cache road graph belum ada: {graphml_path}. "
            "Jalankan scripts/build_osm_graph.py atau aktifkan auto_build_osm pada request /api/route."
        )
    ox = _require_osmnx()
    G = ox.load_graphml(filepath=graphml_path)
    _loaded_graphs_cache[path_str] = G
    return G


def nearest_road_node(G, lat: float, lon: float):
    ox = _require_osmnx()
    return ox.distance.nearest_nodes(G, X=lon, Y=lat)


def _edge_linestring(G, u, v) -> List[Coordinate]:
    """Return lat/lon coordinates following the OSM edge geometry if present."""
    data_bundle = G.get_edge_data(u, v)
    if not data_bundle:
        return [(float(G.nodes[u]["y"]), float(G.nodes[u]["x"])), (float(G.nodes[v]["y"]), float(G.nodes[v]["x"]))]

    # MultiDiGraph edge data is keyed by integer. Choose the shortest edge.
    if isinstance(data_bundle, dict) and all(isinstance(k, (int, str)) for k in data_bundle.keys()):
        edge_datas = list(data_bundle.values())
    else:
        edge_datas = [data_bundle]
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
        data_bundle = G.get_edge_data(u, v)
        if not data_bundle:
            continue
        edge_datas = list(data_bundle.values()) if isinstance(data_bundle, dict) else [data_bundle]
        total += min(float(d.get("length", 0.0)) for d in edge_datas)
    return total


def path_travel_time_s(G, route_nodes: Sequence) -> float:
    total = 0.0
    for u, v in zip(route_nodes[:-1], route_nodes[1:]):
        data_bundle = G.get_edge_data(u, v)
        if not data_bundle:
            continue
        edge_datas = list(data_bundle.values()) if isinstance(data_bundle, dict) else [data_bundle]
        total += min(float(d.get("travel_time", d.get("length", 0.0) / 8.33)) for d in edge_datas)
    return total


def dijkstra_path(G, source, target, weight: str = "travel_time"):
    return nx.dijkstra_path(G, source, target, weight=weight)


def astar_path(G, source, target, weight: str = "travel_time"):
    # Pre-fetch target coordinates once
    target_node_data = G.nodes[target]
    vy = float(target_node_data["y"])
    vx = float(target_node_data["x"])
    
    # Flat earth approximation cos factor based on average latitude
    source_node_data = G.nodes[source]
    mid_lat = math.radians((float(source_node_data["y"]) + vy) / 2.0)
    cos_factor = math.cos(mid_lat)
    
    def heuristic(u, v):
        node_data = G.nodes[u]
        uy = float(node_data["y"])
        ux = float(node_data["x"])
        
        # Flat earth approximation in meters
        d_lat = (vy - uy) * 111000.0
        d_lon = (vx - ux) * 111000.0 * cos_factor
        meters = math.hypot(d_lat, d_lon)
        
        return meters / 8.33 if weight == "travel_time" else meters

    return nx.astar_path(G, source, target, heuristic=heuristic, weight=weight)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))
