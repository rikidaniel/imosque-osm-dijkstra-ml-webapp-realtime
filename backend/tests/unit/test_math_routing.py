import pytest
import datetime as dt
from backend.app.infrastructure.services.osm_graph import haversine_km
from backend.app.infrastructure.services.routing_osm import (
    _normalise_values,
    _prayer_arrival_details,
    _distance_point_to_segment_km,
)
from backend.app.infrastructure.services.prayer_time import calculate_offline_prayer_times

def test_haversine_km():
    # Distance between Jakarta (-6.2088, 106.8456) and Bandung (-6.9175, 107.6191) is approx 118-120 km
    dist = haversine_km(-6.2088, 106.8456, -6.9175, 107.6191)
    assert 115.0 < dist < 125.0
    
    # Distance to itself should be 0
    assert haversine_km(-6.2088, 106.8456, -6.2088, 106.8456) == 0.0

def test_normalise_values():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    normed = _normalise_values(vals)
    assert normed == [0.0, 0.25, 0.5, 0.75, 1.0]
    
    # Single or flat values
    assert _normalise_values([10.0, 10.0]) == [0.0, 0.0]
    assert _normalise_values([]) == []

def test_distance_point_to_segment_km():
    # Point on line segment
    p = (-6.2, 106.85)
    a = (-6.2, 106.8)
    b = (-6.2, 106.9)
    dist = _distance_point_to_segment_km(p, a, b)
    assert dist < 0.01 # extremely close to 0

def test_prayer_arrival_details():
    # Arrival 15 minutes before prayer (optimal: penalty 0.0)
    penalty, status, minutes = _prayer_arrival_details(15.0, "17:35", "18:05")
    assert penalty == 0.0
    assert status == "before_prayer"
    assert minutes == 15.0
    
    # Arrival after prayer (late: penalty > 0.6)
    penalty_late, status_late, minutes_late = _prayer_arrival_details(40.0, "17:35", "18:05")
    assert penalty_late >= 0.6
    assert status_late == "after_prayer"
    assert minutes_late == -10.0

def test_calculate_offline_prayer_times():
    # Jakarta coordinates on 11 July 2026
    lat = -6.2088
    lon = 106.8456
    date_obj = dt.date(2026, 7, 11)
    times = calculate_offline_prayer_times(lat, lon, date_obj)
    
    # Check that all prayers are returned
    assert "fajr" in times
    assert "dhuhr" in times
    assert "asr" in times
    assert "maghrib" in times
    assert "isha" in times
    
    # Check structure HH:MM
    for k, v in times.items():
        assert len(v) == 5
        assert ":" in v

def test_encode_polyline():
    from backend.app.infrastructure.services.routing_osm import _encode_polyline
    points = [(-6.2, 106.8), (-6.21, 106.81)]
    encoded = _encode_polyline(points)
    assert isinstance(encoded, str)
    assert len(encoded) > 0
    # Coba encode koordinat Jakarta
    encoded_single = _encode_polyline([(-6.2088, 106.8456)])
    assert isinstance(encoded_single, str)
    assert len(encoded_single) > 0
