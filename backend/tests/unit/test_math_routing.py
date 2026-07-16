import pytest
import datetime as dt
from backend.app.infrastructure.services.osm_graph import (
    _validate_bbox_size,
    haversine_km,
    nearest_road_edge_candidates_batch,
    nearest_road_node,
    path_length_m,
    path_travel_time_s,
    route_nodes_to_latlon,
)
from backend.app.infrastructure.services.routing_osm import (
    _best_edge_snapped_route,
    _best_snapped_route,
    _multi_target_dijkstra_paths,
    _normalise_values,
    _prayer_arrival_details,
    _distance_point_to_segment_km,
    select_candidate_mosques,
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


def test_vectorized_candidate_selection_preserves_stable_ranking_and_fallback():
    mosques = [
        {"id": "first", "latitude": -6.20, "longitude": 106.81, "priority_score": 0.5},
        {"id": "second", "latitude": -6.20, "longitude": 106.81, "priority_score": 0.5},
        {"id": "far", "latitude": -6.30, "longitude": 106.90, "priority_score": 1.0},
    ]

    ranked = select_candidate_mosques(
        mosques,
        (-6.20, 106.80),
        (-6.20, 106.82),
        limit=2,
        corridor_km=2.0,
    )
    fallback = select_candidate_mosques(
        mosques,
        (-6.31, 106.90),
        (-6.31, 106.91),
        limit=1,
        corridor_km=0.1,
        fallback_radius_km=20.0,
    )

    assert [mosque["id"] for mosque in ranked] == ["first", "second"]
    assert fallback[0]["id"] == "far"

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


def test_nearest_mosques_uses_one_max_radius_query():
    from unittest.mock import Mock
    from backend.app.use_cases.dataset_usecases import DatasetUseCases

    mosque_repo = Mock()
    mosque_repo.get_nearest_mosques.return_value = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    use_case = DatasetUseCases(mosque_repo, Mock())
    result = use_case.get_nearest_mosques("all", -6.2, 106.8, 50, 3)

    assert result["search_radius_used_km"] == 50.0
    mosque_repo.get_nearest_mosques.assert_called_once_with("all", -6.2, 106.8, 50.0, 3)


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


def test_nearest_road_node_candidates_returns_ordered_alternatives():
    import networkx as nx
    from backend.app.infrastructure.services.osm_graph import nearest_road_node_candidates_batch

    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node("nearest", y=-6.2000, x=106.8000)
    graph.add_node("second", y=-6.2000, x=106.8002)
    graph.add_node("far", y=-6.2000, x=106.8100)

    candidates = nearest_road_node_candidates_batch(graph, [(-6.2000, 106.80001)], k=2)

    assert candidates == [["nearest", "second"]]


def test_edge_projection_finds_road_geometry_between_distant_osm_nodes():
    import networkx as nx
    from shapely.geometry import LineString

    graph = nx.DiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node("west", y=-6.20, x=106.80)
    graph.add_node("east", y=-6.20, x=106.82)
    graph.add_node("decoy", y=-6.2002, x=106.8102)
    graph.add_node("decoy-end", y=-6.21, x=106.811)
    main_geometry = LineString([(106.80, -6.20), (106.82, -6.20)])
    graph.add_edge("west", "east", length=2200.0, travel_time=220.0, geometry=main_geometry)
    graph.add_edge("east", "west", length=2200.0, travel_time=220.0, geometry=main_geometry)
    graph.add_edge("decoy", "decoy-end", length=1100.0, travel_time=110.0)

    point = (-6.20005, 106.81)

    assert nearest_road_node(graph, *point) == "decoy"
    snap = nearest_road_edge_candidates_batch(graph, [point], k=1)[0][0]
    assert {snap.u, snap.v} == {"west", "east"}
    assert snap.connector_m < 10.0


def test_edge_snapped_route_charges_only_the_traversed_edge_fraction():
    import networkx as nx
    from shapely.geometry import LineString

    graph = nx.DiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node("west", y=-6.20, x=106.80)
    graph.add_node("east", y=-6.20, x=106.82)
    geometry = LineString([(106.80, -6.20), (106.82, -6.20)])
    graph.add_edge("west", "east", length=2200.0, travel_time=220.0, geometry=geometry)
    graph.add_edge("east", "west", length=2200.0, travel_time=220.0, geometry=geometry)

    result = _best_edge_snapped_route(
        graph,
        (-6.20001, 106.805),
        (-6.20001, 106.815),
        algorithm="dijkstra",
        candidate_count=1,
    )

    assert result["road_length_m"] == pytest.approx(1100.0, abs=1.0)
    assert result["connector_m"] < 3.0
    assert result["road_coordinates"][0][1] == pytest.approx(106.805, abs=1e-6)
    assert result["road_coordinates"][-1][1] == pytest.approx(106.815, abs=1e-6)


def test_edge_snapped_route_does_not_travel_backwards_on_one_way_edge():
    import networkx as nx
    from shapely.geometry import LineString

    graph = nx.DiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node("west", y=-6.20, x=106.80)
    graph.add_node("east", y=-6.20, x=106.82)
    graph.add_edge(
        "west",
        "east",
        length=2200.0,
        travel_time=220.0,
        geometry=LineString([(106.80, -6.20), (106.82, -6.20)]),
    )

    with pytest.raises(nx.NetworkXNoPath):
        _best_edge_snapped_route(
            graph,
            (-6.20, 106.816),
            (-6.20, 106.804),
            algorithm="dijkstra",
            candidate_count=1,
        )


def test_best_snapped_route_avoids_wrong_side_of_divided_road(monkeypatch):
    import networkx as nx
    from backend.app.infrastructure.services import routing_osm

    graph = nx.DiGraph()
    graph.add_node("wrong-side", y=-6.20000, x=106.80000)
    graph.add_node("right-side", y=-6.20002, x=106.80000)
    graph.add_node("detour", y=-6.21000, x=106.80500)
    graph.add_node("mosque-road", y=-6.20000, x=106.81000)
    graph.add_edge("wrong-side", "detour", travel_time=100.0, length=900.0)
    graph.add_edge("detour", "mosque-road", travel_time=100.0, length=900.0)
    graph.add_edge("right-side", "mosque-road", travel_time=10.0, length=1100.0)
    monkeypatch.setattr(
        routing_osm,
        "nearest_road_node_candidates_batch",
        lambda _graph, _points, k: [["wrong-side", "right-side"], ["mosque-road"]],
    )

    start_node, mosque_node, route_nodes, _ = _best_snapped_route(
        graph,
        (-6.20000, 106.80000),
        (-6.20000, 106.81000),
        algorithm="dijkstra",
    )

    assert start_node == "right-side"
    assert mosque_node == "mosque-road"
    assert route_nodes == ["right-side", "mosque-road"]


def test_route_geometry_is_oriented_in_path_direction():
    import networkx as nx
    from shapely.geometry import LineString

    graph = nx.DiGraph()
    graph.add_node("west", y=-6.20, x=106.80)
    graph.add_node("east", y=-6.20, x=106.81)
    # Some GraphML/OSM edges retain geometry in the opposite direction.
    graph.add_edge(
        "west",
        "east",
        length=1100.0,
        geometry=LineString([(106.81, -6.20), (106.805, -6.201), (106.80, -6.20)]),
    )

    assert route_nodes_to_latlon(graph, ["west", "east"]) == [
        (-6.20, 106.80),
        (-6.201, 106.805),
        (-6.20, 106.81),
    ]
