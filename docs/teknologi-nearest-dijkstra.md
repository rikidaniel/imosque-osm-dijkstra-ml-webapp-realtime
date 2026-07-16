# Teknologi Deteksi Masjid Terdekat dan Routing Dijkstra

Dokumen ini menjelaskan teknologi yang benar-benar digunakan iMosque SafarRoute untuk dua pekerjaan berbeda:

1. mendeteksi masjid terdekat berdasarkan koordinat GPS dan jarak geografis; dan
2. menghitung rute jalan menuju masjid menggunakan graph OpenStreetMap dan Dijkstra.

Keduanya tidak sama. Pencarian nearest bekerja pada dokumen masjid di ArangoDB, sedangkan Dijkstra bekerja pada node dan edge jaringan jalan lokal. Masjid terdekat secara garis lurus belum tentu menjadi masjid tercepat melalui jalan.

## Ringkasan teknologi

| Lapisan | Teknologi | Peran |
|---|---|---|
| Lokasi pengguna | Browser Geolocation API | Mengambil latitude/longitude perangkat dengan izin pengguna |
| Antarmuka | Next.js, React, TypeScript | Mengelola state GPS, request, cancellation, cache, dan card rute |
| Peta | Leaflet dan React-Leaflet | Menampilkan marker masjid serta polyline rute |
| REST API | FastAPI dan Pydantic | Endpoint nearest/routing, validasi koordinat, radius, limit, dan algoritma |
| Database | ArangoDB | Menyimpan 233 ribu lebih dokumen masjid dan metadata dataset |
| Query geospasial | GeoJSON index dan AQL `GEO_DISTANCE` | Menemukan masjid terdekat tanpa memindai seluruh collection |
| Data jalan | OpenStreetMap dan OSMnx | Mengunduh dan membentuk road network sesuai wilayah dataset |
| Struktur graph | NetworkX `DiGraph` | Menyimpan node, edge, arah jalan, jarak, dan waktu tempuh |
| Spatial snapping | Shapely `STRtree` | Memproyeksikan GPS dan masjid ke edge jalan yang dapat dilalui |
| Komputasi numerik | NumPy | Menyaring kandidat dan menghitung jarak secara vectorized |
| Fallback node index | scikit-learn `BallTree`/spatial tree | Mencari node terdekat ketika jalur snapping edge memerlukan fallback |
| Shortest path | Dijkstra bidirectional dan multi-target | Menghitung jalur berbobot terpendek menuju kandidat masjid |
| Alternatif algoritma | NetworkX A* | Mematerialisasi rute final ketika pengguna memilih A* |
| Cache persisten | GraphML, runtime pickle, dan edge-index pickle | Menghindari download, parse graph, dan build STRtree berulang |
| Cache memori | `OrderedDict`, LRU, TTL, dan singleflight lock | Memakai ulang nearest, kandidat, graph, snapping, dan rute |
| Payload | Google Encoded Polyline 5 dan GZip | Memperkecil geometry rute untuk jaringan lambat |
| Fallback eksternal | OSRM public API melalui `requests.Session` | Menjaga routing tetap tersedia ketika graph lokal belum siap/tidak terhubung |

## 1. Teknologi deteksi masjid terdekat

### Browser Geolocation API

Frontend memakai `navigator.geolocation.getCurrentPosition()` dan Permissions API untuk meminta lokasi perangkat. Koordinat yang dihasilkan berbentuk WGS84:

```text
latitude  = -6.2001
longitude = 106.8166
```

Frontend kemudian mengirim lokasi ke:

```http
POST /api/v1/nearest-mosques
Content-Type: application/json
```

Request lama dibatalkan menggunakan `AbortController` ketika GPS atau parameter pencarian berubah. Client saat ini memakai timeout 12 detik dan satu retry hanya untuk kegagalan jaringan sementara.

### FastAPI dan Pydantic

FastAPI menerima request, sedangkan Pydantic membatasi input:

| Field | Validasi |
|---|---|
| `latitude` | -90 sampai 90 |
| `longitude` | -180 sampai 180 |
| `radius_km` | 0,5 sampai 200 km |
| `limit` | 1 sampai 50 |
| `dataset_id` | ID dataset atau `all` untuk lintas dataset |

Validasi ini mencegah query tidak masuk akal dan membatasi ukuran response.

### ArangoDB GeoJSON index

Setiap dokumen masjid menyimpan koordinat GeoJSON dalam urutan:

```json
{
  "coordinate": [106.8166, -6.2001]
}
```

Urutannya adalah `[longitude, latitude]`, bukan sebaliknya. Saat startup, backend memastikan collection `Mosque` memiliki:

- persistent index pada `dataset_id`;
- persistent index gabungan `dataset_id + id`;
- persistent index pada `id`; dan
- GeoJSON index pada `coordinate`.

Index geo yang salah format pada field yang sama diganti secara aman dan hasil pembuatan index diverifikasi kembali.

### AQL `GEO_DISTANCE`

Repository menjalankan satu query terikat radius. Secara konseptual:

```aql
FOR m IN Mosque
  FILTER m.dataset_id == @dataset_id
  LET distance_m = GEO_DISTANCE([@lon, @lat], m.coordinate)
  FILTER distance_m <= @radius_m
  SORT distance_m ASC
  LIMIT @limit
  RETURN { ...m, distance_km: distance_m / 1000 }
```

Untuk pencarian `all`, filter dataset dilewati tetapi geo index, radius, pengurutan, dan limit tetap digunakan. Backend tidak menjalankan radius bertingkat berulang; satu radius maksimum yang diminta menghasilkan satu query.

### Cache dan singleflight nearest

Hasil nearest disimpan di RAM selama 30 detik, maksimum 512 entry. Cache key menggunakan:

- dataset;
- latitude dan longitude yang dibulatkan empat desimal;
- radius yang dibulatkan 0,1 km; dan
- limit.

Request identik yang datang bersamaan memakai lock singleflight. Satu request menjadi pemilik query database, request lain menunggu hasilnya. Cache dihapus saat data masjid berubah.

### Alur deteksi nearest

```text
Browser GPS
  -> validasi Pydantic
  -> cek TTL cache
  -> singleflight
  -> ArangoDB GeoJSON index
  -> GEO_DISTANCE + radius + SORT + LIMIT
  -> JSON masjid terdekat
  -> cache frontend 30 detik
  -> marker Leaflet
```

## 2. Teknologi routing Dijkstra

### OpenStreetMap dan OSMnx

Road network berasal dari OpenStreetMap, bukan Google Maps. OSMnx mengunduh jaringan sesuai bounding box/koridor dan `network_type`:

- `drive`;
- `walk`;
- `bike`; atau
- `all`.

Hasil build disimpan sebagai GraphML per dataset di `data/osm_cache/`. Build dilakukan melalui endpoint admin agar request pengguna tidak menunggu Overpass.

### NetworkX graph

Graph OSM dipadatkan menjadi directed graph. Atribut runtime utama:

| Entitas | Atribut penting |
|---|---|
| Node | ID, longitude `x`, latitude `y` |
| Edge | `length`, `travel_time`, geometry, arah jalan |

Parallel edge diringkas ke edge terbaik yang diperlukan routing. Bobot utama Dijkstra adalah `travel_time`; jarak edge tetap disimpan untuk ringkasan rute.

### GraphML dan runtime binary cache

GraphML tetap menjadi sumber data persisten. Untuk mempercepat cold load berikutnya, backend membuat:

```text
road_graph_<dataset>.graphml
road_graph_<dataset>.graphml.runtime.pkl
road_graph_<dataset>.graphml.edges.pkl
```

`runtime.pkl` menyimpan graph yang sudah diparse dan dipadatkan. `edges.pkl` menyimpan data immutable untuk membangun kembali STRtree. Keduanya hanya dipakai jika fingerprint ukuran dan waktu modifikasi GraphML masih cocok.

Graph yang sudah dimuat disimpan dalam LRU memory cache. Jumlah default graph per proses adalah satu karena graph wilayah besar dapat memakai RAM dalam jumlah besar.

### Shapely STRtree dan edge projection

Koordinat GPS tidak selalu tepat berada pada node jalan. Karena itu sistem tidak hanya mengambil node terdekat. Shapely `STRtree` mencari edge fisik terdekat, lalu titik diproyeksikan ke geometry edge.

Hasil snapping menyimpan:

- edge asal dan tujuan;
- posisi proyeksi pada edge;
- connector dari GPS ke jalan; dan
- beberapa kandidat edge jika edge pertama tidak menghasilkan graph terhubung.

Pendekatan ini mengurangi rute memutar yang muncul ketika GPS dipaksa ke node yang salah. Hasil edge snap dicache maksimum 20.000 entry per graph.

### NumPy candidate preselection

Sebelum Dijkstra, ribuan masjid diperkecil menjadi kandidat terbatas. NumPy melakukan perhitungan vectorized untuk:

- jarak Haversine dari origin;
- jarak dari destination;
- kedekatan ke koridor perjalanan;
- skor prioritas awal; dan
- stable sorting kandidat.

Backend juga dapat menyimpan snapshot kandidat per `dataset_id + data_revision`. Ini mencegah pembacaan dan normalisasi seluruh dataset pada setiap request.

### Dijkstra multi-target

Dijkstra mencari jarak minimum pada graph berbobot non-negatif. Kompleksitas umumnya:

```text
O((V + E) log V)
```

dengan priority queue, tetapi implementasi iMosque tidak selalu menelusuri seluruh graph. Pencarian berhenti setelah target kandidat yang diperlukan sudah diselesaikan.

Optimasi yang digunakan:

- multi-source untuk beberapa kemungkinan hasil snapping edge;
- multi-target untuk menghitung banyak kandidat dalam satu traversal;
- traversal dari origin dan dari destination pada reversed graph;
- bidirectional Dijkstra untuk rute satu source-target; dan
- reuse hasil batch ketika algoritma final adalah Dijkstra.

Jika pengguna memilih A*, ranking kandidat tetap memakai batch Dijkstra, kemudian A* mematerialisasi geometry kandidat final menggunakan heuristic geografis.

### Pembobotan dan scoring

Dijkstra menentukan biaya jalan terpendek. Sesudah itu, kandidat masjid dapat diranking menggunakan profil:

- `fastest`;
- `prayer_priority`;
- `low_cost`; dan
- `balanced`.

Scoring mempertimbangkan waktu tempuh, jarak, penalti keterlambatan salat, biaya proxy, kapasitas/priority score, dan atribut enrichment. Machine learning membantu melengkapi atribut masjid, tetapi tidak menggantikan algoritma shortest path.

### Geometry, Polyline 5, dan GZip

Setelah path ditemukan, geometry edge digabung dan disederhanakan. Response compact mengirim:

- `encoded_polyline` atau `encoded_polylines`;
- ringkasan jarak dan waktu;
- connector snapping; dan
- diagnosis algoritma/cache.

Frontend mendekode Google Encoded Polyline precision 5 dan menggambarnya dengan komponen `Polyline` Leaflet. FastAPI mengaktifkan GZip untuk response minimal 1.000 byte.

### OSRM sebagai fallback

OSRM public API bukan algoritma utama aplikasi. Fallback dipakai jika:

- GraphML lokal belum tersedia;
- graph sedang dipanaskan;
- origin/destination berada di luar cakupan graph;
- edge snapping gagal; atau
- graph lokal tidak mempunyai jalur terhubung.

Response membedakan `routing_mode`, `graph_source`, dan `used_osrm_fallback`, sehingga frontend/operator dapat mengetahui apakah rute berasal dari Dijkstra lokal atau layanan eksternal.

### Alur routing Dijkstra

```text
Origin GPS + destination + dataset
  -> cache recommendation/route
  -> kandidat masjid revision-aware
  -> NumPy corridor preselection
  -> GraphML/runtime graph dari RAM
  -> STRtree edge snapping
  -> Dijkstra multi-source/multi-target
  -> scoring profil dan pilih masjid
  -> Dijkstra bidirectional/final path
  -> gabung + simplifikasi geometry
  -> Polyline 5 + GZip
  -> React-Leaflet
```

## 3. Mengapa kombinasi ini cepat

| Masalah mahal | Teknologi/solusi |
|---|---|
| Mencari di 233 ribu lebih masjid | ArangoDB GeoJSON index, radius, dan `LIMIT` |
| Request GPS berulang | TTL cache frontend/backend |
| Request identik bersamaan | Singleflight lock ber-shard |
| Parse GraphML besar | Runtime pickle ber-fingerprint |
| Membangun spatial index | Persistent edge-index pickle dan prewarm |
| Mencari edge terdekat | STRtree, bukan linear scan |
| Terlalu banyak kandidat masjid | Query koridor, snapshot revision-aware, dan NumPy |
| Menjalankan shortest path per masjid | Dijkstra multi-target dalam satu traversal |
| Geometry response besar | Simplifikasi, Polyline 5, compact response, dan GZip |
| Graph lokal belum siap | OSRM fallback tanpa menahan request pada cold load |

## 4. Versi teknologi utama

Versi mengikuti `backend/requirements.txt` dan `frontend/package.json` saat dokumen ini dibuat.

| Teknologi | Versi |
|---|---:|
| FastAPI | 0.115.6 |
| Uvicorn | 0.32.1 |
| python-arango | 7.9.0 |
| OSMnx | 2.0.1 |
| NetworkX | 3.4.2 |
| NumPy | 2.1.3 |
| Shapely | 2.0.6 |
| scikit-learn | 1.5.2 |
| GeoPandas | 1.0.1 |
| PyProj | 3.7.0 |
| Next.js | 16.2.10 |
| React | 19.2.4 |
| Leaflet | 1.9.4 |
| React-Leaflet | 5.0.0 |
| ArangoDB Docker image | 3.11.8 |

## 5. Lokasi implementasi

| Bagian | File |
|---|---|
| Endpoint nearest/routing | `backend/app/interfaces/api/routes.py` |
| Validasi request | `backend/app/domain/models/schemas.py` |
| Index database | `backend/app/infrastructure/database/arangodb_client.py` |
| AQL nearest/corridor | `backend/app/infrastructure/database/arangodb_repo.py` |
| TTL cache nearest | `backend/app/use_cases/dataset_usecases.py` |
| Candidate snapshot dan recommendation cache | `backend/app/use_cases/routing_usecases.py` |
| Build/load graph, STRtree, dan Dijkstra/A* | `backend/app/infrastructure/services/osm_graph.py` |
| Preselection, scoring, geometry, dan fallback OSRM | `backend/app/infrastructure/services/routing_osm.py` |
| Prewarm dan GZip | `backend/app/main.py` |
| GPS, timeout, cancellation, dan decoding polyline | `frontend/src/components/SafarDashboard.tsx` |
| API client/cache frontend | `frontend/src/lib/api.ts` |
| Render marker dan rute | `frontend/src/components/map/MapComponent.tsx` |

## 6. Batasan penting

- Geolocation browser memerlukan izin pengguna dan pada deployment web umumnya membutuhkan HTTPS.
- Hasil nearest memakai jarak geografis; akses jalan baru diperhitungkan saat routing.
- Akurasi rute bergantung pada kelengkapan dan arah jalan OpenStreetMap.
- OSRM publik tidak mempunyai SLA untuk aplikasi ini dan sebaiknya diganti/dihost sendiri untuk produksi.
- Cache in-memory tidak dibagi antarworker. Setiap worker dapat memuat graph sendiri dan memakai RAM tambahan.
- Build graph sebaiknya tidak dilakukan dalam request interaktif; gunakan endpoint admin dan prewarm.
- Atribut ML seperti fasilitas, kapasitas, rating hasil prediksi, dan priority score adalah proxy, bukan verifikasi lapangan.
