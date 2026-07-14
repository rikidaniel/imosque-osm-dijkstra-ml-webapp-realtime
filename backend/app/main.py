import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.interfaces.api.routes import router
from app.infrastructure.database.arangodb_client import init_db


def _preload_active_road_graph() -> None:
    """Warm the expensive GraphML parse outside the first route request."""
    try:
        from app.infrastructure.database.arangodb_repo import ArangoDatasetRepository
        from app.infrastructure.services.osm_graph import get_graphml_path, load_road_graph, warm_road_graph_indexes

        dataset_id = ArangoDatasetRepository().get_active_dataset_id()
        graph_path = get_graphml_path(dataset_id)
        if graph_path.exists():
            graph = load_road_graph(graph_path)
            warm_road_graph_indexes(graph)
            print(f"Road graph and spatial index preloaded: {graph_path.name}")
    except Exception as exc:
        # Startup must stay available even when a cache is missing or malformed.
        print(f"Road graph preload skipped: {exc}")

app = FastAPI(
    title="iMosque ArangoDB Web API (Clean Architecture)",
    description=(
        "Backend untuk upload/switch dataset real-time ke ArangoDB, AI/ML-enriched mosque dataset, "
        "dan Dijkstra routing pada road network OpenStreetMap."
    ),
    version="4.0.0",
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    # Inisialisasi database (membuat database & koleksi jika belum ada)
    init_db()
    threading.Thread(
        target=_preload_active_road_graph,
        name="imosque-road-graph-preload",
        daemon=True,
    ).start()

app.include_router(router, prefix="/api/v1")
