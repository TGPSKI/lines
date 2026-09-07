"""The N/A guard: which blank cells mean a dead server, and which mean OMM."""

from __future__ import annotations

import pytest
from tune_prod_calibration import _extract_run_metrics

REQUIRED = {
    "Throughput (merges/hour)": "5.085",
    "Throughput Active (merges/hour)": "5.000",
    "Throughput Peak8 (merges/hour)": "5.000",
    "Throughput Peak8 p90 (merges/hour)": "5.000",
    "MRs Merged": "5",
    "Rebase/Merge Ratio": "2.600",
    "Merge Interval p95 (seconds)": "1020.000",
}

# run_standalone renders these whenever omm is absent from the compared set,
# which is every policy set the calibration tuner uses.
OMM_ROWS = [
    "OMM Groups Formed",
    "OMM Groups Completed",
    "OMM Groups Destroyed %",
    "OMM Avg Group Size",
    "OMM Max Group Size",
    "OMM Window Expired",
    "OMM Skip-CI Rebases",
    "OMM Pending Ejected",
]


def _table(rows: dict[str, str]) -> str:
    lines = ["| Metric                       | active-cap |"]
    lines += [f"| {label:28} | {value:>10} |" for label, value in rows.items()]
    return "\n".join(lines)


def _comparison_output(**overrides: str) -> str:
    rows = dict(REQUIRED) | {label: "N/A" for label in OMM_ROWS}
    rows.update(overrides)
    return _table(rows)


def test_omm_group_stats_do_not_read_as_a_failed_run() -> None:
    parsed = _extract_run_metrics(_comparison_output())

    assert parsed is not None
    assert parsed["throughput_mph"] == pytest.approx(5.085)
    assert parsed["merged"] == pytest.approx(5)
    assert parsed["rebase_per_merge"] == pytest.approx(2.6)
    assert parsed["merge_interval_p95_seconds"] == pytest.approx(1020.0)


@pytest.mark.parametrize("label", sorted(REQUIRED))
def test_a_required_metric_reading_na_stays_retriable(label: str) -> None:
    assert _extract_run_metrics(_comparison_output(**{label: "N/A"})) is None


def test_a_required_metric_absent_altogether_is_a_hard_error() -> None:
    rows = dict(REQUIRED) | {label: "N/A" for label in OMM_ROWS}
    del rows["MRs Merged"]

    with pytest.raises(RuntimeError, match="MRs Merged"):
        _extract_run_metrics(_table(rows))
