# Proposal Ringkas Kelompok 2

## Judul

**Realtime Multi-Dataset AI-Enriched Dijkstra Routing Berbasis OpenStreetMap untuk Safar Mode iMosque**

## Latar Belakang

Aplikasi iMosque membutuhkan fitur Safar Mode yang mampu merekomendasikan masjid dan rute perjalanan secara efisien. Dataset masjid tersedia dalam beberapa wilayah, seperti Banten, DKI Jakarta, Jawa Barat, Jawa Tengah, Jawa Timur, dan DI Yogyakarta. Agar sistem fleksibel, aplikasi perlu mendukung pergantian dataset wilayah secara real-time.

## Tujuan

1. Mengembangkan aplikasi web berbasis peta untuk menampilkan data masjid hasil enrichment.
2. Menyediakan fitur upload dan switch dataset CSV dari frontend.
3. Melakukan AI/ML enrichment untuk melengkapi atribut pendukung routing.
4. Mengimplementasikan algoritma Dijkstra/A* pada road network OpenStreetMap.
5. Menghasilkan rekomendasi rute Safar Mode berbasis multi-objective scoring.

## Metodologi

1. Dataset CSV dibersihkan dan divalidasi koordinatnya.
2. Atribut kosong seperti rating dan facilities dilengkapi menggunakan ML.
3. Sistem membuat atribut turunan seperti capacity_proxy, priority_score, dan tier.
4. Data hasil enrichment disimpan dalam JSON dan dikirim ke frontend.
5. Road graph diambil dari OpenStreetMap menggunakan OSMnx.
6. Dijkstra/A* digunakan untuk mencari rute start → masjid kandidat → destination.
7. Rekomendasi dipilih berdasarkan waktu, jarak, waktu shalat, kapasitas proxy, dan priority score.

## Output

- Backend FastAPI.
- Frontend Leaflet map.
- Upload/switch dataset realtime.
- Enriched JSON per dataset.
- Routing Dijkstra/A* berbasis OpenStreetMap.
- Rekomendasi masjid untuk Safar Mode.

## Batasan

Atribut kapasitas dan fasilitas adalah estimasi/proxy. Sistem belum menggunakan data traffic real-time, biaya tol aktual, atau kapasitas masjid aktual.
