import os
from typing import Any, Dict, Iterable, List

from arango import ArangoClient

ARANGO_HOST = os.getenv("ARANGO_HOST", "http://localhost:8529")
ARANGO_ROOT_PASSWORD = os.getenv("ARANGO_ROOT_PASSWORD", "imosque_password")
DB_NAME = "imosque"
MOSQUE_SEARCH_VIEW = "MosqueSearch"
MOSQUE_SEARCH_ANALYZER = "imosque_text"
ARANGO_REQUEST_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv("ARANGO_REQUEST_TIMEOUT_SECONDS", "10"))
)

_client = ArangoClient(
    hosts=ARANGO_HOST,
    request_timeout=ARANGO_REQUEST_TIMEOUT_SECONDS,
)

_db_initialized = False
_db_error = None

def init_db():
    global _db_initialized, _db_error
    try:
        sys_db = _client.db('_system', username='root', password=ARANGO_ROOT_PASSWORD)
        if not sys_db.has_database(DB_NAME):
            sys_db.create_database(DB_NAME)

        db = _client.db(DB_NAME, username='root', password=ARANGO_ROOT_PASSWORD)

        # Document Collections
        doc_cols = ['Province', 'City', 'District', 'Village', 'Mosque', 'RoadNode', 'CheckInCheckOut', 'datasets', 'app_settings', 'osm_graph_cache', 'user_settings']
        for name in doc_cols:
            if not db.has_collection(name):
                db.create_collection(name)
                
            col = db.collection(name)
            if name == 'Mosque':
                col.add_persistent_index(fields=['dataset_id'])
                col.add_persistent_index(fields=['dataset_id', 'id'])
                col.add_persistent_index(fields=['id'])
                _ensure_geojson_index(db, name, ['coordinate'])
            elif name == 'RoadNode':
                _ensure_geojson_index(db, name, ['coordinate'])
            elif name == 'user_settings':
                # Index untuk query cepat berdasarkan user_id
                col.add_persistent_index(fields=['user_id'], unique=True)

        # Edge Collections
        edge_cols = ['BELONGS_TO_PROVINCE', 'BELONGS_TO_CITY', 'BELONGS_TO_DISTRICT', 'LOCATED_IN_VILLAGE', 'ROAD_CONNECTION']
        for name in edge_cols:
            if not db.has_collection(name):
                db.create_collection(name, edge=True)
        _ensure_mosque_search_view(db)
        _db_initialized = True
        _db_error = None
    except Exception as exc:
        _db_initialized = False
        _db_error = str(exc)
        print(f"Database initialization failed: {exc}")

def check_db_health() -> tuple[bool, str | None]:
    global _db_initialized, _db_error
    if not _db_initialized:
        # Coba inisialisasi ulang jika sebelumnya gagal (misal docker baru aktif)
        init_db()
        if not _db_initialized:
            return False, _db_error

    try:
        sys_db = _client.db('_system', username='root', password=ARANGO_ROOT_PASSWORD)
        # Ping arango server dengan memanggil API sederhana (properties() adalah method yang valid)
        sys_db.properties()
        return True, None
    except Exception as exc:
        _db_initialized = False
        _db_error = str(exc)
        return False, str(exc)

def is_db_initialized() -> bool:
    global _db_initialized
    return _db_initialized


def _normalise_index_fields(fields: Iterable[str]) -> List[str]:
    return [str(field) for field in fields]


def _is_expected_geojson_index(index: Dict[str, Any], fields: Iterable[str]) -> bool:
    """Return True only for a GeoJSON index on the requested fields.

    python-arango has exposed the GeoJSON flag as both ``geoJson`` and
    ``geo_json`` across releases, so accept either spelling while keeping the
    field comparison exact. A geo index for another attribute must never make
    startup skip the ``coordinate`` index.
    """
    expected_fields = _normalise_index_fields(fields)
    index_fields = _normalise_index_fields(index.get('fields') or [])
    is_geojson = bool(index.get('geoJson', index.get('geo_json', False)))
    return index.get('type') == 'geo' and index_fields == expected_fields and is_geojson


def _ensure_geojson_index(db, collection_name, fields):
    col = db.collection(collection_name)
    expected_fields = _normalise_index_fields(fields)
    indexes = col.indexes()

    if any(_is_expected_geojson_index(index, expected_fields) for index in indexes):
        return

    # Remove only an incompatible index on these exact fields. Other geo
    # indexes may be intentional and are left untouched.
    for index in indexes:
        if (
            index.get('type') == 'geo'
            and _normalise_index_fields(index.get('fields') or []) == expected_fields
        ):
            try:
                col.delete_index(index['id'])
            except Exception as exc:
                raise RuntimeError(
                    f"Gagal mengganti geo index {collection_name}.{'.'.join(expected_fields)}: {exc}"
                ) from exc

    try:
        # Public python-arango API. ``ordered=True`` maps to ``geoJson: true``
        # and therefore stores coordinates in [longitude, latitude] order.
        col.add_geo_index(fields=expected_fields, ordered=True)
    except Exception as exc:
        raise RuntimeError(
            f"Gagal membuat GeoJSON index {collection_name}.{'.'.join(expected_fields)}: {exc}"
        ) from exc

    created_indexes = col.indexes()
    if not any(_is_expected_geojson_index(index, expected_fields) for index in created_indexes):
        raise RuntimeError(
            f"GeoJSON index {collection_name}.{'.'.join(expected_fields)} tidak terverifikasi setelah dibuat."
        )


def _ensure_mosque_search_view(db) -> None:
    """Create a case-insensitive n-gram search view for national lookup.

    The regular ``/mosques`` endpoint is ordered for administration and cannot
    efficiently autocomplete across hundreds of thousands of documents. The
    ArangoSearch view keeps the interactive search outside the nearest-radius
    result set without loading the collection into the frontend.
    """
    analyzer_names = {str(item.get("name")) for item in db.analyzers()}
    qualified_analyzer_name = f"{DB_NAME}::{MOSQUE_SEARCH_ANALYZER}"
    if MOSQUE_SEARCH_ANALYZER not in analyzer_names and qualified_analyzer_name not in analyzer_names:
        db.create_analyzer(
            MOSQUE_SEARCH_ANALYZER,
            "text",
            properties={
                "locale": "id_ID.utf-8",
                "case": "lower",
                "accent": False,
                "stemming": False,
                "stopwords": [],
                "edgeNgram": {
                    "min": 2,
                    "max": 15,
                    "preserveOriginal": True,
                },
            },
            features=["frequency", "norm", "position"],
        )

    view_names = {str(item.get("name")) for item in db.views()}
    indexed_fields = {
        field: {"analyzers": [MOSQUE_SEARCH_ANALYZER]}
        for field in ("name", "address", "kecamatan", "kabko", "provinsi")
    }
    view_properties = {
        "links": {
            "Mosque": {
                "includeAllFields": False,
                "fields": indexed_fields,
            }
        }
    }
    if MOSQUE_SEARCH_VIEW not in view_names:
        db.create_arangosearch_view(MOSQUE_SEARCH_VIEW, properties=view_properties)
        return

    current_view = db.view(MOSQUE_SEARCH_VIEW)
    current_fields = current_view.get("links", {}).get("Mosque", {}).get("fields", {})
    if all(
        MOSQUE_SEARCH_ANALYZER in current_fields.get(field, {}).get("analyzers", [])
        for field in indexed_fields
    ):
        return
    db.update_arangosearch_view(MOSQUE_SEARCH_VIEW, view_properties)

def get_db():
    return _client.db(DB_NAME, username='root', password=ARANGO_ROOT_PASSWORD)
