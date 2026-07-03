from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from backend.app.infrastructure.database.arangodb_client import init_db
from backend.app.infrastructure.database.arangodb_repo import ArangoMosqueRepository, ArangoDatasetRepository
from backend.app.use_cases.dataset_usecases import DatasetUseCases

if __name__ == "__main__":
    # Inisialisasi DB (buat database & koleksi jika belum ada)
    init_db()

    mosque_repo = ArangoMosqueRepository()
    dataset_repo = ArangoDatasetRepository()
    dataset_usecases = DatasetUseCases(mosque_repo, dataset_repo)

    dataset_id = sys.argv[1] if len(sys.argv) > 1 else dataset_usecases.get_active_dataset_id()
    
    print(f"Menjalankan ML Enrichment untuk dataset: {dataset_id}...")
    res = dataset_usecases.run_pipeline(dataset_id=dataset_id)
    profile = res["profile"]
    
    print(f"\nML enrichment selesai untuk dataset: {profile['dataset_id']}")
    for k, v in profile.items():
        print(f"{k}: {v}")
