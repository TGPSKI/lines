#!/usr/bin/env python3
"""Calibrate simulator inputs from user-supplied gitlab-housekeeping logs.

The caller must select the project whose events should be analyzed. The tool prints:
- Per-log merge/rebase throughput
- Aggregate calibration targets
- Optional scenario YAML tuned to those aggregate targets
"""

from __future__ import annotations

import argparse
import random
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import yaml

LINE_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+"
    r"\[(?P<lvl>INFO|ERROR)\].*?- (?P<msg>.*)$"
)
GQL_RE = re.compile(r"using gql endpoint")
MERGE_RE = re.compile(r"\['merge',\s*'([^']+)',\s*(\d+)\]")
MERGE_ERR_RE = re.compile(r"unable to merge\s+(\d+):")
REBASE_RE = re.compile(r"\['rebase',\s*'([^']+)',\s*(\d+)\]")
REBASE_LIMIT_RE = re.compile(r"rebase limit reached for this reconcile loop")
ACTION_RE = re.compile(r"\['([^']+)',\s*'([^']+)'")


# Authored demonstration distributions. These values are deliberately simple and
# were not derived from operational logs. Input logs calibrate aggregate timing
# and throughput only; generated identities and CI shapes remain synthetic.
SYNTHETIC_PRIORITY_LABEL_WEIGHTS: list[tuple[str, int]] = [
    ("bot/approved: critical", 5),
    ("bot/approved: high", 15),
    ("bot/approved: medium", 30),
    ("bot/approved: low", 30),
    ("bot/approved", 15),
    ("lgtm", 5),
]

SYNTHETIC_TENANT_LABELS = [
    "tenant-alpha",
    "tenant-beta",
    "tenant-gamma",
    "tenant-delta",
    "tenant-epsilon",
    "tenant-zeta",
]

SYNTHETIC_CI_DURATION_MINUTES_WEIGHTS: dict[int, int] = {
    5: 5,
    8: 25,
    12: 35,
    18: 25,
    25: 10,
}
SYNTHETIC_PIPELINE_FAILURE_RATE = 0.05
SYNTHETIC_API_FAILURE_RATE = 0.01
SYNTHETIC_BASE_APPROVED_AT = datetime(2024, 1, 1, 9, 0, 0)


def _ci_minutes_to_tick_weights(tick_seconds: int) -> dict[int, int]:
    """Convert the minutes-based CI distribution to tick-based weights."""
    import math

    weights: dict[int, int] = {}
    for minutes, weight in SYNTHETIC_CI_DURATION_MINUTES_WEIGHTS.items():
        ticks = max(1, math.ceil(minutes * 60 / tick_seconds))
        weights[ticks] = weights.get(ticks, 0) + weight
    return weights


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class LogStats:
    path: Path
    window_hours: float
    cycles: int
    avg_cycle_seconds: float
    merge_attempts: int
    merge_successes: int
    merge_failures: int
    rebases: int
    rebase_limit_hits: int
    project_hourly_activity: list[int]
    project_hourly_arrivals: list[int]
    project_weekday_activity: list[int]
    project_weekday_arrivals: list[int]
    project_hourly_merges: list[int]
    project_hourly_rebases: list[int]
    performance_dimensions: dict[str, float]

    @property
    def merge_success_per_hour(self) -> float:
        return self.merge_successes / self.window_hours if self.window_hours else 0.0

    @property
    def rebase_per_hour(self) -> float:
        return self.rebases / self.window_hours if self.window_hours else 0.0


def _estimate_cycle_seconds_from_events(event_ts: list[datetime]) -> float:
    if len(event_ts) < 2:
        return 0.0

    event_ts_sorted = sorted(event_ts)
    intervals = [
        (b - a).total_seconds()
        for a, b in zip(event_ts_sorted, event_ts_sorted[1:], strict=False)
        if 0 < (b - a).total_seconds() <= 600
    ]
    if not intervals:
        return 0.0

    # Ignore dense within-cycle bursts and infer the loop cadence from a modal
    # 5-second bin over medium gaps.
    medium_intervals = [d for d in intervals if d >= 20]
    if not medium_intervals:
        return float(median(intervals))

    by_bin: dict[int, list[float]] = defaultdict(list)
    for interval in medium_intervals:
        bin_key = int(interval // 5) * 5
        by_bin[bin_key].append(interval)
    modal_bin = max(by_bin.items(), key=lambda item: len(item[1]))[0]
    modal_samples = by_bin[modal_bin]
    return sum(modal_samples) / len(modal_samples)


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * pct / 100))
    return float(ordered[idx])


def _best_peak_window_start(hourly_merge_avg: list[float], width: int = 8) -> int:
    if not hourly_merge_avg:
        return 0
    best_start = 0
    best_avg = -1.0
    for start in range(24):
        hours = [(start + i) % 24 for i in range(width)]
        avg = sum(hourly_merge_avg[h] for h in hours) / width
        if avg > best_avg:
            best_avg = avg
            best_start = start
    return best_start


def _compute_performance_dimensions(
    *,
    merge_successes: int,
    rebases: int,
    window_hours: float,
    merge_timestamps: list[datetime],
    hourly_merges: list[int],
    hourly_rebases: list[int],
    hourly_merge_avg_by_hod: list[float],
    hourly_start_hour_of_day: int,
) -> dict[str, float]:
    if not hourly_merges:
        return {
            "merge_per_hour_24h": 0.0,
            "merge_per_hour_active_hours": 0.0,
            "merge_per_hour_peak8": 0.0,
            "merge_per_hour_peak8_p90": 0.0,
            "merge_peak_offpeak_ratio": 0.0,
            "rebase_per_merge_global": 0.0,
            "rebase_per_merge_peak8": 0.0,
            "rebase_per_merge_offpeak": 0.0,
            "merge_interval_seconds_p50": 0.0,
            "merge_interval_seconds_p95": 0.0,
            "peak_window_start_hour_utc": 0.0,
        }

    peak_start_hour = _best_peak_window_start(hourly_merge_avg_by_hod, width=8)
    peak_hours = {(peak_start_hour + i) % 24 for i in range(8)}

    peak_merge_counts = [
        m
        for h, m in enumerate(hourly_merges)
        if (hourly_start_hour_of_day + h) % 24 in peak_hours
    ]
    peak_rebase_counts = [
        r
        for h, r in enumerate(hourly_rebases)
        if (hourly_start_hour_of_day + h) % 24 in peak_hours
    ]
    offpeak_merge_counts = [
        m
        for h, m in enumerate(hourly_merges)
        if (hourly_start_hour_of_day + h) % 24 not in peak_hours
    ]
    offpeak_rebase_counts = [
        r
        for h, r in enumerate(hourly_rebases)
        if (hourly_start_hour_of_day + h) % 24 not in peak_hours
    ]
    active_hours = [c for c in hourly_merges if c > 0]

    merge_ts = sorted(merge_timestamps)
    merge_intervals = [
        (b - a).total_seconds()
        for a, b in zip(merge_ts, merge_ts[1:], strict=False)
        if (b - a).total_seconds() > 0
    ]

    merge_total = sum(hourly_merges)
    peak_merge_total = sum(peak_merge_counts)
    offpeak_merge_total = sum(offpeak_merge_counts)
    peak_merge_avg = (
        sum(peak_merge_counts) / len(peak_merge_counts) if peak_merge_counts else 0.0
    )
    offpeak_merge_avg = (
        sum(offpeak_merge_counts) / len(offpeak_merge_counts)
        if offpeak_merge_counts
        else 0.0
    )
    peak_offpeak_ratio = (
        peak_merge_avg / offpeak_merge_avg if offpeak_merge_avg > 0 else 0.0
    )

    return {
        "merge_per_hour_24h": merge_successes / window_hours if window_hours else 0.0,
        "merge_per_hour_active_hours": (
            sum(active_hours) / len(active_hours) if active_hours else 0.0
        ),
        "merge_per_hour_peak8": (
            sum(peak_merge_counts) / len(peak_merge_counts)
            if peak_merge_counts
            else 0.0
        ),
        "merge_per_hour_peak8_p90": _percentile(peak_merge_counts, 90),
        "merge_peak_offpeak_ratio": peak_offpeak_ratio,
        "rebase_per_merge_global": rebases / max(1, merge_total),
        "rebase_per_merge_peak8": sum(peak_rebase_counts) / max(1, peak_merge_total),
        "rebase_per_merge_offpeak": (
            sum(offpeak_rebase_counts) / max(1, offpeak_merge_total)
        ),
        "merge_interval_seconds_p50": _percentile(merge_intervals, 50),
        "merge_interval_seconds_p95": _percentile(merge_intervals, 95),
        "peak_window_start_hour_utc": float(peak_start_hour),
    }


def compute_weighted_performance_dimensions(stats: list[LogStats]) -> dict[str, float]:
    if not stats:
        return {}
    by_hours = [s.window_hours for s in stats]
    keys = sorted({k for s in stats for k in s.performance_dimensions})
    out: dict[str, float] = {}
    for key in keys:
        out[key] = weighted_avg(
            [s.performance_dimensions.get(key, 0.0) for s in stats],
            by_hours,
        )
    return out


def _normalize_weights(counts: list[int], floor_ratio: float = 0.02) -> list[float]:
    if not counts:
        return []
    max_count = max(counts)
    if max_count <= 0:
        return [1.0 for _ in counts]
    floor = max_count * floor_ratio
    return [max(float(count), floor) for count in counts]


def _skew_weight(position: float, arrival_skew: float) -> float:
    skew = _clamp(arrival_skew, 0.4, 2.5)
    if abs(skew - 1.0) < 1e-9:
        return 1.0
    if skew > 1.0:
        return max(0.05, (1.0 - position) ** (skew - 1.0))
    return max(0.05, position ** ((1.0 / skew) - 1.0))


def _sample_arrival_ticks(
    *,
    rng: random.Random,
    arrivals: int,
    window_ticks: int,
    tick_seconds: int,
    arrival_skew: float,
    hourly_weights: list[float],
    weekday_weights: list[float],
    start_hour: int,
    start_weekday: int,
) -> list[int]:
    if arrivals <= 0:
        return []
    if window_ticks <= 1:
        return [0 for _ in range(arrivals)]

    candidate_ticks = list(range(1, window_ticks))
    candidate_weights: list[float] = []
    span_ticks = max(1, window_ticks - 1)
    for tick in candidate_ticks:
        seconds_from_start = tick * tick_seconds
        hour = (start_hour + int(seconds_from_start // 3600)) % 24
        weekday = (start_weekday + int(seconds_from_start // 86400)) % 7
        position = tick / span_ticks
        weight = (
            hourly_weights[hour]
            * weekday_weights[weekday]
            * _skew_weight(position, arrival_skew)
        )
        candidate_weights.append(weight)

    return sorted(rng.choices(candidate_ticks, weights=candidate_weights, k=arrivals))


def parse_log(path: Path, project_filter: str) -> LogStats:
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    gql_ts: list[datetime] = []
    project_action_ts: list[datetime] = []
    project_hourly_activity = [0 for _ in range(24)]
    project_hourly_arrivals = [0 for _ in range(24)]
    project_weekday_activity = [0 for _ in range(7)]
    project_weekday_arrivals = [0 for _ in range(7)]
    project_hourly_merges = [0 for _ in range(24)]
    project_hourly_rebases = [0 for _ in range(24)]
    merge_timestamps: list[datetime] = []
    hourly_merge_by_abs: dict[datetime, int] = defaultdict(int)
    hourly_rebase_by_abs: dict[datetime, int] = defaultdict(int)
    merges: list[dict[str, Any]] = []
    merge_pending: dict[int, list[dict[str, Any]]] = defaultdict(list)
    rebases = 0
    rebase_limit_hits = 0

    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
            msg = m.group("msg")
            if first_ts is None:
                first_ts = ts
            last_ts = ts

            if GQL_RE.search(msg):
                gql_ts.append(ts)

            action_m = ACTION_RE.search(msg)
            if action_m:
                action_name = action_m.group(1)
                action_project = action_m.group(2)
                if action_project == project_filter:
                    project_action_ts.append(ts)
                    if action_name in {
                        "merge",
                        "add_label",
                        "remove_label",
                        "close_item",
                    }:
                        project_hourly_activity[ts.hour] += 1
                        project_weekday_activity[ts.weekday()] += 1
                    if action_name in {"add_label", "remove_label"}:
                        project_hourly_arrivals[ts.hour] += 1
                        project_weekday_arrivals[ts.weekday()] += 1

            merge_m = MERGE_RE.search(msg)
            if merge_m:
                project = merge_m.group(1)
                if project != project_filter:
                    continue
                iid = int(merge_m.group(2))
                rec = {"iid": iid, "failed": False}
                merges.append(rec)
                merge_pending[iid].append(rec)
                merge_timestamps.append(ts)
                project_hourly_merges[ts.hour] += 1
                hour_key = ts.replace(minute=0, second=0)
                hourly_merge_by_abs[hour_key] += 1
                continue

            err_m = MERGE_ERR_RE.search(msg)
            if err_m:
                iid = int(err_m.group(1))
                if merge_pending[iid]:
                    merge_pending[iid].pop()["failed"] = True
                continue

            rebase_m = REBASE_RE.search(msg)
            if rebase_m and rebase_m.group(1) == project_filter:
                rebases += 1
                project_hourly_rebases[ts.hour] += 1
                hour_key = ts.replace(minute=0, second=0)
                hourly_rebase_by_abs[hour_key] += 1
                continue

            if REBASE_LIMIT_RE.search(msg):
                rebase_limit_hits += 1

    if first_ts is None or last_ts is None:
        raise ValueError(f"no parseable log lines in {path}")

    window_hours = (last_ts - first_ts).total_seconds() / 3600.0
    intervals = [
        (b - a).total_seconds()
        for a, b in zip(gql_ts, gql_ts[1:], strict=False)
        if 0 < (b - a).total_seconds() < 600
    ]
    if intervals:
        avg_cycle_seconds = sum(intervals) / len(intervals)
        cycles = len(gql_ts)
    else:
        avg_cycle_seconds = _estimate_cycle_seconds_from_events(project_action_ts)
        cycles = (
            int(round((window_hours * 3600.0) / avg_cycle_seconds))
            if avg_cycle_seconds > 0.0
            else 0
        )
    merge_successes = sum(1 for m in merges if not m["failed"])
    merge_failures = len(merges) - merge_successes
    start_hour = first_ts.replace(minute=0, second=0)
    end_hour = last_ts.replace(minute=0, second=0)
    hourly_merges_abs: list[int] = []
    hourly_rebases_abs: list[int] = []
    cursor = start_hour
    while cursor <= end_hour:
        hourly_merges_abs.append(hourly_merge_by_abs.get(cursor, 0))
        hourly_rebases_abs.append(hourly_rebase_by_abs.get(cursor, 0))
        cursor += timedelta(hours=1)

    hourly_merge_avg_by_hod = [0.0 for _ in range(24)]
    hourly_hod_counts = [0 for _ in range(24)]
    cursor = start_hour
    idx = 0
    while cursor <= end_hour:
        hourly_merge_avg_by_hod[cursor.hour] += hourly_merges_abs[idx]
        hourly_hod_counts[cursor.hour] += 1
        cursor += timedelta(hours=1)
        idx += 1
    for hour in range(24):
        if hourly_hod_counts[hour] > 0:
            hourly_merge_avg_by_hod[hour] /= hourly_hod_counts[hour]

    performance_dimensions = _compute_performance_dimensions(
        merge_successes=merge_successes,
        rebases=rebases,
        window_hours=window_hours,
        merge_timestamps=merge_timestamps,
        hourly_merges=hourly_merges_abs,
        hourly_rebases=hourly_rebases_abs,
        hourly_merge_avg_by_hod=hourly_merge_avg_by_hod,
        hourly_start_hour_of_day=start_hour.hour,
    )

    return LogStats(
        path=path,
        window_hours=window_hours,
        cycles=cycles,
        avg_cycle_seconds=avg_cycle_seconds,
        merge_attempts=len(merges),
        merge_successes=merge_successes,
        merge_failures=merge_failures,
        rebases=rebases,
        rebase_limit_hits=rebase_limit_hits,
        project_hourly_activity=project_hourly_activity,
        project_hourly_arrivals=project_hourly_arrivals,
        project_weekday_activity=project_weekday_activity,
        project_weekday_arrivals=project_weekday_arrivals,
        project_hourly_merges=project_hourly_merges,
        project_hourly_rebases=project_hourly_rebases,
        performance_dimensions=performance_dimensions,
    )


def weighted_avg(values: list[float], weights: list[float]) -> float:
    total_w = sum(weights)
    if total_w <= 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights, strict=False)) / total_w


def sample_weighted(rng: random.Random, weights: dict[int, int]) -> int:
    vals = list(weights.keys())
    probs = list(weights.values())
    return rng.choices(vals, weights=probs, k=1)[0]


def _scale_duration_weights(weights: dict[int, int], *, scale: float) -> dict[int, int]:
    if not weights:
        return {}
    scaled: dict[int, int] = {}
    for ticks, weight in weights.items():
        scaled_ticks = max(1, int(round(float(ticks) * scale)))
        scaled[scaled_ticks] = scaled.get(scaled_ticks, 0) + int(weight)
    return scaled


def build_scenario_dict(
    *,
    stats: list[LogStats],
    scenario_name: str,
    window_ticks: int,
    seed: int,
    tick_seconds_override: int | None = None,
    arrival_skew: float = 0.85,
    queue_depth_scale: float = 1.0,
) -> dict[str, Any]:
    rng = random.Random(seed)
    by_hours = [s.window_hours for s in stats]

    perf_dims = compute_weighted_performance_dimensions(stats)
    target_merges_per_hour = perf_dims.get("merge_per_hour_24h", 0.0)
    target_rebases_per_hour = weighted_avg([s.rebase_per_hour for s in stats], by_hours)
    avg_cycle_seconds = weighted_avg([s.avg_cycle_seconds for s in stats], by_hours)
    total_attempts = sum(s.merge_attempts for s in stats)
    merge_failure_rate = sum(s.merge_failures for s in stats) / max(1, total_attempts)
    tick_seconds_input = (
        tick_seconds_override
        if tick_seconds_override is not None
        else int(round(avg_cycle_seconds))
    )
    tick_seconds = max(30, tick_seconds_input)
    ci_duration_weights = _ci_minutes_to_tick_weights(tick_seconds)

    # Scenario window is driven by ticks * calibrated tick length.
    window_hours = (window_ticks * tick_seconds) / 3600.0
    expected_merges = max(30, int(round(target_merges_per_hour * window_hours)))

    # Short-horizon scenarios (e.g. 24h) are prone to artificial steady-state
    # behavior if we over-seed the queue. Keep less initial backlog so hourly
    # arrival shape meaningfully affects observed throughput over the window.
    if window_hours <= 36:
        initial_open_ratio = 0.06
        total_mrs_ratio = 1.10
        min_initial_open = 4
        extra_floor = 10
    elif window_hours <= 192:
        initial_open_ratio = 0.12
        total_mrs_ratio = 1.25
        min_initial_open = 8
        extra_floor = 14
    else:
        initial_open_ratio = 0.18
        total_mrs_ratio = 1.35
        min_initial_open = 13
        extra_floor = 16

    initial_open_base = max(
        min_initial_open, int(round(expected_merges * initial_open_ratio))
    )
    total_mrs_base = max(
        initial_open_base + extra_floor,
        int(round(expected_merges * total_mrs_ratio)),
    )
    initial_open = max(10, int(round(initial_open_base * queue_depth_scale)))
    total_mrs = max(initial_open + 8, int(round(total_mrs_base * queue_depth_scale)))
    arrivals = max(0, total_mrs - initial_open)

    # Scale initial queue composition to the aggregate rebase pressure measured
    # in the caller's logs without copying project identities into the scenario.
    rebase_pressure = target_rebases_per_hour / max(target_merges_per_hour, 0.1)
    stale_share = _clamp(0.04 + 0.008 * rebase_pressure, 0.08, 0.14)
    if window_hours <= 36:
        running_share = 0.16
        ready_share = 0.24
    elif window_hours <= 192:
        running_share = 0.20
        ready_share = 0.32
    else:
        running_share = 0.24
        ready_share = 0.40
    no_pipeline_share = _clamp(
        1.0 - (ready_share + running_share + stale_share),
        0.22,
        0.45,
    )
    raw_hourly_activity = [0 for _ in range(24)]
    raw_hourly_arrivals = [0 for _ in range(24)]
    raw_weekday_activity = [0 for _ in range(7)]
    raw_weekday_arrivals = [0 for _ in range(7)]
    raw_hourly_merges = [0 for _ in range(24)]
    raw_hourly_rebases = [0 for _ in range(24)]
    for stat in stats:
        for hour in range(24):
            raw_hourly_activity[hour] += stat.project_hourly_activity[hour]
            raw_hourly_arrivals[hour] += stat.project_hourly_arrivals[hour]
            raw_hourly_merges[hour] += stat.project_hourly_merges[hour]
            raw_hourly_rebases[hour] += stat.project_hourly_rebases[hour]
        for weekday in range(7):
            raw_weekday_activity[weekday] += stat.project_weekday_activity[weekday]
            raw_weekday_arrivals[weekday] += stat.project_weekday_arrivals[weekday]
    hourly_weights = _normalize_weights(raw_hourly_activity)
    weekday_weights = _normalize_weights(raw_weekday_activity)
    peak_start_hour = _best_peak_window_start(
        [float(v) for v in raw_hourly_merges],
        width=8,
    )
    peak_hours = [f"{(peak_start_hour + i) % 24:02d}" for i in range(8)]
    synthetic_ci_minutes_range = (
        f"{min(SYNTHETIC_CI_DURATION_MINUTES_WEIGHTS)}-"
        f"{max(SYNTHETIC_CI_DURATION_MINUTES_WEIGHTS)}"
    )

    scenario: dict[str, Any] = {
        "metadata": {
            "name": scenario_name,
            "source": "calibrate_from_housekeeping_logs.py",
            "source_scope": "caller-selected project aggregates",
            "seed": seed,
            "window_ticks": window_ticks,
            "calibration_targets": {
                "target_merge_success_per_hour": round(target_merges_per_hour, 3),
                "target_rebases_per_hour": round(target_rebases_per_hour, 3),
                "avg_reconcile_cycle_seconds": round(avg_cycle_seconds, 2),
                "observed_merge_failure_rate": round(merge_failure_rate, 4),
                "window_hours": round(window_hours, 3),
                "target_rebases_per_merge": round(rebase_pressure, 3),
                "ci_model": "fixed-minutes-distribution",
                "ci_minutes_range": synthetic_ci_minutes_range,
                "performance_dimensions": {
                    key: round(value, 4) for key, value in perf_dims.items()
                },
                "seed_shares": {
                    "ready": round(ready_share, 3),
                    "running": round(running_share, 3),
                    "stale_success": round(stale_share, 3),
                    "no_pipeline": round(no_pipeline_share, 3),
                },
                "knobs": {
                    "tick_seconds_override": tick_seconds_override,
                    "arrival_skew": round(arrival_skew, 4),
                    "queue_depth_scale": round(queue_depth_scale, 4),
                },
                "arrival_profile": {
                    "model": "observed-hourly-activity",
                    "peak_window_hours_utc": peak_hours,
                    "hourly_arrival_counts": {
                        f"{hour:02d}": raw_hourly_activity[hour] for hour in range(24)
                    },
                    "hourly_activity_counts": {
                        f"{hour:02d}": raw_hourly_activity[hour] for hour in range(24)
                    },
                    "hourly_merge_counts": {
                        f"{hour:02d}": raw_hourly_merges[hour] for hour in range(24)
                    },
                    "hourly_rebase_counts": {
                        f"{hour:02d}": raw_hourly_rebases[hour] for hour in range(24)
                    },
                    "weekday_activity_counts": {
                        day: raw_weekday_activity[idx]
                        for idx, day in enumerate(
                            ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                        )
                    },
                },
            },
            "notes": [
                "Calibrated from aggregate events in user-supplied logs.",
                "Project identities are omitted from generated scenario records.",
                "Priority, tenant, and CI defaults are fabricated demonstration data.",
                "Arrival timing uses aggregate hourly/weekday activity from the input.",
            ],
        },
        "tick_seconds": tick_seconds,
        "project": {
            "id": 1001,
            "name": "calibration-demo",
            "path": "calibration-demo",
            "path_with_namespace": "example-org/calibration-demo",
            "default_branch": "master",
            "target_head": "target-001",
        },
        "merge_requests": [],
        "pipeline_durations": {
            "distribution": "weighted",
            "min_ticks": min(ci_duration_weights),
            "max_ticks": max(ci_duration_weights),
            "failure_rate": SYNTHETIC_PIPELINE_FAILURE_RATE,
            "weights": {str(k): v for k, v in ci_duration_weights.items()},
        },
        "failure_path_realism": {
            "merge_failure_rate": SYNTHETIC_API_FAILURE_RATE,
            "rebase_failure_rate": SYNTHETIC_API_FAILURE_RATE,
        },
        "sha_pools": {
            "target_advances": {"master": [f"target-{i:03d}" for i in range(2, 420)]}
        },
    }

    if window_hours >= 22:
        start_hour = peak_start_hour
    else:
        start_hour = rng.choices(
            list(range(24)),
            weights=hourly_weights,
            k=1,
        )[0]
    if window_hours >= 120:
        start_weekday = rng.choices(
            list(range(7)),
            weights=weekday_weights,
            k=1,
        )[0]
    else:
        max_wd = max(range(5), key=lambda d: weekday_weights[d])
        start_weekday = max_wd
    scenario["metadata"]["calibration_targets"]["arrival_profile"][
        "scenario_start_hour"
    ] = start_hour
    scenario["metadata"]["calibration_targets"]["arrival_profile"][
        "scenario_start_weekday"
    ] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][start_weekday]

    arrival_ticks = _sample_arrival_ticks(
        rng=rng,
        arrivals=arrivals,
        window_ticks=window_ticks,
        tick_seconds=tick_seconds,
        arrival_skew=arrival_skew,
        hourly_weights=hourly_weights,
        weekday_weights=weekday_weights,
        start_hour=start_hour,
        start_weekday=start_weekday,
    )
    base_approved = SYNTHETIC_BASE_APPROVED_AT

    # Reflect only the aggregate failure rate supplied by the caller. Individual
    # merge requests and their placement are fabricated deterministically.
    deterministic_failures = max(1, int(round(expected_merges * merge_failure_rate)))
    failure_pool_start = max(initial_open + 1, int(total_mrs * 0.55))
    failure_pool = list(range(failure_pool_start, total_mrs + 1))
    failure_count = min(deterministic_failures, len(failure_pool))
    failure_iids = (
        set(rng.sample(failure_pool, k=failure_count)) if failure_count else set()
    )

    for idx in range(total_mrs):
        iid = idx + 1
        mr_id = 4000 + iid
        priority_label = rng.choices(
            [p[0] for p in SYNTHETIC_PRIORITY_LABEL_WEIGHTS],
            weights=[p[1] for p in SYNTHETIC_PRIORITY_LABEL_WEIGHTS],
            k=1,
        )[0]
        labels = [priority_label, rng.choice(SYNTHETIC_TENANT_LABELS)]
        if rng.random() < 0.15:
            labels.append(rng.choice(SYNTHETIC_TENANT_LABELS))
        labels = sorted(set(labels))

        ci_duration = sample_weighted(rng, ci_duration_weights)
        arrival_tick = 0 if idx < initial_open else arrival_ticks[idx - initial_open]
        approved_at = (base_approved + timedelta(minutes=idx * 3)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        mr: dict[str, Any] = {
            "id": mr_id,
            "iid": iid,
            "title": f"synthetic/calibrated-change-{iid:03d}",
            "state": "opened",
            "sha": f"mr{iid}-sha-001",
            "rebased_target_sha": "target-000",
            "labels": labels,
            "approved_at": approved_at,
            "pipelines": [],
            "ci_duration": ci_duration,
            "arrival_tick": arrival_tick,
        }

        if iid in failure_iids:
            mr["merge_failure"] = {
                "remaining": 1,
                "status_code": 405,
                "detail": "405 Method Not Allowed",
            }

        # Seed a mixed initial queue for useful policy comparisons:
        # some ready-to-merge, some running, many stale or missing pipeline.
        if idx < initial_open:
            roll = rng.random()
            if roll < ready_share:
                mr["rebased_target_sha"] = "target-001"
                mr["pipelines"] = [
                    {
                        "id": 9000 + iid,
                        "status": "success",
                        "sha": mr["sha"],
                        "root_sha": "target-001",
                        "outcome": "success",
                    }
                ]
            elif roll < ready_share + running_share:
                mr["rebased_target_sha"] = "target-001"
                mr["pipelines"] = [
                    {
                        "id": 9000 + iid,
                        "status": "running",
                        "sha": mr["sha"],
                        "root_sha": "target-001",
                        "running_ticks_remaining": max(2, ci_duration // 2),
                        "outcome": "success",
                    }
                ]
            elif roll < ready_share + running_share + stale_share:
                mr["rebased_target_sha"] = "target-000"
                mr["pipelines"] = [
                    {
                        "id": 9000 + iid,
                        "status": "success",
                        "sha": mr["sha"],
                        "root_sha": "target-000",
                        "outcome": "success",
                    }
                ]
            else:
                mr["rebased_target_sha"] = "target-000"
                mr["pipelines"] = []

        scenario["merge_requests"].append(mr)

    return scenario


def print_stats(stats: list[LogStats]) -> None:
    for s in stats:
        print(f"{s.path}")
        print(f"  window_hours={s.window_hours:.3f}")
        print(
            f"  merge_success={s.merge_successes}/{s.merge_attempts} "
            f"({s.merge_success_per_hour:.3f} per hour)"
        )
        print(
            f"  rebases={s.rebases} ({s.rebase_per_hour:.3f} per hour) "
            f"rebase_limit_hits={s.rebase_limit_hits}"
        )
        print(f"  avg_cycle_seconds={s.avg_cycle_seconds:.2f}")
        print(
            "  dimensions:"
            f" merge24h={s.performance_dimensions['merge_per_hour_24h']:.3f}"
            f" active={s.performance_dimensions['merge_per_hour_active_hours']:.3f}"
            f" peak8={s.performance_dimensions['merge_per_hour_peak8']:.3f}"
            f" peak8_p90={s.performance_dimensions['merge_per_hour_peak8_p90']:.3f}"
        )
        print(
            "              "
            f" rebase/merge={s.performance_dimensions['rebase_per_merge_global']:.3f}"
            f" peak_ratio={s.performance_dimensions['rebase_per_merge_peak8']:.3f}"
            f" offpeak_ratio={s.performance_dimensions['rebase_per_merge_offpeak']:.3f}"
            " interval_p95_s="
            f"{s.performance_dimensions['merge_interval_seconds_p95']:.1f}"
        )
        print()

    by_hours = [s.window_hours for s in stats]
    target_merges_per_hour = weighted_avg(
        [s.merge_success_per_hour for s in stats],
        by_hours,
    )
    target_rebases_per_hour = weighted_avg([s.rebase_per_hour for s in stats], by_hours)
    avg_cycle_seconds = weighted_avg([s.avg_cycle_seconds for s in stats], by_hours)
    total_attempts = sum(s.merge_attempts for s in stats)
    total_failures = sum(s.merge_failures for s in stats)
    perf_dims = compute_weighted_performance_dimensions(stats)
    print("Aggregate targets (weighted by window hours):")
    print(f"  merge_success_per_hour={target_merges_per_hour:.3f}")
    print(f"  rebase_per_hour={target_rebases_per_hour:.3f}")
    print(f"  avg_cycle_seconds={avg_cycle_seconds:.2f}")
    print(
        f"  merge_failure_rate={total_failures}/{total_attempts}="
        f"{(total_failures / max(1, total_attempts)):.4f}"
    )
    print(
        "  dimensions:"
        " active_merge_per_hour="
        f"{perf_dims.get('merge_per_hour_active_hours', 0.0):.3f}"
        f" peak8_merge_per_hour={perf_dims.get('merge_per_hour_peak8', 0.0):.3f}"
        f" peak8_merge_p90={perf_dims.get('merge_per_hour_peak8_p90', 0.0):.3f}"
    )
    print(
        "              "
        f" rebase_per_merge={perf_dims.get('rebase_per_merge_global', 0.0):.3f}"
        f" peak_rebase_per_merge={perf_dims.get('rebase_per_merge_peak8', 0.0):.3f}"
        f" merge_interval_p95_s={perf_dims.get('merge_interval_seconds_p95', 0.0):.1f}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--logs",
        nargs="+",
        required=True,
        help="Paths to housekeeping pod logs",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Project name filter from ['merge', '<project>', iid] log entries",
    )
    parser.add_argument(
        "--emit-scenario",
        default=None,
        help="Optional output path for generated scenario YAML",
    )
    parser.add_argument(
        "--scenario-name",
        default="Calibrated Merge Queue",
        help="Metadata name for generated scenario",
    )
    parser.add_argument(
        "--window-ticks",
        type=int,
        default=480,
        help="Scenario window length in ticks",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic scenario generation",
    )
    parser.add_argument(
        "--tick-seconds-override",
        type=int,
        default=None,
        help="Optional fixed tick duration in seconds for scenario output",
    )
    parser.add_argument(
        "--arrival-skew",
        type=float,
        default=0.85,
        help="Arrival timing power curve (>1 earlier arrivals, <1 later arrivals)",
    )
    parser.add_argument(
        "--queue-depth-scale",
        type=float,
        default=1.0,
        help="Multiplier for initial/open queue depth generated by calibration",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log_paths = [Path(p) for p in args.logs]
    stats = [parse_log(p, args.project) for p in log_paths]
    print_stats(stats)

    if args.emit_scenario:
        out_path = Path(args.emit_scenario)
        scenario = build_scenario_dict(
            stats=stats,
            scenario_name=args.scenario_name,
            window_ticks=args.window_ticks,
            seed=args.seed,
            tick_seconds_override=args.tick_seconds_override,
            arrival_skew=args.arrival_skew,
            queue_depth_scale=args.queue_depth_scale,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml.safe_dump(scenario, sort_keys=False))
        print()
        print(f"Wrote scenario: {out_path}")


if __name__ == "__main__":
    main()
