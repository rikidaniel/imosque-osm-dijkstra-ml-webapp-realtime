import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.interfaces.api.routes import router, routing_usecases
from app.infrastructure.database.arangodb_client import init_db


def _preload_active_road_graph() -> None:
    """Warm the expensive GraphML parse outside the first route request."""
    try:
        from app.infrastructure.database.arangodb_repo import ArangoDatasetRepository
        from app.infrastructure.services.osm_graph import get_graphml_path, start_road_graph_prewarm

        dataset_id = ArangoDatasetRepository().get_active_dataset_id()
        graph_path = get_graphml_path(dataset_id)
        if start_road_graph_prewarm(graph_path):
            print(f"Road graph preload started: {graph_path.name}")
        if routing_usecases.start_mosque_candidate_prewarm(dataset_id):
            print(f"Mosque candidate preload started: {dataset_id}")
    except Exception as exc:
        # Startup must stay available even when a cache is missing or malformed.
        print(f"Road graph preload skipped: {exc}")


def startup_event() -> None:
    """Initialize durable services and start non-blocking route prewarming."""
    init_db()
    # Prewarm runs on a daemon thread, so health and nearest-mosque APIs remain
    # available while route requests temporarily use their configured fallback.
    # Operators with very tight startup CPU/RAM limits can explicitly opt out.
    if os.getenv("IMOSQUE_PREWARM_GRAPH_ON_STARTUP", "true").lower() in {"1", "true", "yes"}:
        _preload_active_road_graph()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup_event()
    yield

app = FastAPI(
    title="iMosque ArangoDB Web API (Clean Architecture)",
    description=(
        "Backend untuk upload/switch dataset real-time ke ArangoDB, AI/ML-enriched mosque dataset, "
        "dan Dijkstra routing pada road network OpenStreetMap."
    ),
    version="4.0.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
