#!/usr/bin/env python3
"""Run repeatable discrimination passes for arbitrary policy pairs.

The script:
1. Loads a calibrated base scenario.
2. Generates stress variants that should tease apart policy behavior.
3. Runs full compare simulations for each variant.
4. Emits CSV + Markdown summary with policy-agnostic deltas.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "run_standalone.py"

DEFAULT_BASE_SCENARIO = ROOT / "scenarios" / "synthetic-calibration-demo.yaml"
DEFAULT_POLICIES = "top-k,active-cap,old-burst"


@dataclass
class VariantSpec:
    slug: str
    title: str
    description: str
    scenario: dict[str, Any]
    scenario_path: Path | None = None


@dataclass
class VariantResult:
    variant: VariantSpec
    metrics: dict[str, dict[str, float | int | None]]
    raw_output_path: Path


def _policy_col_key(policy: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", policy.lower()).strip("_")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _status(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


def _parse_metadata_pairs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in pairs:
        if "=" not in raw:
            raise ValueError(f"invalid --metadata entry '{raw}', expected KEY=VALUE")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid --metadata entry '{raw}', key must not be empty")
        out[key] = value.strip()
    return out


def _write_metadata_file(
    *,
    out_dir: Path,
    args: argparse.Namespace,
    base_scenario: Path,
    policies: list[str],
    lhs_policy: str,
    rhs_policy: str,
    baseline_policy: str,
    rows: list[dict[str, Any]],
) -> None:
    now = datetime.now().astimezone()
    metadata = {
        "generated_at_iso": now.isoformat(),
        "generated_at_epoch": int(now.timestamp()),
        "run_category": "discrimination",
        "generator": Path(__file__).name,
        "custom_metadata": _parse_metadata_pairs(args.metadata),
        "context": {
            "base_scenario": base_scenario.name,
            "policies": policies,
            "lhs_policy": lhs_policy,
            "rhs_policy": rhs_policy,
            "baseline_policy": baseline_policy,
            "cycles": args.cycles,
            "limit": args.limit,
            "ticks_per_cycle": args.ticks_per_cycle,
            "variants": [r["variant"] for r in rows],
            "summary_csv": "discrimination-summary.csv",
            "summary_md": "discrimination-summary.md",
        },
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True)
    )


def _fmt_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    rem = seconds - mins * 60
    return f"{mins}m {rem:.1f}s"


def _run_capture(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"STDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
        )
    return proc.stdout


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _set_priority_label(mr: dict[str, Any], level: str) -> None:
    labels = list(mr.get("labels", []))
    labels = [label for label in labels if not label.startswith("bot/approved:")]
    labels.append(f"bot/approved: {level}")
    mr["labels"] = sorted(set(labels))


def _ensure_pipeline(
    mr: dict[str, Any],
    *,
    status: str,
    root_sha: str,
    pipeline_id: int,
    running_ticks: int | None = None,
) -> None:
    pipe: dict[str, Any] = {
        "id": pipeline_id,
        "status": status,
        "sha": mr["sha"],
        "root_sha": root_sha,
        "outcome": "success",
    }
    if running_ticks is not None:
        pipe["running_ticks_remaining"] = running_ticks
    mr["pipelines"] = [pipe]


def _variant_baseline(base: dict[str, Any]) -> VariantSpec:
    sc = copy.deepcopy(base)
    sc.setdefault("metadata", {})["discrimination_variant"] = "baseline"
    return VariantSpec(
        slug="baseline",
        title="Synthetic Baseline",
        description="Unmodified synthetic calibration demo scenario.",
        scenario=sc,
    )


def _variant_poisoned_head(base: dict[str, Any]) -> VariantSpec:
    sc = copy.deepcopy(base)
    mrs = sc.get("merge_requests", [])
    initial = sorted(
        [mr for mr in mrs if int(mr.get("arrival_tick", 0)) == 0],
        key=lambda x: int(x["iid"]),
    )
    target_head = sc.get("project", {}).get("target_head", "target-001")
    stale_head = "target-000"

    heavy = initial[:8]
    stale = initial[8:12]
    for i, mr in enumerate(heavy):
        mr["rebased_target_sha"] = stale_head
        _ensure_pipeline(
            mr,
            status="running",
            root_sha=stale_head,
            pipeline_id=9800 + i,
            running_ticks=max(10, int(mr.get("ci_duration", 8)) * 2),
        )
    for i, mr in enumerate(stale):
        mr["rebased_target_sha"] = stale_head
        _ensure_pipeline(
            mr,
            status="success",
            root_sha=stale_head,
            pipeline_id=9900 + i,
        )
    for mr in initial[:5]:
        _set_priority_label(mr, "critical")

    sc.setdefault("metadata", {})["discrimination_variant"] = "poisoned-head-window"
    sc["metadata"]["discrimination_notes"] = (
        "Front of queue polluted with stale/running high-priority MRs."
    )
    # keep failure realism mild to avoid overwhelming signal
    sc.setdefault("failure_path_realism", {})
    sc["failure_path_realism"]["merge_failure_rate"] = 0.003
    sc["failure_path_realism"]["rebase_failure_rate"] = 0.001
    sc.setdefault("project", {})["target_head"] = target_head
    return VariantSpec(
        slug="poisoned-head-window",
        title="Poisoned Head Window",
        description="High-priority stale/running MRs at queue head to test slot logic.",
        scenario=sc,
    )


def _variant_late_burst(base: dict[str, Any], cycles: int) -> VariantSpec:
    sc = copy.deepcopy(base)
    mrs = sorted(sc.get("merge_requests", []), key=lambda x: int(x["iid"]))
    arrivals = [mr for mr in mrs if int(mr.get("arrival_tick", 0)) > 0]
    if not arrivals:
        arrivals = mrs[-16:]
    burst = arrivals[:16]

    burst_start = max(1, int(cycles * 0.60))
    for i, mr in enumerate(burst):
        mr["arrival_tick"] = min(cycles - 1, burst_start + i * 3)
        _set_priority_label(mr, "critical" if i < 8 else "high")
        if i % 3 == 0:
            mr["ci_duration"] = max(9, int(mr.get("ci_duration", 8)) + 3)

    sc.setdefault("metadata", {})["discrimination_variant"] = "late-critical-burst"
    sc["metadata"]["discrimination_notes"] = (
        "Mid/late simulation burst of high-priority arrivals."
    )
    return VariantSpec(
        slug="late-critical-burst",
        title="Late Critical Burst",
        description="Burst of critical/high arrivals later in run.",
        scenario=sc,
    )


def _variant_high_variance_ci(base: dict[str, Any]) -> VariantSpec:
    sc = copy.deepcopy(base)
    mrs = sc.get("merge_requests", [])
    weights = {
        "3": 6,
        "4": 8,
        "5": 10,
        "6": 12,
        "10": 10,
        "12": 9,
        "14": 8,
        "16": 7,
    }
    sc["pipeline_durations"] = {
        "distribution": "weighted",
        "min_ticks": 3,
        "max_ticks": 16,
        "failure_rate": 0.015,
        "weights": weights,
    }
    for mr in mrs:
        iid = int(mr["iid"])
        if iid % 5 == 0:
            mr["ci_duration"] = 14
        elif iid % 5 == 1:
            mr["ci_duration"] = 4
        elif iid % 5 == 2:
            mr["ci_duration"] = 10
        # keep others unchanged

    sc.setdefault("metadata", {})["discrimination_variant"] = "high-variance-ci"
    sc["metadata"]["discrimination_notes"] = (
        "Higher CI duration variance and slight CI flakiness."
    )
    return VariantSpec(
        slug="high-variance-ci",
        title="High Variance CI",
        description="Wider CI duration distribution to stress active slot management.",
        scenario=sc,
    )


def _build_variants(base: dict[str, Any], cycles: int) -> list[VariantSpec]:
    return [
        _variant_baseline(base),
        _variant_poisoned_head(base),
        _variant_late_burst(base, cycles),
        _variant_high_variance_ci(base),
    ]


def _try_parse_number(text: str) -> float | int | None:
    t = text.strip()
    if not t or t == "N/A":
        return None
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return float(t)
    return None


def _parse_compare_table(
    output: str, policies: list[str]
) -> dict[str, dict[str, float | int | None]]:
    table: dict[str, dict[str, float | int | None]] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if not cols:
            continue
        metric = cols[0]
        if (
            metric == "Metric"
            or metric.startswith("---")
            or metric.startswith("──")
            or not metric
        ):
            continue
        vals = cols[1 : 1 + len(policies)]
        table[metric] = {
            policy: _try_parse_number(v)
            for policy, v in zip(policies, vals, strict=False)
        }
    return table


def _run_compare(
    *,
    scenario_path: Path,
    policies_csv: str,
    cycles: int,
    limit: int,
    ticks_per_cycle: int,
    port: int,
    retries: int,
) -> tuple[dict[str, dict[str, float | int | None]], str, int]:
    current_port = port
    policies = [p.strip() for p in policies_csv.split(",") if p.strip()]
    for attempt in range(1, retries + 1):
        _status(
            f"compare start attempt={attempt}/{retries}"
            f" port={current_port} scenario={scenario_path.name}"
        )
        cmd = [
            sys.executable,
            str(RUNNER),
            "--compare",
            "--policies",
            policies_csv,
            "--scenario",
            str(scenario_path),
            "--limit",
            str(limit),
            "--cycles",
            str(cycles),
            "--ticks-per-cycle",
            str(ticks_per_cycle),
            "--log-level",
            "WARNING",
            "--port",
            str(current_port),
        ]
        output = _run_capture(cmd, cwd=ROOT)
        table = _parse_compare_table(output, policies)
        if "MRs Merged" in table and all(
            table["MRs Merged"].get(p) is not None for p in policies
        ):
            return table, output, current_port + 1
        _status("compare output incomplete (likely transient N/A), retrying next port")
        current_port += 1
    raise RuntimeError(
        "unable to get complete compare output for"
        f" {scenario_path} after {retries} retries"
    )


def _metric_val(
    metrics: dict[str, dict[str, float | int | None]], metric: str, policy: str
) -> float:
    v = metrics.get(metric, {}).get(policy)
    if v is None:
        return 0.0
    return float(v)


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_md(
    path: Path,
    *,
    base_scenario: Path,
    policies: list[str],
    lhs_policy: str,
    rhs_policy: str,
    baseline_policy: str,
    rows: list[dict[str, Any]],
) -> None:
    throughput_headers = [f"{p} mph" for p in policies]
    lines = [
        "# Discrimination Pass Summary",
        "",
        f"- Base scenario: `{base_scenario}`",
        f"- Policies: `{', '.join(policies)}`",
        f"- Comparison pair: `{rhs_policy} - {lhs_policy}`",
        f"- Baseline context policy: `{baseline_policy or 'none'}`",
        "",
        "## Variant Results",
        "",
    ]
    header_cols = (
        ["Variant"]
        + throughput_headers
        + [
            f"{rhs_policy} - {lhs_policy} mph",
            f"{lhs_policy} rebases",
            f"{rhs_policy} rebases",
            "Rebase savings",
            f"{lhs_policy} starved",
            f"{rhs_policy} starved",
            "Raw compare",
        ]
    )
    lines.append("| " + " | ".join(header_cols) + " |")
    lines.append("|" + "|".join(["---"] + ["---:"] * (len(header_cols) - 1)) + "|")

    for r in rows:
        vals = [r["variant"]]
        for p in policies:
            vals.append(f"{float(r[f'throughput_mph__{_policy_col_key(p)}']):.3f}")
        vals.extend(
            [
                f"{float(r['rhs_minus_lhs_mph']):+.3f}",
                f"{float(r['lhs_rebases']):.0f}",
                f"{float(r['rhs_rebases']):.0f}",
                f"{float(r['rebase_savings']):+.0f}",
                f"{float(r['lhs_starved']):.0f}",
                f"{float(r['rhs_starved']):.0f}",
                r["raw_compare"],
            ]
        )
        lines.append("| " + " | ".join(vals) + " |")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            (
                f"- Positive `{rhs_policy} - {lhs_policy} mph` means `{rhs_policy}`"
                " has higher throughput."
            ),
            (
                "- Positive `Rebase savings` means the left-hand policy"
                f" (`{lhs_policy}`) performs more rebases than `{rhs_policy}`."
            ),
        ]
    )
    if baseline_policy:
        lines.append(
            f"- `{baseline_policy}` is included as contextual baseline throughput."
        )
    path.write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-scenario",
        default=str(DEFAULT_BASE_SCENARIO),
        help="Base calibrated scenario used to derive variants",
    )
    parser.add_argument(
        "--policies",
        default=DEFAULT_POLICIES,
        help="Comma-separated policies to compare",
    )
    parser.add_argument(
        "--lhs-policy",
        default="",
        help=(
            "Policy used as the left-hand side of delta metrics"
            " (default: first in --policies)"
        ),
    )
    parser.add_argument(
        "--rhs-policy",
        default="",
        help=(
            "Policy used as the right-hand side of delta metrics"
            " (default: second in --policies)"
        ),
    )
    parser.add_argument(
        "--baseline-policy",
        default="",
        help=(
            "Optional context baseline policy to surface in outputs"
            " (default: old-burst if present)"
        ),
    )
    parser.add_argument(
        "--cycles", type=int, default=480, help="Cycles per variant run"
    )
    parser.add_argument("--limit", type=int, default=2, help="Merge/rebase limit")
    parser.add_argument(
        "--ticks-per-cycle",
        type=int,
        default=1,
        help="Ticks per cycle",
    )
    parser.add_argument("--port-base", type=int, default=8200, help="Starting port")
    parser.add_argument(
        "--out-dir",
        default="",
        help="Output directory (default: reports/discrimination/<timestamp>)",
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="Retries per variant compare"
    )
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Arbitrary metadata key/value pairs to include in metadata.json",
    )
    return parser.parse_args()


def main() -> None:
    start = perf_counter()
    args = parse_args()
    base_scenario = Path(args.base_scenario).resolve()
    policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    if len(policies) < 2:
        raise ValueError("at least two policies are required for discrimination")
    lhs_policy = args.lhs_policy.strip() if args.lhs_policy else policies[0]
    rhs_policy = args.rhs_policy.strip() if args.rhs_policy else policies[1]
    if lhs_policy == rhs_policy:
        raise ValueError("lhs_policy and rhs_policy must be different")
    if lhs_policy not in policies or rhs_policy not in policies:
        raise ValueError("lhs_policy and rhs_policy must be present in --policies")
    baseline_policy = args.baseline_policy.strip() if args.baseline_policy else ""
    if not baseline_policy and "old-burst" in policies:
        baseline_policy = "old-burst"
    if baseline_policy and baseline_policy not in policies:
        raise ValueError("baseline_policy must be present in --policies")

    timestamp = datetime.now().strftime("%m-%d-%y_%I-%M-%S-%p")
    if args.out_dir:
        out_dir = Path(args.out_dir).resolve()
    else:
        out_dir = ROOT / "reports" / "discrimination" / timestamp
        if out_dir.exists():
            import uuid

            timestamp = f"{timestamp}_{uuid.uuid4().hex[:6]}"
            out_dir = ROOT / "reports" / "discrimination" / timestamp
    scenarios_dir = out_dir / "scenarios"
    raw_dir = out_dir / "raw"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    _status("=== Discrimination Pass Start ===")
    _status(f"base scenario: {base_scenario}")
    _status(f"policies: {policies}")
    _status(f"pair: rhs={rhs_policy} lhs={lhs_policy}")
    _status(f"baseline policy: {baseline_policy or 'none'}")
    _status(
        f"cycles={args.cycles} limit={args.limit}"
        f" ticks_per_cycle={args.ticks_per_cycle}"
    )
    _status(f"output dir: {out_dir}")

    base = _load_yaml(base_scenario)
    variants = _build_variants(base, args.cycles)
    _status(f"generated {len(variants)} variants")

    for v in variants:
        v.scenario_path = scenarios_dir / f"{v.slug}.yaml"
        _dump_yaml(v.scenario_path, v.scenario)

    results: list[VariantResult] = []
    port = args.port_base
    for idx, variant in enumerate(variants, start=1):
        var_start = perf_counter()
        _status(
            f"[{idx}/{len(variants)}] variant start: {variant.slug} - {variant.title}"
        )
        _status(f"[{idx}] scenario file: {variant.scenario_path}")
        table, output, port = _run_compare(
            scenario_path=variant.scenario_path or base_scenario,
            policies_csv=args.policies,
            cycles=args.cycles,
            limit=args.limit,
            ticks_per_cycle=args.ticks_per_cycle,
            port=port,
            retries=args.retries,
        )
        raw_path = raw_dir / f"{variant.slug}-compare.txt"
        raw_path.write_text(output)
        results.append(
            VariantResult(variant=variant, metrics=table, raw_output_path=raw_path)
        )

        lhs_mph = _metric_val(table, "Throughput (merges/hour)", lhs_policy)
        rhs_mph = _metric_val(table, "Throughput (merges/hour)", rhs_policy)
        lhs_rebases = _metric_val(table, "Rebases", lhs_policy)
        rhs_rebases = _metric_val(table, "Rebases", rhs_policy)
        delta_mph = rhs_mph - lhs_mph
        rebase_savings = lhs_rebases - rhs_rebases
        _status(
            f"[{idx}] done: {rhs_policy}-{lhs_policy}"
            f" mph delta={delta_mph:+.3f},"
            f" rebase savings={rebase_savings:+.0f},"
            f" elapsed={_fmt_seconds(perf_counter() - var_start)}"
        )

    rows: list[dict[str, Any]] = []
    for r in results:
        m = r.metrics
        row = {
            "variant": r.variant.slug,
            "title": r.variant.title,
            "description": r.variant.description,
            "policies_csv": ",".join(policies),
            "lhs_policy": lhs_policy,
            "rhs_policy": rhs_policy,
            "baseline_policy": baseline_policy,
            "lhs_throughput_mph": _metric_val(
                m, "Throughput (merges/hour)", lhs_policy
            ),
            "rhs_throughput_mph": _metric_val(
                m, "Throughput (merges/hour)", rhs_policy
            ),
            "rhs_minus_lhs_mph": _metric_val(m, "Throughput (merges/hour)", rhs_policy)
            - _metric_val(m, "Throughput (merges/hour)", lhs_policy),
            "lhs_rebases": _metric_val(m, "Rebases", lhs_policy),
            "rhs_rebases": _metric_val(m, "Rebases", rhs_policy),
            "rebase_savings": _metric_val(m, "Rebases", lhs_policy)
            - _metric_val(m, "Rebases", rhs_policy),
            "lhs_starved": _metric_val(m, "Starved MRs (>100 ticks)", lhs_policy),
            "rhs_starved": _metric_val(m, "Starved MRs (>100 ticks)", rhs_policy),
            "baseline_throughput_mph": _metric_val(
                m, "Throughput (merges/hour)", baseline_policy
            )
            if baseline_policy
            else 0.0,
            "raw_compare": str(r.raw_output_path.relative_to(out_dir)),
        }
        policy_metrics: dict[str, dict[str, float]] = {}
        for policy in policies:
            k = _policy_col_key(policy)
            throughput_mph = _metric_val(m, "Throughput (merges/hour)", policy)
            throughput_active_mph = _metric_val(
                m,
                "Throughput Active (merges/hour)",
                policy,
            )
            throughput_peak8_mph = _metric_val(
                m,
                "Throughput Peak8 (merges/hour)",
                policy,
            )
            throughput_peak8_p90_mph = _metric_val(
                m,
                "Throughput Peak8 p90 (merges/hour)",
                policy,
            )
            throughput_peak_window_mph = _metric_val(
                m,
                "Throughput Peak Window (merges/hour)",
                policy,
            )
            throughput_offpeak_mph = _metric_val(
                m,
                "Throughput Offpeak (merges/hour)",
                policy,
            )
            peak_offpeak_ratio = _metric_val(
                m,
                "Peak/Offpeak Throughput Ratio",
                policy,
            )
            modeled_hours = _metric_val(m, "Modeled Window (hours)", policy)
            total_arrivals = _metric_val(m, "Total Arrivals", policy)
            merged = _metric_val(m, "MRs Merged", policy)
            rebases = _metric_val(m, "Rebases", policy)
            starved = _metric_val(m, "Starved MRs (>100 ticks)", policy)
            row[f"throughput_mph__{k}"] = throughput_mph
            row[f"throughput_active_mph__{k}"] = throughput_active_mph
            row[f"throughput_peak8_mph__{k}"] = throughput_peak8_mph
            row[f"throughput_peak8_p90_mph__{k}"] = throughput_peak8_p90_mph
            row[f"throughput_peak_window_mph__{k}"] = throughput_peak_window_mph
            row[f"throughput_offpeak_mph__{k}"] = throughput_offpeak_mph
            row[f"peak_offpeak_ratio__{k}"] = peak_offpeak_ratio
            row[f"modeled_hours__{k}"] = modeled_hours
            row[f"total_arrivals__{k}"] = total_arrivals
            row[f"merged__{k}"] = merged
            row[f"rebases__{k}"] = rebases
            row[f"starved__{k}"] = starved
            policy_metrics[policy] = {
                "throughput_mph": throughput_mph,
                "throughput_active_mph": throughput_active_mph,
                "throughput_peak8_mph": throughput_peak8_mph,
                "throughput_peak8_p90_mph": throughput_peak8_p90_mph,
                "throughput_peak_window_mph": throughput_peak_window_mph,
                "throughput_offpeak_mph": throughput_offpeak_mph,
                "peak_offpeak_ratio": peak_offpeak_ratio,
                "modeled_hours": modeled_hours,
                "total_arrivals": total_arrivals,
                "merged": merged,
                "rebases": rebases,
                "starved": starved,
            }
        row["policy_metrics_json"] = json.dumps(policy_metrics, sort_keys=True)
        rows.append(row)

    headers = [
        "variant",
        "title",
        "description",
        "policies_csv",
        "lhs_policy",
        "rhs_policy",
        "baseline_policy",
        "lhs_throughput_mph",
        "rhs_throughput_mph",
        "rhs_minus_lhs_mph",
        "lhs_rebases",
        "rhs_rebases",
        "rebase_savings",
        "lhs_starved",
        "rhs_starved",
        "baseline_throughput_mph",
    ]
    for policy in policies:
        k = _policy_col_key(policy)
        headers.extend(
            [
                f"throughput_mph__{k}",
                f"throughput_active_mph__{k}",
                f"throughput_peak8_mph__{k}",
                f"throughput_peak8_p90_mph__{k}",
                f"throughput_peak_window_mph__{k}",
                f"throughput_offpeak_mph__{k}",
                f"peak_offpeak_ratio__{k}",
                f"modeled_hours__{k}",
                f"total_arrivals__{k}",
                f"merged__{k}",
                f"rebases__{k}",
                f"starved__{k}",
            ]
        )
    headers.append("policy_metrics_json")
    headers.append("raw_compare")

    csv_path = out_dir / "discrimination-summary.csv"
    md_path = out_dir / "discrimination-summary.md"
    _write_csv(
        csv_path,
        rows=rows,
        headers=headers,
    )
    _write_summary_md(
        md_path,
        base_scenario=base_scenario,
        policies=policies,
        lhs_policy=lhs_policy,
        rhs_policy=rhs_policy,
        baseline_policy=baseline_policy,
        rows=rows,
    )
    _write_metadata_file(
        out_dir=out_dir,
        args=args,
        base_scenario=base_scenario,
        policies=policies,
        lhs_policy=lhs_policy,
        rhs_policy=rhs_policy,
        baseline_policy=baseline_policy,
        rows=rows,
    )

    _status("=== Discrimination Pass Complete ===")
    _status(f"summary csv: {csv_path}")
    _status(f"summary md:  {md_path}")
    _status(f"elapsed={_fmt_seconds(perf_counter() - start)}")


if __name__ == "__main__":
    main()
