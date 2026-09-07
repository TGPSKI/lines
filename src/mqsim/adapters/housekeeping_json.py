"""Example adapter: qontract-reconcile housekeeping logs, JSON export dialect.

The shape a CloudWatch Logs Insights export produces — a JSON array of
`{"@timestamp", "message"}` objects, where the message carries the emitting
`gitlab_housekeeping.py` function and a trailing `[<project>, <iid>]`.

This is an example. It reads one product's logs; an external log needs a
sibling of this file, not a change to it. What it has to produce is
`mqsim.logrecord.LogRecord` — see `docs/log-format.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mqsim.logrecord import LogRecord, LogSource, parse_ts

DIALECT = "housekeeping-json"

FUNC_RE = re.compile(r"gitlab_housekeeping\.py:(?P<func>\w+):(?P<line>\d+)\]")
# The merge request the line is about, at the very end of the message. A line
# with trailing text after the bracket names no specific MR — which is how
# "rebase limit reached" stays out of the per-MR counts.
TARGET_RE = re.compile(r"\[(?P<project>[^\[\]]+?),\s*(?P<iid>\d+)\]\s*$")
REBASE_LIMIT_RE = re.compile(r"rebase limit reached")

# The emitting function names the signal, and for a rebase also the policy
# that emitted it. Stage 1 detects algorithm windows from that policy.
FUNC_EVENTS: dict[str, tuple[str, str | None]] = {
    "merge_merge_requests": ("merge", None),
    "rebase_merge_requests": ("rebase", "old-burst"),
    "_try_rebase": ("rebase", "active-cap"),
}


def read(path: Path) -> LogSource:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(
            f"{path}: expected a JSON array of {{'@timestamp', 'message'}} "
            "objects, not a bare object"
        )

    records: list[LogRecord] = []
    entries = 0
    first_ts = last_ts = None

    for entry in data:
        raw_ts = entry.get("@timestamp")
        if raw_ts is None:
            continue
        ts = parse_ts(raw_ts)
        entries += 1
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts

        msg = entry.get("message", "")
        func_m = FUNC_RE.search(msg)
        if not func_m:
            continue
        mapped = FUNC_EVENTS.get(func_m.group("func"))
        if mapped is None:
            continue
        event, policy = mapped

        target = TARGET_RE.search(msg)
        if target is None:
            if event == "rebase" and REBASE_LIMIT_RE.search(msg):
                records.append(LogRecord(ts=ts, event="rebase_limit"))
            continue
        records.append(
            LogRecord(
                ts=ts,
                event=event,
                project=target.group("project").strip(),
                iid=int(target.group("iid")),
                policy=policy,
            )
        )

    return LogSource(
        path=path,
        dialect=DIALECT,
        entries=entries,
        records=records,
        first_ts=first_ts,
        last_ts=last_ts,
    )
