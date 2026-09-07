#!/usr/bin/env python3
"""Promote a proofed scenario out of a calibration run, with its proof attached.

A calibration run writes selected-scenario.yaml when its gates pass, but the
evidence — what the run achieved, which gates it passed and by how much — stays
behind in the run's metadata.json. Copy the scenario by hand and you keep a file
that claims to represent a queue while the proof that it does lives somewhere
gitignored.

This copies the scenario and embeds the proof in metadata.proof, including the
worst single-dimension error. A scenario that passed on a relaxed gate is
useful; a scenario that hides which dimension it missed is not.

Refuses to promote a rejected run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def worst_dimension(errors: dict[str, float]) -> tuple[str, float]:
    if not errors:
        return ("", 0.0)
    name = max(errors, key=lambda k: float(errors[k]))
    return (name, float(errors[name]))


def build_proof(run_dir: Path, context: dict) -> dict:
    best_key = max(
        (k for k in context if k.startswith("best_")),
        key=lambda k: int(k.split("_")[1]) if k.split("_")[1].isdigit() else 0,
        default="",
    )
    best = context.get(best_key, {}) if best_key else {}
    errors = {k: float(v) for k, v in (best.get("dimension_error_pct") or {}).items()}
    name, value = worst_dimension(errors)
    return {
        "proofed_from": run_dir.name,
        "proofed_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "calibrated_policy": context.get("policy"),
        "project_filter": context.get("project"),
        "validated_over_cycles": best_key.removeprefix("best_") or None,
        "gates": context.get("decision_gates"),
        "thresholds": context.get("decision_thresholds"),
        "dimension_error_pct": errors,
        "worst_dimension": name,
        "worst_dimension_error_pct": round(value, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir", type=Path, help="A reports/calibration/<run> directory"
    )
    parser.add_argument("--name", required=True, help="Scenario name, without .yaml")
    parser.add_argument(
        "--out-dir", type=Path, default=ROOT / "scenarios", help="Where to write"
    )
    parser.add_argument(
        "--force-rejected",
        action="store_true",
        help="Promote a run whose gates failed. The proof records that it failed",
    )
    args = parser.parse_args()

    run_dir: Path = args.run_dir.expanduser().resolve()
    scenario_path = run_dir / "selected-scenario.yaml"
    meta_path = run_dir / "metadata.json"

    if not meta_path.exists():
        raise SystemExit(f"no metadata.json in {run_dir}")
    context = json.loads(meta_path.read_text()).get("context", {})
    accepted = bool((context.get("decision_gates") or {}).get("accepted"))

    if not scenario_path.exists():
        rejected = run_dir / "rejected-scenario.yaml"
        if rejected.exists() and args.force_rejected:
            scenario_path = rejected
        elif rejected.exists():
            print(f"This run was rejected: {context.get('decision_reason_codes')}")
            print("Read why before promoting. --force-rejected records the failure.")
            return 1
        else:
            raise SystemExit(f"no scenario to promote in {run_dir}")

    scenario = yaml.safe_load(scenario_path.read_text())
    scenario.setdefault("metadata", {})["proof"] = build_proof(run_dir, context)
    if not accepted:
        scenario["metadata"]["proof"]["accepted"] = False

    out = args.out_dir / f"{args.name}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(scenario, sort_keys=False, width=100))

    proof = scenario["metadata"]["proof"]
    print(f"wrote {out}")
    print(f"  calibrated policy   : {proof['calibrated_policy']}")
    print(f"  gates               : {'passed' if accepted else 'FAILED'}")
    if proof["worst_dimension"]:
        print(
            f"  worst dimension     : {proof['worst_dimension']} "
            f"off by {proof['worst_dimension_error_pct']}%"
        )
    print("\nQuote the worst dimension whenever you quote results from this scenario.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
