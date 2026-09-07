"""Report generation – single-run summaries and multi-policy comparisons."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from glab_api.metrics import compute_summary, load_metrics

from .phase1 import compute_phase1_metrics


def api_call_counts(events: list[dict[str, Any]]) -> tuple[dict[str, int], int]:
    """Return (endpoint counts, total) from api_call events or their summary.

    scripts/migrate_reports.py replaces api_call events with a single
    api_call_summary carrying the same counts, so both shapes read alike.
    """
    counts: dict[str, int] = {}
    total = 0
    for event in events:
        if event.get("event") == "api_call":
            endpoint = event.get("endpoint", "unknown")
            counts[endpoint] = counts.get(endpoint, 0) + 1
            total += 1
    if total:
        return counts, total
    for event in events:
        if event.get("event") == "api_call_summary":
            summary = event.get("endpoints") or {}
            return dict(summary), int(event.get("total", sum(summary.values())))
    return {}, 0


def generate_single_report(
    metrics_path: str | Path,
    scenario_name: str = "",
    policy_name: str = "",
) -> str:
    """Generate a markdown single-run report from metrics NDJSON."""
    events = load_metrics(metrics_path)
    summary = compute_summary(events)
    phase1 = compute_phase1_metrics(events)

    lines: list[str] = []
    lines.append(f"# Simulation Report: {scenario_name or 'unnamed'}")
    lines.append("")
    if policy_name:
        lines.append(f"**Policy:** {policy_name}")
        lines.append("")

    lines.append("## API Call Summary")
    lines.append("")
    endpoint_counts, api_total = api_call_counts(events)
    lines.append("| Endpoint | Calls |")
    lines.append("|---|---:|")
    for ep, count in sorted(endpoint_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {ep} | {count} |")
    lines.append(f"| **Total** | **{api_total}** |")
    lines.append("")

    lines.append("## Rebase / Merge Summary")
    lines.append("")
    lines.append(f"- Rebase calls: {summary['rebase_calls']}")
    lines.append(f"- Merge calls: {summary['merge_calls']}")
    lines.append(f"- Pipeline cancels: {summary['pipeline_cancels']}")
    lines.append(f"- Pipelines created: {summary['pipelines_created']}")
    lines.append(
        f"- Duplicate rebases (total extra): {summary['duplicate_rebase_total']}"
    )
    lines.append("")

    lines.append("## Active Pipeline Pressure")
    lines.append("")
    lines.append(f"- Peak active pipelines: {summary['peak_active_pipelines']}")
    lines.append(f"- Total ticks: {summary['total_ticks']}")
    lines.append("")

    lines.append("## Same-Root Success Pool")
    lines.append("")
    lines.append(f"- P50: {summary['same_root_success_pool_p50']}")
    lines.append(f"- P95: {summary['same_root_success_pool_p95']}")
    lines.append(f"- Max: {summary['same_root_success_pool_max']}")
    lines.append("")

    lines.append("## Stale / Wasted CI")
    lines.append("")
    lines.append(f"- Max stale successes: {summary['stale_success_max']}")
    lines.append(
        f"- Stale successes from merges: {summary['total_stale_successes_from_merges']}"
    )
    lines.append("")

    if phase1.get("merge_batches_completed", 0) > 0:
        lines.append("## Phase 1 Batch Metrics")
        lines.append("")
        lines.append(f"- Merge batches completed: {phase1['merge_batches_completed']}")
        lines.append(f"- Average batch size: {phase1['average_batch_size']:.1f}")
        avg = phase1["average_mrs_per_target_advance"]
        lines.append(f"- Average MRs per target advance: {avg:.2f}")
        lines.append("")

    return "\n".join(lines)


def generate_comparison_report(
    runs: dict[str, str | Path],
) -> str:
    """Generate a markdown comparison report from multiple metrics files.

    Args:
        runs: Mapping of policy_name -> metrics NDJSON path.
    """
    summaries: dict[str, dict[str, Any]] = {}
    phase1_summaries: dict[str, dict[str, Any]] = {}

    for name, path in runs.items():
        events = load_metrics(path)
        summaries[name] = compute_summary(events)
        phase1_summaries[name] = compute_phase1_metrics(events)

    lines: list[str] = []
    lines.append("# Policy Comparison Report")
    lines.append("")

    # Main comparison table
    headers = [
        "policy",
        "API calls",
        "rebases",
        "pipelines created",
        "peak active",
        "same-root p95",
        "stale max",
        "dup rebases",
        "merges",
        "avg MRs/advance",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append(
        "|" + "|".join(["---:" if i > 0 else "---" for i in range(len(headers))]) + "|"
    )

    for name, s in summaries.items():
        p1 = phase1_summaries[name]
        _, api_total = api_call_counts(load_metrics(runs[name]))
        avg_advance = p1.get(
            "average_mrs_per_target_advance", s.get("average_mrs_per_target_advance", 0)
        )
        row = [
            name,
            str(api_total),
            str(s["rebase_calls"]),
            str(s["pipelines_created"]),
            str(s["peak_active_pipelines"]),
            str(s["same_root_success_pool_p95"]),
            str(s["stale_success_max"]),
            str(s["duplicate_rebase_total"]),
            str(s["mrs_merged"]),
            f"{avg_advance:.2f}",
        ]
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("_Fill in scenario-specific interpretation here._")
    lines.append("")

    return "\n".join(lines)
