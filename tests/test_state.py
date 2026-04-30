"""Tests for the core state model."""

from gitlab_hk_sim.state import (
    MergeRequest,
    MRState,
    Pipeline,
    PipelineStatus,
    Project,
    SHAPools,
    SimState,
)


def _make_project(target_head: str = "target-001") -> Project:
    return Project(
        id=1001,
        name="sim-repo",
        path="sim-repo",
        path_with_namespace="app-sre/sim-repo",
        target_head=target_head,
    )


def _make_pipeline(
    pid: int = 5001,
    status: PipelineStatus = PipelineStatus.SUCCESS,
    sha: str = "mr1-sha-001",
    root_sha: str = "target-001",
) -> Pipeline:
    return Pipeline(id=pid, status=status, sha=sha, root_sha=root_sha)


def _make_mr(
    iid: int = 1,
    sha: str = "mr1-sha-001",
    rebased_target_sha: str = "target-001",
    pipelines: list[Pipeline] | None = None,
    state: MRState = MRState.OPENED,
    tenant_domains: list[str] | None = None,
) -> MergeRequest:
    return MergeRequest(
        id=2000 + iid,
        iid=iid,
        title=f"MR {iid}",
        state=state,
        sha=sha,
        rebased_target_sha=rebased_target_sha,
        source_project_id=1001,
        target_project_id=1001,
        pipelines=pipelines or [],
        tenant_domains=tenant_domains or [],
    )


class TestPipeline:
    def test_is_active_running(self):
        p = _make_pipeline(status=PipelineStatus.RUNNING)
        assert p.is_active

    def test_is_active_pending(self):
        p = _make_pipeline(status=PipelineStatus.PENDING)
        assert p.is_active

    def test_not_active_success(self):
        p = _make_pipeline(status=PipelineStatus.SUCCESS)
        assert not p.is_active

    def test_not_active_failed(self):
        p = _make_pipeline(status=PipelineStatus.FAILED)
        assert not p.is_active


class TestMergeRequest:
    def test_has_useful_success(self):
        p = _make_pipeline(sha="mr1-sha-001", root_sha="target-001")
        mr = _make_mr(sha="mr1-sha-001", rebased_target_sha="target-001", pipelines=[p])
        assert mr.has_useful_success("target-001")

    def test_no_useful_success_wrong_root(self):
        p = _make_pipeline(sha="mr1-sha-001", root_sha="target-000")
        mr = _make_mr(sha="mr1-sha-001", rebased_target_sha="target-001", pipelines=[p])
        assert not mr.has_useful_success("target-001")

    def test_no_useful_success_wrong_sha(self):
        p = _make_pipeline(sha="old-sha", root_sha="target-001")
        mr = _make_mr(sha="mr1-sha-001", rebased_target_sha="target-001", pipelines=[p])
        assert not mr.has_useful_success("target-001")

    def test_no_useful_success_not_rebased(self):
        p = _make_pipeline(sha="mr1-sha-001", root_sha="target-001")
        mr = _make_mr(sha="mr1-sha-001", rebased_target_sha="target-000", pipelines=[p])
        assert not mr.has_useful_success("target-001")

    def test_no_useful_success_closed(self):
        p = _make_pipeline(sha="mr1-sha-001", root_sha="target-001")
        mr = _make_mr(
            sha="mr1-sha-001",
            rebased_target_sha="target-001",
            pipelines=[p],
            state=MRState.MERGED,
        )
        assert not mr.has_useful_success("target-001")


class TestSHAPools:
    def test_next_mr_sha_from_pool(self):
        pools = SHAPools(mr_rebases={"1": ["sha-a", "sha-b"]})
        assert pools.next_mr_sha(1) == "sha-a"
        assert pools.next_mr_sha(1) == "sha-b"

    def test_next_mr_sha_fallback_synthetic(self):
        pools = SHAPools(mr_rebases={"1": ["sha-a"]})
        pools.next_mr_sha(1)  # consume pool
        synthetic = pools.next_mr_sha(1)
        assert synthetic.startswith("mr1-sha-")

    def test_next_target_sha_from_pool(self):
        pools = SHAPools(target_advances={"master": ["t-002", "t-003"]})
        assert pools.next_target_sha("master") == "t-002"
        assert pools.next_target_sha("master") == "t-003"

    def test_next_target_sha_fallback(self):
        pools = SHAPools(target_advances={})
        sha = pools.next_target_sha("master")
        assert sha.startswith("target-")


class TestSimState:
    def test_open_mrs(self):
        project = _make_project()
        mr_open = _make_mr(iid=1)
        mr_merged = _make_mr(iid=2, state=MRState.MERGED)
        state = SimState(project=project, merge_requests=[mr_open, mr_merged])
        assert len(state.open_mrs()) == 1
        assert state.open_mrs()[0].iid == 1

    def test_active_pipelines(self):
        project = _make_project()
        p_running = _make_pipeline(pid=1, status=PipelineStatus.RUNNING)
        p_success = _make_pipeline(pid=2, status=PipelineStatus.SUCCESS)
        mr = _make_mr(pipelines=[p_running, p_success])
        state = SimState(project=project, merge_requests=[mr])
        assert len(state.active_pipelines()) == 1

    def test_same_root_success_pool(self):
        project = _make_project(target_head="target-001")
        p = _make_pipeline(sha="mr1-sha-001", root_sha="target-001")
        mr = _make_mr(sha="mr1-sha-001", rebased_target_sha="target-001", pipelines=[p])
        state = SimState(project=project, merge_requests=[mr])
        assert len(state.same_root_success_pool()) == 1

    def test_stale_successes(self):
        project = _make_project(target_head="target-002")
        p = _make_pipeline(sha="mr1-sha-001", root_sha="target-001")
        mr = _make_mr(sha="mr1-sha-001", rebased_target_sha="target-001", pipelines=[p])
        state = SimState(project=project, merge_requests=[mr])
        assert len(state.stale_successes()) == 1

    def test_get_mr(self):
        project = _make_project()
        mr = _make_mr(iid=5)
        state = SimState(project=project, merge_requests=[mr])
        assert state.get_mr(5) is mr
        assert state.get_mr(99) is None
