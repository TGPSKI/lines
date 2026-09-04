#!/usr/bin/env python3
"""Verify or re-download the pinned third-party UI assets in ui/vendor/.

The UI loads Chart.js, js-yaml, and nine d3 modules from ui/vendor/ so that
opening ui/index.html needs no network access. ui/vendor/MANIFEST.txt pins each
file to a version and SHA-384; this script is the only thing that writes them.

Uses the standard library only, so it runs without the project virtualenv.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import urllib.request
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parents[1] / "ui" / "vendor"
MANIFEST = VENDOR_DIR / "MANIFEST.txt"


def read_manifest() -> list[tuple[str, str, str]]:
    entries = []
    for lineno, raw in enumerate(MANIFEST.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise SystemExit(f"{MANIFEST}:{lineno}: expected '<file> <sha384> <url>'")
        entries.append((parts[0], parts[1], parts[2]))
    return entries


def sha384_of(data: bytes) -> str:
    return base64.b64encode(hashlib.sha384(data).digest()).decode()


def fetch(url: str) -> bytes:
    if not url.startswith("https://"):
        raise SystemExit(f"refusing non-https vendor url: {url}")
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - https only
        return resp.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Re-download every manifest entry before verifying it",
    )
    args = parser.parse_args()

    failures = 0
    for name, expected, url in read_manifest():
        path = VENDOR_DIR / name
        if args.fetch:
            data = fetch(url)
            actual = sha384_of(data)
            if actual != expected:
                print(f"FAIL {name}: upstream hash {actual} != pinned {expected}")
                failures += 1
                continue
            path.write_bytes(data)
        elif not path.exists():
            print(f"FAIL {name}: missing (run 'make vendor')")
            failures += 1
            continue
        else:
            actual = sha384_of(path.read_bytes())
            if actual != expected:
                print(f"FAIL {name}: local hash {actual} != pinned {expected}")
                failures += 1
                continue
        print(f"ok   {name} sha384-{expected}")

    if failures:
        print(f"{failures} vendored asset(s) failed verification")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
