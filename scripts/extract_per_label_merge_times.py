#!/usr/bin/env python3
"""Extract per-priority-label time-to-merge breakdown from comparison NDJSON files."""

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, quantiles

LABEL_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4, "lgtm": 5}
TABLE_HEADER = (
    f"{'Policy':<14} {'Count':>5} {'Mean(s)':>8} {'Median(s)':>9}"
    f" {'P95(s)':>8} {'Min(s)':>7} {'Max(s)':>7}"
)
TABLE_RULE = f"{'─' * 14} {'─' * 5} {'─' * 8} {'─' * 9} {'─' * 8} {'─' * 7} {'─' * 7}"


def load_ndjson(path: Path) -> tuple[dict, list[dict]]:
    """Load NDJSON, return (scenario_meta, merge_events)."""
    meta = None
    merges = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("event") == "scenario_meta":
                meta = obj
            elif obj.get("event") == "merge":
                merges.append(obj)
    return meta, merges


def compute_label_breakdown(report_dir: Path, tick_seconds: int = 30) -> dict:
    """Compute per-label time-to-merge for all policies in a report directory."""
    policies = {}
    for ndjson_path in sorted(report_dir.glob("*-metrics.ndjson")):
        policy_name = ndjson_path.stem.replace("-metrics", "")
        meta, merges = load_ndjson(ndjson_path)
        if not meta:
            continue

        mr_catalog = meta.get("mr_catalog", {})
        arrivals_list = meta.get("arrivals", [])
        arrival_ticks = {a["iid"]: a["tick"] for a in arrivals_list}
        for iid_str in mr_catalog:
            iid = int(iid_str)
            if iid not in arrival_ticks:
                arrival_ticks[iid] = 0

        label_merges = defaultdict(list)
        for m in merges:
            iid = m["mr_iid"]
            merge_tick = m["tick"]
            arrival_tick = arrival_ticks.get(iid, 0)
            time_to_merge_ticks = merge_tick - arrival_tick
            time_to_merge_seconds = time_to_merge_ticks * tick_seconds

            iid_str = str(iid)
            priority_label = mr_catalog.get(iid_str, {}).get(
                "priority_label", "unknown"
            )
            priority_tier = priority_label.replace("bot/approved: ", "").replace(
                "bot/approved", "none"
            )

            label_merges[priority_tier].append(
                {
                    "iid": iid,
                    "time_to_merge_ticks": time_to_merge_ticks,
                    "time_to_merge_seconds": time_to_merge_seconds,
                }
            )

        policy_stats = {}
        for label, items in sorted(label_merges.items()):
            times_s = [x["time_to_merge_seconds"] for x in items]
            times_t = [x["time_to_merge_ticks"] for x in items]
            stats = {
                "count": len(items),
                "mean_seconds": round(mean(times_s), 1),
                "median_seconds": round(median(times_s), 1),
                "mean_ticks": round(mean(times_t), 1),
                "median_ticks": round(median(times_t), 1),
            }
            if len(times_s) >= 4:
                q = quantiles(times_s, n=20)
                stats["p95_seconds"] = round(q[18], 1)
            else:
                stats["p95_seconds"] = round(max(times_s), 1)
            stats["min_seconds"] = round(min(times_s), 1)
            stats["max_seconds"] = round(max(times_s), 1)
            policy_stats[label] = stats

        policies[policy_name] = policy_stats

    return policies


def print_table(policies: dict, report_name: str, tick_seconds: int = 30):
    """Print formatted comparison table."""
    print(f"\n{'=' * 80}")
    print(f"Per-Priority-Label Time-to-Merge Breakdown: {report_name}")
    print(f"{'=' * 80}")
    print(f"(tick = {tick_seconds}s, times in seconds)")

    all_labels = sorted(
        {label for stats in policies.values() for label in stats},
        key=lambda x: LABEL_ORDER.get(x, 9),
    )

    policy_names = sorted(policies.keys())

    for label in all_labels:
        print(f"\n--- Priority: {label} ---")
        print(TABLE_HEADER)
        print(TABLE_RULE)
        for pname in policy_names:
            s = policies[pname].get(label)
            if s:
                print(
                    f"{pname:<14} {s['count']:>5} {s['mean_seconds']:>8.1f}"
                    f" {s['median_seconds']:>9.1f} {s['p95_seconds']:>8.1f}"
                    f" {s['min_seconds']:>7.1f} {s['max_seconds']:>7.1f}"
                )
            else:
                print(f"{pname:<14} {'—':>5}")

    print("\n--- All labels combined ---")
    print(TABLE_HEADER)
    print(TABLE_RULE)
    for pname in policy_names:
        all_times = []
        for label_stats in policies[pname].values():
            n = label_stats["count"]
            all_times.extend([label_stats["mean_seconds"]] * n)
        if all_times:
            total_count = sum(s["count"] for s in policies[pname].values())
            all_s = []
            for label in all_labels:
                s = policies[pname].get(label)
                if s:
                    all_s.extend([s["mean_seconds"]] * s["count"])
            overall_mean = mean(all_s) if all_s else 0
            overall_med = median(all_s) if all_s else 0
            overall_max = max(s["max_seconds"] for s in policies[pname].values())
            overall_min = min(s["min_seconds"] for s in policies[pname].values())
            print(
                f"{pname:<14} {total_count:>5} {overall_mean:>8.1f}"
                f" {overall_med:>9.1f} {'—':>8} {overall_min:>7.1f}"
                f" {overall_max:>7.1f}"
            )


def output_json(all_results: dict, output_path: Path):
    """Write structured JSON for UI consumption."""
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nJSON written to: {output_path}")


def main():
    report_dirs = sys.argv[1:] if len(sys.argv) > 1 else []
    if not report_dirs:
        print(
            "Usage: extract_per_label_merge_times.py <report_dir> [<report_dir2> ...]"
        )
        sys.exit(1)

    all_results = {}
    for rd in report_dirs:
        report_path = Path(rd)
        if not report_path.exists():
            print(f"WARNING: {rd} does not exist, skipping")
            continue
        policies = compute_label_breakdown(report_path)
        report_name = report_path.name
        all_results[report_name] = policies
        print_table(policies, report_name)

    if all_results:
        first_dir = Path(report_dirs[0])
        output_path = first_dir.parent / "per-label-merge-times.json"
        output_json(all_results, output_path)


if __name__ == "__main__":
    main()
