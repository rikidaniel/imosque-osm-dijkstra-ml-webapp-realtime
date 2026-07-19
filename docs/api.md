# Dokumentasi API iMosque SafarRoute

Dokumentasi ini menjelaskan secara komprehensif seluruh antarmuka pemrograman aplikasi (API) pada platform **iMosque SafarRoute**, merujuk langsung pada implementasi rute FastAPI di [routes.py](file:///c:/Users/Riki%20Daniel/Documents/GitHub/imosque-osm-dijkstra-ml-webapp-realtime/backend/app/interfaces/api/routes.py) dan skema model Pydantic di [schemas.py](file:///c:/Users/Riki%20Daniel/Documents/GitHub/imosque-osm-dijkstra-ml-webapp-realtime/backend/app/domain/models/schemas.py).

---

## Daftar Isi

* [1. Informasi Umum](#1-informasi-umum)
* [2. Standar & Konvensi API](#2-standar--konvensi-api)
  * [2.1 Penanganan Dataset Aktif](#21-penanganan-dataset-aktif)
  * [2.2 Format Waktu & Koordinat Geografis](#22-format-waktu--koordinat-geografis)
  * [2.3 Pagination & Pembatasan Payload](#23-pagination--pembatasan-payload)
  * [2.4 Struktur & Format Respons Error](#24-struktur--format-respons-error)
* [3. Ringkasan Endpoint](#3-ringkasan-endpoint)
* [4. API Sistem](#4-api-sistem)
  * [4.1 GET `/health`](#41-get-health)
* [5. API Dataset & Pipeline Administrasi](#5-api-dataset--pipeline-administrasi)
  * [5.1 GET `/datasets`](#51-get-datasets)
  * [5.2 POST `/datasets/active`](#52-post-datasetsactive)
  * [5.3 POST `/datasets/upload`](#53-post-datasetsupload)
  * [5.4 POST `/pipeline/run`](#54-post-pipelinerun)
  * [5.5 GET `/datasets/status/{dataset_id}`](#55-get-datasetsstatusdataset_id)
  * [5.6 GET `/profile`](#56-get-profile)
  * [5.7 GET `/datasets/{dataset_id}/bbox`](#57-get-datasetsdataset_idbbox)
  * [5.8 DELETE `/datasets/{dataset_id}`](#58-delete-datasetsdataset_id)
* [6. API Manajemen Masjid](#6-api-manajemen-masjid)
  * [6.1 GET `/mosques`](#61-get-mosques)
  * [6.2 POST `/mosques/{dataset_id}`](#62-post-mosquesdataset_id)
  * [6.3 PUT `/mosques/{dataset_id}/{mosque_id}`](#63-put-mosquesdataset_idmosque_id)
  * [6.4 DELETE `/mosques/{dataset_id}/{mosque_id}`](#64-delete-mosquesdataset_idmosque_id)
  * [6.5 POST `/mosques/bulk-delete`](#65-post-mosquesbulk-delete)
* [7. API Pencarian & Layanan Salat](#7-api-pencarian--layanan-salat)
  * [7.1 GET `/mosques/search`](#71-get-mosquessearch)
  * [7.2 POST `/nearest-mosques`](#72-post-nearest-mosques)
  * [7.3 POST `/realtime/location`](#73-post-realtimelocation)
  * [7.4 GET `/prayer-times`](#74-get-prayer-times)
* [8. API Pencarian Rute (Routing)](#8-api-pencarian-rute-routing)
  * [8.1 POST `/route`](#81-post-route)
  * [8.2 POST `/route/to-mosque`](#82-post-routeto-mosque)
  * [8.3 POST `/routes/recommend`](#83-post-routesrecommend)
  * [8.4 POST `/routes/benchmark`](#84-post-routesbenchmark)
  * [8.5 GET `/routes/{route_id}`](#85-get-routesroute_id)
  * [8.6 GET `/routing-profiles`](#86-get-routing-profiles)
* [9. API Pengelolaan Graph OSM & Caching](#9-api-pengelolaan-graph-osm--caching)
  * [9.1 POST `/routing/prewarm`](#91-post-routingprewarm)
  * [9.2 GET `/routing/corridors/{graph_id}`](#92-get-routingcorridorsgraph_id)
  * [9.3 GET `/osm/status`](#93-get-osmstatus)
  * [9.4 POST `/osm/build-bbox`](#94-post-osmbuild-bbox)
  * [9.5 POST `/osm/build-route`](#95-post-osmbuild-route)
  * [9.6 POST `/osm/build-all`](#96-post-osmbuild-all)
  * [9.7 GET `/osm/build-all/status`](#97-get-osmbuild-allstatus)
  * [9.8 POST `/osm/build-all/cancel`](#98-post-osmbuild-allcancel)
* [10. API Pengaturan Pengguna (User Settings)](#10-api-pengaturan-pengguna-user-settings)
  * [10.1 POST `/user-settings`](#101-post-user-settings)
  * [10.2 GET `/user-settings/{user_id}`](#102-get-user-settingsuser_id)
  * [10.3 DELETE `/user-settings/{user_id}`](#103-delete-user-settingsuser_id)
* [11. Arsitektur Performa & Caching](#11-arsitektur-performa--caching)
* [12. Panduan Integrasi Klien Aman](#12-panduan-integrasi-klien-aman)

---

## 1. Informasi Umum

Platform iMosque menggunakan FastAPI sebagai server backend dengan spesifikasi dasar sebagai berikut:

| Parameter | Spesifikasi |
| :--- | :--- |
| **Base URL Lokal** | `http://127.0.0.1:8000/api/v1` |
| **Format Utama Data** | `application/json` |
| **Unggah Berkas/Form** | `multipart/form-data` |
| **Versi Aplikasi** | `4.0.0` |
| **Swagger UI (Docs)** | `http://127.0.0.1:8000/docs` |
| **ReDoc** | `http://127.0.0.1:8000/redoc` |
| **OpenAPI Spec (JSON)**| `http://127.0.0.1:8000/openapi.json` |

> [!WARNING]
> Endpoint mutasi data belum dilengkapi lapisan otentikasi bawaan. Hindari membuka port API ini secara langsung ke jaringan publik tanpa menambahkan *reverse proxy* (seperti Nginx) atau otentikasi API Key/OAuth.

---

## 2. Standar & Konvensi API

### 2.1 Penanganan Dataset Aktif
* Nama dataset yang dikirimkan melalui parameter akan otomatis dinormalisasi menjadi format *slug* huruf kecil (contoh: `"DKI Jakarta"` menjadi `"dki_jakarta"`).
* Jika `dataset_id` tidak disertakan pada parameter request, sistem secara otomatis akan menggunakan ID dari **Dataset Aktif** saat ini, kecuali pada endpoint `POST /nearest-mosques` di mana string kosong diperlakukan sebagai kata kunci `"all"` (lintas dataset).

### 2.2 Format Waktu & Koordinat Geografis
* **Tanggal**: Menggunakan format ISO `YYYY-MM-DD` (contoh: `2026-07-18`).
* **Waktu Sederhana**: Menggunakan format 24 jam `HH:MM` (contoh: `17:00`).
* **Format Timestamp**: Disarankan menggunakan standar ISO 8601 disertai zona waktu (contoh: `2026-07-18T17:00:00+07:00`).
* **Sistem Koordinat**: Menggunakan standar WGS84 (derajat desimal).
* **Format Geometri (Polyline)**: Pengkodean jalur rute menggunakan skema *Google Polyline* dengan presisi 5 desimal (`google_polyline5`).
* **GeoJSON**: Urutan koordinat mengikuti standar geografis `[longitude, latitude]`.

### 2.3 Pagination & Pembatasan Payload
* Layanan pengambilan data menggunakan skema pagination berbasis parameter `limit` dan `offset`.
* Radius pencarian spasial dibatasi secara ketat di sisi backend (maksimum 200 km) untuk menjamin stabilitas konsumsi memori dan kecepatan query spasial database.

### 2.4 Struktur & Format Respons Error
Sistem mengembalikan format standardisasi error JSON sebagai berikut:

```json
{
  "detail": "Pesan deskripsi kesalahan teknis."
}
```

Jika terjadi kegagalan validasi skema input (HTTP 422), FastAPI akan mengembalikan rincian lokasi input yang salah:

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

---

## 3. Ringkasan Endpoint

Aplikasi ini memiliki 29 rute aktif yang dikelompokkan berdasarkan fungsinya:

| Metode | Path | Deskripsi Singkat |
| :--- | :--- | :--- |
| **GET** | `/health` | Memeriksa status kesehatan backend & graph |
| **GET** | `/datasets` | Mengambil seluruh daftar dataset lokal |
| **POST** | `/datasets/active` | Mengubah dataset yang aktif |
| **POST** | `/datasets/upload` | Mengunggah CSV & memulai pipeline background |
| **POST** | `/pipeline/run` | Menjalankan ulang pipeline sinkron dari disk |
| **GET** | `/datasets/status/{dataset_id}` | Mengambil progres pemrosesan pipeline |
| **GET** | `/profile` | Mengambil profil pengayaan dataset |
| **GET** | `/datasets/{dataset_id}/bbox` | Mengambil koordinat Bounding Box dataset |
| **DELETE** | `/datasets/{dataset_id}` | Menghapus dataset beserta graph-nya |
| **GET** | `/mosques` | Mengambil daftar masjid (paginated) |
| **POST** | `/mosques/{dataset_id}` | Menambah satu masjid baru |
| **PUT** | `/mosques/{dataset_id}/{mosque_id}` | Memperbarui informasi masjid |
| **DELETE** | `/mosques/{dataset_id}/{mosque_id}` | Menghapus satu masjid |
| **POST** | `/mosques/bulk-delete` | Menghapus banyak masjid secara massal |
| **GET** | `/mosques/search` | Mencari masjid dengan indeks teks |
| **POST** | `/nearest-mosques` | Mencari masjid terdekat dari koordinat |
| **POST** | `/realtime/location` | Mengirimkan koordinat GPS pengguna ke stream Kafka |
| **GET** | `/prayer-times` | Menghitung jadwal shalat offline lokal |
| **POST** | `/route` | Perhitungan rute level rendah ke masjid terdekat |
| **POST** | `/route/to-mosque` | Perhitungan rute ke satu masjid spesifik |
| **POST** | `/routes/recommend` | Rekomendasi rute multi-objective (safar) |
| **POST** | `/routes/benchmark` | Komparasi real-time algoritma Dijkstra vs A* |
| **GET** | `/routes/{route_id}` | Mengambil data rute tiruan (stub) |
| **GET** | `/routing-profiles` | Mengambil bobot profil rute pencarian |
| **POST** | `/routing/prewarm` | Memuat graph regional / koridor ke memori |
| **GET** | `/routing/corridors/{graph_id}` | Memantau status pembuatan graph koridor |
| **GET** | `/osm/status` | Mengambil info file graph di disk |
| **POST** | `/osm/build-bbox` | Build graph secara kustom berdasarkan BBox |
| **POST** | `/osm/build-route` | Build graph koridor berdasarkan koordinat rute |
| **POST** | `/osm/build-all` | Build massal graph untuk semua dataset |
| **GET** | `/osm/build-all/status` | Memantau progres status build massal |
| **POST** | `/osm/build-all/cancel` | Membatalkan antrean build massal |
| **POST** | `/user-settings` | Menyimpan setelan pencarian pengguna |
| **GET** | `/user-settings/{user_id}` | Mengambil setelan pencarian pengguna |
| **DELETE** | `/user-settings/{user_id}` | Menghapus setelan pencarian pengguna |

---

## 4. API Sistem

### 4.1 GET `/health`
Memeriksa status operasional backend, ketersediaan data graph jalan default, dan status dataset aktif.

* **Metode**: `GET`
* **Path**: `/health`
* **Contoh Request**:
  ```bash
  curl http://127.0.0.1:8000/api/v1/health
  ```
* **Contoh Respons 200**:
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
      "nodes": 75652,
      "edges": 174439
    },
    "version": "4.0.0",
    "active_dataset_id": "dki_jakarta"
  }
  ```

---

## 5. API Dataset & Pipeline Administrasi

### 5.1 GET `/datasets`
Mengambil seluruh daftar dataset wilayah yang terdaftar di dalam database beserta status pemrosesannya.

* **Metode**: `GET`
* **Path**: `/datasets`
* **Contoh Respons 200**:
  ```json
  {
    "active_dataset_id": "dki_jakarta",
    "items": [
      {
        "_key": "dki_jakarta",
        "dataset_id": "dki_jakarta",
        "dataset_label": "DKI Jakarta",
        "processed": true,
        "processing_status": "completed",
        "progress_percent": 100,
        "mosque_count": 1245,
        "data_revision": 12,
        "is_active": true
      }
    ]
  }
  ```

### 5.2 POST `/datasets/active`
Mengatur dataset wilayah yang aktif untuk digunakan sebagai rujukan pencarian rute global.

* **Metode**: `POST`
* **Path**: `/datasets/active`
* **Tipe Content**: `application/x-www-form-urlencoded` atau `multipart/form-data`
* **Parameter Request**:
  | Field Form | Tipe | Wajib | Keterangan |
  | :--- | :--- | :--- | :--- |
  | `dataset_id` | string | Ya | ID dataset wilayah (contoh: `dki_jakarta`) |

* **Contoh Request**:
  ```bash
  curl -X POST http://127.0.0.1:8000/api/v1/datasets/active \
    -F "dataset_id=dki_jakarta"
  ```
* **Contoh Respons 200**:
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
    }
  }
  ```

### 5.3 POST `/datasets/upload`
Mengunggah berkas CSV mentah berisi data masjid baru dan memicu jalannya pipeline ETL (*Clean, Enrich, Ingest*) secara asinkron di latar belakang.

* **Metode**: `POST`
* **Path**: `/datasets/upload`
* **Tipe Content**: `multipart/form-data`
* **Parameter Request**:
  | Field Form | Tipe | Wajib | Default | Keterangan |
  | :--- | :--- | :--- | :--- | :--- |
  | `file` | file | Ya | - | Berkas harus berformat `.csv` |
  | `dataset_name` | string | Tidak | Nama file | Label nama wilayah |
  | `make_active` | boolean | Tidak | `true` | Otomatis aktifkan jika sukses |

* **Contoh Request**:
  ```bash
  curl -X POST http://127.0.0.1:8000/api/v1/datasets/upload \
    -F "file=@data/raw/dataset_masjid_banten.csv" \
    -F "dataset_name=Banten" \
    -F "make_active=true"
  ```
* **Contoh Respons 200**:
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

### 5.4 POST `/pipeline/run`
Menjalankan ulang pipeline pemrosesan data untuk berkas CSV yang sudah tersimpan di direktori lokal server.

* **Metode**: `POST`
* **Path**: `/pipeline/run`
* **Query Parameter**:
  | Parameter | Tipe | Wajib | Keterangan |
  | :--- | :--- | :--- | :--- |
  | `dataset_id` | string | Tidak | Default: Dataset Aktif |

* **Contoh Request**:
  ```bash
  curl -X POST "http://127.0.0.1:8000/api/v1/pipeline/run?dataset_id=banten"
  ```

### 5.5 GET `/datasets/status/{dataset_id}`
Memantau status progres pemrosesan pipeline ETL asinkron untuk dataset tertentu.

* **Metode**: `GET`
* **Path**: `/datasets/status/{dataset_id}`
* **Contoh Respons 200**:
  ```json
  {
    "dataset_id": "banten",
    "processed": true,
    "processing_status": "completed",
    "progress_percent": 100,
    "message": "Selesai!"
  }
  ```

### 5.6 GET `/profile`
Mengambil dokumen profil pengayaan machine learning (*enrichment*) dari dataset terkait.

* **Metode**: `GET`
* **Path**: `/profile`
* **Query Parameter**: `dataset_id` (opsional)

### 5.7 GET `/datasets/{dataset_id}/bbox`
Menghitung koordinat pembatas (*Bounding Box*) geografis dari persebaran seluruh masjid di dalam dataset dengan membuang koordinat pencilan (*outliers*) menggunakan metode IQR.

* **Metode**: `GET`
* **Path**: `/datasets/{dataset_id}/bbox`
* **Contoh Respons 200**:
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

### 5.8 DELETE `/datasets/{dataset_id}`
Menghapus seluruh rekaman data masjid, dokumen profil, CSV mentah, dan berkas graphml fisik dari sistem lokal.

* **Metode**: `DELETE`
* **Path**: `/datasets/{dataset_id}`

---

## 6. API Manajemen Masjid

### 6.1 GET `/mosques`
Mengambil daftar masjid di dalam dataset wilayah tertentu menggunakan pagination offset.

* **Metode**: `GET`
* **Path**: `/mosques`
* **Query Parameter**:
  | Parameter | Tipe | Wajib | Default | Batasan |
  | :--- | :--- | :--- | :--- | :--- |
  | `dataset_id` | string | Tidak | Dataset Aktif | - |
  | `limit` | integer | Tidak | 1000 | 1 s/d 30000 |
  | `offset` | integer | Tidak | 0 | >= 0 |
  | `kabko` | string | Tidak | - | Filter Kota/Kabupaten |

* **Contoh Request**:
  ```bash
  curl "http://127.0.0.1:8000/api/v1/mosques?dataset_id=dki_jakarta&limit=5&offset=0"
  ```

### 6.2 POST `/mosques/{dataset_id}`
Menambahkan data satu masjid baru secara manual ke dalam dataset terpilih.

* **Metode**: `POST`
* **Path**: `/mosques/{dataset_id}`
* **Format Body (JSON)**:
  ```json
  {
    "name": "Masjid Raya Baitussalam",
    "latitude": -6.2234,
    "longitude": 106.8456,
    "kecamatan": "Tebet",
    "kabko": "Jakarta Selatan",
    "provinsi": "DKI Jakarta",
    "address": "Jl. Tebet Barat Raya No.10",
    "fasilitas": "AC, parkir, toilet, wudu"
  }
  ```

### 6.3 PUT `/mosques/{dataset_id}/{mosque_id}`
Memperbarui data detail dari masjid yang sudah terdaftar di database.

* **Metode**: `PUT`
* **Path**: `/mosques/{dataset_id}/{mosque_id}`

### 6.4 DELETE `/mosques/{dataset_id}/{mosque_id}`
Menghapus satu masjid tertentu dari database.

* **Metode**: `DELETE`
* **Path**: `/mosques/{dataset_id}/{mosque_id}`

### 6.5 POST `/mosques/bulk-delete`
Menghapus banyak data masjid secara massal sekaligus untuk efisiensi transaksi query database.

* **Metode**: `POST`
* **Path**: `/mosques/bulk-delete`
* **Format Body (JSON)**:
  ```json
  {
    "dataset_id": "dki_jakarta",
    "mosque_ids": ["mosque_abc123", "mosque_xyz789"]
  }
  ```

---

## 7. API Pencarian & Layanan Salat

### 7.1 GET `/mosques/search`
Mencari masjid berdasarkan nama atau alamat menggunakan indeks teks penuh (*Full-Text Search*) database ArangoSearch tanpa batasan jarak spasial.

* **Metode**: `GET`
* **Path**: `/mosques/search`
* **Query Parameter**:
  | Parameter | Tipe | Wajib | Default | Keterangan |
  | :--- | :--- | :--- | :--- | :--- |
  | `q` | string | Ya | - | Kata kunci pencarian (min. 2 karakter) |
  | `dataset_id`| string | Tidak | `all` | Batasi pencarian ke dataset tertentu |
  | `limit` | integer| Tidak | 10 | Batasi jumlah hasil (1 s/d 20) |
  | `latitude`  | number | Tidak | - | Koordinat asal (untuk kalkulasi jarak) |
  | `longitude` | number | Tidak | - | Koordinat asal (untuk kalkulasi jarak) |

* **Contoh Request**:
  ```bash
  curl "http://127.0.0.1:8000/api/v1/mosques/search?q=nurul%20iman&latitude=-6.20&longitude=106.81"
  ```
* **Contoh Respons 200**:
  ```json
  {
    "dataset_id": "all",
    "query": "nurul iman",
    "total": 1,
    "items": [
      {
        "id": "mosque_8829",
        "dataset_id": "dki_jakarta",
        "name": "Masjid Nurul Iman",
        "latitude": -6.2112,
        "longitude": 106.8223,
        "distance_km": 1.45
      }
    ]
  }
  ```

### 7.2 POST `/nearest-mosques`
Mencari daftar masjid terdekat dari posisi geografis pengguna saat ini dalam radius lingkaran tertentu.

* **Metode**: `POST`
* **Path**: `/nearest-mosques`
* **Format Body (JSON)**:
  ```json
  {
    "latitude": -6.2088,
    "longitude": 106.8456,
    "dataset_id": "all",
    "limit": 5,
    "radius_km": 15.0
  }
  ```
* **Contoh Respons 200**:
  ```json
  {
    "dataset_id": "all",
    "origin": {
      "latitude": -6.2088,
      "longitude": 106.8456
    },
    "radius_km": 15.0,
    "search_radius_used_km": 15.0,
    "total": 1,
    "items": [
      {
        "id": "mosque_2291",
        "name": "Mushola Baiturrahmah",
        "latitude": -6.2150,
        "longitude": 106.8390,
        "distance_km": 1.02,
        "rating": 5.0,
        "facilities": "AC, parkir",
        "tier": "B"
      }
    ],
    "cache_hit": false
  }
  ```

### 7.3 POST `/realtime/location`
Mengirimkan koordinat lokasi terkini pengguna ke pipeline streaming Kafka untuk ingestion data realtime.

* **Metode**: `POST`
* **Path**: `/realtime/location`
* **Status Respons Sukses**: `202 Accepted`
* **Format Body (JSON)**:
  ```json
  {
    "user_id": "device_vpshvo_rm",
    "session_id": "trip_session_881",
    "latitude": -6.2088,
    "longitude": 106.8456,
    "occurred_at": "2026-07-18T17:00:00+07:00",
    "dataset_id": "dki_jakarta",
    "region_id": "dki-jakarta",
    "road_segment_id": "osm-way-99281",
    "speed_kph": 32.5,
    "heading_degrees": 180,
    "accuracy_m": 8
  }
  ```

> [!NOTE]
> Nilai `user_id` dan `session_id` akan secara otomatis dipseudonimkan menggunakan SHA-256 disertai salt rahasia (`IMOSQUE_EVENT_PSEUDONYM_SECRET`) sebelum diteruskan ke Kafka untuk melindungi privasi koordinat pengguna.

### 7.4 GET `/prayer-times`
Menghitung jadwal waktu shalat 5 waktu secara offline menggunakan algoritma astronomi Kementerian Agama RI.

* **Metode**: `GET`
* **Path**: `/prayer-times`
* **Query Parameter**:
  | Parameter | Tipe | Wajib | Keterangan |
  | :--- | :--- | :--- | :--- |
  | `latitude` | number | Ya | Koordinat lintang |
  | `longitude`| number | Ya | Koordinat bujur (untuk mendeteksi WIB/WITA/WIT) |
  | `date` | string | Tidak | Format `YYYY-MM-DD` (default: Tanggal Server) |

* **Contoh Request**:
  ```bash
  curl "http://127.0.0.1:8000/api/v1/prayer-times?latitude=-6.20&longitude=106.84"
  ```
* **Contoh Respons 200**:
  ```json
  {
    "source": "offline_kemenag_calculation",
    "date": "2026-07-18",
    "timezone": "Asia/Jakarta",
    "timings": {
      "Fajr": "04:45",
      "Dhuhr": "12:03",
      "Asr": "15:24",
      "Maghrib": "17:58",
      "Isha": "19:11"
    }
  }
  ```

---

## 8. API Pencarian Rute (Routing)

### 8.1 POST `/route`
Endpoint pencarian rute level rendah yang mengembalikan rute navigasi terdekat yang menghubungkan titik awal dan titik akhir.

* **Metode**: `POST`
* **Path**: `/route`
* **Format Body (JSON)**:
  ```json
  {
    "dataset_id": "dki_jakarta",
    "start_lat": -6.2088,
    "start_lon": 106.8456,
    "end_lat": -6.2150,
    "end_lon": 106.8390,
    "algorithm": "astar",
    "current_time": "17:00",
    "prayer_time": "17:58",
    "max_candidates": 3,
    "auto_build_osm": false,
    "buffer_km": 6.0
  }
  ```

### 8.2 POST `/route/to-mosque`
Menghitung rute perjalanan dari lokasi awal langsung ke penanda ID masjid tertentu.

* **Metode**: `POST`
* **Path**: `/route/to-mosque`

### 8.3 POST `/routes/recommend`
Endpoint utama pencarian rute multi-objective untuk merekomendasikan masjid terbaik di sepanjang rute perjalanan berdasarkan estimasi waktu tempuh dan sisa waktu adzan.

* **Metode**: `POST`
* **Path**: `/routes/recommend`
* **Format Body (JSON)**:
  ```json
  {
    "origin": {
      "latitude": -6.2088,
      "longitude": 106.8456
    },
    "destination": {
      "latitude": -6.2500,
      "longitude": 106.8800
    },
    "departure_time": "2026-07-18T17:00:00+07:00",
    "prayer": "maghrib",
    "algorithm": "astar",
    "profile": "balanced",
    "search_radius_km": 10.0,
    "maximum_results": 3,
    "auto_build_osm": false,
    "dataset_id": "dki_jakarta",
    "compact_response": true,
    "cost_parameters": {
      "fuel_price_per_liter": 10000,
      "fuel_efficiency_km_per_liter": 12,
      "operating_cost_per_km": 300,
      "toll_cost_per_km": 1000
    }
  }
  ```

`cost_parameters` bersifat opsional dan seluruh nilai menggunakan rupiah/km atau
rupiah/liter sesuai nama field. Profil `low_cost` meranking kandidat menggunakan
estimasi biaya BBM + operasional kendaraan + tol pada edge OSM bertanda `toll`.
Respons mengembalikan `route_summary.estimated_cost_rupiah` dan
`route_summary.cost_breakdown` agar asumsi perhitungan dapat diaudit.

### 8.4 POST `/routes/benchmark`
Membandingkan kecepatan komputasi dan konsumsi memori pathfinding secara langsung antara algoritma **Dijkstra** (bidirectional) dan **A\*** (heuristik konsisten) pada rute perjalanan yang sama.

* **Metode**: `POST`
* **Path**: `/routes/benchmark`
* **Format Body (JSON)**:
  ```json
  {
    "origin": {
      "latitude": -6.2088,
      "longitude": 106.8456
    },
    "destination": {
      "latitude": -6.2150,
      "longitude": 106.8390
    },
    "departure_time": "2026-07-18T17:00:00+07:00",
    "prayer": "maghrib",
    "search_radius_km": 10.0,
    "dataset_id": "dki_jakarta"
  }
  ```

#### Respons HTTP 202 (Proses Pengunduhan/Penyiapan Graph Koridor):
Jika koordinat rute yang diminta berada di luar area graph lokal yang aktif, backend akan mulai mengunduh BBox jalan yang melingkupi rute tersebut secara otomatis dan mengembalikan kode asinkron **202 Accepted**:

```json
{
  "status": "preparing_graph",
  "message": "Menyiapkan graph jalan lokal untuk wilayah rute. Benchmark akan dilanjutkan otomatis.",
  "corridor": {
    "graph_id": "dki_jakarta-c76b2e1a",
    "status": "building",
    "ready": false
  },
  "status_url": "/api/v1/routing/corridors/dki_jakarta-c76b2e1a",
  "retry_after_ms": 1500
}
```

#### Respons HTTP 200 (Benchmark Selesai):
```json
{
  "status": "success",
  "benchmark": {
    "dijkstra": {
      "algorithm": "Dijkstra (Bidirectional)",
      "execution_time_ms": 120.45,
      "expanded_nodes": 171,
      "explored_nodes": 171,
      "examined_edges": 462,
      "route_distance_km": 1.42,
      "route_travel_time_seconds": 284.1,
      "memory_usage_kb": 912.5
    },
    "astar": {
      "algorithm": "A* (Heuristik Konsisten)",
      "execution_time_ms": 75.12,
      "expanded_nodes": 68,
      "explored_nodes": 68,
      "examined_edges": 181,
      "heuristic_calls": 93,
      "route_distance_km": 1.42,
      "route_travel_time_seconds": 284.1,
      "memory_usage_kb": 640.2
    },
    "comparison": {
      "faster_algorithm": "A*",
      "time_difference_ms": 45.33,
      "efficiency_gain_percent": 37.6,
      "fewer_explored_algorithm": "A*",
      "explored_nodes_difference": 103,
      "optimal_cost_match": true
    },
    "measurement": {
      "scope": "shortest_path_only",
      "weight": "travel_time_seconds",
      "graph_warm": true,
      "cache_used": false,
      "graph_scope": "dataset_graph",
      "graph_path": "data/osm_cache/road_graph_dki_jakarta.graphml",
      "source_node": "399281",
      "target_node": "229104"
    }
  }
}
```

### 8.5 GET `/routes/{route_id}`
Mengambil data geometri GeoJSON dari rute aktif tertentu (saat ini masih berupa data tiruan/stub).

* **Metode**: `GET`
* **Path**: `/routes/{route_id}`

### 8.6 GET `/routing-profiles`
Mengambil daftar profil pencarian rute beserta bobot parameter perkaliannya (*scoring weights*).

* **Metode**: `GET`
* **Path**: `/routing-profiles`

---

## 9. API Pengelolaan Graph OSM & Caching

### 9.1 POST `/routing/prewarm`
Memicu pemuatan berkas graph jalan (.graphml) dan data masjid dari disk ke memori RAM secara asinkron agar request pencarian rute pertama berjalan instan.

* **Metode**: `POST`
* **Path**: `/routing/prewarm`
* **Status Respons**: `202 Accepted`
* **Query Parameter**:
  | Parameter | Tipe | Wajib | Keterangan |
  | :--- | :--- | :--- | :--- |
  | `dataset_id` | string | Ya | Dataset regional tujuan (nilai `all` ditolak) |
  | `start_lat`, `start_lon` | number | Tidak | Pasangan koordinat asal (untuk graph koridor) |
  | `end_lat`, `end_lon` | number | Tidak | Pasangan koordinat tujuan (untuk graph koridor) |
  | `buffer_km` | number | Tidak | Lebar radius koridor (default: 8.0) |

* **Contoh Request**:
  ```bash
  curl -X POST "http://127.0.0.1:8000/api/v1/routing/prewarm?dataset_id=dki_jakarta"
  ```

### 9.2 GET `/routing/corridors/{graph_id}`
Memeriksa status pembuatan graph koridor asinkron di latar belakang.

* **Metode**: `GET`
* **Path**: `/routing/corridors/{graph_id}`
* **Contoh Respons 200 (Selesai)**:
  ```json
  {
    "graph_id": "dki_jakarta-c76b2e1a",
    "status": "ready",
    "ready": true,
    "nodes": 4500,
    "edges": 12000,
    "graphml_path": "data/osm_cache/road_graph_corridor_dki_jakarta-c76b2e1a.graphml"
  }
  ```

### 9.3 GET `/osm/status`
Mengambil detail metadata dan lokasi berkas cache graphml fisik yang tersimpan di server.

* **Metode**: `GET`
* **Path**: `/osm/status`

### 9.4 POST `/osm/build-bbox`
Mengunduh jaringan jalan OpenStreetMap secara kustom berdasarkan batas koordinat segiempat (BBox) lalu menyimpannya sebagai graphml lokal.

* **Metode**: `POST`
* **Path**: `/osm/build-bbox`
* **Format Body (JSON)**:
  ```json
  {
    "dataset_id": "dki_jakarta",
    "north": -6.08,
    "south": -6.38,
    "east": 106.98,
    "west": 106.68,
    "network_type": "drive"
  }
  ```

### 9.5 POST `/osm/build-route`
Membangun graph jalan kustom lokal yang melingkupi garis lintasan perjalanan saja (corridor graph) untuk efisiensi.

* **Metode**: `POST`
* **Path**: `/osm/build-route`

### 9.6 POST `/osm/build-all`
Memicu antrean background job untuk mengunduh dan membangun graph jalan lokal secara serial untuk semua dataset masjid yang berstatus `processed`.

* **Metode**: `POST`
* **Path**: `/osm/build-all`
* **Format Body (JSON)**:
  ```json
  {
    "network_type": "drive",
    "force": false
  }
  ```

### 9.7 GET `/osm/build-all/status`
Memantau progres antrean status dari build graph massal yang sedang berjalan.

* **Metode**: `GET`
* **Path**: `/osm/build-all/status`

### 9.8 POST `/osm/build-all/cancel`
Mengirim instruksi pembatalan proses antrean build graph massal.

* **Metode**: `POST`
* **Path**: `/osm/build-all/cancel`

---

## 10. API Pengaturan Pengguna (User Settings)

### 10.1 POST `/user-settings`
Menyimpan atau menggabungkan (*merge*) konfigurasi pencarian rute dan alarm waktu shalat pengguna ke database berdasarkan ID perangkat.

* **Metode**: `POST`
* **Path**: `/user-settings`
* **Format Body (JSON)**:
  ```json
  {
    "user_id": "device_abc123",
    "search_settings": {
      "algorithm": "dijkstra",
      "profile": "balanced",
      "currentTime": "17:00",
      "prayer": "maghrib",
      "maxCandidates": "3",
      "bufferKm": "15.0",
      "autoBuild": false
    },
    "prayer_settings": {
      "schedule": [
        {
          "name": "Maghrib",
          "time": "17:58",
          "isAlarmActive": true
        }
      ],
      "hijriDate": "1 Safar 1448 H",
      "masehiDate": "18 Juli 2026"
    },
    "updated_at": "2026-07-18T17:00:00+07:00"
  }
  ```

> [!IMPORTANT]
> Elemen `user_id` wajib bernilai 3 s/d 128 karakter dan hanya boleh berisi pola ASCII `^[A-Za-z0-9_-]+$`. Elemen `maxCandidates` divalidasi ketat oleh regex backend dan hanya diperbolehkan bernilai antara string `"1"` sampai `"10"`.

### 10.2 GET `/user-settings/{user_id}`
Mengambil dokumen konfigurasi setelan yang disimpan oleh pengguna terkait.

* **Metode**: `GET`
* **Path**: `/user-settings/{user_id}`
* **Contoh Respons 200 (Jika Ditemukan)**:
  ```json
  {
    "status": "success",
    "user_id": "device_abc123",
    "data": {
      "search_settings": {
        "algorithm": "dijkstra",
        "profile": "balanced",
        "currentTime": "17:00",
        "prayer": "maghrib",
        "maxCandidates": "3",
        "bufferKm": "15.0",
        "autoBuild": false
      },
      "prayer_settings": {
        "schedule": [],
        "hijriDate": "1 Safar 1448 H",
        "masehiDate": "18 Juli 2026"
      }
    }
  }
  ```

### 10.3 DELETE `/user-settings/{user_id}`
Menghapus dokumen konfigurasi setelan milik pengguna dari database.

* **Metode**: `DELETE`
* **Path**: `/user-settings/{user_id}`

---

## 11. Arsitektur Performa & Caching

Untuk mempercepat kecepatan komputasi pencarian rute spasial, backend iMosque menerapkan lapisan caching bertingkat:

| Lapisan Caching | Durasi Cache | Kunci & Metode Invalidasi |
| :--- | :--- | :--- |
| **Nearest Cache** | 30 Detik | Koordinat GPS (dibulatkan 4 desimal), radius, dan limit. Dihapus otomatis jika data masjid pada dataset berubah. |
| **Kandidat Masjid** | Persisten | Menggunakan LRU cache untuk memori RAM. Di-refresh otomatis jika nilai `data_revision` berubah. |
| **Rekomendasi Rute** | 5 Menit | Kombinasi koordinat asal-tujuan, parameter waktu berangkat, profil rute, dan sidik jari graph. |
| **Selected Route** | 24 Jam | Kunci koordinat GPS asal-tujuan, ID masjid, dan tipe algoritma terpilih. |
| **Loaded Graph** | LRU Cache | Memuat berkas `.graphml` ke RAM. Menggunakan singleflight lock untuk mencegah pembacaan ganda. |
| **Edge Snapping** | Dinamis | Indeks geometri jalan (STRtree) yang disimpan ke berkas `*.edges.pkl` saat pertama kali graph selesai di-build. |

---

## 12. Panduan Integrasi Klien Aman

Bagi pengembang frontend/klien yang melakukan integrasi dengan API iMosque, sangat disarankan untuk menerapkan kebijakan berikut:
* **Mekanisme Polling Aman**: Saat memantau pemrosesan ETL atau pengunduhan graph, lakukan polling ke endpoint status dengan interval minimal **1 s/d 3 detik** untuk mencegah kemacetan server.
* **Optimasi Payload**: Use flag `"compact_response": true` pada request routing untuk menghemat bandwidth data seluler (memotong respons GeoJSON mentah dan hanya mengirimkan encoded polyline string).
* **Manajemen Timeout Klien**:
  * API Pencarian Masjid Terdekat: Set batas timeout klien sebesar **12 detik**.
  * API Jadwal Shalat: Set batas timeout klien sebesar **3 detik**.
  * API Navigasi Rute: Set batas timeout klien sebesar **15 detik** dan batalkan request yang lama jika pengguna mengubah koordinat tujuan baru secara cepat.
