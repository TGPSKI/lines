"""Tests for state mutations."""

from gitlab_hk_sim.mutations import cancel_pipeline, merge_mr, rebase_mr, tick
from gitlab_hk_sim.state import (
    MergeRequest,
    MRState,
    Pipeline,
    PipelineStatus,
    Project,
    SHAPools,
    SimState,
)


def _make_state(
    target_head: str = "target-001",
    mrs: list[MergeRequest] | None = None,
    sha_pools: SHAPools | None = None,
) -> SimState:
    project = Project(
        id=1001,
        name="sim-repo",
        path="sim-repo",
        path_with_namespace="app-sre/sim-repo",
        target_head=target_head,
    )
    return SimState(
        project=project,
        merge_requests=mrs or [],
        sha_pools=sha_pools or SHAPools(),
    )


def _make_mr(
    iid: int = 1, sha: str = "mr1-sha-001", rebased: str = "target-000"
) -> MergeRequest:
    return MergeRequest(
        id=2000 + iid,
        iid=iid,
        title=f"MR {iid}",
        sha=sha,
        rebased_target_sha=rebased,
        source_project_id=1001,
        target_project_id=1001,
    )


class TestRebase:
    def test_rebase_updates_sha(self):
        pools = SHAPools(mr_rebases={"1": ["new-sha-001"]})
        mr = _make_mr(iid=1, sha="old-sha", rebased="target-000")
        state = _make_state(target_head="target-001", mrs=[mr], sha_pools=pools)

        event = rebase_mr(state, mr)

        assert mr.sha == "new-sha-001"
        assert mr.rebased_target_sha == "target-001"
        assert mr.rebase_count == 1
        assert event["event"] == "rebase"
        assert event["new_sha"] == "new-sha-001"

    def test_rebase_creates_pipeline(self):
        mr = _make_mr(iid=1)
        state = _make_state(mrs=[mr])

        rebase_mr(state, mr)

        assert len(mr.pipelines) == 1
        p = mr.pipelines[0]
        assert p.status == PipelineStatus.PENDING
        assert p.sha == mr.sha
        assert p.root_sha == "target-001"

    def test_rebase_increments_count(self):
        mr = _make_mr(iid=1)
        state = _make_state(mrs=[mr])

        rebase_mr(state, mr)
        rebase_mr(state, mr)

        assert mr.rebase_count == 2


class TestMerge:
    def test_merge_marks_merged(self):
        pools = SHAPools(target_advances={"master": ["target-002"]})
        mr = _make_mr(iid=1, sha="mr1-sha-001", rebased="target-001")
        state = _make_state(target_head="target-001", mrs=[mr], sha_pools=pools)

        event = merge_mr(state, mr)

        assert mr.state == MRState.MERGED
        assert state.project.target_head == "target-002"
        assert event["event"] == "merge"
        assert event["new_target_head"] == "target-002"

    def test_merge_advances_target(self):
        pools = SHAPools(target_advances={"master": ["target-002", "target-003"]})
        mr1 = _make_mr(iid=1, rebased="target-001")
        mr2 = _make_mr(iid=2, rebased="target-001")
        state = _make_state(target_head="target-001", mrs=[mr1, mr2], sha_pools=pools)

        merge_mr(state, mr1)
        assert state.project.target_head == "target-002"

        merge_mr(state, mr2)
        assert state.project.target_head == "target-003"


class TestTick:
    def test_tick_pending_to_running(self):
        p = Pipeline(
            id=5001,
            status=PipelineStatus.PENDING,
            sha="sha-1",
            root_sha="target-001",
            pending_ticks_remaining=1,
            running_ticks_remaining=3,
        )
        mr = _make_mr(iid=1)
        mr.pipelines = [p]
        state = _make_state(mrs=[mr])

        event = tick(state)

        assert p.status == PipelineStatus.RUNNING
        assert event["tick"] == 1
        assert len(event["transitions"]) == 1
        assert event["transitions"][0]["to"] == "running"

    def test_tick_running_to_success(self):
        p = Pipeline(
            id=5001,
            status=PipelineStatus.RUNNING,
            sha="sha-1",
            root_sha="target-001",
            running_ticks_remaining=1,
            outcome=PipelineStatus.SUCCESS,
        )
        mr = _make_mr(iid=1)
        mr.pipelines = [p]
        state = _make_state(mrs=[mr])

        event = tick(state)

        assert p.status == PipelineStatus.SUCCESS
        assert event["transitions"][0]["to"] == "success"

    def test_tick_running_to_failed(self):
        p = Pipeline(
            id=5001,
            status=PipelineStatus.RUNNING,
            sha="sha-1",
            root_sha="target-001",
            running_ticks_remaining=1,
            outcome=PipelineStatus.FAILED,
        )
        mr = _make_mr(iid=1)
        mr.pipelines = [p]
        state = _make_state(mrs=[mr])

        tick(state)

        assert p.status == PipelineStatus.FAILED

    def test_tick_decrements_remaining(self):
        p = Pipeline(
            id=5001,
            status=PipelineStatus.RUNNING,
            sha="sha-1",
            root_sha="target-001",
            running_ticks_remaining=5,
        )
        mr = _make_mr(iid=1)
        mr.pipelines = [p]
        state = _make_state(mrs=[mr])

        tick(state)

        assert p.status == PipelineStatus.RUNNING
        assert p.running_ticks_remaining == 4


class TestCancelPipeline:
    def test_cancel_running(self):
        p = Pipeline(
            id=5001,
            status=PipelineStatus.RUNNING,
            sha="sha-1",
            root_sha="target-001",
        )
        mr = _make_mr(iid=1)
        mr.pipelines = [p]
        state = _make_state(mrs=[mr])

        event = cancel_pipeline(state, p, mr)

        assert p.status == PipelineStatus.CANCELED
        assert event["event"] == "pipeline_cancel"
        assert event["old_status"] == "running"
