"""Log adapters: one product's log dialect in, neutral records out.

Both shipped adapters read qontract-reconcile housekeeping logs, in the two
dialects the pipeline grew up on. An external log needs one new module here
that produces `mqsim.logrecord.LogRecord`, and both `analyze_logs.py` and
`calibrate_from_housekeeping_logs.py` then read it.

`docs/log-format.md` documents the record; `docs/logs-to-scenario.md` walks
the stages that consume it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from mqsim.logrecord import LogSource, read_ndjson

from . import housekeeping_json, housekeeping_text

ADAPTERS: dict[str, Callable[[Path], LogSource]] = {
    "records": read_ndjson,
    housekeeping_json.DIALECT: housekeeping_json.read,
    housekeeping_text.DIALECT: housekeeping_text.read,
}

DIALECTS = tuple(ADAPTERS)


def detect(path: Path) -> str:
    """Name the dialect of a log from its first meaningful bytes.

    An NDJSON record file and a JSON array both start with JSON, so the two
    are told apart by container: `[` opens the export array, `{` opens a
    record. Anything else is read as pod text.
    """
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("["):
                # A pod log line also starts with '[' — '[2026-01-01 ...]'.
                # The export array is JSON; a log line is not.
                try:
                    json.loads(stripped)
                except json.JSONDecodeError:
                    if stripped.startswith("[{") or stripped == "[":
                        return housekeeping_json.DIALECT
                    return housekeeping_text.DIALECT
                return housekeeping_json.DIALECT
            if stripped.startswith("{"):
                return "records"
            return housekeeping_text.DIALECT
    raise ValueError(f"{path} is empty")


def read_log(path: Path, dialect: str = "auto") -> LogSource:
    """Read a log into neutral records, detecting the dialect by default."""
    if dialect == "auto":
        dialect = detect(path)
    try:
        adapter = ADAPTERS[dialect]
    except KeyError:
        raise ValueError(
            f"unknown log dialect {dialect!r}; expected one of "
            f"{', '.join(DIALECTS)} or auto"
        ) from None
    return adapter(path)
