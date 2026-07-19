# Rencana Eksekusi Skala Nasional

Dokumen ini adalah backlog migrasi operasional iMosque dari prototipe satu mesin
menjadi layanan nasional. Migrasi dilakukan bertahap dan setiap tahap harus bisa
di-rollback tanpa memindahkan data utama secara prematur.

## Keputusan arsitektur

- ArangoDB dipertahankan sampai benchmark workload iMosque membuktikan kandidat lain lebih baik.
- API publik dibuat stateless dan tidak memuat road graph jika routing worker remote aktif.
- Road graph dibagi per dataset/region dan dilayani routing worker yang dapat direplikasi.
- Event lokasi masuk melalui Kafka; Flink menghasilkan snapshot bobot lalu lintas per ruas.
- Kafka/Flink tidak ditempatkan pada jalur sinkron request rute.
- Compose skala di repository adalah referensi staging, bukan cluster production yang high-availability.

## Task backlog

| ID | Task | Status | Kriteria selesai |
|---|---|---|---|
| NS-001 | Gateway routing remote berdasarkan dataset/prefix | Selesai | Remote dispatch, token internal, timeout, fallback teruji |
| NS-002 | FastAPI regional routing worker | Selesai | Endpoint route, route-to-mosque, recommend, health tersedia |
| NS-003 | Mematikan graph prewarm pada API stateless | Selesai | API tidak memuat graph saat worker remote dikonfigurasi |
| NS-004 | Kontrak ingestion GPS Kafka | Selesai | Endpoint 202, partition key region, producer idempotent |
| NS-005 | Flink SQL agregasi 30 detik | Selesai awal | Source/sink Kafka dan output multiplier tersedia |
| NS-006 | Container backend/frontend/worker | Selesai | Image backend dan Next.js standalone dapat dibangun |
| NS-007 | Compose staging skala | Selesai | API, worker, Kafka, Flink, frontend tervalidasi oleh Compose |
| NS-008 | Load probe p50/p95/p99 | Selesai | Probe konkurensi menghasilkan JSON dan gagal pada error HTTP |
| NS-009 | Map matching event GPS ke OSM edge | Belum | Event tanpa `road_segment_id` dipetakan oleh stream processor |
| NS-010 | Consumer snapshot bobot pada routing worker | Belum | Snapshot diterapkan atomik tanpa merusak admissibility A* |
| NS-011 | Distribusi artifact graph melalui S3 | Belum | Worker mengambil artifact terverifikasi checksum saat startup |
| NS-012 | ArangoDB cluster lintas failure domain | Belum | Replikasi, failover, backup, dan restore drill lulus |
| NS-013 | Autoscaling API dan worker per region | Belum | Scaling berdasarkan RPS, CPU, RAM, serta queue depth |
| NS-014 | Observability dan SLO | Belum | Dashboard p50/p95/p99, error rate, Kafka lag, Flink checkpoint |
| NS-015 | Security production | Sebagian | ID event dipseudonimkan; TLS, secret manager, auth admin, rate limit, CORS terbatas belum selesai |
| NS-016 | Benchmark ArangoDB versus PostGIS | Belum | Dataset/query/hardware identik dan laporan reproducible |
| NS-017 | Benchmark NetworkX versus Neo4j | Belum | OSM graph dan pasangan OD identik, optimal cost divalidasi |
| NS-018 | Disaster recovery nasional | Belum | RTO/RPO ditetapkan dan simulasi pemulihan berhasil |
| NS-019 | Cache graph koridor ter-shard nasional | Selesai | ID deterministik per dataset/lokasi, registry persisten, singleflight dan batas kapasitas lintas worker |

Graph interaktif tidak lagi bergantung pada satu file graph per provinsi. Detail
masjid memicu build koridor kecil pada volume bersama; route dan benchmark memilih
artifact terkecil yang mencakup kedua titik. Cache dibatasi jumlah artifact dan
build dijalankan melalui slot global agar lonjakan pengguna tidak menyebabkan
unduhan Overpass serta penggunaan RAM serentak. Distribusi artifact lintas cluster
melalui object storage tetap dicatat pada NS-011.

## Urutan rollout

### Fase 1 — staging terpisah

1. Jalankan API dengan routing worker default tunggal.
2. Pastikan hasil rute remote identik dengan fallback lokal.
3. Jalankan load probe pada nearest dan rute.
4. Aktifkan `IMOSQUE_ROUTING_REMOTE_FALLBACK=false` setelah worker stabil.

Acceptance:

- Tidak ada perbedaan biaya rute.
- Error rate di bawah 1% pada target staging.
- Nearest p95 di bawah 100 ms.
- Routing warm-cache p95 ditentukan dari graph terbesar dan tidak mengalami timeout.

### Fase 2 — routing regional

1. Kelompokkan dataset berdasarkan provinsi/pulau.
2. Isi `IMOSQUE_ROUTING_WORKER_MAP` dengan mapping dataset ke worker.
3. Jalankan minimal dua replica untuk region trafik tinggi.
4. Buat overlay graph antardaerah untuk perjalanan lintas provinsi.

Contoh mapping:

```json
{
  "dataset_masjid_imosque_table_mosque_dki*": "http://routing-dki:8010",
  "dataset_masjid_imosque_table_mosque_jawa_barat*": "http://routing-jabar:8010"
}
```

### Fase 3 — realtime

1. Aktifkan profile `realtime` pada staging.
2. Kirim event sintetis ke `POST /api/v1/realtime/location`.
3. Implementasikan NS-009 sebelum menggunakan event pengguna nyata.
4. Implementasikan NS-010 dengan multiplier minimum 1.0 agar heuristik A* tetap aman.
5. Uji Kafka consumer lag, late event, restart Flink, dan recovery checkpoint.

### Fase 4 — high availability

1. Ganti ArangoDB standalone staging dengan cluster production.
2. Ganti Kafka satu broker dengan minimal tiga broker atau layanan terkelola.
3. Simpan checkpoint dan artifact mahal pada storage durable.
4. Sebarkan node pada failure domain berbeda.

## Gate migrasi database

Database tidak dimigrasikan karena hasil benchmark kelompok lain. Migrasi hanya
dilakukan jika kandidat memenuhi seluruh syarat berikut pada workload iMosque:

- p95 end-to-end minimal 25% lebih cepat daripada ArangoDB;
- error rate dan konsistensi hasil tidak lebih buruk;
- penggunaan RAM/CPU masuk dalam anggaran;
- import, update, backup, restore, dan failover lulus;
- dual-write tidak diperlukan permanen;
- tersedia rollback dan checksum jumlah/isi record.

PostGIS diuji untuk nearest/radius/spatial join. Neo4j diuji untuk traversal dan
shortest-path, bukan untuk menggantikan geo query hanya berdasarkan satu angka.

## Perintah staging

```bash
docker compose -f docker-compose.scale.yml up -d
docker compose -f docker-compose.scale.yml --profile realtime up -d
python scripts/load_test_national.py --requests 1000 --concurrency 50 --dataset-id all
```

Untuk production, pin image menggunakan digest, gunakan secret manager, dan
jangan memakai password default dari file contoh.

## Rollback

- Kosongkan `IMOSQUE_ROUTING_WORKER_URL` dan `IMOSQUE_ROUTING_WORKER_MAP` untuk kembali ke routing lokal.
- Nonaktifkan Kafka dengan mengosongkan `IMOSQUE_KAFKA_BOOTSTRAP_SERVERS`.
- Jangan hapus data ArangoDB selama benchmark kandidat.
- Pertahankan GraphML/runtime cache versi sebelumnya sampai rollout tervalidasi.
