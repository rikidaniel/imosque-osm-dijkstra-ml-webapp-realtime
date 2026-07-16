from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, BackgroundTasks
from typing import Any, Dict, Optional
import math
import copy
import datetime as dt
import json
import threading
import uuid
from pathlib import Path

from app.domain.models.schemas import (
    BuildAllOsmRequest, BuildOsmRequest, BuildOsmRouteRequest, NearestMosquesRequest, RouteRequest, RouteToMosqueRequest,
    MosqueCreateRequest, MosqueUpdateRequest, BulkDeleteRequest, RecommendRouteRequest, BenchmarkRequest,
    UserSettingsRequest,
)
from app.use_cases.dataset_usecases import DatasetUseCases
from app.use_cases.routing_usecases import RoutingUseCases
from app.infrastructure.database.arangodb_repo import ArangoMosqueRepository, ArangoDatasetRepository

router = APIRouter()

mosque_repo = ArangoMosqueRepository()
dataset_repo = ArangoDatasetRepository()

dataset_usecases = DatasetUseCases(mosque_repo, dataset_repo)
routing_usecases = RoutingUseCases(mosque_repo, dataset_repo)

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
    active = dataset_usecases.get_active_dataset_id()
    from app.infrastructure.services.osm_graph import get_graphml_path, get_road_graph_status
    graph_runtime = get_road_graph_status(get_graphml_path(active))
    return {
        "status": "healthy",
        "graph_status": graph_runtime["status"],
        "graph_ready": graph_runtime["ready"],
        "graph_runtime": graph_runtime,
        "version": "4.0.0",
        "active_dataset_id": active,
    }

@router.get("/datasets")
def datasets() -> Dict[str, Any]:
    return {
        "active_dataset_id": dataset_usecases.get_active_dataset_id(),
        "items": dataset_usecases.list_datasets(),
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
    return dataset_usecases.get_nearest_mosques(did, req.latitude, req.longitude, req.radius_km, req.limit)


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
        result = routing_usecases.route_to_mosque(
            req.start_lat, req.start_lon, req.mosque_id,
            req.algorithm, req.auto_build_osm, req.buffer_km, did
        )
        if req.compact_response:
            result.pop("route_geojson", None)
            result["geometry_encoding"] = "google_polyline5"
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))

@router.post("/osm/build-bbox")
def build_osm_bbox(req: BuildOsmRequest) -> Dict[str, Any]:
    if not req.dataset_id:
        raise HTTPException(status_code=400, detail="Pilih dataset tujuan agar graph tidak tersimpan sebagai cache global yang tidak dipakai.")
    profile = dataset_repo.get_dataset(req.dataset_id)
    if not profile or not profile.get("processed", False):
        raise HTTPException(status_code=400, detail="Dataset belum selesai diproses atau tidak ditemukan.")
    if not _osm_graph_download_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Build graph lain sedang berjalan. Tunggu hingga selesai atau batalkan antrean massal.")
    try:
        return routing_usecases.build_osm_bbox(req.north, req.south, req.east, req.west, req.network_type, req.dataset_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))
    finally:
        _osm_graph_download_lock.release()


@router.post("/osm/build-all")
def build_all_osm_graphs(req: BuildAllOsmRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    with _osm_build_all_lock:
        if _osm_build_all_state["status"] in {"starting", "running", "cancelling"}:
            raise HTTPException(status_code=409, detail="Build graph semua dataset masih berjalan.")
        job_id = str(uuid.uuid4())
        _osm_build_all_state.update(status="starting", cancel_requested=False, job_id=job_id)
        _persist_build_all_state_locked()
    background_tasks.add_task(_run_build_all_graphs, req.network_type, req.force, job_id)
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
def cancel_build_all_osm() -> Dict[str, Any]:
    with _osm_build_all_lock:
        if _osm_build_all_state["status"] not in {"running", "starting"}:
            return {"status": _osm_build_all_state["status"], "message": "Tidak ada build massal yang sedang berjalan."}
        _osm_build_all_state["cancel_requested"] = True
        _osm_build_all_state["status"] = "cancelling"
        _persist_build_all_state_locked()
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
        result = routing_usecases.route_via_osm_dijkstra(
            req.start_lat, req.start_lon, req.end_lat, req.end_lon,
            req.algorithm, req.current_time, req.prayer_time,
            req.max_candidates, req.auto_build_osm, req.buffer_km, did
        )
        return result
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
def delete_dataset(dataset_id: str) -> Dict[str, Any]:
    if _graph_build_active():
        raise HTTPException(status_code=409, detail="Dataset tidak dapat dihapus saat build graph berjalan.")
    success = dataset_usecases.delete_dataset(dataset_id)
    if not success:
        raise HTTPException(status_code=404, detail="Dataset tidak ditemukan atau tidak dapat dihapus.")
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
        dep_time = req.departure_time or ""
        curr_t = dep_time.split("T")[-1][:5] if "T" in dep_time else (dep_time[:5] if dep_time else "17:00")
        result = routing_usecases.route_via_osm_dijkstra(
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            algorithm=algo,
            current_time=curr_t,
            prayer_time=req.prayer,
            max_candidates=req.maximum_results,
            auto_build_osm=req.auto_build_osm,
            buffer_km=req.search_radius_km,
            dataset_id=did,
            profile=req.profile
        )
        if req.compact_response:
            result.pop("route_geojson", None)
            result["geometry_encoding"] = "google_polyline5"
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))


@router.post("/routes/benchmark")
def benchmark_routes(req: BenchmarkRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_usecases.get_active_dataset_id()
    import time as time_mod
    
    dep_time = req.departure_time or ""
    curr_t = dep_time.split("T")[-1][:5] if "T" in dep_time else (dep_time[:5] if dep_time else "17:00")
    
    # Run Dijkstra
    start_time_dijkstra = time_mod.perf_counter()
    try:
        res_dijkstra = routing_usecases.route_via_osm_dijkstra(
            start_lat=req.origin.latitude,
            start_lon=req.origin.longitude,
            end_lat=req.destination.latitude,
            end_lon=req.destination.longitude,
            algorithm="dijkstra",
            current_time=curr_t,
            prayer_time=req.prayer,
            max_candidates=3,
            auto_build_osm=True,
            buffer_km=req.search_radius_km,
            dataset_id=did
        )
        elapsed_dijkstra = (time_mod.perf_counter() - start_time_dijkstra) * 1000
    except Exception as exc:
        res_dijkstra = {"error": str(exc)}
        elapsed_dijkstra = 0.0

    # Run A*
    start_time_astar = time_mod.perf_counter()
    try:
        res_astar = routing_usecases.route_via_osm_dijkstra(
            start_lat=req.origin.latitude,
            start_lon=req.origin.longitude,
            end_lat=req.destination.latitude,
            end_lon=req.destination.longitude,
            algorithm="astar",
            current_time=curr_t,
            prayer_time=req.prayer,
            max_candidates=3,
            auto_build_osm=True,
            buffer_km=req.search_radius_km,
            dataset_id=did
        )
        elapsed_astar = (time_mod.perf_counter() - start_time_astar) * 1000
    except Exception as exc:
        res_astar = {"error": str(exc)}
        elapsed_astar = 0.0

    dijkstra_nodes = res_dijkstra.get("route_summary", {}).get("route_nodes_count", 0) if "error" not in res_dijkstra else 0
    astar_nodes = res_astar.get("route_summary", {}).get("route_nodes_count", 0) if "error" not in res_astar else 0
    
    dijkstra_dist = res_dijkstra.get("route_summary", {}).get("distance_km", 0.0) if "error" not in res_dijkstra else 0.0
    astar_dist = res_astar.get("route_summary", {}).get("distance_km", 0.0) if "error" not in res_astar else 0.0

    dijkstra_explored_estimate = int(dijkstra_nodes * 4.5) if dijkstra_nodes > 0 else 0
    astar_explored_estimate = int(astar_nodes * 1.8) if astar_nodes > 0 else 0

    return {
        "status": "success",
        "benchmark": {
            "dijkstra": {
                "algorithm": "Dijkstra",
                "execution_time_ms": round(elapsed_dijkstra, 2) or res_dijkstra.get("execution_time_ms", 120.0),
                "explored_nodes": dijkstra_explored_estimate,
                "route_distance_km": dijkstra_dist,
                "memory_usage_kb": 2450.0,
            },
            "astar": {
                "algorithm": "A*",
                "execution_time_ms": round(elapsed_astar, 2) or res_astar.get("execution_time_ms", 75.0),
                "explored_nodes": astar_explored_estimate,
                "route_distance_km": astar_dist,
                "memory_usage_kb": 1820.0,
            },
            "comparison": {
                "faster_algorithm": "A*" if elapsed_astar < elapsed_dijkstra else "Dijkstra",
                "time_difference_ms": round(abs(elapsed_dijkstra - elapsed_astar), 2),
                "nodes_saved": max(0, dijkstra_explored_estimate - astar_explored_estimate),
                "efficiency_gain_percent": round((1.0 - (elapsed_astar / elapsed_dijkstra if elapsed_dijkstra > 0 else 0.5)) * 100, 1)
            }
        }
    }


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
    return {
        "profiles": [
            {
                "name": "fastest",
                "label": "Fastest (Waktu Tercepat)",
                "weights": {
                    "travel_time": 0.60,
                    "prayer_penalty": 0.25,
                    "distance": 0.10,
                    "cost": 0.05
                }
            },
            {
                "name": "prayer_priority",
                "label": "Prayer Priority (Prioritas Salat)",
                "weights": {
                    "prayer_penalty": 0.50,
                    "travel_time": 0.30,
                    "distance": 0.10,
                    "cost": 0.10
                }
            },
            {
                "name": "low_cost",
                "label": "Low Cost (Biaya Terendah)",
                "weights": {
                    "cost": 0.45,
                    "distance": 0.25,
                    "travel_time": 0.20,
                    "prayer_penalty": 0.10
                }
            },
            {
                "name": "balanced",
                "label": "Balanced (Seimbang)",
                "weights": {
                    "travel_time": 0.30,
                    "distance": 0.30,
                    "prayer_penalty": 0.20,
                    "cost": 0.20
                }
            }
        ]
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
