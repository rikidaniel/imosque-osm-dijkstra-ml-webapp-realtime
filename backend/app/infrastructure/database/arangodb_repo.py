import re
from typing import Any, Dict, List, Optional
from app.domain.repositories.mosque_repo import MosqueRepository
from app.domain.repositories.dataset_repo import DatasetRepository
from app.infrastructure.database.arangodb_client import get_db

def _slugify_key(text: str) -> str:
    s = str(text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "empty"

class ArangoMosqueRepository(MosqueRepository):
    def save_mosques(self, dataset_id: str, mosques: List[Dict[str, Any]]) -> None:
        db = get_db()
        col = db.collection('Mosque')
        
        # Batch insert/upsert mosques
        for m in mosques:
            m['dataset_id'] = dataset_id
            m['coordinate'] = [float(m['longitude']), float(m['latitude'])]
            m['_key'] = f"{dataset_id}_{m.get('id', '')}"
            
        col.insert_many(mosques, overwrite=True)
        
        # Ingest graph structure
        provinces = {}
        cities = {}
        districts = {}
        villages = {}
        
        belongs_to_prov = []
        belongs_to_city = []
        belongs_to_dist = []
        located_in_village = []
        
        for m in mosques:
            m_key = m['_key']
            prov_name = m.get('province') or ''
            city_name = m.get('kabko') or ''
            dist_name = m.get('kecamatan') or ''
            vill_name = m.get('kelurahan') or ''
            
            prov_key = _slugify_key(prov_name) if prov_name else ""
            city_key = _slugify_key(city_name) if city_name else ""
            dist_key = _slugify_key(dist_name) if dist_name else ""
            vill_key = _slugify_key(vill_name) if vill_name else ""
            
            if prov_key:
                provinces[prov_key] = {"_key": prov_key, "name": prov_name}
            if city_key:
                cities[city_key] = {"_key": city_key, "name": city_name}
                if prov_key:
                    belongs_to_prov.append({
                        "_key": f"{city_key}_{prov_key}",
                        "_from": f"City/{city_key}",
                        "_to": f"Province/{prov_key}"
                    })
            if dist_key:
                districts[dist_key] = {"_key": dist_key, "name": dist_name}
                if city_key:
                    belongs_to_city.append({
                        "_key": f"{dist_key}_{city_key}",
                        "_from": f"District/{dist_key}",
                        "_to": f"City/{city_key}"
                    })
            if vill_key:
                villages[vill_key] = {"_key": vill_key, "name": vill_name}
                if dist_key:
                    belongs_to_dist.append({
                        "_key": f"{vill_key}_{dist_key}",
                        "_from": f"Village/{vill_key}",
                        "_to": f"District/{dist_key}"
                    })
                located_in_village.append({
                    "_key": f"{m_key}_{vill_key}",
                    "_from": f"Mosque/{m_key}",
                    "_to": f"Village/{vill_key}"
                })
                
        # Insert master data
        if provinces:
            db.collection('Province').insert_many(list(provinces.values()), overwrite=True)
        if cities:
            db.collection('City').insert_many(list(cities.values()), overwrite=True)
        if districts:
            db.collection('District').insert_many(list(districts.values()), overwrite=True)
        if villages:
            db.collection('Village').insert_many(list(villages.values()), overwrite=True)
            
        # Insert edges (filtering out duplicates within the batch)
        if belongs_to_prov:
            unique_prov_edges = {edge['_key']: edge for edge in belongs_to_prov}
            db.collection('BELONGS_TO_PROVINCE').insert_many(list(unique_prov_edges.values()), overwrite=True)
        if belongs_to_city:
            unique_city_edges = {edge['_key']: edge for edge in belongs_to_city}
            db.collection('BELONGS_TO_CITY').insert_many(list(unique_city_edges.values()), overwrite=True)
        if belongs_to_dist:
            unique_dist_edges = {edge['_key']: edge for edge in belongs_to_dist}
            db.collection('BELONGS_TO_DISTRICT').insert_many(list(unique_dist_edges.values()), overwrite=True)
        if located_in_village:
            unique_loc_edges = {edge['_key']: edge for edge in located_in_village}
            db.collection('LOCATED_IN_VILLAGE').insert_many(list(unique_loc_edges.values()), overwrite=True)

    def get_mosques(self, dataset_id: str, limit: int = 1000, offset: int = 0, kabko: Optional[str] = None) -> List[Dict[str, Any]]:
        db = get_db()
        if dataset_id and dataset_id != "all":
            query = """
            FOR m IN Mosque
                FILTER m.dataset_id == @did
            """
            bind_vars = {"did": dataset_id, "limit": limit, "offset": offset}
        else:
            query = """
            FOR m IN Mosque
            """
            bind_vars = {"limit": limit, "offset": offset}
            
        if kabko:
            query += " FILTER LOWER(m.kabko) == LOWER(@kabko)"
            bind_vars["kabko"] = kabko
            
        query += """
            SORT m.priority_score DESC
            LIMIT @offset, @limit
            RETURN m
        """
        cursor = db.aql.execute(query, bind_vars=bind_vars)
        return [doc for doc in cursor]

    def count_mosques(self, dataset_id: str, kabko: Optional[str] = None) -> int:
        db = get_db()
        if dataset_id and dataset_id != "all":
            query = """
            FOR m IN Mosque
                FILTER m.dataset_id == @did
            """
            bind_vars = {"did": dataset_id}
        else:
            query = """
            FOR m IN Mosque
            """
            bind_vars = {}
            
        if kabko:
            query += " FILTER LOWER(m.kabko) == LOWER(@kabko)"
            bind_vars["kabko"] = kabko
        query += " COLLECT WITH COUNT INTO length RETURN length"
        cursor = db.aql.execute(query, bind_vars=bind_vars)
        res = [doc for doc in cursor]
        return res[0] if res else 0

    def get_mosque_by_id(self, dataset_id: str, mosque_id: str) -> Optional[Dict[str, Any]]:
        db = get_db()
        if dataset_id and dataset_id != "all":
            try:
                return db.collection('Mosque').get(f"{dataset_id}_{mosque_id}")
            except Exception:
                pass
        # Fallback: search across all mosques by the field 'id'
        try:
            query = "FOR m IN Mosque FILTER m.id == @m_id LIMIT 1 RETURN m"
            cursor = db.aql.execute(query, bind_vars={"m_id": mosque_id})
            res = [doc for doc in cursor]
            return res[0] if res else None
        except Exception:
            return None

    def get_mosques_in_bounds(self, dataset_id: str, bounds: tuple[float, float, float, float]) -> List[Dict[str, Any]]:
        db = get_db()
        south, north, west, east = bounds
        
        polygon = {
            "type": "Polygon",
            "coordinates": [[
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south]
            ]]
        }
        
        query = """
        FOR m IN Mosque
            FILTER m.dataset_id == @did
            FILTER GEO_CONTAINS(@poly, m.coordinate)
            SORT m.priority_score DESC
            RETURN m
        """
        bind_vars = {
            "did": dataset_id,
            "poly": polygon
        }
        cursor = db.aql.execute(query, bind_vars=bind_vars)
        return [doc for doc in cursor]

    def get_nearest_mosques(self, dataset_id: str, lat: float, lon: float, radius_km: float, limit: int = 100) -> List[Dict[str, Any]]:
        db = get_db()
        if dataset_id and dataset_id != "all":
            query = """
            FOR m IN Mosque
                FILTER m.dataset_id == @did
                FILTER GEO_DISTANCE([@lon, @lat], m.coordinate) <= @radius_m
                LET dist = GEO_DISTANCE([@lon, @lat], m.coordinate)
                SORT dist ASC
                LIMIT @limit
                RETURN MERGE(m, {distance_km: dist / 1000})
            """
            bind_vars = {
                "did": dataset_id,
                "lat": lat,
                "lon": lon,
                "radius_m": radius_km * 1000,
                "limit": limit
            }
        else:
            query = """
            FOR m IN Mosque
                FILTER GEO_DISTANCE([@lon, @lat], m.coordinate) <= @radius_m
                LET dist = GEO_DISTANCE([@lon, @lat], m.coordinate)
                SORT dist ASC
                LIMIT @limit
                RETURN MERGE(m, {distance_km: dist / 1000})
            """
            bind_vars = {
                "lat": lat,
                "lon": lon,
                "radius_m": radius_km * 1000,
                "limit": limit
            }
        cursor = db.aql.execute(query, bind_vars=bind_vars)
        return [doc for doc in cursor]

    def delete_mosque(self, dataset_id: str, mosque_id: str) -> bool:
        db = get_db()
        key = f"{dataset_id}_{mosque_id}"
        try:
            db.collection('Mosque').delete(key)
            db.aql.execute("""
                FOR edge IN LOCATED_IN_VILLAGE
                    FILTER edge._from == @m_id
                    REMOVE edge IN LOCATED_IN_VILLAGE
            """, bind_vars={"m_id": f"Mosque/{key}"})
            return True
        except Exception:
            return False

    def delete_mosques_bulk(self, dataset_id: str, mosque_ids: list[str]) -> bool:
        db = get_db()
        if not mosque_ids:
            return True
        keys = [f"{dataset_id}_{mid}" for mid in mosque_ids]
        try:
            # Delete from Mosque collection
            db.aql.execute("""
                FOR key IN @keys
                    REMOVE key IN Mosque
            """, bind_vars={"keys": keys})
            
            # Delete related edges
            db.aql.execute("""
                FOR key IN @keys
                    LET m_id = CONCAT('Mosque/', key)
                    FOR edge IN LOCATED_IN_VILLAGE
                        FILTER edge._from == m_id
                        REMOVE edge IN LOCATED_IN_VILLAGE
            """, bind_vars={"keys": keys})
            return True
        except Exception as e:
            print(f"Error in delete_mosques_bulk: {e}")
            return False

    def delete_all_mosques(self, dataset_id: str) -> None:
        db = get_db()
        db.aql.execute("""
            FOR m IN Mosque
                FILTER m.dataset_id == @did
                LET m_id = m._id
                REMOVE m IN Mosque
                FOR edge IN LOCATED_IN_VILLAGE
                    FILTER edge._from == m_id
                    REMOVE edge IN LOCATED_IN_VILLAGE
        """, bind_vars={"did": dataset_id})

    def create_mosque(self, dataset_id: str, data: Dict[str, Any]) -> str:
        db = get_db()
        col = db.collection('Mosque')
        
        import uuid
        mosque_id = str(uuid.uuid4()).replace('-', '')[:12]
        
        doc = {
            **data,
            "id": mosque_id,
            "dataset_id": dataset_id,
            "coordinate": [float(data['longitude']), float(data['latitude'])],
            "_key": f"{dataset_id}_{mosque_id}"
        }
        
        # Priority score default
        if 'priority_score' not in doc:
            doc['priority_score'] = 1.0
            
        col.insert(doc, overwrite=True)
        return mosque_id

    def update_mosque(self, dataset_id: str, mosque_id: str, data: Dict[str, Any]) -> bool:
        db = get_db()
        col = db.collection('Mosque')
        key = f"{dataset_id}_{mosque_id}"
        
        try:
            doc = col.get(key)
            if not doc:
                return False
                
            update_data = {**data}
            if 'latitude' in update_data and 'longitude' in update_data:
                update_data['coordinate'] = [float(update_data['longitude']), float(update_data['latitude'])]
                
            col.update(key, update_data)
            return True
        except Exception:
            return False

class ArangoDatasetRepository(DatasetRepository):
    def upsert_dataset(self, dataset_id: str, data: Dict[str, Any]) -> None:
        db = get_db()
        data['_key'] = dataset_id
        db.collection('datasets').insert(data, overwrite=True)

    def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        db = get_db()
        try:
            return db.collection('datasets').get(dataset_id)
        except Exception:
            return None

    def list_datasets(self) -> List[Dict[str, Any]]:
        db = get_db()
        cursor = db.aql.execute("FOR d IN datasets RETURN d")
        return [doc for doc in cursor]

    def set_active_dataset_id(self, dataset_id: str) -> None:
        db = get_db()
        db.collection('app_settings').insert({"_key": "active_dataset_id", "value": dataset_id}, overwrite=True)

    def get_active_dataset_id(self) -> str:
        db = get_db()
        try:
            doc = db.collection('app_settings').get("active_dataset_id")
            return doc.get("value") if doc else "banten"
        except Exception:
            return "banten"

    def save_osm_cache(self, cache_id: str, data: Dict[str, Any]) -> None:
        db = get_db()
        data['_key'] = cache_id
        if 'graphml_path' in data and data['graphml_path'] is not None:
            data['graphml_path'] = str(data['graphml_path'])
        db.collection('osm_graph_cache').insert(data, overwrite=True)
        
        # Ingest road nodes and road connections to ArangoDB collections
        try:
            from app.infrastructure.services.osm_graph import load_road_graph
            from pathlib import Path
            graph_path = Path(data.get("graphml_path", ""))
            if graph_path.exists():
                G = load_road_graph(graph_path)
                
                # Batch save nodes
                nodes_to_insert = []
                for node_id, node_data in G.nodes(data=True):
                    nodes_to_insert.append({
                        "_key": str(node_id),
                        "latitude": float(node_data.get("y", 0.0)),
                        "longitude": float(node_data.get("x", 0.0)),
                        "coordinate": [float(node_data.get("x", 0.0)), float(node_data.get("y", 0.0))]
                    })
                
                # Batch save edges
                edges_to_insert = []
                for u, v, key, edge_data in G.edges(keys=True, data=True):
                    edges_to_insert.append({
                        "_key": f"{u}_{v}_{key}",
                        "_from": f"RoadNode/{u}",
                        "_to": f"RoadNode/{v}",
                        "length": float(edge_data.get("length", 0.0)),
                        "travel_time": float(edge_data.get("travel_time", 0.0))
                    })
                
                # Insert in batches
                if nodes_to_insert:
                    db.collection('RoadNode').insert_many(nodes_to_insert, overwrite=True)
                if edges_to_insert:
                    db.collection('ROAD_CONNECTION').insert_many(edges_to_insert, overwrite=True)
        except Exception as exc:
            import sys
            print(f"Gagal menyimpan graph jalan ke ArangoDB: {exc}", file=sys.stderr)

    def get_osm_cache(self, cache_id: str = "latest") -> Optional[Dict[str, Any]]:
        db = get_db()
        try:
            return db.collection('osm_graph_cache').get(cache_id)
        except Exception:
            return None

    def delete_dataset(self, dataset_id: str) -> bool:
        db = get_db()
        try:
            db.collection('datasets').delete(dataset_id)
            return True
        except Exception:
            return False
