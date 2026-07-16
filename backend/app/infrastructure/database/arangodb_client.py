import os
from typing import Any, Dict, Iterable, List

from arango import ArangoClient

ARANGO_HOST = os.getenv("ARANGO_HOST", "http://localhost:8529")
ARANGO_ROOT_PASSWORD = os.getenv("ARANGO_ROOT_PASSWORD", "imosque_password")
DB_NAME = "imosque"

_client = ArangoClient(hosts=ARANGO_HOST)

def init_db():
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

def get_db():
    return _client.db(DB_NAME, username='root', password=ARANGO_ROOT_PASSWORD)
