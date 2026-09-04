"""Scenario loader – parses YAML scenario files into SimState."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .state import (
    Commit,
    MergeRequest,
    MRState,
    OperationFailureConfig,
    Pipeline,
    PipelineDurationConfig,
    PipelineStatus,
    Project,
    SHAPools,
    SimState,
)


def load_scenario(path: str | Path) -> SimState:
    """Load a scenario YAML file and return initialized SimState."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return _build_state(raw)


def _build_state(raw: dict[str, Any]) -> SimState:
    project = _build_project(raw["project"])
    mrs = [_build_mr(m, project.id) for m in raw.get("merge_requests", [])]
    sha_pools = _build_sha_pools(raw.get("sha_pools", {}))
    pipeline_duration_config = _build_pipeline_duration_config(
        raw.get("pipeline_durations", {})
    )
    operation_failure_config = _build_operation_failure_config(
        raw.get("failure_path_realism")
    )

    max_pipeline_id = 9000
    for mr in mrs:
        for p in mr.pipelines:
            if p.id >= max_pipeline_id:
                max_pipeline_id = p.id + 1

    scheduled_target_advances = {
        int(k): str(v) for k, v in raw.get("scheduled_target_advances", {}).items()
    }
    tick_seconds = max(1, int(raw.get("tick_seconds", 60)))

    return SimState(
        project=project,
        merge_requests=mrs,
        sha_pools=sha_pools,
        pipeline_duration_config=pipeline_duration_config,
        operation_failure_config=operation_failure_config,
        scenario_metadata=raw.get("metadata", {}),
        tick_seconds=tick_seconds,
        scheduled_target_advances=scheduled_target_advances,
        _next_pipeline_id=max_pipeline_id,
    )


def _build_project(raw: dict[str, Any]) -> Project:
    return Project(
        id=raw["id"],
        name=raw["name"],
        path=raw.get("path", raw["name"]),
        path_with_namespace=raw.get("path_with_namespace", f"sim/{raw['name']}"),
        web_url=raw.get("web_url", ""),
        default_branch=raw.get("default_branch", "master"),
        squash_option=raw.get("squash_option", "default_on"),
        target_head=raw["target_head"],
    )


def _build_mr(raw: dict[str, Any], project_id: int) -> MergeRequest:
    pipelines = [_build_pipeline(p) for p in raw.get("pipelines", [])]
    commits = [_build_commit(c) for c in raw.get("commits", [])]

    if not commits and raw.get("sha"):
        commits = [
            Commit(
                id=raw["sha"],
                short_id=raw["sha"][:8],
                title=raw.get("title", ""),
            )
        ]

    arrival_tick = int(raw.get("arrival_tick", 0))
    explicit_state = raw.get("state", "opened")
    if arrival_tick > 0 and explicit_state == "opened":
        effective_state = MRState.CLOSED
    else:
        effective_state = MRState(explicit_state)

    merge_failure_raw = raw.get("merge_failure", {})
    remaining_raw = (
        merge_failure_raw.get("remaining")
        if isinstance(merge_failure_raw, dict)
        else raw.get("merge_failures_remaining", 0)
    )
    if remaining_raw is None:
        remaining_raw = raw.get("merge_failures_remaining", 0)
    merge_failures_remaining = _parse_failures_remaining(remaining_raw)
    merge_failure_status_code = int(
        (
            merge_failure_raw.get("status_code")
            if isinstance(merge_failure_raw, dict)
            else raw.get("merge_failure_status_code", 405)
        )
        or raw.get("merge_failure_status_code", 405)
    )
    merge_failure_detail = str(
        (
            merge_failure_raw.get("detail")
            if isinstance(merge_failure_raw, dict)
            else raw.get("merge_failure_detail", "405 Method Not Allowed")
        )
        or raw.get("merge_failure_detail", "405 Method Not Allowed")
    )

    return MergeRequest(
        id=raw["id"],
        iid=raw["iid"],
        title=raw.get("title", f"MR {raw['iid']}"),
        state=effective_state,
        draft=raw.get("draft", False),
        merge_status=raw.get("merge_status", "can_be_merged"),
        target_branch=raw.get("target_branch", "master"),
        source_branch=raw.get("source_branch", f"sim/mr-{raw['iid']}"),
        source_project_id=raw.get("source_project_id", project_id),
        target_project_id=raw.get("target_project_id", project_id),
        sha=raw.get("sha", ""),
        rebased_target_sha=raw.get("rebased_target_sha", ""),
        labels=raw.get("labels", []),
        pipelines=pipelines,
        commits=commits,
        approved_at=raw.get("approved_at", ""),
        arrival_tick=arrival_tick,
        cancel_tick=int(raw.get("cancel_tick", 0)),
        force_merge_tick=int(raw.get("force_merge_tick", 0)),
        push_tick=int(raw.get("push_tick", 0)),
        ci_duration=int(raw["ci_duration"]) if "ci_duration" in raw else None,
        merge_failures_remaining=merge_failures_remaining,
        merge_failure_status_code=merge_failure_status_code,
        merge_failure_detail=merge_failure_detail,
    )


def _build_pipeline(raw: dict[str, Any]) -> Pipeline:
    return Pipeline(
        id=raw["id"],
        status=PipelineStatus(raw["status"]),
        sha=raw["sha"],
        root_sha=raw.get("root_sha", ""),
        pending_ticks_remaining=raw.get("pending_ticks_remaining", 0),
        running_ticks_remaining=raw.get("running_ticks_remaining", 0),
        outcome=PipelineStatus(raw.get("outcome", raw["status"])),
    )


def _build_commit(raw: dict[str, Any]) -> Commit:
    return Commit(
        id=raw["id"],
        short_id=raw.get("short_id", raw["id"][:8]),
        title=raw.get("title", ""),
        message=raw.get("message", ""),
    )


def _build_sha_pools(raw: dict[str, Any]) -> SHAPools:
    return SHAPools(
        mr_rebases=raw.get("mr_rebases", {}),
        target_advances=raw.get("target_advances", {}),
    )


def _build_pipeline_duration_config(raw: dict[str, Any]) -> PipelineDurationConfig:
    if not raw:
        return PipelineDurationConfig()
    weights = {int(k): int(v) for k, v in raw.get("weights", {}).items()}
    return PipelineDurationConfig(
        min_ticks=raw.get("min_ticks", 3),
        max_ticks=raw.get("max_ticks", 3),
        weights=weights,
        failure_rate=float(raw.get("failure_rate", 0.0)),
    )


def _build_operation_failure_config(raw: Any) -> OperationFailureConfig:
    """Build per-scenario merge/rebase operation failure configuration."""
    if raw is None:
        return OperationFailureConfig()

    if isinstance(raw, (int, float)):
        rate = _normalized_rate(float(raw))
        return OperationFailureConfig(
            merge_failure_rate=rate,
            rebase_failure_rate=rate,
        )

    if not isinstance(raw, dict):
        return OperationFailureConfig()

    default_rate = _normalized_rate(float(raw.get("failure_rate", 0.0)))
    merge_failure_rate = _normalized_rate(
        float(raw.get("merge_failure_rate", default_rate))
    )
    rebase_failure_rate = _normalized_rate(
        float(raw.get("rebase_failure_rate", default_rate))
    )
    return OperationFailureConfig(
        merge_failure_rate=merge_failure_rate,
        rebase_failure_rate=rebase_failure_rate,
    )


def _normalized_rate(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_failures_remaining(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, str) and raw.strip().lower() == "always":
        return -1
    try:
        return int(raw)
    except TypeError, ValueError:
        return 0
