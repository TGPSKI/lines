#!/usr/bin/env python3
"""Convert a log to neutral records, and report what the adapter found.

    python scripts/convert_log.py --input pod.log --output records.ndjson

Both `analyze_logs.py` and `calibrate_from_housekeeping_logs.py` read the
output. Run this first on an unfamiliar log: the counts it prints are the
check that the adapter matched the format, before any number downstream
describes only the fraction that happened to parse.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mqsim.adapters import DIALECTS, read_log  # noqa: E402
from mqsim.logrecord import write_ndjson  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", required=True, help="Log file to convert")
    parser.add_argument(
        "--output",
        "-o",
        default="-",
        help="NDJSON output path, or - for stdout (default)",
    )
    parser.add_argument(
        "--log-dialect",
        default="auto",
        choices=["auto", *DIALECTS],
        help="Log dialect to read. Default: detect from the file",
    )
    args = parser.parse_args()

    source = read_log(Path(args.input), args.log_dialect)

    if args.output == "-":
        write_ndjson(source.records, sys.stdout)
    else:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            write_ndjson(source.records, fh)

    counts = Counter(r.event for r in source.records)
    projects = {r.project for r in source.records if r.project}
    print(f"{source.path} read as {source.dialect}", file=sys.stderr)
    print(
        f"  {source.entries:,} timestamped entries, {len(source.records):,} records",
        file=sys.stderr,
    )
    if source.first_ts and source.last_ts:
        hours = (source.last_ts - source.first_ts).total_seconds() / 3600
        print(
            f"  {source.first_ts:%Y-%m-%d %H:%M} → "
            f"{source.last_ts:%Y-%m-%d %H:%M} ({hours:.2f}h)",
            file=sys.stderr,
        )
    for event, count in sorted(counts.items()):
        print(f"  {event}: {count:,}", file=sys.stderr)
    for missing in sorted({"merge", "rebase", "cycle"} - set(counts)):
        print(f"  {missing}: 0 — see docs/log-format.md", file=sys.stderr)
    print(f"  projects: {', '.join(sorted(projects)) or 'none'}", file=sys.stderr)


if __name__ == "__main__":
    main()
