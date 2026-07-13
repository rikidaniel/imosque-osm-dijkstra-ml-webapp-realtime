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

<<<<<<< HEAD
app.include_router(router, prefix="/api/v1")
=======
def _friendly_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "overpass" in lowered or "httpsconnectionpool" in lowered or "max retries exceeded" in lowered or "timed out" in lowered:
        return (
            "Overpass/OSM sedang lambat atau tidak merespons, jadi graph Dijkstra lokal belum berhasil dibuat. "
            "Rute tetap bisa dicari dengan OSRM fallback. Coba lagi nanti, kecilkan Buffer OSM ke 1-2 km, "
            "atau pilih start-tujuan yang lebih dekat."
        )
    return message


def _profile_for(dataset_id: str) -> Dict[str, Any] | None:
    db_profile = local_db.get_dataset_profile(dataset_id)
    if db_profile:
        return db_profile
    paths = dataset_paths(dataset_id)
    if not paths["profile_json"].exists():
        return None
    with paths["profile_json"].open("r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    active = get_active_dataset_id()
    paths = dataset_paths(active)
    return {
        "status": "ok",
        "project": "AI/ML-Enriched OSM Dijkstra Routing for Safar Mode",
        "active_dataset_id": active,
        "active_dataset_file_exists": paths["raw_csv"].exists(),
        "sqlite_db_exists": local_db.DB_PATH.exists(),
        "sqlite_db_path": str(local_db.DB_PATH),
        "sqlite_dataset_ready": local_db.dataset_has_mosques(active),
        "enriched_json_exists": paths["enriched_json"].exists(),
        "active_dataset_json_path": str(paths["enriched_json"]),
        "osm_graph_cache_exists": DEFAULT_GRAPHML.exists(),
        "osm_graph_cache_path": str(DEFAULT_GRAPHML),
    }


@app.get("/api/datasets")
def datasets() -> Dict[str, Any]:
    return {
        "active_dataset_id": get_active_dataset_id(),
        "items": list_datasets(),
    }


@app.post("/api/datasets/active")
def set_active_dataset(dataset_id: str = Form(...)) -> Dict[str, Any]:
    did = slugify_dataset_name(dataset_id)
    paths = dataset_paths(did)
    if not paths["raw_csv"].exists():
        raise HTTPException(status_code=404, detail=f"Dataset tidak ditemukan: {did}")
    set_active_dataset_id(did)
    if not local_db.dataset_has_mosques(did) and not paths["enriched_json"].exists():
        enrich_dataset(did, make_active=True)
    elif not local_db.dataset_has_mosques(did):
        load_enriched_mosques(dataset_id=did)
    return {
        "status": "success",
        "active_dataset_id": did,
        "profile": _profile_for(did),
    }


@app.post("/api/datasets/upload")
async def upload_dataset(
    file: UploadFile = File(...),
    dataset_name: str | None = Form(None),
    process_now: bool = Form(True),
    make_active: bool = Form(True),
) -> Dict[str, Any]:
    filename = file.filename or "dataset.csv"
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Saat ini upload hanya mendukung file CSV.")
    content = await file.read()
    saved = save_uploaded_dataset(content, filename=filename, dataset_name=dataset_name, make_active=make_active)
    profile = None
    if process_now:
        profile = enrich_dataset(saved["dataset_id"], make_active=make_active)
    return {
        "status": "success",
        **saved,
        "processed": bool(process_now),
        "profile": profile,
    }


@app.post("/api/pipeline/run")
def run_pipeline(dataset_id: str | None = Query(None)) -> Dict[str, Any]:
    return enrich_dataset(dataset_id=dataset_id, make_active=False)


@app.get("/api/profile")
def profile(dataset_id: str | None = Query(None)) -> Dict[str, Any]:
    did = slugify_dataset_name(dataset_id or get_active_dataset_id() or DEFAULT_DATASET_ID)
    paths = dataset_paths(did)
    db_profile = local_db.get_dataset_profile(did)
    if db_profile:
        return JSONResponse(content=db_profile)
    if not paths["profile_json"].exists():
        enrich_dataset(did)
    with paths["profile_json"].open("r", encoding="utf-8") as f:
        return JSONResponse(content=json.load(f))


@app.get("/api/mosques")
def mosques(
    dataset_id: str | None = Query(None),
    limit: int = Query(1000, ge=1, le=30000),
    offset: int = Query(0, ge=0),
    kabko: str | None = None,
) -> Dict[str, Any]:
    did = slugify_dataset_name(dataset_id or get_active_dataset_id() or DEFAULT_DATASET_ID)
    if local_db.dataset_has_mosques(did):
        total = local_db.count_mosques(did, kabko=kabko)
        data = local_db.load_mosques(did, limit=limit, offset=offset, kabko=kabko)
    else:
        data = load_enriched_mosques(dataset_id=did)
        if kabko:
            data = [m for m in data if m.get("kabko", "").lower() == kabko.lower()]
        total = len(data)
        data = data[offset: offset + limit]
    return {
        "dataset_id": did,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": data,
    }


@app.get("/api/osm/status")
def osm_status() -> Dict[str, Any]:
    size_mb = round(DEFAULT_GRAPHML.stat().st_size / (1024 * 1024), 2) if DEFAULT_GRAPHML.exists() else 0
    cache_meta = local_db.get_osm_graph_cache()
    return {
        "cache_exists": DEFAULT_GRAPHML.exists(),
        "cache_path": str(DEFAULT_GRAPHML),
        "size_mb": size_mb,
        "metadata": cache_meta,
        "note": (
            "Jika cache belum ada atau kamu ganti wilayah dataset, jalankan /api/osm/build-route "
            "atau centang auto-build OSM pada frontend. OSM data diambil dari OpenStreetMap melalui OSMnx."
        ),
    }


@app.post("/api/nearest-mosques")
def nearest_mosques(req: NearestMosquesRequest) -> Dict[str, Any]:
    did = slugify_dataset_name(req.dataset_id or get_active_dataset_id() or DEFAULT_DATASET_ID)
    radius_deg_lat = req.radius_km / 111.0
    radius_deg_lon = req.radius_km / (111.0 * max(abs(math.cos(math.radians(req.latitude))), 0.2))
    bounds = (
        req.latitude - radius_deg_lat,
        req.latitude + radius_deg_lat,
        req.longitude - radius_deg_lon,
        req.longitude + radius_deg_lon,
    )
    data = local_db.load_mosques(did, bounds=bounds) if local_db.dataset_has_mosques(did) else load_enriched_mosques(dataset_id=did)
    scored = []
    for item in data:
        distance_km = haversine_km(req.latitude, req.longitude, float(item["latitude"]), float(item["longitude"]))
        if distance_km <= req.radius_km:
            enriched = dict(item)
            enriched["distance_km"] = round(distance_km, 3)
            scored.append(enriched)
    scored.sort(key=lambda m: (m["distance_km"], -float(m.get("priority_score", 0.0))))
    return {
        "dataset_id": did,
        "origin": {"latitude": req.latitude, "longitude": req.longitude},
        "radius_km": req.radius_km,
        "total": len(scored),
        "items": scored[: req.limit],
    }


@app.post("/api/route/to-mosque")
def route_selected_mosque(req: RouteToMosqueRequest) -> Dict[str, Any]:
    did = slugify_dataset_name(req.dataset_id or get_active_dataset_id() or DEFAULT_DATASET_ID)
    mosque = local_db.get_mosque(did, req.mosque_id)
    if mosque is None:
        data = load_enriched_mosques(dataset_id=did)
        mosque = next((m for m in data if str(m.get("id")) == req.mosque_id), None)
    if mosque is None:
        raise HTTPException(status_code=404, detail=f"Masjid tidak ditemukan pada dataset {did}: {req.mosque_id}")
    try:
        return route_to_mosque(
            start_lat=req.start_lat,
            start_lon=req.start_lon,
            mosque=mosque,
            algorithm=req.algorithm,
            auto_build_osm=req.auto_build_osm,
            buffer_km=req.buffer_km,
            dataset_id=did,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))


@app.post("/api/osm/build-bbox")
def build_osm_bbox(req: BuildOsmRequest) -> Dict[str, Any]:
    try:
        G = build_osm_graph_for_bbox(
            north=req.north,
            south=req.south,
            east=req.east,
            west=req.west,
            network_type=req.network_type,
            output_graphml=DEFAULT_GRAPHML,
        )
        local_db.save_osm_graph_cache(
            graphml_path=DEFAULT_GRAPHML,
            bounds=graph_bounds(G),
            buffer_km=None,
            network_type=req.network_type,
            nodes=len(G.nodes),
            edges=len(G.edges),
        )
        return {
            "status": "success",
            "cache_path": str(DEFAULT_GRAPHML),
            "nodes": len(G.nodes),
            "edges": len(G.edges),
            "network_type": req.network_type,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))


@app.post("/api/osm/build-route")
def build_osm_route(req: BuildOsmRouteRequest) -> Dict[str, Any]:
    try:
        G = build_osm_graph_for_route(
            start_lat=req.start_lat,
            start_lon=req.start_lon,
            end_lat=req.end_lat,
            end_lon=req.end_lon,
            buffer_km=req.buffer_km,
            network_type=req.network_type,
            output_graphml=DEFAULT_GRAPHML,
        )
        local_db.save_osm_graph_cache(
            graphml_path=DEFAULT_GRAPHML,
            bounds=graph_bounds(G),
            buffer_km=req.buffer_km,
            network_type=req.network_type,
            nodes=len(G.nodes),
            edges=len(G.edges),
        )
        return {
            "status": "success",
            "cache_path": str(DEFAULT_GRAPHML),
            "nodes": len(G.nodes),
            "edges": len(G.edges),
            "buffer_km": req.buffer_km,
            "network_type": req.network_type,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))


@app.post("/api/route")
def route(req: RouteRequest) -> Dict[str, Any]:
    did = slugify_dataset_name(req.dataset_id or get_active_dataset_id() or DEFAULT_DATASET_ID)
    try:
        return route_via_osm_dijkstra(
            start_lat=req.start_lat,
            start_lon=req.start_lon,
            end_lat=req.end_lat,
            end_lon=req.end_lon,
            algorithm=req.algorithm,
            current_time=req.current_time,
            prayer_time=req.prayer_time,
            max_candidates=req.max_candidates,
            auto_build_osm=req.auto_build_osm,
            buffer_km=req.buffer_km,
            dataset_id=did,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=424,
            detail=(
                str(exc)
                + " Solusi: klik tombol Build OSM Graph di frontend atau centang Auto-build OSM Graph."
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error(exc))
>>>>>>> 096c8ae6ace9c26a27b3adf04c8b2efbc3694a5a
