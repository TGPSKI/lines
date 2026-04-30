"""State mutations: rebase, merge, tick, cancel.

Each mutation modifies SimState in place and returns a metrics event dict.
"""

from __future__ import annotations

from .state import (
    Commit,
    MergeRequest,
    MRState,
    Pipeline,
    PipelineStatus,
    SimState,
)


def rebase_mr(state: SimState, mr: MergeRequest) -> dict:
    """Simulate a rebase of an MR.

    1. Assign new branch SHA from pool or synthetic.
    2. Set rebased_target_sha = current target_head.
    3. Append a new commit.
    4. Create a pending pipeline.
    5. Return metrics event.
    """
    new_sha = state.sha_pools.next_mr_sha(mr.iid)
    old_sha = mr.sha

    mr.sha = new_sha
    mr.rebased_target_sha = state.project.target_head
    mr.rebase_count += 1

    commit = Commit(
        id=new_sha,
        short_id=new_sha[:8],
        title=f"Rebase {mr.source_branch} onto {state.project.target_head[:8]}",
    )
    mr.commits.append(commit)

    pipeline_id = state.next_pipeline_id()
    duration = state.pipeline_duration_config.sample_duration()
    pipeline = Pipeline(
        id=pipeline_id,
        status=PipelineStatus.PENDING,
        sha=new_sha,
        root_sha=state.project.target_head,
        pending_ticks_remaining=1,
        running_ticks_remaining=duration,
        outcome=PipelineStatus.SUCCESS,
    )
    mr.pipelines.append(pipeline)

    return {
        "event": "rebase",
        "tick": state.tick_count,
        "mr_iid": mr.iid,
        "old_sha": old_sha,
        "new_sha": new_sha,
        "target_head": state.project.target_head,
        "pipeline_id": pipeline_id,
        "rebase_count": mr.rebase_count,
    }


def merge_mr(state: SimState, mr: MergeRequest) -> dict:
    """Simulate merging an MR.

    1. Mark MR as merged.
    2. Advance target_head.
    3. Record metrics event.
    """
    old_target = state.project.target_head
    new_target = state.sha_pools.next_target_sha(mr.target_branch)

    mr.state = MRState.MERGED
    state.project.target_head = new_target

    return {
        "event": "merge",
        "tick": state.tick_count,
        "mr_iid": mr.iid,
        "old_target_head": old_target,
        "new_target_head": new_target,
        "stale_successes_created": _count_newly_stale(state, old_target),
    }


def cancel_pipeline(state: SimState, pipeline: Pipeline, mr: MergeRequest) -> dict:
    """Cancel an active pipeline."""
    old_status = pipeline.status
    pipeline.status = PipelineStatus.CANCELED

    return {
        "event": "pipeline_cancel",
        "tick": state.tick_count,
        "mr_iid": mr.iid,
        "pipeline_id": pipeline.id,
        "old_status": old_status.value,
    }


def tick(state: SimState) -> dict:
    """Advance simulation by one tick.

    Pipeline lifecycle: pending -> running -> outcome (success/failed).
    """
    state.tick_count += 1
    transitions: list[dict] = []

    for mr in state.merge_requests:
        for p in mr.pipelines:
            if p.status == PipelineStatus.PENDING:
                if p.pending_ticks_remaining <= 1:
                    p.status = PipelineStatus.RUNNING
                    transitions.append(
                        {
                            "pipeline_id": p.id,
                            "mr_iid": mr.iid,
                            "from": "pending",
                            "to": "running",
                        }
                    )
                else:
                    p.pending_ticks_remaining -= 1

            elif p.status == PipelineStatus.RUNNING:
                if p.running_ticks_remaining <= 1:
                    p.status = p.outcome
                    transitions.append(
                        {
                            "pipeline_id": p.id,
                            "mr_iid": mr.iid,
                            "from": "running",
                            "to": p.outcome.value,
                        }
                    )
                else:
                    p.running_ticks_remaining -= 1

    return {
        "event": "tick",
        "tick": state.tick_count,
        "transitions": transitions,
        "active_pipelines": len(state.active_pipelines()),
        "same_root_success_pool": len(state.same_root_success_pool()),
        "stale_successes": len(state.stale_successes()),
        "open_mrs": len(state.open_mrs()),
    }


def _count_newly_stale(state: SimState, old_target: str) -> int:
    """Count pipelines that became stale due to a target advance."""
    count = 0
    for mr in state.open_mrs():
        for p in mr.pipelines:
            if p.status == PipelineStatus.SUCCESS and p.root_sha == old_target:
                count += 1
    return count
