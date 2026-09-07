"""Summarise a run from its event stream alone.

One implementation, three readers: run_standalone.py writes the result into the
metrics NDJSON as a run_summary event, scripts/backfill_run_summary.py adds it
to runs that predate it, and the UI displays it instead of recomputing its own
copy. Every divergence this replaces — two queue_drain denominators, arrivals
counted twice, pipelines that ignored skip_ci — came from a second
implementation drifting from this one.

Pure: it takes events and returns a dict. No server, no files, no globals,
which also makes it the first part of the metrics path that unit tests can
reach.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from glab_api.metrics import compute_summary


def _percentile(sorted_vals: list, pct: int) -> int:
    """Compute percentile from a pre-sorted list of values."""
    if not sorted_vals:
        return 0
    idx = int(len(sorted_vals) * pct / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]

def _percentile_float(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * pct / 100))
    return float(ordered[idx])

def _compute_hourly_throughput_dims(
    *,
    merge_ticks: list[int],
    total_time_ticks: int,
    tick_seconds: int,
) -> dict[str, float]:
    if total_time_ticks <= 0:
        return {
            "throughput_active_merges_per_hour": 0.0,
            "throughput_peak8_merges_per_hour": 0.0,
            "throughput_peak8_p90_merges_per_hour": 0.0,
            "merge_interval_p50_seconds": 0.0,
            "merge_interval_p95_seconds": 0.0,
        }

    hour_count = max(1, int(math.ceil((total_time_ticks * tick_seconds) / 3600.0)))
    hourly_merges = [0 for _ in range(hour_count)]
    for tick in merge_ticks:
        bucket = min(hour_count - 1, max(0, int((tick * tick_seconds) // 3600)))
        hourly_merges[bucket] += 1

    active_hourly = [v for v in hourly_merges if v > 0]
    active_mph = sum(active_hourly) / len(active_hourly) if active_hourly else 0.0
    peak_window = min(8, hour_count)
    best_avg = 0.0
    best_slice = hourly_merges[:peak_window] if peak_window else []
    for start in range(0, hour_count - peak_window + 1):
        window_vals = hourly_merges[start : start + peak_window]
        avg = sum(window_vals) / peak_window
        if avg > best_avg:
            best_avg = avg
            best_slice = window_vals

    merge_ticks_sorted = sorted(merge_ticks)
    merge_intervals_seconds = [
        (b - a) * tick_seconds
        for a, b in zip(merge_ticks_sorted, merge_ticks_sorted[1:], strict=False)
        if b > a
    ]
    return {
        "throughput_active_merges_per_hour": active_mph,
        "throughput_peak8_merges_per_hour": best_avg,
        "throughput_peak8_p90_merges_per_hour": _percentile_float(best_slice, 90),
        "merge_interval_p50_seconds": _percentile_float(merge_intervals_seconds, 50),
        "merge_interval_p95_seconds": _percentile_float(merge_intervals_seconds, 95),
    }

def _load_ndjson_events(path: str | None) -> list[dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    events: list[dict[str, Any]] = []
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(evt, dict):
                events.append(evt)
    return events

def _extract_hourly_event_profile(
    *,
    events: list[dict[str, Any]],
    tick_seconds: int,
    total_time_ticks: int,
) -> dict[str, Any]:
    if not events or total_time_ticks <= 0:
        return {
            "modeled_hours": 0.0,
            "total_arrivals": 0,
            "throughput_peak_window_merges_per_hour": 0.0,
            "throughput_offpeak_merges_per_hour": 0.0,
            "arrival_peak_window_per_hour": 0.0,
            "arrival_offpeak_window_per_hour": 0.0,
            "peak_offpeak_throughput_ratio": 0.0,
            "scenario_start_hour_utc": 0,
            "peak_window_hours_utc": [],
            "hourly_merges": [],
            "hourly_arrivals": [],
        }

    hour_count = max(1, int(math.ceil((total_time_ticks * tick_seconds) / 3600.0)))
    hourly_merges = [0 for _ in range(hour_count)]
    hourly_arrivals = [0 for _ in range(hour_count)]

    for evt in events:
        if evt.get("event") != "merge":
            continue
        tick = evt.get("tick")
        if not isinstance(tick, int):
            continue
        bucket = min(hour_count - 1, max(0, int((tick * tick_seconds) // 3600)))
        hourly_merges[bucket] += 1

    scenario_meta = next((e for e in events if e.get("event") == "scenario_meta"), {})

    # An arrival that fires appears both in scenario_meta["arrivals"] (the
    # schedule) and in the tick event that emitted it. Counting both
    # double-counts every arrival and adds the scheduled ones that never
    # happened, so prefer the observed ticks and fall back to the schedule only
    # for runs that emitted none.
    observed = 0
    for evt in events:
        if evt.get("event") != "tick":
            continue
        tick = evt.get("tick")
        arrivals = evt.get("arrivals")
        if not isinstance(tick, int) or not isinstance(arrivals, list):
            continue
        bucket = min(hour_count - 1, max(0, int((tick * tick_seconds) // 3600)))
        hourly_arrivals[bucket] += len(arrivals)
        observed += len(arrivals)

    if not observed:
        arrivals_meta = scenario_meta.get("arrivals", [])
        if isinstance(arrivals_meta, list):
            for item in arrivals_meta:
                if not isinstance(item, dict):
                    continue
                tick = item.get("tick")
                if not isinstance(tick, int):
                    continue
                bucket = min(hour_count - 1, max(0, int((tick * tick_seconds) // 3600)))
                hourly_arrivals[bucket] += 1

    calibration = scenario_meta.get("scenario_metadata", {}).get(
        "calibration_targets", {}
    )
    arrival_profile = calibration.get("arrival_profile", {})
    start_hour = int(arrival_profile.get("scenario_start_hour", 0) or 0) % 24
    peak_hours_raw = arrival_profile.get("peak_window_hours_utc", [])
    peak_hours = {
        int(h) % 24
        for h in peak_hours_raw
        if isinstance(h, (str, int, float)) and str(h).strip()
    }

    peak_merges: list[int] = []
    offpeak_merges: list[int] = []
    peak_arrivals: list[int] = []
    offpeak_arrivals: list[int] = []
    for idx in range(hour_count):
        utc_hour = (start_hour + idx) % 24
        if utc_hour in peak_hours:
            peak_merges.append(hourly_merges[idx])
            peak_arrivals.append(hourly_arrivals[idx])
        else:
            offpeak_merges.append(hourly_merges[idx])
            offpeak_arrivals.append(hourly_arrivals[idx])

    peak_merge_avg = sum(peak_merges) / len(peak_merges) if peak_merges else 0.0
    offpeak_merge_avg = (
        sum(offpeak_merges) / len(offpeak_merges) if offpeak_merges else 0.0
    )
    peak_arrival_avg = sum(peak_arrivals) / len(peak_arrivals) if peak_arrivals else 0.0
    offpeak_arrival_avg = (
        sum(offpeak_arrivals) / len(offpeak_arrivals) if offpeak_arrivals else 0.0
    )
    peak_offpeak_ratio = (
        peak_merge_avg / offpeak_merge_avg if offpeak_merge_avg > 0 else 0.0
    )

    return {
        "modeled_hours": (total_time_ticks * tick_seconds) / 3600.0,
        "total_arrivals": int(sum(hourly_arrivals)),
        "throughput_peak_window_merges_per_hour": peak_merge_avg,
        "throughput_offpeak_merges_per_hour": offpeak_merge_avg,
        "arrival_peak_window_per_hour": peak_arrival_avg,
        "arrival_offpeak_window_per_hour": offpeak_arrival_avg,
        "peak_offpeak_throughput_ratio": peak_offpeak_ratio,
        "scenario_start_hour_utc": start_hour,
        "peak_window_hours_utc": sorted(peak_hours),
        "hourly_merges": hourly_merges,
        "hourly_arrivals": hourly_arrivals,
    }


def derive_run_shape(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Recover from events what the run loop used to track in local variables.

    total_time_ticks is max(tick), not max(tick) + 1: the loop advances a tick
    only between cycles, so a 1200-cycle run ends at tick 1199.
    """
    merge_ticks = [
        int(e["tick"])
        for e in events
        if e.get("event") == "merge" and isinstance(e.get("tick"), int)
    ]
    tick_ticks = [
        int(e["tick"])
        for e in events
        if e.get("event") == "tick" and isinstance(e.get("tick"), int)
    ]
    meta = next((e for e in events if e.get("event") == "scenario_meta"), {})
    arrival_by_iid = {
        int(a["iid"]): int(a["tick"])
        for a in (meta.get("arrivals") or [])
        if isinstance(a, dict) and "iid" in a and "tick" in a
    }
    return {
        "merge_ticks": sorted(merge_ticks),
        "total_time_ticks": max(tick_ticks) if tick_ticks else 0,
        "tick_seconds": max(1, int(meta.get("tick_seconds", 60) or 60)),
        "total_mrs": int(meta.get("total_mrs", 0) or 0),
        # One runner pass per cycle, so every merge in a cycle shares its tick.
        "merge_cycles": len(set(merge_ticks)),
        "arrival_by_iid": arrival_by_iid,
        "scenario_meta": meta,
    }


def wait_time_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-MR wait times, matching /__sim/merged_mrs: merge_tick - arrival_tick."""
    shape = derive_run_shape(events)
    arrival = shape["arrival_by_iid"]
    waits = sorted(
        int(e["tick"]) - max(0, arrival.get(int(e["mr_iid"]), 0))
        for e in events
        if e.get("event") == "merge" and isinstance(e.get("mr_iid"), int)
    )
    if not waits:
        return {"wait_p50": 0, "wait_p95": 0, "wait_max": 0, "starved_mrs": 0}
    return {
        "wait_p50": _percentile(waits, 50),
        "wait_p95": _percentile(waits, 95),
        "wait_max": waits[-1],
        "starved_mrs": sum(1 for w in waits if w > 100),
    }


def summarize_run(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Every summary metric for one run, from its events alone."""
    shape = derive_run_shape(events)
    merge_ticks = shape["merge_ticks"]
    total_time_ticks = shape["total_time_ticks"]
    tick_seconds = shape["tick_seconds"]
    total_mrs = shape["total_mrs"]
    merge_cycles = shape["merge_cycles"]

    metrics = compute_summary(events)
    mrs_merged = len(merge_ticks)
    throughput = mrs_merged / total_time_ticks if total_time_ticks > 0 else 0
    total_time_hours = (total_time_ticks * tick_seconds) / 3600
    throughput_per_hour = mrs_merged / total_time_hours if total_time_hours > 0 else 0
    # None, not the run length: a threshold never reached must not render as
    # though it were reached on the last tick.
    time_to_first_merge = merge_ticks[0] if merge_ticks else None
    time_to_merge_10 = merge_ticks[9] if len(merge_ticks) >= 10 else None
    avg_merge_interval = total_time_ticks / mrs_merged if mrs_merged > 0 else 0
    avg_mrs_per_merge_cycle = mrs_merged / merge_cycles if merge_cycles > 0 else 0
    throughput_dims = _compute_hourly_throughput_dims(
        merge_ticks=merge_ticks,
        total_time_ticks=total_time_ticks,
        tick_seconds=tick_seconds,
    )
    rebase_calls = float(metrics.get("rebase_calls", 0))
    rebase_per_merge = rebase_calls / mrs_merged if mrs_merged > 0 else 0.0

    metrics["total_time_ticks"] = total_time_ticks
    metrics["mrs_merged"] = mrs_merged
    metrics["tick_seconds"] = tick_seconds
    metrics["throughput_merges_per_tick"] = round(throughput, 4)
    metrics["throughput_merges_per_hour"] = round(throughput_per_hour, 3)
    metrics["time_to_first_merge"] = time_to_first_merge
    metrics["time_to_merge_10"] = time_to_merge_10
    metrics["avg_merge_interval_ticks"] = round(avg_merge_interval, 2)
    metrics["queue_drain_pct"] = (
        round(mrs_merged / total_mrs * 100, 1) if total_mrs > 0 else 0
    )
    metrics["merge_cycles"] = merge_cycles
    metrics["avg_mrs_per_merge_cycle"] = round(avg_mrs_per_merge_cycle, 2)
    metrics["throughput_active_merges_per_hour"] = round(
        throughput_dims["throughput_active_merges_per_hour"],
        3,
    )
    metrics["throughput_peak8_merges_per_hour"] = round(
        throughput_dims["throughput_peak8_merges_per_hour"],
        3,
    )
    metrics["throughput_peak8_p90_merges_per_hour"] = round(
        throughput_dims["throughput_peak8_p90_merges_per_hour"],
        3,
    )
    metrics["merge_interval_p50_seconds"] = round(
        throughput_dims["merge_interval_p50_seconds"],
        1,
    )
    metrics["merge_interval_p95_seconds"] = round(
        throughput_dims["merge_interval_p95_seconds"],
        1,
    )
    metrics["rebase_per_merge_ratio"] = round(rebase_per_merge, 3)
    hourly_profile = _extract_hourly_event_profile(
        events=events,
        tick_seconds=tick_seconds,
        total_time_ticks=total_time_ticks,
    )
    metrics["modeled_hours"] = round(float(hourly_profile["modeled_hours"]), 3)
    metrics["total_arrivals"] = int(hourly_profile["total_arrivals"])
    metrics["throughput_peak_window_merges_per_hour"] = round(
        float(hourly_profile["throughput_peak_window_merges_per_hour"]),
        3,
    )
    metrics["throughput_offpeak_merges_per_hour"] = round(
        float(hourly_profile["throughput_offpeak_merges_per_hour"]),
        3,
    )
    metrics["arrival_peak_window_per_hour"] = round(
        float(hourly_profile["arrival_peak_window_per_hour"]),
        3,
    )
    metrics["arrival_offpeak_window_per_hour"] = round(
        float(hourly_profile["arrival_offpeak_window_per_hour"]),
        3,
    )
    metrics["peak_offpeak_throughput_ratio"] = round(
        float(hourly_profile["peak_offpeak_throughput_ratio"]),
        3,
    )
    metrics["scenario_start_hour_utc"] = int(hourly_profile["scenario_start_hour_utc"])
    metrics["peak_window_hours_utc_json"] = json.dumps(
        hourly_profile["peak_window_hours_utc"],
        separators=(",", ":"),
    )
    metrics["hourly_merges_json"] = json.dumps(
        hourly_profile["hourly_merges"],
        separators=(",", ":"),
    )
    metrics["hourly_arrivals_json"] = json.dumps(
        hourly_profile["hourly_arrivals"],
        separators=(",", ":"),
    )

    metrics.update(wait_time_metrics(events))

    # Counted here rather than in the UI so both read one number. CI segment
    # durations (ci_min/avg/max) stay UI-side: they come from the swimlane
    # segment model, which has only one implementation and so cannot drift.
    metrics["force_merges"] = sum(
        1 for e in events if e.get("event") == "merge" and e.get("force_merge")
    )
    metrics["ci_failures"] = sum(
        1
        for e in events
        if e.get("event") == "tick"
        for tr in (e.get("transitions") or [])
        if str(tr.get("to", "")).lower() == "failed"
    )
    metrics["total_mrs"] = total_mrs
    return metrics


def append_run_summary(metrics_path: str, summary: dict[str, Any]) -> bool:
    """Append the summary to a metrics NDJSON as a run_summary event.

    Idempotent: a file that already carries one is left alone, so re-running a
    backfill cannot stack duplicates. Returns whether a line was written.
    """
    if not metrics_path or not os.path.exists(metrics_path):
        return False
    with open(metrics_path) as fh:
        for line in fh:
            if '"run_summary"' in line:
                return False
    with open(metrics_path, "a") as fh:
        fh.write(json.dumps({"event": "run_summary", "metrics": summary}) + "\n")
    return True


def read_run_summary(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The summary a run carries, or None for runs written before it existed."""
    for event in reversed(events):
        if event.get("event") == "run_summary":
            metrics = event.get("metrics")
            return metrics if isinstance(metrics, dict) else None
    return None
