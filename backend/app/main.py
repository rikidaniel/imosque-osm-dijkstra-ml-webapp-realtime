from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.interfaces.api.routes import router
from app.infrastructure.database.arangodb_client import init_db

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

app.include_router(router, prefix="/api/v1")
