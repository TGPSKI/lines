"""FastAPI application factory for the fake GitLab server."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .endpoints import gitlab_router, sim_router
from .metrics import MetricsCollector
from .scenario import load_scenario


class NamespacedProjectPath:
    """Route requests that address the project by its namespaced path.

    python-gitlab sends `GET /api/v4/projects/example%2Fqueue-lab`, which real
    GitLab reads as one path segment. Starlette percent-decodes before routing,
    so the same request arrives here as two segments and matches no route.
    Collapse the project's own path back to its id, which is what every handler
    ignores anyway — the sim serves one project.
    """

    def __init__(self, app: object, project_path: str, project_id: int) -> None:
        self.app = app
        self.prefix = f"/api/v4/projects/{project_path}"
        self.replacement = f"/api/v4/projects/{project_id}"

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope["type"] == "http":
            path = scope["path"]
            if path == self.prefix or path.startswith(f"{self.prefix}/"):
                scope = dict(scope)
                scope["path"] = self.replacement + path[len(self.prefix) :]
        await self.app(scope, receive, send)  # type: ignore[operator]


def create_app(
    scenario_path: str | Path,
    metrics_out: str | Path | None = None,
) -> FastAPI:
    """Create a FastAPI app loaded with a scenario.

    Args:
        scenario_path: Path to scenario YAML file.
        metrics_out: Optional path to write NDJSON metrics.
    """
    app = FastAPI(
        title="GitLab HK Sim",
        description="Fake GitLab server for housekeeping policy simulation",
    )

    state = load_scenario(scenario_path)
    metrics = MetricsCollector()

    if metrics_out:
        metrics.open_file(metrics_out)

    app.state.sim_state = state
    app.state.metrics = metrics
    app.state.scenario_path = str(scenario_path)

    metrics.record_scenario_meta(state)

    app.add_middleware(
        NamespacedProjectPath,
        project_path=state.project.path_with_namespace,
        project_id=state.project.id,
    )

    app.include_router(gitlab_router)
    app.include_router(sim_router)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        metrics.close()

    return app
