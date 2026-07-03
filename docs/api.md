# API Documentation - iMosque SafarRoute v1

Seluruh API menggunakan prefiks `/api/v1`.

## 1. Health Check
- **Endpoint:** `GET /api/v1/health`
- **Respons:**
  ```json
  {
    "status": "healthy",
    "graph_status": "connected",
    "version": "1.0.0",
    "active_dataset_id": "banten"
  }
  ```

## 2. Mencari Rekomendasi Rute
- **Endpoint:** `POST /api/v1/routes/recommend`
- **Request Body:**
  ```json
  {
    "origin": { "latitude": -6.2000, "longitude": 106.8166 },
    "destination": { "latitude": -6.2500, "longitude": 106.9000 },
    "departure_time": "2026-07-11T17:10:00+07:00",
    "prayer": "maghrib",
    "algorithm": "astar",
    "profile": "prayer_priority",
    "search_radius_km": 10,
    "maximum_results": 3
  }
  ```

## 3. Bandingkan Algoritma (Benchmark)
- **Endpoint:** `POST /api/v1/routes/benchmark`
- **Request Body:**
  ```json
  {
    "origin": { "latitude": -6.2000, "longitude": 106.8166 },
    "destination": { "latitude": -6.2500, "longitude": 106.9000 },
    "departure_time": "2026-07-11T17:10:00+07:00",
    "prayer": "maghrib",
    "search_radius_km": 10
  }
  ```
- **Respons:** Perbandingan execution time, explored nodes, memory usage, dan efisiensi A* vs Dijkstra.

## 4. Routing Profiles
- **Endpoint:** `GET /api/v1/routing-profiles`
- **Respons:** Daftar profil routing beserta bobot kriterianya.
