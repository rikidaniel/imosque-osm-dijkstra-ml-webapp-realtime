# Fitur User Settings Database Sync

## 📝 Deskripsi

Fitur ini menyimpan pengaturan pengguna (routing settings dan prayer alarm settings) ke database ArangoDB, sehingga pengaturan tetap tersimpan meskipun pengguna membuka aplikasi dari perangkat berbeda atau setelah clear cache browser.

## ✨ Fitur yang Disimpan

### 1. **Pengaturan Pencarian Rute (Search Settings)**
   - Algorithm: dijkstra / astar
   - Profile: balanced / fastest / prayer_priority / low_cost
   - Current Time: waktu keberangkatan
   - Prayer: waktu sholat target (maghrib, dzuhur, dll)
   - Buffer Km: radius pencarian masjid
   - Max Candidates: jumlah rekomendasi masjid
   - Auto Build: build OSM graph otomatis

### 2. **Pengaturan Jadwal Sholat (Prayer Settings)**
   - Prayer Schedule: jadwal 5 waktu sholat + status alarm
   - Hijri Date: tanggal hijriah
   - Masehi Date: tanggal masehi

## 🏗️ Arsitektur

```
Frontend (Zustand Store)
    ↓
Settings Sync Utility
    ↓
Backend API Endpoints (/api/v1/user-settings)
    ↓
User Settings Repository
    ↓
ArangoDB (user_settings collection)
```

## 📂 File yang Dimodifikasi/Dibuat

### Backend:
1. **`backend/app/infrastructure/database/arangodb_client.py`**
   - Menambahkan collection `user_settings` dengan unique index pada `user_id`

2. **`backend/app/domain/repositories/user_settings_repo.py`** (BARU)
   - `save_user_settings()`: Simpan/update settings
   - `get_user_settings()`: Ambil settings berdasarkan user_id
   - `delete_user_settings()`: Hapus settings (untuk reset)

3. **`backend/app/interfaces/api/routes.py`**
   - `POST /api/v1/user-settings`: Simpan settings
   - `GET /api/v1/user-settings/{user_id}`: Load settings
   - `DELETE /api/v1/user-settings/{user_id}`: Hapus settings

### Frontend:
1. **`frontend/src/lib/settings-sync.ts`** (BARU)
   - `getUserId()`: Generate unique device ID
   - `saveSettingsToDatabase()`: Simpan ke backend
   - `loadSettingsFromDatabase()`: Load dari backend
   - `debouncedSaveSettings()`: Auto-save dengan debounce 2 detik

2. **`frontend/src/lib/store.ts`**
   - Integrasi auto-save saat `setSearchSettings()` atau `setPrayerSchedule()` dipanggil
   - Auto-load settings dari database saat aplikasi pertama kali dibuka
   - Merge database settings dengan local storage

## 🔧 Cara Kerja

### 1. **Inisialisasi (App Load)**
```typescript
// Saat aplikasi dibuka pertama kali:
1. Load settings dari localStorage (Zustand persist)
2. Load settings dari database (async)
3. Merge database settings dengan local settings
4. Update store dengan hasil merge
```

### 2. **Auto-Save (Setiap Perubahan)**
```typescript
// Saat user mengubah settings:
1. User mengubah pengaturan via UI
2. Store dipanggil: setSearchSettings() atau setPrayerSchedule()
3. Settings di-update di local state
4. Debounced save triggered (tunggu 2 detik)
5. Jika tidak ada perubahan lagi dalam 2 detik → save ke database
```

### 3. **User ID Generation**
```typescript
// Generate unique device ID:
1. Cek localStorage untuk existing ID
2. Jika tidak ada, buat ID dari browser fingerprint:
   - User Agent
   - Language
   - Screen Resolution
   - Timezone
   - Storage Support
3. Hash menjadi: device_[hash]_[timestamp]
4. Simpan ke localStorage
```

## 📊 API Endpoints

### POST /api/v1/user-settings
Simpan atau update user settings.

**Request Body:**
```json
{
  "user_id": "device_abc123",
  "search_settings": {
    "algorithm": "dijkstra",
    "profile": "balanced",
    "currentTime": "17:00",
    "prayer": "maghrib",
    "bufferKm": "15",
    "maxCandidates": "3",
    "autoBuild": false
  },
  "prayer_settings": {
    "schedule": [
      {"name": "Subuh", "time": "04:45", "isAlarmActive": true},
      {"name": "Dzuhur", "time": "12:02", "isAlarmActive": false}
    ],
    "hijriDate": "12 Muharram 1448 H",
    "masehiDate": "12 Juli 2026"
  },
  "updated_at": "2026-07-14T17:00:00Z"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Settings berhasil disimpan ke database",
  "user_id": "device_abc123",
  "data": { ... }
}
```

### GET /api/v1/user-settings/{user_id}
Ambil user settings dari database.

**Response:**
```json
{
  "status": "success",
  "user_id": "device_abc123",
  "data": {
    "search_settings": { ... },
    "prayer_settings": { ... },
    "updated_at": "2026-07-14T17:00:00Z"
  }
}
```

### DELETE /api/v1/user-settings/{user_id}
Hapus user settings (untuk reset atau logout).

**Response:**
```json
{
  "status": "success",
  "message": "Settings user device_abc123 berhasil dihapus"
}
```

## ✅ Testing

Sudah ditest menggunakan script `test_user_settings.py`:

```bash
python test_user_settings.py
```

**Test Coverage:**
- ✅ Save settings
- ✅ Load settings
- ✅ Update settings
- ✅ Delete settings
- ✅ Auto-save dengan debounce
- ✅ Auto-load saat app initialization

## 🚀 Benefits

1. **Cross-Device Sync**: Settings tersimpan di database, bisa diakses dari perangkat berbeda
2. **Persistent**: Settings tidak hilang meskipun clear browser cache
3. **Performance**: Debounced save mencegah terlalu banyak request ke server
4. **Offline-First**: Tetap menggunakan localStorage sebagai fallback jika offline
5. **User Experience**: Auto-save tanpa perlu tombol "Save" manual

## 📌 Catatan

- Device ID disimpan di localStorage dengan key `imosque_user_id`
- Auto-save triggered setelah 2 detik tidak ada perubahan (debounced)
- Jika backend offline, settings tetap tersimpan di localStorage
- Database settings akan override local settings saat app load
- Collection `user_settings` di ArangoDB memiliki unique index pada `user_id`

## 🔮 Future Enhancements

- [ ] Login/Register untuk sync antar perangkat dengan user account
- [ ] Conflict resolution jika settings diubah di 2 perangkat berbeda
- [ ] Settings version untuk backward compatibility
- [ ] Export/Import settings sebagai JSON file
- [ ] Settings history untuk restore pengaturan lama
