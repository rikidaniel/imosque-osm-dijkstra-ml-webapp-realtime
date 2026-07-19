# Laporan Evaluasi Routing Kelompok 2

Evaluasi ini mengukur implementasi production Dijkstra bidirectional dan A* dengan
heuristik konsisten pada graph jalan OpenStreetMap yang sama. Semua angka di bawah
berasal dari output mentah yang disimpan di repository, bukan estimasi teoritis.

## Metodologi

- Graph: DKI Jakarta, 196.565 node dan 456.186 edge.
- Bobot shortest path: `travel_time` dalam detik.
- Graph sudah warm agar waktu parsing GraphML tidak tercampur dengan waktu algoritma.
- Pasangan origin-destination diambil dari strongly connected component terbesar.
- Sampling memakai seed tetap `20260718` dan jarak lurus 3–35 km.
- Setiap pasangan dijalankan dengan Dijkstra dan A* pada node serta graph identik.
- Metrik: waktu, memori, expanded nodes, examined edges, panjang rute, dan biaya optimal.
- Sepuluh pasangan tambahan dibandingkan dengan OSRM sebagai referensi independen.

Perintah reproduksi:

```powershell
$env:PYTHONPATH='.'
python scripts/evaluate_routing_algorithms.py `
  --graph data/osm_cache/road_graph_dataset_masjid_imosque_table_mosque_dki_jakarta_1.graphml `
  --pairs 20 --seed 20260718 --min-straight-km 3 --max-straight-km 35 `
  --output docs/evaluation-results/dki-routing-20-pairs.json
```

Validasi OSRM dapat diulang dengan menambahkan:

```text
--osrm-base-url https://router.project-osrm.org/route/v1/driving
```

## Hasil 20 pasangan OD

| Metrik | Dijkstra | A* |
|---|---:|---:|
| Waktu rata-rata | 348,88 ms | 581,24 ms |
| Waktu median | 328,87 ms | 548,95 ms |
| Waktu p95 | 737,69 ms | 1.192,82 ms |
| Memori median | 30.049,41 KB | 11.097,89 KB |
| Expanded nodes rata-rata | 35.806,75 | 43.156,25 |
| Examined edges rata-rata | 83.175,25 | 99.296,85 |
| Jarak rute rata-rata | 23,83 km | 23,83 km |

- Dijkstra lebih cepat pada 20 dari 20 pasangan.
- A* menggunakan memori median sekitar 63% lebih rendah.
- Biaya shortest path identik pada 20 dari 20 pasangan (`100% optimal_cost_match`).
- Selisih biaya maksimum adalah 0 detik.

Hasil ini membatalkan asumsi lama bahwa A* selalu 30–50% lebih cepat. Heuristik
tetap benar dan admissible, tetapi bound kecepatan global yang konservatif membuat
arah pencarian kurang kuat pada graph DKI. Karena Dijkstra yang dipakai bersifat
bidirectional, ia dapat lebih cepat daripada A* satu arah untuk workload ini.

## Validasi terhadap OSRM

Pada sepuluh pasangan dengan seed dan batas jarak yang sama:

- seluruh biaya Dijkstra dan A* tetap identik;
- MAPE jarak terhadap OSRM: 6,349%;
- MAPE durasi terhadap OSRM: 12,983%.

Perbedaan terhadap OSRM tidak otomatis berarti local graph salah. Artifact OSM,
profil kecepatan, snapping, serta waktu pembaruan graph dapat berbeda. Angka ini
adalah baseline terukur yang dapat dipakai untuk membandingkan perbaikan berikutnya.

## Model multi-objective dan biaya rupiah

Kandidat masjid diranking menggunakan lima komponen:

1. waktu tempuh;
2. estimasi biaya rupiah;
3. penalti kedatangan terhadap waktu salat;
4. penalti kapasitas;
5. priority score masjid.

Estimasi biaya menggunakan rumus:

```text
total = (jarak / efisiensi_km_per_liter × harga_bbm)
      + (jarak × biaya_operasional_per_km)
      + (jarak_tol × tarif_tol_per_km)
```

Default eksperimen adalah harga BBM Rp10.000/liter, efisiensi 12 km/liter,
operasional Rp300/km, dan tol Rp1.000/km. Nilai tersebut adalah asumsi transparan,
bukan harga resmi, dan dapat dioverride melalui `cost_parameters` pada API.

## Artifact hasil

- `evaluation-results/dki-routing-20-pairs.json`: hasil lengkap 20 pasangan.
- `evaluation-results/dki-routing-20-pairs.csv`: tabel mentah untuk analisis.
- `evaluation-results/dki-routing-10-pairs-osrm.json`: validasi OSRM lengkap.
- `evaluation-results/dki-routing-10-pairs-osrm.csv`: tabel validasi OSRM.

## Kesimpulan

Dijkstra dan A* sama-sama menghasilkan shortest path optimal. Untuk graph DKI yang
diuji, Dijkstra bidirectional adalah pilihan default tercepat, sedangkan A* memberi
trade-off penggunaan memori lebih rendah. Pemilihan algoritma harus berdasarkan
benchmark per graph/region, bukan klaim bahwa satu algoritma selalu menang.
