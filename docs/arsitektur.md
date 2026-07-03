# Arsitektur Sistem

## Alur Baru: Realtime Multi-Dataset

```text
Frontend Leaflet
  ├─ upload CSV dataset wilayah
  ├─ pilih dataset aktif
  ├─ load marker dari JSON enrichment
  └─ request routing Dijkstra/A*
        ↓
FastAPI Backend
  ├─ /api/datasets/upload
  ├─ /api/datasets/active
  ├─ /api/pipeline/run
  ├─ /api/mosques
  └─ /api/route
        ↓
ML Enrichment Pipeline
  ├─ cleaning koordinat
  ├─ rating prediction
  ├─ facilities prediction
  ├─ capacity proxy
  ├─ priority score
  └─ enriched_mosques.json
        ↓
OpenStreetMap Routing
  ├─ download road graph via OSMnx
  ├─ snap start/end/mosque to nearest road node
  ├─ Dijkstra/A* via NetworkX
  └─ output route GeoJSON
```

## Penyimpanan Dataset

Setiap dataset disimpan terpisah:

```text
data/raw/datasets/<dataset_id>.csv
data/processed/<dataset_id>/enriched_mosques.json
data/processed/<dataset_id>/data_profile_summary.json
```

Dataset aktif disimpan pada:

```text
data/processed/active_dataset.json
```

## Catatan Akademik

- Dataset dapat diganti real-time tanpa restart backend.
- ML enrichment dilakukan ulang setiap upload dataset baru.
- Routing tetap menggunakan graph jalan OpenStreetMap, bukan proximity graph antar masjid.
- Atribut hasil ML/proxy harus dijelaskan sebagai estimasi, bukan fakta lapangan.
