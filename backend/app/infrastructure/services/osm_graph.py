from __future__ import annotations

import math
import os
import threading
import gc
import heapq
import pickle
import tempfile
import time
import tracemalloc
from collections import OrderedDict
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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


@dataclass(frozen=True)
class RoadEdgeSnap:
    """Projection of a coordinate onto one physical road edge.

    ``fraction`` follows the stored ``u -> v`` edge geometry: 0 is node ``u``
    and 1 is node ``v``. Routing can therefore charge only the traversed part
    of an edge without copying the full graph or inserting temporary nodes.
    """

    u: Any
    v: Any
    fraction: float
    coordinate: Coordinate
    connector_m: float

_MAX_LOADED_GRAPHS = max(1, int(os.getenv("IMOSQUE_MAX_LOADED_GRAPHS", "1")))
_MAX_EDGE_SNAP_CACHE_ENTRIES = max(
    0, int(os.getenv("IMOSQUE_EDGE_SNAP_CACHE_SIZE", "20000"))
)
_loaded_graphs_cache: "OrderedDict[str, nx.Graph]" = OrderedDict()
_graph_cache_lock = threading.RLock()
_graph_runtime_state_lock = threading.RLock()
_nearest_index_lock = threading.RLock()
_nearest_edge_index_lock = threading.RLock()
_nearest_edge_snap_cache_lock = threading.RLock()
_graph_build_locks_lock = threading.Lock()
_graph_build_locks: Dict[str, threading.Lock] = {}
_graph_prewarm_lock = threading.Lock()
_graph_prewarm_threads: Dict[str, threading.Thread] = {}
_edge_index_persist_lock = threading.Lock()
_edge_index_persist_threads: Dict[str, threading.Thread] = {}
_graph_runtime_states: Dict[str, Dict[str, Any]] = {}
_pathfinding_benchmark_lock = threading.Lock()
_RUNTIME_CACHE_VERSION = 1
_EDGE_INDEX_CACHE_VERSION = 1


def runtime_graph_cache_path(graphml_path: Path = DEFAULT_GRAPHML) -> Path:
    """Return the internal binary cache paired with a GraphML source file."""
    graphml_path = Path(graphml_path)
    return graphml_path.with_suffix(graphml_path.suffix + ".runtime.pkl")


def edge_index_cache_path(graphml_path: Path = DEFAULT_GRAPHML) -> Path:
    """Return the persistent Shapely edge-index cache paired with GraphML."""
    graphml_path = Path(graphml_path)
    return graphml_path.with_suffix(graphml_path.suffix + ".edges.pkl")


def _graph_source_fingerprint(graphml_path: Path) -> Tuple[int, int]:
    stat = Path(graphml_path).stat()
    return stat.st_size, stat.st_mtime_ns


def _load_edge_index_cache(graphml_path: Path):
    """Load a trusted local edge index only when its GraphML source still matches."""
    cache_path = edge_index_cache_path(graphml_path)
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, dict) or payload.get("version") != _EDGE_INDEX_CACHE_VERSION:
            return None
        if tuple(payload.get("source_fingerprint", ())) != _graph_source_fingerprint(graphml_path):
            return None
        index = payload.get("index")
        if not isinstance(index, tuple) or len(index) != 3:
            return None
        tree, geometries, records = index
        if tree is None or not hasattr(tree, "query") or len(geometries) != len(records) or not records:
            return None
        return index
    except Exception:
        # Rebuilding is always safe when a write was interrupted or Shapely changed.
        return None


def _write_edge_index_cache(graphml_path: Path, index) -> Path:
    """Persist the immutable edge STRtree atomically."""
    graphml_path = Path(graphml_path)
    cache_path = edge_index_cache_path(graphml_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=cache_path.parent,
            prefix=f".{cache_path.name}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            pickle.dump(
                {
                    "version": _EDGE_INDEX_CACHE_VERSION,
                    "source_fingerprint": _graph_source_fingerprint(graphml_path),
                    "index": index,
                },
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, cache_path)
        temp_path = None
        return cache_path
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _persist_edge_index_in_background(graphml_path: Path, index) -> None:
    """Write a newly built edge index without extending the interactive request."""
    graphml_path = Path(graphml_path)
    path_str = str(graphml_path.resolve())
    with _edge_index_persist_lock:
        existing = _edge_index_persist_threads.get(path_str)
        if existing is not None and existing.is_alive():
            return

        def run() -> None:
            try:
                _write_edge_index_cache(graphml_path, index)
            except Exception as exc:
                print(f"Persistent edge index skipped: {exc}")
            finally:
                with _edge_index_persist_lock:
                    if _edge_index_persist_threads.get(path_str) is threading.current_thread():
                        _edge_index_persist_threads.pop(path_str, None)

        thread = threading.Thread(
            target=run,
            name=f"imosque-edge-index-persist-{graphml_path.stem}",
            daemon=True,
        )
        _edge_index_persist_threads[path_str] = thread
        thread.start()


def _load_runtime_graph_cache(graphml_path: Path):
    """Load a trusted local runtime cache only when its GraphML fingerprint matches."""
    cache_path = runtime_graph_cache_path(graphml_path)
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, dict) or payload.get("version") != _RUNTIME_CACHE_VERSION:
            return None
        if tuple(payload.get("source_fingerprint", ())) != _graph_source_fingerprint(graphml_path):
            return None
        graph = payload.get("graph")
        if graph is None or not hasattr(graph, "nodes") or not hasattr(graph, "edges"):
            return None
        return graph
    except Exception:
        # GraphML remains the source of truth when a partial/old pickle exists.
        return None


def _write_runtime_graph_cache(graphml_path: Path, graph) -> Path:
    """Persist the compact graph atomically so a crash cannot expose a partial cache."""
    graphml_path = Path(graphml_path)
    cache_path = runtime_graph_cache_path(graphml_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=cache_path.parent,
            prefix=f".{cache_path.name}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            pickle.dump(
                {
                    "version": _RUNTIME_CACHE_VERSION,
                    "source_fingerprint": _graph_source_fingerprint(graphml_path),
                    "graph": graph,
                },
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, cache_path)
        temp_path = None
        return cache_path
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _set_graph_runtime_state(graphml_path: Path, **changes: Any) -> None:
    path_str = str(Path(graphml_path).resolve())
    with _graph_runtime_state_lock:
        state = _graph_runtime_states.setdefault(path_str, {})
        state.update(changes)


def get_road_graph_status(graphml_path: Path = DEFAULT_GRAPHML) -> Dict[str, Any]:
    """Expose whether a graph merely exists or is loaded and ready for requests."""
    graphml_path = Path(graphml_path)
    path_str = str(graphml_path.resolve())
    exists = graphml_path.exists()
    with _graph_runtime_state_lock:
        state = dict(_graph_runtime_states.get(path_str, {}))
    loaded = bool(state.get("ready", False))
    if not exists:
        status = "not_configured"
    elif loaded:
        status = "ready"
    else:
        status = state.get("status", "available")
        if status == "ready":
            status = "available"
    result = {
        "status": status,
        "ready": bool(exists and loaded),
        "cache_exists": exists,
        "graphml_path": str(graphml_path),
        "runtime_cache_exists": runtime_graph_cache_path(graphml_path).exists(),
        "edge_index_cache_exists": edge_index_cache_path(graphml_path).exists(),
    }
    result.update({key: value for key, value in state.items() if key not in {"status", "ready"}})
    return result


def _cache_loaded_graph(path_str: str, graph):
    """Keep graph memory bounded: one DKI GraphML can occupy several GB in RAM."""
    with _graph_cache_lock:
        _loaded_graphs_cache[path_str] = graph
        _loaded_graphs_cache.move_to_end(path_str)
        while len(_loaded_graphs_cache) > _MAX_LOADED_GRAPHS:
            evicted_path, _ = _loaded_graphs_cache.popitem(last=False)
            with _graph_runtime_state_lock:
                evicted_state = _graph_runtime_states.setdefault(evicted_path, {})
                evicted_state.update(status="available", ready=False)
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
        toll_value = data.get("toll", "no")
        if isinstance(toll_value, (list, tuple, set)):
            toll_value = next(iter(toll_value), "no")
        attrs = {
            "length": length,
            "travel_time": travel_time,
            # Retain the OSM toll flag so cost-efficient routing can estimate
            # monetary toll cost. Older cached graphs simply behave as no-toll.
            "toll": str(toll_value),
        }
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


def _build_osm_graph_for_bbox_once(
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

    output_graphml = Path(output_graphml)
    output_graphml.parent.mkdir(parents=True, exist_ok=True)
    temporary_graphml: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_graphml.parent,
            prefix=f".{output_graphml.stem}-",
            suffix=output_graphml.suffix,
            delete=False,
        ) as handle:
            temporary_graphml = Path(handle.name)
        ox.save_graphml(G, filepath=temporary_graphml)
        os.replace(temporary_graphml, output_graphml)
        temporary_graphml = None
    finally:
        if temporary_graphml is not None:
            try:
                temporary_graphml.unlink(missing_ok=True)
            except OSError:
                pass
    G = _compact_graph_for_routing(G)
    try:
        _write_runtime_graph_cache(output_graphml, G)
    except Exception as exc:
        # A binary cache is an optimization; a valid GraphML build must still succeed.
        print(f"Runtime graph cache skipped: {exc}")
    gc.collect()
    path_str = str(Path(output_graphml).resolve())
    G._imosque_graphml_path = Path(output_graphml).resolve()
    _cache_loaded_graph(path_str, G)
    
    # Print optimization stats
    print(f"Graph optimized: {len(G.nodes)} nodes, {len(G.edges)} edges")
    
    return G


def build_osm_graph_for_bbox(
    north: float,
    south: float,
    east: float,
    west: float,
    network_type: str = "drive",
    output_graphml: Path = DEFAULT_GRAPHML,
):
    """Singleflight wrapper: only one writer may replace a dataset graph at a time."""
    path_str = str(Path(output_graphml).resolve())
    with _graph_build_locks_lock:
        build_lock = _graph_build_locks.setdefault(path_str, threading.Lock())
    with build_lock:
        _set_graph_runtime_state(output_graphml, status="loading", ready=False, error=None)
        try:
            graph = _build_osm_graph_for_bbox_once(
                north=north,
                south=south,
                east=east,
                west=west,
                network_type=network_type,
                output_graphml=output_graphml,
            )
            _set_graph_runtime_state(
                output_graphml,
                status="ready",
                ready=True,
                source="fresh_build",
                nodes=len(graph.nodes),
                edges=len(graph.edges),
                error=None,
            )
            return graph
        except Exception as exc:
            _set_graph_runtime_state(output_graphml, status="error", ready=False, error=str(exc)[:300])
            raise


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
        _set_graph_runtime_state(graphml_path, status="loading", ready=False, error=None)
        load_started = time.perf_counter()
        try:
            G = _load_runtime_graph_cache(graphml_path)
            source = "runtime_binary"
            if G is None:
                ox = _require_osmnx()
                G = ox.load_graphml(filepath=graphml_path)
                G = _compact_graph_for_routing(G)
                source = "graphml"
                try:
                    _write_runtime_graph_cache(graphml_path, G)
                except Exception as exc:
                    print(f"Runtime graph cache skipped: {exc}")
            gc.collect()
            G._imosque_graphml_path = Path(graphml_path).resolve()
            G = _cache_loaded_graph(path_str, G)
            _set_graph_runtime_state(
                graphml_path,
                status="ready",
                ready=True,
                source=source,
                load_time_ms=round((time.perf_counter() - load_started) * 1000, 2),
                nodes=len(G.nodes),
                edges=len(G.edges),
                error=None,
            )
            return G
        except Exception as exc:
            _set_graph_runtime_state(graphml_path, status="error", ready=False, error=str(exc)[:300])
            raise


def evict_road_graph(graphml_path: Path = DEFAULT_GRAPHML) -> None:
    """Release a graph built by an offline batch job from process memory."""
    with _graph_cache_lock:
        path_str = str(Path(graphml_path).resolve())
        _loaded_graphs_cache.pop(path_str, None)
        with _graph_runtime_state_lock:
            state = _graph_runtime_states.setdefault(path_str, {})
            state.update(status="available" if Path(graphml_path).exists() else "not_configured", ready=False)


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


def nearest_road_node_candidates_batch(G, points: List[Coordinate], k: int = 3):
    """Return several nearby road-node candidates for each coordinate.

    A single nearest node is ambiguous around divided roads, bridges, rivers, and
    parallel carriageways. Routing can evaluate these candidates and retain the
    reachable one with the lowest total access plus road cost.
    """
    if not points:
        return []
    mode, tree, node_ids = _nearest_node_index(G)
    candidate_count = min(max(1, int(k)), len(node_ids))
    if mode == "projected":
        queries = np.asarray([(lon, lat) for lat, lon in points], dtype=float)
    else:
        queries = np.deg2rad(np.asarray(points, dtype=float))
    _, indices = tree.query(queries, k=candidate_count)
    indices = np.asarray(indices)
    if indices.ndim == 1:
        indices = indices.reshape(len(points), candidate_count)
    return [
        [node_ids[int(index)] for index in row]
        for row in indices
    ]


def _nearest_edge_index(G):
    """Build one reusable STRtree over physical road-edge geometries.

    OSMnx simplifies roads into long edges. Indexing only their endpoint nodes
    can make a road passing directly beside a coordinate appear hundreds of
    metres away, especially near rivers, railways, and divided carriageways.
    Reverse directed edges share one physical geometry record here; direction
    is evaluated later against the original graph.
    """
    cached = getattr(G, "_imosque_nearest_edge_index", None)
    if cached is not None:
        return cached

    with _nearest_edge_index_lock:
        cached = getattr(G, "_imosque_nearest_edge_index", None)
        if cached is not None:
            return cached

        graphml_path = getattr(G, "_imosque_graphml_path", None)
        if graphml_path is not None:
            load_started = time.perf_counter()
            cached = _load_edge_index_cache(Path(graphml_path))
            if cached is not None:
                G._imosque_nearest_edge_index = cached
                _set_graph_runtime_state(
                    Path(graphml_path),
                    edge_index_source="persistent_binary",
                    edge_index_load_time_ms=round((time.perf_counter() - load_started) * 1000, 2),
                )
                return cached

        from shapely.geometry import LineString
        from shapely.strtree import STRtree

        build_started = time.perf_counter()
        geometries = []
        records: List[Tuple[Any, Any]] = []
        seen_physical_edges = set()
        for u, v in G.edges():
            if u == v:
                continue
            physical_key = frozenset((u, v))
            if physical_key in seen_physical_edges:
                continue
            seen_physical_edges.add(physical_key)
            coordinates = _edge_linestring(G, u, v)
            if len(coordinates) < 2:
                continue
            try:
                line = LineString([(lon, lat) for lat, lon in coordinates])
            except Exception:
                continue
            if line.is_empty or line.length <= 0:
                continue
            geometries.append(line)
            records.append((u, v))

        if not geometries:
            raise ValueError("Graph OSM tidak memiliki geometri edge yang dapat diindeks.")
        cached = (STRtree(geometries), geometries, records)
        G._imosque_nearest_edge_index = cached
        if graphml_path is not None:
            _set_graph_runtime_state(
                Path(graphml_path),
                edge_index_source="fresh_build",
                edge_index_build_time_ms=round((time.perf_counter() - build_started) * 1000, 2),
            )
            _persist_edge_index_in_background(Path(graphml_path), cached)
        return cached


def nearest_road_edge_candidates_batch(
    G,
    points: List[Coordinate],
    k: int = 4,
    max_search_radius_m: float = 750.0,
) -> List[List[RoadEdgeSnap]]:
    """Project coordinates onto nearby road edges, ordered by access distance."""
    if not points:
        return []

    from shapely.geometry import Point, box

    tree, geometries, records = _nearest_edge_index(G)
    candidate_count = max(1, int(k))
    with _nearest_edge_snap_cache_lock:
        snap_cache = getattr(G, "_imosque_edge_snap_cache", None)
        if snap_cache is None:
            snap_cache = OrderedDict()
            G._imosque_edge_snap_cache = snap_cache
    results: List[List[RoadEdgeSnap]] = []
    for lat, lon in points:
        cache_key = (
            float(lat),
            float(lon),
            candidate_count,
            float(max_search_radius_m),
        )
        if _MAX_EDGE_SNAP_CACHE_ENTRIES:
            with _nearest_edge_snap_cache_lock:
                cached_snaps = snap_cache.get(cache_key)
                if cached_snaps is not None:
                    snap_cache.move_to_end(cache_key)
                    results.append(list(cached_snaps))
                    continue

        point = Point(float(lon), float(lat))
        nearby_indices = set()
        radius_m = 50.0
        while radius_m <= max_search_radius_m and len(nearby_indices) < candidate_count * 4:
            latitude_delta = radius_m / 111_320.0
            longitude_delta = radius_m / (
                111_320.0 * max(math.cos(math.radians(float(lat))), 0.05)
            )
            query_box = box(
                float(lon) - longitude_delta,
                float(lat) - latitude_delta,
                float(lon) + longitude_delta,
                float(lat) + latitude_delta,
            )
            nearby_indices.update(int(index) for index in tree.query(query_box))
            radius_m *= 2.0

        if not nearby_indices:
            nearest_index = tree.query_nearest(point)
            nearest_values = np.asarray(nearest_index).reshape(-1)
            nearby_indices.update(int(index) for index in nearest_values)

        snaps: List[RoadEdgeSnap] = []
        for index in nearby_indices:
            line = geometries[index]
            try:
                fraction = float(line.project(point, normalized=True))
                projected = line.interpolate(fraction, normalized=True)
                snapped_coordinate = (float(projected.y), float(projected.x))
                connector_m = 1000.0 * haversine_km(
                    float(lat), float(lon), snapped_coordinate[0], snapped_coordinate[1]
                )
            except Exception:
                continue
            u, v = records[index]
            snaps.append(
                RoadEdgeSnap(
                    u=u,
                    v=v,
                    fraction=max(0.0, min(1.0, fraction)),
                    coordinate=snapped_coordinate,
                    connector_m=connector_m,
                )
            )

        snaps.sort(key=lambda snap: (snap.connector_m, str(snap.u), str(snap.v)))
        if not snaps:
            raise ValueError("Tidak ada edge jalan terdekat untuk koordinat yang diminta.")
        # Alternatives are useful for adjacent carriageways, but a much farther
        # edge can create a fake shortcut through buildings or across water.
        nearest_distance = snaps[0].connector_m
        maximum_alternative_distance = max(
            nearest_distance + 12.0,
            nearest_distance * 1.75,
        )
        eligible = [
            snap for snap in snaps
            if snap.connector_m <= maximum_alternative_distance
        ]
        selected = eligible[:candidate_count] or snaps[:1]
        if _MAX_EDGE_SNAP_CACHE_ENTRIES:
            with _nearest_edge_snap_cache_lock:
                snap_cache[cache_key] = tuple(selected)
                snap_cache.move_to_end(cache_key)
                while len(snap_cache) > _MAX_EDGE_SNAP_CACHE_ENTRIES:
                    snap_cache.popitem(last=False)
        results.append(list(selected))
    return results


def warm_road_graph_indexes(G) -> None:
    """Precompute route-critical immutable lookup structures."""
    graph_bounds(G)
    _nearest_edge_index(G)
    _heuristic_distance_per_weight_unit(G, "travel_time")


def prewarm_road_graph(graphml_path: Path = DEFAULT_GRAPHML) -> Dict[str, Any]:
    """Synchronously load a graph and its spatial index, recording readiness timings."""
    started = time.perf_counter()
    _set_graph_runtime_state(graphml_path, status="loading", ready=False, error=None)
    try:
        graph = load_road_graph(graphml_path)
        # load_road_graph marks the graph object ready for ordinary callers;
        # prewarm keeps interactive routing in fallback mode until the edge
        # index is also usable.
        _set_graph_runtime_state(graphml_path, status="loading", ready=False, error=None)
        index_started = time.perf_counter()
        warm_road_graph_indexes(graph)
        _set_graph_runtime_state(
            graphml_path,
            status="ready",
            ready=True,
            index_time_ms=round((time.perf_counter() - index_started) * 1000, 2),
            prewarm_time_ms=round((time.perf_counter() - started) * 1000, 2),
            error=None,
        )
    except Exception as exc:
        _set_graph_runtime_state(graphml_path, status="error", ready=False, error=str(exc)[:300])
        raise
    return get_road_graph_status(graphml_path)


def start_road_graph_prewarm(graphml_path: Path = DEFAULT_GRAPHML) -> bool:
    """Start one background prewarm per graph path; return False if none is needed."""
    graphml_path = Path(graphml_path)
    path_str = str(graphml_path.resolve())
    if not graphml_path.exists():
        _set_graph_runtime_state(graphml_path, status="not_configured", ready=False, error=None)
        return False
    if get_road_graph_status(graphml_path)["ready"]:
        return False
    with _graph_prewarm_lock:
        existing = _graph_prewarm_threads.get(path_str)
        if existing is not None and existing.is_alive():
            return False
        _set_graph_runtime_state(graphml_path, status="loading", ready=False, error=None)

        def run() -> None:
            try:
                prewarm_road_graph(graphml_path)
            except Exception as exc:
                print(f"Road graph preload skipped: {exc}")
            finally:
                with _graph_prewarm_lock:
                    if _graph_prewarm_threads.get(path_str) is threading.current_thread():
                        _graph_prewarm_threads.pop(path_str, None)

        thread = threading.Thread(
            target=run,
            name=f"imosque-road-graph-preload-{graphml_path.stem}",
            daemon=True,
        )
        _graph_prewarm_threads[path_str] = thread
        thread.start()
        return True


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
            coordinates = [(float(lat), float(lon)) for lon, lat in geom.coords]
            if len(coordinates) >= 2:
                u_coordinate = (float(G.nodes[u]["y"]), float(G.nodes[u]["x"]))
                v_coordinate = (float(G.nodes[v]["y"]), float(G.nodes[v]["x"]))

                def endpoint_error(points: List[Coordinate]) -> float:
                    return (
                        (points[0][0] - u_coordinate[0]) ** 2
                        + (points[0][1] - u_coordinate[1]) ** 2
                        + (points[-1][0] - v_coordinate[0]) ** 2
                        + (points[-1][1] - v_coordinate[1]) ** 2
                    )

                reversed_coordinates = list(reversed(coordinates))
                if endpoint_error(reversed_coordinates) < endpoint_error(coordinates):
                    coordinates = reversed_coordinates
            return coordinates
        except Exception:
            pass
    return [(float(G.nodes[u]["y"]), float(G.nodes[u]["x"])), (float(G.nodes[v]["y"]), float(G.nodes[v]["x"]))]


def edge_snap_segment_coordinates(
    G,
    snap: RoadEdgeSnap,
    start_fraction: float,
    end_fraction: float,
) -> List[Coordinate]:
    """Return the directed subsection of a snapped edge geometry."""
    from shapely.geometry import LineString, Point
    from shapely.ops import substring

    coordinates = _edge_linestring(G, snap.u, snap.v)
    if len(coordinates) < 2:
        return [snap.coordinate]
    line = LineString([(lon, lat) for lat, lon in coordinates])
    start = max(0.0, min(1.0, float(start_fraction)))
    end = max(0.0, min(1.0, float(end_fraction)))
    segment = substring(line, start, end, normalized=True)
    if isinstance(segment, Point):
        return [(float(segment.y), float(segment.x))]
    return [(float(lat), float(lon)) for lon, lat in segment.coords]


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


def path_toll_distance_m(G, route_nodes: Sequence) -> float:
    """Return road distance explicitly marked as tolled in OpenStreetMap."""
    total = 0.0
    truthy = {"yes", "true", "1", "designated"}
    for u, v in zip(route_nodes, route_nodes[1:]):
        edge_data = G.get_edge_data(u, v)
        if not edge_data:
            continue
        candidates = list(edge_data.values()) if G.is_multigraph() else [edge_data]
        selected = min(
            candidates,
            key=lambda data: float(data.get("travel_time", data.get("length", 0.0) / 8.33)),
        )
        toll_value = selected.get("toll", "no")
        if isinstance(toll_value, (list, tuple, set)):
            is_toll = any(str(value).strip().lower() in truthy for value in toll_value)
        else:
            is_toll = str(toll_value).strip().lower() in truthy
        if is_toll:
            total += float(selected.get("length", 0.0))
    return total


def _weight_function(G, weight: str | Callable):
    """Match NetworkX's public string/callable weight semantics."""
    if callable(weight):
        return weight
    if G.is_multigraph():
        return lambda _u, _v, data: min(
            attrs.get(weight, 1) for attrs in data.values()
        )
    return lambda _u, _v, data: data.get(weight, 1)


def _node_metric_distance_m(G, u, v) -> float:
    """Metric distance for geographic graphs, with a projected-graph fallback."""
    u_data = G.nodes[u]
    v_data = G.nodes[v]
    uy, ux = float(u_data["y"]), float(u_data["x"])
    vy, vx = float(v_data["y"]), float(v_data["x"])
    if all((-90.0 <= lat <= 90.0) for lat in (uy, vy)) and all(
        -180.0 <= lon <= 180.0 for lon in (ux, vx)
    ):
        return 1000.0 * haversine_km(uy, ux, vy, vx)
    return math.hypot(vy - uy, vx - ux)


def _heuristic_distance_per_weight_unit(G, weight: str | Callable) -> float:
    """Return a graph-derived scale that makes straight-line A* consistent.

    For every directed edge ``u -> v`` this chooses a scale at least as large
    as ``metric_distance(u, v) / edge_cost``. The triangle inequality then
    guarantees ``h(u) <= cost(u, v) + h(v)``. A zero-cost edge that changes
    position forces a zero heuristic, which remains correct.
    """
    if callable(weight):
        return math.inf
    cache = getattr(G, "_imosque_heuristic_scales", None)
    if cache is None:
        cache = {}
        G._imosque_heuristic_scales = cache
    if weight in cache:
        return cache[weight]

    maximum_ratio = 0.0
    edge_iter = (
        G.edges(keys=True, data=True)
        if G.is_multigraph()
        else ((u, v, None, data) for u, v, data in G.edges(data=True))
    )
    for u, v, _key, data in edge_iter:
        try:
            cost = float(data.get(weight, 1))
        except (AttributeError, TypeError, ValueError):
            cache[weight] = math.inf
            return math.inf
        if not math.isfinite(cost) or cost < 0:
            cache[weight] = math.inf
            return math.inf
        direct_m = _node_metric_distance_m(G, u, v)
        if cost == 0:
            if direct_m > 1e-9:
                cache[weight] = math.inf
                return math.inf
            continue
        maximum_ratio = max(maximum_ratio, direct_m / cost)

    # A tiny floating-point margin protects consistency at equality.
    scale = maximum_ratio * (1.0 + 1e-12) if maximum_ratio > 0 else math.inf
    cache[weight] = scale
    return scale


def _astar_heuristic(G, target, weight: str | Callable, stats: Optional[Dict[str, Any]] = None):
    scale = _heuristic_distance_per_weight_unit(G, weight)
    if stats is not None:
        stats["heuristic_scale_m_per_weight_unit"] = None if not math.isfinite(scale) else scale
        if weight == "travel_time" and math.isfinite(scale):
            stats["heuristic_upper_speed_kph"] = scale * 3.6

    def heuristic(u, _v):
        if stats is not None:
            stats["heuristic_calls"] = stats.get("heuristic_calls", 0) + 1
        if not math.isfinite(scale) or scale <= 0:
            return 0.0
        return _node_metric_distance_m(G, u, target) / scale

    return heuristic


def _prepare_search_stats(stats: Dict[str, Any], algorithm: str) -> None:
    stats.setdefault("algorithm", algorithm)
    stats["search_calls"] = stats.get("search_calls", 0) + 1
    stats.setdefault("expanded_nodes", 0)
    stats.setdefault("expanded_states", 0)
    stats.setdefault("examined_edges", 0)
    stats.setdefault("heuristic_calls", 0)


def _bidirectional_dijkstra_with_stats(G, source, target, weight, stats: Dict[str, Any]):
    if source not in G:
        raise nx.NodeNotFound(f"Source {source} is not in G")
    if target not in G:
        raise nx.NodeNotFound(f"Target {target} is not in G")
    if source == target:
        return [source]

    weight_fn = _weight_function(G, weight)
    dists = [{}, {}]
    paths = [{source: [source]}, {target: [target]}]
    fringe = [[], []]
    seen = [{source: 0.0}, {target: 0.0}]
    serial = count()
    heapq.heappush(fringe[0], (0.0, next(serial), source))
    heapq.heappush(fringe[1], (0.0, next(serial), target))
    neighs = [G._succ, G._pred] if G.is_directed() else [G._adj, G._adj]
    final_distance = math.inf
    final_path: List[Any] = []
    direction = 1
    expanded_this_call = set()

    while fringe[0] and fringe[1]:
        direction = 1 - direction
        distance, _, node = heapq.heappop(fringe[direction])
        if node in dists[direction]:
            continue
        dists[direction][node] = distance
        stats["expanded_states"] += 1
        if node not in expanded_this_call:
            expanded_this_call.add(node)
            stats["expanded_nodes"] += 1
        if node in dists[1 - direction]:
            return final_path

        for neighbor, edge_data in neighs[direction][node].items():
            stats["examined_edges"] += 1
            cost = (
                weight_fn(node, neighbor, edge_data)
                if direction == 0
                else weight_fn(neighbor, node, edge_data)
            )
            if cost is None:
                continue
            candidate = distance + cost
            if neighbor in dists[direction]:
                if candidate < dists[direction][neighbor]:
                    raise ValueError("Contradictory paths found: negative weights?")
            elif neighbor not in seen[direction] or candidate < seen[direction][neighbor]:
                seen[direction][neighbor] = candidate
                heapq.heappush(fringe[direction], (candidate, next(serial), neighbor))
                paths[direction][neighbor] = paths[direction][node] + [neighbor]
                if neighbor in seen[0] and neighbor in seen[1]:
                    total = seen[0][neighbor] + seen[1][neighbor]
                    if not final_path or total < final_distance:
                        final_distance = total
                        reverse_path = list(reversed(paths[1][neighbor]))
                        final_path = paths[0][neighbor] + reverse_path[1:]
    raise nx.NetworkXNoPath(f"No path between {source} and {target}.")


def _astar_with_stats(G, source, target, heuristic, weight, stats: Dict[str, Any]):
    if source not in G:
        raise nx.NodeNotFound(f"Source {source} is not G")
    if target not in G:
        raise nx.NodeNotFound(f"Target {target} is not G")

    weight_fn = _weight_function(G, weight)
    serial = count()
    queue = [(0.0, next(serial), source, 0.0, None)]
    enqueued: Dict[Any, Tuple[float, float]] = {}
    explored: Dict[Any, Any] = {}
    expanded_this_call = set()

    while queue:
        _, _, node, distance, parent = heapq.heappop(queue)
        if node == target:
            path = [node]
            while parent is not None:
                path.append(parent)
                parent = explored[parent]
            return list(reversed(path))
        if node in explored:
            if explored[node] is None:
                continue
            queued_cost, _ = enqueued[node]
            if queued_cost < distance:
                continue
        explored[node] = parent
        stats["expanded_states"] += 1
        if node not in expanded_this_call:
            expanded_this_call.add(node)
            stats["expanded_nodes"] += 1

        for neighbor, edge_data in G._adj[node].items():
            stats["examined_edges"] += 1
            cost = weight_fn(node, neighbor, edge_data)
            if cost is None:
                continue
            candidate = distance + cost
            if neighbor in enqueued:
                queued_cost, estimate = enqueued[neighbor]
                if queued_cost <= candidate:
                    continue
            else:
                estimate = heuristic(neighbor, target)
            enqueued[neighbor] = candidate, estimate
            heapq.heappush(
                queue,
                (candidate + estimate, next(serial), neighbor, candidate, node),
            )
    raise nx.NetworkXNoPath(f"Node {target} not reachable from {source}")


def dijkstra_path(
    G,
    source,
    target,
    weight: str = "travel_time",
    stats: Optional[Dict[str, Any]] = None,
):
    """Optimized bidirectional Dijkstra, optionally with real search counters."""
    if stats is None:
        # Propagate NetworkXNoPath directly. Retrying the same disconnected graph
        # with single-source Dijkstra only doubles the work before fallback routing.
        return nx.bidirectional_dijkstra(G, source, target, weight=weight)[1]
    _prepare_search_stats(stats, "dijkstra")
    return _bidirectional_dijkstra_with_stats(G, source, target, weight, stats)


def astar_path(
    G,
    source,
    target,
    weight: str = "travel_time",
    stats: Optional[Dict[str, Any]] = None,
):
    """A* with a graph-derived, consistent straight-line heuristic."""
    heuristic = _astar_heuristic(G, target, weight, stats)
    if stats is None:
        return nx.astar_path(G, source, target, heuristic=heuristic, weight=weight)
    _prepare_search_stats(stats, "astar")
    return _astar_with_stats(G, source, target, heuristic, weight, stats)


def benchmark_pathfinding_algorithms(
    G,
    source,
    target,
    weight: str = "travel_time",
) -> Dict[str, Dict[str, Any]]:
    """Benchmark the production Dijkstra and A* searches on the same warm graph."""
    results: Dict[str, Dict[str, Any]] = {}
    # Deriving the graph-wide admissibility bound is a one-time graph warmup,
    # not part of an individual shortest-path search.
    _heuristic_distance_per_weight_unit(G, weight)
    with _pathfinding_benchmark_lock:
        for algorithm, pathfinder in (("dijkstra", dijkstra_path), ("astar", astar_path)):
            stats: Dict[str, Any] = {}
            gc.collect()
            started_tracing = not tracemalloc.is_tracing()
            if started_tracing:
                tracemalloc.start()
            baseline, _ = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()
            started = time.perf_counter()
            try:
                path = pathfinder(G, source, target, weight=weight, stats=stats)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                _current, peak = tracemalloc.get_traced_memory()
            finally:
                if started_tracing:
                    tracemalloc.stop()
            stats.update(
                execution_time_ms=round(elapsed_ms, 2),
                memory_usage_kb=round(max(0, peak - baseline) / 1024.0, 2),
                route_nodes_count=len(path),
                route_distance_km=round(path_length_m(G, path) / 1000.0, 3),
                route_travel_time_seconds=round(path_travel_time_s(G, path), 6),
            )
            results[algorithm] = stats
    return results


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))
