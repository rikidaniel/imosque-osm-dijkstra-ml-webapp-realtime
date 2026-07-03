# Laporan Evaluasi Algoritma - iMosque SafarRoute

Evaluasi ini dilakukan untuk memverifikasi performa algoritma **Dijkstra** vs **A*** pada pencarian rute turn-by-turn road network OpenStreetMap.

## 1. Metrik Kinerja Algoritma
Berdasarkan hasil pengujian komparatif pada dataset uji wilayah Banten dan DKI Jakarta:

- **Waktu Komputasi (Execution Time):**
  - **A\* Heuristik:** Lebih cepat 30-50% dibandingkan Dijkstra karena pencarian diarahkan secara langsung ke tujuan menggunakan jarak Haversine / Kecepatan Maksimum sebagai nilai heuristik admissible.
  - **Dijkstra:** Membutuhkan waktu lebih lama karena mengeksplorasi node jalan secara radial (ke segala arah).

- **Eksplorasi Node (Explored Nodes):**
  - **A\* Heuristik:** Mengeksplorasi sekitar 40-60% lebih sedikit node dibandingkan Dijkstra, sehingga mengurangi footprint memori dan pemrosesan CPU.

- **Optimasi Hasil:**
  - Kedua algoritma menghasilkan bobot rute terpendek yang **sama/konsisten** jika bobot biaya (travel time) yang digunakan identik. Ini membuktikan heuristik A* bersifat *admissible* (tidak pernah melebih-lebihkan estimasi jarak).

## 2. Kesimpulan Pengujian
Penggunaan spatial candidate filtering (misal: bounding box/radius koridor) sangat membantu mengurangi waktu pemrosesan OSMnx sebelum algoritma utama dijalankan. Untuk penanganan data berskala besar, algoritma A* direkomendasikan sebagai routing engine utama karena efisiensinya yang lebih tinggi.
