# Product Requirements Document (PRD) - iMosque SafarRoute

Dokumen ini berisi persyaratan produk versi ringkas untuk fitur **Safar Mode iMosque** (Shortest Path and Multi-Objective Mosque Routing).

## 1. Deskripsi Produk
iMosque SafarRoute membantu pengguna dalam perjalanan (musafir) menemukan masjid terbaik di sepanjang rutenya berdasarkan kombinasi parameter waktu tempuh, jarak, kecocokan waktu salat, kapasitas masjid, dan prioritas rekomendasi AI/ML.

## 2. Fitur MVP
1. **Prayer-Aware Routing:** Menghitung waktu tiba di masjid dan membandingkannya dengan waktu salat guna menghindari keterlambatan.
2. **Multi-Objective Scoring:** Perhitungan rute tidak hanya berdasarkan jarak terpendek, melainkan pembobotan multi-kriteria.
3. **Komparasi Algoritma:** Membandingkan kinerja (latensi, memori, explored nodes) dari algoritma Dijkstra dan A*.
4. **Visualisasi Peta:** Me-render rute turn-by-turn pada peta berbasis OpenStreetMap & Leaflet.

## 3. Batasan Sistem
- Atribut masjid (kapasitas, fasilitas) bersifat estimasi/proxy dari AI/ML model.
- Keakuratan rute jalan bergantung pada kelengkapan data OpenStreetMap.
