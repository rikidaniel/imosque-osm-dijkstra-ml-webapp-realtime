import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.domain.models.schemas import NearestMosquesRequest
from app.infrastructure.database import arangodb_client, arangodb_repo
from app.infrastructure.database.arangodb_repo import ArangoMosqueRepository
from app.use_cases.dataset_usecases import DatasetUseCases


class _FakeIndexCollection:
    def __init__(self, indexes):
        self._indexes = list(indexes)
        self.deleted = []
        self.created = []

    def indexes(self):
        return list(self._indexes)

    def delete_index(self, index_id):
        self.deleted.append(index_id)
        self._indexes = [index for index in self._indexes if index.get("id") != index_id]

    def add_geo_index(self, *, fields, ordered):
        self.created.append((list(fields), ordered))
        self._indexes.append(
            {
                "id": "Mosque/geo-created",
                "type": "geo",
                "fields": list(fields),
                "geoJson": ordered,
            }
        )


class _FakeDb:
    def __init__(self, collection):
        self._collection = collection

    def collection(self, _name):
        return self._collection


def test_geojson_index_verification_requires_exact_coordinate_field():
    collection = _FakeIndexCollection(
        [
            {"id": "Mosque/other", "type": "geo", "fields": ["other"], "geoJson": True},
            {"id": "Mosque/wrong", "type": "geo", "fields": ["coordinate"], "geoJson": False},
        ]
    )

    arangodb_client._ensure_geojson_index(_FakeDb(collection), "Mosque", ["coordinate"])

    assert collection.deleted == ["Mosque/wrong"]
    assert collection.created == [(["coordinate"], True)]
    assert any(index["id"] == "Mosque/other" for index in collection.indexes())


class _FakeAql:
    def __init__(self):
        self.calls = []

    def execute(self, query, bind_vars=None):
        self.calls.append((query, bind_vars))
        return []


class _FakeMosqueCollection:
    def __init__(self, documents=None):
        self.documents = dict(documents or {})
        self.updated = []
        self.inserted = []

    def get(self, key):
        document = self.documents.get(key)
        return dict(document) if document else None

    def update(self, patch):
        self.updated.append(dict(patch))
        key = patch["_key"]
        self.documents[key] = {**self.documents[key], **patch}
        return True

    def insert(self, document, overwrite=False):
        self.inserted.append(dict(document))
        self.documents[document["_key"]] = dict(document)
        return True


class _FakeMosqueDb:
    def __init__(self, collection):
        self._collection = collection
        self.aql = _FakeAql()

    def collection(self, name):
        assert name == "Mosque"
        return self._collection


@pytest.fixture(autouse=True)
def clear_mosque_lookup_cache():
    with ArangoMosqueRepository._lookup_cache_lock:
        ArangoMosqueRepository._lookup_cache.clear()
    yield
    with ArangoMosqueRepository._lookup_cache_lock:
        ArangoMosqueRepository._lookup_cache.clear()


@pytest.mark.parametrize(
    ("patch", "expected_coordinate"),
    [
        ({"latitude": -6.25}, [106.8, -6.25]),
        ({"longitude": 106.9}, [106.9, -6.2]),
    ],
)
def test_partial_coordinate_update_syncs_geo_field_and_lookup_cache(
    monkeypatch,
    patch,
    expected_coordinate,
):
    key = "banten_m1"
    collection = _FakeMosqueCollection(
        {
            key: {
                "_key": key,
                "id": "m1",
                "dataset_id": "banten",
                "name": "Masjid Lama",
                "latitude": -6.2,
                "longitude": 106.8,
                "coordinate": [106.8, -6.2],
            }
        }
    )
    monkeypatch.setattr(arangodb_repo, "get_db", lambda: _FakeMosqueDb(collection))
    repository = ArangoMosqueRepository()

    assert repository.update_mosque("banten", "m1", patch) is True

    assert collection.updated[0]["coordinate"] == expected_coordinate
    cached = repository.get_mosque_by_id("banten", "m1")
    assert cached["coordinate"] == expected_coordinate
    assert cached["latitude"] == patch.get("latitude", -6.2)
    assert cached["longitude"] == patch.get("longitude", 106.8)


def test_create_and_bulk_delete_keep_lookup_cache_consistent(monkeypatch):
    collection = _FakeMosqueCollection()
    database = _FakeMosqueDb(collection)
    monkeypatch.setattr(arangodb_repo, "get_db", lambda: database)
    repository = ArangoMosqueRepository()

    mosque_id = repository.create_mosque(
        "banten",
        {"name": "Masjid Baru", "latitude": -6.2, "longitude": 106.8},
    )
    assert ("banten", mosque_id) in repository._lookup_cache

    assert repository.delete_mosques_bulk("banten", [mosque_id]) is True
    assert ("banten", mosque_id) not in repository._lookup_cache


def test_nearest_singleflight_coalesces_equal_concurrent_requests():
    repository = Mock()
    call_lock = threading.Lock()
    calls = 0

    def query(*_args):
        nonlocal calls
        with call_lock:
            calls += 1
        time.sleep(0.08)
        return [{"id": "m1"}]

    repository.get_nearest_mosques.side_effect = query
    use_cases = DatasetUseCases(repository, Mock())
    workers = 6
    barrier = threading.Barrier(workers)

    def run_request():
        barrier.wait()
        return use_cases.get_nearest_mosques("banten", -6.2, 106.8, 20, 100)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda _index: run_request(), range(workers)))

    assert calls == 1
    repository.get_nearest_mosques.assert_called_once_with("banten", -6.2, 106.8, 20.0, 50)
    assert sum(result["cache_hit"] is False for result in results) == 1
    assert sum(result["cache_hit"] is True for result in results) == workers - 1


class _RevisionRepository:
    def __init__(self):
        self.profile = {"_key": "banten", "mosque_count": 2, "data_revision": 7}

    def get_dataset(self, _dataset_id):
        return dict(self.profile)

    def upsert_dataset(self, _dataset_id, profile):
        self.profile = dict(profile)


def test_mutation_bumps_revision_and_invalidates_dataset_and_all_caches():
    repository = Mock()
    repository.get_nearest_mosques.return_value = [{"id": "m1"}]
    repository.update_mosque.return_value = True
    dataset_repository = _RevisionRepository()
    use_cases = DatasetUseCases(repository, dataset_repository)

    use_cases.get_nearest_mosques("banten", -6.2, 106.8, 10, 5)
    use_cases.get_nearest_mosques("all", -6.2, 106.8, 10, 5)
    assert repository.get_nearest_mosques.call_count == 2

    assert use_cases.update_mosque("banten", "m1", {"name": "Masjid Baru"}) is True
    assert dataset_repository.profile["data_revision"] == 8

    use_cases.get_nearest_mosques("banten", -6.2, 106.8, 10, 5)
    use_cases.get_nearest_mosques("all", -6.2, 106.8, 10, 5)
    assert repository.get_nearest_mosques.call_count == 4


def test_nearest_request_rejects_payload_limit_above_interactive_cap():
    with pytest.raises(ValidationError):
        NearestMosquesRequest(latitude=-6.2, longitude=106.8, limit=51)
