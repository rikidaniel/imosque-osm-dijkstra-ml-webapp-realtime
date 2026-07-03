"""Optional local test after OSM graph cache has been built."""
from pathlib import Path
import json
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from backend.app.infrastructure.database.arangodb_client import init_db
from backend.app.infrastructure.database.arangodb_repo import ArangoMosqueRepository, ArangoDatasetRepository
from backend.app.use_cases.routing_usecases import RoutingUseCases

if __name__ == "__main__":
    init_db()

    mosque_repo = ArangoMosqueRepository()
    dataset_repo = ArangoDatasetRepository()
    routing_usecases = RoutingUseCases(mosque_repo, dataset_repo)

    result = routing_usecases.route_via_osm_dijkstra(
        start_lat=-6.1754,
        start_lon=106.8272,
        end_lat=-6.2000,
        end_lon=106.8200,
        algorithm="dijkstra",
        current_time="17:35",
        prayer_time="18:05",
        max_candidates=4,
        auto_build_osm=True,
        buffer_km=6.0,
        dataset_id="dataset_masjid_imosque_table_mosque_dki_jakarta_1"
    )
    out = PROJECT_DIR / "outputs" / "sample_route_response.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved sample route to {out}")
