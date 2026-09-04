"""CLI for mqsim: turn metrics NDJSON into reports."""

from __future__ import annotations

from pathlib import Path

import click


@click.group()
def cli() -> None:
    """mqsim — merge-queue policy simulator."""


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


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
