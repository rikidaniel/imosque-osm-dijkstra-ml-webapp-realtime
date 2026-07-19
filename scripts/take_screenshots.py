import os
import sys
import time
import json
import urllib.request
import urllib.error
from playwright.sync_api import sync_playwright

def get_active_frontend_port():
    ports = [3000, 3001]
    for port in ports:
        url = f"http://localhost:{port}"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    print(f"[INFO] Frontend aktif terdeteksi di port {port}")
                    return port
        except (urllib.error.URLError, Exception):
            continue
    print("[WARNING] Tidak dapat mendeteksi frontend aktif. Menggunakan port default 3000.")
    return 3000

def get_recommendation_route_data(dest_lat, dest_lng, retries=3, delay=2):
    print(f"[INFO] Melakukan query rute rekomendasi ke backend (Monas ke {dest_lat}, {dest_lng})...")
    url = "http://127.0.0.1:8000/api/v1/routes/recommend"
    payload = {
        "dataset_id": "dataset_masjid_imosque_table_mosque_dki_jakarta_1",
        "origin": {
            "latitude": -6.17539,
            "longitude": 106.82715
        },
        "destination": {
            "latitude": dest_lat,
            "longitude": dest_lng
        },
        "algorithm": "dijkstra",
        "departure_time": "2026-07-19T14:40:00+07:00",
        "prayer": "auto",
        "profile": "balanced",
        "maximum_results": 3,
        "search_radius_km": 15,
        "auto_build_osm": False,
        "compact_response": True,
        "cost_parameters": {
            "fuel_price_per_liter": 10000,
            "fuel_efficiency_km_per_liter": 12,
            "operating_cost_per_km": 300,
            "toll_cost_per_km": 1000
        }
    }
    
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method="POST")
    
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    print("[SUCCESS] Data rute rekomendasi berhasil didapatkan dari backend.")
                    return data
        except Exception as e:
            print(f"[WARNING] Gagal memanggil API (Percobaan {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(delay)
    
    print("[ERROR] Semua percobaan memanggil API recommend gagal. Menggunakan data rute kosong.")
    return None

def main():
    print("[START] Menjalankan proses pengambilan 8 screenshot komprehensif (Direct Zustand Injection)...")
    
    frontend_port = get_active_frontend_port()
    base_url = f"http://localhost:{frontend_port}"
    admin_url = f"{base_url}/admin"
    swagger_url = "http://localhost:8000/docs"
    
    # Detail data masjid "Musholla Wanita Stasiun Gambir"
    gambir_mosque = {
        "_key": "dataset_masjid_imosque_table_mosque_dki_jakarta_1_240bd80c-79a2-56bc-abac-94f3a3818ae6",
        "_id": "Mosque/dataset_masjid_imosque_table_mosque_dki_jakarta_1_240bd80c-79a2-56bc-abac-94f3a3818ae6",
        "id": "240bd80c-79a2-56bc-abac-94f3a3818ae6",
        "name": "Musholla Wanita Stasiun Gambir",
        "address": "Place of worship \u00b7 Jl. Medan Merdeka Tim. No.11, RT.6/RW.1",
        "province": "DAERAH KHUSUS IBUKOTA JAKARTA",
        "kabko": "KOTA ADMINISTRASI JAKARTA PUSAT",
        "kecamatan": "SENEN",
        "kelurahan": "",
        "latitude": -6.177059,
        "longitude": 106.8307627,
        "rating": 4.3,
        "review_count": 4,
        "mosque_type": "musholla",
        "facilities": ["ac", "library"],
        "capacity_proxy": "small",
        "priority_score": 0.5004,
        "tier": "C",
        "data_quality": {
            "coordinate_source": "original_dataset",
            "rating_source": "original",
            "facilities_source": "ml_prediction",
            "capacity_source": "proxy_estimation"
        },
        "dataset_id": "dataset_masjid_imosque_table_mosque_dki_jakarta_1",
        "coordinate": [106.8307627, -6.177059]
    }
    
    # Dapatkan data rute ke Gambir
    route_data_gambir = get_recommendation_route_data(-6.177059, 106.8307627)
    
    # Default State
    default_state = {
        "state": {
            "startPoint": { "lat": -6.17539, "lng": 106.82715 },
            "startPointUpdatedAt": int(time.time() * 1000),
            "startPointSource": "map",
            "endPoint": None,
            "activeDatasetId": "dataset_masjid_imosque_table_mosque_dki_jakarta_1",
            "routeData": None,
            "selectedMosque": None,
            "searchSettings": {
                "algorithm": "dijkstra",
                "profile": "balanced",
                "departureMode": "now",
                "currentTime": "17:00",
                "prayer": "auto",
                "maxCandidates": "3",
                "bufferKm": "15",
                "autoBuild": False,
                "fuelPricePerLiter": "10000",
                "fuelEfficiencyKmPerLiter": "12",
                "operatingCostPerKm": "300",
                "tollCostPerKm": "1000"
            }
        },
        "version": 7
    }
    
    output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "outputs", "screenshots"))
    os.makedirs(output_dir, exist_ok=True)
    print(f"[INFO] Folder output screenshot: {output_dir}")
    
    with sync_playwright() as p:
        print("[INFO] Memulai browser Chromium (Memaksa Skema Warna Terang)...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme="light")
        page = context.new_page()
        
        # --- SCREENSHOT 1: Dashboard Peta Utama ---
        print(f"[ACTION] Menuju ke Dashboard Utama: {base_url}")
        try:
            page.goto(base_url, timeout=30000)
            page.evaluate(f"""
                localStorage.setItem('imosque-theme', 'light');
                localStorage.setItem('imosque-app-store', '{json.dumps(default_state)}');
            """)
            page.reload()
            print("[INFO] Menunggu data peta dimuat (5 detik)...")
            time.sleep(5)
            
            # Paksakan suntikan default state ke RAM
            page.evaluate(f"""
                if (window.useAppStore) {{
                    window.useAppStore.setState({{
                        startPoint: {{ lat: -6.17539, lng: 106.82715 }},
                        endPoint: null,
                        selectedMosque: null,
                        routeData: null,
                        activeDatasetId: 'dataset_masjid_imosque_table_mosque_dki_jakarta_1'
                    }});
                }}
            """)
            time.sleep(2)
            
            screenshot_path = os.path.join(output_dir, "dashboard_peta.png")
            page.screenshot(path=screenshot_path)
            print(f"[SUCCESS] Screenshot 1 (Dashboard Peta) disimpan ke: {screenshot_path}")
            
            # --- SCREENSHOT 2: Dashboard Peta Utama dengan Popover Settings Terbuka ---
            print("[ACTION] Mengklik tombol Pengaturan Pencarian untuk memunculkan Parameter Multi-Objective...")
            page.click('button[title="Pengaturan Pencarian"]')
            print("[INFO] Menunggu popover pengaturan terbuka (2 detik)...")
            time.sleep(2)
            
            screenshot_path = os.path.join(output_dir, "dashboard_settings.png")
            page.screenshot(path=screenshot_path)
            print(f"[SUCCESS] Screenshot 2 (Dashboard Settings) disimpan ke: {screenshot_path}")
            
            # Tutup popover settings
            page.click('button[title="Pengaturan Pencarian"]')
            time.sleep(1)
            
            # --- SCREENSHOT 3: Detail Drawer Masjid Gambir Terbuka ---
            print("[ACTION] Menyuntikkan selectedMosque (Musholla Wanita Stasiun Gambir) ke RAM Zustand...")
            page.evaluate(f"""
                if (window.useAppStore) {{
                    window.useAppStore.setState({{
                        startPoint: {{ lat: -6.17539, lng: 106.82715 }},
                        endPoint: null,
                        selectedMosque: {json.dumps(gambir_mosque)},
                        routeData: null
                    }});
                }}
            """)
            print("[INFO] Menunggu drawer detail masjid ter-render (3 detik)...")
            time.sleep(3)
            
            screenshot_path = os.path.join(output_dir, "dashboard_detail_masjid.png")
            page.screenshot(path=screenshot_path)
            print(f"[SUCCESS] Screenshot 3 (Detail Drawer Masjid) disimpan ke: {screenshot_path}")
            
            # --- SCREENSHOT 4: Rute Perjalanan Aktif ke Masjid Gambir ---
            print("[ACTION] Menyuntikkan routeData navigasi aktif ke RAM Zustand...")
            page.evaluate(f"""
                if (window.useAppStore) {{
                    window.useAppStore.setState({{
                        startPoint: {{ lat: -6.17539, lng: 106.82715 }},
                        endPoint: {{ lat: -6.177059, lng: 106.8307627 }},
                        selectedMosque: {json.dumps(gambir_mosque)},
                        routeData: {json.dumps(route_data_gambir) if route_data_gambir else 'null'}
                    }});
                }}
            """)
            print("[INFO] Menunggu rute navigasi ter-render di peta (4 detik)...")
            time.sleep(4)
            
            screenshot_path = os.path.join(output_dir, "dashboard_rute_aktif.png")
            page.screenshot(path=screenshot_path)
            print(f"[SUCCESS] Screenshot 4 (Rute Aktif) disimpan ke: {screenshot_path}")
            
            # --- RESET STATE RAM agar Search Bar & Tombol Admin Terlihat Kembali ---
            print("[ACTION] Mereset routeData & selectedMosque di RAM agar tombol Admin muncul kembali...")
            page.evaluate("""
                if (window.useAppStore) {
                    window.useAppStore.setState({
                        routeData: null,
                        selectedMosque: null,
                        endPoint: null
                    });
                }
            """)
            print("[INFO] Menunggu transisi layout kembali normal (2 detik)...")
            time.sleep(2)
            
            # --- SCREENSHOT 5: Admin Panel - Overview Dashboard (Client-side Navigation) ---
            print("[ACTION] Navigasi Client-side ke Halaman Admin...")
            page.click('button[title="Halaman Admin"]')
            print("[INFO] Menunggu halaman admin memuat data Overview via SPA transition (5 detik)...")
            time.sleep(5)
            
            screenshot_path = os.path.join(output_dir, "admin_dashboard.png")
            page.screenshot(path=screenshot_path)
            print(f"[SUCCESS] Screenshot 5 (Admin Overview) disimpan ke: {screenshot_path}")
            
            # --- SCREENSHOT 6: Admin Panel - Dataset Manager ---
            print("[ACTION] Mengklik tab Dataset Masjid...")
            page.click('button:has-text("Dataset Masjid")')
            print("[INFO] Menunggu tab Dataset memuat data (3 detik)...")
            time.sleep(3)
            
            screenshot_path = os.path.join(output_dir, "admin_dataset.png")
            page.screenshot(path=screenshot_path)
            print(f"[SUCCESS] Screenshot 6 (Admin Dataset) disimpan ke: {screenshot_path}")

            # --- SCREENSHOT 7: Admin Panel - Evaluasi Benchmark Panel (Menjalankan Perbandingan Dijkstra vs A*) ---
            print("[ACTION] Mengklik tab Evaluasi Algoritma...")
            page.click('button:has-text("Evaluasi Algoritma")')
            print("[INFO] Menunggu tab Evaluasi memuat panel benchmark (3 detik)...")
            time.sleep(3)
            
            # Paksakan pengisian startPoint dan endPoint di RAM admin tab sebelum klik benchmark
            print("[ACTION] Memastikan startPoint dan endPoint terisi di RAM Admin panel...")
            page.evaluate(f"""
                if (window.useAppStore) {{
                    window.useAppStore.setState({{
                        startPoint: {{ lat: -6.17539, lng: 106.82715 }},
                        endPoint: {{ lat: -6.177059, lng: 106.8307627 }},
                        activeDatasetId: 'dataset_masjid_imosque_table_mosque_dki_jakarta_1'
                    }});
                }}
            """)
            time.sleep(1)
            
            # Klik tombol "Mulai Bandingkan Algoritma" agar grafik visual recharts digambar
            print("[ACTION] Mengklik tombol 'Mulai Bandingkan Algoritma' untuk menjalankan benchmark komparatif...")
            page.click('button:has-text("Mulai Bandingkan Algoritma")')
            print("[INFO] Menunggu perhitungan benchmark dan visualisasi grafik recharts (9 detik)...")
            time.sleep(9)
            
            screenshot_path = os.path.join(output_dir, "admin_benchmark.png")
            page.screenshot(path=screenshot_path)
            print(f"[SUCCESS] Screenshot 7 (Admin Benchmark) disimpan ke: {screenshot_path}")
        except Exception as e:
            print(f"[ERROR] Gagal mengambil screenshot Dashboard/Admin: {e}")
            
        # --- SCREENSHOT 8: Swagger API Docs ---
        print(f"[ACTION] Menuju ke Swagger API Docs: {swagger_url}")
        try:
            page.goto(swagger_url, timeout=30000)
            print("[INFO] Menunggu Swagger UI memuat endpoint (4 detik)...")
            time.sleep(4)
            screenshot_path = os.path.join(output_dir, "swagger_api_docs.png")
            page.screenshot(path=screenshot_path)
            print(f"[SUCCESS] Screenshot 8 (Swagger UI) disimpan ke: {screenshot_path}")
        except Exception as e:
            print(f"[ERROR] Gagal mengambil screenshot Swagger UI: {e}")
            
        browser.close()
    
    print("[FINISH] Selesai mengambil kedelapan screenshot revisi SPA Mode.")

if __name__ == "__main__":
    main()
