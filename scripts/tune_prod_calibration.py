#!/usr/bin/env python3
"""Tune calibration knobs against user-supplied multidimensional targets.

The tuner performs a grid search and then validates the best candidates.
Cycle lengths can be fixed or adapt to input log scale for better
speed/accuracy balance.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

import yaml
from calibrate_from_housekeeping_logs import (
    LogStats,
    build_scenario_dict,
    compute_weighted_performance_dimensions,
    parse_log,
    weighted_avg,
)

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "run_standalone.py"

TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<label>[^|]+?)\s*\|\s*(?P<value>[^|]+?)\s*\|$",
    re.MULTILINE,
)
LEGACY_STANDARD_DIMENSION_WEIGHTS = {
    "merge_per_hour_24h": 0.30,
    "merge_per_hour_active_hours": 0.20,
    "merge_per_hour_peak8": 0.20,
    "merge_per_hour_peak8_p90": 0.15,
    "rebase_per_merge_global": 0.10,
    "merge_interval_seconds_p95": 0.05,
}
HOURLY_EXTENDED_DIMENSION_WEIGHTS = {
    "merge_per_hour_24h": 0.10,
    "merge_per_hour_active_hours": 0.20,
    "merge_per_hour_peak8": 0.20,
    "merge_per_hour_peak8_p90": 0.15,
    "merge_peak_offpeak_ratio": 0.17,
    "rebase_per_merge_global": 0.12,
    "merge_interval_seconds_p50": 0.01,
    "merge_interval_seconds_p95": 0.05,
}
DIMENSION_WEIGHT_PROFILES = {
    "legacy": LEGACY_STANDARD_DIMENSION_WEIGHTS,
    "hourly-extended": HOURLY_EXTENDED_DIMENSION_WEIGHTS,
}
DEFAULT_DIMENSION_PROFILE = "hourly-extended"
TARGET_ANCHOR_TO_DIMENSION = {
    "window24h": "merge_per_hour_24h",
    "active": "merge_per_hour_active_hours",
    "peak8": "merge_per_hour_peak8",
    "peak8_p90": "merge_per_hour_peak8_p90",
}
DEFAULT_TARGET_ANCHOR_BY_PROFILE = {
    "legacy": "window24h",
    "hourly-extended": "active",
}
TARGET_TO_RUN_METRIC = {
    "merge_per_hour_24h": "throughput_mph",
    "merge_per_hour_active_hours": "throughput_active_mph",
    "merge_per_hour_peak8": "throughput_peak8_mph",
    "merge_per_hour_peak8_p90": "throughput_peak8_p90_mph",
    "merge_peak_offpeak_ratio": "peak_offpeak_ratio",
    "rebase_per_merge_global": "rebase_per_merge",
    "merge_interval_seconds_p50": "merge_interval_p50_seconds",
    "merge_interval_seconds_p95": "merge_interval_p95_seconds",
}


EXTENDED_SCORE_COMPONENT_WEIGHTS = {
    "standard_score": 0.70,
    "max_dimension_error": 0.20,
    "throughput_rel_error": 0.10,
}


@dataclass
class CandidateResult:
    tick_seconds: int
    arrival_skew: float
    queue_depth_scale: float
    throughput_mph: float
    throughput_active_mph: float
    throughput_peak8_mph: float
    throughput_peak8_p90_mph: float
    throughput_peak_window_mph: float
    throughput_offpeak_mph: float
    peak_offpeak_ratio: float
    modeled_hours: float
    total_arrivals: float
    rebase_per_merge: float
    merge_interval_p50_seconds: float
    merge_interval_p95_seconds: float
    merged: int
    rel_error: float
    standard_score: float
    extended_score: float
    max_dimension_error: float
    score: float
    dimension_errors: dict[str, float]
    scenario_path: Path


@dataclass
class ValidationResult:
    candidate: CandidateResult
    throughput_mph: float
    throughput_active_mph: float
    throughput_peak8_mph: float
    throughput_peak8_p90_mph: float
    throughput_peak_window_mph: float
    throughput_offpeak_mph: float
    peak_offpeak_ratio: float
    modeled_hours: float
    total_arrivals: float
    rebase_per_merge: float
    merge_interval_p50_seconds: float
    merge_interval_p95_seconds: float
    merged: int
    rel_error: float
    standard_score: float
    extended_score: float
    max_dimension_error: float
    score: float
    dimension_errors: dict[str, float]


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


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _status(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


def _phase(title: str) -> None:
    print()
    _status(f"=== {title} ===")


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
        cmd_str = " ".join(cmd)
        raise RuntimeError(
            f"command failed ({proc.returncode}): {cmd_str}\n"
            f"{proc.stderr}\n{proc.stdout}"
        )
    return proc.stdout


def _parse_table_metrics(output: str) -> dict[str, float]:
    rows: dict[str, float] = {}
    for match in TABLE_ROW_RE.finditer(output):
        label = match.group("label").strip()
        value_raw = match.group("value").strip()
        if not label or not value_raw:
            continue
        try:
            rows[label] = float(value_raw)
        except ValueError:
            continue
    return rows


def _extract_run_metrics(output: str) -> dict[str, float] | None:
    # run_standalone can return "N/A" table rows if a policy server failed to start;
    # treat as a retriable run instead of a hard parse error.
    if "N/A" in output:
        return None
    table = _parse_table_metrics(output)
    required = [
        "Throughput (merges/hour)",
        "Throughput Active (merges/hour)",
        "Throughput Peak8 (merges/hour)",
        "Throughput Peak8 p90 (merges/hour)",
        "MRs Merged",
        "Rebase/Merge Ratio",
        "Merge Interval p95 (seconds)",
    ]
    missing = [k for k in required if k not in table]
    if missing:
        raise RuntimeError(
            "unable to parse expected metrics from comparison output:"
            f" missing={missing}\n{output[-1800:]}"
        )
    return {
        "throughput_mph": table["Throughput (merges/hour)"],
        "throughput_active_mph": table["Throughput Active (merges/hour)"],
        "throughput_peak8_mph": table["Throughput Peak8 (merges/hour)"],
        "throughput_peak8_p90_mph": table["Throughput Peak8 p90 (merges/hour)"],
        "throughput_peak_window_mph": table.get(
            "Throughput Peak Window (merges/hour)",
            0.0,
        ),
        "throughput_offpeak_mph": table.get(
            "Throughput Offpeak (merges/hour)",
            0.0,
        ),
        "peak_offpeak_ratio": table.get("Peak/Offpeak Throughput Ratio", 0.0),
        "modeled_hours": table.get("Modeled Window (hours)", 0.0),
        "total_arrivals": table.get("Total Arrivals", 0.0),
        "merged": table["MRs Merged"],
        "rebase_per_merge": table["Rebase/Merge Ratio"],
        "merge_interval_p50_seconds": table.get("Merge Interval p50 (seconds)", 0.0),
        "merge_interval_p95_seconds": table["Merge Interval p95 (seconds)"],
    }


def _score_metrics(
    *,
    run_metrics: dict[str, float],
    target_dimensions: dict[str, float],
    dimension_weights: dict[str, float],
) -> tuple[float, float, dict[str, float]]:
    weighted_error_sum = 0.0
    active_weight = 0.0
    dim_errors: dict[str, float] = {}
    for dim_name, weight in dimension_weights.items():
        target = target_dimensions.get(dim_name, 0.0)
        if target <= 0:
            continue
        run_metric_name = TARGET_TO_RUN_METRIC.get(dim_name)
        if not run_metric_name:
            continue
        actual = run_metrics.get(run_metric_name, 0.0)
        rel = abs(actual - target) / target
        dim_errors[dim_name] = rel
        weighted_error_sum += rel * weight
        active_weight += weight
    if active_weight <= 0:
        return 0.0, 0.0, dim_errors
    standard_score = weighted_error_sum / active_weight
    max_dimension_error = max(dim_errors.values(), default=0.0)
    return standard_score, max_dimension_error, dim_errors


def _extended_score(*, standard_score: float, max_dimension_error: float, rel_error: float) -> float:
    return (
        standard_score * EXTENDED_SCORE_COMPONENT_WEIGHTS["standard_score"]
        + max_dimension_error * EXTENDED_SCORE_COMPONENT_WEIGHTS["max_dimension_error"]
        + rel_error * EXTENDED_SCORE_COMPONENT_WEIGHTS["throughput_rel_error"]
    )


def _score_for_rank(
    *,
    rank_score: str,
    rel_error: float,
    standard_score: float,
    extended_score: float,
) -> float:
    if rank_score == "throughput":
        return rel_error
    if rank_score == "standard":
        return standard_score
    return extended_score


def _evaluate_gate_results(
    *,
    rel_error: float,
    ranking_score: float,
    max_dimension_error: float,
    throughput_tolerance: float,
    score_tolerance: float,
    max_dimension_tolerance: float,
) -> dict[str, bool]:
    throughput_pass = rel_error <= throughput_tolerance
    score_pass = ranking_score <= score_tolerance
    max_dimension_pass = max_dimension_error <= max_dimension_tolerance
    accepted = throughput_pass and score_pass and max_dimension_pass
    return {
        "throughput_pass": throughput_pass,
        "score_pass": score_pass,
        "max_dimension_pass": max_dimension_pass,
        "accepted": accepted,
    }


def _decision_reason_codes(gate_results: dict[str, bool]) -> list[str]:
    reasons: list[str] = []
    if not gate_results.get("throughput_pass", False):
        reasons.append("throughput_gate_failed")
    if not gate_results.get("score_pass", False):
        reasons.append("rank_score_gate_failed")
    if not gate_results.get("max_dimension_pass", False):
        reasons.append("max_dimension_gate_failed")
    if not reasons:
        reasons.append("accepted")
    return reasons


def _format_dim_errors(dim_errors: dict[str, float]) -> str:
    if not dim_errors:
        return "none"
    ordered = sorted(dim_errors.items(), key=lambda kv: kv[1], reverse=True)
    return ", ".join(f"{name}={err*100:.1f}%" for name, err in ordered)


def _run_policy_with_retry(
    *,
    policy: str,
    scenario_path: Path,
    limit: int,
    cycles: int,
    ticks_per_cycle: int,
    port: int,
    retries: int,
) -> tuple[dict[str, float], int]:
    current_port = port
    for attempt in range(1, retries + 1):
        _status(
            f"{policy} run start: cycles={cycles}, port={current_port},"
            f" attempt={attempt}/{retries}, scenario={scenario_path.name}"
        )
        run_start = perf_counter()
        run_out = _run_capture(
            [
                sys.executable,
                str(RUNNER),
                "--compare",
                "--policies",
                policy,
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
            ],
            cwd=ROOT,
        )
        parsed = _extract_run_metrics(run_out)
        if parsed is not None:
            elapsed = perf_counter() - run_start
            _status(
                f"{policy} run done: mph={parsed['throughput_mph']:.3f},"
                f" merged={int(parsed['merged'])},"
                f" elapsed={_fmt_seconds(elapsed)}"
            )
            return parsed, current_port + 1
        elapsed = perf_counter() - run_start
        _status(
            f"{policy} run returned N/A metrics; retrying with next port "
            f"(elapsed={_fmt_seconds(elapsed)})"
        )
        current_port += 1
    raise RuntimeError(
        f"{policy} output stayed N/A after {retries} retries"
        f" (last port={current_port})"
    )


def _derive_tick_candidates(stats: list[LogStats]) -> list[int]:
    by_hours = [s.window_hours for s in stats]
    weighted = weighted_avg([s.avg_cycle_seconds for s in stats], by_hours)
    mean = sum(s.avg_cycle_seconds for s in stats) / len(stats)
    sorted_vals = sorted(s.avg_cycle_seconds for s in stats)
    median = sorted_vals[len(sorted_vals) // 2]
    cands = sorted(
        {
            max(55, int(round(weighted))),
            max(55, int(round(mean))),
            max(55, int(round(median))),
        }
    )
    return cands


def _build_target_dimensions(stats: list[LogStats]) -> dict[str, float]:
    return compute_weighted_performance_dimensions(stats)


def _resolve_target_anchor(dimension_profile: str, requested_anchor: str) -> str:
    if requested_anchor != "auto":
        return requested_anchor
    return DEFAULT_TARGET_ANCHOR_BY_PROFILE.get(dimension_profile, "window24h")


def _hours_to_cycles(
    *,
    hours: float,
    tick_seconds: int,
    ticks_per_cycle: int,
    resolution_minutes: int,
) -> int:
    cycle_seconds = max(1, tick_seconds * ticks_per_cycle)
    raw_cycles = max(1, int(round((hours * 3600.0) / cycle_seconds)))
    bucket_cycles = max(1, int(round((resolution_minutes * 60.0) / cycle_seconds)))
    return max(bucket_cycles, ((raw_cycles + bucket_cycles - 1) // bucket_cycles) * bucket_cycles)


def _resolve_cycle_plan(
    *,
    args: argparse.Namespace,
    stats: list[LogStats],
    tick_candidates: list[int],
    target_mph: float,
) -> tuple[int, int, int]:
    if args.cycle_scaling == "fixed":
        scenario_ticks = (
            args.scenario_window_ticks
            if args.scenario_window_ticks > 0
            else max(480, args.validate_cycles)
        )
        return args.tune_cycles, args.validate_cycles, scenario_ticks

    weighted_window_hours = weighted_avg(
        [s.window_hours for s in stats],
        [s.window_hours for s in stats],
    )
    base_tick = int(round(median(tick_candidates)))
    tune_hours = max(
        args.min_tune_hours,
        min(args.max_tune_hours, weighted_window_hours * args.tune_window_fraction),
    )
    validate_hours = max(
        args.min_validate_hours,
        min(args.max_validate_hours, weighted_window_hours * args.validate_window_fraction),
    )
    validate_hours = max(validate_hours, tune_hours * 1.5)

    tune_cycles = _hours_to_cycles(
        hours=tune_hours,
        tick_seconds=base_tick,
        ticks_per_cycle=args.ticks_per_cycle,
        resolution_minutes=args.resolution_minutes,
    )
    validate_cycles = _hours_to_cycles(
        hours=validate_hours,
        tick_seconds=base_tick,
        ticks_per_cycle=args.ticks_per_cycle,
        resolution_minutes=args.resolution_minutes,
    )

    if target_mph > 0:
        min_hours_for_tune_merges = args.min_tune_expected_merges / target_mph
        min_hours_for_validate_merges = args.min_validate_expected_merges / target_mph
        tune_cycles = max(
            tune_cycles,
            _hours_to_cycles(
                hours=min_hours_for_tune_merges,
                tick_seconds=base_tick,
                ticks_per_cycle=args.ticks_per_cycle,
                resolution_minutes=args.resolution_minutes,
            ),
        )
        validate_cycles = max(
            validate_cycles,
            _hours_to_cycles(
                hours=min_hours_for_validate_merges,
                tick_seconds=base_tick,
                ticks_per_cycle=args.ticks_per_cycle,
                resolution_minutes=args.resolution_minutes,
            ),
        )

    tune_cycles = max(args.min_tune_cycles, min(args.max_tune_cycles, tune_cycles))
    validate_cycles = max(
        args.min_validate_cycles,
        min(args.max_validate_cycles, validate_cycles),
    )
    validate_cycles = max(validate_cycles, tune_cycles)
    scenario_ticks = (
        args.scenario_window_ticks
        if args.scenario_window_ticks > 0
        else max(480, validate_cycles)
    )
    if scenario_ticks > 0:
        min_cycles_for_scenario = scenario_ticks * args.ticks_per_cycle
        tune_cycles = max(tune_cycles, min_cycles_for_scenario // 2)
        validate_cycles = max(validate_cycles, min_cycles_for_scenario)
    return tune_cycles, validate_cycles, scenario_ticks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--logs",
        nargs="+",
        required=True,
        help="Housekeeping log files",
    )
    parser.add_argument("--project", required=True, help="Project filter")
    parser.add_argument(
        "--target-mph",
        type=float,
        default=None,
        help="Target policy merges/hour; overrides metric-derived anchor",
    )
    parser.add_argument(
        "--target-anchor",
        choices=["auto", *sorted(TARGET_ANCHOR_TO_DIMENSION.keys())],
        default="auto",
        help=(
            "Dimension used to derive target mph when --target-mph is unset"
            " (auto: profile-specific default)"
        ),
    )
    parser.add_argument(
        "--policy",
        default="old-burst",
        help="Policy to calibrate against (default: old-burst)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Scenario generation seed")
    parser.add_argument(
        "--scenario-out",
        default="",
        help=(
            "Path for winning calibrated scenario"
            " (default: <run-output>/calibrated-scenario-<policy>.yaml)"
        ),
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help="Run output directory (default: reports/calibration/<timestamp>)",
    )
    parser.add_argument(
        "--tune-cycles",
        type=int,
        default=240,
        help="Cycles for tuning runs (used directly when --cycle-scaling=fixed)",
    )
    parser.add_argument(
        "--validate-cycles",
        type=int,
        default=480,
        help="Cycles for validation runs (used directly when --cycle-scaling=fixed)",
    )
    parser.add_argument("--limit", type=int, default=2, help="Merge/rebase limit")
    parser.add_argument(
        "--ticks-per-cycle",
        type=int,
        default=1,
        help="Ticks per cycle",
    )
    parser.add_argument("--port-base", type=int, default=8120, help="Starting port")
    parser.add_argument(
        "--tolerance-pct",
        type=float,
        default=5.0,
        help="Target throughput absolute relative error percent",
    )
    parser.add_argument(
        "--score-tolerance-pct",
        type=float,
        default=30.0,
        help="Target ranking-score percent threshold",
    )
    parser.add_argument(
        "--max-dimension-error-pct",
        type=float,
        default=90.0,
        help="Maximum allowed single-dimension absolute relative error percent",
    )
    parser.add_argument(
        "--validate-top-n",
        type=int,
        default=3,
        help="Validate this many best 240-cycle candidates at 480 cycles",
    )
    parser.add_argument(
        "--early-stop",
        action="store_true",
        help="Stop grid search early when throughput + score tolerances are met",
    )
    parser.add_argument(
        "--rank-score",
        choices=["extended", "standard", "throughput"],
        default="extended",
        help="Score objective used to rank candidates",
    )
    parser.add_argument(
        "--dimension-profile",
        choices=sorted(DIMENSION_WEIGHT_PROFILES.keys()),
        default=DEFAULT_DIMENSION_PROFILE,
        help=(
            "Target-dimension weighting profile used for score calculation"
            " (hourly-extended emphasizes intra-day shape over pure 24h mean)"
        ),
    )
    parser.add_argument(
        "--cycle-scaling",
        choices=["adaptive", "fixed"],
        default="adaptive",
        help="Adaptive scales cycles to log window size + resolution",
    )
    parser.add_argument(
        "--resolution-minutes",
        type=int,
        default=10,
        help="Cycle planning resolution bucket in minutes",
    )
    parser.add_argument(
        "--tune-window-fraction",
        type=float,
        default=0.40,
        help="Adaptive tune window fraction of weighted log window hours",
    )
    parser.add_argument(
        "--validate-window-fraction",
        type=float,
        default=0.80,
        help="Adaptive validate window fraction of weighted log window hours",
    )
    parser.add_argument(
        "--min-tune-hours",
        type=float,
        default=6.0,
        help="Adaptive minimum tune run horizon in hours",
    )
    parser.add_argument(
        "--max-tune-hours",
        type=float,
        default=12.0,
        help="Adaptive maximum tune run horizon in hours",
    )
    parser.add_argument(
        "--min-validate-hours",
        type=float,
        default=12.0,
        help="Adaptive minimum validation run horizon in hours",
    )
    parser.add_argument(
        "--max-validate-hours",
        type=float,
        default=24.0,
        help="Adaptive maximum validation run horizon in hours",
    )
    parser.add_argument(
        "--min-tune-cycles",
        type=int,
        default=180,
        help="Adaptive minimum tune cycles",
    )
    parser.add_argument(
        "--max-tune-cycles",
        type=int,
        default=720,
        help="Adaptive maximum tune cycles",
    )
    parser.add_argument(
        "--min-validate-cycles",
        type=int,
        default=360,
        help="Adaptive minimum validation cycles",
    )
    parser.add_argument(
        "--max-validate-cycles",
        type=int,
        default=1440,
        help="Adaptive maximum validation cycles",
    )
    parser.add_argument(
        "--min-tune-expected-merges",
        type=float,
        default=24.0,
        help="Adaptive floor for expected merges in tune runs",
    )
    parser.add_argument(
        "--min-validate-expected-merges",
        type=float,
        default=48.0,
        help="Adaptive floor for expected merges in validation runs",
    )
    parser.add_argument(
        "--scenario-window-ticks",
        type=int,
        default=0,
        help="Scenario window ticks override (0 = auto from validate cycles)",
    )
    parser.add_argument(
        "--tick-seconds-candidates",
        default="",
        help="Comma-separated tick second candidates; default derives from logs",
    )
    parser.add_argument(
        "--arrival-skew-candidates",
        default="0.9,1.05",
        help="Comma-separated arrival skew candidates",
    )
    parser.add_argument(
        "--queue-depth-candidates",
        default="0.95,1.05",
        help="Comma-separated queue-depth-scale candidates",
    )
    parser.add_argument(
        "--ci-duration-scale-candidates",
        default="1.0",
        help="DEPRECATED: CI duration is now fixed from real data. Ignored.",
    )
    parser.add_argument(
        "--grid-out",
        default="",
        help="Optional path for 240-cycle grid CSV output",
    )
    parser.add_argument(
        "--validation-out",
        default="",
        help="Optional path for 480-cycle validation CSV output",
    )
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Arbitrary metadata key/value pairs to include in metadata.json",
    )
    return parser.parse_args()


def _parse_csv_floats(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_csv_ints(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _write_csv_rows(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, int | float | str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_metadata_file(
    *,
    out_dir: Path,
    args: argparse.Namespace,
    dimension_profile: str,
    dimension_weights: dict[str, float],
    target_anchor: str,
    target_anchor_dimension: str,
    policy: str,
    target_mph: float,
    target_dimensions: dict[str, float],
    best_240: CandidateResult,
    best_480: ValidationResult,
    decision_status: str,
    decision_reason_codes: list[str],
    decision_gates: dict[str, bool],
    decision_thresholds: dict[str, float],
    grid_rows: list[dict[str, int | float | str]],
    validation_rows: list[dict[str, int | float | str]],
    selected_scenario_path: Path | None,
    selected_scenario_copy_path: Path | None,
    rejected_scenario_path: Path | None,
    grid_csv_path: Path,
    validation_csv_path: Path,
    scenario_window_ticks: int,
    tune_cycles: int,
    validate_cycles: int,
) -> None:
    now = datetime.now().astimezone()
    metadata = {
        "generated_at_iso": now.isoformat(),
        "generated_at_epoch": int(now.timestamp()),
        "run_category": "calibration",
        "generator": Path(__file__).name,
        "custom_metadata": _parse_metadata_pairs(args.metadata),
        "context": {
            "project": args.project,
            "policy": policy,
            "scenario": best_480.candidate.scenario_path.stem if best_480 else "",
            "target_mph": target_mph,
            "target_dimensions": target_dimensions,
            "dimension_profile": dimension_profile,
            "target_anchor": target_anchor,
            "target_anchor_dimension": target_anchor_dimension,
            "standard_dimension_weights": dimension_weights,
            "extended_score_component_weights": EXTENDED_SCORE_COMPONENT_WEIGHTS,
            "tune_cycles": tune_cycles,
            "validate_cycles": validate_cycles,
            "cycle_scaling": args.cycle_scaling,
            "resolution_minutes": args.resolution_minutes,
            "rank_score": args.rank_score,
            "limit": args.limit,
            "ticks_per_cycle": args.ticks_per_cycle,
            "scenario_window_ticks": scenario_window_ticks,
            "grid_candidates": len(grid_rows),
            "validated_candidates": len(validation_rows),
            "finalist_knobs": {
                "tick_seconds": best_480.candidate.tick_seconds,
                "arrival_skew": best_480.candidate.arrival_skew,
                "queue_depth_scale": best_480.candidate.queue_depth_scale,
            } if best_480 else {},
            "decision_status": decision_status,
            "decision_reason_codes": decision_reason_codes,
            "decision_gates": decision_gates,
            "decision_thresholds": decision_thresholds,
            "best_240": {
                "tick_seconds": best_240.tick_seconds,
                "arrival_skew": best_240.arrival_skew,
                "queue_depth_scale": best_240.queue_depth_scale,
                "throughput_mph": best_240.throughput_mph,
                "throughput_active_mph": best_240.throughput_active_mph,
                "throughput_peak8_mph": best_240.throughput_peak8_mph,
                "throughput_peak8_p90_mph": best_240.throughput_peak8_p90_mph,
                "throughput_peak_window_mph": best_240.throughput_peak_window_mph,
                "throughput_offpeak_mph": best_240.throughput_offpeak_mph,
                "peak_offpeak_ratio": best_240.peak_offpeak_ratio,
                "modeled_hours": best_240.modeled_hours,
                "total_arrivals": best_240.total_arrivals,
                "rebase_per_merge": best_240.rebase_per_merge,
                "merge_interval_p50_seconds": best_240.merge_interval_p50_seconds,
                "merge_interval_p95_seconds": best_240.merge_interval_p95_seconds,
                "rel_error_pct": best_240.rel_error * 100.0,
                "standard_score_pct": best_240.standard_score * 100.0,
                "extended_score_pct": best_240.extended_score * 100.0,
                "max_dimension_error_pct": best_240.max_dimension_error * 100.0,
                "score_pct": best_240.score * 100.0,
                "dimension_error_pct": {
                    k: v * 100.0 for k, v in best_240.dimension_errors.items()
                },
            },
            "best_480": {
                "tick_seconds": best_480.candidate.tick_seconds,
                "arrival_skew": best_480.candidate.arrival_skew,
                "queue_depth_scale": best_480.candidate.queue_depth_scale,
                "throughput_mph": best_480.throughput_mph,
                "throughput_active_mph": best_480.throughput_active_mph,
                "throughput_peak8_mph": best_480.throughput_peak8_mph,
                "throughput_peak8_p90_mph": best_480.throughput_peak8_p90_mph,
                "throughput_peak_window_mph": best_480.throughput_peak_window_mph,
                "throughput_offpeak_mph": best_480.throughput_offpeak_mph,
                "peak_offpeak_ratio": best_480.peak_offpeak_ratio,
                "modeled_hours": best_480.modeled_hours,
                "total_arrivals": best_480.total_arrivals,
                "rebase_per_merge": best_480.rebase_per_merge,
                "merge_interval_p50_seconds": best_480.merge_interval_p50_seconds,
                "merge_interval_p95_seconds": best_480.merge_interval_p95_seconds,
                "merged": best_480.merged,
                "rel_error_pct": best_480.rel_error * 100.0,
                "standard_score_pct": best_480.standard_score * 100.0,
                "extended_score_pct": best_480.extended_score * 100.0,
                "max_dimension_error_pct": best_480.max_dimension_error * 100.0,
                "score_pct": best_480.score * 100.0,
                "dimension_error_pct": {
                    k: v * 100.0 for k, v in best_480.dimension_errors.items()
                },
            },
            "outputs": {
                "selected_scenario_out": (
                    selected_scenario_path.name if selected_scenario_path else None
                ),
                "selected_scenario_copy": (
                    selected_scenario_copy_path.name
                    if selected_scenario_copy_path
                    else None
                ),
                "rejected_scenario_out": (
                    rejected_scenario_path.name if rejected_scenario_path else None
                ),
                "grid_csv": grid_csv_path.name,
                "validation_csv": validation_csv_path.name,
            },
        },
        "decision": {
            "status": decision_status,
            "reason_codes": decision_reason_codes,
            "gates": decision_gates,
            "thresholds_pct": decision_thresholds,
        },
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True)
    )


def main() -> None:
    global_start = perf_counter()
    args = parse_args()
    if args.resolution_minutes <= 0:
        raise ValueError("--resolution-minutes must be > 0")
    logs = [Path(p) for p in args.logs]
    policy = args.policy.strip()
    if not policy:
        raise ValueError("--policy must not be empty")
    stats = [parse_log(p, args.project) for p in logs]
    timestamp = datetime.now().strftime("%m-%d-%y_%I-%M-%S-%p")
    if args.out_dir:
        run_out_dir = Path(args.out_dir).resolve()
    elif args.grid_out:
        run_out_dir = Path(args.grid_out).resolve().parent
    elif args.validation_out:
        run_out_dir = Path(args.validation_out).resolve().parent
    else:
        run_out_dir = ROOT / "reports" / "calibration" / timestamp
        if run_out_dir.exists():
            import uuid
            timestamp = f"{timestamp}_{uuid.uuid4().hex[:6]}"
            run_out_dir = ROOT / "reports" / "calibration" / timestamp
    run_out_dir.mkdir(parents=True, exist_ok=True)
    target_dimensions = _build_target_dimensions(stats)
    dimension_weights = DIMENSION_WEIGHT_PROFILES[args.dimension_profile]
    target_anchor = _resolve_target_anchor(args.dimension_profile, args.target_anchor)
    target_anchor_dimension = TARGET_ANCHOR_TO_DIMENSION[target_anchor]
    derived_target_mph = target_dimensions.get(target_anchor_dimension, 0.0)
    target_mph = (
        args.target_mph
        if args.target_mph is not None
        else derived_target_mph
    )
    if target_mph > 0 and target_anchor_dimension in target_dimensions:
        target_dimensions[target_anchor_dimension] = target_mph
    throughput_tolerance = args.tolerance_pct / 100.0
    score_tolerance = args.score_tolerance_pct / 100.0
    max_dimension_tolerance = args.max_dimension_error_pct / 100.0

    if args.tick_seconds_candidates.strip():
        tick_candidates = _parse_csv_ints(args.tick_seconds_candidates)
    else:
        tick_candidates = _derive_tick_candidates(stats)
    arrival_candidates = _parse_csv_floats(args.arrival_skew_candidates)
    depth_candidates = _parse_csv_floats(args.queue_depth_candidates)
    if not tick_candidates:
        raise ValueError("no tick candidates resolved")
    if not arrival_candidates:
        raise ValueError("no arrival skew candidates resolved")
    if not depth_candidates:
        raise ValueError("no queue depth candidates resolved")
    tune_cycles, validate_cycles, scenario_window_ticks = _resolve_cycle_plan(
        args=args,
        stats=stats,
        tick_candidates=tick_candidates,
        target_mph=target_mph,
    )
    total_candidates = (
        len(tick_candidates) * len(arrival_candidates) * len(depth_candidates)
    )
    weighted_window_hours = weighted_avg(
        [s.window_hours for s in stats],
        [s.window_hours for s in stats],
    )

    _phase("Calibration Target")
    _status(f"project={args.project}")
    _status(f"policy={policy}")
    _status(
        f"logs={len(stats)}, weighted_window_hours={weighted_window_hours:.2f}"
    )
    _status(f"target_mph={target_mph:.3f}")
    _status(
        "target anchor:"
        f" {target_anchor} ({target_anchor_dimension})"
        f" derived={derived_target_mph:.3f}"
        + (
            f" overridden={target_mph:.3f}"
            if args.target_mph is not None
            else ""
        )
    )
    _status(
        "target dimensions:"
        f" active={target_dimensions.get('merge_per_hour_active_hours', 0.0):.3f}"
        f" peak8={target_dimensions.get('merge_per_hour_peak8', 0.0):.3f}"
        f" peak8_p90={target_dimensions.get('merge_per_hour_peak8_p90', 0.0):.3f}"
        f" peak/offpeak={target_dimensions.get('merge_peak_offpeak_ratio', 0.0):.3f}"
        f" rebase/merge={target_dimensions.get('rebase_per_merge_global', 0.0):.3f}"
        f" merge_p50_s={target_dimensions.get('merge_interval_seconds_p50', 0.0):.1f}"
        f" merge_p95_s={target_dimensions.get('merge_interval_seconds_p95', 0.0):.1f}"
    )
    _status(f"dimension profile={args.dimension_profile}")
    _status(f"run out dir={run_out_dir}")
    _status(
        f"grid: ticks={tick_candidates}, arrival_skew={arrival_candidates},"
        f" queue_depth={depth_candidates} (CI: fabricated default distribution)"
    )
    _status(
        f"candidates={total_candidates}, tune_cycles={tune_cycles},"
        f" validate_cycles={validate_cycles},"
        f" scenario_window_ticks={scenario_window_ticks},"
        f" resolution={args.resolution_minutes}m,"
        f" rank_score={args.rank_score}"
    )
    _status(
        "tolerances:"
        f" throughput<={args.tolerance_pct:.2f}%"
        f" score<={args.score_tolerance_pct:.2f}%"
        f" max_dim<={args.max_dimension_error_pct:.2f}%"
    )

    best: CandidateResult | None = None
    attempts: list[CandidateResult] = []
    port = args.port_base

    _phase(f"Grid Search ({tune_cycles}-cycle tuning)")
    with TemporaryDirectory(prefix="calib-tune-") as td:
        temp_dir = Path(td)
        _status(f"temporary candidate dir: {temp_dir}")
        idx = 0
        for tick_seconds in tick_candidates:
            for arrival_skew in arrival_candidates:
                for depth in depth_candidates:
                    idx += 1
                    candidate_start = perf_counter()
                    scenario_path = temp_dir / (
                        f"cand-{idx}-t{tick_seconds}-a{arrival_skew}"
                        f"-q{depth}.yaml"
                    )
                    _status(
                        f"[{idx:02d}/{total_candidates}] candidate start:"
                        f" tick={tick_seconds}, arrival={arrival_skew:.2f},"
                        f" depth={depth:.2f}"
                    )
                    _status(
                        f"[{idx:02d}] generating scenario"
                        f" -> {scenario_path.name}"
                    )
                    scenario_dict = build_scenario_dict(
                        stats=stats,
                        scenario_name=(
                            f"Calib candidate {idx} ({policy})"
                        ),
                        window_ticks=scenario_window_ticks,
                        seed=args.seed,
                        tick_seconds_override=tick_seconds,
                        arrival_skew=arrival_skew,
                        queue_depth_scale=depth,
                    )
                    scenario_path.write_text(
                        yaml.safe_dump(scenario_dict, sort_keys=False)
                    )
                    _status(f"[{idx:02d}] scenario generated")

                    _status(
                        f"[{idx:02d}] running {policy} tuning"
                        " simulation"
                    )
                    run_metrics, port = _run_policy_with_retry(
                        policy=policy,
                        scenario_path=scenario_path,
                        limit=args.limit,
                        cycles=tune_cycles,
                        ticks_per_cycle=args.ticks_per_cycle,
                        port=port,
                        retries=3,
                    )
                    mph = float(run_metrics["throughput_mph"])
                    merged = int(run_metrics["merged"])
                    rel_err = (
                        abs(mph - target_mph) / target_mph
                        if target_mph > 0
                        else 0.0
                    )
                    standard_score, max_dim_error, dim_errors = (
                        _score_metrics(
                            run_metrics=run_metrics,
                            target_dimensions=target_dimensions,
                            dimension_weights=dimension_weights,
                        )
                    )
                    ext_score = _extended_score(
                        standard_score=standard_score,
                        max_dimension_error=max_dim_error,
                        rel_error=rel_err,
                    )
                    ranking_score = _score_for_rank(
                        rank_score=args.rank_score,
                        rel_error=rel_err,
                        standard_score=standard_score,
                        extended_score=ext_score,
                    )
                    result = CandidateResult(
                        tick_seconds=tick_seconds,
                        arrival_skew=arrival_skew,
                        queue_depth_scale=depth,
                        throughput_mph=mph,
                        throughput_active_mph=float(
                            run_metrics["throughput_active_mph"]
                        ),
                        throughput_peak8_mph=float(
                            run_metrics["throughput_peak8_mph"]
                        ),
                        throughput_peak8_p90_mph=float(
                            run_metrics["throughput_peak8_p90_mph"]
                        ),
                        throughput_peak_window_mph=float(
                            run_metrics["throughput_peak_window_mph"]
                        ),
                        throughput_offpeak_mph=float(
                            run_metrics["throughput_offpeak_mph"]
                        ),
                        peak_offpeak_ratio=float(
                            run_metrics["peak_offpeak_ratio"]
                        ),
                        modeled_hours=float(
                            run_metrics["modeled_hours"]
                        ),
                        total_arrivals=float(
                            run_metrics["total_arrivals"]
                        ),
                        rebase_per_merge=float(
                            run_metrics["rebase_per_merge"]
                        ),
                        merge_interval_p50_seconds=float(
                            run_metrics["merge_interval_p50_seconds"]
                        ),
                        merge_interval_p95_seconds=float(
                            run_metrics["merge_interval_p95_seconds"]
                        ),
                        merged=merged,
                        rel_error=rel_err,
                        standard_score=standard_score,
                        extended_score=ext_score,
                        max_dimension_error=max_dim_error,
                        score=ranking_score,
                        dimension_errors=dim_errors,
                        scenario_path=scenario_path,
                    )
                    attempts.append(result)
                    if best is None or result.score < best.score:
                        best = result
                        _status(
                            f"[{idx:02d}] NEW BEST at"
                            f" {tune_cycles} cycles:"
                            f" mph={mph:.3f},"
                            f" err={rel_err*100:.2f}%"
                            f" std={standard_score*100:.2f}%"
                            f" ext={ext_score*100:.2f}%"
                            f" max_dim={max_dim_error*100:.2f}%"
                            f" rank={ranking_score*100:.2f}%"
                            f" ({args.rank_score})"
                            f" dims[{_format_dim_errors(dim_errors)}]"
                        )
                    elapsed = perf_counter() - candidate_start
                    _status(
                        f"[{idx:02d}] done: tick={tick_seconds:>3}"
                        f" arrival={arrival_skew:.2f}"
                        f" depth={depth:.2f}"
                        f" -> mph={mph:.3f} merged={merged}"
                        f" err={rel_err*100:.2f}%"
                        f" std={standard_score*100:.2f}%"
                        f" ext={ext_score*100:.2f}%"
                        f" rank={ranking_score*100:.2f}%"
                        f" elapsed={_fmt_seconds(elapsed)}"
                    )
                    if (
                        args.early_stop
                        and rel_err <= throughput_tolerance
                        and ranking_score <= score_tolerance
                    ):
                        _status(
                            "tuning tolerance hit at 240-cycle"
                            " pass; stopping grid search early"
                        )
                        break
                if (
                    args.early_stop
                    and best
                    and best.rel_error <= throughput_tolerance
                    and best.score <= score_tolerance
                ):
                    break
            if (
                args.early_stop
                and best
                and best.rel_error <= throughput_tolerance
                and best.score <= score_tolerance
            ):
                break

        if best is None:
            raise RuntimeError("no candidates evaluated")

        _phase(f"Best Candidate ({tune_cycles}-cycle pass)")
        _status(
            f"tick={best.tick_seconds}, arrival={best.arrival_skew:.2f},"
            f" depth={best.queue_depth_scale:.2f}"
        )
        _status(
            f"mph={best.throughput_mph:.3f} merged={best.merged}"
            f" rel_error={best.rel_error*100:.2f}%"
            f" std={best.standard_score*100:.2f}%"
            f" ext={best.extended_score*100:.2f}%"
            f" max_dim={best.max_dimension_error*100:.2f}%"
            f" rank={best.score*100:.2f}% ({args.rank_score})"
        )
        _status(f"dimension errors: {_format_dim_errors(best.dimension_errors)}")

        top_n = max(1, args.validate_top_n)
        to_validate = sorted(attempts, key=lambda x: x.score)[:top_n]
        _phase(
            f"Validation (top {len(to_validate)} candidates at {validate_cycles} cycles)"
        )

        validations: list[ValidationResult] = []
        for i, cand in enumerate(to_validate, start=1):
            val_start = perf_counter()
            _status(
                f"[val {i}/{len(to_validate)}] start: tick={cand.tick_seconds},"
                f" arrival={cand.arrival_skew:.2f},"
                f" depth={cand.queue_depth_scale:.2f}"
            )
            val_run_metrics, port = _run_policy_with_retry(
                policy=policy,
                scenario_path=cand.scenario_path,
                limit=args.limit,
                cycles=validate_cycles,
                ticks_per_cycle=args.ticks_per_cycle,
                port=port,
                retries=4,
            )
            v_mph = float(val_run_metrics["throughput_mph"])
            v_merged = int(val_run_metrics["merged"])
            v_err = abs(v_mph - target_mph) / target_mph if target_mph > 0 else 0.0
            v_std_score, v_max_dim_error, v_dim_errors = _score_metrics(
                run_metrics=val_run_metrics,
                target_dimensions=target_dimensions,
                dimension_weights=dimension_weights,
            )
            v_ext_score = _extended_score(
                standard_score=v_std_score,
                max_dimension_error=v_max_dim_error,
                rel_error=v_err,
            )
            v_rank = _score_for_rank(
                rank_score=args.rank_score,
                rel_error=v_err,
                standard_score=v_std_score,
                extended_score=v_ext_score,
            )
            validations.append(
                ValidationResult(
                    candidate=cand,
                    throughput_mph=v_mph,
                    throughput_active_mph=float(
                        val_run_metrics["throughput_active_mph"]
                    ),
                    throughput_peak8_mph=float(val_run_metrics["throughput_peak8_mph"]),
                    throughput_peak8_p90_mph=float(
                        val_run_metrics["throughput_peak8_p90_mph"]
                    ),
                    throughput_peak_window_mph=float(
                        val_run_metrics["throughput_peak_window_mph"]
                    ),
                    throughput_offpeak_mph=float(
                        val_run_metrics["throughput_offpeak_mph"]
                    ),
                    peak_offpeak_ratio=float(val_run_metrics["peak_offpeak_ratio"]),
                    modeled_hours=float(val_run_metrics["modeled_hours"]),
                    total_arrivals=float(val_run_metrics["total_arrivals"]),
                    rebase_per_merge=float(val_run_metrics["rebase_per_merge"]),
                    merge_interval_p50_seconds=float(
                        val_run_metrics["merge_interval_p50_seconds"]
                    ),
                    merge_interval_p95_seconds=float(
                        val_run_metrics["merge_interval_p95_seconds"]
                    ),
                    merged=v_merged,
                    rel_error=v_err,
                    standard_score=v_std_score,
                    extended_score=v_ext_score,
                    max_dimension_error=v_max_dim_error,
                    score=v_rank,
                    dimension_errors=v_dim_errors,
                )
            )
            elapsed = perf_counter() - val_start
            _status(
                f"[val {i}/{len(to_validate)}] done: tick={cand.tick_seconds:>3}"
                f" arrival={cand.arrival_skew:.2f}"
                f" depth={cand.queue_depth_scale:.2f}"
                f" -> mph={v_mph:.3f} merged={v_merged}"
                f" err={v_err*100:.2f}%"
                f" std={v_std_score*100:.2f}%"
                f" ext={v_ext_score*100:.2f}%"
                f" rank={v_rank*100:.2f}% ({args.rank_score})"
                f" elapsed={_fmt_seconds(elapsed)}"
            )

        accepted_validations = [
            v
            for v in validations
            if _evaluate_gate_results(
                rel_error=v.rel_error,
                ranking_score=v.score,
                max_dimension_error=v.max_dimension_error,
                throughput_tolerance=throughput_tolerance,
                score_tolerance=score_tolerance,
                max_dimension_tolerance=max_dimension_tolerance,
            )["accepted"]
        ]
        pool = accepted_validations if accepted_validations else validations
        best_validation = min(pool, key=lambda x: x.score)
        if accepted_validations:
            _status(
                f"{len(accepted_validations)}/{len(validations)}"
                " validation candidates pass all gates; selecting best among"
                " accepted"
            )
        else:
            _status(
                "no validation candidates pass all gates; selecting best"
                " overall"
            )
        final_path = (
            Path(args.scenario_out).resolve()
            if args.scenario_out
            else (run_out_dir / f"calibrated-scenario-{policy}.yaml")
        )
        final_path.parent.mkdir(parents=True, exist_ok=True)

        grid_out_path = (
            Path(args.grid_out).resolve()
            if args.grid_out
            else run_out_dir / "calibration-grid.csv"
        )
        validation_out_path = (
            Path(args.validation_out).resolve()
            if args.validation_out
            else run_out_dir / "calibration-validation.csv"
        )

        best_240 = min(attempts, key=lambda x: x.score)
        best_480 = best_validation
        grid_rows: list[dict[str, int | float | str]] = []
        for i, cand in enumerate(attempts, start=1):
            gate_results = _evaluate_gate_results(
                rel_error=cand.rel_error,
                ranking_score=cand.score,
                max_dimension_error=cand.max_dimension_error,
                throughput_tolerance=throughput_tolerance,
                score_tolerance=score_tolerance,
                max_dimension_tolerance=max_dimension_tolerance,
            )
            grid_rows.append(
                {
                    "phase": "tune",
                    "policy": policy,
                    "candidate_idx": i,
                    "tick_seconds": cand.tick_seconds,
                    "arrival_skew": cand.arrival_skew,
                    "queue_depth_scale": cand.queue_depth_scale,
                    "throughput_mph": cand.throughput_mph,
                    "throughput_active_mph": cand.throughput_active_mph,
                    "throughput_peak8_mph": cand.throughput_peak8_mph,
                    "throughput_peak8_p90_mph": cand.throughput_peak8_p90_mph,
                    "throughput_peak_window_mph": cand.throughput_peak_window_mph,
                    "throughput_offpeak_mph": cand.throughput_offpeak_mph,
                    "peak_offpeak_ratio": cand.peak_offpeak_ratio,
                    "modeled_hours": cand.modeled_hours,
                    "total_arrivals": cand.total_arrivals,
                    "rebase_per_merge": cand.rebase_per_merge,
                    "merge_interval_p50_seconds": cand.merge_interval_p50_seconds,
                    "merge_interval_p95_seconds": cand.merge_interval_p95_seconds,
                    "merged": cand.merged,
                    "rel_error_pct": cand.rel_error * 100.0,
                    "throughput_rel_error_pct": cand.rel_error * 100.0,
                    "standard_score_pct": cand.standard_score * 100.0,
                    "extended_score_pct": cand.extended_score * 100.0,
                    "max_dimension_error_pct": cand.max_dimension_error * 100.0,
                    "score_pct": cand.score * 100.0,
                    "dimension_error_pct_json": json.dumps(
                        {
                            k: round(v * 100.0, 4)
                            for k, v in cand.dimension_errors.items()
                        },
                        sort_keys=True,
                    ),
                    "target_mph": target_mph,
                    "target_dimensions_json": json.dumps(
                        {k: round(v, 6) for k, v in target_dimensions.items()},
                        sort_keys=True,
                    ),
                    "cycles": tune_cycles,
                    "rank_score": args.rank_score,
                    "throughput_gate_pass": int(gate_results["throughput_pass"]),
                    "score_gate_pass": int(gate_results["score_pass"]),
                    "max_dimension_gate_pass": int(gate_results["max_dimension_pass"]),
                    "accepted_by_gates": int(gate_results["accepted"]),
                    "is_best_240": int(cand is best_240),
                }
            )
        _write_csv_rows(
            grid_out_path,
            [
                "phase",
                "policy",
                "candidate_idx",
                "tick_seconds",
                "arrival_skew",
                "queue_depth_scale",
                "throughput_mph",
                "throughput_active_mph",
                "throughput_peak8_mph",
                "throughput_peak8_p90_mph",
                "throughput_peak_window_mph",
                "throughput_offpeak_mph",
                "peak_offpeak_ratio",
                "modeled_hours",
                "total_arrivals",
                "rebase_per_merge",
                "merge_interval_p50_seconds",
                "merge_interval_p95_seconds",
                "merged",
                "rel_error_pct",
                "throughput_rel_error_pct",
                "standard_score_pct",
                "extended_score_pct",
                "max_dimension_error_pct",
                "score_pct",
                "dimension_error_pct_json",
                "target_mph",
                "target_dimensions_json",
                "cycles",
                "rank_score",
                "throughput_gate_pass",
                "score_gate_pass",
                "max_dimension_gate_pass",
                "accepted_by_gates",
                "is_best_240",
            ],
            grid_rows,
        )

        validation_rows: list[dict[str, int | float | str]] = []
        sorted_validations = sorted(validations, key=lambda x: x.score)
        for rank, val in enumerate(sorted_validations, start=1):
            gate_results = _evaluate_gate_results(
                rel_error=val.rel_error,
                ranking_score=val.score,
                max_dimension_error=val.max_dimension_error,
                throughput_tolerance=throughput_tolerance,
                score_tolerance=score_tolerance,
                max_dimension_tolerance=max_dimension_tolerance,
            )
            validation_rows.append(
                {
                    "phase": "validate",
                    "policy": policy,
                    "validation_rank": rank,
                    "tick_seconds": val.candidate.tick_seconds,
                    "arrival_skew": val.candidate.arrival_skew,
                    "queue_depth_scale": val.candidate.queue_depth_scale,
                    "throughput_mph": val.throughput_mph,
                    "throughput_active_mph": val.throughput_active_mph,
                    "throughput_peak8_mph": val.throughput_peak8_mph,
                    "throughput_peak8_p90_mph": val.throughput_peak8_p90_mph,
                    "throughput_peak_window_mph": val.throughput_peak_window_mph,
                    "throughput_offpeak_mph": val.throughput_offpeak_mph,
                    "peak_offpeak_ratio": val.peak_offpeak_ratio,
                    "modeled_hours": val.modeled_hours,
                    "total_arrivals": val.total_arrivals,
                    "rebase_per_merge": val.rebase_per_merge,
                    "merge_interval_p50_seconds": val.merge_interval_p50_seconds,
                    "merge_interval_p95_seconds": val.merge_interval_p95_seconds,
                    "merged": val.merged,
                    "rel_error_pct": val.rel_error * 100.0,
                    "throughput_rel_error_pct": val.rel_error * 100.0,
                    "standard_score_pct": val.standard_score * 100.0,
                    "extended_score_pct": val.extended_score * 100.0,
                    "max_dimension_error_pct": val.max_dimension_error * 100.0,
                    "score_pct": val.score * 100.0,
                    "dimension_error_pct_json": json.dumps(
                        {
                            k: round(v * 100.0, 4)
                            for k, v in val.dimension_errors.items()
                        },
                        sort_keys=True,
                    ),
                    "target_mph": target_mph,
                    "target_dimensions_json": json.dumps(
                        {k: round(v, 6) for k, v in target_dimensions.items()},
                        sort_keys=True,
                    ),
                    "cycles": validate_cycles,
                    "rank_score": args.rank_score,
                    "throughput_gate_pass": int(gate_results["throughput_pass"]),
                    "score_gate_pass": int(gate_results["score_pass"]),
                    "max_dimension_gate_pass": int(gate_results["max_dimension_pass"]),
                    "accepted_by_gates": int(gate_results["accepted"]),
                    "source_240_rel_error_pct": val.candidate.rel_error * 100.0,
                    "source_240_score_pct": val.candidate.score * 100.0,
                    "is_best_480": int(val is best_480),
                }
            )
        _write_csv_rows(
            validation_out_path,
            [
                "phase",
                "policy",
                "validation_rank",
                "tick_seconds",
                "arrival_skew",
                "queue_depth_scale",
                "throughput_mph",
                "throughput_active_mph",
                "throughput_peak8_mph",
                "throughput_peak8_p90_mph",
                "throughput_peak_window_mph",
                "throughput_offpeak_mph",
                "peak_offpeak_ratio",
                "modeled_hours",
                "total_arrivals",
                "rebase_per_merge",
                "merge_interval_p50_seconds",
                "merge_interval_p95_seconds",
                "merged",
                "rel_error_pct",
                "throughput_rel_error_pct",
                "standard_score_pct",
                "extended_score_pct",
                "max_dimension_error_pct",
                "score_pct",
                "dimension_error_pct_json",
                "target_mph",
                "target_dimensions_json",
                "cycles",
                "rank_score",
                "throughput_gate_pass",
                "score_gate_pass",
                "max_dimension_gate_pass",
                "accepted_by_gates",
                "source_240_rel_error_pct",
                "source_240_score_pct",
                "is_best_480",
            ],
            validation_rows,
        )

        final_gate_results = _evaluate_gate_results(
            rel_error=best_validation.rel_error,
            ranking_score=best_validation.score,
            max_dimension_error=best_validation.max_dimension_error,
            throughput_tolerance=throughput_tolerance,
            score_tolerance=score_tolerance,
            max_dimension_tolerance=max_dimension_tolerance,
        )
        decision_reason_codes = _decision_reason_codes(final_gate_results)
        decision_status = "accepted" if final_gate_results["accepted"] else "rejected"
        decision_thresholds = {
            "throughput_rel_error_pct": args.tolerance_pct,
            "rank_score_pct": args.score_tolerance_pct,
            "max_dimension_error_pct": args.max_dimension_error_pct,
        }

        _phase("Final Decision")
        selected_scenario_path: Path | None = None
        selected_scenario_copy_path: Path | None = None
        rejected_scenario_path: Path | None = None
        scenario_content = best_validation.candidate.scenario_path.read_text()
        if final_gate_results["accepted"]:
            _status(f"writing selected scenario -> {final_path}")
            final_path.write_text(scenario_content)
            selected_scenario_path = final_path
            selected_scenario_copy_path = run_out_dir / "selected-scenario.yaml"
            selected_scenario_copy_path.write_text(scenario_content)
            _status(f"scenario copy -> {selected_scenario_copy_path}")
        else:
            rejected_scenario_path = run_out_dir / "rejected-scenario.yaml"
            rejected_scenario_path.write_text(scenario_content)
            _status(f"run rejected; writing rejected scenario -> {rejected_scenario_path}")
            if args.scenario_out:
                _status(
                    "selected scenario output not written due to failed acceptance gates:"
                    f" {final_path}"
                )
        _status(f"grid csv -> {grid_out_path}")
        _status(f"validation csv -> {validation_out_path}")

        _status(f"best {validate_cycles}-cycle candidate:")
        _status(
            f"tick={best_validation.candidate.tick_seconds},"
            f" arrival={best_validation.candidate.arrival_skew:.2f},"
            f" depth={best_validation.candidate.queue_depth_scale:.2f}"
        )
        _status(
            "target="
            f"{target_mph:.3f} mph | {policy}={best_validation.throughput_mph:.3f} mph"
            f" | merged={best_validation.merged}"
            f" | throughput_rel_error={best_validation.rel_error*100:.2f}%"
            f" | standard_score={best_validation.standard_score*100:.2f}%"
            f" | extended_score={best_validation.extended_score*100:.2f}%"
            f" | max_dim_error={best_validation.max_dimension_error*100:.2f}%"
            f" | rank_score={best_validation.score*100:.2f}% ({args.rank_score})"
        )
        _status(f"dimension errors: {_format_dim_errors(best_validation.dimension_errors)}")
        _status(
            "PASS"
            if final_gate_results["accepted"]
            else "MISS"
        )
        _status(
            "gate results:"
            f" throughput={'PASS' if final_gate_results['throughput_pass'] else 'MISS'}"
            f" ({best_validation.rel_error*100:.2f}% <= {args.tolerance_pct:.2f}%)"
            f" | rank_score={'PASS' if final_gate_results['score_pass'] else 'MISS'}"
            f" ({best_validation.score*100:.2f}% <= {args.score_tolerance_pct:.2f}%)"
            f" | max_dim={'PASS' if final_gate_results['max_dimension_pass'] else 'MISS'}"
            f" ({best_validation.max_dimension_error*100:.2f}% <= {args.max_dimension_error_pct:.2f}%)"
        )
        _status(f"decision={decision_status} reasons={','.join(decision_reason_codes)}")
        _write_metadata_file(
            out_dir=run_out_dir,
            args=args,
            dimension_profile=args.dimension_profile,
            dimension_weights=dimension_weights,
            target_anchor=target_anchor,
            target_anchor_dimension=target_anchor_dimension,
            policy=policy,
            target_mph=target_mph,
            target_dimensions=target_dimensions,
            best_240=best_240,
            best_480=best_480,
            decision_status=decision_status,
            decision_reason_codes=decision_reason_codes,
            decision_gates=final_gate_results,
            decision_thresholds=decision_thresholds,
            grid_rows=grid_rows,
            validation_rows=validation_rows,
            selected_scenario_path=selected_scenario_path,
            selected_scenario_copy_path=selected_scenario_copy_path,
            rejected_scenario_path=rejected_scenario_path,
            grid_csv_path=grid_out_path,
            validation_csv_path=validation_out_path,
            scenario_window_ticks=scenario_window_ticks,
            tune_cycles=tune_cycles,
            validate_cycles=validate_cycles,
        )
        _status(f"metadata -> {run_out_dir / 'metadata.json'}")
        _status(f"total elapsed={_fmt_seconds(perf_counter() - global_start)}")
        if not final_gate_results["accepted"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
