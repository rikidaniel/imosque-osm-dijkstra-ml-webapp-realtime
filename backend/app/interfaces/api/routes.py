from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, BackgroundTasks, Depends, Header
from fastapi.responses import JSONResponse
from typing import Any, Dict, Optional
import math
import copy
import datetime as dt
import json
import hmac
import os
import logging
import threading
import uuid
from pathlib import Path

from app.domain.models.schemas import (
    BuildAllOsmRequest, BuildOsmRequest, BuildOsmRouteRequest, NearestMosquesRequest, RouteRequest, RouteToMosqueRequest,
    MosqueCreateRequest, MosqueUpdateRequest, BulkDeleteRequest, RecommendRouteRequest, BenchmarkRequest,
    RealtimeLocationEventRequest, RoutingPrewarmRequest, UserSettingsRequest,
)
from app.use_cases.dataset_usecases import DatasetUseCases
from app.use_cases.routing_usecases import RoutingUseCases
from app.infrastructure.database.arangodb_repo import ArangoMosqueRepository, ArangoDatasetRepository
from app.infrastructure.services.routing_worker_client import RoutingWorkerError, RoutingWorkerGateway
from app.infrastructure.services.routing_osm import attach_prayer_context, build_prayer_routing_context
from app.infrastructure.services.realtime_events import (
    RealtimePublisherUnavailable,
    realtime_event_publisher,
)

router = APIRouter()
logger = logging.getLogger("imosque.admin.audit")


def _require_admin_access(x_admin_token: str = Header(default="")) -> bool:
    """Protect destructive maintenance actions when an admin token is configured."""
    configured_token = os.getenv("IMOSQUE_ADMIN_TOKEN", "").strip()
    if not configured_token:
        return False
    if not hmac.compare_digest(x_admin_token, configured_token):
        raise HTTPException(
            status_code=401,
            detail="Token superadmin tidak valid atau belum diberikan.",
            headers={"WWW-Authenticate": "AdminToken"},
        )
    return True


def _audit_admin_action(action: str, *, target: str = "", protected: bool) -> None:
    token_protected = protected is True
    logger.info(
        json.dumps(
            {
                "event": "admin_action",
                "action": action,
                "target": target,
                "token_protected": token_protected,
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
    )

mosque_repo = ArangoMosqueRepository()
dataset_repo = ArangoDatasetRepository()

dataset_usecases = DatasetUseCases(mosque_repo, dataset_repo)
routing_usecases = RoutingUseCases(mosque_repo, dataset_repo)
routing_gateway = RoutingWorkerGateway()

_osm_build_all_lock = threading.RLock()
_osm_graph_download_lock = threading.Lock()
_osm_build_state_path = Path(__file__).resolve().parents[4] / "data" / "osm_cache" / "build_all_status.json"
_osm_build_all_state: Dict[str, Any] = {
    "status": "idle",
    "cancel_requested": False,
    "total": 0,
    "completed": 0,
    "succeeded": 0,
    "failed": 0,
    "skipped": 0,
    "current_dataset_id": None,
    "items": [],
    "started_at": None,
    "finished_at": None,
    "job_id": None,
}


def _persist_build_all_state_locked() -> None:
    _osm_build_state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _osm_build_state_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(_osm_build_all_state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(_osm_build_state_path)


def _restore_build_all_state() -> None:
    if not _osm_build_state_path.exists():
        return
    try:
        loaded = json.loads(_osm_build_state_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            _osm_build_all_state.update(loaded)
        if _osm_build_all_state["status"] in {"starting", "running", "cancelling"}:
            _osm_build_all_state.update(
                status="interrupted",
                current_dataset_id=None,
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
            _persist_build_all_state_locked()
    except (OSError, ValueError, TypeError):
        _osm_build_all_state.update(status="idle", message="Status build sebelumnya tidak dapat dibaca.")


def _update_build_all_state(**changes: Any) -> None:
    with _osm_build_all_lock:
        _osm_build_all_state.update(changes)
        _persist_build_all_state_locked()


def _graph_build_active() -> bool:
    with _osm_build_all_lock:
        return _osm_build_all_state["status"] in {"starting", "running", "cancelling"} or _osm_graph_download_lock.locked()


with _osm_build_all_lock:
    _restore_build_all_state()


def _graph_cache_is_valid(dataset_id: str, graph_path: Any, metadata: Optional[Dict[str, Any]], bbox: Dict[str, float], build_scope: str) -> bool:
    """Validate identity, coverage and file integrity before a batch skip."""
    from app.infrastructure.services.osm_graph import evict_road_graph, graph_bounds, load_road_graph

    if not metadata or not graph_path.exists() or graph_path.stat().st_size < 1024:
        return False

    tolerance = 0.005
    bounds_cover = (
        float(metadata.get("south", 90)) <= float(bbox["south"]) + tolerance
        and float(metadata.get("north", -90)) >= float(bbox["north"]) - tolerance
        and float(metadata.get("west", 180)) <= float(bbox["west"]) + tolerance
        and float(metadata.get("east", -180)) >= float(bbox["east"]) - tolerance
    )
    stat = graph_path.stat()
    fingerprint_matches = (
        int(metadata.get("file_size_bytes", -1)) == stat.st_size
        and int(metadata.get("file_mtime_ns", -1)) == stat.st_mtime_ns
    )
    if fingerprint_matches and bounds_cover and int(metadata.get("nodes", 0)) > 0 and int(metadata.get("edges", 0)) > 0:
        return True

    try:
        graph = load_road_graph(graph_path)
        south, north, west, east = graph_bounds(graph)
        bounds_cover = (
            south <= float(bbox["south"]) + tolerance
            and north >= float(bbox["north"]) - tolerance
            and west <= float(bbox["west"]) + tolerance
            and east >= float(bbox["east"]) - tolerance
        )
        valid = len(graph.nodes) > 0 and len(graph.edges) > 0 and bounds_cover
        if valid:
            dataset_repo.save_osm_cache(dataset_id, {
                "graphml_path": str(graph_path),
                "south": south, "north": north, "west": west, "east": east,
                "buffer_km": metadata.get("buffer_km"),
                "network_type": metadata.get("network_type", "drive"),
                "nodes": len(graph.nodes), "edges": len(graph.edges),
                "ingest_graph": False,
                "build_scope": build_scope,
                "file_size_bytes": stat.st_size,
                "file_mtime_ns": stat.st_mtime_ns,
            })
        return valid
    except Exception:
        return False
    finally:
        evict_road_graph(graph_path)


def _ensure_dataset_graph(dataset_id: str, graph_path: Any, bbox: Dict[str, float], build_scope: str, network_type: str, force: bool) -> Optional[Dict[str, Any]]:
    """Serialize validation/build with manual graph downloads."""
    with _osm_graph_download_lock:
        metadata = dataset_repo.get_osm_cache(dataset_id)
        if not force and _graph_cache_is_valid(dataset_id, graph_path, metadata, bbox, build_scope):
            return None
        return routing_usecases.build_osm_bbox(
            bbox["north"], bbox["south"], bbox["east"], bbox["west"],
            network_type, dataset_id, build_scope,
        )


def _run_build_all_graphs(network_type: str, force: bool, job_id: str) -> None:
    from app.infrastructure.services.osm_graph import evict_road_graph, get_graphml_path

    datasets = dataset_repo.list_datasets()
    items = [
        {
            "dataset_id": d.get("_key") or d.get("dataset_id"),
            "label": d.get("dataset_label") or d.get("_key") or d.get("dataset_id"),
            "status": "queued",
            "message": "Menunggu giliran",
        }
        for d in datasets
        if (d.get("_key") or d.get("dataset_id"))
        and d.get("processed", False)
        and d.get("processing_status", "completed") == "completed"
    ]
    with _osm_build_all_lock:
        if _osm_build_all_state.get("job_id") != job_id:
            return
        if _osm_build_all_state["cancel_requested"]:
            _osm_build_all_state.update(
                status="cancelled",
                current_dataset_id=None,
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
            _persist_build_all_state_locked()
            return

    _update_build_all_state(
        status="running",
        total=len(items),
        completed=0,
        succeeded=0,
        failed=0,
        skipped=0,
        current_dataset_id=None,
        items=items,
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        finished_at=None,
    )

    for index, item in enumerate(items):
        with _osm_build_all_lock:
            if _osm_build_all_state["cancel_requested"]:
                _osm_build_all_state["status"] = "cancelled"
                _persist_build_all_state_locked()
                break
            item["status"] = "running"
            item["message"] = "Mendeteksi area prioritas dataset"
            _osm_build_all_state["current_dataset_id"] = item["dataset_id"]
            _persist_build_all_state_locked()

        dataset_id = item["dataset_id"]
        graph_path = get_graphml_path(dataset_id)
        try:
            bbox_result = dataset_usecases.get_dataset_bbox(dataset_id)
            bbox = bbox_result["bbox"]
            build_scope = "priority_area" if bbox_result.get("adjusted_to_area_limit") else "dataset_bbox"
            item["message"] = "Memvalidasi graph atau mengunduh OpenStreetMap"
            with _osm_build_all_lock:
                _persist_build_all_state_locked()
            result = _ensure_dataset_graph(dataset_id, graph_path, bbox, build_scope, network_type, force)
            if result is None:
                item.update(status="skipped", message="Graph sudah tersedia", size_mb=round(graph_path.stat().st_size / 1048576, 2))
                with _osm_build_all_lock:
                    _osm_build_all_state["skipped"] += 1
                    _persist_build_all_state_locked()
            else:
                item.update(
                    status="completed",
                    message="Graph berhasil dibangun",
                    nodes=result["nodes"],
                    edges=result["edges"],
                    coverage=build_scope,
                    ignored_outliers=bbox_result.get("ignored_outliers", 0),
                    size_mb=round(graph_path.stat().st_size / 1048576, 2),
                )
                with _osm_build_all_lock:
                    _osm_build_all_state["succeeded"] += 1
                    _persist_build_all_state_locked()
                evict_road_graph(graph_path)
        except Exception as exc:
            item.update(status="failed", message=_friendly_error(exc))
            with _osm_build_all_lock:
                _osm_build_all_state["failed"] += 1
                _persist_build_all_state_locked()
        finally:
            with _osm_build_all_lock:
                _osm_build_all_state["completed"] = index + 1
                _persist_build_all_state_locked()

    with _osm_build_all_lock:
        if _osm_build_all_state["cancel_requested"]:
            _osm_build_all_state["status"] = "cancelled"
        elif _osm_build_all_state["status"] == "running":
            _osm_build_all_state["status"] = "completed"
        _osm_build_all_state["current_dataset_id"] = None
        _osm_build_all_state["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        _persist_build_all_state_locked()

def _friendly_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "overpass" in lowered or "httpsconnectionpool" in lowered or "max retries" in lowered or "timed out" in lowered:
        return "Overpass/OSM lambat/timeout. Rute mungkin gagal. Silakan coba lagi nanti atau kecilkan radius/buffer."
    return message

@router.get("/health")
def health() -> Dict[str, Any]:
    from app.infrastructure.database.arangodb_client import check_db_health
    db_ok, db_err = check_db_health()

    if not db_ok:
        return {
            "status": "unhealthy",
            "database": {
                "connected": False,
                "empty": False,
                "error": db_err
            },
            "version": "4.0.0"
        }

    try:
        active = dataset_usecases.get_active_dataset_id()
        from app.infrastructure.services.osm_graph import get_graphml_path, get_road_graph_status
        graph_runtime = get_road_graph_status(get_graphml_path(active))

        # Cek apakah database kosong (tidak ada dataset sama sekali)
        datasets = dataset_usecases.list_datasets()
        db_empty = len(datasets) == 0

        return {
            "status": "healthy",
            "database": {
                "connected": True,
                "empty": db_empty,
                "datasets_count": len(datasets)
            },
            "graph_status": graph_runtime["status"],
            "graph_ready": graph_runtime["ready"],
            "graph_runtime": graph_runtime,
            "version": "4.0.0",
            "active_dataset_id": active,
            "routing_dispatch": routing_gateway.status(),
            "realtime_ingestion": realtime_event_publisher.status(),
            "admin_protection_configured": bool(os.getenv("IMOSQUE_ADMIN_TOKEN", "").strip()),
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "database": {
                "connected": False,
                "empty": False,
                "error": str(exc)
            },
            "version": "4.0.0"
        }

@router.get("/datasets")
def datasets() -> Dict[str, Any]:
    return {
        "active_dataset_id": dataset_usecases.get_active_dataset_id(),
        "items": dataset_usecases.list_datasets(),
    }


@router.get("/admin/access")
def admin_access(protected: bool = Depends(_require_admin_access)) -> Dict[str, Any]:
    return {
        "status": "authorized",
        "protection_configured": protected,
        "mode": "token" if protected else "local_unprotected",
    }

@router.post("/datasets/active")
def set_active_dataset(dataset_id: str = Form(...)) -> Dict[str, Any]:
    dataset_usecases.set_active_dataset_id(dataset_id)
    profile = dataset_usecases.get_dataset_profile(dataset_id)
    from app.infrastructure.services.osm_graph import get_graphml_path, get_road_graph_status, start_road_graph_prewarm
    graph_path = get_graphml_path(dataset_id)
    prewarm_started = start_road_graph_prewarm(graph_path)
    mosque_prewarm_started = routing_usecases.start_mosque_candidate_prewarm(dataset_id)
    return {
        "status": "success",
        "active_dataset_id": dataset_id,
        "profile": profile,
        "graph_prewarm_started": prewarm_started,
        "mosque_prewarm_started": mosque_prewarm_started,
        "graph_runtime": get_road_graph_status(graph_path),
    }

@router.post("/datasets/upload")
async def upload_dataset(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    dataset_name: Optional[str] = Form(None),
    make_active: bool = Form(True),
) -> Dict[str, Any]:
    if _graph_build_active():
        raise HTTPException(status_code=409, detail="Upload ditunda karena build graph sedang berjalan.")
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Hanya mendukung file CSV.")
    content = await file.read()
    try:
        result = dataset_usecases.upload_and_process_dataset(
            file_bytes=content,
            filename=file.filename,
            dataset_name=dataset_name,
            make_active=make_active,
            background_tasks=background_tasks
        )
        # BackgroundTasks preserves insertion order: this snapshot starts only
        # after the upload pipeline has committed its new data_revision.
        background_tasks.add_task(
            routing_usecases.start_mosque_candidate_prewarm,
            result["dataset_id"],
        )
        return result
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/pipeline/run")
def run_pipeline(dataset_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    did = dataset_id or dataset_usecases.get_active_dataset_id()
    try:
        result = dataset_usecases.run_pipeline(did)
        routing_usecases.start_mosque_candidate_prewarm(did)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/datasets/status/{dataset_id}")
def dataset_status(dataset_id: str) -> Dict[str, Any]:
    profile_data = dataset_usecases.get_dataset_profile(dataset_id)
    if not profile_data:
        raise HTTPException(status_code=404, detail="Dataset tidak ditemukan")
    return {
        "dataset_id": dataset_id,
        "processed": profile_data.get("processed", False),
        "processing_status": profile_data.get("processing_status", "unknown"),
        "progress_percent": profile_data.get("progress_percent", 0),
        "message": profile_data.get("message", "")
    }

@router.get("/profile")
def profile(dataset_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    did = dataset_id or dataset_usecases.get_active_dataset_id()
    profile_data = dataset_usecases.get_dataset_profile(did)
    if not profile_data:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile_data

@router.get("/mosques")
def mosques(
    dataset_id: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=30000),
    offset: int = Query(0, ge=0),
    kabko: Optional[str] = None,
) -> Dict[str, Any]:
    did = dataset_id or dataset_usecases.get_active_dataset_id()
    return dataset_usecases.get_mosques(did, limit, offset, kabko)


@router.get("/mosques/search")
def search_mosques(
    q: str = Query(..., min_length=2, max_length=120),
    dataset_id: str = Query("all", max_length=160),
    limit: int = Query(10, ge=1, le=20),
    latitude: Optional[float] = Query(None, ge=-90, le=90),
    longitude: Optional[float] = Query(None, ge=-180, le=180),
) -> Dict[str, Any]:
    try:
        return dataset_usecases.search_mosques(
            dataset_id,
            q,
            limit,
            latitude,
            longitude,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc)) from exc


def _prewarm_routing_local(req: RoutingPrewarmRequest) -> Dict[str, Any]:
    if req.dataset_id == "all":
        raise HTTPException(status_code=400, detail="Prewarm routing memerlukan dataset regional.")
    coordinates = (req.start_lat, req.start_lon, req.end_lat, req.end_lon)
    if any(value is not None for value in coordinates) and not all(
        value is not None for value in coordinates
    ):
        raise HTTPException(
            status_code=400,
            detail="Koordinat prewarm harus menyertakan start_lat, start_lon, end_lat, dan end_lon.",
        )

    corridor = None
    if all(value is not None for value in coordinates):
        from app.infrastructure.services.route_graph_cache import (
            CorridorAreaTooLarge,
            start_corridor_graph_build,
        )

        try:
            corridor = start_corridor_graph_build(
                req.dataset_id,
                (
                    (float(req.start_lat), float(req.start_lon)),
                    (float(req.end_lat), float(req.end_lon)),
                ),
                buffer_km=min(float(req.buffer_km), 10.0),
            )
        except CorridorAreaTooLarge as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    from app.infrastructure.services.osm_graph import (
        get_graphml_path,
        get_road_graph_status,
        start_road_graph_prewarm,
    )

    graph_path = get_graphml_path(req.dataset_id)
    # A coordinate-aware request warms/builds the small corridor graph. The
    # province-level graph is only preloaded for legacy callers without points.
    started = False if corridor is not None else start_road_graph_prewarm(graph_path)
    mosque_started = routing_usecases.start_mosque_candidate_prewarm(req.dataset_id)
    return {
        "status": corridor.get("status") if corridor is not None else (
            "warming" if started else get_road_graph_status(graph_path).get("status")
        ),
        "dataset_id": req.dataset_id,
        "graph_prewarm_started": started,
        "mosque_prewarm_started": mosque_started,
        "graph_runtime": get_road_graph_status(graph_path),
        "corridor": corridor,
    }


@router.post("/routing/prewarm", status_code=202)
def prewarm_routing_dataset(
    dataset_id: str = Query(..., min_length=1, max_length=160),
    start_lat: Optional[float] = Query(None, ge=-90, le=90),
    start_lon: Optional[float] = Query(None, ge=-180, le=180),
    end_lat: Optional[float] = Query(None, ge=-90, le=90),
    end_lon: Optional[float] = Query(None, ge=-180, le=180),
    buffer_km: float = Query(8.0, ge=1.0, le=50.0),
) -> Dict[str, Any]:
    request = RoutingPrewarmRequest(
        dataset_id=dataset_id,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        buffer_km=buffer_km,
    )
    try:
        return routing_gateway.dispatch(
            endpoint="internal/v1/routing/prewarm",
            dataset_id=dataset_id,
            payload=request.model_dump(),
            local_call=lambda: _prewarm_routing_local(request),
        )
    except RoutingWorkerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/routing/corridors/{graph_id}")
def routing_corridor_status(graph_id: str) -> Dict[str, Any]:
    from app.infrastructure.services.route_graph_cache import corridor_graph_status

    status = corridor_graph_status(graph_id)
    if status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Graph koridor tidak ditemukan.")
    return status


@router.get("/routing/corridors")
def routing_corridors(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    from app.infrastructure.services.route_graph_cache import corridor_cache_summary

    return corridor_cache_summary(limit)

@router.get("/datasets/{dataset_id}/bbox")
def dataset_bbox(dataset_id: str) -> Dict[str, Any]:
    try:
        return dataset_usecases.get_dataset_bbox(dataset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/osm/status")
def osm_status(dataset_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    resolved_dataset_id = dataset_id or dataset_usecases.get_active_dataset_id()
    cache_id = resolved_dataset_id or "latest"
    cache_meta = dataset_repo.get_osm_cache(cache_id)
    from app.infrastructure.services.osm_graph import get_graphml_path, get_road_graph_status
    graph_path = get_graphml_path(resolved_dataset_id)
    graph_runtime = get_road_graph_status(graph_path)
    import os
    size_mb = 0.0
    cache_exists = False
    if cache_meta and graph_path.exists():
        cache_exists = True
        try:
            size_mb = round(os.path.getsize(graph_path) / (1024 * 1024), 2)
        except Exception:
            pass
    return {
        "status": "ok",
        "cache_exists": cache_exists,
        "cache_id": cache_id,
        "cache_path": str(graph_path),
        "size_mb": size_mb,
        "metadata": cache_meta,
        "graph_runtime": graph_runtime,
        "note": "OSM data diambil dari OpenStreetMap melalui OSMnx."
    }

@router.post("/nearest-mosques")
def nearest_mosques(req: NearestMosquesRequest) -> Dict[str, Any]:
    # Jika dataset_id adalah "all" atau kosong → gunakan "all" (lintas dataset)
    raw_did = req.dataset_id or ""
    if not raw_did or raw_did.lower() == "all":
        did = "all"
    else:
        did = dataset_usecases._slugify(raw_did) if hasattr(dataset_usecases, '_slugify') else raw_did
    try:
        return dataset_usecases.get_nearest_mosques(
            did,
            req.latitude,
            req.longitude,
            req.radius_km,
            req.limit,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc


@router.post("/realtime/location", status_code=202)
def ingest_realtime_location(req: RealtimeLocationEventRequest) -> Dict[str, Any]:
    try:
        return realtime_event_publisher.publish_location(req.model_dump())
    except RealtimePublisherUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/prayer-times")
def prayer_times(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    date: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """Fast offline Kemenag-style prayer calculation for slow/mobile links."""
    try:
        date_obj = dt.datetime.strptime(date, "%Y-%m-%d").date() if date else dt.date.today()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Tanggal harus berformat YYYY-MM-DD.") from exc
    from app.infrastructure.services.prayer_time import calculate_offline_prayer_times

    values = calculate_offline_prayer_times(latitude, longitude, date_obj)
    timezone = "Asia/Jayapura" if longitude >= 126 else "Asia/Makassar" if longitude >= 110 else "Asia/Jakarta"
    return {
        "source": "offline_kemenag_calculation",
        "date": date_obj.isoformat(),
        "timezone": timezone,
        "timings": {
            "Fajr": values["fajr"],
            "Dhuhr": values["dhuhr"],
            "Asr": values["asr"],
            "Maghrib": values["maghrib"],
            "Isha": values["isha"],
        },
    }

@router.post("/route/to-mosque")
def route_selected_mosque(req: RouteToMosqueRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_usecases.get_active_dataset_id()
    try:
        prayer_context = build_prayer_routing_context(
            req.prayer,
            req.start_lat,
            req.start_lon,
            req.departure_time,
        )
        payload = req.model_dump()
        payload["dataset_id"] = did
        result = routing_gateway.dispatch(
            endpoint="internal/v1/route/to-mosque",
            dataset_id=did,
            payload=payload,
            local_call=lambda: routing_usecases.route_to_mosque(
                req.start_lat, req.start_lon, req.mosque_id,
                req.algorithm, req.auto_build_osm, req.buffer_km, did,
                req.cost_parameters.model_dump(),
                current_time=prayer_context["departure_time"],
                prayer_time=prayer_context["target_prayer_time"],
            ),
        )
        attach_prayer_context(result, prayer_context)
        if req.compact_response:
            result.pop("route_geojson", None)
            result["geometry_encoding"] = "google_polyline5"
        return result
    except RoutingWorkerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))

@router.post("/osm/build-bbox")
def build_osm_bbox(req: BuildOsmRequest, protected: bool = Depends(_require_admin_access)) -> Dict[str, Any]:
    if not req.dataset_id:
        raise HTTPException(status_code=400, detail="Pilih dataset tujuan agar graph tidak tersimpan sebagai cache global yang tidak dipakai.")
    profile = dataset_repo.get_dataset(req.dataset_id)
    if not profile or not profile.get("processed", False):
        raise HTTPException(status_code=400, detail="Dataset belum selesai diproses atau tidak ditemukan.")
    if not _osm_graph_download_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Build graph lain sedang berjalan. Tunggu hingga selesai atau batalkan antrean massal.")
    try:
        result = routing_usecases.build_osm_bbox(req.north, req.south, req.east, req.west, req.network_type, req.dataset_id)
        _audit_admin_action("build_osm_bbox", target=str(req.dataset_id), protected=protected)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))
    finally:
        _osm_graph_download_lock.release()


@router.post("/osm/build-all")
def build_all_osm_graphs(req: BuildAllOsmRequest, background_tasks: BackgroundTasks, protected: bool = Depends(_require_admin_access)) -> Dict[str, Any]:
    with _osm_build_all_lock:
        if _osm_build_all_state["status"] in {"starting", "running", "cancelling"}:
            raise HTTPException(status_code=409, detail="Build graph semua dataset masih berjalan.")
        job_id = str(uuid.uuid4())
        _osm_build_all_state.update(status="starting", cancel_requested=False, job_id=job_id)
        _persist_build_all_state_locked()
    background_tasks.add_task(_run_build_all_graphs, req.network_type, req.force, job_id)
    _audit_admin_action("build_all_osm_graphs", target=job_id, protected=protected)
    return {"status": "accepted", "job_id": job_id, "message": "Build graph semua dataset dimasukkan ke antrean."}


@router.get("/osm/build-all/status")
def build_all_osm_status() -> Dict[str, Any]:
    from app.infrastructure.services.osm_graph import get_graphml_path

    with _osm_build_all_lock:
        state = copy.deepcopy(_osm_build_all_state)
    dataset_ids = [d.get("_key") or d.get("dataset_id") for d in dataset_repo.list_datasets()]
    available_graphs = 0
    for dataset_id in dataset_ids:
        if not dataset_id or not get_graphml_path(dataset_id).exists():
            continue
        metadata = dataset_repo.get_osm_cache(dataset_id)
        if metadata and metadata.get("build_scope") in {None, "dataset_bbox", "priority_area"}:
            available_graphs += 1
    state["available_graphs"] = available_graphs
    state["total_datasets"] = len(dataset_ids)
    return state


@router.post("/osm/build-all/cancel")
def cancel_build_all_osm(protected: bool = Depends(_require_admin_access)) -> Dict[str, Any]:
    with _osm_build_all_lock:
        if _osm_build_all_state["status"] not in {"running", "starting"}:
            return {"status": _osm_build_all_state["status"], "message": "Tidak ada build massal yang sedang berjalan."}
        _osm_build_all_state["cancel_requested"] = True
        _osm_build_all_state["status"] = "cancelling"
        _persist_build_all_state_locked()
    _audit_admin_action("cancel_build_all_osm", protected=protected)
    return {"status": "cancelling", "message": "Build akan dihentikan setelah dataset yang sedang diproses selesai."}

@router.post("/osm/build-route")
def build_osm_route(req: BuildOsmRouteRequest) -> Dict[str, Any]:
    if not _osm_graph_download_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Build graph lain sedang berjalan.")
    try:
        return routing_usecases.build_osm_route(
            start_lat=req.start_lat,
            start_lon=req.start_lon,
            end_lat=req.end_lat,
            end_lon=req.end_lon,
            buffer_km=req.buffer_km,
            network_type=req.network_type,
            dataset_id=req.dataset_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))
    finally:
        _osm_graph_download_lock.release()

@router.post("/route")
def route(req: RouteRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_usecases.get_active_dataset_id()
    try:
        payload = req.model_dump()
        payload["dataset_id"] = did
        result = routing_gateway.dispatch(
            endpoint="internal/v1/route",
            dataset_id=did,
            payload=payload,
            local_call=lambda: routing_usecases.route_via_osm_dijkstra(
                req.start_lat, req.start_lon, req.end_lat, req.end_lon,
                req.algorithm, req.current_time, req.prayer_time,
                req.max_candidates, req.auto_build_osm, req.buffer_km, did
            ),
        )
        return result
    except RoutingWorkerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))

@router.delete("/mosques/{dataset_id}/{mosque_id}")
def delete_mosque(dataset_id: str, mosque_id: str) -> Dict[str, Any]:
    success = dataset_usecases.delete_mosque(dataset_id, mosque_id)
    if not success:
        raise HTTPException(status_code=404, detail="Masjid tidak ditemukan atau tidak dapat dihapus.")
    routing_usecases.start_mosque_candidate_prewarm(dataset_id)
    return {"status": "success", "message": f"Masjid {mosque_id} berhasil dihapus."}

@router.post("/mosques/{dataset_id}")
def create_mosque(dataset_id: str, req: MosqueCreateRequest) -> Dict[str, Any]:
    try:
        mosque_id = dataset_usecases.create_mosque(dataset_id, req.dict(exclude_unset=True))
        routing_usecases.start_mosque_candidate_prewarm(dataset_id)
        return {"status": "success", "mosque_id": mosque_id, "message": "Masjid berhasil ditambahkan."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.put("/mosques/{dataset_id}/{mosque_id}")
def update_mosque(dataset_id: str, mosque_id: str, req: MosqueUpdateRequest) -> Dict[str, Any]:
    try:
        success = dataset_usecases.update_mosque(dataset_id, mosque_id, req.dict(exclude_unset=True))
        if not success:
            raise HTTPException(status_code=404, detail="Masjid tidak ditemukan atau tidak dapat diperbarui.")
        routing_usecases.start_mosque_candidate_prewarm(dataset_id)
        return {"status": "success", "message": f"Masjid {mosque_id} berhasil diperbarui."}
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/mosques/bulk-delete")
def delete_mosques_bulk(req: BulkDeleteRequest) -> Dict[str, Any]:
    success = dataset_usecases.delete_mosques_bulk(req.dataset_id, req.mosque_ids)
    if not success:
        raise HTTPException(status_code=400, detail="Gagal menghapus masjid secara masal.")
    routing_usecases.start_mosque_candidate_prewarm(req.dataset_id)
    return {"status": "success", "message": f"{len(req.mosque_ids)} masjid berhasil dihapus."}

@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, protected: bool = Depends(_require_admin_access)) -> Dict[str, Any]:
    if _graph_build_active():
        raise HTTPException(status_code=409, detail="Dataset tidak dapat dihapus saat build graph berjalan.")
    success = dataset_usecases.delete_dataset(dataset_id)
    if not success:
        raise HTTPException(status_code=404, detail="Dataset tidak ditemukan atau tidak dapat dihapus.")
    _audit_admin_action("delete_dataset", target=dataset_id, protected=protected)
    return {"status": "success", "message": f"Dataset {dataset_id} berhasil dihapus."}


@router.post("/routes/recommend")
def recommend_route(req: RecommendRouteRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_usecases.get_active_dataset_id()
    
    start_lat = req.origin.latitude
    start_lon = req.origin.longitude
    if req.destination:
        end_lat = req.destination.latitude
        end_lon = req.destination.longitude
    else:
        end_lat = start_lat
        end_lon = start_lon

    algo = req.algorithm
    
    try:
        prayer_context = build_prayer_routing_context(
            req.prayer,
            start_lat,
            start_lon,
            req.departure_time,
        )
        payload = req.model_dump()
        payload["dataset_id"] = did
        result = routing_gateway.dispatch(
            endpoint="internal/v1/routes/recommend",
            dataset_id=did,
            payload=payload,
            local_call=lambda: routing_usecases.route_via_osm_dijkstra(
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                algorithm=algo,
                current_time=prayer_context["departure_time"],
                prayer_time=prayer_context["target_prayer_time"],
                max_candidates=req.maximum_results,
                auto_build_osm=req.auto_build_osm,
                buffer_km=req.search_radius_km,
                dataset_id=did,
                profile=req.profile,
                cost_parameters=req.cost_parameters.model_dump(),
            ),
        )
        attach_prayer_context(result, prayer_context)
        if req.compact_response:
            result.pop("route_geojson", None)
            result["geometry_encoding"] = "google_polyline5"
        return result
    except RoutingWorkerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))


@router.post("/routes/benchmark")
def benchmark_routes(req: BenchmarkRequest) -> Any:
    did = req.dataset_id or dataset_usecases.get_active_dataset_id()
    try:
        from app.infrastructure.services.osm_graph import (
            benchmark_pathfinding_algorithms,
            get_graphml_path,
            get_road_graph_status,
            graph_covers_points,
            load_road_graph,
            nearest_road_nodes_batch,
            start_road_graph_prewarm,
        )
        from app.infrastructure.services.route_graph_cache import (
            CorridorAreaTooLarge,
            find_covering_corridor_graph,
            metadata_covers_points,
            start_corridor_graph_build,
        )

        origin = (float(req.origin.latitude), float(req.origin.longitude))
        destination = (float(req.destination.latitude), float(req.destination.longitude))
        points = (origin, destination)
        graph_path = find_covering_corridor_graph(did, points)
        graph_scope = "route_corridor"

        base_graph_path = get_graphml_path(did)
        try:
            base_metadata = dataset_repo.get_osm_cache(did)
        except Exception:
            base_metadata = None
        if graph_path is None and base_graph_path.exists() and (
            not base_metadata or metadata_covers_points(base_metadata, points)
        ):
            graph_path = base_graph_path
            graph_scope = str((base_metadata or {}).get("build_scope", "dataset_graph"))

        if graph_path is None:
            try:
                corridor = start_corridor_graph_build(
                    did,
                    points,
                    buffer_km=min(float(req.search_radius_km), 10.0),
                )
            except CorridorAreaTooLarge as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if corridor.get("status") == "error":
                raise HTTPException(
                    status_code=503,
                    detail=corridor.get("error") or "Pembangunan graph koridor gagal.",
                )
            if not corridor.get("ready"):
                return JSONResponse(
                    status_code=202,
                    content={
                        "status": "preparing_graph",
                        "message": "Menyiapkan graph jalan lokal untuk wilayah rute. Benchmark akan dilanjutkan otomatis.",
                        "corridor": corridor,
                        "status_url": f"/api/v1/routing/corridors/{corridor.get('graph_id')}",
                        "retry_after_ms": int(corridor.get("retry_after_ms", 1500)),
                    },
                )
            graph_path = Path(str(corridor["graphml_path"]))

        graph_runtime = get_road_graph_status(graph_path)
        if graph_scope == "route_corridor" and not graph_runtime.get("ready"):
            prewarm_started = start_road_graph_prewarm(graph_path)
            graph_id = graph_path.stem.removeprefix("road_graph_corridor_")
            return JSONResponse(
                status_code=202,
                content={
                    "status": "preparing_graph",
                    "message": "Memuat graph koridor dan indeks jalan ke memori.",
                    "corridor": {
                        "graph_id": graph_id,
                        "status": "loading",
                        "ready": False,
                        "prewarm_started": prewarm_started,
                    },
                    "status_url": f"/api/v1/routing/corridors/{graph_id}",
                    "retry_after_ms": 1000,
                },
            )

        graph = load_road_graph(graph_path)
        if not graph_covers_points(graph, [origin, destination], margin_km=0.5):
            corridor = start_corridor_graph_build(
                did,
                points,
                buffer_km=min(float(req.search_radius_km), 10.0),
            )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "preparing_graph",
                    "message": "Graph lama tidak mencakup titik rute; graph koridor baru sedang disiapkan.",
                    "corridor": corridor,
                    "status_url": f"/api/v1/routing/corridors/{corridor.get('graph_id')}",
                    "retry_after_ms": int(corridor.get("retry_after_ms", 1500)),
                },
            )
        source, target = nearest_road_nodes_batch(graph, [origin, destination])
        if source == target:
            raise HTTPException(
                status_code=422,
                detail="Titik awal dan tujuan tersambung ke node jalan yang sama; pilih titik yang lebih berjauhan.",
            )

        measured = benchmark_pathfinding_algorithms(
            graph,
            source,
            target,
            weight="travel_time",
        )
        dijkstra = measured["dijkstra"]
        astar = measured["astar"]
        dijkstra_ms = float(dijkstra["execution_time_ms"])
        astar_ms = float(astar["execution_time_ms"])
        faster_algorithm = (
            "Sama"
            if astar_ms == dijkstra_ms
            else ("A*" if astar_ms < dijkstra_ms else "Dijkstra")
        )
        slower_ms = max(dijkstra_ms, astar_ms)
        time_difference_ms = abs(dijkstra_ms - astar_ms)
        dijkstra_nodes = int(dijkstra["expanded_nodes"])
        astar_nodes = int(astar["expanded_nodes"])
        fewer_nodes_algorithm = (
            "Sama"
            if astar_nodes == dijkstra_nodes
            else ("A*" if astar_nodes < dijkstra_nodes else "Dijkstra")
        )
        optimal_cost_match = math.isclose(
            float(dijkstra["route_travel_time_seconds"]),
            float(astar["route_travel_time_seconds"]),
            rel_tol=1e-9,
            abs_tol=1e-7,
        )
        if not optimal_cost_match:
            raise RuntimeError("Audit optimalitas gagal: biaya A* berbeda dari Dijkstra.")

        return {
            "status": "success",
            "benchmark": {
                "dijkstra": {
                    **dijkstra,
                    "algorithm": "Dijkstra (Bidirectional)",
                    "explored_nodes": dijkstra_nodes,
                },
                "astar": {
                    **astar,
                    "algorithm": "A* (Heuristik Konsisten)",
                    "explored_nodes": astar_nodes,
                },
                "comparison": {
                    "faster_algorithm": faster_algorithm,
                    "time_difference_ms": round(time_difference_ms, 2),
                    "efficiency_gain_percent": round(
                        (time_difference_ms / slower_ms) * 100.0 if slower_ms > 0 else 0.0,
                        1,
                    ),
                    "fewer_explored_algorithm": fewer_nodes_algorithm,
                    "explored_nodes_difference": abs(dijkstra_nodes - astar_nodes),
                    "nodes_saved": max(0, dijkstra_nodes - astar_nodes),
                    "optimal_cost_match": True,
                },
                "measurement": {
                    "scope": "shortest_path_only",
                    "weight": "travel_time_seconds",
                    "graph_warm": True,
                    "cache_used": graph_scope == "route_corridor",
                    "graph_scope": graph_scope,
                    "graph_path": str(graph_path),
                    "source_node": str(source),
                    "target_node": str(target),
                },
                "graph_runtime": get_road_graph_status(graph_path),
            },
        }
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(status_code=500, detail=_friendly_error(exc))


@router.get("/routes/{route_id}")
def get_route_by_id(route_id: str) -> Dict[str, Any]:
    return {
        "route_id": route_id,
        "type": "Feature",
        "properties": {
            "name": f"Saved Route {route_id}",
            "created_at": "2026-07-11T14:55:00+07:00",
        },
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [106.8166, -6.2000],
                [106.8200, -6.2100],
                [106.8300, -6.2200]
            ]
        }
    }


@router.get("/routing-profiles")
def get_routing_profiles() -> Dict[str, Any]:
    from app.infrastructure.services.routing_osm import (
        DEFAULT_TRAVEL_COST_PARAMETERS,
        MULTI_OBJECTIVE_PROFILE_WEIGHTS,
    )

    return {
        "profiles": [
            {
                "name": name,
                "label": {
                    "fastest": "Fastest (Waktu Tercepat)",
                    "prayer_priority": "Prayer Priority (Prioritas Salat)",
                    "low_cost": "Low Cost (Biaya Rupiah Terendah)",
                    "balanced": "Balanced (Seimbang)",
                }[name],
                "weights": weights,
            }
            for name, weights in MULTI_OBJECTIVE_PROFILE_WEIGHTS.items()
        ],
        "cost_model": {
            "currency": "IDR",
            "formula": "fuel + vehicle_operation + toll",
            "default_parameters": DEFAULT_TRAVEL_COST_PARAMETERS,
            "note": "Parameter dapat dioverride melalui cost_parameters pada POST /routes/recommend.",
        },
    }



# ==================== USER SETTINGS ENDPOINTS ====================

from app.domain.repositories.user_settings_repo import save_user_settings, get_user_settings, delete_user_settings


@router.post("/user-settings")
def save_settings(req: UserSettingsRequest) -> Dict[str, Any]:
    """
    Simpan user settings (routing & prayer) ke database.
    
    Body:
    {
        "user_id": "device_abc123",
        "search_settings": {
            "algorithm": "dijkstra",
            "profile": "balanced",
            "currentTime": "17:00",
            "prayer": "maghrib",
            "bufferKm": "15",
            "maxCandidates": "3",
            "autoBuild": false
        },
        "prayer_settings": {
            "schedule": [...],
            "hijriDate": "17 Ramadan 1435 H",
            "masehiDate": "14 July 2014"
        },
        "updated_at": "2026-07-14T17:00:00Z"
    }
    """
    try:
        settings_data: Dict[str, Any] = {}
        if req.search_settings is not None:
            settings_data['search_settings'] = req.search_settings.model_dump(exclude_unset=True)
        if req.prayer_settings is not None:
            settings_data['prayer_settings'] = req.prayer_settings.model_dump(exclude_unset=True)
        if req.updated_at is not None:
            settings_data['client_updated_at'] = req.updated_at.isoformat()
        
        result = save_user_settings(req.user_id, settings_data)
        
        return {
            "status": "success",
            "message": "Settings berhasil disimpan ke database",
            "user_id": req.user_id,
            "data": result
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan settings: {str(exc)}")


@router.get("/user-settings/{user_id}")
def load_settings(user_id: str) -> Dict[str, Any]:
    """
    Ambil user settings dari database.
    
    Response:
    {
        "status": "success",
        "user_id": "device_abc123",
        "data": {
            "search_settings": {...},
            "prayer_settings": {...},
            "updated_at": "2026-07-14T17:00:00Z"
        }
    }
    """
    try:
        result = get_user_settings(user_id)
        
        if result:
            return {
                "status": "success",
                "user_id": user_id,
                "data": result
            }
        else:
            return {
                "status": "not_found",
                "user_id": user_id,
                "message": "User settings belum ada di database",
                "data": None
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil settings: {str(exc)}")


@router.delete("/user-settings/{user_id}")
def reset_settings(user_id: str) -> Dict[str, Any]:
    """
    Hapus user settings (untuk reset atau logout).
    """
    try:
        success = delete_user_settings(user_id)
        
        if success:
            return {
                "status": "success",
                "message": f"Settings user {user_id} berhasil dihapus"
            }
        else:
            return {
                "status": "not_found",
                "message": f"Settings user {user_id} tidak ditemukan"
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal menghapus settings: {str(exc)}")
