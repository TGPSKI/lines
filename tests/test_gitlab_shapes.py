"""Tests for GitLab API response shapes."""

from gitlab_hk_sim.gitlab_shapes import (
    compare_shape,
    mr_shape,
    pipeline_list_shape,
    pipeline_shape,
    project_shape,
    user_shape,
)
from gitlab_hk_sim.state import (
    MergeRequest,
    Pipeline,
    PipelineStatus,
    Project,
)


def _make_project() -> Project:
    return Project(
        id=1001,
        name="queue-lab",
        path="queue-lab",
        path_with_namespace="example/queue-lab",
        target_head="target-001",
    )


class TestProjectShape:
    def test_basic_fields(self):
        p = _make_project()
        result = project_shape(p, "http://localhost:8080")
        assert result["id"] == 1001
        assert result["name"] == "queue-lab"
        assert result["path_with_namespace"] == "example/queue-lab"
        assert "namespace" in result


class TestMRShape:
    def test_basic_fields(self):
        project = _make_project()
        mr = MergeRequest(
            id=2001,
            iid=1,
            title="Test MR",
            sha="sha-001",
            rebased_target_sha="target-001",
            labels=["lgtm"],
            source_project_id=1001,
            target_project_id=1001,
        )
        result = mr_shape(mr, project, "http://localhost:8080")
        assert result["iid"] == 1
        assert result["title"] == "Test MR"
        assert result["sha"] == "sha-001"
        assert result["labels"] == ["lgtm"]
        assert "diff_refs" in result

    def test_with_pipeline(self):
        project = _make_project()
        p = Pipeline(
            id=5001, status=PipelineStatus.SUCCESS, sha="sha-001", root_sha="target-001"
        )
        mr = MergeRequest(
            id=2001,
            iid=1,
            title="MR",
            sha="sha-001",
            source_project_id=1001,
            target_project_id=1001,
            pipelines=[p],
        )
        result = mr_shape(mr, project, "http://localhost:8080")
        assert result["head_pipeline"]["id"] == 5001
        assert result["head_pipeline"]["status"] == "success"

    def test_merge_commit_sha_none_when_open(self):
        project = _make_project()
        mr = MergeRequest(
            id=2001,
            iid=1,
            title="Open MR",
            sha="sha-001",
            source_project_id=1001,
            target_project_id=1001,
        )
        result = mr_shape(mr, project, "http://localhost:8080")
        assert result["merge_commit_sha"] is None

    def test_merge_commit_sha_present_when_merged(self):
        project = _make_project()
        from gitlab_hk_sim.state import MRState

        mr = MergeRequest(
            id=2001,
            iid=1,
            title="Merged MR",
            sha="sha-001",
            source_project_id=1001,
            target_project_id=1001,
            merge_commit_sha="target-002",
        )
        mr.state = MRState.MERGED
        result = mr_shape(mr, project, "http://localhost:8080")
        assert result["merge_commit_sha"] == "target-002"


class TestPipelineShape:
    def test_basic(self):
        p = Pipeline(
            id=5001, status=PipelineStatus.RUNNING, sha="sha-001", root_sha="target-001"
        )
        result = pipeline_shape(p)
        assert result["id"] == 5001
        assert result["status"] == "running"
        assert result["sha"] == "sha-001"

    def test_list_is_newest_first(self):
        older = Pipeline(
            id=5001, status=PipelineStatus.SUCCESS, sha="sha-001", root_sha="target-001"
        )
        newer = Pipeline(
            id=5002, status=PipelineStatus.RUNNING, sha="sha-002", root_sha="target-001"
        )
        result = pipeline_list_shape([older, newer])
        assert [p["id"] for p in result] == [5002, 5001]


class TestCompareShape:
    def test_rebased_returns_empty_commits(self):
        result = compare_shape("mr-sha", "target-001", "target-001")
        assert result["commits"] == []

    def test_not_rebased_returns_commits(self):
        result = compare_shape("mr-sha", "target-002", "target-001")
        assert len(result["commits"]) == 1

    def test_empty_rebased_target_returns_commits(self):
        result = compare_shape("mr-sha", "target-001", "")
        assert len(result["commits"]) == 1


class TestUserShape:
    def test_user(self):
        result = user_shape()
        assert result["username"] == "sim-bot"
        assert result["state"] == "active"
