"""Metrics collection and NDJSON output.

Captures per-tick snapshots and mutation events into a metrics stream
that can be written to an NDJSON file for later reporting.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from .state import SimState


@dataclass
class MetricsCollector:
    """Collects metrics events and writes them as NDJSON."""

    events: list[dict[str, Any]] = field(default_factory=list)
    _file: IO[str] | None = field(default=None, repr=False)

    def open_file(self, path: str | Path) -> None:
        self._file = Path(path).open("w")  # noqa: SIM115

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None

    def record(self, event: dict[str, Any]) -> None:
        event["timestamp"] = time.time()
        self.events.append(event)
        if self._file:
            self._file.write(json.dumps(event) + "\n")
            self._file.flush()

    def record_snapshot(self, state: SimState) -> None:
        """Record a full state snapshot at current tick."""
        snapshot = {
            "event": "snapshot",
            "tick": state.tick_count,
            "target_head": state.project.target_head,
            "open_mrs": len(state.open_mrs()),
            "active_pipelines": len(state.active_pipelines()),
            "same_root_success_pool": len(state.same_root_success_pool()),
            "stale_successes": len(state.stale_successes()),
            "peak_active_pipelines": self._peak_active(),
        }
        self.record(snapshot)

    def _peak_active(self) -> int:
        peak = 0
        for e in self.events:
            if e.get("event") == "snapshot" or e.get("event") == "tick":
                peak = max(peak, e.get("active_pipelines", 0))
        return peak

    def summary(self) -> dict[str, Any]:
        """Compute summary metrics from collected events."""
        rebase_events = [e for e in self.events if e.get("event") == "rebase"]
        merge_events = [e for e in self.events if e.get("event") == "merge"]
        tick_events = [e for e in self.events if e.get("event") == "tick"]
        cancel_events = [e for e in self.events if e.get("event") == "pipeline_cancel"]

        peak_active = 0
        same_root_pool_values: list[int] = []
        stale_values: list[int] = []

        for e in tick_events:
            peak_active = max(peak_active, e.get("active_pipelines", 0))
            same_root_pool_values.append(e.get("same_root_success_pool", 0))
            stale_values.append(e.get("stale_successes", 0))

        mr_rebase_counts: dict[int, int] = {}
        for e in rebase_events:
            iid = e["mr_iid"]
            mr_rebase_counts[iid] = mr_rebase_counts.get(iid, 0) + 1

        duplicate_rebases = {k: v for k, v in mr_rebase_counts.items() if v > 1}

        total_stale_from_merges = sum(
            e.get("stale_successes_created", 0) for e in merge_events
        )

        return {
            "total_ticks": len(tick_events),
            "rebase_calls": len(rebase_events),
            "merge_calls": len(merge_events),
            "pipeline_cancels": len(cancel_events),
            "pipelines_created": len(rebase_events),
            "peak_active_pipelines": peak_active,
            "same_root_success_pool_values": same_root_pool_values,
            "same_root_success_pool_p50": _percentile(same_root_pool_values, 50),
            "same_root_success_pool_p95": _percentile(same_root_pool_values, 95),
            "same_root_success_pool_max": max(same_root_pool_values)
            if same_root_pool_values
            else 0,
            "stale_success_max": max(stale_values) if stale_values else 0,
            "total_stale_successes_from_merges": total_stale_from_merges,
            "duplicate_rebases_per_mr": duplicate_rebases,
            "duplicate_rebase_total": sum(v - 1 for v in duplicate_rebases.values()),
            "mrs_merged": len(merge_events),
            "target_advances": len(merge_events),
            "average_mrs_per_target_advance": (
                len(merge_events) / len(merge_events) if merge_events else 0
            ),
        }


def load_metrics(path: str | Path) -> list[dict[str, Any]]:
    """Load metrics from an NDJSON file."""
    events: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def compute_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary from loaded events (same logic as MetricsCollector.summary)."""
    collector = MetricsCollector(events=events)
    return collector.summary()


def _percentile(values: list[int], pct: int) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    idx = int(len(sorted_values) * pct / 100)
    idx = min(idx, len(sorted_values) - 1)
    return sorted_values[idx]
