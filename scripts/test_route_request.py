"""Optional local test after OSM graph cache has been built."""
from pathlib import Path
import json
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "backend"))

from app.routing_osm import route_via_osm_dijkstra  # noqa: E402

if __name__ == "__main__":
    result = route_via_osm_dijkstra(
        start_lat=-6.1783,
        start_lon=106.6319,
        end_lat=-6.2050,
        end_lon=106.6500,
        algorithm="dijkstra",
        current_time="17:35",
        prayer_time="18:05",
        max_candidates=4,
        auto_build_osm=True,
    )
    out = PROJECT_DIR / "outputs" / "sample_route_response.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved sample route to {out}")
