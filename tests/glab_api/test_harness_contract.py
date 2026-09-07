"""What the real gitlab_housekeeping integration requires of the fake server.

Each check here stands for a failure seen while running qontract-reconcile
0.10.2.dev859 (commit 5d6cf91) against this server on 2026-09-04.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from glab_api.server import create_app

SCENARIO = Path(__file__).resolve().parents[2] / "scenarios/mvp-active-cap.yaml"

MERGE_LABEL = "lgtm"


def _client() -> TestClient:
    return TestClient(create_app(SCENARIO))


def test_project_is_addressable_by_its_namespaced_path() -> None:
    # python-gitlab sends the project path percent-encoded; Starlette decodes
    # it before routing, so without the middleware this is a 404.
    with _client() as client:
        project = client.get("/api/v4/projects/example%2Fqueue-lab")
        mrs = client.get("/api/v4/projects/example%2Fqueue-lab/merge_requests")

    assert project.status_code == 200
    assert project.json()["path_with_namespace"] == "example/queue-lab"
    assert mrs.status_code == 200


def test_project_is_still_addressable_by_id() -> None:
    with _client() as client:
        assert client.get("/api/v4/projects/1001").status_code == 200


def test_merge_requests_report_an_update_time_the_stale_pass_can_parse() -> None:
    # handle_stale_items reads updated_at and compares it against wall-clock
    # now. Absent, python-gitlab raises AttributeError mid-run.
    with _client() as client:
        mrs = client.get("/api/v4/projects/1001/merge_requests").json()

    assert mrs
    for mr in mrs:
        updated = datetime.fromisoformat(mr["updated_at"])
        age = datetime.now(UTC) - updated.replace(tzinfo=UTC)
        assert age.days < 1, "an MR older than days_interval is labelled stale"


def test_label_events_are_dated_when_the_scenario_records_no_approval() -> None:
    # The merge-label event carries the approval time, and the integration
    # parses it. mvp-active-cap.yaml sets no approved_at; an empty string
    # there raised ValueError inside the merge pass.
    with _client() as client:
        events = client.get(
            "/api/v4/projects/1001/merge_requests/1/resource_label_events"
        ).json()

    dated = [e for e in events if e["label"]["name"] == MERGE_LABEL]
    assert dated, f"scenario MR 1 should carry {MERGE_LABEL}"
    for event in dated:
        assert datetime.fromisoformat(event["created_at"])
