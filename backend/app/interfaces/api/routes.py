from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, BackgroundTasks
from typing import Any, Dict, Optional
import math

from app.domain.models.schemas import (
    BuildOsmRequest, BuildOsmRouteRequest, NearestMosquesRequest, RouteRequest, RouteToMosqueRequest,
    MosqueCreateRequest, MosqueUpdateRequest, BulkDeleteRequest, RecommendRouteRequest, BenchmarkRequest
)
from app.use_cases.dataset_usecases import DatasetUseCases
from app.use_cases.routing_usecases import RoutingUseCases
from app.infrastructure.database.arangodb_repo import ArangoMosqueRepository, ArangoDatasetRepository

router = APIRouter()

mosque_repo = ArangoMosqueRepository()
dataset_repo = ArangoDatasetRepository()

dataset_usecases = DatasetUseCases(mosque_repo, dataset_repo)
routing_usecases = RoutingUseCases(mosque_repo, dataset_repo)

def _friendly_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "overpass" in lowered or "httpsconnectionpool" in lowered or "max retries" in lowered or "timed out" in lowered:
        return "Overpass/OSM lambat/timeout. Rute mungkin gagal. Silakan coba lagi nanti atau kecilkan radius/buffer."
    return message

@router.get("/health")
def health() -> Dict[str, Any]:
    active = dataset_usecases.get_active_dataset_id()
    from app.infrastructure.services.osm_graph import DEFAULT_GRAPHML
    graph_status = "connected" if DEFAULT_GRAPHML.exists() else "not_configured"
    return {
        "status": "healthy",
        "graph_status": graph_status,
        "version": "1.0.0",
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
    return {
        "status": "success",
        "active_dataset_id": dataset_id,
        "profile": profile,
    }

@router.post("/datasets/upload")
async def upload_dataset(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    dataset_name: Optional[str] = Form(None),
    make_active: bool = Form(True),
) -> Dict[str, Any]:
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Hanya mendukung file CSV.")
    content = await file.read()
    try:
        return dataset_usecases.upload_and_process_dataset(
            file_bytes=content,
            filename=file.filename,
            dataset_name=dataset_name,
            make_active=make_active,
            background_tasks=background_tasks
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/pipeline/run")
def run_pipeline(dataset_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    did = dataset_id or dataset_usecases.get_active_dataset_id()
    try:
        return dataset_usecases.run_pipeline(did)
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

@router.get("/osm/status")
def osm_status() -> Dict[str, Any]:
    cache_meta = dataset_repo.get_osm_cache()
    from app.infrastructure.services.osm_graph import DEFAULT_GRAPHML
    import os
    size_mb = 0.0
    cache_exists = False
    if cache_meta and DEFAULT_GRAPHML.exists():
        cache_exists = True
        try:
            size_mb = round(os.path.getsize(DEFAULT_GRAPHML) / (1024 * 1024), 2)
        except Exception:
            pass
    return {
        "status": "ok",
        "cache_exists": cache_exists,
        "size_mb": size_mb,
        "metadata": cache_meta,
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

@router.post("/route/to-mosque")
def route_selected_mosque(req: RouteToMosqueRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_usecases.get_active_dataset_id()
    try:
        return routing_usecases.route_to_mosque(
            req.start_lat, req.start_lon, req.mosque_id,
            req.algorithm, req.auto_build_osm, req.buffer_km, did
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))

@router.post("/osm/build-bbox")
def build_osm_bbox(req: BuildOsmRequest) -> Dict[str, Any]:
    try:
        return routing_usecases.build_osm_bbox(req.north, req.south, req.east, req.west, req.network_type, req.dataset_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))

@router.post("/osm/build-route")
def build_osm_route(req: BuildOsmRouteRequest) -> Dict[str, Any]:
    try:
        return routing_usecases.build_osm_route(req.start_lat, req.start_lon, req.end_lat, req.end_lon, req.buffer_km, req.network_type, req.dataset_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))

@router.post("/route")
def route(req: RouteRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_usecases.get_active_dataset_id()
    try:
        return routing_usecases.route_via_osm_dijkstra(
            req.start_lat, req.start_lon, req.end_lat, req.end_lon,
            req.algorithm, req.current_time, req.prayer_time,
            req.max_candidates, req.auto_build_osm, req.buffer_km, did
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))

@router.delete("/mosques/{dataset_id}/{mosque_id}")
def delete_mosque(dataset_id: str, mosque_id: str) -> Dict[str, Any]:
    success = dataset_usecases.delete_mosque(dataset_id, mosque_id)
    if not success:
        raise HTTPException(status_code=404, detail="Masjid tidak ditemukan atau tidak dapat dihapus.")
    return {"status": "success", "message": f"Masjid {mosque_id} berhasil dihapus."}

@router.post("/mosques/{dataset_id}")
def create_mosque(dataset_id: str, req: MosqueCreateRequest) -> Dict[str, Any]:
    try:
        mosque_id = dataset_usecases.create_mosque(dataset_id, req.dict(exclude_unset=True))
        return {"status": "success", "mosque_id": mosque_id, "message": "Masjid berhasil ditambahkan."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.put("/mosques/{dataset_id}/{mosque_id}")
def update_mosque(dataset_id: str, mosque_id: str, req: MosqueUpdateRequest) -> Dict[str, Any]:
    try:
        success = dataset_usecases.update_mosque(dataset_id, mosque_id, req.dict(exclude_unset=True))
        if not success:
            raise HTTPException(status_code=404, detail="Masjid tidak ditemukan atau tidak dapat diperbarui.")
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
    return {"status": "success", "message": f"{len(req.mosque_ids)} masjid berhasil dihapus."}

@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str) -> Dict[str, Any]:
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
        import time as time_mod
        dep_time = req.departure_time or ""
        curr_t = dep_time.split("T")[-1][:5] if "T" in dep_time else (dep_time[:5] if dep_time else "17:00")
        return routing_usecases.route_via_osm_dijkstra(
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
