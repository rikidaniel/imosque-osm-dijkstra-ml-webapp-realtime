import os
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import Depends, FastAPI, Header, HTTPException

from app.domain.models.schemas import RecommendRouteRequest, RouteRequest, RouteToMosqueRequest, RoutingPrewarmRequest
from app.infrastructure.database.arangodb_client import check_db_health, init_db
from app.infrastructure.database.arangodb_repo import ArangoDatasetRepository, ArangoMosqueRepository
from app.infrastructure.services.osm_graph import get_graphml_path, get_road_graph_status, start_road_graph_prewarm
from app.infrastructure.services.routing_osm import attach_prayer_context, build_prayer_routing_context
from app.use_cases.routing_usecases import RoutingUseCases


mosque_repo = ArangoMosqueRepository()
dataset_repo = ArangoDatasetRepository()
routing_usecases = RoutingUseCases(mosque_repo, dataset_repo)


def _configured_datasets() -> list[str]:
    configured = [
        item.strip()
        for item in os.getenv("IMOSQUE_ROUTING_DATASET_IDS", "").split(",")
        if item.strip()
    ]
    if configured:
        return configured
    try:
        return [dataset_repo.get_active_dataset_id()]
    except Exception:
        return []


def _require_internal_token(x_internal_token: str = Header(default="")) -> None:
    expected = os.getenv("IMOSQUE_ROUTING_INTERNAL_TOKEN", "")
    if expected and x_internal_token != expected:
        raise HTTPException(status_code=401, detail="Token routing internal tidak valid")


def _prewarm_worker_graphs() -> None:
    if os.getenv("IMOSQUE_PREWARM_GRAPH_ON_STARTUP", "true").lower() not in {"1", "true", "yes"}:
        return
    for dataset_id in _configured_datasets():
        start_road_graph_prewarm(get_graphml_path(dataset_id))
        routing_usecases.start_mosque_candidate_prewarm(dataset_id)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    _prewarm_worker_graphs()
    yield


app = FastAPI(
    title="iMosque Regional Routing Worker",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/internal/v1/health", dependencies=[Depends(_require_internal_token)])
def health() -> Dict[str, Any]:
    db_ok, db_error = check_db_health()
    datasets = _configured_datasets()
    graph_states = {
        dataset_id: get_road_graph_status(get_graphml_path(dataset_id))
        for dataset_id in datasets
    }


@app.post("/internal/v1/routing/prewarm", dependencies=[Depends(_require_internal_token)])
def prewarm(req: RoutingPrewarmRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_repo.get_active_dataset_id()
    coordinates = (req.start_lat, req.start_lon, req.end_lat, req.end_lon)
    if any(value is not None for value in coordinates) and not all(
        value is not None for value in coordinates
    ):
        raise HTTPException(
            status_code=400,
            detail="Koordinat prewarm harus lengkap.",
        )

    corridor = None
    graph_started = False
    graph_path = get_graphml_path(did)
    if all(value is not None for value in coordinates):
        from app.infrastructure.services.route_graph_cache import (
            CorridorAreaTooLarge,
            start_corridor_graph_build,
        )

        try:
            corridor = start_corridor_graph_build(
                did,
                (
                    (float(req.start_lat), float(req.start_lon)),
                    (float(req.end_lat), float(req.end_lon)),
                ),
                buffer_km=min(float(req.buffer_km), 10.0),
            )
        except CorridorAreaTooLarge as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        graph_started = start_road_graph_prewarm(graph_path)

    mosque_started = routing_usecases.start_mosque_candidate_prewarm(did)
    return {
        "status": corridor.get("status") if corridor else (
            "warming" if graph_started else get_road_graph_status(graph_path).get("status")
        ),
        "dataset_id": did,
        "graph_prewarm_started": graph_started,
        "mosque_prewarm_started": mosque_started,
        "graph_runtime": get_road_graph_status(graph_path),
        "corridor": corridor,
    }
    healthy = bool(datasets) and db_ok and all(
        state.get("ready") or state.get("status") == "loading"
        for state in graph_states.values()
    )
    return {
        "status": "healthy" if healthy else "degraded",
        "database": {"connected": db_ok, "error": db_error},
        "datasets": datasets,
        "graphs": graph_states,
    }


@app.post("/internal/v1/route", dependencies=[Depends(_require_internal_token)])
def route(req: RouteRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_repo.get_active_dataset_id()
    return routing_usecases.route_via_osm_dijkstra(
        req.start_lat,
        req.start_lon,
        req.end_lat,
        req.end_lon,
        req.algorithm,
        req.current_time,
        req.prayer_time,
        req.max_candidates,
        req.auto_build_osm,
        req.buffer_km,
        did,
        req.cost_parameters.model_dump(),
    )


@app.post("/internal/v1/route/to-mosque", dependencies=[Depends(_require_internal_token)])
def route_to_mosque(req: RouteToMosqueRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_repo.get_active_dataset_id()
    prayer_context = build_prayer_routing_context(
        req.prayer,
        req.start_lat,
        req.start_lon,
        req.departure_time,
    )
    result = routing_usecases.route_to_mosque(
        req.start_lat,
        req.start_lon,
        req.mosque_id,
        req.algorithm,
        req.auto_build_osm,
        req.buffer_km,
        did,
        req.cost_parameters.model_dump(),
        prayer_context["departure_time"],
        prayer_context["target_prayer_time"],
    )
    attach_prayer_context(result, prayer_context)
    if req.compact_response:
        result.pop("route_geojson", None)
        result["geometry_encoding"] = "google_polyline5"
    return result


@app.post("/internal/v1/routes/recommend", dependencies=[Depends(_require_internal_token)])
def recommend(req: RecommendRouteRequest) -> Dict[str, Any]:
    did = req.dataset_id or dataset_repo.get_active_dataset_id()
    end_lat = req.destination.latitude if req.destination else req.origin.latitude
    end_lon = req.destination.longitude if req.destination else req.origin.longitude
    prayer_context = build_prayer_routing_context(
        req.prayer,
        req.origin.latitude,
        req.origin.longitude,
        req.departure_time,
    )
    result = routing_usecases.route_via_osm_dijkstra(
        start_lat=req.origin.latitude,
        start_lon=req.origin.longitude,
        end_lat=end_lat,
        end_lon=end_lon,
        algorithm=req.algorithm,
        current_time=prayer_context["departure_time"],
        prayer_time=prayer_context["target_prayer_time"],
        max_candidates=req.maximum_results,
        auto_build_osm=req.auto_build_osm,
        buffer_km=req.search_radius_km,
        dataset_id=did,
        profile=req.profile,
        cost_parameters=req.cost_parameters.model_dump(),
    )
    attach_prayer_context(result, prayer_context)
    if req.compact_response:
        result.pop("route_geojson", None)
        result["geometry_encoding"] = "google_polyline5"
    return result
