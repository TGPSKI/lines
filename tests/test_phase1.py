"""Tests for Phase 1 multi-merge logic."""

from gitlab_hk_sim.phase1 import (
    Phase1Config,
    compute_overlap_blocked,
    execute_batch_merge,
    select_merge_batch,
)
from gitlab_hk_sim.state import (
    MergeRequest,
    MRState,
    Pipeline,
    PipelineStatus,
    Project,
    SHAPools,
    SimState,
)


def _make_state(mrs: list[MergeRequest]) -> SimState:
    project = Project(
        id=1001,
        name="sim-repo",
        path="sim-repo",
        path_with_namespace="app-sre/sim-repo",
        target_head="target-001",
    )
    return SimState(
        project=project,
        merge_requests=mrs,
        sha_pools=SHAPools(
            target_advances={
                "master": ["target-002", "target-003", "target-004", "target-005"]
            }
        ),
    )


def _make_success_mr(
    iid: int, tenant_domains: list[str], priority: int = 0
) -> MergeRequest:
    sha = f"mr{iid}-sha-001"
    pipeline = Pipeline(
        id=5000 + iid,
        status=PipelineStatus.SUCCESS,
        sha=sha,
        root_sha="target-001",
    )
    return MergeRequest(
        id=2000 + iid,
        iid=iid,
        title=f"MR {iid}",
        state=MRState.OPENED,
        sha=sha,
        rebased_target_sha="target-001",
        source_project_id=1001,
        target_project_id=1001,
        tenant_domains=tenant_domains,
        priority=priority,
        pipelines=[pipeline],
    )


class TestSelectMergeBatch:
    def test_disabled_returns_empty(self):
        config = Phase1Config(enabled=False)
        state = _make_state([_make_success_mr(1, ["a"])])
        assert select_merge_batch(state, config) == []

    def test_non_overlapping_all_selected(self):
        config = Phase1Config(
            enabled=True, merge_limit=5, conflict_key="tenant_domains"
        )
        mrs = [
            _make_success_mr(1, ["tenant-a"], priority=1),
            _make_success_mr(2, ["tenant-b"], priority=2),
            _make_success_mr(3, ["tenant-c"], priority=3),
        ]
        state = _make_state(mrs)
        batch = select_merge_batch(state, config)
        assert len(batch) == 3

    def test_overlap_blocks_second(self):
        config = Phase1Config(
            enabled=True, merge_limit=5, conflict_key="tenant_domains"
        )
        mrs = [
            _make_success_mr(1, ["tenant-a"], priority=1),
            _make_success_mr(2, ["tenant-a"], priority=2),
            _make_success_mr(3, ["tenant-b"], priority=3),
        ]
        state = _make_state(mrs)
        batch = select_merge_batch(state, config)
        assert len(batch) == 2
        assert batch[0].iid == 1
        assert batch[1].iid == 3

    def test_merge_limit_respected(self):
        config = Phase1Config(
            enabled=True, merge_limit=2, conflict_key="tenant_domains"
        )
        mrs = [
            _make_success_mr(1, ["a"], priority=1),
            _make_success_mr(2, ["b"], priority=2),
            _make_success_mr(3, ["c"], priority=3),
        ]
        state = _make_state(mrs)
        batch = select_merge_batch(state, config)
        assert len(batch) == 2

    def test_priority_ordering(self):
        config = Phase1Config(
            enabled=True, merge_limit=5, conflict_key="tenant_domains"
        )
        mrs = [
            _make_success_mr(1, ["a"], priority=10),
            _make_success_mr(2, ["b"], priority=1),
            _make_success_mr(3, ["c"], priority=5),
        ]
        state = _make_state(mrs)
        batch = select_merge_batch(state, config)
        assert batch[0].iid == 2
        assert batch[1].iid == 3
        assert batch[2].iid == 1


class TestExecuteBatchMerge:
    def test_batch_merge_advances_target(self):
        mrs = [
            _make_success_mr(1, ["a"], priority=1),
            _make_success_mr(2, ["b"], priority=2),
        ]
        state = _make_state(mrs)

        event = execute_batch_merge(state, mrs)

        assert event["batch_size"] == 2
        assert event["merged"] == [1, 2]
        assert mrs[0].state == MRState.MERGED
        assert mrs[1].state == MRState.MERGED

    def test_empty_batch(self):
        state = _make_state([])
        event = execute_batch_merge(state, [])
        assert event["batch_size"] == 0


class TestComputeOverlapBlocked:
    def test_overlap_counted(self):
        config = Phase1Config(
            enabled=True, merge_limit=5, conflict_key="tenant_domains"
        )
        mrs = [
            _make_success_mr(1, ["a"], priority=1),
            _make_success_mr(2, ["a"], priority=2),
            _make_success_mr(3, ["b"], priority=3),
            _make_success_mr(4, ["a"], priority=4),
        ]
        state = _make_state(mrs)
        blocked = compute_overlap_blocked(state, config)
        assert blocked == 2  # MR 2 and MR 4 blocked by MR 1's "a"
