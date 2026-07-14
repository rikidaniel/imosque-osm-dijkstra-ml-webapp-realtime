from unittest.mock import MagicMock

from app.use_cases import dataset_usecases as dataset_module
from app.use_cases.dataset_usecases import DatasetUseCases


def test_invalidate_osm_graph_removes_file_metadata_and_memory_cache(tmp_path, monkeypatch):
    graph_path = tmp_path / "road_graph_dataset.graphml"
    graph_path.write_text("graph", encoding="utf-8")
    mosque_repo = MagicMock()
    dataset_repo = MagicMock()
    evicted = []
    monkeypatch.setattr(dataset_module, "get_graphml_path", lambda _dataset_id: graph_path)
    monkeypatch.setattr(dataset_module, "evict_road_graph", lambda path: evicted.append(path))
    use_cases = DatasetUseCases(mosque_repo, dataset_repo)

    use_cases.invalidate_osm_graph("Dataset Example")

    assert not graph_path.exists()
    assert evicted == [graph_path]
    dataset_repo.delete_osm_cache.assert_called_once_with("dataset_example")
