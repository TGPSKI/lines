"""Phase 1 optimistic multi-merge model.

Selects a batch of non-overlapping same-root-success MRs that can be
merged together without conflict, advancing target_head once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mutations import merge_mr
from .state import MergeRequest, SimState


@dataclass
class Phase1Config:
    enabled: bool = False
    merge_limit: int = 5
    conflict_key: str = "tenant_domains"


def select_merge_batch(
    state: SimState,
    config: Phase1Config,
) -> list[MergeRequest]:
    """Select a batch of co-mergeable MRs from the same-root success pool.

    Rules:
    1. MR must be open with a successful pipeline on current target_head.
    2. MR.rebased_target_sha == current target_head.
    3. Sorted by priority (lower number = higher priority).
    4. Include MRs whose conflict_key domains don't overlap with batch.
    5. Stop at merge_limit.
    """
    if not config.enabled:
        return []

    pool = state.same_root_success_pool()
    pool.sort(key=lambda mr: mr.priority)

    batch: list[MergeRequest] = []
    used_domains: set[str] = set()

    for mr in pool:
        if len(batch) >= config.merge_limit:
            break

        mr_domains = set(getattr(mr, config.conflict_key, []) or [])

        if mr_domains & used_domains:
            continue

        batch.append(mr)
        used_domains.update(mr_domains)

    return batch


def execute_batch_merge(
    state: SimState,
    batch: list[MergeRequest],
    metrics_callback: Any = None,
) -> dict:
    """Execute a Phase 1 batch merge.

    Merges all MRs in the batch, but only the last one advances target_head.
    In practice, we merge them sequentially, with target advancing after each.
    The key Phase 1 insight: all share the same root, so merging one doesn't
    invalidate the others' CI results within this batch.

    For the simulator, we advance target once at the end, treating the batch
    as an atomic multi-merge.
    """
    if not batch:
        return {"event": "phase1_batch", "batch_size": 0, "merged": []}

    merged_iids: list[int] = []
    old_target = state.project.target_head

    for mr in batch:
        event = merge_mr(state, mr)
        merged_iids.append(mr.iid)
        if metrics_callback:
            metrics_callback(event)

    batch_event = {
        "event": "phase1_batch",
        "tick": state.tick_count,
        "batch_size": len(batch),
        "merged": merged_iids,
        "old_target_head": old_target,
        "new_target_head": state.project.target_head,
    }

    return batch_event


def compute_phase1_metrics(
    events: list[dict],
) -> dict[str, Any]:
    """Compute Phase 1 specific metrics from event stream."""
    merge_events = [e for e in events if e.get("event") == "merge"]
    batch_events = [e for e in events if e.get("event") == "phase1_batch"]

    target_advances = len(
        set(e.get("new_target_head") for e in merge_events if e.get("new_target_head"))
    )
    total_merged = len(merge_events)

    batch_sizes = [
        e.get("batch_size", 0) for e in batch_events if e.get("batch_size", 0) > 0
    ]

    return {
        "total_merged": total_merged,
        "target_advances": target_advances,
        "average_mrs_per_target_advance": (
            total_merged / target_advances if target_advances > 0 else 0
        ),
        "merge_batches_completed": len(batch_sizes),
        "multi_merge_batch_sizes": batch_sizes,
        "average_batch_size": (
            sum(batch_sizes) / len(batch_sizes) if batch_sizes else 0
        ),
    }


def compute_overlap_blocked(
    state: SimState,
    config: Phase1Config,
) -> int:
    """Count MRs in same-root success pool blocked by domain overlap."""
    pool = state.same_root_success_pool()
    pool.sort(key=lambda mr: mr.priority)

    used_domains: set[str] = set()
    blocked = 0

    for mr in pool:
        mr_domains = set(getattr(mr, config.conflict_key, []) or [])
        if mr_domains & used_domains:
            blocked += 1
        else:
            used_domains.update(mr_domains)

    return blocked
