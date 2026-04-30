"""CLI for the GitLab Housekeeping Policy Simulator."""

from __future__ import annotations

from pathlib import Path

import click


@click.group()
def cli() -> None:
    """GitLab Housekeeping Policy Simulator."""


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
    "--metrics-out",
    default=None,
    type=click.Path(),
    help="Path for NDJSON metrics output",
)
def serve(scenario: str, host: str, port: int, metrics_out: str | None) -> None:
    """Start the fake GitLab server loaded with a scenario."""
    import uvicorn

    from .server import create_app

    app = create_app(scenario_path=scenario, metrics_out=metrics_out)
    click.echo(f"Starting sim server on {host}:{port}")
    click.echo(f"Scenario: {scenario}")
    if metrics_out:
        click.echo(f"Metrics: {metrics_out}")
    click.echo("")
    click.echo("GitLab API at: http://{host}:{port}/api/v4/")
    click.echo("Sim control at: http://{host}:{port}/__sim/")
    uvicorn.run(app, host=host, port=port, log_level="info")


@cli.command()
@click.option(
    "--metrics",
    required=True,
    type=click.Path(exists=True),
    help="Path to metrics NDJSON",
)
@click.option(
    "--out", default=None, type=click.Path(), help="Output markdown file path"
)
@click.option("--scenario-name", default="", help="Scenario name for the report header")
@click.option("--policy-name", default="", help="Policy name for the report")
def report(metrics: str, out: str | None, scenario_name: str, policy_name: str) -> None:
    """Generate a single-run report from metrics NDJSON."""
    from .report import generate_single_report

    md = generate_single_report(
        metrics, scenario_name=scenario_name, policy_name=policy_name
    )

    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(md)
        click.echo(f"Report written to {out}")
    else:
        click.echo(md)


@cli.command()
@click.option(
    "--run",
    multiple=True,
    help="name=path pairs (e.g. --run old=reports/old/metrics.ndjson)",
)
@click.option(
    "--out", default=None, type=click.Path(), help="Output markdown file path"
)
def compare(run: tuple[str, ...], out: str | None) -> None:
    """Generate a comparison report from multiple metric runs."""
    from .report import generate_comparison_report

    runs: dict[str, str] = {}
    for r in run:
        if "=" not in r:
            click.echo(f"Error: --run must be name=path, got: {r}", err=True)
            raise SystemExit(1)
        name, path = r.split("=", 1)
        runs[name] = path

    if not runs:
        click.echo("Error: at least one --run required", err=True)
        raise SystemExit(1)

    md = generate_comparison_report(runs)

    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(md)
        click.echo(f"Comparison report written to {out}")
    else:
        click.echo(md)


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
