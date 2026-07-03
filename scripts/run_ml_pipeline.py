from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "backend"))

from app.ml_enrichment import enrich_dataset, get_active_dataset_id  # noqa: E402

if __name__ == "__main__":
    dataset_id = sys.argv[1] if len(sys.argv) > 1 else get_active_dataset_id()
    profile = enrich_dataset(dataset_id=dataset_id)
    print(f"ML enrichment selesai untuk dataset: {profile['dataset_id']}")
    for k, v in profile.items():
        print(f"{k}: {v}")
