"""Tests for the shared run summariser.

These are the first tests to reach the metrics path at all: it used to live
inside run_policy, which needs a live simulator, so none of its ~40 metrics
could be exercised from a fixture.
"""

from mqsim.summary import derive_run_shape, summarize_run, wait_time_metrics


def build_events(merge_ticks, *, last_tick=10, tick_seconds=60, arrivals=None):
    """A minimal event stream of the shape glab_api writes."""
    events = [
        {
            "event": "scenario_meta",
            "tick": 0,
            "tick_seconds": tick_seconds,
            "total_mrs": 20,
            "arrivals": arrivals or [],
        }
    ]
    for i, tick in enumerate(merge_ticks):
        events.append({"event": "merge", "tick": tick, "mr_iid": i + 1})
    for tick in range(last_tick + 1):
        events.append({"event": "tick", "tick": tick, "arrivals": []})
    return events


class TestDeriveRunShape:
    def test_total_ticks_is_max_not_count(self):
        """The loop advances between cycles, so a run ending at tick 9 is 9."""
        shape = derive_run_shape(build_events([2], last_tick=9))
        assert shape["total_time_ticks"] == 9

    def test_merge_cycles_counts_distinct_ticks(self):
        shape = derive_run_shape(build_events([3, 3, 3, 7]))
        assert shape["merge_cycles"] == 2

    def test_reads_tick_seconds_from_scenario_meta(self):
        shape = derive_run_shape(build_events([1], tick_seconds=30))
        assert shape["tick_seconds"] == 30

    def test_missing_scenario_meta_does_not_raise(self):
        shape = derive_run_shape([{"event": "tick", "tick": 4}])
        assert shape["total_time_ticks"] == 4
        assert shape["tick_seconds"] == 60


class TestWaitTimes:
    def test_wait_is_merge_tick_minus_arrival(self):
        events = build_events([10], arrivals=[{"iid": 1, "tick": 4}])
        assert wait_time_metrics(events)["wait_max"] == 6

    def test_mrs_without_an_arrival_entry_start_at_zero(self):
        assert wait_time_metrics(build_events([8]))["wait_max"] == 8

    def test_starved_counts_waits_over_100(self):
        events = build_events([50, 150, 300], last_tick=300)
        assert wait_time_metrics(events)["starved_mrs"] == 2

    def test_no_merges_gives_zeros_not_an_error(self):
        assert wait_time_metrics(build_events([]))["wait_p95"] == 0


class TestSummarizeRun:
    def test_unreached_threshold_is_none_not_the_run_length(self):
        """Four merges cannot answer "when did the tenth merge happen"."""
        summary = summarize_run(build_events([1, 2, 3, 4], last_tick=99))
        assert summary["time_to_merge_10"] is None
        assert summary["time_to_first_merge"] == 1

    def test_reached_threshold_reports_its_tick(self):
        summary = summarize_run(build_events(list(range(1, 12)), last_tick=99))
        assert summary["time_to_merge_10"] == 10

    def test_no_merges_leaves_both_thresholds_unset(self):
        summary = summarize_run(build_events([]))
        assert summary["time_to_first_merge"] is None
        assert summary["time_to_merge_10"] is None

    def test_throughput_divides_by_total_ticks(self):
        summary = summarize_run(build_events([1, 2], last_tick=10))
        assert summary["mrs_merged"] == 2
        assert summary["throughput_merges_per_tick"] == 0.2

    def test_queue_drain_uses_the_scenario_population(self):
        """20 total MRs in the fixture, so 5 merged is 25%, never above 100."""
        summary = summarize_run(build_events([1, 2, 3, 4, 5]))
        assert summary["queue_drain_pct"] == 25.0

    def test_is_pure(self):
        """Same events in, same summary out, with no server or file access."""
        events = build_events([2, 5, 9])
        assert summarize_run(events) == summarize_run(events)
