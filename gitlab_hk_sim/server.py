"""FastAPI application factory for the fake GitLab server."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .endpoints import gitlab_router, sim_router
from .metrics import MetricsCollector
from .scenario import load_scenario


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

    app.include_router(gitlab_router)
    app.include_router(sim_router)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        metrics.close()

    return app
