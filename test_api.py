import urllib.request, json

def test(lat, lng, label=""):
    body = json.dumps({
        "dataset_id": "all",
        "latitude": lat,
        "longitude": lng,
        "radius_km": 50,
        "limit": 30
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/nearest-mosques",
        body,
        {"Content-Type": "application/json"}
    )
    try:
        r = json.loads(urllib.request.urlopen(req).read())
        print(f"\n[{label}] lat={lat} lng={lng}")
        print(f"  dataset_id used: {r.get('dataset_id')}")
        print(f"  total: {r.get('total')}")
        for i, m in enumerate(r.get("items", [])[:3]):
            print(f"  {i+1}. {m['name']} ({m.get('dataset_id','?')}) dist={m.get('distance_km',0):.2f}km")
    except Exception as e:
        print(f"ERROR: {e}")

# Test DKI Jakarta
test(-6.2088, 106.8456, "Jakarta")

# Test Bandung
test(-6.9175, 107.6191, "Bandung")

# Test klik random
test(-6.5, 107.0, "Random antara Jakarta-Bandung")
