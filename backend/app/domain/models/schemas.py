from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RouteRequest(BaseModel):
    start_lat: float = Field(..., description="Latitude titik awal user")
    start_lon: float = Field(..., description="Longitude titik awal user")
    end_lat: float = Field(..., description="Latitude titik tujuan user")
    end_lon: float = Field(..., description="Longitude titik tujuan user")
    algorithm: Literal["dijkstra", "astar"] = "astar"
    dataset_id: Optional[str] = Field(None, description="Dataset aktif, contoh: banten, dki_jakarta, jawa_barat")
    current_time: Optional[str] = Field(None, description="Jam sekarang format HH:MM, contoh 17:35")
    prayer_time: Optional[str] = Field(None, description="Jam adzan/shalat format HH:MM, contoh 18:05")
    max_candidates: int = Field(6, ge=1, le=20)
    auto_build_osm: bool = Field(False, description="Jika true, backend akan rebuild/download OSM graph untuk area start-end saat routing")
    buffer_km: float = Field(6.0, ge=1.0, le=200.0)


class NearestMosquesRequest(BaseModel):
    latitude: float
    longitude: float
    dataset_id: Optional[str] = Field(None, description="Dataset aktif, contoh: banten, dki_jakarta, jawa_barat")
    limit: int = Field(6, ge=1, le=5000)
    radius_km: float = Field(10.0, ge=0.5, le=200.0)


class RouteToMosqueRequest(BaseModel):
    start_lat: float
    start_lon: float
    mosque_id: str
    dataset_id: Optional[str] = Field(None, description="Dataset aktif, contoh: banten, dki_jakarta, jawa_barat")
    algorithm: Literal["dijkstra", "astar"] = "astar"
    auto_build_osm: bool = Field(False, description="Jika true, backend boleh mencoba build graph OSM")
    buffer_km: float = Field(6.0, ge=1.0, le=200.0)


class BuildOsmRequest(BaseModel):
    north: float
    south: float
    east: float
    west: float
    network_type: Literal["drive", "walk", "bike", "all"] = "drive"
    dataset_id: Optional[str] = Field(None, description="Dataset ID aktif")


class BuildOsmRouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    buffer_km: float = Field(6.0, ge=1.0, le=25.0)
    network_type: Literal["drive", "walk", "bike", "all"] = "drive"
    dataset_id: Optional[str] = Field(None, description="Dataset ID aktif")


class BulkDeleteRequest(BaseModel):
    dataset_id: str
    mosque_ids: list[str] = Field(..., description="Daftar ID masjid yang akan dihapus secara masal")

class MosqueCreateRequest(BaseModel):
    name: str = Field(..., description="Nama masjid")
    latitude: float
    longitude: float
    kecamatan: Optional[str] = None
    kabko: Optional[str] = None
    provinsi: Optional[str] = None
    kelurahan: Optional[str] = None
    address: Optional[str] = None
    fasilitas: Optional[str] = None

class MosqueUpdateRequest(BaseModel):
    name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    kecamatan: Optional[str] = None
    kabko: Optional[str] = None
    provinsi: Optional[str] = None
    kelurahan: Optional[str] = None
    address: Optional[str] = None
    fasilitas: Optional[str] = None


class CoordinateModel(BaseModel):
    latitude: float
    longitude: float


class RecommendRouteRequest(BaseModel):
    origin: CoordinateModel = Field(..., description="Titik asal user")
    destination: Optional[CoordinateModel] = Field(None, description="Titik tujuan akhir user (opsional)")
    departure_time: str = Field(..., description="ISO-8601 string atau waktu keberangkatan, contoh: 2026-07-11T17:10:00+07:00")
    prayer: str = Field(..., description="Nama salat target, contoh: maghrib, isha, asr")
    algorithm: Literal["dijkstra", "astar"] = "astar"
    profile: Literal["fastest", "prayer_priority", "low_cost", "balanced"] = "balanced"
    search_radius_km: float = Field(10.0, ge=1.0, le=200.0)
    maximum_results: int = Field(3, ge=1, le=10)
    auto_build_osm: bool = Field(True, description="Otomatis build/download map dari Overpass jika rute di luar batas")
    dataset_id: Optional[str] = None


class BenchmarkRequest(BaseModel):
    origin: CoordinateModel = Field(..., description="Titik asal user")
    destination: CoordinateModel = Field(..., description="Titik tujuan user")
    departure_time: str = Field(..., description="ISO-8601 string atau waktu keberangkatan, contoh: 2026-07-11T17:10:00+07:00")
    prayer: str = Field(..., description="Nama salat target, contoh: maghrib, isha, asr")
    profile: Literal["fastest", "prayer_priority", "low_cost", "balanced"] = "balanced"
    search_radius_km: float = Field(10.0, ge=1.0, le=200.0)
    dataset_id: Optional[str] = None

