# iMosque OSM Dijkstra ML Web App

Project ini adalah prototype Safar Mode iMosque untuk mencari masjid terdekat dan menghitung rute perjalanan secara cepat di web.

Alur sistemnya:

```text
CSV dataset masjid per wilayah
-> upload / pilih dataset dari frontend
-> ML enrichment realtime
-> data masjid tersimpan di ArangoDB
-> marker masjid tampil di Leaflet
-> OpenStreetMap road graph dibaca dari cache GraphML
-> nearest-node index dipakai untuk snap titik
-> Dijkstra / A* multi-destination
-> encoded polyline / GeoJSON ke frontend
```

Versi ini mendukung ganti dataset lewat frontend tanpa restart backend. Jadi anggota kelompok bisa upload CSV masing-masing, lalu memilih dataset aktif langsung dari UI.

## Fitur Utama

1. Dataset switcher realtime
   - Upload CSV baru dari frontend.
   - Dataset langsung diproses ML enrichment.
   - Dataset tersimpan di `data/raw/datasets/`.
   - Hasil enrichment tersimpan per dataset di `data/processed/<dataset_id>/enriched_mosques.json`.
   - Marker peta otomatis berubah sesuai dataset aktif.

2. AI/ML enrichment
   - Cleaning latitude-longitude.
   - Filter koordinat umum Indonesia dan bounds provinsi.
   - Prediksi `rating` kosong memakai Random Forest Regressor.
   - Prediksi `facilities` kosong memakai TF-IDF dan One-vs-Rest Logistic Regression.
   - Membuat `capacity_proxy`, `priority_score`, dan `tier`.

3. Routing OpenStreetMap
   - Road network diambil dari OpenStreetMap via OSMnx.
   - Graph disimpan sebagai GraphML per dataset.
   - Titik awal, tujuan, dan masjid kandidat di-snap ke node jalan terdekat.
   - Dijkstra/A* multi-destination dijalankan hanya untuk kandidat yang dibutuhkan.
   - Rute dikirim sebagai polyline ter-encode atau GeoJSON ringkas.

4. Multi-objective scoring
   - Waktu tempuh.
   - Jarak tempuh.
   - Kesesuaian waktu salat/adzan.
   - Kapasitas proxy.
   - Priority score hasil enrichment.

5. Optimasi performa
   - Cache GraphML in-memory.
   - Spatial index nearest-node dibangun sekali lalu dipakai ulang.
   - Singleflight untuk mencegah request rute ganda saat tombol ditekan berulang.
   - Cache hasil route sementara.
   - GZip response untuk payload API.
   - Compact response dengan encoded polyline agar hemat bandwidth.
   - Service worker frontend membantu cache tile peta dan aset statis.

## Teknologi yang Dipakai

- Frontend: Next.js, React, Leaflet
- Backend: FastAPI, Uvicorn
- Database: ArangoDB
- Graph routing: OSMnx, NetworkX, SciPy, scikit-learn
- Enrichment: pandas, numpy, joblib, TF-IDF, Random Forest, Logistic Regression
- Peta: OpenStreetMap tiles

## Catatan Jujur Akademik

Atribut hasil ML seperti `facilities`, `capacity_proxy`, `priority_score`, dan `tier` adalah estimasi/proxy, bukan data lapangan terverifikasi. Road network berasal dari OpenStreetMap, bukan Google Maps.

Kalimat aman untuk laporan:

> Sistem menggunakan AI/ML enrichment untuk melengkapi atribut pendukung routing, lalu melakukan pencarian rute menggunakan algoritma Dijkstra atau A* pada graph jalan OpenStreetMap. Atribut hasil enrichment diperlakukan sebagai proxy estimation, bukan fakta lapangan aktual.

## Struktur Project

```text
imosque-osm-dijkstra-ml-webapp/
|-- backend/
|   |-- requirements.txt
|   `-- app/
|       |-- main.py
|       |-- ml_enrichment.py
|       |-- osm_graph.py
|       |-- routing_osm.py
|       `-- schemas.py
|-- frontend/
|   |-- index.html
|   |-- app.js
|   `-- style.css
|-- scripts/
|   |-- run_ml_pipeline.py
|   |-- build_osm_graph.py
|   `-- test_route_request.py
|-- data/
|   |-- raw/
|   |-- processed/
|   `-- osm_cache/
|-- docs/
|-- outputs/
|-- start_backend.bat
|-- start_frontend.bat
`-- run_ml_pipeline.bat
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

Kalau dataset masih di Google Sheets, lakukan:

```text
File > Download > Comma Separated Values (.csv)
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
2. Pada bagian Dataset Realtime, pilih file CSV.
3. Isi nama dataset, contoh:

```text
dki_jakarta
jawa_barat
jawa_tengah
jawa_timur
```

4. Klik Upload + Proses ML.
5. Setelah selesai, marker peta akan berpindah ke dataset baru.
6. Untuk kembali ke dataset lain, pilih dari dropdown Dataset aktif, lalu klik Gunakan Dataset.

## Cara Routing

1. Pilih dataset aktif.
2. Klik Load Marker Masjid.
3. Klik Set Start, lalu klik titik awal di peta.
4. Klik Set Destination, lalu klik titik tujuan di peta.
5. Centang Auto-build/rebuild OSM graph saat routing hanya jika graph untuk area itu belum tersedia.
6. Klik Cari Rute Dijkstra atau Cari Rute A*.

Catatan: build OSM butuh internet dan bisa memakan waktu tergantung luas buffer area.

## Endpoint API Penting

Semua endpoint tersedia di `docs/api.md`. Yang paling sering dipakai:

- `GET /api/v1/health`
- `GET /api/v1/datasets`
- `POST /api/v1/datasets/upload`
- `POST /api/v1/datasets/active`
- `POST /api/v1/pipeline/run?dataset_id=<id>`
- `GET /api/v1/profile?dataset_id=<id>`
- `GET /api/v1/mosques?dataset_id=<id>&limit=3000`
- `POST /api/v1/nearest-mosques`
- `GET /api/v1/prayer-times`
- `POST /api/v1/routes/recommend`
- `POST /api/v1/route/to-mosque`
- `POST /api/v1/routes/benchmark`
- `GET /api/v1/routing-profiles`
- `POST /api/v1/osm/build-bbox`
- `POST /api/v1/osm/build-route`
- `POST /api/v1/osm/build-all`
- `GET /api/v1/osm/build-all/status`
- `POST /api/v1/user-settings`
- `GET /api/v1/user-settings/{user_id}`

## Kenapa Aplikasi Ini Lebih Cepat

1. Graph OSM dibangun sekali lalu disimpan sebagai GraphML.
2. Graph dibaca lewat cache in-memory sehingga parse file besar tidak diulang di setiap request.
3. Nearest-node index dibangun sekali dan dipakai untuk banyak query.
4. Routing memakai multi-destination Dijkstra/A* sehingga pencarian berhenti setelah kandidat yang diperlukan ditemukan.
5. Payload API diperkecil dengan encoded polyline dan compact response.
6. Frontend menyalakan GZip dan service worker untuk mengurangi beban jaringan.
7. Waktu salat dihitung lokal, jadi tidak tergantung API eksternal yang lambat.

## Contoh Request Routing

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
  "buffer_km": 6
}
```

## Batasan Project

- Kapasitas dan fasilitas adalah estimasi/proxy, bukan hasil survei lapangan.
- Akurasi rute bergantung pada kualitas data OpenStreetMap.
- Untuk wilayah baru, graph OSM perlu dibangun atau di-refresh sesuai area data.
- Project ini prototype akademik, bukan pengganti Google Maps production.
