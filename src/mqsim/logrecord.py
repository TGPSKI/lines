"""The log record every ingest path consumes.

Stage 1 (`scripts/analyze_logs.py`) and stage 2
(`scripts/calibrate_from_housekeeping_logs.py`) were written against two
different qontract-reconcile log dialects. Both reduce to the records below —
a timestamp, one of seven signals, and the merge request it happened to. An
adapter turns a log into records; every stage downstream reads records only,
so an external log needs one adapter rather than two regex dialects.

Serialised form is NDJSON, one object per line:

    {"ts": "2026-05-06T13:01:07Z", "event": "merge",
     "project": "example/queue-lab", "iid": 1200}

`docs/log-format.md` documents the same shape for a reader who is producing
records rather than consuming them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

# Every signal the pipeline consumes.
#
#   cycle         a reconcile loop started; the spacing sets tick length
#   merge         a merge was attempted (counted as a success unless a
#                 merge_failure for the same iid follows)
#   merge_failure the most recent merge attempt for that iid did not land
#   rebase        a rebase was requested
#   rebase_limit  the loop hit its per-cycle rebase cap; reported, not modelled
#   label_change  a priority label was added or removed; counted as an arrival
#   close         the merge request was closed without merging
EVENTS: frozenset[str] = frozenset(
    {
        "cycle",
        "merge",
        "merge_failure",
        "rebase",
        "rebase_limit",
        "label_change",
        "close",
    }
)

# Events that must name a merge request. label_change and close carry an iid
# when the log supplies one, but nothing downstream reads it: they are counted
# as arrivals and activity by hour, not tracked per merge request.
IID_EVENTS: frozenset[str] = frozenset({"merge", "merge_failure", "rebase"})

# Events the --project filter applies to. merge_failure is matched by iid
# against a pending merge, because the dialect that reports it names no
# project on the failure line.
PROJECT_EVENTS: frozenset[str] = frozenset({"merge", "rebase", "label_change", "close"})


def parse_ts(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp into a naive UTC datetime.

    A timestamp with no offset is read as UTC. Everything downstream does
    naive datetime arithmetic, so offsets are resolved here or nowhere.
    """
    try:
        ts = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cannot parse timestamp {raw!r}") from exc
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC).replace(tzinfo=None)
    return ts


@dataclass(frozen=True, slots=True)
class LogRecord:
    """One signal, at one time, about at most one merge request."""

    ts: datetime
    event: str
    project: str | None = None
    iid: int | None = None
    # Which policy emitted a rebase. Optional, and only stage 1 reads it: it is
    # how algorithm windows are detected. Records without it produce a single
    # window named "unknown".
    policy: str | None = None

    def to_json(self) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "ts": self.ts.isoformat(timespec="milliseconds") + "Z",
            "event": self.event,
        }
        if self.project is not None:
            obj["project"] = self.project
        if self.iid is not None:
            obj["iid"] = self.iid
        if self.policy is not None:
            obj["policy"] = self.policy
        return obj


def record_from_json(obj: Mapping[str, Any], *, where: str = "") -> LogRecord:
    """Build a record from one decoded NDJSON object, or raise ValueError."""
    prefix = f"{where}: " if where else ""
    if not isinstance(obj, Mapping):
        raise ValueError(f"{prefix}record must be a JSON object")

    event = obj.get("event")
    if event not in EVENTS:
        raise ValueError(
            f"{prefix}unknown event {event!r}; expected one of "
            f"{', '.join(sorted(EVENTS))}"
        )

    if "ts" not in obj:
        raise ValueError(f"{prefix}record has no 'ts'")
    try:
        ts = parse_ts(obj["ts"])
    except ValueError as exc:
        raise ValueError(f"{prefix}{exc}") from exc

    iid = obj.get("iid")
    if event in IID_EVENTS and iid is None:
        raise ValueError(f"{prefix}{event} record has no 'iid'")
    if iid is not None:
        try:
            iid = int(iid)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix}iid {iid!r} is not an integer") from exc

    project = obj.get("project")
    if event in PROJECT_EVENTS and not project:
        raise ValueError(
            f"{prefix}{event} record has no 'project'; the calibration "
            "--project filter matches on it"
        )

    policy = obj.get("policy")
    return LogRecord(ts=ts, event=event, project=project, iid=iid, policy=policy)


@dataclass
class LogSource:
    """The records one log yielded, plus what the source itself reported."""

    path: Path
    dialect: str
    # Timestamped entries the adapter examined, whether or not they matched.
    # `entries` far above `len(records)` means the adapter is missing the
    # format, and every number downstream describes only the part that matched.
    entries: int
    records: list[LogRecord]
    # First and last timestamp seen in the source, including lines that
    # produced no record. window_hours is measured across these when the
    # adapter reports them, and across the records themselves when it does not.
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    def __post_init__(self) -> None:
        if self.first_ts is None and self.records:
            self.first_ts = min(r.ts for r in self.records)
        if self.last_ts is None and self.records:
            self.last_ts = max(r.ts for r in self.records)


def iter_ndjson(path: Path) -> Iterator[LogRecord]:
    """Read neutral records from an NDJSON file. Blank lines are skipped."""
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: {exc.msg}") from exc
            yield record_from_json(obj, where=f"{path}:{lineno}")


def read_ndjson(path: Path) -> LogSource:
    records = list(iter_ndjson(path))
    return LogSource(
        path=path, dialect="records", entries=len(records), records=records
    )


def write_ndjson(records: Iterable[LogRecord], fh: TextIO) -> int:
    """Write records as NDJSON. Returns the number written."""
    written = 0
    for record in records:
        fh.write(json.dumps(record.to_json(), separators=(",", ":")) + "\n")
        written += 1
    return written
