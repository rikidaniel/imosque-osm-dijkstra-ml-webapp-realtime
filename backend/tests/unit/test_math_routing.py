import pytest
import datetime as dt
from backend.app.infrastructure.services.osm_graph import (
    _validate_bbox_size,
    haversine_km,
    path_length_m,
    path_travel_time_s,
    route_nodes_to_latlon,
)
from backend.app.infrastructure.services.routing_osm import (
    _multi_target_dijkstra_paths,
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


def test_bbox_validation_rejects_reversed_bounds():
    with pytest.raises(ValueError, match="South"):
        _validate_bbox_size(-6.4, -6.0, 106.9, 106.7)
    with pytest.raises(ValueError, match="West"):
        _validate_bbox_size(-6.0, -6.4, 106.7, 106.9)


def test_edge_helpers_support_digraph_and_multidigraph():
    import networkx as nx

    for graph in (nx.DiGraph(), nx.MultiDiGraph()):
        graph.add_node(1, y=-6.2, x=106.8)
        graph.add_node(2, y=-6.21, x=106.81)
        graph.add_edge(1, 2, length=125.0, travel_time=15.0)
        assert path_length_m(graph, [1, 2]) == 125.0
        assert path_travel_time_s(graph, [1, 2]) == 15.0
        assert route_nodes_to_latlon(graph, [1, 2]) == [(-6.2, 106.8), (-6.21, 106.81)]


def test_nearest_mosques_uses_adaptive_radius():
    from unittest.mock import Mock
    from backend.app.use_cases.dataset_usecases import DatasetUseCases

    mosque_repo = Mock()
    mosque_repo.get_nearest_mosques.side_effect = [[], [{"id": "a"}, {"id": "b"}, {"id": "c"}]]
    use_case = DatasetUseCases(mosque_repo, Mock())
    result = use_case.get_nearest_mosques("all", -6.2, 106.8, 50, 3)

    assert result["search_radius_used_km"] == 15.0
    assert mosque_repo.get_nearest_mosques.call_args_list[0].args[3] == 5.0
    assert mosque_repo.get_nearest_mosques.call_args_list[1].args[3] == 15.0


def test_inline_osm_build_is_opt_in(monkeypatch):
    from backend.app.use_cases.routing_usecases import RoutingUseCases

    monkeypatch.delenv("IMOSQUE_ALLOW_INLINE_OSM_BUILD", raising=False)
    assert RoutingUseCases._inline_build_enabled(True) is False
    monkeypatch.setenv("IMOSQUE_ALLOW_INLINE_OSM_BUILD", "true")
    assert RoutingUseCases._inline_build_enabled(True) is True


def test_multi_target_dijkstra_returns_only_reachable_targets():
    import networkx as nx

    graph = nx.MultiDiGraph()
    graph.add_edge("start", "a", travel_time=2.0)
    graph.add_edge("a", "mosque-1", travel_time=3.0)
    graph.add_edge("start", "mosque-2", travel_time=8.0)
    graph.add_node("unreachable")

    distances, paths = _multi_target_dijkstra_paths(
        graph, "start", ["mosque-1", "mosque-2", "unreachable"]
    )

    assert distances == {"mosque-1": 5.0, "mosque-2": 8.0}
    assert paths["mosque-1"] == ["start", "a", "mosque-1"]
    assert paths["mosque-2"] == ["start", "mosque-2"]
    assert "unreachable" not in paths


def test_nearest_road_nodes_reuses_spatial_index():
    import networkx as nx
    from backend.app.infrastructure.services.osm_graph import nearest_road_nodes_batch

    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node("west", y=-6.20, x=106.80)
    graph.add_node("east", y=-6.20, x=106.90)

    assert nearest_road_nodes_batch(graph, [(-6.201, 106.801), (-6.199, 106.899)]) == ["west", "east"]
    first_index = graph._imosque_nearest_index
    assert nearest_road_nodes_batch(graph, [(-6.20, 106.80)]) == ["west"]
    assert graph._imosque_nearest_index is first_index
