import copy
import pandas as pd
import io
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.domain.repositories.mosque_repo import MosqueRepository
from app.domain.repositories.dataset_repo import DatasetRepository
from app.infrastructure.services.ml_enrichment_service import MLEnrichmentService, slugify_dataset_name
from app.infrastructure.services.osm_graph import (
    DATA_DIR,
    evict_road_graph,
    get_graphml_path,
    runtime_graph_cache_path,
)

from fastapi import BackgroundTasks

NEAREST_SINGLEFLIGHT_WAIT_SECONDS = max(
    0.1, float(os.getenv("IMOSQUE_NEAREST_SINGLEFLIGHT_WAIT_SECONDS", "8"))
)

class DatasetUseCases:
    def __init__(self, mosque_repo: MosqueRepository, dataset_repo: DatasetRepository):
        self.mosque_repo = mosque_repo
        self.dataset_repo = dataset_repo
        self._nearest_cache: "OrderedDict[tuple, tuple[float, Dict[str, Any]]]" = OrderedDict()
        self._nearest_cache_lock = threading.RLock()
        self._nearest_inflight: Dict[tuple, threading.Event] = {}
        self._nearest_generation: Dict[str, int] = {}
        self._dataset_revision_lock = threading.RLock()

    @staticmethod
    def _read_data_revision(profile: Any) -> int:
        if not isinstance(profile, dict):
            return 0
        try:
            return max(0, int(profile.get("data_revision", 0)))
        except (TypeError, ValueError):
            return 0

    def _invalidate_nearest_cache(self, dataset_id: str) -> None:
        """Invalidate dataset-specific and cross-dataset nearest results."""
        did = str(dataset_id or "all")
        scopes = {"all"} if did == "all" else {did, "all"}
        with self._nearest_cache_lock:
            for scope in scopes:
                self._nearest_generation[scope] = self._nearest_generation.get(scope, 0) + 1
            stale_keys = [key for key in self._nearest_cache if key[0] in scopes]
            for key in stale_keys:
                self._nearest_cache.pop(key, None)

    def _record_dataset_mutation(
        self,
        dataset_id: str,
        *,
        profile_updates: Optional[Dict[str, Any]] = None,
        mosque_count_delta: Optional[int] = None,
    ) -> int:
        """Publish a monotonic revision and invalidate derived query results."""
        did = slugify_dataset_name(dataset_id)
        self._invalidate_nearest_cache(did)
        with self._dataset_revision_lock:
            current = self.dataset_repo.get_dataset(did)
            current_profile = dict(current) if isinstance(current, dict) else {}
            updates = dict(profile_updates or {})
            current_revision = max(
                self._read_data_revision(current_profile),
                self._read_data_revision(updates),
            )
            payload = {**current_profile, **updates}
            payload["dataset_id"] = did
            payload["data_revision"] = current_revision + 1
            if mosque_count_delta is not None:
                try:
                    mosque_count = int(current_profile.get("mosque_count", 0))
                except (TypeError, ValueError):
                    mosque_count = 0
                payload["mosque_count"] = max(0, mosque_count + int(mosque_count_delta))
            self.dataset_repo.upsert_dataset(did, payload)
            return payload["data_revision"]

    def invalidate_osm_graph(self, dataset_id: str) -> None:
        """Remove road cache derived from a dataset whose contents changed."""
        did = slugify_dataset_name(dataset_id)
        self._invalidate_nearest_cache(did)
        graph_path = get_graphml_path(did)
        evict_road_graph(graph_path)
        if graph_path.exists():
            graph_path.unlink()
        binary_cache_path = runtime_graph_cache_path(graph_path)
        if binary_cache_path.exists():
            binary_cache_path.unlink()
        self.dataset_repo.delete_osm_cache(did)

    def get_active_dataset_id(self) -> str:
        return self.dataset_repo.get_active_dataset_id()

    def set_active_dataset_id(self, dataset_id: str) -> None:
        self.dataset_repo.set_active_dataset_id(slugify_dataset_name(dataset_id))

    def list_datasets(self) -> List[Dict[str, Any]]:
        active = self.get_active_dataset_id()
        datasets = self.dataset_repo.list_datasets()
        for d in datasets:
            d['is_active'] = d['_key'] == active
            d['dataset_id'] = d['_key']
        return datasets

    def get_dataset_profile(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        did = slugify_dataset_name(dataset_id)
        return self.dataset_repo.get_dataset(did)

    def upload_and_process_dataset(
        self,
        file_bytes: bytes,
        filename: str,
        dataset_name: Optional[str] = None,
        make_active: bool = True,
        background_tasks: Optional[BackgroundTasks] = None
    ) -> Dict[str, Any]:
        base_name = dataset_name or filename or "dataset"
        did = slugify_dataset_name(base_name)
        existing_profile = self.dataset_repo.get_dataset(did)
        existing_revision = self._read_data_revision(existing_profile)
        
        # Simpan status awal (processing 10%)
        initial_profile = {
            "dataset_id": did,
            "filename": filename,
            "dataset_label": base_name.replace("_", " ").title(),
            "processed": False,
            "processing_status": "processing",
            "progress_percent": 10,
            "message": "Mengunggah file CSV...",
            "data_revision": existing_revision,
        }
        self.dataset_repo.upsert_dataset(did, initial_profile)
        
        def run_pipeline_internal():
            try:
                # Update status (30%)
                self.dataset_repo.upsert_dataset(did, {
                    **initial_profile,
                    "progress_percent": 30,
                    "message": "Membaca file CSV..."
                })
                
                try:
                    df = pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python")
                except Exception:
                    df = pd.read_csv(io.BytesIO(file_bytes))
                
                # Update status (60%)
                self.dataset_repo.upsert_dataset(did, {
                    **initial_profile,
                    "progress_percent": 60,
                    "message": "Menjalankan pemrosesan ML (rating & fasilitas)..."
                })
                
                records, profile = MLEnrichmentService.clean_and_enrich(df, did)
                
                profile['filename'] = filename
                profile['processed'] = True
                profile['processing_status'] = "completed"
                profile['progress_percent'] = 100
                profile['message'] = "Selesai!"
                profile['data_revision'] = existing_revision
                
                # Update status (80%)
                self.dataset_repo.upsert_dataset(did, {
                    **profile,
                    "progress_percent": 80,
                    "message": "Menyimpan data masjid ke ArangoDB..."
                })
                
                self.mosque_repo.delete_all_mosques(did)
                self.mosque_repo.save_mosques(did, records)
                self.invalidate_osm_graph(did)
                
                # Selesai! Update status ke completed
                self._record_dataset_mutation(did, profile_updates=profile)
                
                if make_active:
                    self.set_active_dataset_id(did)
                    
            except Exception as exc:
                import traceback
                traceback.print_exc()
                self.dataset_repo.upsert_dataset(did, {
                    **initial_profile,
                    "processing_status": "failed",
                    "progress_percent": 100,
                    "message": f"Gagal: {str(exc)}"
                })

        if background_tasks:
            background_tasks.add_task(run_pipeline_internal)
            return {
                "dataset_id": did,
                "filename": filename,
                "processed": False,
                "processing_status": "processing",
                "progress_percent": 10,
                "message": "Pemrosesan asinkron dimulai di latar belakang."
            }
        else:
            run_pipeline_internal()
            profile = self.dataset_repo.get_dataset(did)
            return {
                "dataset_id": did,
                "filename": filename,
                "processed": True,
                "is_active": make_active,
                "profile": profile
            }

    def run_pipeline(self, dataset_id: str) -> Dict[str, Any]:
        did = slugify_dataset_name(dataset_id)
        # Find raw CSV file in data/raw/datasets/
        csv_path = DATA_DIR / "raw" / "datasets" / f"{did}.csv"
        if not csv_path.exists():
            # Fallback to checking the root raw folder
            csv_path = DATA_DIR / "raw" / f"dataset_masjid_{did}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"File CSV untuk dataset '{did}' tidak ditemukan di disk.")
            
        with open(csv_path, "rb") as f:
            file_bytes = f.read()
            
        return self.upload_and_process_dataset(file_bytes, csv_path.name, did, make_active=False)

    def get_mosques(self, dataset_id: str, limit: int = 1000, offset: int = 0, kabko: Optional[str] = None) -> Dict[str, Any]:
        did = slugify_dataset_name(dataset_id)
        items = self.mosque_repo.get_mosques(did, limit, offset, kabko)
        total = self.mosque_repo.count_mosques(did, kabko)
        return {
            "dataset_id": did,
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items
        }

    def search_mosques(
        self,
        dataset_id: str,
        query_text: str,
        limit: int = 10,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> Dict[str, Any]:
        did = "all" if not dataset_id or dataset_id == "all" else slugify_dataset_name(dataset_id)
        items = self.mosque_repo.search_mosques(
            did,
            query_text,
            limit,
            latitude,
            longitude,
        )
        return {
            "dataset_id": did,
            "query": query_text,
            "items": items,
            "total": len(items),
        }

    def get_dataset_bbox(self, dataset_id: str) -> Dict[str, Any]:
        """Calculate a robust bbox from every valid coordinate in a dataset."""
        import math
        import numpy as np

        did = slugify_dataset_name(dataset_id)
        total = self.mosque_repo.count_mosques(did)
        if total == 0:
            raise ValueError("Dataset tidak memiliki data masjid.")
        mosques = self.mosque_repo.get_mosques(did, limit=total)
        coords = []
        for mosque in mosques:
            try:
                lat = float(mosque.get("latitude"))
                lon = float(mosque.get("longitude"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(lat) and math.isfinite(lon) and -11.5 <= lat <= 6.5 and 94 <= lon <= 142.5:
                coords.append((lat, lon))
        if not coords:
            raise ValueError("Dataset tidak memiliki koordinat valid.")

        values = np.asarray(coords, dtype=float)
        q1 = np.quantile(values, 0.25, axis=0)
        q3 = np.quantile(values, 0.75, axis=0)
        lower = q1 - 1.5 * (q3 - q1)
        upper = q3 + 1.5 * (q3 - q1)
        clean = values[np.all((values >= lower) & (values <= upper), axis=1)]
        if len(clean) == 0:
            clean = values

        south, west = clean.min(axis=0)
        north, east = clean.max(axis=0)
        raw_area = abs(north - south) * 111.0 * abs(east - west) * 111.0 * max(math.cos(math.radians((north + south) / 2)), 0.2)
        adjusted = bool(raw_area > 1100.0)
        if adjusted:
            center_lat, center_lon = np.median(clean, axis=0)
            half_side_km = math.sqrt(1100.0) / 2.0
            delta_lat = half_side_km / 111.0
            delta_lon = half_side_km / (111.0 * max(math.cos(math.radians(center_lat)), 0.2))
            north, south = center_lat + delta_lat, center_lat - delta_lat
            east, west = center_lon + delta_lon, center_lon - delta_lon
        else:
            north, south, east, west = north + 0.02, south - 0.02, east + 0.02, west - 0.02

        return {
            "dataset_id": did,
            "total_rows": total,
            "valid_rows": int(len(values)),
            "used_rows": int(len(clean)),
            "ignored_outliers": int(len(values) - len(clean)),
            "adjusted_to_area_limit": adjusted,
            "raw_area_km2": round(float(raw_area), 2),
            "bbox": {"north": float(north), "south": float(south), "east": float(east), "west": float(west)},
        }

    def get_nearest_mosques(self, dataset_id: str, lat: float, lon: float, radius_km: float, limit: int = 10) -> Dict[str, Any]:
        # "all" = lintas semua dataset, jangan di-slugify menjadi filter yang salah.
        raw_dataset_id = str(dataset_id or "all").strip() or "all"
        did = "all" if raw_dataset_id.lower() == "all" else slugify_dataset_name(raw_dataset_id)
        origin_lat = float(lat)
        origin_lon = float(lon)
        max_radius = min(200.0, max(0.5, float(radius_km)))
        safe_limit = min(50, max(1, int(limit)))

        while True:
            now = time.monotonic()
            with self._nearest_cache_lock:
                generation = self._nearest_generation.get(did, 0)
                cache_key = (
                    did,
                    generation,
                    round(origin_lat, 4),
                    round(origin_lon, 4),
                    round(max_radius, 1),
                    safe_limit,
                )
                cached = self._nearest_cache.get(cache_key)
                if cached and now - cached[0] <= 30.0:
                    self._nearest_cache.move_to_end(cache_key)
                    response = copy.deepcopy(cached[1])
                    response["origin"] = {"latitude": origin_lat, "longitude": origin_lon}
                    response["cache_hit"] = True
                    return response

                inflight = self._nearest_inflight.get(cache_key)
                if inflight is None:
                    inflight = threading.Event()
                    self._nearest_inflight[cache_key] = inflight
                    is_owner = True
                else:
                    is_owner = False

            if not is_owner:
                if not inflight.wait(timeout=NEAREST_SINGLEFLIGHT_WAIT_SECONDS):
                    raise TimeoutError(
                        "Pencarian masjid identik masih diproses terlalu lama oleh database."
                    )
                # The owner either populated the cache, failed, or was made
                # obsolete by a dataset mutation. Re-evaluate all three cases.
                continue

            generation_is_current = False
            try:
                # One indexed database query at the caller's maximum radius.
                items = self.mosque_repo.get_nearest_mosques(
                    did,
                    origin_lat,
                    origin_lon,
                    max_radius,
                    safe_limit,
                )
                response = {
                    "dataset_id": did,
                    "origin": {"latitude": origin_lat, "longitude": origin_lon},
                    "radius_km": max_radius,
                    "search_radius_used_km": max_radius,
                    "total": len(items),
                    "items": items,
                    "cache_hit": False,
                }
                with self._nearest_cache_lock:
                    generation_is_current = self._nearest_generation.get(did, 0) == generation
                    if generation_is_current:
                        self._nearest_cache[cache_key] = (time.monotonic(), copy.deepcopy(response))
                        while len(self._nearest_cache) > 512:
                            self._nearest_cache.popitem(last=False)
            finally:
                with self._nearest_cache_lock:
                    if self._nearest_inflight.get(cache_key) is inflight:
                        self._nearest_inflight.pop(cache_key, None)
                    inflight.set()

            if generation_is_current:
                return response
            # A write raced this query. Retry against the new generation rather
            # than returning a stale nearest result.

    def delete_mosque(self, dataset_id: str, mosque_id: str) -> bool:
        did = slugify_dataset_name(dataset_id)
        success = self.mosque_repo.delete_mosque(did, mosque_id)
        if success:
            self._record_dataset_mutation(did, mosque_count_delta=-1)
        return success

    def delete_dataset(self, dataset_id: str) -> bool:
        did = slugify_dataset_name(dataset_id)
        
        # 1. Delete all mosques in dataset
        self.mosque_repo.delete_all_mosques(did)
        self.invalidate_osm_graph(did)
        
        # 2. Delete raw CSV on disk if exists
        try:
            csv_path = DATA_DIR / "raw" / "datasets" / f"{did}.csv"
            if csv_path.exists():
                csv_path.unlink()
        except Exception:
            pass
            
        # 3. Delete dataset metadata
        success = self.dataset_repo.delete_dataset(did)
        
        # 4. Reset active dataset if deleted was active
        if self.get_active_dataset_id() == did:
            datasets = self.list_datasets()
            next_active = "banten"
            if datasets:
                # Find first non-deleted one
                available = [d['dataset_id'] for d in datasets if d['dataset_id'] != did]
                if available:
                    next_active = available[0]
            self.set_active_dataset_id(next_active)
            
        return success

    def delete_mosques_bulk(self, dataset_id: str, mosque_ids: list[str]) -> bool:
        if not dataset_id:
            return False
        if not mosque_ids:
            return True
        did = slugify_dataset_name(dataset_id)
        # Delete from repo
        success = self.mosque_repo.delete_mosques_bulk(did, mosque_ids)
        if success:
            self._record_dataset_mutation(did, mosque_count_delta=-len(mosque_ids))
        return success

    def _sync_mosque_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Sync provinsi / province
        if 'provinsi' in data and 'province' not in data:
            data['province'] = data['provinsi']
        elif 'province' in data and 'provinsi' not in data:
            data['provinsi'] = data['province']

        # Sync fasilitas / facilities
        if 'fasilitas' in data and 'facilities' not in data:
            fac = data['fasilitas']
            if isinstance(fac, str):
                data['facilities'] = [f.strip() for f in re.split(r"[|,;]+", fac) if f.strip()]
            else:
                data['facilities'] = fac
        elif 'facilities' in data and 'fasilitas' not in data:
            facs = data['facilities']
            if isinstance(facs, list):
                data['fasilitas'] = ", ".join(facs)
            else:
                data['fasilitas'] = facs

        return data

    def create_mosque(self, dataset_id: str, data: Dict[str, Any]) -> str:
        did = slugify_dataset_name(dataset_id)
        synced_data = self._sync_mosque_fields(dict(data))
        mosque_id = self.mosque_repo.create_mosque(did, synced_data)
        self._record_dataset_mutation(did, mosque_count_delta=1)
        return mosque_id

    def update_mosque(self, dataset_id: str, mosque_id: str, data: Dict[str, Any]) -> bool:
        did = slugify_dataset_name(dataset_id)
        synced_data = self._sync_mosque_fields(dict(data))
        success = self.mosque_repo.update_mosque(did, mosque_id, synced_data)
        if success:
            self._record_dataset_mutation(did)
        return success
