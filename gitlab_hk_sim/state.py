"""Core state model for the GitLab Housekeeping Policy Simulator.

Represents a simulated GitLab project with MRs, pipelines, and SHA pools.
All state is mutable and advanced by mutations (rebase, merge, tick).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PipelineStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"


class MRState(StrEnum):
    OPENED = "opened"
    MERGED = "merged"
    CLOSED = "closed"


@dataclass
class Pipeline:
    id: int
    status: PipelineStatus
    sha: str
    root_sha: str
    pending_ticks_remaining: int = 1
    running_ticks_remaining: int = 3
    outcome: PipelineStatus = PipelineStatus.SUCCESS

    @property
    def is_active(self) -> bool:
        return self.status in (PipelineStatus.PENDING, PipelineStatus.RUNNING)

    @property
    def is_stale(self) -> bool:
        """A pipeline is stale if it succeeded but its root_sha != current target.
        Caller must check against current target_head.
        """
        return self.status == PipelineStatus.SUCCESS


@dataclass
class Commit:
    id: str
    short_id: str
    title: str
    message: str = ""
    author_name: str = "Sim User"
    author_email: str = "sim@example.com"


@dataclass
class MergeRequest:
    id: int
    iid: int
    title: str
    state: MRState = MRState.OPENED
    draft: bool = False
    merge_status: str = "can_be_merged"
    target_branch: str = "master"
    source_branch: str = ""
    source_project_id: int = 0
    target_project_id: int = 0
    sha: str = ""
    rebased_target_sha: str = ""
    labels: list[str] = field(default_factory=list)
    tenant_domains: list[str] = field(default_factory=list)
    pipelines: list[Pipeline] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)
    priority: int = 0
    rebase_count: int = 0

    @property
    def is_open(self) -> bool:
        return self.state == MRState.OPENED

    def latest_pipeline(self) -> Pipeline | None:
        if not self.pipelines:
            return None
        return self.pipelines[-1]

    def successful_pipelines_for_root(self, target_head: str) -> list[Pipeline]:
        return [
            p
            for p in self.pipelines
            if p.status == PipelineStatus.SUCCESS and p.root_sha == target_head
        ]

    def has_useful_success(self, target_head: str) -> bool:
        return (
            self.is_open
            and self.rebased_target_sha == target_head
            and any(
                p.status == PipelineStatus.SUCCESS
                and p.root_sha == target_head
                and p.sha == self.sha
                for p in self.pipelines
            )
        )


@dataclass
class SHAPools:
    """Pre-defined SHA sequences for deterministic simulation."""

    mr_rebases: dict[str, list[str]] = field(default_factory=dict)
    target_advances: dict[str, list[str]] = field(default_factory=dict)
    _mr_rebase_idx: dict[str, int] = field(default_factory=dict)
    _target_advance_idx: dict[str, int] = field(default_factory=dict)

    def next_mr_sha(self, mr_iid: int) -> str:
        key = str(mr_iid)
        pool = self.mr_rebases.get(key, [])
        idx = self._mr_rebase_idx.get(key, 0)
        if idx < len(pool):
            sha = pool[idx]
            self._mr_rebase_idx[key] = idx + 1
            return sha
        return f"mr{mr_iid}-sha-{uuid.uuid4().hex[:8]}"

    def next_target_sha(self, branch: str = "master") -> str:
        pool = self.target_advances.get(branch, [])
        idx = self._target_advance_idx.get(branch, 0)
        if idx < len(pool):
            sha = pool[idx]
            self._target_advance_idx[branch] = idx + 1
            return sha
        return f"target-{uuid.uuid4().hex[:8]}"


@dataclass
class Project:
    id: int
    name: str
    path: str
    path_with_namespace: str
    web_url: str = ""
    default_branch: str = "master"
    squash_option: str = "default_on"
    target_head: str = ""


@dataclass
class LabelEvent:
    """Resource label event for an MR."""

    id: int
    label_name: str
    action: str = "add"
    created_at: str = "2024-01-01T00:00:00Z"


@dataclass
class PipelineDurationConfig:
    """Configuration for how long new pipelines take."""

    min_ticks: int = 3
    max_ticks: int = 3
    weights: dict[int, int] = field(default_factory=dict)

    def sample_duration(self) -> int:
        """Sample a pipeline running duration from the configured distribution."""
        if self.weights:
            durations = list(self.weights.keys())
            weights = list(self.weights.values())
            return random.choices(durations, weights=weights, k=1)[0]
        if self.min_ticks == self.max_ticks:
            return self.min_ticks
        return random.randint(self.min_ticks, self.max_ticks)


@dataclass
class SimState:
    """Complete simulator state, loaded from scenario YAML."""

    project: Project
    merge_requests: list[MergeRequest] = field(default_factory=list)
    sha_pools: SHAPools = field(default_factory=SHAPools)
    pipeline_duration_config: PipelineDurationConfig = field(
        default_factory=PipelineDurationConfig
    )
    tick_count: int = 0
    _next_pipeline_id: int = field(default=9000)

    def next_pipeline_id(self) -> int:
        pid = self._next_pipeline_id
        self._next_pipeline_id += 1
        return pid

    def get_mr(self, iid: int) -> MergeRequest | None:
        for mr in self.merge_requests:
            if mr.iid == iid:
                return mr
        return None

    def open_mrs(self) -> list[MergeRequest]:
        return [mr for mr in self.merge_requests if mr.is_open]

    def all_pipelines(self) -> list[Pipeline]:
        pipes: list[Pipeline] = []
        for mr in self.merge_requests:
            pipes.extend(mr.pipelines)
        return pipes

    def active_pipelines(self) -> list[Pipeline]:
        return [p for p in self.all_pipelines() if p.is_active]

    def same_root_success_pool(self) -> list[MergeRequest]:
        return [
            mr
            for mr in self.open_mrs()
            if mr.has_useful_success(self.project.target_head)
        ]

    def stale_successes(self) -> list[Pipeline]:
        return [
            p
            for p in self.all_pipelines()
            if p.status == PipelineStatus.SUCCESS
            and p.root_sha != self.project.target_head
        ]

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot for /__sim/state."""
        return {
            "tick_count": self.tick_count,
            "target_head": self.project.target_head,
            "open_mrs": len(self.open_mrs()),
            "active_pipelines": len(self.active_pipelines()),
            "same_root_success_pool": len(self.same_root_success_pool()),
            "stale_successes": len(self.stale_successes()),
            "merge_requests": [
                {
                    "iid": mr.iid,
                    "title": mr.title,
                    "state": mr.state.value,
                    "sha": mr.sha,
                    "rebased_target_sha": mr.rebased_target_sha,
                    "rebase_count": mr.rebase_count,
                    "pipelines": [
                        {
                            "id": p.id,
                            "status": p.status.value,
                            "sha": p.sha,
                            "root_sha": p.root_sha,
                        }
                        for p in mr.pipelines
                    ],
                }
                for mr in self.merge_requests
            ],
        }
