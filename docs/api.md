# Dokumentasi API iMosque SafarRoute

Dokumentasi ini mengikuti implementasi FastAPI pada `backend/app/interfaces/api/routes.py` dan model Pydantic pada `backend/app/domain/models/schemas.py`.

## Daftar isi

- [Informasi umum](#informasi-umum)
- [Konvensi](#konvensi)
- [Ringkasan endpoint](#ringkasan-endpoint)
- [Sistem](#sistem)
- [Dataset dan pipeline](#dataset-dan-pipeline)
- [Masjid](#masjid)
- [Pencarian dan waktu salat](#pencarian-dan-waktu-salat)
- [Routing](#routing)
- [OSM graph dan cache](#osm-graph-dan-cache)
- [User settings](#user-settings)
- [Catatan operasional](#catatan-operasional)
- [Contoh alur end-to-end](#contoh-alur-end-to-end)

## Informasi umum

| Item | Nilai |
|---|---|
| Base URL lokal | `http://127.0.0.1:8000/api/v1` |
| Format utama | `application/json` |
| Upload/form | `multipart/form-data` |
| Autentikasi | Belum tersedia |
| Versi aplikasi FastAPI | `4.0.0` |
| Swagger UI | `http://127.0.0.1:8000/docs` |
| ReDoc | `http://127.0.0.1:8000/redoc` |
| OpenAPI JSON | `http://127.0.0.1:8000/openapi.json` |

Semua contoh menggunakan base URL lokal. Respons besar dapat dikompresi GZip. CORS saat ini mengizinkan seluruh origin.

API tidak memakai cookie/session dan belum menyediakan API key, OAuth, rate-limit header, request ID, atau idempotency key. Jangan membuka endpoint mutasi langsung ke internet tanpa lapisan keamanan tambahan.

## Konvensi

### Dataset aktif

Endpoint yang menerima `dataset_id` opsional menggunakan dataset aktif jika ID tidak dikirim. Pengecualian: `POST /nearest-mosques` memperlakukan ID kosong sebagai `all` untuk pencarian lintas dataset.

Nama dataset dinormalisasi menjadi slug huruf kecil, misalnya `DKI Jakarta` menjadi `dki_jakarta`.

### Versi dan kompatibilitas

- Prefix kontrak saat ini adalah `/api/v1` dan versi aplikasi adalah `4.0.0`.
- Request body tervalidasi oleh Pydantic; field tambahan mengikuti perilaku default Pydantic dan dapat diabaikan.
- Mayoritas endpoint belum menetapkan response model eksplisit di decorator FastAPI. Client sebaiknya membaca field yang dibutuhkan dan toleran terhadap field respons tambahan.
- Endpoint `GET /routes/{route_id}` masih berupa contoh/stub, bukan kontrak penyimpanan rute persisten.
- Untuk kontrak mesin yang paling aktual, gunakan OpenAPI runtime dari `/openapi.json`.

Ekspor OpenAPI ketika backend aktif:

```bash
curl http://127.0.0.1:8000/openapi.json -o openapi.json
```

Memeriksa jumlah path dari source tanpa menjalankan server/lifespan database:

```bash
cd backend
python -c "from app.main import app; print(len(app.openapi()['paths']))"
```

Implementasi saat dokumentasi ini diaudit mempunyai 29 path OpenAPI.

### Waktu dan koordinat

- Tanggal kalender: `YYYY-MM-DD`.
- Waktu sederhana: `HH:MM` dalam format 24 jam.
- `departure_time`: disarankan ISO 8601, misalnya `2026-07-15T17:10:00+07:00`.
- Latitude/longitude memakai WGS84 dalam derajat desimal.
- Urutan GeoJSON adalah `[longitude, latitude]`.
- Encoded geometry memakai Google Polyline precision 5 (`google_polyline5`).

### Pagination dan filter

- `GET /mosques` memakai pagination berbasis `limit` dan `offset`, bukan cursor.
- Pencarian nama pada dashboard saat ini dilakukan di frontend terhadap halaman data yang sudah dimuat; API `GET /mosques` tidak mempunyai query pencarian nama.
- Filter `kabko` diterapkan di backend.
- Endpoint nearest memakai `limit` maksimal 50 dan radius maksimal 200 km untuk membatasi query serta payload.

### Format error

Error yang dibuat aplikasi umumnya berbentuk:

```json
{
  "detail": "Pesan kesalahan"
}
```

Error validasi FastAPI/Pydantic menggunakan HTTP 422:

```json
{
  "detail": [
    {
      "type": "greater_than_equal",
      "loc": ["body", "search_radius_km"],
      "msg": "Input should be greater than or equal to 1",
      "input": 0,
      "ctx": {"ge": 1}
    }
  ]
}
```

Status yang umum:

| Status | Arti |
|---|---|
| 200 | Berhasil; termasuk beberapa operasi asinkron dan hasil `not_found` settings |
| 400 | Input/kondisi dataset tidak valid |
| 404 | Resource tidak ditemukan |
| 409 | Konflik karena build graph sedang berjalan |
| 422 | Request tidak lolos validasi |
| 500 | Kegagalan database, routing, pipeline, atau layanan OSM |

> Implementasi belum mendeklarasikan response schema khusus per endpoint. Field respons yang berasal dari data/pipeline dapat bertambah tanpa perubahan kontrak request.

## Ringkasan endpoint

| Grup | Method | Path |
|---|---|---|
| Sistem | GET | `/health` |
| Dataset | GET | `/datasets` |
| Dataset | POST | `/datasets/active` |
| Dataset | POST | `/datasets/upload` |
| Dataset | POST | `/pipeline/run` |
| Dataset | GET | `/datasets/status/{dataset_id}` |
| Dataset | GET | `/profile` |
| Dataset | GET | `/datasets/{dataset_id}/bbox` |
| Dataset | DELETE | `/datasets/{dataset_id}` |
| Masjid | GET | `/mosques` |
| Masjid | POST | `/mosques/{dataset_id}` |
| Masjid | PUT | `/mosques/{dataset_id}/{mosque_id}` |
| Masjid | DELETE | `/mosques/{dataset_id}/{mosque_id}` |
| Masjid | POST | `/mosques/bulk-delete` |
| Pencarian | POST | `/nearest-mosques` |
| Salat | GET | `/prayer-times` |
| Routing | POST | `/route` |
| Routing | POST | `/route/to-mosque` |
| Routing | POST | `/routes/recommend` |
| Routing | POST | `/routes/benchmark` |
| Routing | GET | `/routes/{route_id}` |
| Routing | GET | `/routing-profiles` |
| OSM | GET | `/osm/status` |
| OSM | POST | `/osm/build-bbox` |
| OSM | POST | `/osm/build-route` |
| OSM | POST | `/osm/build-all` |
| OSM | GET | `/osm/build-all/status` |
| OSM | POST | `/osm/build-all/cancel` |
| Settings | POST | `/user-settings` |
| Settings | GET | `/user-settings/{user_id}` |
| Settings | DELETE | `/user-settings/{user_id}` |

## Sistem

### GET `/health`

Memeriksa backend, keberadaan graph default, dan dataset aktif.

```bash
curl http://127.0.0.1:8000/api/v1/health
```

Respons 200:

```json
{
  "status": "healthy",
  "graph_status": "ready",
  "graph_ready": true,
  "graph_runtime": {
    "status": "ready",
    "ready": true,
    "cache_exists": true,
    "runtime_cache_exists": true,
    "source": "runtime_binary",
    "load_time_ms": 842.31,
    "nodes": 12000,
    "edges": 28000
  },
  "version": "4.0.0",
  "active_dataset_id": "dki_jakarta"
}
```

`graph_status` dapat bernilai `not_configured`, `available`, `loading`, `ready`, atau `error`. `available` berarti GraphML ada tetapi belum dimuat ke memori; `graph_ready: true` berarti graph siap melayani routing lokal. Nilai ini bukan pemeriksaan koneksi ArangoDB secara langsung.

## Dataset dan pipeline

### GET `/datasets`

Mengambil dataset yang tersimpan beserta dataset aktif.

Respons 200 (contoh ringkas):

```json
{
  "active_dataset_id": "dki_jakarta",
  "items": [
    {
      "_key": "dki_jakarta",
      "dataset_id": "dki_jakarta",
      "dataset_label": "Dki Jakarta",
      "processed": true,
      "processing_status": "completed",
      "progress_percent": 100,
      "mosque_count": 1200,
      "data_revision": 7,
      "is_active": true
    }
  ]
}
```

### POST `/datasets/active`

Memilih dataset aktif. Body harus berupa form, bukan JSON.

| Field form | Tipe | Wajib | Keterangan |
|---|---:|---:|---|
| `dataset_id` | string | Ya | ID dataset yang akan diaktifkan |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/datasets/active \
  -F "dataset_id=dki_jakarta"
```

Respons 200:

```json
{
  "status": "success",
  "active_dataset_id": "dki_jakarta",
  "graph_prewarm_started": true,
  "mosque_prewarm_started": true,
  "graph_runtime": {
    "status": "loading",
    "ready": false,
    "cache_exists": true,
    "runtime_cache_exists": true
  },
  "profile": {
    "dataset_id": "dki_jakarta",
    "processed": true,
    "data_revision": 7
  }
}
```

Pemilihan dataset memulai prewarm graph dan snapshot kandidat masjid di background. Catatan: route saat ini tidak secara eksplisit menolak ID yang belum ada; `profile` dapat bernilai `null` tergantung repository.

### POST `/datasets/upload`

Mengunggah CSV dan memulai pipeline asinkron.

Content-Type: `multipart/form-data`.

| Field | Tipe | Wajib | Default | Keterangan |
|---|---:|---:|---:|---|
| `file` | file | Ya | - | Harus berekstensi `.csv` |
| `dataset_name` | string | Tidak | nama file | Nama/label yang diubah menjadi slug |
| `make_active` | boolean | Tidak | `true` | Aktifkan setelah pipeline berhasil |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/datasets/upload \
  -F "file=@data/raw/dataset_masjid_banten.csv" \
  -F "dataset_name=banten" \
  -F "make_active=true"
```

Respons 200:

```json
{
  "dataset_id": "banten",
  "filename": "dataset_masjid_banten.csv",
  "processed": false,
  "processing_status": "processing",
  "progress_percent": 10,
  "message": "Pemrosesan asinkron dimulai di latar belakang."
}
```

Setelah respons diterima, polling endpoint status. Pipeline membaca CSV, membersihkan/enrich data, menyimpan masjid ke ArangoDB, dan menginvalidasi graph dataset lama.

Error khusus:

- 400: file bukan CSV.
- 409: upload ditunda karena build graph sedang berlangsung.
- 500: gagal membaca/menjadwalkan upload.

Kegagalan di background tidak mengubah respons awal; status dataset berubah menjadi `failed` dan penyebab masuk ke `message`.

### POST `/pipeline/run`

Menjalankan pipeline dari file CSV yang sudah ada di disk.

Query:

| Parameter | Tipe | Wajib | Keterangan |
|---|---:|---:|---|
| `dataset_id` | string | Tidak | Default dataset aktif |

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/pipeline/run?dataset_id=banten"
```

Backend mencari `data/raw/datasets/{dataset_id}.csv`, lalu fallback ke `data/raw/dataset_masjid_{dataset_id}.csv`. Endpoint ini menjalankan pipeline sinkron dan dapat memerlukan waktu.

Respons 200 berisi `dataset_id`, `filename`, `processed`, `is_active`, dan `profile`. Error 500 bila file atau pipeline gagal.

### GET `/datasets/status/{dataset_id}`

Mengambil progres pipeline.

```bash
curl http://127.0.0.1:8000/api/v1/datasets/status/banten
```

Respons 200:

```json
{
  "dataset_id": "banten",
  "processed": true,
  "processing_status": "completed",
  "progress_percent": 100,
  "message": "Selesai!"
}
```

Nilai `processing_status`: biasanya `processing`, `completed`, atau `failed`. Error 404 bila dataset tidak ditemukan.

### GET `/profile`

Mengambil metadata/profil enrichment dataset.

Query: `dataset_id` opsional; default dataset aktif.

```bash
curl "http://127.0.0.1:8000/api/v1/profile?dataset_id=banten"
```

Respons 200 adalah dokumen profile dataset. Error 404 bila profile tidak ditemukan.

### GET `/datasets/{dataset_id}/bbox`

Menghitung bounding box robust dari koordinat masjid yang valid. Outlier IQR dibuang. Area yang terlalu luas disesuaikan ke area prioritas sekitar median.

Respons 200:

```json
{
  "dataset_id": "dki_jakarta",
  "total_rows": 1200,
  "valid_rows": 1198,
  "used_rows": 1180,
  "ignored_outliers": 18,
  "adjusted_to_area_limit": false,
  "raw_area_km2": 640.25,
  "bbox": {
    "north": -6.08,
    "south": -6.38,
    "east": 106.98,
    "west": 106.68
  }
}
```

Error 400 bila dataset kosong atau tidak mempunyai koordinat valid.

### DELETE `/datasets/{dataset_id}`

Menghapus masjid, metadata dataset, CSV raw jika sesuai nama slug, serta cache graph dataset. Jika dataset aktif dihapus, backend memilih dataset berikutnya atau fallback `banten`.

Respons 200:

```json
{
  "status": "success",
  "message": "Dataset dki_jakarta berhasil dihapus."
}
```

Error: 404 bila tidak ditemukan, 409 bila build graph sedang berjalan.

## Masjid

### GET `/mosques`

Mengambil masjid dengan pagination offset.

| Query | Tipe | Default | Validasi |
|---|---:|---:|---|
| `dataset_id` | string | dataset aktif | Opsional |
| `limit` | integer | 1000 | 1-30000 |
| `offset` | integer | 0 | >= 0 |
| `kabko` | string | - | Filter kota/kabupaten |

```bash
curl "http://127.0.0.1:8000/api/v1/mosques?dataset_id=dki_jakarta&limit=100&offset=0&kabko=Jakarta%20Pusat"
```

Respons 200:

```json
{
  "dataset_id": "dki_jakarta",
  "total": 250,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "id": "mosque_123",
      "name": "Masjid Contoh",
      "latitude": -6.2,
      "longitude": 106.8,
      "kabko": "Jakarta Pusat"
    }
  ]
}
```

### POST `/mosques/{dataset_id}`

Menambah masjid ke dataset.

| Field JSON | Tipe | Wajib |
|---|---:|---:|
| `name` | string | Ya |
| `latitude` | number | Ya |
| `longitude` | number | Ya |
| `kecamatan` | string/null | Tidak |
| `kabko` | string/null | Tidak |
| `provinsi` | string/null | Tidak |
| `kelurahan` | string/null | Tidak |
| `address` | string/null | Tidak |
| `fasilitas` | string/null | Tidak |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/mosques/dki_jakarta \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Masjid Contoh",
    "latitude": -6.2,
    "longitude": 106.8,
    "provinsi": "DKI Jakarta",
    "fasilitas": "parkir, toilet, tempat wudhu"
  }'
```

Respons 200:

```json
{
  "status": "success",
  "mosque_id": "generated-id",
  "message": "Masjid berhasil ditambahkan."
}
```

`provinsi` disinkronkan dengan `province`; string `fasilitas` dipecah menjadi array `facilities`. Error 500 bila penyimpanan gagal.

### PUT `/mosques/{dataset_id}/{mosque_id}`

Memperbarui sebagian atau seluruh field yang sama dengan create. Semua field bersifat opsional.

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/mosques/dki_jakarta/mosque_123 \
  -H "Content-Type: application/json" \
  -d '{"name":"Nama Baru","fasilitas":"parkir, AC"}'
```

Respons 200:

```json
{
  "status": "success",
  "message": "Masjid mosque_123 berhasil diperbarui."
}
```

Error 404 bila tidak ditemukan, 500 bila update gagal.

### DELETE `/mosques/{dataset_id}/{mosque_id}`

Menghapus satu masjid.

Respons 200:

```json
{
  "status": "success",
  "message": "Masjid mosque_123 berhasil dihapus."
}
```

Error 404 bila masjid tidak ditemukan atau tidak dapat dihapus.

### POST `/mosques/bulk-delete`

Menghapus banyak masjid.

```json
{
  "dataset_id": "dki_jakarta",
  "mosque_ids": ["mosque_1", "mosque_2"]
}
```

Respons 200:

```json
{
  "status": "success",
  "message": "2 masjid berhasil dihapus."
}
```

Error 400 bila operasi repository gagal. Array kosong belum mempunyai batas minimum pada schema saat ini.

## Pencarian dan waktu salat

### POST `/nearest-mosques`

Mencari masjid terdekat dengan satu geo query pada radius maksimum yang diminta. Request identik yang berlangsung bersamaan digabung agar database tidak menjalankan pekerjaan yang sama berulang kali.

| Field | Tipe | Wajib | Default/validasi |
|---|---:|---:|---|
| `latitude` | number | Ya | -90 sampai 90 |
| `longitude` | number | Ya | -180 sampai 180 |
| `dataset_id` | string/null | Tidak | Kosong/`all` = lintas dataset |
| `limit` | integer | Tidak | 6; 1-50 |
| `radius_km` | number | Tidak | 10; 0.5-200 |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/nearest-mosques \
  -H "Content-Type: application/json" \
  -d '{
    "latitude": -6.2,
    "longitude": 106.8,
    "dataset_id": "all",
    "limit": 6,
    "radius_km": 10
  }'
```

Respons 200:

```json
{
  "dataset_id": "all",
  "origin": {"latitude": -6.2, "longitude": 106.8},
  "radius_km": 10,
  "search_radius_used_km": 10,
  "total": 2,
  "items": [
    {
      "id": "mosque_123",
      "name": "Masjid Contoh",
      "distance_km": 1.42
    }
  ],
  "cache_hit": false
}
```

Hasil dicache sekitar 30 detik; request sama dapat mengembalikan `cache_hit: true`. Cache dibatalkan ketika data masjid berubah. Setiap mutasi menaikkan `data_revision` dataset agar cache rekomendasi rute lama tidak digunakan kembali.

### GET `/prayer-times`

Menghitung waktu salat secara offline.

| Query | Tipe | Wajib | Validasi |
|---|---:|---:|---|
| `latitude` | number | Ya | -90 sampai 90 |
| `longitude` | number | Ya | -180 sampai 180 |
| `date` | string | Tidak | `YYYY-MM-DD`; default hari server |

```bash
curl "http://127.0.0.1:8000/api/v1/prayer-times?latitude=-6.2&longitude=106.8&date=2026-07-15"
```

Respons 200:

```json
{
  "source": "offline_kemenag_calculation",
  "date": "2026-07-15",
  "timezone": "Asia/Jakarta",
  "timings": {
    "Fajr": "04:42",
    "Dhuhr": "12:01",
    "Asr": "15:22",
    "Maghrib": "17:55",
    "Isha": "19:08"
  }
}
```

Zona waktu dipilih dari longitude: WIT mulai 126, WITA mulai 110, selain itu WIB. Error 422 bila tanggal/koordinat tidak valid.

## Routing

### Respons routing umum

Struktur aktual dapat berbeda menurut mode graph lokal/fallback. Contoh ringkas:

```json
{
  "algorithm": "A*",
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
    "tier": "B"
  },
  "encoded_polyline": "}_ilF...",
  "geometry_encoding": "google_polyline5",
  "route_summary": {
    "distance_km": 1.42,
    "estimated_time_minutes": 4.8,
    "arrival_status": "before_prayer",
    "minutes_before_prayer": 12.5,
    "multi_objective_score": 0.154,
    "route_nodes_count": 38,
    "geometry_points_count": 24,
    "geometry_original_points_count": 91
  },
  "timings_ms": {
    "mosque_query": 4.2,
    "candidate_ranking_paths": 12.8,
    "geometry": 0.6,
    "total": 18.42
  },
  "pathfinding": {
    "candidate_ranking_algorithm": "dijkstra_multi_target",
    "final_path_algorithm": "astar"
  },
  "cache_hit": false
}
```

Jika `compact_response` false, respons dapat menyertakan `route_geojson`. Encoded polyline dan GeoJSON memakai geometri hasil simplifikasi yang sama. Hasil rekomendasi dicache sekitar lima menit berdasarkan dataset, `data_revision`, versi graph, koordinat, algoritma, waktu, kandidat, buffer, dan profil.

### POST `/route`

Endpoint routing level rendah/kompatibilitas yang mencari kandidat masjid dan rute antara start-end.

| Field | Tipe | Wajib | Default/validasi |
|---|---:|---:|---|
| `start_lat`, `start_lon` | number | Ya | - |
| `end_lat`, `end_lon` | number | Ya | - |
| `algorithm` | enum | Tidak | `astar`; `dijkstra`/`astar` |
| `dataset_id` | string/null | Tidak | dataset aktif |
| `current_time` | string/null | Tidak | Disarankan `HH:MM` |
| `prayer_time` | string/null | Tidak | Disarankan `HH:MM` |
| `max_candidates` | integer | Tidak | 6; 1-20 |
| `auto_build_osm` | boolean | Tidak | false |
| `buffer_km` | number | Tidak | 6; 1-200 |

```json
{
  "dataset_id": "dki_jakarta",
  "start_lat": -6.2001,
  "start_lon": 106.8166,
  "end_lat": -6.2501,
  "end_lon": 106.9002,
  "algorithm": "astar",
  "current_time": "17:35",
  "prayer_time": "18:05",
  "max_candidates": 6,
  "auto_build_osm": false,
  "buffer_km": 10
}
```

Respons 200 mengikuti respons routing umum. Error routing dikembalikan sebagai 500.

### POST `/route/to-mosque`

Menghitung rute dari origin ke ID masjid tertentu.

| Field | Tipe | Wajib | Default/validasi |
|---|---:|---:|---|
| `start_lat`, `start_lon` | number | Ya | - |
| `mosque_id` | string | Ya | - |
| `dataset_id` | string/null | Tidak | dataset aktif |
| `algorithm` | enum | Tidak | `astar` |
| `auto_build_osm` | boolean | Tidak | false |
| `buffer_km` | number | Tidak | 6; 1-200 |
| `compact_response` | boolean | Tidak | true |

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

Jika masjid tidak ditemukan, implementasi saat ini membungkusnya sebagai error 500, bukan 404.

### POST `/routes/recommend`

Endpoint utama rekomendasi rute multi-objective.

| Field | Tipe | Wajib | Default/validasi |
|---|---:|---:|---|
| `origin` | coordinate | Ya | `{latitude, longitude}` |
| `destination` | coordinate/null | Tidak | Default sama dengan origin |
| `departure_time` | string | Ya | ISO 8601 atau string waktu |
| `prayer` | string | Ya | Gunakan `HH:MM` pada implementasi saat ini |
| `algorithm` | enum | Tidak | `astar` |
| `profile` | enum | Tidak | `balanced` |
| `search_radius_km` | number | Tidak | 10; 1-200 |
| `maximum_results` | integer | Tidak | 3; 1-10 |
| `auto_build_osm` | boolean | Tidak | false |
| `dataset_id` | string/null | Tidak | dataset aktif |
| `compact_response` | boolean | Tidak | true |

Coordinate:

```json
{"latitude": -6.2, "longitude": 106.8}
```

Request:

```json
{
  "origin": {"latitude": -6.2, "longitude": 106.8},
  "destination": {"latitude": -6.25, "longitude": 106.9},
  "departure_time": "2026-07-15T17:10:00+07:00",
  "prayer": "18:05",
  "algorithm": "astar",
  "profile": "balanced",
  "search_radius_km": 10,
  "maximum_results": 3,
  "auto_build_osm": false,
  "dataset_id": "dki_jakarta",
  "compact_response": true
}
```

`departure_time` diambil bagian jamnya. Walaupun deskripsi schema memberi contoh nama salat seperti `maghrib`, routing internal menggunakan parameter ini sebagai `prayer_time`; format `HH:MM` adalah pilihan aman untuk scoring waktu.

### POST `/routes/benchmark`

Menjalankan Dijkstra dan A* untuk input yang sama.

```json
{
  "origin": {"latitude": -6.2, "longitude": 106.8},
  "destination": {"latitude": -6.25, "longitude": 106.9},
  "departure_time": "2026-07-15T17:10:00+07:00",
  "prayer": "18:05",
  "profile": "balanced",
  "search_radius_km": 10,
  "dataset_id": "dki_jakarta"
}
```

Validasi: `profile` adalah salah satu empat profil, radius 1-200. Endpoint meminta `auto_build_osm=true` secara internal, tetapi build hanya benar-benar aktif jika environment server mengizinkannya.

Respons 200:

```json
{
  "status": "success",
  "benchmark": {
    "dijkstra": {
      "algorithm": "Dijkstra",
      "execution_time_ms": 120.4,
      "explored_nodes": 171,
      "route_distance_km": 1.42,
      "memory_usage_kb": 2450.0
    },
    "astar": {
      "algorithm": "A*",
      "execution_time_ms": 75.1,
      "explored_nodes": 68,
      "route_distance_km": 1.42,
      "memory_usage_kb": 1820.0
    },
    "comparison": {
      "faster_algorithm": "A*",
      "time_difference_ms": 45.3,
      "nodes_saved": 103,
      "efficiency_gain_percent": 37.6
    }
  }
}
```

Jumlah explored nodes dihitung dari faktor estimasi terhadap jumlah node rute. Nilai memori saat ini konstan, bukan hasil profiler. Jika salah satu routing gagal, endpoint tetap dapat mengembalikan 200 dengan angka nol/default.

### GET `/routes/{route_id}`

Mengembalikan GeoJSON contoh.

```bash
curl http://127.0.0.1:8000/api/v1/routes/demo-1
```

Respons 200 selalu menggunakan koordinat contoh dan memasukkan `route_id` yang diminta. Endpoint ini belum membaca penyimpanan rute persisten dan tidak mengembalikan 404.

### GET `/routing-profiles`

Mengambil profil dan bobot scoring.

| Profil | Bobot utama |
|---|---|
| `fastest` | travel time 0.60, prayer 0.25, distance 0.10, cost 0.05 |
| `prayer_priority` | prayer 0.50, travel time 0.30, distance 0.10, cost 0.10 |
| `low_cost` | cost 0.45, distance 0.25, travel time 0.20, prayer 0.10 |
| `balanced` | travel time 0.30, distance 0.30, prayer 0.20, cost 0.20 |

Respons 200 berisi array `profiles`, masing-masing dengan `name`, `label`, dan `weights`.

## OSM graph dan cache

### GET `/osm/status`

Memeriksa file dan metadata cache graph.

Query: `dataset_id` opsional. Tanpa ID, endpoint memakai dataset aktif.

Respons 200:

```json
{
  "status": "ok",
  "cache_exists": true,
  "cache_id": "dki_jakarta",
  "cache_path": "C:\\project\\data\\osm_cache\\road_graph_dki_jakarta.graphml",
  "size_mb": 42.7,
  "metadata": {
    "nodes": 12000,
    "edges": 28000,
    "network_type": "drive",
    "build_scope": "dataset_bbox"
  },
  "graph_runtime": {
    "status": "ready",
    "ready": true,
    "cache_exists": true,
    "runtime_cache_exists": true,
    "source": "runtime_binary"
  },
  "note": "OSM data diambil dari OpenStreetMap melalui OSMnx."
}
```

`cache_exists` baru true bila metadata ArangoDB dan file graph sama-sama ada.

### POST `/osm/build-bbox`

Mengunduh dan menyimpan graph dari bounding box untuk dataset yang sudah selesai diproses.

| Field | Tipe | Wajib | Default/validasi |
|---|---:|---:|---|
| `north`, `south`, `east`, `west` | number | Ya | Urutan belum divalidasi schema; OSM layer akan menolak bbox salah |
| `network_type` | enum | Tidak | `drive`; juga `walk`, `bike`, `all` |
| `dataset_id` | string | Ya secara aplikasi | Dataset harus processed |

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

Respons 200:

```json
{
  "status": "success",
  "cache_path": "data/osm_cache/road_graph_dki_jakarta.graphml",
  "nodes": 12000,
  "edges": 28000,
  "network_type": "drive",
  "build_scope": "custom_bbox"
}
```

Error: 400 bila dataset ID kosong/belum processed; 409 bila build lain berjalan; 500 bila Overpass/OSM gagal.

### POST `/osm/build-route`

Membangun graph koridor berdasarkan start-end dan buffer.

| Field | Tipe | Wajib | Default/validasi |
|---|---:|---:|---|
| `start_lat`, `start_lon` | number | Ya | - |
| `end_lat`, `end_lon` | number | Ya | - |
| `buffer_km` | number | Tidak | 6; 1-25 |
| `network_type` | enum | Tidak | `drive` |
| `dataset_id` | string/null | Tidak | Jika kosong memakai cache `latest` |

Respons 200 berisi `status`, `cache_path`, `nodes`, `edges`, `buffer_km`, dan `network_type`. Error 409 bila build lain berjalan, 500 bila build gagal.

### POST `/osm/build-all`

Memulai background job yang memvalidasi/membangun graph setiap dataset processed secara serial.

```json
{
  "network_type": "drive",
  "force": false
}
```

`network_type`: `drive`, `walk`, `bike`, atau `all`. `force=true` membangun ulang graph yang sudah valid.

Respons 200:

```json
{
  "status": "accepted",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Build graph semua dataset dimasukkan ke antrean."
}
```

Walaupun semantiknya accepted, status HTTP aktual adalah 200. Error 409 bila job sebelumnya masih `starting`, `running`, atau `cancelling`.

### GET `/osm/build-all/status`

Mengambil status job dan jumlah graph tersedia.

Respons 200 (contoh):

```json
{
  "status": "running",
  "cancel_requested": false,
  "total": 4,
  "completed": 1,
  "succeeded": 1,
  "failed": 0,
  "skipped": 0,
  "current_dataset_id": "jawa_barat",
  "items": [
    {
      "dataset_id": "dki_jakarta",
      "label": "DKI Jakarta",
      "status": "completed",
      "message": "Graph berhasil dibangun",
      "nodes": 12000,
      "edges": 28000,
      "size_mb": 42.7
    }
  ],
  "started_at": "2026-07-15T10:00:00+00:00",
  "finished_at": null,
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "available_graphs": 1,
  "total_datasets": 4
}
```

Status job dapat berupa `idle`, `starting`, `running`, `cancelling`, `cancelled`, `completed`, atau `interrupted`. State disimpan di `data/osm_cache/build_all_status.json` sehingga proses yang terputus dapat ditandai `interrupted` saat restart.

### POST `/osm/build-all/cancel`

Meminta pembatalan. Build dataset yang sedang aktif tidak dihentikan paksa; antrean berhenti setelah item itu selesai.

Respons saat aktif:

```json
{
  "status": "cancelling",
  "message": "Build akan dihentikan setelah dataset yang sedang diproses selesai."
}
```

Jika tidak ada job aktif, endpoint tetap mengembalikan 200 dengan status terakhir.

## User settings

### Schema settings

`user_id` wajib 3-128 karakter dan hanya boleh berisi huruf ASCII, angka, underscore, atau hyphen (`^[A-Za-z0-9_-]+$`).

Search settings:

| Field | Tipe | Default/validasi |
|---|---:|---|
| `algorithm` | enum | `dijkstra`; `dijkstra`/`astar` |
| `profile` | enum | `balanced`; empat profil routing |
| `currentTime` | string | `17:00`; format `HH:MM` |
| `prayer` | enum | `maghrib`; `subuh`, `dzuhur`, `ashar`, `maghrib`, `isya` |
| `maxCandidates` | string | `3`; string angka 1-10 |
| `bufferKm` | string | `15`; string angka 2-200, desimal didukung |
| `autoBuild` | boolean | false |

Prayer schedule item:

| Field | Tipe | Validasi |
|---|---:|---|
| `name` | enum | `Subuh`, `Dzuhur`, `Ashar`, `Maghrib`, `Isya` |
| `time` | string | `HH:MM` |
| `isAlarmActive` | boolean | Default false |

`prayer_settings.schedule` maksimal lima item. `hijriDate` dan `masehiDate` maksimal 80 karakter.

### POST `/user-settings`

Menyimpan atau merge settings berdasarkan `user_id`. `search_settings` dan `prayer_settings` opsional, sehingga update parsial tidak menghapus kelompok lain.

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
      {"name": "Subuh", "time": "04:42", "isAlarmActive": true}
    ],
    "hijriDate": "1 Safar 1448 H",
    "masehiDate": "15 Juli 2026"
  },
  "updated_at": "2026-07-15T17:00:00+07:00"
}
```

Respons 200:

```json
{
  "status": "success",
  "message": "Settings berhasil disimpan ke database",
  "user_id": "device_abc123",
  "data": {
    "user_id": "device_abc123",
    "search_settings": {},
    "prayer_settings": {},
    "updated_at": "2026-07-15T10:00:00+00:00"
  }
}
```

`updated_at` dari client disimpan sebagai `client_updated_at`; repository juga dapat menambahkan timestamp server. Error 422 untuk schema tidak valid, 500 untuk database.

### GET `/user-settings/{user_id}`

Mengambil settings.

Respons ditemukan:

```json
{
  "status": "success",
  "user_id": "device_abc123",
  "data": {
    "search_settings": {},
    "prayer_settings": {}
  }
}
```

Respons tidak ditemukan tetap HTTP 200:

```json
{
  "status": "not_found",
  "user_id": "device_abc123",
  "message": "User settings belum ada di database",
  "data": null
}
```

Path parameter belum divalidasi dengan regex yang sama seperti POST.

### DELETE `/user-settings/{user_id}`

Menghapus settings.

Respons berhasil:

```json
{
  "status": "success",
  "message": "Settings user device_abc123 berhasil dihapus"
}
```

Jika tidak ada, endpoint tetap HTTP 200 dengan `status: not_found`.

## Catatan operasional

### Cache dan performa

| Lapisan | Masa berlaku/batas | Invalidasi/kunci |
|---|---|---|
| Nearest backend | 30 detik, 512 entry | dataset, GPS 4 desimal, radius 0,1 km, limit; dihapus saat data masjid berubah |
| Kandidat masjid | 2 dataset secara default, maksimal 25.000 baris/dataset | `dataset_id + data_revision` |
| Rekomendasi use case | 5 menit, 128 entry | dataset, revision, graph fingerprint, koordinat, waktu, algoritma, profil, dan parameter pencarian |
| Selected/internal route | 24 jam, 256 entry | koordinat, masjid, algoritma, graph, dan parameter routing |
| Loaded graph | LRU, default 1 graph/proses | path/fingerprint GraphML |
| Edge snap | default 20.000 entry/graph | graph object dan koordinat yang dibulatkan |

- GraphML adalah sumber persisten; cache runtime biner ber-fingerprint dibuat otomatis untuk mempercepat cold load berikutnya. Cache rusak atau tidak cocok diabaikan dan dibangun ulang dari GraphML.
- Graph aktif dipanaskan di background saat startup/pemilihan dataset dan dipertahankan di memori sesuai `IMOSQUE_MAX_LOADED_GRAPHS`.
- Ketika graph masih `loading`, request tidak ikut menunggu parse GraphML; backend segera mencoba fallback OSRM.
- STRtree edge index dan proyeksi edge dibangun sekali atau dipulihkan dari `*.edges.pkl`, lalu dipakai ulang untuk snapping.
- Nearest dan routing memakai singleflight ber-shard agar request identik bersamaan tidak menduplikasi pekerjaan mahal.
- Ranking kandidat memakai Dijkstra multi-target. A* hanya dijalankan untuk kandidat final ketika algoritma tersebut diminta.
- GZip aktif untuk payload minimal 1.000 byte.

Pada benchmark lokal graph 101,7 MiB, cold-load GraphML membutuhkan 12,13 detik dan cold-load cache runtime biner 0,84 detik (sekitar 14,5x lebih cepat). Ini adalah hasil pengukuran workspace, bukan SLA produksi.

Benchmark HTTP lokal 16 Juli 2026 mencatat nearest lintas dataset limit 6 sebesar 51,70 ms pada cache miss, rute Dijkstra 22,65 ms pada cache miss, serta payload compact sekitar 0,98 KB dan 1,33 KB. Sepuluh request serentak berhasil 10/10 dengan p95 54,24 ms untuk nearest dan 195,75 ms untuk routing. Kondisi jaringan, ukuran graph, hardware, jumlah worker, dan rasio cache hit dapat mengubah angka tersebut secara signifikan.

### Inline OSM build

Field `auto_build_osm` pada request hanya efektif bila server menetapkan:

```text
IMOSQUE_ALLOW_INLINE_OSM_BUILD=true
```

Default `false` sengaja mencegah request UI menunggu download Overpass yang mahal. Gunakan endpoint `/osm/build-*` untuk administrasi graph.

### Konsumsi API yang aman

- Terapkan timeout dan cancellation pada client.
- Polling status upload/build dengan interval wajar (misalnya 1-3 detik).
- Jangan mengirim build graph secara paralel; API akan merespons 409.
- Simpan `encoded_polyline` bila hanya perlu menggambar rute dan gunakan `compact_response: true` untuk jaringan lambat.
- Jangan expose API ke internet sebelum menambahkan autentikasi, otorisasi admin, rate limiting, pembatasan CORS, dan password database yang kuat.

Perilaku client Next.js saat ini:

| Operasi | Timeout client | Retry |
|---|---:|---|
| Nearest mosque | 12 detik | Satu retry hanya untuk kegagalan jaringan sementara |
| Prayer times | 3 detik | Tidak ada |
| Route to mosque | 15 detik | Tidak ada; request rute lama dibatalkan ketika request baru dimulai |

Timeout di atas adalah kebijakan frontend, bukan timeout server dan bukan jaminan SLA. Client lain perlu menetapkan timeout, cancellation, dan retry sendiri. Jangan me-retry operasi mutasi secara otomatis karena API belum menyediakan idempotency key.

### Worker dan konkurensi

- Cache, loaded graph, STRtree, dan singleflight berada di memori proses. Beberapa worker Uvicorn/Gunicorn tidak berbagi cache tersebut.
- Setiap worker dapat memuat graph sendiri; perhitungkan RAM sebelum menaikkan jumlah worker.
- Dijkstra dan operasi graph bersifat CPU-bound. Tambah worker hanya setelah load test menunjukkan RAM cukup dan kontensi CPU masih terkendali.
- Background build graph diserialisasi di dalam satu proses. Untuk deployment multi-worker, endpoint build sebaiknya dipisahkan ke worker/admin service tunggal atau antrean pekerjaan eksternal.

## Contoh alur end-to-end

```bash
# 1. Upload CSV
curl -X POST http://127.0.0.1:8000/api/v1/datasets/upload \
  -F "file=@data/raw/dataset_masjid_banten.csv" \
  -F "dataset_name=banten" -F "make_active=true"

# 2. Pantau pipeline
curl http://127.0.0.1:8000/api/v1/datasets/status/banten

# 3. Dapatkan bbox
curl http://127.0.0.1:8000/api/v1/datasets/banten/bbox

# 4. Build graph memakai nilai bbox dari langkah 3
curl -X POST http://127.0.0.1:8000/api/v1/osm/build-bbox \
  -H "Content-Type: application/json" \
  -d '{"north":-6.1,"south":-6.3,"east":106.9,"west":106.7,"network_type":"drive","dataset_id":"banten"}'

# 5. Cari masjid terdekat
curl -X POST http://127.0.0.1:8000/api/v1/nearest-mosques \
  -H "Content-Type: application/json" \
  -d '{"latitude":-6.2,"longitude":106.8,"dataset_id":"banten","limit":3,"radius_km":10}'

# 6. Gunakan id dari hasil nearest untuk membuat rute compact
curl -X POST http://127.0.0.1:8000/api/v1/route/to-mosque \
  -H "Content-Type: application/json" \
  -d '{"start_lat":-6.2,"start_lon":106.8,"mosque_id":"<mosque_id>","dataset_id":"banten","algorithm":"dijkstra","auto_build_osm":false,"buffer_km":6,"compact_response":true}'
```

Untuk eksplorasi interaktif dan schema terbaru yang dihasilkan runtime, gunakan Swagger UI di `/docs`.
