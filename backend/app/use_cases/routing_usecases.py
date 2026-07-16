import os
import copy
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional
from app.domain.repositories.mosque_repo import MosqueRepository
from app.domain.repositories.dataset_repo import DatasetRepository
from app.infrastructure.services.routing_osm import route_via_osm_dijkstra, route_to_mosque
from app.infrastructure.services.osm_graph import build_osm_graph_for_bbox, build_osm_graph_for_route, graph_bounds, DEFAULT_GRAPHML, get_graphml_path

class RoutingUseCases:
    def __init__(self, mosque_repo: MosqueRepository, dataset_repo: DatasetRepository):
        self.mosque_repo = mosque_repo
        self.dataset_repo = dataset_repo
        self._recommend_cache: "OrderedDict[tuple, tuple[float, Dict[str, Any]]]" = OrderedDict()
        self._recommend_cache_lock = threading.RLock()
        self._recommend_singleflight = tuple(threading.Lock() for _ in range(16))
        self._mosque_candidate_cache: "OrderedDict[tuple, tuple[Dict[str, Any], ...]]" = OrderedDict()
        self._mosque_candidate_cache_lock = threading.RLock()
        self._mosque_candidate_prewarm_threads: Dict[tuple, threading.Thread] = {}
        self._mosque_candidate_cache_max_datasets = max(
            1, int(os.getenv("IMOSQUE_MOSQUE_CACHE_DATASETS", "2"))
        )
        self._mosque_candidate_cache_max_rows = max(
            0, int(os.getenv("IMOSQUE_MOSQUE_CACHE_MAX_ROWS", "25000"))
        )

    @staticmethod
    def _inline_build_enabled(requested: bool) -> bool:
        """Keep expensive Overpass/GraphML builds out of interactive requests by default."""
        enabled = os.getenv("IMOSQUE_ALLOW_INLINE_OSM_BUILD", "false").strip().lower() in {"1", "true", "yes"}
        return bool(requested and enabled)

    @staticmethod
    def _data_revision(dataset_profile: Dict[str, Any]) -> Any:
        return (
            dataset_profile.get("data_revision", 0)
            if "data_revision" in dataset_profile
            else dataset_profile.get("_rev")
        )

    def _get_cached_mosque_candidates(self, dataset_id: str, data_revision: Any) -> Optional[List[Dict[str, Any]]]:
        cache_key = (str(dataset_id), data_revision)
        with self._mosque_candidate_cache_lock:
            cached = self._mosque_candidate_cache.get(cache_key)
            if cached is None:
                return None
            self._mosque_candidate_cache.move_to_end(cache_key)
            return list(cached)

    def _load_mosque_candidate_snapshot(self, dataset_id: str, data_revision: Any) -> None:
        """Load one complete, revision-bound routing snapshot in the background."""
        if self._mosque_candidate_cache_max_rows <= 0:
            return
        try:
            total = self.mosque_repo.count_mosques(dataset_id)
            if not isinstance(total, int) or total < 0 or total > self._mosque_candidate_cache_max_rows:
                return
            rows = self.mosque_repo.get_mosques(dataset_id, limit=max(1, total), offset=0)
            if not isinstance(rows, list):
                rows = list(rows)
            if len(rows) != total:
                # A concurrent write can move pagination/count boundaries. The
                # next revision will prewarm a coherent snapshot instead.
                return
            latest_profile = self.dataset_repo.get_dataset(dataset_id) or {}
            if self._data_revision(latest_profile) != data_revision:
                return
            cache_key = (str(dataset_id), data_revision)
            with self._mosque_candidate_cache_lock:
                stale_keys = [key for key in self._mosque_candidate_cache if key[0] == str(dataset_id)]
                for key in stale_keys:
                    self._mosque_candidate_cache.pop(key, None)
                self._mosque_candidate_cache[cache_key] = tuple(rows)
                self._mosque_candidate_cache.move_to_end(cache_key)
                while len(self._mosque_candidate_cache) > self._mosque_candidate_cache_max_datasets:
                    self._mosque_candidate_cache.popitem(last=False)
        except Exception:
            # The indexed corridor query remains the authoritative fallback.
            return

    def start_mosque_candidate_prewarm(
        self,
        dataset_id: str,
        data_revision: Any = None,
    ) -> bool:
        """Start one revision-aware mosque snapshot prewarm; never block the API."""
        if self._mosque_candidate_cache_max_rows <= 0:
            return False
        if data_revision is None:
            try:
                data_revision = self._data_revision(self.dataset_repo.get_dataset(dataset_id) or {})
            except Exception:
                return False
        cache_key = (str(dataset_id), data_revision)
        with self._mosque_candidate_cache_lock:
            if cache_key in self._mosque_candidate_cache:
                self._mosque_candidate_cache.move_to_end(cache_key)
                return False
            existing = self._mosque_candidate_prewarm_threads.get(cache_key)
            if existing is not None and existing.is_alive():
                return False

            def run() -> None:
                try:
                    self._load_mosque_candidate_snapshot(dataset_id, data_revision)
                finally:
                    with self._mosque_candidate_cache_lock:
                        if self._mosque_candidate_prewarm_threads.get(cache_key) is threading.current_thread():
                            self._mosque_candidate_prewarm_threads.pop(cache_key, None)

            thread = threading.Thread(
                target=run,
                name=f"imosque-mosque-candidates-{dataset_id}-{data_revision}",
                daemon=True,
            )
            self._mosque_candidate_prewarm_threads[cache_key] = thread
            thread.start()
            return True

    def route_via_osm_dijkstra(
        self,
        start_lat: float, start_lon: float,
        end_lat: float, end_lon: float,
        algorithm: str,
        current_time: Optional[str],
        prayer_time: Optional[str],
        max_candidates: int,
        auto_build_osm: bool,
        buffer_km: float,
        dataset_id: str,
        profile: str = "balanced"
    ) -> Dict[str, Any]:
        # Provide a repository callback for routing to fetch mosques
        def fetch_mosques(did: str, bounds: Optional[tuple[float, float, float, float]] = None) -> List[Dict[str, Any]]:
            cached_candidates = self._get_cached_mosque_candidates(did, data_revision)
            if cached_candidates is not None:
                return cached_candidates
            if bounds:
                # A narrow indexed corridor is sufficient in the common case.
                # Only run the wider geo-nearest query when that corridor is empty.
                query_limit = max(72, min(400, int(max_candidates) * 24))
                corridor_rows = self.mosque_repo.get_mosques_in_bounds(
                    did,
                    bounds,
                    limit=query_limit,
                    anchors=((start_lat, start_lon), (end_lat, end_lon)),
                )
                if corridor_rows:
                    self.start_mosque_candidate_prewarm(did, data_revision)
                    return corridor_rows
                fallback_radius_km = max(25.0, max(float(buffer_km), 5.0) * 4.0)
                fallback_rows = self.mosque_repo.get_nearest_mosques(
                    did,
                    float(start_lat),
                    float(start_lon),
                    fallback_radius_km,
                    query_limit,
                )
                self.start_mosque_candidate_prewarm(did, data_revision)
                return fallback_rows
            return self.mosque_repo.get_mosques(did, limit=5000)
            
        def save_osm_cache(cache_id: str = "latest", **kwargs):
            self.dataset_repo.save_osm_cache(cache_id, kwargs)
            
        g_path = get_graphml_path(dataset_id)
        try:
            dataset_profile = self.dataset_repo.get_dataset(dataset_id) or {}
        except Exception:
            dataset_profile = {}
        data_revision = self._data_revision(dataset_profile)
        try:
            graph_version = g_path.stat().st_mtime_ns
        except OSError:
            graph_version = 0
        cache_key = (
            dataset_id, data_revision, graph_version,
            round(float(start_lat), 4), round(float(start_lon), 4),
            round(float(end_lat), 4), round(float(end_lon), 4),
            algorithm.lower(), current_time, prayer_time, int(max_candidates),
            round(float(buffer_km), 2), profile.lower(),
        )

        def get_cached():
            now = time.monotonic()
            with self._recommend_cache_lock:
                cached = self._recommend_cache.get(cache_key)
                if cached and now - cached[0] <= 300:
                    self._recommend_cache.move_to_end(cache_key)
                    result = copy.deepcopy(cached[1])
                    result["cache_hit"] = True
                    return result
                if cached:
                    self._recommend_cache.pop(cache_key, None)
            return None

        cached_result = get_cached()
        if cached_result is not None:
            return cached_result

        lock = self._recommend_singleflight[hash(cache_key) % len(self._recommend_singleflight)]
        with lock:
            cached_result = get_cached()
            if cached_result is not None:
                return cached_result
            result = route_via_osm_dijkstra(
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                algorithm=algorithm,
                current_time=current_time,
                prayer_time=prayer_time,
                max_candidates=max_candidates,
                auto_build_osm=self._inline_build_enabled(auto_build_osm),
                buffer_km=buffer_km,
                dataset_id=dataset_id,
                profile=profile,
                graphml_path=g_path,
                fetch_mosques_fn=fetch_mosques,
                save_osm_cache_fn=save_osm_cache
            )
            result["cache_hit"] = False
            with self._recommend_cache_lock:
                self._recommend_cache[cache_key] = (time.monotonic(), copy.deepcopy(result))
                self._recommend_cache.move_to_end(cache_key)
                while len(self._recommend_cache) > 128:
                    self._recommend_cache.popitem(last=False)
            return result

    def route_to_mosque(
        self,
        start_lat: float, start_lon: float,
        mosque_id: str,
        algorithm: str,
        auto_build_osm: bool,
        buffer_km: float,
        dataset_id: str
    ) -> Dict[str, Any]:
        def save_osm_cache(cache_id: str = "latest", **kwargs):
            self.dataset_repo.save_osm_cache(cache_id, kwargs)

        mosque = self.mosque_repo.get_mosque_by_id(dataset_id, mosque_id)
        if not mosque:
            raise ValueError(f"Mosque {mosque_id} not found")
            
        g_path = get_graphml_path(dataset_id)
        return route_to_mosque(
            start_lat=start_lat,
            start_lon=start_lon,
            mosque=mosque,
            algorithm=algorithm,
            auto_build_osm=self._inline_build_enabled(auto_build_osm),
            buffer_km=buffer_km,
            dataset_id=dataset_id,
            graphml_path=g_path,
            save_osm_cache_fn=save_osm_cache
        )

    def build_osm_bbox(self, north: float, south: float, east: float, west: float, network_type: str, dataset_id: Optional[str] = None, build_scope: str = "custom_bbox") -> Dict[str, Any]:
        g_path = get_graphml_path(dataset_id)
        G = build_osm_graph_for_bbox(north, south, east, west, network_type, g_path)
        bounds = graph_bounds(G)
        self.dataset_repo.save_osm_cache(dataset_id or "latest", {
            "graphml_path": str(g_path),
            "south": bounds[0], "north": bounds[1], "west": bounds[2], "east": bounds[3],
            "buffer_km": None, "network_type": network_type,
            "nodes": len(G.nodes), "edges": len(G.edges),
            "ingest_graph": False,
            "build_scope": build_scope,
            "file_size_bytes": g_path.stat().st_size,
            "file_mtime_ns": g_path.stat().st_mtime_ns,
        })
        return {
            "status": "success",
            "cache_path": str(g_path),
            "nodes": len(G.nodes), "edges": len(G.edges),
            "network_type": network_type,
            "build_scope": build_scope,
        }

    def build_osm_route(self, start_lat: float, start_lon: float, end_lat: float, end_lon: float, buffer_km: float, network_type: str, dataset_id: Optional[str] = None) -> Dict[str, Any]:
        g_path = get_graphml_path(dataset_id)
        G = build_osm_graph_for_route(
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            buffer_km=buffer_km,
            network_type=network_type,
            output_graphml=g_path,
        )
        bounds = graph_bounds(G)
        self.dataset_repo.save_osm_cache(dataset_id or "latest", {
            "graphml_path": str(g_path),
            "south": bounds[0], "north": bounds[1], "west": bounds[2], "east": bounds[3],
            "buffer_km": buffer_km, "network_type": network_type,
            "nodes": len(G.nodes), "edges": len(G.edges),
            "ingest_graph": False,
        })
        return {
            "status": "success",
            "cache_path": str(g_path),
            "nodes": len(G.nodes), "edges": len(G.edges),
            "buffer_km": buffer_km,
            "network_type": network_type
        }
