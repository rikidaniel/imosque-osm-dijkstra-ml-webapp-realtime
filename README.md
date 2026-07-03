# iMosque OSM Dijkstra ML Web App

Project ini adalah prototype **Safar Mode iMosque** dengan alur:

```text
CSV dataset masjid per wilayah
→ upload / pilih dataset dari frontend
→ ML enrichment realtime
→ enriched JSON
→ marker masjid di peta Leaflet
→ OpenStreetMap road graph via OSMnx
→ Dijkstra/A* routing
→ GeoJSON route ke frontend
```

Versi ini sudah mendukung **ganti-ganti dataset lewat frontend**. Misalnya kamu punya CSV/tab untuk:

- Banten
- DKI Jakarta
- Jawa Barat
- Jawa Tengah
- Jawa Timur
- DI Yogyakarta

Kamu bisa upload CSV-nya satu per satu dari website, lalu pilih dataset aktif tanpa restart backend.

## Fitur Utama

1. **Dataset switcher realtime**
   - Upload CSV baru dari frontend.
   - Dataset langsung diproses ML enrichment.
   - Dataset tersimpan di `data/raw/datasets/`.
   - Hasil JSON tersimpan per dataset di `data/processed/<dataset_id>/enriched_mosques.json`.
   - Marker peta otomatis berubah sesuai dataset aktif.

2. **AI/ML enrichment**
   - Cleaning latitude-longitude.
   - Filter koordinat umum Indonesia + filter bounds provinsi untuk Banten, DKI Jakarta, Jawa Barat, Jawa Tengah, Jawa Timur, dan DI Yogyakarta.
   - Prediksi `rating` kosong memakai Random Forest Regressor.
   - Prediksi `facilities` kosong memakai TF-IDF + One-vs-Rest Logistic Regression.
   - Membuat `capacity_proxy`, `priority_score`, dan `tier`.

3. **Routing OpenStreetMap**
   - Road network diambil dari **OpenStreetMap** via OSMnx.
   - Start, destination, dan masjid kandidat di-*snap* ke node jalan terdekat.
   - Dijkstra/A* dijalankan pada graph jalan OSM.
   - Rute dikirim ke frontend sebagai GeoJSON.

4. **Multi-objective scoring**
   - Waktu tempuh.
   - Jarak tempuh.
   - Kesesuaian waktu shalat/adzan.
   - Kapasitas proxy.
   - Priority score hasil enrichment.

## Catatan Jujur Akademik

Atribut hasil ML seperti `facilities`, `capacity_proxy`, `priority_score`, dan `tier` adalah **estimasi/proxy**, bukan data lapangan terverifikasi. Road network berasal dari OpenStreetMap, bukan Google Maps.

Kalimat aman untuk laporan:

> Sistem menggunakan AI/ML enrichment untuk melengkapi atribut pendukung routing, kemudian melakukan pencarian rute menggunakan algoritma Dijkstra pada graph jalan OpenStreetMap. Atribut hasil enrichment diperlakukan sebagai proxy estimation, bukan fakta lapangan aktual.

## Struktur Project

```text
imosque-osm-dijkstra-ml-webapp/
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── ml_enrichment.py
│       ├── osm_graph.py
│       ├── routing_osm.py
│       └── schemas.py
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── scripts/
│   ├── run_ml_pipeline.py
│   ├── build_osm_graph.py
│   └── test_route_request.py
├── data/
│   ├── raw/
│   │   ├── dataset_masjid_banten.csv
│   │   └── datasets/
│   │       └── banten.csv
│   ├── processed/
│   │   ├── active_dataset.json
│   │   └── banten/
│   │       ├── enriched_mosques.json
│   │       └── data_profile_summary.json
│   └── osm_cache/
├── docs/
├── outputs/
├── start_backend.bat
├── start_frontend.bat
└── run_ml_pipeline.bat
```

## Format CSV yang Dibutuhkan

Minimal CSV harus punya kolom:

```text
name, latitude, longitude
```

Lebih bagus kalau punya kolom seperti dataset iMosque:

```text
uuid (primary_key), provinsi, kabko, kecamatan, kelurahan,
name, address, postal_code, latitude, longitude,
rating, review_count, checkin_count, mosque_type, mosque_topology, facilities
```

Kalau dataset masih di Google Sheets seperti pada gambar, lakukan:

```text
File → Download → Comma Separated Values (.csv)
```

Lakukan untuk setiap tab, misalnya tab DKI Jakarta, Jawa Barat, Jawa Tengah, dan seterusnya. Setelah itu upload dari frontend.

## Cara Menjalankan

### 1. Install backend dependencies

Dari root project:

```bash
cd backend
pip install -r requirements.txt
```

Catatan: `osmnx` membutuhkan dependensi geospasial. Pada Windows, cara paling mudah biasanya memakai Anaconda/Miniconda:

```bash
conda create -n imosque python=3.11 -y
conda activate imosque
pip install -r backend/requirements.txt
```

### 2. Jalankan backend

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app --reload-include "*.py"
```

Atau jalankan `start_backend.bat` dari root project. Mode ini otomatis me-restart backend setiap ada perubahan file Python di `backend/app`.

Buka dokumentasi API:

```text
http://127.0.0.1:8000/docs
```

### 3. Jalankan frontend

Buka terminal baru dari root project, lalu masuk ke folder `frontend` dan jalankan Next.js:

```bash
cd frontend
npm install
npm run dev
```

Lalu buka di browser:

```text
http://localhost:3000
```

## Cara Ganti Dataset dari Frontend

1. Buka website frontend.
2. Pada bagian **Dataset Realtime**, pilih file CSV.
3. Isi nama dataset, contoh:

```text
dki_jakarta
jawa_barat
jawa_tengah
jawa_timur
```

4. Klik **Upload + Proses ML**.
5. Setelah selesai, marker peta akan berpindah ke dataset baru.
6. Untuk kembali ke dataset lain, pilih dari dropdown **Dataset aktif**, lalu klik **Gunakan Dataset**.

## Cara Routing

1. Pilih dataset aktif.
2. Klik **Load Marker Masjid**.
3. Klik **Set Start**, lalu klik titik awal di peta.
4. Klik **Set Destination**, lalu klik titik tujuan di peta.
5. Centang **Auto-build/rebuild OSM graph saat routing** agar backend otomatis mengambil road network OSM untuk area tersebut.
6. Klik **Cari Rute Dijkstra**.

Catatan: build OSM butuh internet dan bisa memakan waktu tergantung luas buffer area.

## Endpoint API Penting

```text
GET  /api/v1/health
GET  /api/v1/datasets
POST /api/v1/datasets/upload
POST /api/v1/datasets/active
POST /api/v1/pipeline/run?dataset_id=<id>
GET  /api/v1/profile?dataset_id=<id>
GET  /api/v1/mosques?dataset_id=<id>&limit=3000
POST /api/v1/routes/recommend
POST /api/v1/routes/benchmark
GET  /api/v1/routing-profiles
GET  /api/v1/routes/{route_id}
```

Contoh upload dataset dilakukan dari frontend menggunakan `multipart/form-data`.

Contoh request routing:

```json
{
  "dataset_id": "dki_jakarta",
  "start_lat": -6.2001,
  "start_lon": 106.8166,
  "end_lat": -6.2501,
  "end_lon": 106.9002,
  "algorithm": "dijkstra",
  "current_time": "17:35",
  "prayer_time": "18:05",
  "max_candidates": 6,
  "auto_build_osm": true,
  "buffer_km": 6
}
```

## Output JSON ke Frontend

Backend mengirim:

- `recommended_mosque`
- `route_summary`
- `route_geojson`
- `candidate_mosques`
- `dataset_id`

Contoh ringkas:

```json
{
  "algorithm": "Dijkstra",
  "dataset_id": "dki_jakarta",
  "road_network": "OpenStreetMap via OSMnx/NetworkX",
  "recommended_mosque": {
    "name": "Masjid Contoh",
    "latitude": -6.2,
    "longitude": 106.8,
    "tier": "B",
    "capacity_proxy": "medium"
  },
  "route_geojson": {
    "type": "Feature",
    "geometry": {
      "type": "LineString",
      "coordinates": [[106.8, -6.2], [106.81, -6.21]]
    }
  }
}
```

## Batasan Project

- Kapasitas dan fasilitas adalah estimasi/proxy, bukan hasil survei lapangan.
- Akurasi rute bergantung pada kualitas data OpenStreetMap.
- Untuk ganti wilayah, OSM graph perlu dibuat ulang sesuai area start-destination.
- Project ini prototype UAS, bukan pengganti Google Maps production.
