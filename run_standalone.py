#!/usr/bin/env python
"""Standalone driver: exercise the sim server using different policies.

Implements top-K, active-cap, and old-burst rebase policies to compare
their behavior against the same scenario.

Usage:
    # Start sim server first, then:
    python run_standalone.py --policy top-k --sim-url http://127.0.0.1:8080
    python run_standalone.py --policy active-cap --sim-url http://127.0.0.1:8080
    python run_standalone.py --policy old-burst --sim-url http://127.0.0.1:8080

    # Or run a full comparison:
    python run_standalone.py --compare --scenario scenarios/top-k-poisoned-window.yaml
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from typing import Any

import requests

try:
    import gitlab  # noqa: F401
except ImportError:
    print("ERROR: python-gitlab not installed. Run: pip install python-gitlab")
    sys.exit(1)

from gitlab_hk_sim.state import HOLD_LABELS, MERGE_LABELS_SET, label_priority


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone policy driver for the GitLab HK sim"
    )
    parser.add_argument("--sim-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--limit", type=int, default=5, help="Rebase/merge limit per cycle"
    )
    parser.add_argument(
        "--cycles", type=int, default=5, help="Number of reconcile cycles"
    )
    parser.add_argument(
        "--ticks-per-cycle", type=int, default=4, help="Ticks between cycles"
    )
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--policy",
        choices=["top-k", "active-cap", "old-burst", "active-cap+phase1"],
        default="active-cap",
        help="Which rebase policy to simulate",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Run all policies and produce comparison (requires --scenario)",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Scenario YAML (required for --compare; server must not be running)",
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for --compare mode"
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _mr_sort_key(mr: dict) -> tuple[int, str]:
    return (label_priority(mr.get("labels", [])), mr.get("approved_at", ""))


def get_all_open_mrs(sim_url: str, project_id: int) -> list[dict]:
    """Fetch all open MRs, handling pagination, sorted by label priority."""
    all_mrs: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            f"{sim_url}/api/v4/projects/{project_id}/merge_requests",
            params={"state": "opened", "page": page, "per_page": 100},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_mrs.extend(batch)
        total_pages = int(resp.headers.get("x-total-pages", "1"))
        if page >= total_pages:
            break
        page += 1
    return sorted(all_mrs, key=_mr_sort_key)


def is_mergeable(mr: dict) -> bool:
    labels = set(mr.get("labels", []))
    has_merge_label = bool(labels & MERGE_LABELS_SET)
    has_hold_label = bool(labels & HOLD_LABELS)
    return has_merge_label and not has_hold_label and not mr.get("draft", False)


def needs_rebase(sim_url: str, project_id: int, mr_sha: str, target_head: str) -> bool:
    resp = requests.get(
        f"{sim_url}/api/v4/projects/{project_id}/repository/compare",
        params={"from": mr_sha, "to": target_head},
    )
    resp.raise_for_status()
    return len(resp.json().get("commits", [])) > 0


def get_mr_pipelines(sim_url: str, project_id: int, mr_iid: int) -> list[dict]:
    resp = requests.get(
        f"{sim_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/pipelines"
    )
    resp.raise_for_status()
    return resp.json()


def has_successful_pipeline(sim_url: str, project_id: int, mr: dict) -> bool:
    pipelines = get_mr_pipelines(sim_url, project_id, mr["iid"])
    return any(p["status"] == "success" and p["sha"] == mr["sha"] for p in pipelines)


def has_active_pipeline(sim_url: str, project_id: int, mr: dict) -> bool:
    """Check if MR has a pending or running pipeline for its current SHA."""
    pipelines = get_mr_pipelines(sim_url, project_id, mr["iid"])
    return any(
        p["status"] in ("pending", "running") and p["sha"] == mr["sha"]
        for p in pipelines
    )


def rebase_mr(sim_url: str, project_id: int, mr_iid: int) -> None:
    resp = requests.put(
        f"{sim_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/rebase"
    )
    resp.raise_for_status()


def merge_mr(sim_url: str, project_id: int, mr_iid: int) -> None:
    resp = requests.put(
        f"{sim_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/merge",
        headers={"Content-Type": "application/json"},
        json={},
    )
    resp.raise_for_status()


def sim_tick(sim_url: str) -> dict:
    resp = requests.post(f"{sim_url}/__sim/tick")
    resp.raise_for_status()
    return resp.json()


def get_state(sim_url: str) -> dict:
    resp = requests.get(f"{sim_url}/__sim/state")
    resp.raise_for_status()
    return resp.json()


def get_metrics(sim_url: str) -> dict:
    resp = requests.get(f"{sim_url}/__sim/metrics")
    resp.raise_for_status()
    return resp.json()


def reset_sim(sim_url: str) -> None:
    resp = requests.post(f"{sim_url}/__sim/reset")
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Policy implementations
# ---------------------------------------------------------------------------


def run_cycle_top_k(
    sim_url: str, project_id: int, limit: int, log: logging.Logger
) -> None:
    """Top-K policy: only consider the first K MRs by position.

    Behavior:
    - Sort MRs by position (as returned by API).
    - Only the first `limit` MRs are eligible for any work.
    - Merge eligible ready MRs.
    - Rebase eligible non-rebased MRs.
    - MRs below the top-K window are invisible.
    """
    state = get_state(sim_url)
    target_head = state["target_head"]

    all_mrs = get_all_open_mrs(sim_url, project_id)

    log.info(f"  Open MRs: {len(all_mrs)}, target_head: {target_head}")

    # Top-K window: only first `limit` MRs are visible
    eligible_mrs = all_mrs[:limit]
    log.info(f"  Top-K window: MRs {[m['iid'] for m in eligible_mrs]}")

    merge_count = 0
    rebase_count = 0

    # Merge ready MRs in the window
    for mr in eligible_mrs:
        if not is_mergeable(mr):
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        if has_successful_pipeline(sim_url, project_id, mr):
            log.info(f"  MERGE MR !{mr['iid']} ({mr['title']})")
            merge_mr(sim_url, project_id, mr["iid"])
            merge_count += 1
            state = get_state(sim_url)
            target_head = state["target_head"]

    # Rebase non-rebased MRs in the window
    for mr in eligible_mrs:
        if mr["state"] != "opened":
            continue
        if not is_mergeable(mr):
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            log.info(f"  REBASE MR !{mr['iid']} ({mr['title']})")
            rebase_mr(sim_url, project_id, mr["iid"])
            rebase_count += 1

    log.info(f"  Cycle result: {merge_count} merges, {rebase_count} rebases")


def run_cycle_active_cap(
    sim_url: str, project_id: int, limit: int, log: logging.Logger
) -> None:
    """Active-cap policy: maintain steady-state CI concurrency budget.

    Behavior:
    - Count MRs with active (pending/running) pipelines = active_count.
    - Budget = limit - active_count (remaining slots).
    - Merge ready MRs first (frees slots on next cycle).
    - Rebase up to `budget` MRs, by priority, to fill remaining CI slots.
    - MRs below the budget are not starved — any open MR can fill a slot.
    """
    state = get_state(sim_url)
    target_head = state["target_head"]

    all_mrs = get_all_open_mrs(sim_url, project_id)

    log.info(f"  Open MRs: {len(all_mrs)}, target_head: {target_head}")

    # Count active pipelines (the CI inventory currently in flight)
    active_count = 0
    for mr in all_mrs:
        if has_active_pipeline(sim_url, project_id, mr):
            active_count += 1

    budget = max(0, limit - active_count)
    log.info(f"  Active pipelines: {active_count}, budget: {budget} (limit={limit})")

    merge_count = 0
    rebase_count = 0

    # Merge ready MRs (no budget constraint on merging)
    for mr in all_mrs:
        if not is_mergeable(mr):
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        if has_successful_pipeline(sim_url, project_id, mr):
            log.info(f"  MERGE MR !{mr['iid']} ({mr['title']})")
            merge_mr(sim_url, project_id, mr["iid"])
            merge_count += 1
            state = get_state(sim_url)
            target_head = state["target_head"]

    # Rebase to fill remaining budget (any open MR by priority)
    for mr in all_mrs:
        if rebase_count >= budget:
            break
        if mr["state"] != "opened":
            continue
        if not is_mergeable(mr):
            continue
        # Skip if already has an active pipeline (already consuming a slot)
        if has_active_pipeline(sim_url, project_id, mr):
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            log.info(f"  REBASE MR !{mr['iid']} ({mr['title']})")
            rebase_mr(sim_url, project_id, mr["iid"])
            rebase_count += 1

    log.info(
        f"  Cycle result: {merge_count} merges,"
        f" {rebase_count} rebases (budget was {budget})"
    )


def run_cycle_old_burst(
    sim_url: str, project_id: int, limit: int, log: logging.Logger
) -> None:
    """Old burst policy: rebase up to limit MRs per run, ignoring active state.

    Behavior:
    - Each reconcile run can rebase up to `limit` MRs.
    - The limit resets every run (no awareness of already-active CI).
    - Over time, active CI can exceed the intended cap.
    """
    state = get_state(sim_url)
    target_head = state["target_head"]

    all_mrs = get_all_open_mrs(sim_url, project_id)

    log.info(f"  Open MRs: {len(all_mrs)}, target_head: {target_head}")

    merge_count = 0
    rebase_count = 0

    # Merge ready MRs (no cap — production old-burst only caps rebases)
    for mr in all_mrs:
        if not is_mergeable(mr):
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        if has_successful_pipeline(sim_url, project_id, mr):
            log.info(f"  MERGE MR !{mr['iid']} ({mr['title']})")
            merge_mr(sim_url, project_id, mr["iid"])
            merge_count += 1
            state = get_state(sim_url)
            target_head = state["target_head"]

    # Rebase up to limit (regardless of active pipelines)
    for mr in all_mrs:
        if rebase_count >= limit:
            break
        if mr["state"] != "opened":
            continue
        if not is_mergeable(mr):
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            log.info(f"  REBASE MR !{mr['iid']} ({mr['title']})")
            rebase_mr(sim_url, project_id, mr["iid"])
            rebase_count += 1

    log.info(f"  Cycle result: {merge_count} merges, {rebase_count} rebases")


TENANT_LABEL_PREFIX = "tenant-"


def get_tenant_domains(mr: dict) -> set[str]:
    """Extract tenant domains from MR labels (tenant-* labels)."""
    labels = mr.get("labels", [])
    return {lbl for lbl in labels if lbl.startswith(TENANT_LABEL_PREFIX)}


def run_cycle_active_cap_phase1(
    sim_url: str, project_id: int, limit: int, log: logging.Logger
) -> None:
    """Active-cap + Phase 1 optimistic multi-merge.

    Combines active-cap rebase concurrency control with Phase 1 optimistic
    multi-merge. The key Phase 1 insight:

    1. Build the "same-root success pool": all MRs that are rebased onto the
       current target AND have a successful pipeline.
    2. From that pool, select a non-overlapping batch (by tenant domains).
    3. Merge the entire batch — each merge advances target, but because all
       shared the same root, subsequent MRs in the batch use skip_ci rebase
       (fast-forward) before merge.

    This models ADR-019 Phase 1:
    - Active-cap maintains N useful pipeline slots (rebase phase)
    - Phase 1 consumes same-root success pool via batch merge
    - Result: >1 MR merged per target advance cycle
    """
    state = get_state(sim_url)
    target_head = state["target_head"]

    all_mrs = get_all_open_mrs(sim_url, project_id)

    log.info(f"  Open MRs: {len(all_mrs)}, target_head: {target_head}")

    # Count active pipelines for budget
    active_count = 0
    for mr in all_mrs:
        if has_active_pipeline(sim_url, project_id, mr):
            active_count += 1

    budget = max(0, limit - active_count)
    log.info(f"  Active pipelines: {active_count}, budget: {budget} (limit={limit})")

    merge_count = 0
    rebase_count = 0

    # Phase 1: Build same-root success pool BEFORE any merges
    # These are MRs rebased onto current target with successful pipelines
    same_root_pool: list[dict] = []
    for mr in all_mrs:
        if mr["state"] != "opened":
            continue
        if not is_mergeable(mr):
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        if has_successful_pipeline(sim_url, project_id, mr):
            same_root_pool.append(mr)

    log.info(f"  Same-root success pool: {len(same_root_pool)} MRs")

    # Select non-overlapping batch from pool (greedy by priority/position)
    merge_batch: list[dict] = []
    used_domains: set[str] = set()
    merge_limit = limit  # use limit as merge_limit too

    for mr in same_root_pool:
        if len(merge_batch) >= merge_limit:
            break
        mr_domains = get_tenant_domains(mr)

        if not merge_batch:
            # First MR always included (the "safe" first merge)
            merge_batch.append(mr)
            used_domains.update(mr_domains)
        else:
            # Subsequent MRs: must have tenant labels and no overlap
            if not mr_domains:
                continue
            if mr_domains & used_domains:
                log.debug(
                    f"    SKIP MR !{mr['iid']} (overlap: {mr_domains & used_domains})"
                )
                continue
            merge_batch.append(mr)
            used_domains.update(mr_domains)

    # Execute the batch merge
    if merge_batch:
        log.info(f"  Phase 1 batch: {len(merge_batch)} MRs selected for merge")
        for i, mr in enumerate(merge_batch):
            if i == 0:
                log.info(f"  MERGE MR !{mr['iid']} ({mr['title']}) [first]")
            else:
                log.info(
                    f"  MULTI-MERGE MR !{mr['iid']} ({mr['title']}) [phase1-optimistic]"
                )
            merge_mr(sim_url, project_id, mr["iid"])
            merge_count += 1

        # Refresh state after batch
        state = get_state(sim_url)
        target_head = state["target_head"]

    # Rebase to fill remaining budget (active-cap logic)
    # Re-fetch MRs since some were merged
    all_mrs = get_all_open_mrs(sim_url, project_id)
    for mr in all_mrs:
        if rebase_count >= budget:
            break
        if mr["state"] != "opened":
            continue
        if not is_mergeable(mr):
            continue
        if has_active_pipeline(sim_url, project_id, mr):
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            log.info(f"  REBASE MR !{mr['iid']} ({mr['title']})")
            rebase_mr(sim_url, project_id, mr["iid"])
            rebase_count += 1

    optimistic = max(0, merge_count - 1) if merge_count > 0 else 0
    log.info(
        f"  Cycle result: {merge_count} merges"
        f" ({optimistic} optimistic), {rebase_count} rebases"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

POLICY_RUNNERS = {
    "top-k": run_cycle_top_k,
    "active-cap": run_cycle_active_cap,
    "old-burst": run_cycle_old_burst,
    "active-cap+phase1": run_cycle_active_cap_phase1,
}


def run_policy(
    sim_url: str,
    policy: str,
    limit: int,
    cycles: int,
    ticks_per_cycle: int,
    log: logging.Logger,
) -> dict[str, Any]:
    """Run a complete simulation with one policy. Returns enriched metrics."""
    project_id = 1001
    runner = POLICY_RUNNERS[policy]

    log.info(
        f"Policy: {policy}, limit={limit},"
        f" cycles={cycles}, ticks_per_cycle={ticks_per_cycle}"
    )
    log.info("=" * 60)

    # Track temporal data
    total_ticks = 0
    merge_ticks: list[int] = []  # tick at which each merge happened
    initial_state = get_state(sim_url)
    initial_open = initial_state["open_mrs"]

    # Track merges-per-cycle for Phase 1 multi-merge metric
    # "target advance" in Phase 1 context = one reconcile cycle that produced merges
    merge_cycles = 0  # cycles where at least 1 merge happened

    for cycle in range(1, cycles + 1):
        log.info(f"--- Cycle {cycle}/{cycles} ---")

        # Capture merges by checking state before/after
        state_before = get_state(sim_url)
        runner(sim_url, project_id, limit, log)
        state_after = get_state(sim_url)

        # Detect merges that happened (open count dropped)
        merges_this_cycle = state_before["open_mrs"] - state_after["open_mrs"]
        for _ in range(merges_this_cycle):
            merge_ticks.append(total_ticks)

        if merges_this_cycle > 0:
            merge_cycles += 1

        if cycle < cycles:
            log.info(f"  Advancing {ticks_per_cycle} ticks...")
            for _ in range(ticks_per_cycle):
                sim_tick(sim_url)
                total_ticks += 1

    log.info("=" * 60)
    metrics = get_metrics(sim_url)
    final_state = get_state(sim_url)

    # Compute temporal metrics
    mrs_merged = metrics.get("merge_calls", 0) or len(merge_ticks)
    total_time_ticks = total_ticks
    throughput = mrs_merged / total_time_ticks if total_time_ticks > 0 else 0

    # Time to first merge
    time_to_first_merge = merge_ticks[0] if merge_ticks else total_time_ticks

    # Time to merge top-10 (first 10 MRs merged)
    time_to_merge_10 = merge_ticks[9] if len(merge_ticks) >= 10 else total_time_ticks

    # Average time between merges
    avg_merge_interval = (
        total_time_ticks / mrs_merged if mrs_merged > 0 else total_time_ticks
    )

    # Avg MRs per merge-cycle (Phase 1 key metric)
    # Serial: ~1 MR per merge-cycle. Phase 1 goal: >1 MR per merge-cycle.
    avg_mrs_per_merge_cycle = mrs_merged / merge_cycles if merge_cycles > 0 else 0

    # Enrich metrics with temporal data
    metrics["total_time_ticks"] = total_time_ticks
    metrics["mrs_merged"] = mrs_merged
    metrics["throughput_merges_per_tick"] = round(throughput, 4)
    metrics["time_to_first_merge"] = time_to_first_merge
    metrics["time_to_merge_10"] = time_to_merge_10
    metrics["avg_merge_interval_ticks"] = round(avg_merge_interval, 2)
    metrics["queue_drain_pct"] = (
        round(mrs_merged / initial_open * 100, 1) if initial_open > 0 else 0
    )
    metrics["merge_cycles"] = merge_cycles
    metrics["avg_mrs_per_merge_cycle"] = round(avg_mrs_per_merge_cycle, 2)

    log.info("Final metrics:")
    log.info(f"  total_time: {total_time_ticks} ticks")
    log.info(f"  mrs_merged: {mrs_merged}")
    log.info(f"  throughput: {throughput:.4f} merges/tick")
    log.info(f"  time_to_first_merge: {time_to_first_merge} ticks")
    log.info(f"  time_to_merge_10: {time_to_merge_10} ticks")
    log.info(f"  avg_merge_interval: {avg_merge_interval:.1f} ticks")
    log.info(f"  queue_drain: {metrics['queue_drain_pct']}%")
    log.info(f"  peak_active_pipelines: {metrics.get('peak_active_pipelines')}")
    log.info(f"  duplicate_rebases: {metrics.get('duplicate_rebase_total')}")
    log.info(f"  remaining open MRs: {final_state['open_mrs']}")

    return metrics


def run_comparison(args: argparse.Namespace) -> None:
    """Run all policies against the same scenario and print comparison."""
    if not args.scenario:
        print("ERROR: --compare requires --scenario")
        sys.exit(1)

    log = logging.getLogger("compare")
    sim_url = f"http://127.0.0.1:{args.port}"
    venv_python = os.path.join(os.path.dirname(__file__), ".venv", "bin", "python")

    results: dict[str, dict] = {}

    policies = ["top-k", "active-cap", "old-burst", "active-cap+phase1"]

    reports_dir = os.path.join(
        os.path.abspath(os.path.dirname(__file__) or "."), "reports", "last-run"
    )
    os.makedirs(reports_dir, exist_ok=True)

    for policy in policies:
        log.info(f"\n{'#' * 60}")
        log.info(f"# POLICY: {policy}")
        log.info(f"{'#' * 60}\n")

        metrics_out = os.path.abspath(
            os.path.join(reports_dir, f"{policy}-metrics.ndjson")
        )

        # Start server
        sim_dir = os.path.abspath(os.path.dirname(__file__) or ".")
        server_log = open(  # noqa: SIM115
            os.path.join(reports_dir, f"{policy}-server.log"), "w"
        )
        server_proc = subprocess.Popen(
            [
                venv_python,
                "-m",
                "gitlab_hk_sim.cli",
                "serve",
                "--scenario",
                os.path.abspath(args.scenario),
                "--port",
                str(args.port),
                "--host",
                "127.0.0.1",
                "--metrics-out",
                metrics_out,
            ],
            stdout=server_log,
            stderr=server_log,
            cwd=sim_dir,
            env={**os.environ, "PYTHONPATH": sim_dir},
        )
        time.sleep(2)

        # Verify server
        try:
            requests.get(f"{sim_url}/api/v4/user").raise_for_status()
        except Exception:
            log.error(f"Server failed to start for policy {policy}")
            server_proc.kill()
            continue

        try:
            results[policy] = run_policy(
                sim_url, policy, args.limit, args.cycles, args.ticks_per_cycle, log
            )
        finally:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait()
            server_log.close()
            time.sleep(1)

    # Print comparison table
    print("\n")
    print("=" * 90)
    print("POLICY COMPARISON")
    print("=" * 90)
    print()

    metrics_keys = [
        # --- Throughput & Time ---
        (None, "── Throughput & Time ──"),
        ("total_time_ticks", "Total Time (ticks)"),
        ("mrs_merged", "MRs Merged"),
        ("throughput_merges_per_tick", "Throughput (merges/tick)"),
        ("time_to_first_merge", "Time to First Merge"),
        ("time_to_merge_10", "Time to Merge 10 MRs"),
        ("avg_merge_interval_ticks", "Avg Merge Interval"),
        ("queue_drain_pct", "Queue Drain %"),
        # --- CI Efficiency ---
        (None, "── CI Efficiency ──"),
        ("rebase_calls", "Rebases"),
        ("pipelines_created", "Pipelines Created"),
        ("peak_active_pipelines", "Peak Active Pipelines"),
        ("duplicate_rebase_total", "Duplicate Rebases"),
        ("stale_success_max", "Stale Successes Max"),
        # --- Queue Health ---
        (None, "── Queue Health ──"),
        ("same_root_success_pool_p95", "Same-Root Pool p95"),
        ("same_root_success_pool_max", "Same-Root Pool Max"),
        # --- Phase 1 Multi-Merge ---
        (None, "── Phase 1 Multi-Merge ──"),
        ("merge_cycles", "Merge Cycles"),
        ("avg_mrs_per_merge_cycle", "Avg MRs / Merge Cycle"),
    ]

    # Header
    col_w = 14
    header_policies = ["top-k", "active-cap", "old-burst", "cap+phase1"]
    print(
        f"| {'Metric':<28} |"
        + "|".join(f" {p:>{col_w - 2}} " for p in header_policies)
        + "|"
    )
    print(f"|{'-' * 30}|" + "|".join("-" * col_w for _ in header_policies) + "|")

    policy_keys = ["top-k", "active-cap", "old-burst", "active-cap+phase1"]
    for key, label in metrics_keys:
        if key is None:
            print(
                f"| {label:<28} |"
                + "|".join(f" {'':>{col_w - 2}} " for _ in header_policies)
                + "|"
            )
            continue
        vals = []
        for policy in policy_keys:
            v = results.get(policy, {}).get(key, "N/A")
            if isinstance(v, float):
                vals.append(f"{v:.3f}")
            else:
                vals.append(str(v))
        print(f"| {label:<28} |" + "|".join(f" {v:>{col_w - 2}} " for v in vals) + "|")

    print()
    print("Key:")
    print("  tick ≈ 1 minute of CI time (when using pipeline_durations config)")
    print("  throughput = MRs merged / total ticks elapsed")
    print("  queue drain = % of initial open MRs that got merged")
    print()
    print("Interpretation:")
    print("  - Higher throughput = faster queue processing")
    print("  - Lower time-to-first-merge = faster initial results")
    print("  - Lower peak active = better concurrency control (limit respected)")
    print("  - Lower duplicate rebases = less wasted CI")
    print("  - Higher same-root pool = more Phase 1 multi-merge candidates")
    print()

    # Save comparison table to file
    comparison_file = os.path.join(reports_dir, "8hour-comparison.txt")
    with open(comparison_file, "w") as f:
        f.write("\n\n")
        f.write("=" * 90 + "\n")
        f.write("POLICY COMPARISON\n")
        f.write("=" * 90 + "\n\n")
        f.write(
            f"| {'Metric':<28} |"
            + "|".join(f" {p:>{col_w - 2}} " for p in header_policies)
            + "|\n"
        )
        sep = "|".join("-" * col_w for _ in header_policies)
        f.write(f"|{'-' * 30}|{sep}|\n")
        for key, label in metrics_keys:
            if key is None:
                f.write(
                    f"| {label:<28} |"
                    + "|".join(f" {'':>{col_w - 2}} " for _ in header_policies)
                    + "|\n"
                )
            else:
                vals = []
                for policy in policy_keys:
                    v = results.get(policy, {}).get(key, "N/A")
                    if isinstance(v, float):
                        vals.append(f"{v:.3f}")
                    else:
                        vals.append(str(v))
                cells = "|".join(f" {v:>{col_w - 2}} " for v in vals)
                f.write(f"| {label:<28} |{cells}|\n")
        f.write("\nKey:\n")
        f.write("  tick ≈ 1 minute of CI time (when using pipeline_durations config)\n")
        f.write("  throughput = MRs merged / total ticks elapsed\n")
        f.write("  queue drain = % of initial open MRs that got merged\n\n")
        f.write("Interpretation:\n")
        f.write("  - Higher throughput = faster queue processing\n")
        f.write("  - Lower time-to-first-merge = faster initial results\n")
        f.write(
            "  - Lower peak active = better concurrency control (limit respected)\n"
        )
        f.write("  - Lower duplicate rebases = less wasted CI\n")
        f.write("  - Higher same-root pool = more Phase 1 multi-merge candidates\n\n")
    log.info(f"Comparison saved to {comparison_file}")
    log.info(f"Per-policy NDJSON files saved to {reports_dir}/")


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    log = logging.getLogger("standalone")

    if args.compare:
        run_comparison(args)
        return

    sim_url = args.sim_url.rstrip("/")

    # Verify server is running
    try:
        requests.get(f"{sim_url}/api/v4/user").raise_for_status()
    except Exception as e:
        log.error(f"Cannot reach sim server at {sim_url}: {e}")
        log.error(
            "Start the sim first: make serve SCENARIO=scenarios/mvp-active-cap.yaml"
        )
        sys.exit(1)

    run_policy(sim_url, args.policy, args.limit, args.cycles, args.ticks_per_cycle, log)


if __name__ == "__main__":
    main()
