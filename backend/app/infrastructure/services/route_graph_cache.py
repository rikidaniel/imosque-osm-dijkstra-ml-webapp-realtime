from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from .osm_graph import (
    MAX_OSM_BUILD_AREA_KM2,
    OSM_CACHE_DIR,
    bbox_area_km2,
    bbox_from_points,
    build_osm_graph_for_bbox,
    edge_index_cache_path,
    evict_road_graph,
    graph_bounds,
    get_road_graph_status,
    prewarm_road_graph,
    runtime_graph_cache_path,
    start_road_graph_prewarm,
)

Coordinate = Tuple[float, float]
Bounds = Tuple[float, float, float, float]  # north, south, east, west

CORRIDOR_CACHE_DIR = Path(
    os.getenv("IMOSQUE_CORRIDOR_CACHE_DIR", str(OSM_CACHE_DIR / "route_corridors"))
)
CORRIDOR_GRID_DEGREES = max(
    0.01, float(os.getenv("IMOSQUE_CORRIDOR_GRID_DEGREES", "0.025"))
)
CORRIDOR_MIN_BUFFER_KM = max(
    1.0, float(os.getenv("IMOSQUE_CORRIDOR_MIN_BUFFER_KM", "2.5"))
)
CORRIDOR_MAX_BUFFER_KM = max(
    CORRIDOR_MIN_BUFFER_KM,
    float(os.getenv("IMOSQUE_CORRIDOR_MAX_BUFFER_KM", "6")),
)
CORRIDOR_TARGET_AREA_KM2 = min(
    MAX_OSM_BUILD_AREA_KM2,
    max(100.0, float(os.getenv("IMOSQUE_CORRIDOR_TARGET_AREA_KM2", "350"))),
)
CORRIDOR_MAX_GRAPHS = max(
    8, int(os.getenv("IMOSQUE_CORRIDOR_MAX_GRAPHS", "128"))
)
CORRIDOR_LOCK_STALE_SECONDS = max(
    300, int(os.getenv("IMOSQUE_CORRIDOR_LOCK_STALE_SECONDS", "1800"))
)
CORRIDOR_MAX_CONCURRENT_BUILDS = max(
    1, int(os.getenv("IMOSQUE_CORRIDOR_MAX_CONCURRENT_BUILDS", "1"))
)


class CorridorAreaTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class CorridorGraphSpec:
    graph_id: str
    dataset_id: str
    graphml_path: str
    metadata_path: str
    north: float
    south: float
    east: float
    west: float
    buffer_km: float
    area_km2: float
    network_type: str = "drive"


_job_lock = threading.RLock()
_jobs: Dict[str, Dict[str, Any]] = {}
_threads: Dict[str, threading.Thread] = {}


def _safe_dataset_id(dataset_id: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in str(dataset_id or "regional")
    )[:120]


def _snap_bounds(bounds: Bounds) -> Bounds:
    north, south, east, west = bounds
    grid = CORRIDOR_GRID_DEGREES
    return (
        math.ceil(north / grid) * grid,
        math.floor(south / grid) * grid,
        math.ceil(east / grid) * grid,
        math.floor(west / grid) * grid,
    )


def _bounds_cover_points(
    bounds: Bounds,
    points: Sequence[Coordinate],
    margin_km: float = 0.5,
) -> bool:
    north, south, east, west = bounds
    if not points:
        return True
    mid_lat = sum(point[0] for point in points) / len(points)
    lat_margin = margin_km / 111.0
    lon_margin = margin_km / (
        111.0 * max(math.cos(math.radians(mid_lat)), 0.2)
    )
    return all(
        south - lat_margin <= latitude <= north + lat_margin
        and west - lon_margin <= longitude <= east + lon_margin
        for latitude, longitude in points
    )


def metadata_covers_points(
    metadata: Optional[Dict[str, Any]],
    points: Sequence[Coordinate],
    margin_km: float = 0.5,
) -> bool:
    if not metadata:
        return False
    try:
        bounds = (
            float(metadata["north"]),
            float(metadata["south"]),
            float(metadata["east"]),
            float(metadata["west"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return _bounds_cover_points(bounds, points, margin_km=margin_km)


def corridor_graph_spec(
    dataset_id: str,
    points: Sequence[Coordinate],
    buffer_km: float = 8.0,
    network_type: str = "drive",
) -> CorridorGraphSpec:
    if len(points) < 2:
        raise ValueError("Graph koridor membutuhkan titik awal dan tujuan.")
    safe_points = tuple((float(lat), float(lon)) for lat, lon in points)
    requested_buffer = min(
        CORRIDOR_MAX_BUFFER_KM,
        max(CORRIDOR_MIN_BUFFER_KM, float(buffer_km)),
    )

    # Reduce only the surrounding padding when necessary. A route whose point
    # extent alone is too large needs hierarchical/inter-city routing rather
    # than one enormous interactive graph.
    candidate_buffers = [requested_buffer]
    candidate = requested_buffer
    while candidate > CORRIDOR_MIN_BUFFER_KM:
        candidate = max(CORRIDOR_MIN_BUFFER_KM, candidate * 0.75)
        if candidate not in candidate_buffers:
            candidate_buffers.append(candidate)
    selected: Optional[Tuple[Bounds, float, float]] = None
    hard_limit_fallback: Optional[Tuple[Bounds, float, float]] = None
    for candidate_buffer in candidate_buffers:
        bounds = _snap_bounds(bbox_from_points(safe_points, candidate_buffer))
        area = bbox_area_km2(*bounds)
        if area <= MAX_OSM_BUILD_AREA_KM2:
            hard_limit_fallback = (bounds, candidate_buffer, area)
        if area <= CORRIDOR_TARGET_AREA_KM2:
            selected = (bounds, candidate_buffer, area)
            break
    selected = selected or hard_limit_fallback
    if selected is None:
        minimum_bounds = _snap_bounds(
            bbox_from_points(safe_points, CORRIDOR_MIN_BUFFER_KM)
        )
        minimum_area = bbox_area_km2(*minimum_bounds)
        raise CorridorAreaTooLarge(
            "Koridor rute terlalu panjang untuk satu graph interaktif "
            f"({minimum_area:.0f} km2, batas {MAX_OSM_BUILD_AREA_KM2:.0f} km2). "
            "Gunakan routing antar-kota bertingkat atau pecah perjalanan menjadi beberapa segmen."
        )

    bounds, selected_buffer, area = selected
    north, south, east, west = bounds
    identity = json.dumps(
        {
            "dataset_id": str(dataset_id),
            "bounds": [round(value, 6) for value in bounds],
            "network_type": network_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    graph_id = f"{_safe_dataset_id(dataset_id)}-{digest}"
    graphml_path = CORRIDOR_CACHE_DIR / f"road_graph_corridor_{graph_id}.graphml"
    metadata_path = CORRIDOR_CACHE_DIR / f"road_graph_corridor_{graph_id}.json"
    return CorridorGraphSpec(
        graph_id=graph_id,
        dataset_id=str(dataset_id),
        graphml_path=str(graphml_path),
        metadata_path=str(metadata_path),
        north=north,
        south=south,
        east=east,
        west=west,
        buffer_km=round(selected_buffer, 3),
        area_km2=round(area, 2),
        network_type=network_type,
    )


def _read_metadata(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _write_metadata(path: Path, metadata: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _metadata_files() -> Sequence[Path]:
    if not CORRIDOR_CACHE_DIR.exists():
        return ()
    return tuple(CORRIDOR_CACHE_DIR.glob("road_graph_corridor_*.json"))


def find_covering_corridor_graph(
    dataset_id: str,
    points: Sequence[Coordinate],
    margin_km: float = 0.5,
) -> Optional[Path]:
    candidates = []
    for metadata_path in _metadata_files():
        metadata = _read_metadata(metadata_path)
        if not metadata or metadata.get("dataset_id") != str(dataset_id):
            continue
        graphml_path = Path(str(metadata.get("graphml_path", "")))
        if not graphml_path.exists() or not metadata_covers_points(
            metadata, points, margin_km=margin_km
        ):
            continue
        candidates.append(
            (
                float(metadata.get("area_km2", math.inf)),
                -float(metadata.get("last_used_at", metadata.get("created_at", 0))),
                graphml_path,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
    return candidates[0][2]


def _lock_path(spec: CorridorGraphSpec) -> Path:
    return Path(spec.metadata_path).with_suffix(".lock")


def _try_acquire_build_lock(spec: CorridorGraphSpec) -> bool:
    lock_path = _lock_path(spec)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            if time.time() - lock_path.stat().st_mtime > CORRIDOR_LOCK_STALE_SECONDS:
                lock_path.unlink(missing_ok=True)
        except OSError:
            return False
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "started_at": time.time()}))
    return True


def _release_build_lock(spec: CorridorGraphSpec) -> None:
    try:
        _lock_path(spec).unlink(missing_ok=True)
    except OSError:
        pass


def _capacity_lock_path(slot: int) -> Path:
    return CORRIDOR_CACHE_DIR / f".corridor-build-slot-{slot}.lock"


def _acquire_capacity_lock(graph_id: str) -> Path:
    CORRIDOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        for slot in range(CORRIDOR_MAX_CONCURRENT_BUILDS):
            lock_path = _capacity_lock_path(slot)
            if lock_path.exists():
                try:
                    if time.time() - lock_path.stat().st_mtime > CORRIDOR_LOCK_STALE_SECONDS:
                        lock_path.unlink(missing_ok=True)
                except OSError:
                    continue
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"pid": os.getpid(), "graph_id": graph_id, "started_at": time.time()}
                    )
                )
            return lock_path
        time.sleep(0.75)


def _release_capacity_lock(lock_path: Optional[Path]) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _job_payload(spec: CorridorGraphSpec, **changes: Any) -> Dict[str, Any]:
    payload = {
        "graph_id": spec.graph_id,
        "dataset_id": spec.dataset_id,
        "graphml_path": spec.graphml_path,
        "bounds": {
            "north": spec.north,
            "south": spec.south,
            "east": spec.east,
            "west": spec.west,
        },
        "buffer_km": spec.buffer_km,
        "area_km2": spec.area_km2,
        "retry_after_ms": 1500,
    }
    payload.update(changes)
    return payload


def _set_job(spec: CorridorGraphSpec, **changes: Any) -> Dict[str, Any]:
    with _job_lock:
        current = dict(_jobs.get(spec.graph_id, _job_payload(spec)))
        current.update(changes)
        _jobs[spec.graph_id] = current
        return dict(current)


def corridor_graph_status(graph_id: str) -> Dict[str, Any]:
    if not graph_id or graph_id != _safe_dataset_id(graph_id) or len(graph_id) > 140:
        return {"graph_id": graph_id, "status": "not_found", "ready": False}
    metadata_path = CORRIDOR_CACHE_DIR / f"road_graph_corridor_{graph_id}.json"
    metadata = _read_metadata(metadata_path)
    if metadata and Path(str(metadata.get("graphml_path", ""))).exists():
        runtime = get_road_graph_status(Path(str(metadata["graphml_path"])))
        return {
            **metadata,
            "status": runtime.get("status"),
            "ready": bool(runtime.get("ready")),
            "artifact_ready": True,
            "runtime": runtime,
        }
    with _job_lock:
        current = dict(_jobs.get(graph_id, {}))
    if current:
        return current
    lock_path = CORRIDOR_CACHE_DIR / f"road_graph_corridor_{graph_id}.lock"
    if lock_path.exists():
        return {"graph_id": graph_id, "status": "building", "ready": False}
    return {"graph_id": graph_id, "status": "not_found", "ready": False}


def corridor_cache_summary(limit: int = 50) -> Dict[str, Any]:
    """Return a lightweight operational view without loading graph artifacts."""
    safe_limit = min(max(int(limit), 1), 200)
    items = []
    total_size_bytes = 0

    for metadata_path in _metadata_files():
        metadata = _read_metadata(metadata_path)
        if not metadata:
            continue
        graphml_path = Path(str(metadata.get("graphml_path", "")))
        artifact_ready = graphml_path.exists()
        size_bytes = 0
        if artifact_ready:
            for artifact in _cache_artifacts(graphml_path):
                try:
                    size_bytes += artifact.stat().st_size
                except OSError:
                    pass
        total_size_bytes += size_bytes
        items.append(
            {
                "graph_id": metadata.get("graph_id") or metadata_path.stem.removeprefix("road_graph_corridor_"),
                "dataset_id": metadata.get("dataset_id"),
                "status": "ready" if artifact_ready else "missing",
                "artifact_ready": artifact_ready,
                "area_km2": metadata.get("area_km2"),
                "buffer_km": metadata.get("buffer_km"),
                "network_type": metadata.get("network_type", "drive"),
                "nodes": metadata.get("nodes"),
                "edges": metadata.get("edges"),
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "created_at": metadata.get("created_at"),
                "last_used_at": metadata.get("last_used_at"),
            }
        )

    with _job_lock:
        jobs = [dict(value) for value in _jobs.values()]
    known_ids = {str(item.get("graph_id")) for item in items}
    for job in jobs:
        graph_id = str(job.get("graph_id", ""))
        if not graph_id or graph_id in known_ids:
            continue
        items.append(
            {
                "graph_id": graph_id,
                "dataset_id": job.get("dataset_id"),
                "status": job.get("status", "queued"),
                "artifact_ready": False,
                "area_km2": job.get("area_km2"),
                "buffer_km": job.get("buffer_km"),
                "network_type": job.get("network_type", "drive"),
                "nodes": None,
                "edges": None,
                "size_mb": 0.0,
                "created_at": job.get("created_at"),
                "last_used_at": None,
                "error": job.get("error"),
            }
        )

    items.sort(
        key=lambda item: float(item.get("last_used_at") or item.get("created_at") or 0),
        reverse=True,
    )
    ready_count = sum(1 for item in items if item.get("artifact_ready"))
    building_count = sum(
        1 for item in items if item.get("status") in {"queued", "starting", "building", "prewarming"}
    )
    failed_count = sum(1 for item in items if item.get("status") in {"error", "failed", "missing"})
    return {
        "status": "ok",
        "total": len(items),
        "ready": ready_count,
        "building": building_count,
        "failed": failed_count,
        "total_size_mb": round(total_size_bytes / (1024 * 1024), 2),
        "max_graphs": CORRIDOR_MAX_GRAPHS,
        "max_concurrent_builds": CORRIDOR_MAX_CONCURRENT_BUILDS,
        "items": items[:safe_limit],
    }


def _cache_artifacts(graphml_path: Path) -> Sequence[Path]:
    return (
        graphml_path,
        runtime_graph_cache_path(graphml_path),
        edge_index_cache_path(graphml_path),
    )


def _prune_corridor_cache(protected_graph_id: str) -> None:
    metadata_rows = []
    for path in _metadata_files():
        metadata = _read_metadata(path)
        if not metadata:
            continue
        metadata_rows.append(
            (
                float(metadata.get("created_at", 0)),
                str(metadata.get("graph_id", "")),
                path,
                Path(str(metadata.get("graphml_path", ""))),
            )
        )
    if len(metadata_rows) <= CORRIDOR_MAX_GRAPHS:
        return
    metadata_rows.sort(key=lambda item: item[0])
    remove_count = len(metadata_rows) - CORRIDOR_MAX_GRAPHS
    for _, graph_id, metadata_path, graphml_path in metadata_rows:
        if remove_count <= 0:
            break
        if graph_id == protected_graph_id or _lock_path(
            CorridorGraphSpec(
                graph_id=graph_id,
                dataset_id="",
                graphml_path=str(graphml_path),
                metadata_path=str(metadata_path),
                north=0,
                south=0,
                east=0,
                west=0,
                buffer_km=0,
                area_km2=0,
            )
        ).exists():
            continue
        evict_road_graph(graphml_path)
        for artifact in _cache_artifacts(graphml_path):
            try:
                artifact.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            metadata_path.unlink(missing_ok=True)
        except OSError:
            pass
        remove_count -= 1


def register_corridor_graph(spec: CorridorGraphSpec, graph: Any) -> Dict[str, Any]:
    graphml_path = Path(spec.graphml_path)
    metadata_path = Path(spec.metadata_path)
    south, north, west, east = graph_bounds(graph)
    metadata = {
        **asdict(spec),
        "graphml_path": str(graphml_path),
        "north": north,
        "south": south,
        "east": east,
        "west": west,
        "area_km2": round(bbox_area_km2(north, south, east, west), 2),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "created_at": time.time(),
        "build_scope": "route_corridor",
    }
    _write_metadata(metadata_path, metadata)
    _prune_corridor_cache(spec.graph_id)
    return metadata


def start_corridor_graph_build(
    dataset_id: str,
    points: Sequence[Coordinate],
    buffer_km: float = 8.0,
    network_type: str = "drive",
) -> Dict[str, Any]:
    covering = find_covering_corridor_graph(dataset_id, points)
    if covering is not None:
        metadata = _read_metadata(covering.with_suffix(".json")) or {}
        graph_id = str(metadata.get("graph_id", covering.stem))
        prewarm_started = start_road_graph_prewarm(covering)
        runtime = get_road_graph_status(covering)
        return {
            **metadata,
            "graph_id": graph_id,
            "graphml_path": str(covering),
            "status": "loading" if prewarm_started else runtime.get("status"),
            "ready": bool(runtime.get("ready")),
            "prewarm_started": prewarm_started,
            "retry_after_ms": 0 if runtime.get("ready") else 1000,
        }

    spec = corridor_graph_spec(dataset_id, points, buffer_km, network_type)
    graphml_path = Path(spec.graphml_path)
    metadata_path = Path(spec.metadata_path)
    if graphml_path.exists() and metadata_path.exists():
        metadata = _read_metadata(metadata_path) or asdict(spec)
        prewarm_started = start_road_graph_prewarm(graphml_path)
        runtime = get_road_graph_status(graphml_path)
        return {
            **metadata,
            "status": "loading" if prewarm_started else runtime.get("status"),
            "ready": bool(runtime.get("ready")),
            "prewarm_started": prewarm_started,
            "retry_after_ms": 0 if runtime.get("ready") else 1000,
        }

    with _job_lock:
        existing_thread = _threads.get(spec.graph_id)
        if existing_thread is not None and existing_thread.is_alive():
            return dict(_jobs[spec.graph_id])
        existing_job = _jobs.get(spec.graph_id)
        if (
            existing_job
            and existing_job.get("status") == "error"
            and time.time() - float(existing_job.get("finished_at", 0)) < 60.0
        ):
            return dict(existing_job)
        if not _try_acquire_build_lock(spec):
            return _set_job(spec, status="building", ready=False)
        _set_job(
            spec,
            status="queued",
            ready=False,
            started_at=time.time(),
            error=None,
        )

        def run() -> None:
            capacity_lock = None
            try:
                _set_job(spec, status="queued", ready=False)
                capacity_lock = _acquire_capacity_lock(spec.graph_id)
                _set_job(spec, status="building", ready=False)
                graph = build_osm_graph_for_bbox(
                    north=spec.north,
                    south=spec.south,
                    east=spec.east,
                    west=spec.west,
                    network_type=spec.network_type,
                    output_graphml=graphml_path,
                )
                prewarm_road_graph(graphml_path)
                metadata = register_corridor_graph(spec, graph)
                _set_job(
                    spec,
                    **metadata,
                    status="ready",
                    ready=True,
                    finished_at=time.time(),
                    error=None,
                    retry_after_ms=0,
                )
            except Exception as exc:
                _set_job(
                    spec,
                    status="error",
                    ready=False,
                    finished_at=time.time(),
                    error=str(exc)[:500],
                )
            finally:
                _release_capacity_lock(capacity_lock)
                _release_build_lock(spec)
                with _job_lock:
                    if _threads.get(spec.graph_id) is threading.current_thread():
                        _threads.pop(spec.graph_id, None)

        thread = threading.Thread(
            target=run,
            name=f"imosque-corridor-build-{spec.graph_id}",
            daemon=True,
        )
        _threads[spec.graph_id] = thread
        thread.start()
        return dict(_jobs[spec.graph_id])
