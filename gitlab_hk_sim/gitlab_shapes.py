"""GitLab API response shapes – transforms internal state to GitLab-compatible JSON."""

from __future__ import annotations

from typing import Any

from .state import Commit, MergeRequest, Pipeline, Project


def project_shape(project: Project, base_url: str) -> dict[str, Any]:
    web_url = project.web_url or f"{base_url}/{project.path_with_namespace}"
    return {
        "id": project.id,
        "name": project.name,
        "path": project.path,
        "path_with_namespace": project.path_with_namespace,
        "web_url": web_url,
        "default_branch": project.default_branch,
        "squash_option": project.squash_option,
        "namespace": {
            "id": 1,
            "name": project.path_with_namespace.split("/")[0],
            "path": project.path_with_namespace.split("/")[0],
            "kind": "group",
            "full_path": project.path_with_namespace.rsplit("/", 1)[0],
        },
        "merge_method": "merge",
        "only_allow_merge_if_pipeline_succeeds": True,
        "ssh_url_to_repo": f"git@gitlab.example.com:{project.path_with_namespace}.git",
        "http_url_to_repo": f"{web_url}.git",
    }


def mr_shape(mr: MergeRequest, project: Project, base_url: str) -> dict[str, Any]:
    web_url = f"{base_url}/{project.path_with_namespace}/-/merge_requests/{mr.iid}"
    return {
        "id": mr.id,
        "iid": mr.iid,
        "title": mr.title,
        "state": mr.state.value,
        "draft": mr.draft,
        "merge_status": mr.merge_status,
        "detailed_merge_status": "mergeable"
        if mr.merge_status == "can_be_merged"
        else mr.merge_status,
        "target_branch": mr.target_branch,
        "source_branch": mr.source_branch,
        "source_project_id": mr.source_project_id,
        "target_project_id": mr.target_project_id,
        "sha": mr.sha,
        "diff_refs": {
            "base_sha": mr.rebased_target_sha,
            "head_sha": mr.sha,
            "start_sha": mr.rebased_target_sha,
        },
        "labels": mr.labels,
        "web_url": web_url,
        "work_in_progress": mr.draft,
        "merge_when_pipeline_succeeds": False,
        "squash": True,
        "has_conflicts": False,
        "blocking_discussions_resolved": True,
        "author": {"id": 100, "username": "sim-author", "name": "Sim Author"},
        "approved_at": mr.approved_at,
        "assignees": [],
        "reviewers": [],
        "pipeline": _pipeline_summary(mr.latest_pipeline())
        if mr.latest_pipeline()
        else None,
        "head_pipeline": _pipeline_summary(mr.latest_pipeline())
        if mr.latest_pipeline()
        else None,
    }


def mr_list_shape(
    mrs: list[MergeRequest], project: Project, base_url: str
) -> list[dict[str, Any]]:
    return [mr_shape(mr, project, base_url) for mr in mrs]


def pipeline_shape(pipeline: Pipeline) -> dict[str, Any]:
    return {
        "id": pipeline.id,
        "status": pipeline.status.value,
        "sha": pipeline.sha,
        "ref": "main",
        "web_url": f"http://example.com/pipelines/{pipeline.id}",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }


def pipeline_list_shape(pipelines: list[Pipeline]) -> list[dict[str, Any]]:
    return [pipeline_shape(p) for p in pipelines]


def _pipeline_summary(pipeline: Pipeline | None) -> dict[str, Any] | None:
    if pipeline is None:
        return None
    return {
        "id": pipeline.id,
        "status": pipeline.status.value,
        "sha": pipeline.sha,
    }


def commit_shape(commit: Commit) -> dict[str, Any]:
    return {
        "id": commit.id,
        "short_id": commit.short_id,
        "title": commit.title,
        "message": commit.message or commit.title,
        "author_name": commit.author_name,
        "author_email": commit.author_email,
        "created_at": "2024-01-01T00:00:00Z",
    }


def commit_list_shape(commits: list[Commit]) -> list[dict[str, Any]]:
    return [commit_shape(c) for c in commits]


def compare_shape(
    mr_sha: str, target_head: str, rebased_target_sha: str
) -> dict[str, Any]:
    """Repository compare response.

    Critical semantics:
    - commits == [] means MR is rebased onto target
    - commits != [] means MR needs rebase
    """
    if rebased_target_sha == target_head:
        return {"commits": [], "diffs": []}
    return {
        "commits": [
            {
                "id": f"behind-{target_head[:8]}",
                "short_id": f"behind-{target_head[:6]}",
                "title": "target advanced",
                "message": "target advanced beyond MR base",
            }
        ],
        "diffs": [{"old_path": "simulated", "new_path": "simulated"}],
    }


def label_event_shape(
    event_id: int,
    label_name: str,
    action: str = "add",
    created_at: str = "2024-01-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "label": {"id": event_id, "name": label_name},
        "action": action,
        "created_at": created_at,
        "user": {"username": "sim-bot"},
    }


def user_shape() -> dict[str, Any]:
    return {
        "id": 1,
        "username": "sim-bot",
        "name": "Sim Bot",
        "state": "active",
        "is_admin": False,
    }


def personal_access_token_shape() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "name": "sim-token",
            "active": True,
            "scopes": ["api"],
            "expires_at": "2099-12-31",
        }
    ]


def group_shape(group_id: int | str) -> dict[str, Any]:
    path = str(group_id) if isinstance(group_id, int) else group_id
    return {
        "id": int(group_id) if isinstance(group_id, int) else 1,
        "name": path,
        "path": path,
        "full_path": path,
        "web_url": f"http://example.com/groups/{path}",
    }


def group_members_shape() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "username": "sim-bot",
            "name": "Sim Bot",
            "state": "active",
            "access_level": 30,
        }
    ]
