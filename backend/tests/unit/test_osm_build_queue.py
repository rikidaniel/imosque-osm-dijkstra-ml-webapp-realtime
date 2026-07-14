from fastapi import BackgroundTasks, HTTPException

from app.domain.models.schemas import BuildAllOsmRequest
from app.interfaces.api import routes


def _reset_state(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_osm_build_state_path", tmp_path / "build-status.json")
    routes._osm_build_all_state.update(
        status="idle",
        cancel_requested=False,
        total=0,
        completed=0,
        succeeded=0,
        failed=0,
        skipped=0,
        current_dataset_id=None,
        items=[],
        started_at=None,
        finished_at=None,
        job_id=None,
    )


def test_build_all_rejects_second_request_while_starting(tmp_path, monkeypatch):
    _reset_state(tmp_path, monkeypatch)
    request = BuildAllOsmRequest()
    routes.build_all_osm_graphs(request, BackgroundTasks())

    try:
        routes.build_all_osm_graphs(request, BackgroundTasks())
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("Second build request must be rejected while starting")


def test_cancel_before_worker_start_is_preserved(tmp_path, monkeypatch):
    _reset_state(tmp_path, monkeypatch)
    job_id = "job-before-worker"
    routes._osm_build_all_state.update(status="starting", job_id=job_id)
    routes.cancel_build_all_osm()
    monkeypatch.setattr(routes.dataset_repo, "list_datasets", lambda: [])

    routes._run_build_all_graphs("drive", False, job_id)

    assert routes._osm_build_all_state["status"] == "cancelled"
    assert routes._osm_build_all_state["cancel_requested"] is True


def test_missing_graph_is_never_considered_valid(tmp_path):
    assert routes._graph_cache_is_valid(
        "missing-dataset",
        tmp_path / "missing.graphml",
        {"nodes": 1, "edges": 1},
        {"south": -7.0, "north": -6.0, "west": 106.0, "east": 107.0},
        "dataset_bbox",
    ) is False


def test_running_status_is_restored_as_interrupted(tmp_path, monkeypatch):
    _reset_state(tmp_path, monkeypatch)
    routes._osm_build_all_state.update(status="running", current_dataset_id="dki")
    with routes._osm_build_all_lock:
        routes._persist_build_all_state_locked()
    routes._osm_build_all_state.update(status="idle", current_dataset_id=None)

    with routes._osm_build_all_lock:
        routes._restore_build_all_state()

    assert routes._osm_build_all_state["status"] == "interrupted"
    assert routes._osm_build_all_state["current_dataset_id"] is None
