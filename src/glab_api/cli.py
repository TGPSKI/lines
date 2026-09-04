"""CLI for glab_api: run the fake GitLab server and check scenarios."""

from __future__ import annotations

import ipaddress

import click


def _is_loopback_host(host: str) -> bool:
    """Return whether a bind host is limited to the local machine."""
    if host.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@click.group()
def cli() -> None:
    """glab_api — fake GitLab API server for merge-queue simulation."""


@cli.command()
@click.option(
    "--scenario",
    required=True,
    type=click.Path(exists=True),
    help="Path to scenario YAML",
)
@click.option("--host", default="127.0.0.1", help="Server host")
@click.option("--port", default=8080, type=int, help="Server port")
@click.option(
    "--allow-non-loopback",
    is_flag=True,
    help="Allow binding to a non-loopback interface (unsafe)",
)
@click.option(
    "--metrics-out",
    default=None,
    type=click.Path(),
    help="Path for NDJSON metrics output",
)
@click.option(
    "--seed",
    default=None,
    type=int,
    help="Random seed for deterministic pipeline failures/durations",
)
def serve(
    scenario: str,
    host: str,
    port: int,
    allow_non_loopback: bool,
    metrics_out: str | None,
    seed: int | None,
) -> None:
    """Start the fake GitLab server loaded with a scenario."""
    if not allow_non_loopback and not _is_loopback_host(host):
        raise click.UsageError(
            "--host must be localhost or a loopback IP;"
            " pass --allow-non-loopback to override"
        )

    import random

    import uvicorn

    from .server import create_app

    if seed is not None:
        random.seed(seed)
        click.echo(f"Random seed: {seed}")

    app = create_app(scenario_path=scenario, metrics_out=metrics_out)
    click.echo(f"Starting sim server on {host}:{port}")
    click.echo(f"Scenario: {scenario}")
    if metrics_out:
        click.echo(f"Metrics: {metrics_out}")
    click.echo("")
    click.echo(f"GitLab API at: http://{host}:{port}/api/v4/")
    click.echo(f"Sim control at: http://{host}:{port}/__sim/")
    uvicorn.run(app, host=host, port=port, log_level="info")


@cli.command()
@click.option(
    "--scenario",
    required=True,
    type=click.Path(exists=True),
    help="Path to scenario YAML",
)
def validate(scenario: str) -> None:
    """Validate a scenario YAML file."""
    from .scenario import load_scenario

    try:
        state = load_scenario(scenario)
        click.echo(f"Scenario valid: {scenario}")
        click.echo(f"  Project: {state.project.path_with_namespace}")
        click.echo(f"  Target head: {state.project.target_head}")
        click.echo(f"  MRs: {len(state.merge_requests)}")
        click.echo(f"  Open MRs: {len(state.open_mrs())}")
        total_pipelines = sum(len(mr.pipelines) for mr in state.merge_requests)
        click.echo(f"  Pipelines: {total_pipelines}")
    except Exception as e:
        click.echo(f"Scenario invalid: {e}", err=True)
        raise SystemExit(1) from e


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
