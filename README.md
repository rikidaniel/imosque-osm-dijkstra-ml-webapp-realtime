# iMosque SafarRoute

iMosque SafarRoute adalah aplikasi web pencarian masjid dan rekomendasi rute berbasis data masjid, OpenStreetMap, Dijkstra/A*, serta enrichment machine learning. Aplikasi terdiri dari frontend Next.js, REST API FastAPI, dan ArangoDB.

> Status: prototipe akademik. Data fasilitas, kapasitas, rating, `priority_score`, dan `tier` yang dihasilkan pipeline adalah estimasi/proxy, bukan hasil verifikasi lapangan.

## Daftar isi

- [Fitur](#fitur)
- [Arsitektur](#arsitektur)
- [Database, indeks, dan cache](#database-indeks-dan-cache)
- [Performa](#performa)
- [Teknologi](#teknologi)
- [Prasyarat](#prasyarat)
- [Menjalankan aplikasi](#menjalankan-aplikasi)
- [Konfigurasi](#konfigurasi)
- [Format dataset CSV](#format-dataset-csv)
- [Alur penggunaan](#alur-penggunaan)
- [Ringkasan API](#ringkasan-api)
- [Pengujian](#pengujian)
- [Struktur repositori](#struktur-repositori)
- [Troubleshooting](#troubleshooting)
- [Checklist deployment produksi](#checklist-deployment-produksi)
- [Batasan dan catatan keamanan](#batasan-dan-catatan-keamanan)

## Fitur

- Upload, proses, pilih, pantau, dan hapus dataset CSV tanpa restart backend.
- Pembersihan koordinat dan enrichment atribut masjid menggunakan pandas/scikit-learn.
- Pencarian masjid terdekat per dataset atau lintas seluruh dataset dengan satu geo query, cache 30 detik, dan penggabungan request identik.
- Kalkulasi waktu salat offline dengan pembagian zona waktu Indonesia.
- Rekomendasi rute menggunakan Dijkstra atau A* pada graph jalan OpenStreetMap.
- Empat profil scoring: `fastest`, `prayer_priority`, `low_cost`, dan `balanced`.
- Build dan cache GraphML per dataset, runtime cache biner tervalidasi, prewarm saat startup, serta antrean build seluruh dataset.
- CRUD data masjid dan sinkronisasi pengaturan pengguna.
- Respons ringkas menggunakan Google Encoded Polyline 5 dan GZip.
- Dashboard peta Leaflet dengan debounce GPS, pembatalan request usang, panel benchmark, pengelolaan dataset, dan service worker.

## Arsitektur

```text
Browser / Next.js :3000
        |
        | HTTP JSON / multipart
        v
FastAPI /api/v1 :8000
   |        |          |
   |        |          +--> kalkulasi waktu salat offline
   |        +-------------> OSMnx + NetworkX + cache GraphML
   +----------------------> ArangoDB :8529
                              |
                              +--> dataset, masjid, cache metadata,
                                   dan user settings
```

Alur data utamanya:

```text
CSV -> upload -> proses ML -> ArangoDB -> marker peta
                                      -> kandidat masjid
OSM/GraphML -> nearest road node -> Dijkstra/A* -> polyline/GeoJSON
```

Backend mengikuti pemisahan domain, use case, interface API, dan infrastructure. Graph jalan disimpan sebagai GraphML di `data/osm_cache/`; cache runtime biner dibuat otomatis dengan fingerprint ukuran dan waktu modifikasi GraphML. Metadata cache disimpan di ArangoDB.

Jalur request interaktif sengaja dibuat pendek:

```text
GPS -> geo index ArangoDB -> kandidat masjid terbatas
    -> STRtree edge snapping -> Dijkstra multi-target/bidirectional
    -> simplifikasi geometry -> encoded polyline -> GZip -> browser
```

Pekerjaan mahal seperti parsing GraphML, pembuatan spatial index, dan pemanasan kandidat dilakukan saat build/prewarm, bukan diulang pada setiap klik pengguna.

## Database, indeks, dan cache

Backend membuat database `imosque` dan collection yang diperlukan saat startup. Collection utama yang aktif dipakai aplikasi:

| Collection | Isi | Indeks penting |
|---|---|---|
| `Mosque` | Dokumen masjid seluruh dataset | persistent `dataset_id`, gabungan `dataset_id + id`, `id`, dan GeoJSON `coordinate` |
| `datasets` | Metadata upload, status pipeline, dataset aktif, dan `data_revision` | key dokumen per dataset |
| `osm_graph_cache` | Metadata file GraphML, bbox, fingerprint, node, dan edge | key cache per dataset |
| `user_settings` | Preferensi pencarian dan jadwal salat | persistent unik `user_id` |
| `app_settings` | Konfigurasi aplikasi, termasuk dataset aktif | key dokumen setting |

Collection `RoadNode` dan edge collection ArangoDB tetap dibuat untuk kompatibilitas model data, tetapi routing interaktif saat ini membaca road graph dari GraphML/cache runtime lokal.

Lapisan penyimpanan dan cache:

| Lapisan | Lokasi/umur | Tujuan |
|---|---|---|
| Geo query | ArangoDB | Menghindari full scan 233 ribu lebih dokumen masjid |
| Nearest result | RAM, 30 detik, maksimum 512 entry | Mengulang pencarian GPS yang sama tanpa query database baru |
| Candidate snapshot | RAM, berbasis `data_revision` | Menghindari fetch dan normalisasi kandidat berulang |
| GraphML | `data/osm_cache/*.graphml` | Sumber persisten road graph per dataset |
| Runtime graph | `*.runtime.pkl`, fingerprint GraphML | Menghindari parse GraphML pada cold load berikutnya |
| Edge index | `*.edges.pkl`, fingerprint GraphML | Memulihkan STRtree/proyeksi edge dengan cepat |
| Loaded graph | RAM, LRU | Memakai ulang graph dan spatial index antarrute |
| Edge snap | RAM, maksimum 20.000 entry/graph | Memakai ulang hasil proyeksi koordinat ke edge |
| Recommendation | RAM, 5 menit, maksimum 128 entry | Menggabungkan dan memakai ulang rekomendasi identik |
| Selected route | RAM, 24 jam, maksimum 256 entry | Memakai ulang rute origin-masjid yang identik |

Cache nearest dan rekomendasi memakai singleflight/lock ber-shard: request identik yang tiba bersamaan menunggu satu komputasi pemilik. `data_revision` dan fingerprint GraphML mencegah data atau graph lama dipakai setelah mutasi.

## Performa

Optimasi utama yang menjaga latensi tetap rendah:

- Query nearest memakai `GEO_DISTANCE`, filter radius, pengurutan jarak, dan `LIMIT` maksimal 50.
- Kandidat routing dipreseleksi dengan query koridor, NumPy, dan snapshot revision-aware sebelum menyentuh graph.
- Koordinat di-snap ke edge melalui STRtree persisten; endpoint tidak melakukan linear scan seluruh edge.
- Ranking kandidat memakai Dijkstra multi-source/multi-target dan pencarian dapat berhenti setelah target yang dibutuhkan ditemukan.
- Graph dipadatkan ke atribut yang dipakai routing dan disimpan sebagai runtime binary tervalidasi.
- Response compact membuang duplikasi GeoJSON, memakai Google Encoded Polyline 5, simplifikasi geometry, dan GZip untuk payload minimal 1.000 byte.

Hasil benchmark lokal tanggal 16 Juli 2026:

| Skenario | Hasil |
|---|---:|
| Total dokumen masjid | 233.418 |
| Nearest lintas dataset, limit 6, cache miss | 51,70 ms |
| Nearest warm median | 13,64 ms |
| Rute Dijkstra, cache miss | 22,65 ms |
| Komputasi inti Dijkstra pada sampel | 8,27 ms |
| Rute warm median | 15,9 ms |
| Payload nearest 6 masjid | sekitar 0,98 KB |
| Payload rute compact | sekitar 1,33 KB |
| 10 nearest bersamaan | 10/10 berhasil, p95 54,24 ms |
| 10 rute bersamaan | 10/10 berhasil, p95 195,75 ms |

Angka tersebut adalah benchmark HTTP lokal dengan database dan graph sudah tersedia, bukan SLA produksi. Pada 3G, round-trip jaringan dan tile peta biasanya lebih dominan daripada payload JSON API 1–2 KB. Uji 25/50/100 pengguna, p95/p99 end-to-end, RAM per worker, dan kondisi server produksi tetap diperlukan sebelum scale-out.

## Teknologi

| Bagian | Teknologi |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript, Leaflet, Zustand, Recharts, Tailwind CSS |
| Backend | Python, FastAPI, Uvicorn, Pydantic |
| Database | ArangoDB 3.11 |
| Routing | OSMnx, NetworkX, OpenStreetMap, fallback OSRM publik |
| Data/ML | pandas, NumPy, scikit-learn, GeoPandas, Shapely, PyProj |
| Infrastruktur lokal | Docker Compose untuk ArangoDB |

## Prasyarat

- Python 3.11 direkomendasikan.
- Node.js 20.18.1 atau lebih baru dan npm.
- Docker Desktop/Docker Engine dengan Compose, atau instalasi ArangoDB yang dapat diakses.
- Internet saat pertama kali mengunduh dependency, tile peta, atau graph OSM.
- Ruang disk yang cukup; GraphML wilayah besar dapat berukuran besar.

Pada Windows, Miniconda/Anaconda sering lebih mudah untuk dependency geospasial.

## Menjalankan aplikasi

### 1. Clone dan masuk ke repositori

```bash
git clone <url-repositori>
cd imosque-osm-dijkstra-ml-webapp-realtime
```

### 2. Jalankan ArangoDB

```bash
docker compose up -d arangodb
```

Konfigurasi bawaan:

- URL: `http://localhost:8529`
- Username: `root`
- Password: `imosque_password`
- Database aplikasi: `imosque` (dibuat otomatis saat backend mulai)

Untuk memeriksa container:

```bash
docker compose ps
```

### 3. Siapkan backend

Menggunakan virtual environment:

```bash
python -m venv .venv
```

Aktifkan environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux/macOS
source .venv/bin/activate
```

Instal dependency dan jalankan server dari root repositori:

```bash
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Alternatif Windows: jalankan `start_backend.bat`.

Backend tersedia pada:

- API: `http://127.0.0.1:8000/api/v1`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

### 4. Siapkan frontend

Buka terminal kedua dari root repositori:

```bash
cd frontend
npm ci
npm run dev
```

Buka `http://localhost:3000`.

Frontend menentukan backend dari hostname browser dan port `8000`. Contoh: jika frontend dibuka melalui `http://192.168.1.10:3000`, request API diarahkan ke `http://192.168.1.10:8000`.

> `start_frontend.bat` saat ini hanya menjalankan server statis lama pada port 5500. Untuk UI Next.js utama gunakan `npm run dev`.

### 5. Verifikasi instalasi

```bash
curl http://127.0.0.1:8000/api/v1/health
```

Contoh respons:

```json
{
  "status": "healthy",
  "graph_status": "not_configured",
  "graph_ready": false,
  "graph_runtime": {
    "status": "not_configured",
    "ready": false,
    "cache_exists": false,
    "runtime_cache_exists": false,
    "edge_index_cache_exists": false
  },
  "version": "4.0.0",
  "active_dataset_id": "banten"
}
```

`graph_status: not_configured` masih normal sebelum graph OSM dibangun. Status dapat berubah menjadi `available`, `loading`, `ready`, atau `error`; jalankan routing lokal setelah `graph_ready` bernilai `true`. Backend memulai prewarm graph dataset aktif tanpa menahan startup API.

## Konfigurasi

Backend membaca environment variable berikut:

| Variable | Default | Keterangan |
|---|---|---|
| `ARANGO_HOST` | `http://localhost:8529` | URL ArangoDB |
| `ARANGO_ROOT_PASSWORD` | `imosque_password` | Password pengguna `root` |
| `IMOSQUE_ALLOW_INLINE_OSM_BUILD` | `false` | Izinkan request routing membangun graph secara inline |
| `IMOSQUE_PREWARM_GRAPH_ON_STARTUP` | `true` | Muat graph dan indeks edge dataset aktif di background saat backend mulai |
| `IMOSQUE_MAX_LOADED_GRAPHS` | `1` | Jumlah maksimum graph yang dipertahankan di memori |
| `IMOSQUE_COMPACT_GRAPH` | `true` | Aktifkan pemadatan atribut graph |
| `IMOSQUE_EDGE_SNAP_CACHE_SIZE` | `20000` | Batas hasil proyeksi koordinat-ke-edge per graph; `0` menonaktifkan cache |
| `IMOSQUE_MOSQUE_CACHE_DATASETS` | `2` | Jumlah snapshot kandidat masjid berbasis revisi yang disimpan di memori |
| `IMOSQUE_MOSQUE_CACHE_MAX_ROWS` | `25000` | Batas ukuran dataset yang boleh dipanaskan sebagai snapshot kandidat; `0` menonaktifkan |

Contoh PowerShell sebelum menjalankan backend:

```powershell
$env:ARANGO_HOST = "http://localhost:8529"
$env:ARANGO_ROOT_PASSWORD = "password-kuat"
$env:IMOSQUE_MAX_LOADED_GRAPHS = "2"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`IMOSQUE_ALLOW_INLINE_OSM_BUILD=true` dapat membuat request routing berlangsung lama. Untuk penggunaan normal, build graph terlebih dahulu melalui endpoint admin dan biarkan nilainya `false`.

Indeks geometri edge disimpan sebagai cache `.edges.pkl` dengan fingerprint GraphML. Cache otomatis dibangun ulang ketika file GraphML berubah. Set `IMOSQUE_PREWARM_GRAPH_ON_STARTUP=false` hanya jika proses startup tidak boleh memakai CPU/RAM untuk routing di background.

Snapshot kandidat masjid diikat ke `data_revision`, sedangkan cache edge-snap menempel pada objek graph. Keduanya otomatis tidak dipakai lagi ketika dataset atau GraphML berganti, sehingga percepatan tidak mengorbankan konsistensi hasil.

## Format dataset CSV

Kolom minimum:

```csv
name,latitude,longitude
Masjid Contoh,-6.2001,106.8166
```

Kolom yang didukung/direkomendasikan:

```text
uuid, provinsi, kabko, kecamatan, kelurahan, name, address,
postal_code, latitude, longitude, rating, review_count,
checkin_count, mosque_type, mosque_topology, facilities
```

Ketentuan penting:

- File harus berekstensi `.csv`.
- Koordinat harus berupa angka dan berada dalam rentang umum Indonesia.
- Nama dataset diubah menjadi slug, misalnya `DKI Jakarta` menjadi `dki_jakarta`.
- Pemisah CSV dideteksi otomatis; koma adalah pilihan paling aman.
- Upload diproses asinkron. Pantau status sampai `processing_status` bernilai `completed` atau `failed`.
- Perubahan dataset/masjid dapat membatalkan cache graph yang berasal dari dataset tersebut; build ulang graph bila diperlukan.

## Alur penggunaan

### Upload dataset

1. Buka halaman admin/dataset.
2. Pilih CSV, isi nama dataset, dan tentukan apakah langsung aktif.
3. Upload file.
4. Pantau status pemrosesan.
5. Setelah selesai, muat marker masjid atau pilih dataset sebagai aktif.

Contoh dengan cURL:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/datasets/upload \
  -F "file=@data/raw/dataset_masjid_banten.csv" \
  -F "dataset_name=banten" \
  -F "make_active=true"
```

### Build graph OSM

Graph sebaiknya dibangun setelah dataset selesai diproses:

1. Ambil bbox dataset: `GET /datasets/{dataset_id}/bbox`.
2. Kirim bbox ke `POST /osm/build-bbox`, atau mulai `POST /osm/build-all`.
3. Untuk build massal, polling `GET /osm/build-all/status`.
4. Pantau `GET /health` sampai `graph_ready: true`, lalu jalankan rekomendasi rute.

Build membutuhkan akses ke layanan Overpass/OpenStreetMap dan diproses satu per satu. Build massal dapat dibatalkan, tetapi dataset yang sedang diproses diselesaikan terlebih dahulu.

Benchmark lokal pada graph 101,7 MiB menunjukkan cold-load turun dari 12,13 detik melalui GraphML menjadi 0,84 detik melalui cache runtime biner (sekitar 14,5x). Angka aktual bergantung pada ukuran graph, CPU, RAM, dan kecepatan disk; GraphML tetap menjadi sumber data utama.

### Mencari rute

```bash
curl -X POST http://127.0.0.1:8000/api/v1/routes/recommend \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"latitude": -6.2001, "longitude": 106.8166},
    "destination": {"latitude": -6.2501, "longitude": 106.9002},
    "departure_time": "2026-07-15T17:10:00+07:00",
    "prayer": "18:05",
    "algorithm": "astar",
    "profile": "balanced",
    "search_radius_km": 10,
    "maximum_results": 3,
    "auto_build_osm": false,
    "dataset_id": "dki_jakarta",
    "compact_response": true
  }'
```

Pada implementasi saat ini, field `prayer` diteruskan sebagai nilai waktu salat. Gunakan format `HH:MM` untuk hasil scoring yang konsisten. Kandidat diranking dengan dua traversal Dijkstra multi-target; bila `algorithm: astar`, A* hanya mematerialisasi rute kandidat terbaik. Ini menghindari pencarian A* berulang untuk setiap kandidat.

## Ringkasan API

Semua endpoint menggunakan base URL `http://127.0.0.1:8000/api/v1`. Tidak ada autentikasi pada versi saat ini.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/health` | Status backend, graph, dan dataset aktif |
| GET | `/datasets` | Daftar dataset |
| POST | `/datasets/upload` | Upload dan proses CSV |
| POST | `/datasets/active` | Pilih dataset aktif |
| GET | `/datasets/status/{dataset_id}` | Status pipeline |
| GET | `/datasets/{dataset_id}/bbox` | Bounding box robust dataset |
| DELETE | `/datasets/{dataset_id}` | Hapus dataset |
| POST | `/pipeline/run` | Jalankan ulang pipeline dari CSV di disk |
| GET | `/profile` | Profil dataset |
| GET | `/mosques` | Daftar masjid |
| POST | `/mosques/{dataset_id}` | Tambah masjid |
| PUT | `/mosques/{dataset_id}/{mosque_id}` | Ubah masjid |
| DELETE | `/mosques/{dataset_id}/{mosque_id}` | Hapus masjid |
| POST | `/mosques/bulk-delete` | Hapus banyak masjid |
| POST | `/nearest-mosques` | Cari masjid terdekat |
| GET | `/prayer-times` | Hitung waktu salat offline |
| POST | `/route` | Routing kompatibilitas/level rendah |
| POST | `/route/to-mosque` | Rute ke masjid tertentu |
| POST | `/routes/recommend` | Rekomendasi rute multi-objective |
| POST | `/routes/benchmark` | Bandingkan Dijkstra dan A* |
| GET | `/routes/{route_id}` | Respons contoh rute tersimpan |
| GET | `/routing-profiles` | Daftar bobot profil routing |
| GET | `/osm/status` | Status cache graph |
| POST | `/osm/build-bbox` | Build graph dari bbox |
| POST | `/osm/build-route` | Build graph koridor start-end |
| POST | `/osm/build-all` | Mulai build seluruh dataset |
| GET | `/osm/build-all/status` | Progres build massal |
| POST | `/osm/build-all/cancel` | Minta pembatalan build massal |
| POST | `/user-settings` | Simpan/merge settings |
| GET | `/user-settings/{user_id}` | Ambil settings |
| DELETE | `/user-settings/{user_id}` | Hapus settings |

Kontrak lengkap, validasi, contoh request/response, status code, dan catatan setiap endpoint tersedia di [docs/api.md](docs/api.md).

## Pengujian

### Backend unit test

`pytest` belum tercantum dalam dependency runtime, jadi instal sebagai dependency pengembangan:

```bash
pip install pytest
python -m pytest backend/tests -q
```

Test mencakup matematika routing, spatial index, invalidasi graph, antrean build OSM, dan user settings.

### Frontend

```bash
cd frontend
npm run lint
npm run build
```

### Smoke test API

Dengan backend dan database aktif:

```bash
python test_api.py
python scripts/test_route_request.py
```

Script smoke test bergantung pada data lokal dan graph/cache yang tersedia.

### Utility CLI

Jalankan ulang ML untuk dataset aktif atau ID tertentu:

```bash
python scripts/run_ml_pipeline.py
python scripts/run_ml_pipeline.py dki_jakarta
```

Build graph manual:

```bash
python scripts/build_osm_graph.py bbox \
  --north -6.10 --south -6.30 --east 106.95 --west 106.70 \
  --network-type drive
```

## Struktur repositori

```text
.
|-- backend/
|   |-- app/
|   |   |-- domain/            # model dan kontrak repository
|   |   |-- infrastructure/    # ArangoDB, OSM, ML, waktu salat
|   |   |-- interfaces/api/    # route FastAPI
|   |   `-- use_cases/         # orkestrasi dataset dan routing
|   |-- tests/                 # unit test backend
|   `-- requirements.txt
|-- frontend/
|   |-- src/app/               # halaman Next.js
|   |-- src/components/        # map, route, dataset, user, UI
|   |-- src/lib/               # API client dan state
|   `-- public/                # manifest dan service worker
|-- data/
|   |-- raw/                   # dataset CSV sumber
|   `-- osm_cache/             # GraphML, cache runtime biner, dan status build
|-- docs/                      # API, arsitektur, PRD, evaluasi
|-- scripts/                   # utility pipeline, graph, smoke test
|-- docker-compose.yml
`-- README.md
```

Folder `data/processed/`, `data/osm_cache/*.graphml`, `data/osm_cache/*.runtime.pkl`, dan output tertentu tidak dilacak Git karena merupakan hasil proses/cache lokal.

## Troubleshooting

### Backend gagal terhubung ke ArangoDB

- Pastikan `docker compose ps` menunjukkan container berjalan.
- Cocokkan `ARANGO_HOST` dan `ARANGO_ROOT_PASSWORD` dengan konfigurasi database.
- Pastikan port 8529 tidak dipakai proses lain.

### `422 Unprocessable Entity`

Payload tidak lolos validasi Pydantic. Periksa nama field, tipe data, enum, batas angka, dan format jam/tanggal. Detail kesalahan ada pada array `detail` di respons.

### Upload selalu `processing`

Polling `/datasets/status/{dataset_id}`. Jika proses gagal, field `message` berisi penyebab. Pastikan CSV mempunyai `name`, `latitude`, dan `longitude` yang valid.

### Routing gagal karena graph tidak ada

Bangun graph melalui `/osm/build-bbox` atau `/osm/build-all`. `auto_build_osm: true` tidak membangun graph kecuali server menjalankan `IMOSQUE_ALLOW_INLINE_OSM_BUILD=true`.

### Overpass timeout

- Coba lagi beberapa saat kemudian.
- Gunakan bbox/buffer lebih kecil.
- Jangan menjalankan beberapa build secara bersamaan.
- Build per dataset dan gunakan cache yang sudah dihasilkan.

### Frontend tidak dapat mengakses backend

- Pastikan backend listen pada port 8000.
- Jika diakses dari perangkat lain, buka frontend memakai hostname/IP mesin backend yang sama.
- Periksa firewall untuk port 3000 dan 8000.
- Pastikan halaman tidak menggunakan HTTPS sementara API hanya HTTP, karena browser dapat memblokir mixed content.

### Pencarian nearest atau rute timeout

- Periksa `/api/v1/health`; nearest tetap dapat bekerja tanpa graph, tetapi routing lokal memerlukan `graph_ready: true`.
- Periksa `/api/v1/osm/status?dataset_id=<id>` untuk memastikan metadata, GraphML, runtime cache, dan edge index tersedia.
- Pastikan `dataset_id` sesuai; nearest dengan ID kosong mencari lintas seluruh dataset, sedangkan routing memakai dataset aktif.
- Hindari build GraphML pada request pengguna. Jalankan `/osm/build-bbox` atau `/osm/build-all` sebelumnya dan biarkan prewarm selesai.
- Periksa CPU/RAM saat p95 rute naik. Dijkstra adalah pekerjaan CPU-bound dan dapat mengalami kontensi saat banyak request bersamaan.

## Checklist deployment produksi

- Ganti `ARANGO_ROOT_PASSWORD` default dan jangan expose port 8529 ke internet publik.
- Tambahkan autentikasi serta otorisasi admin untuk upload, mutasi data, penghapusan, build graph, dan settings.
- Batasi `allow_origins` CORS ke domain frontend produksi.
- Jalankan frontend dan API melalui HTTPS/reverse proxy dengan hostname API yang eksplisit; implementasi frontend saat ini mengasumsikan port 8000 pada hostname yang sama.
- Build GraphML seluruh dataset sebelum menerima trafik dan pastikan `/health` melaporkan `graph_ready: true` untuk dataset aktif.
- Pilih jumlah worker berdasarkan RAM graph. Setiap proses worker mempunyai graph dan cache in-memory sendiri; menambah worker dapat melipatgandakan penggunaan RAM.
- Tambahkan rate limiting, request ID, structured logging, metrik p50/p95/p99, error tracking, dan health check database terpisah.
- Uji beban nearest dan routing secara terpisah pada profil jaringan cepat serta 3G; ukur payload, CPU, RAM, dan rasio cache hit.
- Backup volume ArangoDB dan direktori graph/cache yang mahal untuk dibangun ulang.
- Gunakan tile provider dengan kebijakan penggunaan dan kapasitas yang sesuai; tile OSM publik bukan CDN tanpa batas untuk produksi.

## Batasan dan catatan keamanan

- API belum memiliki autentikasi/otorisasi; endpoint upload, delete, build graph, dan settings tidak boleh dibuka langsung ke internet.
- CORS saat ini mengizinkan semua origin. Batasi origin sebelum deployment produksi.
- Password default Docker hanya untuk pengembangan lokal; ganti untuk lingkungan bersama/produksi.
- Layanan OSRM publik merupakan fallback dan tidak memiliki SLA untuk aplikasi ini.
- Akurasi rute bergantung pada kelengkapan OpenStreetMap dan proses snap ke node jalan.
- Waktu salat adalah kalkulasi offline bergaya Kemenag dan tetap perlu validasi untuk penggunaan resmi.
- Endpoint `GET /routes/{route_id}` saat ini mengembalikan GeoJSON contoh, bukan mengambil rute persisten dari database.
- Benchmark menggunakan estimasi untuk jumlah node yang dieksplorasi dan penggunaan memori; jangan diperlakukan sebagai profiler ilmiah tanpa instrumentasi tambahan.
- Tidak ada rate limiting, pagination cursor, migrasi database formal, atau observability produksi.

## Dokumentasi terkait

- [Dokumentasi API lengkap](docs/api.md)
- [Arsitektur](docs/arsitektur.md)
- [Evaluasi](docs/evaluation.md)
- [PRD](docs/prd.md)
- [Fitur user settings](FITUR_USER_SETTINGS.md)
