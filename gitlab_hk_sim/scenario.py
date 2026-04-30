"""Scenario loader – parses YAML scenario files into SimState."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .state import (
    Commit,
    MergeRequest,
    MRState,
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

    max_pipeline_id = 9000
    for mr in mrs:
        for p in mr.pipelines:
            if p.id >= max_pipeline_id:
                max_pipeline_id = p.id + 1

    scheduled_target_advances = {
        int(k): str(v) for k, v in raw.get("scheduled_target_advances", {}).items()
    }

    return SimState(
        project=project,
        merge_requests=mrs,
        sha_pools=sha_pools,
        pipeline_duration_config=pipeline_duration_config,
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
