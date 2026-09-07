"""Example adapter: qontract-reconcile housekeeping logs, pod text dialect.

The shape a `kubectl logs` capture produces — one timestamped line each,
`[<ts>] [INFO] ... - <message>`, where a dry-run message carries the action
tuple `['<action>', '<project>', <iid>]`.

This is an example. It reads one product's logs; an external log needs a
sibling of this file, not a change to it. What it has to produce is
`mqsim.logrecord.LogRecord` — see `docs/log-format.md`.

One divergence from the parser this replaces: an action tuple whose name is
none of merge, rebase, add_label, remove_label or close_item is dropped rather
than counted as generic project activity. Those tuples only ever fed the
cycle-length fallback, and only for logs carrying no `using gql endpoint`
lines at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from mqsim.logrecord import LogRecord, LogSource, parse_ts

DIALECT = "housekeeping-text"

LINE_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+"
    r"\[(?P<lvl>INFO|ERROR)\].*?- (?P<msg>.*)$"
)
GQL_RE = re.compile(r"using gql endpoint")
ACTION_RE = re.compile(
    r"\['(?P<action>[^']+)',\s*'(?P<project>[^']+)'(?:,\s*(?P<iid>\d+)\])?"
)
MERGE_ERR_RE = re.compile(r"unable to merge\s+(\d+):")
REBASE_LIMIT_RE = re.compile(r"rebase limit reached for this reconcile loop")

ACTION_EVENTS: dict[str, str] = {
    "merge": "merge",
    "rebase": "rebase",
    "add_label": "label_change",
    "remove_label": "label_change",
    "close_item": "close",
}


def read(path: Path) -> LogSource:
    records: list[LogRecord] = []
    entries = 0
    first_ts = last_ts = None

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = LINE_RE.match(line)
            if not m:
                continue
            ts = parse_ts(m.group("ts"))
            entries += 1
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            msg = m.group("msg")

            if GQL_RE.search(msg):
                records.append(LogRecord(ts=ts, event="cycle"))

            action_m = ACTION_RE.search(msg)
            if action_m:
                event = ACTION_EVENTS.get(action_m.group("action"))
                raw_iid = action_m.group("iid")
                if event is None:
                    continue
                if event in {"merge", "rebase"} and raw_iid is None:
                    # An unterminated tuple names no merge request; the parser
                    # this replaces did not count it either.
                    continue
                records.append(
                    LogRecord(
                        ts=ts,
                        event=event,
                        project=action_m.group("project"),
                        iid=int(raw_iid) if raw_iid is not None else None,
                    )
                )
                continue

            err_m = MERGE_ERR_RE.search(msg)
            if err_m:
                records.append(
                    LogRecord(ts=ts, event="merge_failure", iid=int(err_m.group(1)))
                )
                continue

            if REBASE_LIMIT_RE.search(msg):
                records.append(LogRecord(ts=ts, event="rebase_limit"))

    return LogSource(
        path=path,
        dialect=DIALECT,
        entries=entries,
        records=records,
        first_ts=first_ts,
        last_ts=last_ts,
    )
