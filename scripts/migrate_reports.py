#!/usr/bin/env python3
"""Copy a reports tree, dropping api_call events from the metrics NDJSON.

api_call events are the bulk of a run's bytes and no reader needs them
individually: the UI filters them out before parsing, and report.py only ever
counts them per endpoint. This writes one api_call_summary event carrying those
counts, so the reports keep their API Call Summary section without the events.

Everything else is copied verbatim. The source tree is never modified.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from fnmatch import fnmatch
from pathlib import Path


def migrate_ndjson(src: Path, dest: Path) -> tuple[int, int]:
    """Write src to dest without api_call events. Returns (bytes in, bytes out)."""
    endpoints: dict[str, int] = {}
    total = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(src) as fh, open(dest, "w") as out:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                out.write(line)
                continue
            if event.get("event") == "api_call":
                endpoint = event.get("endpoint", "unknown")
                endpoints[endpoint] = endpoints.get(endpoint, 0) + 1
                total += 1
                continue
            out.write(line)
        if total:
            out.write(
                json.dumps(
                    {
                        "event": "api_call_summary",
                        "total": total,
                        "endpoints": endpoints,
                    }
                )
                + "\n"
            )
    return src.stat().st_size, dest.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path, help="Reports tree to read")
    parser.add_argument("--out", type=Path, required=True, help="Destination tree")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="GLOB",
        help="Only migrate paths matching this glob, relative to src (repeatable)",
    )
    parser.add_argument(
        "--drop-logs",
        action="store_true",
        help="Skip *.log; they duplicate the api_call events and are usually the "
        "largest thing in the tree",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report, write nothing")
    args = parser.parse_args()

    src_root: Path = args.src.expanduser().resolve()
    out_root: Path = args.out.expanduser().resolve()
    if not src_root.is_dir():
        raise SystemExit(f"not a directory: {src_root}")
    if out_root == src_root or out_root.is_relative_to(src_root):
        raise SystemExit("--out must be outside the source tree")

    bytes_in = bytes_out = 0
    migrated = copied = skipped = 0

    for path in sorted(src_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src_root)
        if args.only and not any(fnmatch(str(rel), pat) for pat in args.only):
            continue
        if args.drop_logs and path.suffix == ".log":
            skipped += 1
            bytes_in += path.stat().st_size
            continue

        dest = out_root / rel
        if path.suffix == ".ndjson":
            if args.dry_run:
                bytes_in += path.stat().st_size
                migrated += 1
                continue
            size_in, size_out = migrate_ndjson(path, dest)
            bytes_in += size_in
            bytes_out += size_out
            migrated += 1
        else:
            size = path.stat().st_size
            bytes_in += size
            bytes_out += size
            copied += 1
            if not args.dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)

    mb = 1024 * 1024
    print(f"ndjson migrated : {migrated}")
    print(f"files copied    : {copied}")
    if args.drop_logs:
        print(f"logs skipped    : {skipped}")
    print(f"bytes in        : {bytes_in / mb:.0f} MB")
    if not args.dry_run:
        print(f"bytes out       : {bytes_out / mb:.0f} MB")
        if bytes_in:
            print(f"reduction       : {100 * (1 - bytes_out / bytes_in):.1f}%")
        print(f"written to      : {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
