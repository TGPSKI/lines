#!/usr/bin/env python3
"""Add a run_summary event to metrics NDJSON files written before it existed.

Runs produced from now on carry their own summary. This computes the same
thing, with the same code, for older files — so a backfilled run and a fresh
one are indistinguishable to any reader.

Idempotent: a file that already has a run_summary is skipped, so this can be
re-run over a tree safely.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mqsim.summary import (  # noqa: E402
    append_run_summary,
    read_run_summary,
    summarize_run,
)


def load_events(path: Path) -> list[dict]:
    events = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path, help="Directory to walk for *.ndjson")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would change, write nothing"
    )
    args = parser.parse_args()

    root: Path = args.tree.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    added = skipped = empty = 0
    for path in sorted(root.rglob("*.ndjson")):
        events = load_events(path)
        if not events:
            empty += 1
            continue
        if read_run_summary(events) is not None:
            skipped += 1
            continue
        summary = summarize_run(events)
        if args.dry_run:
            added += 1
            continue
        if append_run_summary(str(path), summary):
            added += 1

    verb = "would add" if args.dry_run else "added"
    print(f"{verb} run_summary : {added}")
    print(f"already had one   : {skipped}")
    print(f"unreadable/empty  : {empty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
