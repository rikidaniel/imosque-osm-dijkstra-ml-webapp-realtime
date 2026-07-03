from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import networkx as nx

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
OSM_CACHE_DIR = DATA_DIR / "osm_cache"
DEFAULT_GRAPHML = OSM_CACHE_DIR / "road_graph_latest.graphml"
MAX_OSM_BUILD_AREA_KM2 = 1200.0

Coordinate = Tuple[float, float]  # lat, lon


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

    # OSMnx 1.x and 2.x have different graph_from_bbox signatures.
    try:
        G = ox.graph_from_bbox(north, south, east, west, network_type=network_type, simplify=True)
    except TypeError:
        G = ox.graph_from_bbox(bbox=(west, south, east, north), network_type=network_type, simplify=True)

    # Add travel-time weights if possible. If speeds cannot be inferred, length remains available.
    try:
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)
    except Exception:
        for _, _, _, data in G.edges(keys=True, data=True):
            length = float(data.get("length", 0.0))
            data["travel_time"] = length / 8.33  # conservative fallback: 30 km/h ~= 8.33 m/s

    ox.save_graphml(G, filepath=output_graphml)
    return G


def graph_bounds(G) -> Tuple[float, float, float, float]:
    """Return graph bounds as (south, north, west, east)."""
    if len(G.nodes) == 0:
        raise ValueError("Graph OSM kosong.")
    lats = [float(data["y"]) for _, data in G.nodes(data=True)]
    lons = [float(data["x"]) for _, data in G.nodes(data=True)]
    return min(lats), max(lats), min(lons), max(lons)


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
    if not graphml_path.exists():
        raise FileNotFoundError(
            f"Cache road graph belum ada: {graphml_path}. "
            "Jalankan scripts/build_osm_graph.py atau aktifkan auto_build_osm pada request /api/route."
        )
    ox = _require_osmnx()
    return ox.load_graphml(filepath=graphml_path)


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
    def heuristic(u, v):
        uy, ux = float(G.nodes[u]["y"]), float(G.nodes[u]["x"])
        vy, vx = float(G.nodes[v]["y"]), float(G.nodes[v]["x"])
        # Haversine meter approximation, converted to seconds at 30 km/h when weight is travel_time.
        meters = haversine_km(uy, ux, vy, vx) * 1000
        return meters / 8.33 if weight == "travel_time" else meters

    return nx.astar_path(G, source, target, heuristic=heuristic, weight=weight)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))
