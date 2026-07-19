import copy
import hashlib
import os
import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple
from app.domain.repositories.mosque_repo import MosqueRepository
from app.domain.repositories.dataset_repo import DatasetRepository
from app.infrastructure.database.arangodb_client import (
    MOSQUE_SEARCH_ANALYZER,
    MOSQUE_SEARCH_VIEW,
    get_db,
)

NEAREST_QUERY_MAX_RUNTIME_SECONDS = max(
    0.1, float(os.getenv("IMOSQUE_NEAREST_QUERY_MAX_RUNTIME_SECONDS", "5"))
)

def _slugify_key(text: str) -> str:
    s = str(text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "empty"

class ArangoMosqueRepository(MosqueRepository):
    _lookup_cache: Dict[tuple[str, str], Dict[str, Any]] = {}
    _lookup_cache_lock = threading.RLock()
    _lookup_cache_max_entries = 10000

    @classmethod
    def _cache_mosque(cls, mosque: Dict[str, Any]) -> None:
        dataset_id = str(mosque.get("dataset_id") or "")
        mosque_id = str(mosque.get("id") or "")
        if not dataset_id or not mosque_id:
            return
        with cls._lookup_cache_lock:
            if len(cls._lookup_cache) >= cls._lookup_cache_max_entries:
                cls._lookup_cache.clear()
            cls._lookup_cache[(dataset_id, mosque_id)] = copy.deepcopy(mosque)

    @classmethod
    def _invalidate_lookup_cache(
        cls,
        dataset_id: str,
        mosque_ids: Optional[Sequence[str]] = None,
    ) -> None:
        """Invalidate cached lookups after a successful write."""
        dataset_key = str(dataset_id or "")
        with cls._lookup_cache_lock:
            if mosque_ids is not None:
                for mosque_id in mosque_ids:
                    cls._lookup_cache.pop((dataset_key, str(mosque_id)), None)
                return
            stale_keys = [key for key in cls._lookup_cache if key[0] == dataset_key]
            for key in stale_keys:
                cls._lookup_cache.pop(key, None)

    def save_mosques(self, dataset_id: str, mosques: List[Dict[str, Any]]) -> None:
        db = get_db()
        col = db.collection('Mosque')

        # An overwrite can replace fields from previously cached documents and
        # can also remove IDs when called after a dataset refresh.
        self._invalidate_lookup_cache(dataset_id)
        
        # Batch insert/upsert mosques
        for m in mosques:
            m['dataset_id'] = dataset_id
            m['coordinate'] = [float(m['longitude']), float(m['latitude'])]
            m['_key'] = f"{dataset_id}_{m.get('id', '')}"
            
        col.insert_many(mosques, overwrite=True)
        for mosque in mosques:
            self._cache_mosque(mosque)
        
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

    def search_mosques(
        self,
        dataset_id: str,
        query_text: str,
        limit: int = 10,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        db = get_db()
        normalized_query = " ".join(str(query_text or "").strip().split())
        if len(normalized_query) < 2:
            return []
        safe_limit = max(1, min(int(limit), 20))
        has_origin = latitude is not None and longitude is not None
        dataset_filter = "FILTER m.dataset_id == @did" if dataset_id and dataset_id != "all" else ""
        search_fields = ("name", "address", "kecamatan", "kabko", "provinsi")
        tokens = [
            token
            for token in re.findall(r"\w+", normalized_query.casefold())
            if len(token) >= 2
        ][:8]
        if not tokens:
            return []
        token_expressions = []
        for token_index in range(len(tokens)):
            field_expression = " OR ".join(
                f"m.{field} == @token_{token_index}"
                for field in search_fields
            )
            token_expressions.append(f"({field_expression})")
        search_expression = " AND ".join(token_expressions)
        aql = f"""
        FOR m IN {MOSQUE_SEARCH_VIEW}
            SEARCH ANALYZER(({search_expression}), '{MOSQUE_SEARCH_ANALYZER}')
            OPTIONS {{ waitForSync: true }}
            {dataset_filter}
            LET dist = @has_origin ? GEO_DISTANCE([@longitude, @latitude], m.coordinate) : null
            SORT BM25(m) DESC, dist ASC, m.priority_score DESC
            LIMIT @limit
            RETURN {{
                id: m.id, dataset_id: m.dataset_id, name: m.name,
                address: m.address, provinsi: m.provinsi, kabko: m.kabko,
                kecamatan: m.kecamatan, kelurahan: m.kelurahan,
                latitude: m.latitude, longitude: m.longitude,
                rating: m.rating, review_count: m.review_count,
                facilities: m.facilities, fasilitas: m.fasilitas,
                capacity_proxy: m.capacity_proxy, priority_score: m.priority_score,
                tier: m.tier,
                distance_km: dist == null ? null : dist / 1000
            }}
        """
        bind_vars: Dict[str, Any] = {
            "limit": safe_limit,
            "has_origin": has_origin,
            "latitude": float(latitude) if has_origin else 0.0,
            "longitude": float(longitude) if has_origin else 0.0,
        }
        bind_vars.update({f"token_{index}": token for index, token in enumerate(tokens)})
        if dataset_filter:
            bind_vars["did"] = dataset_id
        cursor = db.aql.execute(
            aql,
            bind_vars=bind_vars,
            max_runtime=NEAREST_QUERY_MAX_RUNTIME_SECONDS,
        )
        mosques = [doc for doc in cursor]
        for mosque in mosques:
            self._cache_mosque(mosque)
        return mosques

    def get_mosque_by_id(self, dataset_id: str, mosque_id: str) -> Optional[Dict[str, Any]]:
        with self._lookup_cache_lock:
            cached = self._lookup_cache.get((dataset_id, mosque_id))
            if cached is not None:
                return copy.deepcopy(cached)
        db = get_db()
        if dataset_id and dataset_id != "all":
            try:
                mosque = db.collection('Mosque').get(f"{dataset_id}_{mosque_id}")
                if mosque:
                    self._cache_mosque(mosque)
                return mosque
            except Exception:
                pass
        # Fallback uses persistent indexes and never returns an ID collision from
        # another dataset when a dataset was explicitly selected.
        try:
            if dataset_id and dataset_id != "all":
                query = "FOR m IN Mosque FILTER m.dataset_id == @did AND m.id == @m_id LIMIT 1 RETURN m"
                bind_vars = {"did": dataset_id, "m_id": mosque_id}
            else:
                query = "FOR m IN Mosque FILTER m.id == @m_id LIMIT 1 RETURN m"
                bind_vars = {"m_id": mosque_id}
            cursor = db.aql.execute(query, bind_vars=bind_vars)
            res = [doc for doc in cursor]
            mosque = res[0] if res else None
            if mosque:
                self._cache_mosque(mosque)
            return mosque
        except Exception:
            return None

    def get_mosques_in_bounds(
        self,
        dataset_id: str,
        bounds: tuple[float, float, float, float],
        limit: int = 600,
        anchors: Optional[Sequence[Tuple[float, float]]] = None,
    ) -> List[Dict[str, Any]]:
        db = get_db()
        south, north, west, east = bounds
        safe_limit = max(50, min(int(limit), 2000))
        anchor_points = list(anchors or [])
        start = anchor_points[0] if anchor_points else ((south + north) / 2, (west + east) / 2)
        end = anchor_points[1] if len(anchor_points) > 1 else start
        center = ((south + north) / 2, (west + east) / 2)
        
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
        
        dataset_filter = "FILTER m.dataset_id == @did" if dataset_id and dataset_id != "all" else ""
        query = f"""
        FOR m IN Mosque
            {dataset_filter}
            FILTER GEO_CONTAINS(@poly, m.coordinate)
            LET anchor_distance = MIN([
                GEO_DISTANCE(@start, m.coordinate),
                GEO_DISTANCE(@end, m.coordinate),
                GEO_DISTANCE(@center, m.coordinate)
            ])
            SORT anchor_distance ASC, m.priority_score DESC
            LIMIT @limit
            RETURN {{
                id: m.id, dataset_id: m.dataset_id, name: m.name,
                address: m.address, province: m.province, provinsi: m.provinsi,
                kabko: m.kabko, kecamatan: m.kecamatan, kelurahan: m.kelurahan,
                latitude: m.latitude, longitude: m.longitude, coordinate: m.coordinate,
                rating: m.rating, review_count: m.review_count,
                facilities: m.facilities, fasilitas: m.fasilitas,
                capacity_proxy: m.capacity_proxy, priority_score: m.priority_score,
                tier: m.tier
            }}
        """
        bind_vars = {
            "poly": polygon,
            "limit": safe_limit,
            "start": [float(start[1]), float(start[0])],
            "end": [float(end[1]), float(end[0])],
            "center": [float(center[1]), float(center[0])],
        }
        if dataset_filter:
            bind_vars["did"] = dataset_id
        cursor = db.aql.execute(
            query,
            bind_vars=bind_vars,
            max_runtime=NEAREST_QUERY_MAX_RUNTIME_SECONDS,
        )
        mosques = [doc for doc in cursor]
        for mosque in mosques:
            self._cache_mosque(mosque)
        return mosques

    def get_nearest_mosques(self, dataset_id: str, lat: float, lon: float, radius_km: float, limit: int = 100) -> List[Dict[str, Any]]:
        db = get_db()
        if dataset_id and dataset_id != "all":
            query = """
            FOR m IN Mosque
                FILTER m.dataset_id == @did
                LET dist = GEO_DISTANCE([@lon, @lat], m.coordinate)
                FILTER dist <= @radius_m
                SORT dist ASC
                LIMIT @limit
                RETURN {
                    id: m.id, dataset_id: m.dataset_id, name: m.name,
                    address: m.address, provinsi: m.provinsi, kabko: m.kabko,
                    kecamatan: m.kecamatan, kelurahan: m.kelurahan,
                    latitude: m.latitude, longitude: m.longitude,
                    rating: m.rating, review_count: m.review_count,
                    facilities: m.facilities, fasilitas: m.fasilitas,
                    capacity_proxy: m.capacity_proxy, priority_score: m.priority_score,
                    tier: m.tier, distance_km: dist / 1000
                }
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
                LET dist = GEO_DISTANCE([@lon, @lat], m.coordinate)
                FILTER dist <= @radius_m
                SORT dist ASC
                LIMIT @limit
                RETURN {
                    id: m.id, dataset_id: m.dataset_id, name: m.name,
                    address: m.address, provinsi: m.provinsi, kabko: m.kabko,
                    kecamatan: m.kecamatan, kelurahan: m.kelurahan,
                    latitude: m.latitude, longitude: m.longitude,
                    rating: m.rating, review_count: m.review_count,
                    facilities: m.facilities, fasilitas: m.fasilitas,
                    capacity_proxy: m.capacity_proxy, priority_score: m.priority_score,
                    tier: m.tier, distance_km: dist / 1000
                }
            """
            bind_vars = {
                "lat": lat,
                "lon": lon,
                "radius_m": radius_km * 1000,
                "limit": limit
            }
        cursor = db.aql.execute(
            query,
            bind_vars=bind_vars,
            max_runtime=NEAREST_QUERY_MAX_RUNTIME_SECONDS,
        )
        mosques = [doc for doc in cursor]
        for mosque in mosques:
            self._cache_mosque(mosque)
        return mosques

    def delete_mosque(self, dataset_id: str, mosque_id: str) -> bool:
        db = get_db()
        key = f"{dataset_id}_{mosque_id}"
        try:
            db.collection('Mosque').delete(key)
        except Exception:
            return False

        self._invalidate_lookup_cache(dataset_id, [mosque_id])
        try:
            db.aql.execute("""
                FOR edge IN LOCATED_IN_VILLAGE
                    FILTER edge._from == @m_id
                    REMOVE edge IN LOCATED_IN_VILLAGE
            """, bind_vars={"m_id": f"Mosque/{key}"})
        except Exception as exc:
            # The source document is already gone. Keep the mutation visible
            # to callers so higher-level caches/revisions are invalidated;
            # dangling edges can be cleaned independently.
            print(f"Warning: failed to delete mosque edges for {key}: {exc}")
        return True

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
        except Exception as exc:
            print(f"Error in delete_mosques_bulk: {exc}")
            return False

        self._invalidate_lookup_cache(dataset_id, mosque_ids)
        try:
            # Delete related edges after the primary documents are gone.
            db.aql.execute("""
                FOR key IN @keys
                    LET m_id = CONCAT('Mosque/', key)
                    FOR edge IN LOCATED_IN_VILLAGE
                        FILTER edge._from == m_id
                        REMOVE edge IN LOCATED_IN_VILLAGE
            """, bind_vars={"keys": keys})
        except Exception as exc:
            print(f"Warning: failed to delete mosque edges in bulk: {exc}")
        return True

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
        self._invalidate_lookup_cache(dataset_id)

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
        self._cache_mosque(doc)
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
            if 'latitude' in update_data or 'longitude' in update_data:
                # A partial coordinate edit must reuse the persisted other
                # component. Otherwise `coordinate` silently remains stale.
                latitude = update_data.get('latitude', doc.get('latitude'))
                longitude = update_data.get('longitude', doc.get('longitude'))
                if latitude is None or longitude is None:
                    return False
                update_data['coordinate'] = [float(longitude), float(latitude)]

            # python-arango updates by document handle, not by positional
            # ``key, patch`` arguments.
            col.update({"_key": key, **update_data})
            cached_doc = {**doc, **update_data, "_key": key}
            self._cache_mosque(cached_doc)
            return True
        except Exception:
            return False

class ArangoDatasetRepository(DatasetRepository):
    def upsert_dataset(self, dataset_id: str, data: Dict[str, Any]) -> None:
        db = get_db()
        collection = db.collection('datasets')
        payload = copy.deepcopy(data)
        # System metadata returned by reads cannot be sent back through an
        # insert/replace operation.
        for field in ('_id', '_rev', '_oldRev'):
            payload.pop(field, None)
        try:
            persisted = collection.get(dataset_id) or {}
            persisted_revision = int(persisted.get('data_revision', 0))
        except (TypeError, ValueError):
            persisted_revision = 0
        try:
            requested_revision = int(payload.get('data_revision', 0))
        except (TypeError, ValueError):
            requested_revision = 0
        payload['data_revision'] = max(0, persisted_revision, requested_revision)
        payload['_key'] = dataset_id
        collection.insert(payload, overwrite=True)

    def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        db = get_db()
        try:
            dataset = db.collection('datasets').get(dataset_id)
            if dataset:
                dataset.setdefault('data_revision', 0)
            return dataset
        except Exception:
            return None

    def list_datasets(self) -> List[Dict[str, Any]]:
        db = get_db()
        cursor = db.aql.execute("FOR d IN datasets RETURN d")
        datasets = [doc for doc in cursor]
        for dataset in datasets:
            dataset.setdefault('data_revision', 0)
        return datasets

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
        graph_id = str(cache_id or "latest")
        graph_prefix = hashlib.sha1(graph_id.encode("utf-8")).hexdigest()[:16]
        ingest_graph = bool(data.pop("ingest_graph", True))
        data = {**data, '_key': graph_id, 'graph_id': graph_id}
        if 'graphml_path' in data and data['graphml_path'] is not None:
            data['graphml_path'] = str(data['graphml_path'])
        
        # Routing reads GraphML directly. Large administrative-area builds can
        # therefore publish metadata immediately without blocking on a second,
        # redundant copy of every road node and edge in ArangoDB.
        if not ingest_graph:
            data['ingested_nodes'] = 0
            data['ingested_edges'] = 0
            data['sync_status'] = 'file_only'
            db.collection('osm_graph_cache').insert(data, overwrite=True)
            return

        # Optional full graph mirror for installations that query roads in ArangoDB.
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
                        "_key": f"{graph_prefix}_{node_id}",
                        "osm_node_id": str(node_id),
                        "graph_id": graph_id,
                        "latitude": float(node_data.get("y", 0.0)),
                        "longitude": float(node_data.get("x", 0.0)),
                        "coordinate": [float(node_data.get("x", 0.0)), float(node_data.get("y", 0.0))]
                    })
                
                # Batch save edges
                edges_to_insert = []
                edge_iter = (
                    G.edges(keys=True, data=True)
                    if G.is_multigraph()
                    else ((u, v, 0, attrs) for u, v, attrs in G.edges(data=True))
                )
                for u, v, key, edge_data in edge_iter:
                    edges_to_insert.append({
                        "_key": f"{graph_prefix}_{u}_{v}_{key}",
                        "_from": f"RoadNode/{graph_prefix}_{u}",
                        "_to": f"RoadNode/{graph_prefix}_{v}",
                        "graph_id": graph_id,
                        "length": float(edge_data.get("length", 0.0)),
                        "travel_time": float(edge_data.get("travel_time", 0.0))
                    })
                
                db.aql.execute("""
                    FOR e IN ROAD_CONNECTION
                        FILTER e.graph_id == @graph_id OR e.graph_id == null
                        REMOVE e IN ROAD_CONNECTION
                """, bind_vars={"graph_id": graph_id})
                db.aql.execute("""
                    FOR n IN RoadNode
                        FILTER n.graph_id == @graph_id OR n.graph_id == null
                        REMOVE n IN RoadNode
                """, bind_vars={"graph_id": graph_id})

                batch_size = 10000
                for start in range(0, len(nodes_to_insert), batch_size):
                    db.collection('RoadNode').insert_many(nodes_to_insert[start:start + batch_size], overwrite=True)
                for start in range(0, len(edges_to_insert), batch_size):
                    db.collection('ROAD_CONNECTION').insert_many(edges_to_insert[start:start + batch_size], overwrite=True)

                data['ingested_nodes'] = len(nodes_to_insert)
                data['ingested_edges'] = len(edges_to_insert)
                data['sync_status'] = 'completed'
                db.collection('osm_graph_cache').insert(data, overwrite=True)
        except Exception as exc:
            raise RuntimeError(f"Gagal menyinkronkan graph jalan ke ArangoDB: {exc}") from exc

    def get_osm_cache(self, cache_id: str = "latest") -> Optional[Dict[str, Any]]:
        db = get_db()
        try:
            return db.collection('osm_graph_cache').get(cache_id)
        except Exception:
            return None

    def delete_osm_cache(self, cache_id: str) -> None:
        db = get_db()
        graph_id = str(cache_id or "latest")
        db.aql.execute("""
            FOR e IN ROAD_CONNECTION
                FILTER e.graph_id == @graph_id
                REMOVE e IN ROAD_CONNECTION
        """, bind_vars={"graph_id": graph_id})
        db.aql.execute("""
            FOR n IN RoadNode
                FILTER n.graph_id == @graph_id
                REMOVE n IN RoadNode
        """, bind_vars={"graph_id": graph_id})
        try:
            db.collection('osm_graph_cache').delete(graph_id, ignore_missing=True)
        except TypeError:
            if db.collection('osm_graph_cache').has(graph_id):
                db.collection('osm_graph_cache').delete(graph_id)

    def delete_dataset(self, dataset_id: str) -> bool:
        db = get_db()
        try:
            db.collection('datasets').delete(dataset_id)
            return True
        except Exception:
            return False
