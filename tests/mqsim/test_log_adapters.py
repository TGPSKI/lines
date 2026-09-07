"""The adapter seam: one record shape, whatever dialect the log arrived in."""

from __future__ import annotations

import dataclasses
import io
import json
from datetime import datetime
from pathlib import Path

import pytest
from analyze_logs import ingest
from calibrate_from_housekeeping_logs import parse_log

from mqsim.adapters import detect, read_log
from mqsim.logrecord import (
    LogRecord,
    read_ndjson,
    record_from_json,
    write_ndjson,
)

SAMPLE = Path(__file__).resolve().parents[2] / "docs/sample-housekeeping-log.json"


def _text_line(clock: str, level: str, msg: str) -> str:
    return f"[2026-05-06 {clock}] [{level}] reconcile.gitlab_housekeeping - {msg}"


GQL = "using gql endpoint https://example.invalid/graphql"
REBASE_LIMIT = "rebase limit reached for this reconcile loop"

TEXT_LOG = (
    "\n".join(
        [
            _text_line("13:00:00", "INFO", GQL),
            _text_line("13:00:05", "INFO", "['rebase', 'example/queue-lab', 1200]"),
            _text_line("13:00:06", "INFO", "['add_label', 'example/queue-lab', 1201]"),
            _text_line("13:00:07", "INFO", "['close_item', 'example/queue-lab', 1202]"),
            _text_line("13:00:08", "INFO", "nothing structured here"),
            _text_line("13:01:30", "INFO", GQL),
            _text_line("13:01:35", "INFO", "['merge', 'example/queue-lab', 1200]"),
            _text_line("13:01:36", "INFO", "['merge', 'other/project', 7]"),
            _text_line("13:01:40", "ERROR", "unable to merge 1200: 405 Not Allowed"),
            _text_line("13:01:45", "INFO", REBASE_LIMIT),
        ]
    )
    + "\n"
)


def _json_entry(ts: str, message: str) -> dict[str, str]:
    return {"@timestamp": ts, "message": message}


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def test_json_adapter_reads_the_shipped_sample() -> None:
    source = read_log(SAMPLE)

    assert source.dialect == "housekeeping-json"
    assert source.entries == 268
    assert len(source.records) == 268
    assert sum(1 for r in source.records if r.event == "merge") == 60
    assert sum(1 for r in source.records if r.event == "rebase") == 208
    assert {r.project for r in source.records} == {"example/queue-lab"}
    assert {r.policy for r in source.records if r.event == "rebase"} == {"old-burst"}


def test_json_adapter_names_the_policy_that_emitted_each_rebase(
    tmp_path: Path,
) -> None:
    path = tmp_path / "log.json"
    path.write_text(
        json.dumps(
            [
                _json_entry(
                    "2026-05-06 13:00:00.000",
                    "[gitlab_housekeeping.py:_try_rebase:81] processing [ex/q, 5]",
                ),
                _json_entry(
                    "2026-05-06 13:00:01.000",
                    "[gitlab_housekeeping.py:rebase_merge_requests:733] "
                    "processing [ex/q, 6]",
                ),
            ]
        )
    )

    policies = [r.policy for r in read_log(path).records]

    assert policies == ["active-cap", "old-burst"]


def test_json_adapter_keeps_the_rebase_cap_out_of_per_mr_counts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "log.json"
    path.write_text(
        json.dumps(
            [
                _json_entry(
                    "2026-05-06 13:00:00.000",
                    "[gitlab_housekeeping.py:rebase_merge_requests:740] "
                    "rebase limit reached for this reconcile loop",
                )
            ]
        )
    )

    records = read_log(path).records

    assert [(r.event, r.iid) for r in records] == [("rebase_limit", None)]


def test_text_adapter_reads_every_signal(tmp_path: Path) -> None:
    path = tmp_path / "pod.log"
    path.write_text(TEXT_LOG)

    source = read_log(path)

    assert source.dialect == "housekeeping-text"
    assert source.entries == 10
    assert [(r.event, r.project, r.iid) for r in source.records] == [
        ("cycle", None, None),
        ("rebase", "example/queue-lab", 1200),
        ("label_change", "example/queue-lab", 1201),
        ("close", "example/queue-lab", 1202),
        ("cycle", None, None),
        ("merge", "example/queue-lab", 1200),
        ("merge", "other/project", 7),
        ("merge_failure", None, 1200),
        ("rebase_limit", None, None),
    ]


def test_text_adapter_window_spans_lines_that_produced_no_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pod.log"
    path.write_text(TEXT_LOG)

    source = read_log(path)

    # 13:00:08 and 13:01:45 carry no merge request, but they are still the
    # log's own bounds, and window_hours is measured across them.
    assert source.first_ts == datetime(2026, 5, 6, 13, 0, 0)
    assert source.last_ts == datetime(2026, 5, 6, 13, 1, 45)


# ---------------------------------------------------------------------------
# The record itself
# ---------------------------------------------------------------------------


def test_records_round_trip_through_ndjson(tmp_path: Path) -> None:
    original = read_log(SAMPLE).records
    path = tmp_path / "records.ndjson"
    with path.open("w") as fh:
        assert write_ndjson(original, fh) == len(original)

    assert read_ndjson(path).records == original


def test_detect_names_each_dialect(tmp_path: Path) -> None:
    text = tmp_path / "pod.log"
    text.write_text(TEXT_LOG)
    records = tmp_path / "records.ndjson"
    with records.open("w") as fh:
        write_ndjson([LogRecord(datetime(2026, 5, 6), "cycle")], fh)

    assert detect(SAMPLE) == "housekeeping-json"
    assert detect(text) == "housekeeping-text"
    assert detect(records) == "records"


def test_naive_and_offset_timestamps_land_on_the_same_instant() -> None:
    naive = record_from_json({"ts": "2026-05-06T13:00:00", "event": "cycle"})
    offset = record_from_json({"ts": "2026-05-06T15:00:00+02:00", "event": "cycle"})

    assert naive.ts == offset.ts


@pytest.mark.parametrize(
    ("obj", "expected"),
    [
        ({"ts": "2026-05-06T13:00:00", "event": "merged"}, "unknown event"),
        ({"event": "cycle"}, "no 'ts'"),
        ({"ts": "last tuesday", "event": "cycle"}, "cannot parse timestamp"),
        ({"ts": "2026-05-06T13:00:00", "event": "merge", "project": "e/q"}, "no 'iid'"),
        ({"ts": "2026-05-06T13:00:00", "event": "merge", "iid": 5}, "no 'project'"),
    ],
)
def test_a_record_that_cannot_be_used_says_why(obj: dict, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        record_from_json(obj, where="records.ndjson:1")


def test_write_ndjson_omits_fields_the_record_does_not_carry() -> None:
    buf = io.StringIO()
    write_ndjson([LogRecord(datetime(2026, 5, 6, 13), "cycle")], buf)

    assert json.loads(buf.getvalue()) == {
        "ts": "2026-05-06T13:00:00.000Z",
        "event": "cycle",
    }


# ---------------------------------------------------------------------------
# What the stages make of the records
# ---------------------------------------------------------------------------


def test_calibration_reads_the_same_log_through_either_dialect(
    tmp_path: Path,
) -> None:
    text = tmp_path / "pod.log"
    text.write_text(TEXT_LOG)
    records = tmp_path / "records.ndjson"
    with records.open("w") as fh:
        write_ndjson(read_log(text).records, fh)

    from_text = parse_log(text, "example/queue-lab")
    from_records = parse_log(records, "example/queue-lab")

    ignored = {"path"}
    assert {
        f.name: getattr(from_text, f.name)
        for f in dataclasses.fields(from_text)
        if f.name not in ignored
    } == {
        f.name: getattr(from_records, f.name)
        for f in dataclasses.fields(from_records)
        if f.name not in ignored
    }


def test_calibration_counts_a_failed_merge_against_its_iid(tmp_path: Path) -> None:
    path = tmp_path / "pod.log"
    path.write_text(TEXT_LOG)

    stats = parse_log(path, "example/queue-lab")

    assert stats.merge_attempts == 1
    assert stats.merge_failures == 1
    assert stats.rebase_limit_hits == 1
    assert stats.cycles == 2


def test_calibration_ignores_projects_the_caller_did_not_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pod.log"
    path.write_text(TEXT_LOG)

    assert parse_log(path, "other/project").merge_attempts == 1
    assert parse_log(path, "no/such-project").merge_attempts == 0


def test_analysis_reports_one_unknown_window_when_records_name_no_policy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.ndjson"
    with path.open("w") as fh:
        write_ndjson(
            [
                LogRecord(datetime(2026, 5, 6, 13), "rebase", "ex/q", 5),
                LogRecord(datetime(2026, 5, 6, 14), "merge", "ex/q", 5),
            ],
            fh,
        )

    parsed = ingest(path)

    assert [w.name for w in parsed.windows] == ["unknown"]
    assert parsed.raw_entries == 2


def test_analysis_detects_a_policy_switch_from_the_records(tmp_path: Path) -> None:
    path = tmp_path / "records.ndjson"
    with path.open("w") as fh:
        write_ndjson(
            [
                LogRecord(datetime(2026, 5, 6, 13), "rebase", "ex/q", 5, "old-burst"),
                LogRecord(datetime(2026, 5, 6, 14), "rebase", "ex/q", 6, "old-burst"),
                LogRecord(datetime(2026, 5, 6, 15), "rebase", "ex/q", 7, "active-cap"),
                LogRecord(datetime(2026, 5, 6, 16), "rebase", "ex/q", 8, "active-cap"),
            ],
            fh,
        )

    parsed = ingest(path)

    assert [w.name for w in parsed.windows] == ["old-burst", "active-cap"]
