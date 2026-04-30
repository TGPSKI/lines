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
    duration = (
        mr.ci_duration
        if mr.ci_duration is not None
        else state.pipeline_duration_config.sample_duration()
    )
    outcome = state.pipeline_duration_config.sample_outcome()
    pipeline = Pipeline(
        id=pipeline_id,
        status=PipelineStatus.PENDING,
        sha=new_sha,
        root_sha=state.project.target_head,
        pending_ticks_remaining=1,
        running_ticks_remaining=duration,
        outcome=outcome,
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
        "pipeline_outcome": outcome.value,
        "rebase_count": mr.rebase_count,
    }


def merge_mr(
    state: SimState,
    mr: MergeRequest,
    batch_peer_iids: set[int] | None = None,
) -> dict:
    """Simulate merging an MR.

    1. Mark MR as merged.
    2. Advance target_head.
    3. Record metrics event.

    Args:
        batch_peer_iids: IIDs of other MRs being merged in the same Phase 1
            batch.  Their pipelines are NOT counted as stale because they
            will be merged momentarily in the same atomic batch operation.
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
        "stale_successes_created": _count_newly_stale(
            state, old_target, batch_peer_iids or set()
        ),
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

    Order of operations each tick:
    1. Activate MRs whose ``arrival_tick`` has been reached.
    2. Author pushes — new commit invalidates rebase state + cancels pipelines.
    3. Force-merge MRs whose ``force_merge_tick`` has been reached — bypasses
       all queue logic (models a human clicking "Merge" in the GitLab UI).
    4. Cancel/close MRs whose ``cancel_tick`` has been reached.
    5. Apply scheduled external target_head advances.
    6. Pipeline lifecycle: pending -> running -> outcome (success/failed).
    """
    state.tick_count += 1
    transitions: list[dict] = []
    arrivals: list[int] = []
    pushes: list[int] = []
    force_merges: list[dict] = []
    cancellations: list[int] = []
    external_advance: dict | None = None

    # 1. Arrivals
    for mr in state.merge_requests:
        if mr.state == MRState.CLOSED and 0 < mr.arrival_tick <= state.tick_count:
            mr.state = MRState.OPENED
            arrivals.append(mr.iid)

    # 2. Author pushes — new commit invalidates rebase + cancels active pipelines
    for mr in state.merge_requests:
        if mr.is_open and 0 < mr.push_tick <= state.tick_count:
            mr.sha = f"mr{mr.iid}-push-{state.tick_count}"
            mr.rebased_target_sha = ""
            for p in mr.pipelines:
                if p.is_active:
                    p.status = PipelineStatus.CANCELED
            pushes.append(mr.iid)
            mr.push_tick = 0

    # 3. Force-merges — bypass queue, advance target, cancel nothing beforehand
    for mr in state.merge_requests:
        if mr.is_open and 0 < mr.force_merge_tick <= state.tick_count:
            fm_event = merge_mr(state, mr)
            fm_event["force_merge"] = True
            force_merges.append(fm_event)

    # 3. Cancellations
    for mr in state.merge_requests:
        if mr.is_open and 0 < mr.cancel_tick <= state.tick_count:
            mr.state = MRState.CLOSED
            for p in mr.pipelines:
                if p.is_active:
                    p.status = PipelineStatus.CANCELED
            cancellations.append(mr.iid)

    # 4. External target advances
    if state.tick_count in state.scheduled_target_advances:
        new_sha = state.scheduled_target_advances[state.tick_count]
        old_sha = state.project.target_head
        state.project.target_head = new_sha
        external_advance = {"old_target_head": old_sha, "new_target_head": new_sha}

    # 5. Pipeline lifecycle
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

    event: dict = {
        "event": "tick",
        "tick": state.tick_count,
        "transitions": transitions,
        "arrivals": arrivals,
        "pushes": pushes,
        "force_merges": force_merges,
        "cancellations": cancellations,
        "active_pipelines": len(state.active_pipelines()),
        "same_root_success_pool": len(state.same_root_success_pool()),
        "stale_successes": len(state.stale_successes()),
        "open_mrs": len(state.open_mrs()),
    }
    if external_advance:
        event["external_advance"] = external_advance
    return event


def _count_newly_stale(
    state: SimState, old_target: str, exclude_iids: set[int] | None = None
) -> int:
    """Count pipelines that became stale due to a target advance.

    Pipelines belonging to MRs in ``exclude_iids`` are not counted because
    those MRs are co-members of a Phase 1 batch and will be merged using
    the same root — their CI results are still valid.
    """
    excl = exclude_iids or set()
    count = 0
    for mr in state.open_mrs():
        if mr.iid in excl:
            continue
        for p in mr.pipelines:
            if p.status == PipelineStatus.SUCCESS and p.root_sha == old_target:
                count += 1
    return count
