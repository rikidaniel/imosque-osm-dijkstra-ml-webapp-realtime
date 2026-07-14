# API Documentation - iMosque SafarRoute

Seluruh endpoint memakai prefiks `/api/v1`.

Dokumentasi ini disesuaikan dengan implementasi backend saat ini di FastAPI + ArangoDB + OSMnx.

## Ringkasan Teknologi

- Backend: FastAPI
- Database: ArangoDB
- Routing graph: OpenStreetMap via OSMnx + NetworkX
- Peta frontend: Leaflet
- Transfer data ringan: GZip response, compact response, encoded polyline
- Perhitungan waktu salat: kalkulasi offline lokal agar tidak bergantung pada API eksternal

## Endpoint Utama

### 1. Health Check

`GET /api/v1/health`

Contoh respons:

```json
{
  "status": "healthy",
  "graph_status": "connected",
  "version": "1.0.0",
  "active_dataset_id": "dki_jakarta"
}
```

### 2. Daftar Dataset

`GET /api/v1/datasets`

Mengembalikan daftar dataset yang tersimpan dan dataset aktif.

### 3. Upload Dataset

`POST /api/v1/datasets/upload`

Content type: `multipart/form-data`

Field:

- `file` = file CSV
- `dataset_name` = nama dataset opsional
- `make_active` = `true` atau `false`

### 4. Set Dataset Aktif

`POST /api/v1/datasets/active`

Form field:

- `dataset_id`

### 5. Jalankan Pipeline

`POST /api/v1/pipeline/run?dataset_id=<id>`

Memproses dataset aktif atau dataset yang disebutkan secara eksplisit.

### 6. Profil Dataset

`GET /api/v1/profile?dataset_id=<id>`

### 7. Data Masjid

`GET /api/v1/mosques?dataset_id=<id>&limit=1000&offset=0&kabko=<nama>`

### 8. Bounding Box Dataset

`GET /api/v1/datasets/{dataset_id}/bbox`

### 9. Status OSM Cache

`GET /api/v1/osm/status?dataset_id=<id>`

### 10. Cari Masjid Terdekat

`POST /api/v1/nearest-mosques`

Request body:

```json
{
  "latitude": -6.2,
  "longitude": 106.8,
  "dataset_id": "dki_jakarta",
  "limit": 6,
  "radius_km": 10
}
```

### 11. Waktu Salat Offline

`GET /api/v1/prayer-times?latitude=-6.2&longitude=106.8&date=2026-07-14`

Respons ini memakai kalkulasi lokal, jadi ringan untuk jaringan lambat dan tidak memerlukan API pihak ketiga.

### 12. Rute ke Masjid Tertentu

`POST /api/v1/route/to-mosque`

Request body:

```json
{
  "start_lat": -6.2,
  "start_lon": 106.8,
  "mosque_id": "mosque_123",
  "dataset_id": "dki_jakarta",
  "algorithm": "astar",
  "auto_build_osm": false,
  "buffer_km": 6,
  "compact_response": true
}
```

`compact_response=true` akan mengirim polyline ter-encode dan tidak menduplikasi GeoJSON agar payload lebih kecil.

### 13. Rute Rekomendasi

`POST /api/v1/routes/recommend`

Request body:

```json
{
  "origin": { "latitude": -6.2, "longitude": 106.8 },
  "destination": { "latitude": -6.25, "longitude": 106.9 },
  "departure_time": "2026-07-14T17:10:00+07:00",
  "prayer": "maghrib",
  "algorithm": "astar",
  "profile": "balanced",
  "search_radius_km": 10,
  "maximum_results": 3,
  "auto_build_osm": false,
  "dataset_id": "dki_jakarta",
  "compact_response": true
}
```

### 14. Benchmark Routing

`POST /api/v1/routes/benchmark`

Request body:

```json
{
  "origin": { "latitude": -6.2, "longitude": 106.8 },
  "destination": { "latitude": -6.25, "longitude": 106.9 },
  "departure_time": "2026-07-14T17:10:00+07:00",
  "prayer": "maghrib",
  "profile": "balanced",
  "search_radius_km": 10,
  "dataset_id": "dki_jakarta"
}
```

### 15. Profil Routing

`GET /api/v1/routing-profiles`

### 16. Build OSM Graph

`POST /api/v1/osm/build-bbox`

Request body:

```json
{
  "north": -6.1,
  "south": -6.3,
  "east": 106.9,
  "west": 106.7,
  "network_type": "drive",
  "dataset_id": "dki_jakarta"
}
```

### 17. Build OSM Graph dari Start-End

`POST /api/v1/osm/build-route`

### 18. Build OSM Semua Dataset

`POST /api/v1/osm/build-all`

### 19. Status Build Semua Dataset

`GET /api/v1/osm/build-all/status`

### 20. Batalkan Build Semua Dataset

`POST /api/v1/osm/build-all/cancel`

### 21. Routes Tersimpan

`GET /api/v1/routes/{route_id}`

## User Settings

Endpoint ini menyimpan konfigurasi frontend ke database.

### Simpan Settings

`POST /api/v1/user-settings`

Contoh payload:

```json
{
  "user_id": "device_abc123",
  "search_settings": {
    "algorithm": "dijkstra",
    "profile": "balanced",
    "currentTime": "17:00",
    "prayer": "maghrib",
    "maxCandidates": "3",
    "bufferKm": "15",
    "autoBuild": false
  },
  "prayer_settings": {
    "schedule": [
      { "name": "Subuh", "time": "04:42", "isAlarmActive": true }
    ],
    "hijriDate": "17 Ramadan 1435 H",
    "masehiDate": "14 July 2014"
  },
  "updated_at": "2026-07-14T17:00:00Z"
}
```

### Ambil Settings

`GET /api/v1/user-settings/{user_id}`

### Hapus Settings

`DELETE /api/v1/user-settings/{user_id}`

## Skema Request Penting

### `RouteRequest`

- `start_lat`, `start_lon`, `end_lat`, `end_lon`
- `algorithm`: `dijkstra` atau `astar`
- `dataset_id` opsional
- `current_time` format `HH:MM`
- `prayer_time` format `HH:MM`
- `max_candidates` default 6
- `auto_build_osm` default `false`
- `buffer_km` default 6.0

### `RecommendRouteRequest`

- `origin`
- `destination` opsional
- `departure_time`
- `prayer`
- `algorithm`
- `profile`
- `search_radius_km`
- `maximum_results`
- `auto_build_osm`
- `dataset_id`
- `compact_response`

## Catatan Performa

Beberapa optimasi yang sengaja dipakai agar aplikasi lebih cepat:

- Graph OSM disimpan sebagai cache GraphML per dataset.
- Graph dibaca lewat cache in-memory agar parse GraphML tidak berulang.
- Index nearest-node dibangun sekali lalu dipakai ulang.
- Dijkstra berhenti setelah kandidat yang dibutuhkan ditemukan, bukan menghitung seluruh graph.
- A* dipakai untuk target tertentu ketika cocok.
- Result routing dicache sementara di backend.
- Response API dipadatkan dengan encoded polyline dan GZip.
- Service worker frontend membantu cache tile peta dan aset statis.

## Contoh Response Routing

```json
{
  "algorithm": "Dijkstra (Multi-Destination)",
  "dataset_id": "dki_jakarta",
  "routing_mode": "local_graph",
  "road_network": "OpenStreetMap via OSMnx/NetworkX",
  "candidate_count": 3,
  "execution_time_ms": 18.42,
  "recommended_mosque": {
    "id": "mosque_123",
    "name": "Masjid Contoh",
    "latitude": -6.2,
    "longitude": 106.8,
    "tier": "B",
    "capacity_proxy": "medium"
  },
  "encoded_polyline": "}_ilF...",
  "route_summary": {
    "distance_km": 1.42,
    "estimated_time_minutes": 4.8,
    "arrival_to_mosque_minutes": 4.2,
    "arrival_status": "before_prayer",
    "minutes_before_prayer": 12.5,
    "multi_objective_score": 0.154,
    "route_nodes_count": 38,
    "reason": "Rute dipilih pada graph jalan OpenStreetMap..."
  }
}
```

