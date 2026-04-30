"""FastAPI endpoints implementing the minimum GitLab API surface.

Returns GitLab-compatible JSON responses with pagination headers.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query, Request, Response

from . import gitlab_shapes as shapes
from .metrics import MetricsCollector
from .mutations import cancel_pipeline, merge_mr, rebase_mr, tick
from .state import MERGE_LABELS_SET, Commit, MRState, PipelineStatus, SimState

gitlab_router = APIRouter()
sim_router = APIRouter(prefix="/__sim")


def _get_state(request: Request) -> SimState:
    return request.app.state.sim_state


def _get_metrics(request: Request) -> MetricsCollector:
    return request.app.state.metrics


def _get_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _record_api_call(request: Request, endpoint: str) -> None:
    metrics = _get_metrics(request)
    metrics.record(
        {
            "event": "api_call",
            "endpoint": endpoint,
            "tick": _get_state(request).tick_count,
        }
    )


def _paginate(
    items: list[Any],
    page: int,
    per_page: int,
    response: Response,
) -> list[Any]:
    """Apply pagination and set GitLab-compatible headers."""
    total = len(items)
    total_pages = max(1, math.ceil(total / per_page))
    start = (page - 1) * per_page
    end = start + per_page

    response.headers["x-total"] = str(total)
    response.headers["x-total-pages"] = str(total_pages)
    response.headers["x-page"] = str(page)
    response.headers["x-per-page"] = str(per_page)
    response.headers["x-next-page"] = str(page + 1) if page < total_pages else ""

    return items[start:end]


# --- GitLab API endpoints ---


@gitlab_router.get("/api/v4/user")
def get_user(request: Request) -> dict:
    _record_api_call(request, "GET /user")
    return shapes.user_shape()


@gitlab_router.get("/api/v4/personal_access_tokens")
def get_personal_access_tokens(request: Request) -> list:
    _record_api_call(request, "GET /personal_access_tokens")
    return shapes.personal_access_token_shape()


@gitlab_router.get("/api/v4/groups/{group_id}")
def get_group(request: Request, group_id: str) -> dict:
    _record_api_call(request, "GET /groups/:id")
    return shapes.group_shape(group_id)


@gitlab_router.get("/api/v4/groups/{group_id}/members")
def get_group_members(
    request: Request,
    group_id: str,
    response: Response,
    page: int = Query(1),
    per_page: int = Query(20),
) -> list:
    _record_api_call(request, "GET /groups/:id/members")
    members = shapes.group_members_shape()
    return _paginate(members, page, per_page, response)


@gitlab_router.get("/api/v4/projects/{project_id}")
def get_project(request: Request, project_id: str) -> dict:
    _record_api_call(request, "GET /projects/:id")
    state = _get_state(request)
    base_url = _get_base_url(request)
    return shapes.project_shape(state.project, base_url)


@gitlab_router.get("/api/v4/projects/{project_id}/issues")
def get_project_issues(
    request: Request,
    project_id: str,
    response: Response,
    page: int = Query(1),
    per_page: int = Query(20),
) -> list:
    _record_api_call(request, "GET /projects/:id/issues")
    return _paginate([], page, per_page, response)


@gitlab_router.get("/api/v4/projects/{project_id}/merge_requests")
def get_merge_requests(
    request: Request,
    project_id: str,
    response: Response,
    state: str | None = Query(None),
    page: int = Query(1),
    per_page: int = Query(20),
) -> list:
    _record_api_call(request, "GET /projects/:id/merge_requests")
    sim_state = _get_state(request)
    base_url = _get_base_url(request)

    mrs = sim_state.merge_requests
    if state:
        mrs = [mr for mr in mrs if mr.state.value == state]

    mr_dicts = shapes.mr_list_shape(mrs, sim_state.project, base_url)
    return _paginate(mr_dicts, page, per_page, response)


@gitlab_router.get("/api/v4/projects/{project_id}/merge_requests/{mr_iid}")
def get_merge_request(request: Request, project_id: str, mr_iid: int) -> dict:
    _record_api_call(request, "GET /projects/:id/merge_requests/:iid")
    sim_state = _get_state(request)
    base_url = _get_base_url(request)

    mr = sim_state.get_mr(mr_iid)
    if mr is None:
        return {"error": "Not found", "message": f"MR {mr_iid} not found"}

    return shapes.mr_shape(mr, sim_state.project, base_url)


@gitlab_router.get("/api/v4/projects/{project_id}/merge_requests/{mr_iid}/commits")
def get_mr_commits(
    request: Request,
    project_id: str,
    mr_iid: int,
    response: Response,
    page: int = Query(1),
    per_page: int = Query(20),
) -> list:
    _record_api_call(request, "GET /projects/:id/merge_requests/:iid/commits")
    sim_state = _get_state(request)

    mr = sim_state.get_mr(mr_iid)
    if mr is None:
        return []

    commit_dicts = shapes.commit_list_shape(mr.commits)
    return _paginate(commit_dicts, page, per_page, response)


@gitlab_router.get(
    "/api/v4/projects/{project_id}/merge_requests/{mr_iid}/resource_label_events"
)
def get_mr_label_events(
    request: Request,
    project_id: str,
    mr_iid: int,
    response: Response,
    page: int = Query(1),
    per_page: int = Query(20),
) -> list:
    _record_api_call(
        request, "GET /projects/:id/merge_requests/:iid/resource_label_events"
    )
    sim_state = _get_state(request)

    mr = sim_state.get_mr(mr_iid)
    if mr is None:
        return []

    default_ts = "2024-01-01T00:00:00Z"
    events = [
        shapes.label_event_shape(
            i + 1,
            label,
            created_at=(
                mr.approved_at if label in MERGE_LABELS_SET else default_ts
            ),
        )
        for i, label in enumerate(mr.labels)
    ]
    return _paginate(events, page, per_page, response)


@gitlab_router.get("/api/v4/projects/{project_id}/merge_requests/{mr_iid}/pipelines")
def get_mr_pipelines(
    request: Request,
    project_id: str,
    mr_iid: int,
    response: Response,
    page: int = Query(1),
    per_page: int = Query(20),
) -> list:
    _record_api_call(request, "GET /projects/:id/merge_requests/:iid/pipelines")
    sim_state = _get_state(request)

    mr = sim_state.get_mr(mr_iid)
    if mr is None:
        return []

    pipeline_dicts = shapes.pipeline_list_shape(mr.pipelines)
    return _paginate(pipeline_dicts, page, per_page, response)


@gitlab_router.get("/api/v4/projects/{project_id}/repository/commits")
def get_repository_commits(
    request: Request,
    project_id: str,
    response: Response,
    ref_name: str | None = Query(None),
    page: int = Query(1),
    per_page: int = Query(20),
) -> list:
    _record_api_call(request, "GET /projects/:id/repository/commits")
    sim_state = _get_state(request)

    target_commit = Commit(
        id=sim_state.project.target_head,
        short_id=sim_state.project.target_head[:8],
        title="target head",
    )
    commits = [shapes.commit_shape(target_commit)]
    return _paginate(commits, page, per_page, response)


@gitlab_router.get("/api/v4/projects/{project_id}/repository/compare")
def get_repository_compare(
    request: Request,
    project_id: str,
) -> dict:
    """Repository compare — critical for housekeeping rebase detection.

    If commits == [], housekeeping considers MR rebased.
    If commits != [], housekeeping considers MR not rebased.
    """
    _record_api_call(request, "GET /projects/:id/repository/compare")
    sim_state = _get_state(request)

    from_sha = request.query_params.get("from", "")
    to_sha = request.query_params.get("to", "")

    # Find the MR by its sha to get rebased_target_sha
    rebased_target_sha = ""
    for mr in sim_state.merge_requests:
        if mr.sha == from_sha:
            rebased_target_sha = mr.rebased_target_sha
            break

    return shapes.compare_shape(from_sha, to_sha, rebased_target_sha)


@gitlab_router.put("/api/v4/projects/{project_id}/merge_requests/{mr_iid}")
async def update_merge_request(request: Request, project_id: str, mr_iid: int) -> dict:
    """Update MR attributes (labels, state, etc.)."""
    _record_api_call(request, "PUT /projects/:id/merge_requests/:iid")
    sim_state = _get_state(request)
    base_url = _get_base_url(request)

    mr = sim_state.get_mr(mr_iid)
    if mr is None:
        return {"error": "Not found"}

    body = await request.json() if await request.body() else {}

    if "labels" in body:
        mr.labels = (
            body["labels"]
            if isinstance(body["labels"], list)
            else body["labels"].split(",")
        )
    if "state_event" in body and body["state_event"] == "close":
        mr.state = MRState.CLOSED

    return shapes.mr_shape(mr, sim_state.project, base_url)


@gitlab_router.put("/api/v4/projects/{project_id}/merge_requests/{mr_iid}/rebase")
async def rebase_merge_request(request: Request, project_id: str, mr_iid: int) -> dict:
    """Rebase an MR — creates a new pipeline and updates SHA."""
    _record_api_call(request, "PUT /projects/:id/merge_requests/:iid/rebase")
    sim_state = _get_state(request)
    metrics = _get_metrics(request)

    mr = sim_state.get_mr(mr_iid)
    if mr is None:
        return {"error": "Not found"}

    event = rebase_mr(sim_state, mr)
    metrics.record(event)

    return {"rebase_in_progress": True}


@gitlab_router.put("/api/v4/projects/{project_id}/merge_requests/{mr_iid}/merge")
async def merge_merge_request(request: Request, project_id: str, mr_iid: int) -> dict:
    """Merge an MR — advances target_head and marks MR merged."""
    _record_api_call(request, "PUT /projects/:id/merge_requests/:iid/merge")
    sim_state = _get_state(request)
    metrics = _get_metrics(request)
    base_url = _get_base_url(request)

    mr = sim_state.get_mr(mr_iid)
    if mr is None:
        return {"error": "Not found"}

    event = merge_mr(sim_state, mr)
    metrics.record(event)

    return shapes.mr_shape(mr, sim_state.project, base_url)


@gitlab_router.get("/api/v4/projects/{project_id}/pipelines/{pipeline_id}")
def get_pipeline(request: Request, project_id: str, pipeline_id: int) -> dict:
    _record_api_call(request, "GET /projects/:id/pipelines/:pipeline_id")
    sim_state = _get_state(request)

    for mr in sim_state.merge_requests:
        for p in mr.pipelines:
            if p.id == pipeline_id:
                return shapes.pipeline_shape(p)

    return {"error": "Not found"}


@gitlab_router.post("/api/v4/projects/{project_id}/pipelines/{pipeline_id}/cancel")
def cancel_pipeline_endpoint(
    request: Request, project_id: str, pipeline_id: int
) -> dict:
    _record_api_call(request, "POST /projects/:id/pipelines/:pipeline_id/cancel")
    sim_state = _get_state(request)
    metrics = _get_metrics(request)

    for mr in sim_state.merge_requests:
        for p in mr.pipelines:
            if p.id == pipeline_id:
                if p.status in (PipelineStatus.PENDING, PipelineStatus.RUNNING):
                    event = cancel_pipeline(sim_state, p, mr)
                    metrics.record(event)
                return shapes.pipeline_shape(p)

    return {"error": "Not found"}


# --- Simulator control endpoints ---


@sim_router.post("/tick")
def sim_tick(request: Request) -> dict:
    """Advance simulation by one tick."""
    sim_state = _get_state(request)
    metrics = _get_metrics(request)

    event = tick(sim_state)
    metrics.record(event)
    metrics.record_snapshot(sim_state)

    return event


@sim_router.get("/metrics")
def sim_metrics(request: Request) -> dict:
    """Return computed metrics summary."""
    metrics = _get_metrics(request)
    return metrics.summary()


@sim_router.get("/state")
def sim_state_endpoint(request: Request) -> dict:
    """Return current simulation state."""
    sim_state = _get_state(request)
    return sim_state.to_dict()


@sim_router.get("/merged_mrs")
def sim_merged_mrs(request: Request) -> list:
    """Return merged MRs with wait-time data for starvation analysis."""
    sim_state = _get_state(request)
    metrics = _get_metrics(request)

    merge_events = [e for e in metrics.events if e.get("event") == "merge"]
    merge_tick_map = {e["mr_iid"]: e["tick"] for e in merge_events}

    result = []
    for mr in sim_state.merge_requests:
        if mr.state != MRState.MERGED:
            continue
        merge_tick = merge_tick_map.get(mr.iid, 0)
        arrival = max(0, mr.arrival_tick)
        wait = merge_tick - arrival
        labels = mr.labels
        priority = "unknown"
        for lbl in labels:
            if lbl.startswith("bot/approved:"):
                priority = lbl.split(":", 1)[1].strip()
                break
        result.append({
            "iid": mr.iid,
            "arrival_tick": arrival,
            "merge_tick": merge_tick,
            "wait_ticks": wait,
            "priority": priority,
            "title": mr.title,
        })
    return result


@sim_router.post("/reset")
def sim_reset(request: Request) -> dict:
    """Reset simulation to initial scenario state."""
    # Re-load from the stored initial scenario
    initial_scenario_path = getattr(request.app.state, "scenario_path", None)
    if initial_scenario_path:
        from .scenario import load_scenario

        request.app.state.sim_state = load_scenario(initial_scenario_path)
        request.app.state.metrics = MetricsCollector()
        return {"status": "reset", "scenario": str(initial_scenario_path)}
    return {"status": "error", "message": "No scenario path stored"}
