import threading
import time
from unittest.mock import Mock

import networkx as nx
import pytest

from app.infrastructure.services import osm_graph
from app.infrastructure.services import routing_osm
from app.use_cases import routing_usecases as routing_usecases_module


def _small_graph():
    graph = nx.DiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node(0, y=-6.20, x=106.80)
    graph.add_node(1, y=-6.20, x=106.81)
    graph.add_node(2, y=-6.20, x=106.82)
    graph.add_node(3, y=-6.20, x=106.83)
    graph.add_edge(0, 1, length=1000.0, travel_time=100.0)
    graph.add_edge(1, 3, length=2000.0, travel_time=200.0)
    graph.add_edge(0, 2, length=2000.0, travel_time=200.0)
    graph.add_edge(2, 3, length=2000.0, travel_time=200.0)
    return graph


def test_build_route_passes_named_arguments(monkeypatch, tmp_path):
    graph_path = tmp_path / "dataset.graphml"
    build = Mock(return_value=_small_graph())
    dataset_repo = Mock()
    use_cases = routing_usecases_module.RoutingUseCases(Mock(), dataset_repo)
    monkeypatch.setattr(routing_usecases_module, "get_graphml_path", lambda _dataset_id: graph_path)
    monkeypatch.setattr(routing_usecases_module, "build_osm_graph_for_route", build)

    use_cases.build_osm_route(-6.2, 106.8, -6.3, 106.9, 7.5, "walk", "jakarta")

    assert build.call_args.args == ()
    assert build.call_args.kwargs == {
        "start_lat": -6.2,
        "start_lon": 106.8,
        "end_lat": -6.3,
        "end_lon": 106.9,
        "buffer_km": 7.5,
        "network_type": "walk",
        "output_graphml": graph_path,
    }


def test_runtime_binary_cache_uses_graphml_fingerprint(tmp_path):
    graph_path = tmp_path / "dataset.graphml"
    graph_path.write_bytes(b"graphml-v1")
    graph = _small_graph()

    cache_path = osm_graph._write_runtime_graph_cache(graph_path, graph)
    loaded = osm_graph._load_runtime_graph_cache(graph_path)

    assert cache_path.exists()
    assert list(loaded.edges) == list(graph.edges)

    graph_path.write_bytes(b"graphml-version-two")
    assert osm_graph._load_runtime_graph_cache(graph_path) is None


def test_persistent_edge_index_uses_graphml_fingerprint(tmp_path):
    graph_path = tmp_path / "dataset.graphml"
    graph_path.write_bytes(b"graphml-v1")
    graph = _small_graph()
    index = osm_graph._nearest_edge_index(graph)

    cache_path = osm_graph._write_edge_index_cache(graph_path, index)
    loaded = osm_graph._load_edge_index_cache(graph_path)

    assert cache_path == osm_graph.edge_index_cache_path(graph_path)
    assert cache_path.exists()
    assert loaded is not None
    assert loaded[2] == index[2]

    graph_path.write_bytes(b"graphml-version-two")
    assert osm_graph._load_edge_index_cache(graph_path) is None


def test_edge_snap_candidates_reuse_per_graph_coordinate_cache():
    graph = _small_graph()
    point = (-6.20, 106.805)

    first = osm_graph.nearest_road_edge_candidates_batch(graph, [point], k=2)
    assert len(graph._imosque_edge_snap_cache) == 1

    class FailingTree:
        def query(self, *_args, **_kwargs):  # pragma: no cover - cache must bypass this
            raise AssertionError("STRtree should not be queried for a cached coordinate")

        def query_nearest(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("STRtree should not be queried for a cached coordinate")

    _, geometries, records = graph._imosque_nearest_edge_index
    graph._imosque_nearest_edge_index = (FailingTree(), geometries, records)
    second = osm_graph.nearest_road_edge_candidates_batch(graph, [point], k=2)

    assert second == first
    assert second is not first


def test_mosque_candidate_snapshot_is_revision_bound():
    mosque_repo = Mock()
    mosque_repo.count_mosques.return_value = 2
    mosque_repo.get_mosques.return_value = [{"id": "m1"}, {"id": "m2"}]
    dataset_repo = Mock()
    dataset_repo.get_dataset.return_value = {"data_revision": 7}
    use_cases = routing_usecases_module.RoutingUseCases(mosque_repo, dataset_repo)

    assert use_cases.start_mosque_candidate_prewarm("jakarta", 7) is True
    threads = list(use_cases._mosque_candidate_prewarm_threads.values())
    for thread in threads:
        thread.join(timeout=2)

    assert use_cases._get_cached_mosque_candidates("jakarta", 7) == [
        {"id": "m1"},
        {"id": "m2"},
    ]
    assert use_cases._get_cached_mosque_candidates("jakarta", 8) is None
    mosque_repo.get_mosques.assert_called_once_with("jakarta", limit=2, offset=0)


def test_batch_routing_materializes_only_the_winning_option(monkeypatch):
    graph = _small_graph()
    start = (-6.20, 106.80)
    destination = (-6.20, 106.83)
    snaps = [
        [osm_graph.RoadEdgeSnap(0, 1, 0.0, start, 0.0)],
        [
            osm_graph.RoadEdgeSnap(1, 3, 1.0, destination, 0.0),
            osm_graph.RoadEdgeSnap(2, 3, 1.0, destination, 0.0),
        ],
    ]
    materialize = Mock(wraps=routing_osm._edge_route_result)
    monkeypatch.setattr(routing_osm, "_edge_route_result", materialize)

    routes = routing_osm._batch_edge_routes_from_start(
        graph,
        start,
        [destination],
        snap_groups=snaps,
    )

    assert routes[0] is not None
    assert materialize.call_count == 1


def test_corrupt_runtime_cache_falls_back_to_graphml(monkeypatch, tmp_path):
    graph_path = tmp_path / "dataset.graphml"
    graph_path.write_bytes(b"valid-graphml-source")
    osm_graph.runtime_graph_cache_path(graph_path).write_bytes(b"not-a-pickle")
    graph = _small_graph()
    fake_osmnx = Mock()
    fake_osmnx.load_graphml.return_value = graph
    monkeypatch.setattr(osm_graph, "_require_osmnx", lambda: fake_osmnx)
    osm_graph.evict_road_graph(graph_path)

    loaded = osm_graph.load_road_graph(graph_path)

    assert loaded is graph
    fake_osmnx.load_graphml.assert_called_once_with(filepath=graph_path)
    assert osm_graph._load_runtime_graph_cache(graph_path) is not None
    assert osm_graph.get_road_graph_status(graph_path)["source"] == "graphml"


def test_graph_status_does_not_wait_for_slow_graphml_parse(monkeypatch, tmp_path):
    graph_path = tmp_path / "slow.graphml"
    graph_path.write_bytes(b"slow-graphml")
    entered_loader = threading.Event()
    release_loader = threading.Event()
    errors = []

    class SlowOsmnx:
        @staticmethod
        def load_graphml(filepath):
            entered_loader.set()
            if not release_loader.wait(timeout=2):
                raise TimeoutError("test loader was not released")
            return _small_graph()

    monkeypatch.setattr(osm_graph, "_require_osmnx", lambda: SlowOsmnx)
    osm_graph.evict_road_graph(graph_path)

    def load():
        try:
            osm_graph.load_road_graph(graph_path)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=load)
    thread.start()
    assert entered_loader.wait(timeout=1)

    started = time.perf_counter()
    status = osm_graph.get_road_graph_status(graph_path)
    status_elapsed = time.perf_counter() - started
    release_loader.set()
    thread.join(timeout=2)

    assert status_elapsed < 0.1
    assert status["status"] == "loading"
    assert status["ready"] is False
    assert not errors
    assert not thread.is_alive()


def test_startup_graph_prewarm_is_enabled_by_default(monkeypatch):
    from app import main as main_module

    init_db = Mock()
    preload = Mock()
    monkeypatch.setattr(main_module, "init_db", init_db)
    monkeypatch.setattr(main_module, "_preload_active_road_graph", preload)
    monkeypatch.delenv("IMOSQUE_PREWARM_GRAPH_ON_STARTUP", raising=False)

    main_module.startup_event()

    init_db.assert_called_once_with()
    preload.assert_called_once_with()

    preload.reset_mock()
    monkeypatch.setenv("IMOSQUE_PREWARM_GRAPH_ON_STARTUP", "false")
    main_module.startup_event()
    preload.assert_not_called()


def test_route_fails_fast_to_osrm_while_graph_is_loading(monkeypatch, tmp_path):
    graph_path = tmp_path / "loading.graphml"
    graph_path.write_bytes(b"graphml")
    mosque = {
        "id": "m1",
        "name": "Masjid Satu",
        "latitude": -6.205,
        "longitude": 106.805,
        "priority_score": 0.8,
    }
    fallback = Mock(return_value={"routing_mode": "osrm_fallback"})
    monkeypatch.setattr(
        routing_osm,
        "get_road_graph_status",
        lambda _path: {"status": "loading", "ready": False},
    )
    monkeypatch.setattr(routing_osm, "_route_via_osrm_fallback", fallback)
    monkeypatch.setattr(
        routing_osm,
        "load_road_graph",
        lambda _path: pytest.fail("loading graph must not block an interactive request"),
    )

    result = routing_osm.route_via_osm_dijkstra(
        -6.20,
        106.80,
        -6.21,
        106.81,
        graphml_path=graph_path,
        dataset_id="jakarta",
        fetch_mosques_fn=lambda _dataset_id, bounds=None: [mosque],
    )

    assert result["routing_mode"] == "osrm_fallback"
    assert result["graph_runtime"]["status"] == "loading"
    assert "total" in result["timings_ms"]
    fallback.assert_called_once()


def test_route_to_mosque_fails_fast_while_graph_is_loading(monkeypatch, tmp_path):
    graph_path = tmp_path / "loading-selected.graphml"
    graph_path.write_bytes(b"graphml")
    mosque = {
        "id": "m1",
        "name": "Masjid Satu",
        "latitude": -6.205,
        "longitude": 106.805,
        "priority_score": 0.8,
    }
    fallback = Mock(return_value={"routing_mode": "osrm_fallback"})
    monkeypatch.setattr(
        routing_osm,
        "get_road_graph_status",
        lambda _path: {"status": "loading", "ready": False},
    )
    monkeypatch.setattr(routing_osm, "_route_via_osrm_fallback", fallback)
    monkeypatch.setattr(
        routing_osm,
        "load_road_graph",
        lambda _path: pytest.fail("loading graph must not block a selected-mosque request"),
    )

    result = routing_osm._route_to_mosque_uncached(
        start_lat=-6.20,
        start_lon=106.80,
        mosque=mosque,
        graphml_path=graph_path,
        dataset_id="jakarta",
    )

    assert result["routing_mode"] == "osrm_fallback"
    assert result["graph_runtime"]["status"] == "loading"
    fallback.assert_called_once()


@pytest.mark.parametrize(
    ("algorithm", "expected_final_algorithm", "expects_exact_final_route"),
    [
        ("astar", "astar_edge_projection", True),
        ("dijkstra", "dijkstra_edge_batch_reuse", False),
    ],
)
def test_exact_final_route_runs_only_when_requested(
    monkeypatch,
    tmp_path,
    algorithm,
    expected_final_algorithm,
    expects_exact_final_route,
):
    graph_path = tmp_path / "ready.graphml"
    graph_path.write_bytes(b"graphml")
    graph = _small_graph()
    mosques = [
        {"id": "m1", "name": "Masjid Satu", "latitude": -6.20, "longitude": 106.81, "priority_score": 1.0},
        {"id": "m2", "name": "Masjid Dua", "latitude": -6.20, "longitude": 106.82, "priority_score": 0.0},
    ]
    final_route_calls = []

    def fake_route(start, destination, distance_m):
        return {
            "distance_m": distance_m,
            "road_length_m": distance_m,
            "connector_m": 0.0,
            "time_s": distance_m / 10.0,
            "road_time_s": distance_m / 10.0,
            "road_coordinates": [start, destination],
            "access_connectors": [],
            "route_nodes": [0, 1],
        }

    def batch_from_start(_graph, start, destinations, candidate_count, snap_groups=None):
        assert candidate_count == routing_osm.EDGE_SNAP_BATCH_CANDIDATE_COUNT
        assert snap_groups is not None
        return [
            fake_route(start, destinations[0], 100.0),
            fake_route(start, destinations[1], 300.0),
        ]

    def batch_to_destination(_graph, starts, destination, candidate_count, snap_groups=None):
        assert candidate_count == routing_osm.EDGE_SNAP_BATCH_CANDIDATE_COUNT
        assert snap_groups is not None
        return [
            fake_route(starts[0], destination, 100.0),
            fake_route(starts[1], destination, 300.0),
        ]

    def final_route(_graph, start, destination, algorithm, candidate_count):
        final_route_calls.append((start, destination, algorithm, candidate_count))
        return fake_route(start, destination, 100.0)

    monkeypatch.setattr(routing_osm, "load_road_graph", lambda _path: graph)
    monkeypatch.setattr(
        routing_osm,
        "get_road_graph_status",
        lambda _path: {"status": "ready", "ready": True},
    )
    monkeypatch.setattr(routing_osm, "_batch_edge_routes_from_start", batch_from_start)
    monkeypatch.setattr(routing_osm, "_batch_edge_routes_to_destination", batch_to_destination)
    monkeypatch.setattr(routing_osm, "_best_edge_snapped_route", final_route)

    result = routing_osm.route_via_osm_dijkstra(
        -6.20,
        106.80,
        -6.20,
        106.83,
        algorithm=algorithm,
        max_candidates=2,
        graphml_path=graph_path,
        dataset_id="jakarta",
        fetch_mosques_fn=lambda _dataset_id, bounds=None: mosques,
    )

    assert result["recommended_mosque"]["id"] == "m1"
    assert result["pathfinding"]["candidate_ranking_algorithm"] == "dijkstra_edge_projection_batch"
    assert result["pathfinding"]["final_path_algorithm"] == expected_final_algorithm
    expected_calls = (
        [
            ((-6.20, 106.80), (-6.20, 106.81), "astar", routing_osm.EDGE_SNAP_CANDIDATE_COUNT),
            ((-6.20, 106.81), (-6.20, 106.83), "astar", routing_osm.EDGE_SNAP_CANDIDATE_COUNT),
        ]
        if expects_exact_final_route
        else []
    )
    assert final_route_calls == expected_calls


def test_routing_mosque_query_uses_nearest_only_when_corridor_is_empty(monkeypatch, tmp_path):
    graph_path = tmp_path / "dataset.graphml"
    mosque_repo = Mock()
    mosque_repo.get_mosques_in_bounds.return_value = []
    fallback_rows = [{"id": "fallback"}]
    mosque_repo.get_nearest_mosques.return_value = fallback_rows
    dataset_repo = Mock()
    dataset_repo.get_dataset.return_value = {"data_revision": 1}
    use_cases = routing_usecases_module.RoutingUseCases(mosque_repo, dataset_repo)
    monkeypatch.setattr(routing_usecases_module, "get_graphml_path", lambda _dataset_id: graph_path)

    def route_stub(**kwargs):
        rows = kwargs["fetch_mosques_fn"](
            "jakarta",
            bounds=(-6.3, -6.1, 106.7, 106.9),
        )
        return {"rows": rows}

    monkeypatch.setattr(routing_usecases_module, "route_via_osm_dijkstra", route_stub)

    result = use_cases.route_via_osm_dijkstra(
        start_lat=-6.2,
        start_lon=106.8,
        end_lat=-6.21,
        end_lon=106.81,
        algorithm="dijkstra",
        current_time=None,
        prayer_time=None,
        max_candidates=6,
        auto_build_osm=False,
        buffer_km=8.0,
        dataset_id="jakarta",
    )

    assert result["rows"] == fallback_rows
    mosque_repo.get_mosques_in_bounds.assert_called_once()
    mosque_repo.get_nearest_mosques.assert_called_once_with(
        "jakarta", -6.2, 106.8, 32.0, 144
    )


def test_bidirectional_no_path_is_not_recomputed(monkeypatch):
    graph = nx.DiGraph()
    graph.add_nodes_from([1, 2])
    standard_dijkstra = Mock()
    monkeypatch.setattr(
        osm_graph.nx,
        "bidirectional_dijkstra",
        Mock(side_effect=nx.NetworkXNoPath("disconnected")),
    )
    monkeypatch.setattr(osm_graph.nx, "dijkstra_path", standard_dijkstra)

    with pytest.raises(nx.NetworkXNoPath):
        osm_graph.dijkstra_path(graph, 1, 2)

    standard_dijkstra.assert_not_called()


def test_recommendation_cache_key_tracks_dataset_revision(monkeypatch, tmp_path):
    graph_path = tmp_path / "dataset.graphml"
    dataset_repo = Mock()
    dataset_repo.get_dataset.side_effect = [
        {"data_revision": 1},
        {"data_revision": 2},
    ]
    use_cases = routing_usecases_module.RoutingUseCases(Mock(), dataset_repo)
    route = Mock(return_value={"candidate_count": 1})
    monkeypatch.setattr(routing_usecases_module, "get_graphml_path", lambda _dataset_id: graph_path)
    monkeypatch.setattr(routing_usecases_module, "route_via_osm_dijkstra", route)

    kwargs = dict(
        start_lat=-6.2,
        start_lon=106.8,
        end_lat=-6.3,
        end_lon=106.9,
        algorithm="dijkstra",
        current_time=None,
        prayer_time=None,
        max_candidates=3,
        auto_build_osm=False,
        buffer_km=5.0,
        dataset_id="jakarta",
    )
    use_cases.route_via_osm_dijkstra(**kwargs)
    use_cases.route_via_osm_dijkstra(**kwargs)

    assert route.call_count == 2


def test_compact_polyline_uses_simplified_coordinates():
    mosque = {"id": "m1", "name": "Masjid", "latitude": -6.2, "longitude": 106.8}
    coordinates = [(-6.2, 106.8), (-6.2, 106.805), (-6.2, 106.81)]
    result = {
        "mosque": mosque,
        "distance_km": 1.0,
        "estimated_time_minutes": 2.0,
        "arrival_to_mosque_minutes": 2.0,
        "priority_score": 0.5,
        "multi_objective_score": 0.0,
        "route_nodes_count": 3,
        "route_coordinates": coordinates,
    }

    response = routing_osm._format_route_response(
        algorithm_label="Dijkstra",
        road_network="test",
        routing_weight="travel_time_seconds",
        dataset_id="test",
        start_lat=-6.2,
        start_lon=106.8,
        end_lat=-6.2,
        end_lon=106.81,
        requested_candidates=1,
        results=[result],
        elapsed_ms=1.0,
        reason="test",
    )

    assert response["route_summary"]["geometry_points_count"] == 2
    assert response["encoded_polyline"] == routing_osm._encode_polyline([coordinates[0], coordinates[-1]])
