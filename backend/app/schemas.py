from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RouteRequest(BaseModel):
    start_lat: float = Field(..., description="Latitude titik awal user")
    start_lon: float = Field(..., description="Longitude titik awal user")
    end_lat: float = Field(..., description="Latitude titik tujuan user")
    end_lon: float = Field(..., description="Longitude titik tujuan user")
    algorithm: Literal["dijkstra", "astar"] = "dijkstra"
    dataset_id: Optional[str] = Field(None, description="Dataset aktif, contoh: banten, dki_jakarta, jawa_barat")
    current_time: Optional[str] = Field(None, description="Jam sekarang format HH:MM, contoh 17:35")
    prayer_time: Optional[str] = Field(None, description="Jam adzan/shalat format HH:MM, contoh 18:05")
    max_candidates: int = Field(6, ge=1, le=20)
    auto_build_osm: bool = Field(False, description="Jika true, backend akan rebuild/download OSM graph untuk area start-end saat routing")
    buffer_km: float = Field(6.0, ge=1.0, le=25.0)


class NearestMosquesRequest(BaseModel):
    latitude: float
    longitude: float
    dataset_id: Optional[str] = Field(None, description="Dataset aktif, contoh: banten, dki_jakarta, jawa_barat")
    limit: int = Field(6, ge=1, le=20)
    radius_km: float = Field(10.0, ge=0.5, le=50.0)


class RouteToMosqueRequest(BaseModel):
    start_lat: float
    start_lon: float
    mosque_id: str
    dataset_id: Optional[str] = Field(None, description="Dataset aktif, contoh: banten, dki_jakarta, jawa_barat")
    algorithm: Literal["dijkstra", "astar"] = "dijkstra"
    auto_build_osm: bool = Field(False, description="Jika true, backend boleh mencoba build graph OSM")
    buffer_km: float = Field(6.0, ge=1.0, le=25.0)


class BuildOsmRequest(BaseModel):
    north: float
    south: float
    east: float
    west: float
    network_type: Literal["drive", "walk", "bike", "all"] = "drive"


class BuildOsmRouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    buffer_km: float = Field(6.0, ge=1.0, le=25.0)
    network_type: Literal["drive", "walk", "bike", "all"] = "drive"
