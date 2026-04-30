"""Tests for metrics collection."""

import json
import tempfile
from pathlib import Path

from gitlab_hk_sim.metrics import MetricsCollector, compute_summary, load_metrics


class TestMetricsCollector:
    def test_record_event(self):
        mc = MetricsCollector()
        mc.record({"event": "rebase", "mr_iid": 1})
        assert len(mc.events) == 1
        assert mc.events[0]["event"] == "rebase"
        assert "timestamp" in mc.events[0]

    def test_write_ndjson(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
            path = f.name

        mc = MetricsCollector()
        mc.open_file(path)
        mc.record({"event": "rebase", "mr_iid": 1})
        mc.record(
            {
                "event": "tick",
                "tick": 1,
                "active_pipelines": 2,
                "same_root_success_pool": 1,
                "stale_successes": 0,
            }
        )
        mc.close()

        lines = Path(path).read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "rebase"
        assert json.loads(lines[1])["event"] == "tick"

    def test_summary_counts(self):
        mc = MetricsCollector()
        mc.record({"event": "rebase", "mr_iid": 1})
        mc.record({"event": "rebase", "mr_iid": 1})
        mc.record({"event": "rebase", "mr_iid": 2})
        mc.record(
            {
                "event": "merge",
                "mr_iid": 1,
                "new_target_head": "t-002",
                "stale_successes_created": 2,
            }
        )
        mc.record(
            {
                "event": "tick",
                "tick": 1,
                "active_pipelines": 3,
                "same_root_success_pool": 2,
                "stale_successes": 1,
            }
        )
        mc.record(
            {
                "event": "tick",
                "tick": 2,
                "active_pipelines": 1,
                "same_root_success_pool": 1,
                "stale_successes": 0,
            }
        )

        s = mc.summary()
        assert s["rebase_calls"] == 3
        assert s["merge_calls"] == 1
        assert s["peak_active_pipelines"] == 3
        assert s["duplicate_rebases_per_mr"] == {1: 2}
        assert s["duplicate_rebase_total"] == 1
        assert s["total_stale_successes_from_merges"] == 2


class TestLoadMetrics:
    def test_load_and_compute(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
            f.write(json.dumps({"event": "rebase", "mr_iid": 1}) + "\n")
            f.write(
                json.dumps(
                    {
                        "event": "tick",
                        "tick": 1,
                        "active_pipelines": 2,
                        "same_root_success_pool": 1,
                        "stale_successes": 0,
                    }
                )
                + "\n"
            )
            path = f.name

        events = load_metrics(path)
        assert len(events) == 2

        s = compute_summary(events)
        assert s["rebase_calls"] == 1
        assert s["total_ticks"] == 1
