import os
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
    doc_cols = ['Province', 'City', 'District', 'Village', 'Mosque', 'RoadNode', 'CheckInCheckOut', 'datasets', 'app_settings', 'osm_graph_cache']
    for name in doc_cols:
        if not db.has_collection(name):
            db.create_collection(name)
            
        col = db.collection(name)
        if name == 'Mosque':
            col.add_persistent_index(fields=['dataset_id'])
            _ensure_geojson_index(db, name, ['coordinate'])
        elif name == 'RoadNode':
            _ensure_geojson_index(db, name, ['coordinate'])
                
    # Edge Collections
    edge_cols = ['BELONGS_TO_PROVINCE', 'BELONGS_TO_CITY', 'BELONGS_TO_DISTRICT', 'LOCATED_IN_VILLAGE', 'ROAD_CONNECTION']
    for name in edge_cols:
        if not db.has_collection(name):
            db.create_collection(name, edge=True)

def _ensure_geojson_index(db, collection_name, fields):
    from arango.request import Request
    col = db.collection(collection_name)
    has_correct_index = False
    
    for idx in col.indexes():
        if idx['type'] == 'geo':
            if idx.get('geo_json', False) or idx.get('geoJson', False):
                has_correct_index = True
            else:
                try:
                    col.delete_index(idx['id'])
                except Exception:
                    pass
                    
    if not has_correct_index:
        req = Request(
            method='post',
            endpoint='/_api/index',
            params={'collection': collection_name},
            data={'type': 'geo', 'fields': fields, 'geoJson': True}
        )
        try:
            db.conn.send_request(req)
        except Exception as e:
            print(f"Error creating geoJson index on {collection_name}: {e}")

def get_db():
    return _client.db(DB_NAME, username='root', password=ARANGO_ROOT_PASSWORD)
