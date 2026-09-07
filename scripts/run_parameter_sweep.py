#!/usr/bin/env python3
"""Sweep one merge-queue parameter and emit the UI's Sweep tab document.

Two caps, easy to conflate. Sweeping one pins the other so a result
attributes to a single cause:

  --limit             rebase/merge attempts per cycle, and inside an OMM group
                      the merges after which the group is cleared (argparse
                      help, not "CI budget": steady-state CI concurrency is
                      emergent, see ADR-019)
  --omm-max-interval  minutes a group stays valid before it re-forms

There is no group-size axis: qontract-reconcile's _form_omm_group caps nothing,
so group membership is bounded only by tenant non-overlap.

Nothing is fabricated: every row is parsed from a real comparison run's own
table.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "run_standalone.py"

AXES: dict[str, dict] = {
    "limit": {
        "flag": "--limit",
        "label": "Rebase/merge limit",
        "unit": "",
        "values": [2, 4, 6, 8, 10],
        "pins": {"--omm-max-interval": "10"},
        "question": "how many rebases and merges per cycle a policy should attempt",
        # Every policy reads --limit, so this axis has no control policy.
        "reads": None,
    },
    "omm-max-interval": {
        "flag": "--omm-max-interval",
        "label": "OMM window",
        "unit": "m",
        "values": [2, 5, 10, 20, 40],
        "pins": {"--limit": "5"},
        "question": "how long a group stays valid before it must re-form",
        "reads": ["omm"],
    },
}

ROW = re.compile(r"^\|\s*(?P<label>[^|]+?)\s*\|(?P<rest>.+)\|$", re.MULTILINE)


def parse_comparison_table(output: str) -> dict[str, dict[str, float]]:
    """Read the runner's own comparison table back as policy -> metric -> value."""
    policies: list[str] = []
    table: dict[str, dict[str, float]] = {}
    for m in ROW.finditer(output):
        label = m.group("label").strip()
        # `rest` already excludes the trailing pipe; slicing off a last cell
        # here silently dropped the final policy column.
        cells = [c.strip() for c in m.group("rest").split("|")]
        if label == "Metric":
            policies = cells
            table = {p: {} for p in policies}
            continue
        if not policies or set(label) <= set("-─ "):
            continue
        for pol, cell in zip(policies, cells, strict=False):
            try:
                table[pol][label] = float(cell)
            except ValueError:
                continue
    if not policies:
        raise SystemExit("no comparison table in runner output")
    return table


def run_point(spec: dict, value, args, port: int) -> dict[str, dict[str, float]]:
    pins: list[str] = []
    for flag, pinned in spec["pins"].items():
        pins += [flag, pinned]
    cmd = [
        sys.executable,
        str(RUNNER),
        "--compare",
        "--policy-set",
        args.policy_set,
        "--scenario",
        args.scenario,
        "--port",
        str(port),
        spec["flag"],
        str(value),
        *pins,
        "--cycles",
        str(args.cycles),
        "--ticks-per-cycle",
        str(args.ticks_per_cycle),
        "--log-level",
        "WARNING",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-4000:])
        raise SystemExit(f"{spec['flag']} {value}: runner exited {proc.returncode}")
    return parse_comparison_table(proc.stdout)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--axis",
        choices=sorted(AXES),
        default="limit",
        help="which parameter to vary; the other is pinned",
    )
    ap.add_argument("--values", help="comma-separated override for the axis values")
    ap.add_argument("--scenario", default="scenarios/showcase-10h.yaml")
    ap.add_argument("--policy-set", default="phase1")
    ap.add_argument("--cycles", type=int, default=1200)
    ap.add_argument("--ticks-per-cycle", type=int, default=1)
    ap.add_argument("--base-port", type=int, default=8100)
    ap.add_argument("--out", required=True, help="sweep-data.json to write")
    args = ap.parse_args()

    spec = AXES[args.axis]
    values = [int(v) for v in args.values.split(",")] if args.values else spec["values"]

    points = []
    for i, v in enumerate(values):
        print(f"  {spec['flag']} {v} ...", flush=True)
        points.append(run_point(spec, v, args, args.base_port + i * 4))

    policies = list(points[0].keys())
    pins = ", ".join(f"{f.lstrip('-')} {v}" for f, v in spec["pins"].items())
    doc = {
        # `reads` names the policies that consult the swept flag. The rest are
        # controls: they cannot respond to it, so their spread across the axis
        # is this scenario's run-to-run noise floor, and any claimed effect
        # has to clear it.
        "axis": {
            "key": args.axis,
            "label": spec["label"],
            "unit": spec["unit"],
            "reads": spec["reads"],
        },
        "axis_values": values,
        "title": f"{spec['label']} Sweep — {len(policies)} policies "
        f"× {len(values)} values",
        "description": f"Answers {spec['question']}. Held fixed: {pins}.",
        "setup": {
            "scenario": Path(args.scenario).name,
            "swept": f"{spec['flag']} {values}",
            "pinned": pins,
            "cycles": args.cycles,
            "policy_set": args.policy_set,
        },
        # Readers that predate axis_values still key off `limits`.
        "limits": values,
        "policies": policies,
        "data": {p: [pt[p] for pt in points] for p in policies},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    print(f"wrote {out} ({len(policies)} policies x {len(values)} values)")


if __name__ == "__main__":
    main()
