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
import csv
import logging
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import partial
from typing import Any

import requests

try:
    import gitlab  # noqa: F401
except ImportError:
    print("ERROR: python-gitlab not installed. Run: pip install python-gitlab")
    sys.exit(1)

from gitlab_hk_sim.state import HOLD_LABELS, MERGE_LABELS_SET, label_priority

# ---------------------------------------------------------------------------
# Policy set presets for Monte Carlo and comparison runs
# ---------------------------------------------------------------------------
POLICY_SETS: dict[str, list[str]] = {
    "phase0": ["top-k", "active-cap", "old-burst"],
    "phase1": ["top-k", "active-cap", "old-burst", "cap+phase1"],
    "all": [
        "top-k",
        "top-k-no-insist",
        "active-cap",
        "active-cap-no-insist",
        "old-burst",
        "old-burst-no-insist",
        "cap+phase1",
        "cap+phase1-NI",
    ],
}


def _percentile(sorted_vals: list, pct: int) -> int:
    """Compute percentile from a pre-sorted list of values."""
    if not sorted_vals:
        return 0
    idx = int(len(sorted_vals) * pct / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


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
    parser.add_argument(
        "--monte-carlo",
        type=int,
        default=0,
        help="Number of Monte Carlo trials (0=disabled)",
    )
    parser.add_argument(
        "--policy-set",
        choices=["phase0", "phase1", "all"],
        default="phase0",
        help="Named policy preset for Monte Carlo / comparison",
    )
    parser.add_argument(
        "--policies",
        default=None,
        help="Comma-separated policy list (overrides --policy-set)",
    )
    parser.add_argument(
        "--base-seed",
        type=int,
        default=42,
        help="Base random seed for Monte Carlo",
    )
    parser.add_argument(
        "--base-port",
        type=int,
        default=9001,
        help="Base port for parallel servers in Monte Carlo mode",
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


def preprocess_mrs(sim_url: str, project_id: int, mrs: list[dict]) -> list[dict]:
    """Model production's preprocess_merge_requests filtering and API overhead.

    In production, preprocess_merge_requests:
    1. Skips cannot_be_merged, draft, 0-commit MRs
    2. Fetches label_events per MR to validate approval (authorized user added
       a merge label). MRs without valid approval are excluded.
    3. Extracts approved_at from the label event for sort tiebreaking.
    4. Returns sorted by (label_priority, approved_at).
    """
    result = []
    for mr in mrs:
        if mr.get("merge_status") in ("cannot_be_merged", "cannot_be_merged_recheck"):
            continue
        if mr.get("draft", False):
            continue

        # mr.commits() — production checks len(mr.commits()) == 0
        resp = requests.get(
            f"{sim_url}/api/v4/projects/{project_id}"
            f"/merge_requests/{mr['iid']}/commits",
            params={"per_page": 1, "page": 1},
        )
        resp.raise_for_status()
        if not resp.json():
            continue

        labels = set(mr.get("labels", []))
        if not labels:
            continue
        has_merge_label = bool(labels & MERGE_LABELS_SET)
        has_hold_label = bool(labels & HOLD_LABELS)
        if not has_merge_label or has_hold_label:
            continue

        # gl.get_merge_request_label_events(mr) — find valid approval
        resp = requests.get(
            f"{sim_url}/api/v4/projects/{project_id}"
            f"/merge_requests/{mr['iid']}/resource_label_events",
            params={"per_page": 100, "page": 1},
        )
        resp.raise_for_status()
        label_events = resp.json()

        approval_found = False
        approved_at = ""
        for event in reversed(label_events):
            if event.get("action") != "add":
                continue
            label_info = event.get("label", {})
            if not label_info:
                continue
            label_name = label_info.get("name", "")
            if label_name in MERGE_LABELS_SET and not approval_found:
                approval_found = True
                approved_at = event.get("created_at", "")

        if not approval_found:
            continue

        mr_copy = dict(mr)
        mr_copy["approved_at"] = approved_at
        result.append(mr_copy)

    result.sort(key=_mr_sort_key)
    return result


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


def is_consuming_slot(
    sim_url: str, project_id: int, mr: dict, target_head: str
) -> bool:
    """Check if MR is consuming a CI concurrency slot.

    Production active-cap logic (PR #5508):
    - Rebased MR with running/pending/success pipeline = active
      (success = green and waiting to merge, still occupying a slot)
    - Non-rebased MR with running/pending pipeline = active
      (previous rebase still in progress)
    """
    pipelines = get_mr_pipelines(sim_url, project_id, mr["iid"])
    if not pipelines:
        return False

    latest_status = pipelines[0]["status"]
    is_rebased = not needs_rebase(sim_url, project_id, mr["sha"], target_head)

    if is_rebased:
        return latest_status in ("running", "pending", "success")
    else:
        return latest_status in ("running", "pending")


def has_failed_pipeline(sim_url: str, project_id: int, mr: dict) -> bool:
    """Check if MR's latest pipeline has failed (needs retry rebase)."""
    pipelines = get_mr_pipelines(sim_url, project_id, mr["iid"])
    if not pipelines:
        return False
    return pipelines[0]["status"] == "failed"


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
    sim_url: str,
    project_id: int,
    limit: int,
    log: logging.Logger,
    *,
    insist: bool = True,
) -> None:
    """Top-K policy: only consider the first K MRs by priority.

    Models hemslo's proposed design from PR #5508:
    - merge_requests[:rebase_limit] — only the first K MRs are visible.
    - Merge phase: single merge, rebase=True. If insist=True, block on
      first rebased MR with running pipeline. If insist=False, skip it.
    - Rebase phase: rebase non-rebased MRs in the window, skip active.
    - MRs below the top-K window are completely invisible.
    """
    state = get_state(sim_url)
    target_head = state["target_head"]

    all_mrs = get_all_open_mrs(sim_url, project_id)
    preprocessed = preprocess_mrs(sim_url, project_id, all_mrs)

    log.info(f"  Open MRs: {len(all_mrs)}, preprocessed: {len(preprocessed)}")

    # Top-K window: only first `limit` MRs are visible
    eligible_mrs = preprocessed[:limit]
    log.info(f"  Top-K window: MRs {[m['iid'] for m in eligible_mrs]}")

    merge_count = 0
    rebase_count = 0

    # Merge phase: single merge, rebase=True
    for mr in eligible_mrs:
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        pipelines = get_mr_pipelines(sim_url, project_id, mr["iid"])
        if not pipelines:
            continue
        latest_status = pipelines[0]["status"]
        if latest_status in ("running", "pending"):
            if insist:
                log.info(f"  INSIST MR !{mr['iid']} (pipeline {latest_status})")
                break
            log.info(f"  SKIP MR !{mr['iid']} (pipeline {latest_status})")
            continue
        if latest_status == "success":
            log.info(f"  MERGE MR !{mr['iid']} ({mr['title']})")
            merge_mr(sim_url, project_id, mr["iid"])
            merge_count += 1
            break
        continue

    # Rebase phase: rebase non-rebased MRs in the window (skip active)
    if merge_count > 0:
        state = get_state(sim_url)
        target_head = state["target_head"]
    for mr in eligible_mrs:
        if mr["state"] != "opened":
            continue
        if not needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        if has_active_pipeline(sim_url, project_id, mr):
            continue
        log.info(f"  REBASE MR !{mr['iid']} ({mr['title']})")
        rebase_mr(sim_url, project_id, mr["iid"])
        rebase_count += 1

    # Retry failed: re-rebase MRs whose pipeline failed
    for mr in eligible_mrs:
        if mr["state"] != "opened":
            continue
        if has_failed_pipeline(sim_url, project_id, mr):
            log.info(f"  RETRY MR !{mr['iid']} ({mr['title']}) [pipeline failed]")
            rebase_mr(sim_url, project_id, mr["iid"])
            rebase_count += 1

    log.info(f"  Cycle result: {merge_count} merges, {rebase_count} rebases")


def run_cycle_active_cap(
    sim_url: str,
    project_id: int,
    limit: int,
    log: logging.Logger,
    *,
    insist: bool = True,
) -> None:
    """Active-cap policy: maintain steady-state CI concurrency budget.

    Models PR #5508 use_active_cap=True with production merge semantics:
    - Merge phase (single merge, rebase=True): if insist=True, block on
      first rebased MR with running pipeline. If insist=False, skip it.
    - Rebase phase: classify all MRs for active slots, compute budget,
      rebase up to budget MRs by priority.
    """
    state = get_state(sim_url)
    target_head = state["target_head"]

    all_mrs = get_all_open_mrs(sim_url, project_id)
    preprocessed = preprocess_mrs(sim_url, project_id, all_mrs)

    log.info(f"  Open MRs: {len(all_mrs)}, preprocessed: {len(preprocessed)}")

    merge_count = 0
    rebase_count = 0

    # Merge phase: single merge, rebase=True
    for mr in preprocessed:
        if mr["state"] != "opened":
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        pipelines = get_mr_pipelines(sim_url, project_id, mr["iid"])
        if not pipelines:
            continue
        latest_status = pipelines[0]["status"]
        if latest_status in ("running", "pending"):
            if insist:
                log.info(f"  INSIST MR !{mr['iid']} (pipeline {latest_status})")
                break
            log.info(f"  SKIP MR !{mr['iid']} (pipeline {latest_status})")
            continue
        if latest_status == "success":
            log.info(f"  MERGE MR !{mr['iid']} ({mr['title']})")
            merge_mr(sim_url, project_id, mr["iid"])
            merge_count += 1
            break
        continue

    # Refresh state after potential merge
    if merge_count > 0:
        state = get_state(sim_url)
        target_head = state["target_head"]

    # Rebase phase: classify and fill budget
    already_active = 0
    needs_rebase_mrs: list[dict] = []
    failed_mrs: list[dict] = []

    for mr in preprocessed:
        if mr["state"] != "opened":
            continue
        if is_consuming_slot(sim_url, project_id, mr, target_head):
            already_active += 1
        elif has_failed_pipeline(sim_url, project_id, mr):
            failed_mrs.append(mr)
        elif needs_rebase(sim_url, project_id, mr["sha"], target_head):
            needs_rebase_mrs.append(mr)

    budget = max(0, limit - already_active)
    log.info(f"  Already active: {already_active}, budget: {budget} (limit={limit})")

    for mr in needs_rebase_mrs:
        if rebase_count >= budget:
            break
        log.info(f"  REBASE MR !{mr['iid']} ({mr['title']})")
        rebase_mr(sim_url, project_id, mr["iid"])
        rebase_count += 1

    # Retry failed pipelines within remaining budget
    for mr in failed_mrs:
        if rebase_count >= budget:
            break
        log.info(f"  RETRY MR !{mr['iid']} ({mr['title']}) [pipeline failed]")
        rebase_mr(sim_url, project_id, mr["iid"])
        rebase_count += 1

    log.info(
        f"  Cycle result: {merge_count} merges,"
        f" {rebase_count} rebases (budget was {budget})"
    )


def run_cycle_old_burst(
    sim_url: str,
    project_id: int,
    limit: int,
    log: logging.Logger,
    *,
    insist: bool = True,
) -> None:
    """Old burst policy: current production master behavior.

    Models reconcile/gitlab_housekeeping.py on master with rebase=True:
    - Merge phase (single merge, rebase=True): if insist=True, block on
      first rebased MR with running pipeline. If insist=False, skip it.
    - Rebase phase: rebase up to `limit` non-rebased MRs per run.
      Skips MRs with running pipelines (wait_for_pipeline=True).
      The limit resets every run (no awareness of already-active CI).
    """
    state = get_state(sim_url)
    target_head = state["target_head"]

    all_mrs = get_all_open_mrs(sim_url, project_id)
    preprocessed = preprocess_mrs(sim_url, project_id, all_mrs)

    log.info(f"  Open MRs: {len(all_mrs)}, preprocessed: {len(preprocessed)}")

    merge_count = 0
    rebase_count = 0

    # Merge phase: single merge, rebase=True
    for mr in preprocessed:
        if mr["state"] != "opened":
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        pipelines = get_mr_pipelines(sim_url, project_id, mr["iid"])
        if not pipelines:
            continue
        latest_status = pipelines[0]["status"]
        if latest_status in ("running", "pending"):
            if insist:
                log.info(f"  INSIST MR !{mr['iid']} (pipeline {latest_status})")
                break
            log.info(f"  SKIP MR !{mr['iid']} (pipeline {latest_status})")
            continue
        if latest_status == "success":
            log.info(f"  MERGE MR !{mr['iid']} ({mr['title']})")
            merge_mr(sim_url, project_id, mr["iid"])
            merge_count += 1
            break
        continue

    # Refresh state after potential merge
    if merge_count > 0:
        state = get_state(sim_url)
        target_head = state["target_head"]

    # Rebase phase: rebase up to limit (skip rebased, skip active pipelines)
    for mr in preprocessed:
        if rebase_count >= limit:
            break
        if mr["state"] != "opened":
            continue
        if not needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        if has_active_pipeline(sim_url, project_id, mr):
            continue
        log.info(f"  REBASE MR !{mr['iid']} ({mr['title']})")
        rebase_mr(sim_url, project_id, mr["iid"])
        rebase_count += 1

    # Retry failed pipelines within remaining limit
    for mr in preprocessed:
        if rebase_count >= limit:
            break
        if mr["state"] != "opened":
            continue
        if has_failed_pipeline(sim_url, project_id, mr):
            log.info(f"  RETRY MR !{mr['iid']} ({mr['title']}) [pipeline failed]")
            rebase_mr(sim_url, project_id, mr["iid"])
            rebase_count += 1

    log.info(f"  Cycle result: {merge_count} merges, {rebase_count} rebases")


TENANT_LABEL_PREFIX = "tenant-"


def get_tenant_domains(mr: dict) -> set[str]:
    """Extract tenant domains from MR labels (tenant-* labels)."""
    labels = mr.get("labels", [])
    return {lbl for lbl in labels if lbl.startswith(TENANT_LABEL_PREFIX)}


def run_cycle_active_cap_phase1(
    sim_url: str,
    project_id: int,
    limit: int,
    log: logging.Logger,
    *,
    insist: bool = False,
) -> None:
    """Active-cap + Phase 1 optimistic multi-merge.

    Combines active-cap rebase concurrency control with Phase 1 non-blocking
    batch merge:

    Merge phase:
    1. Build the "same-root success pool": all MRs rebased onto current
       target with a successful pipeline.
    2. If pool is non-empty: select non-overlapping batch, merge all.
    3. If pool is empty, fallback to serial merge semantics:
       - insist=True: block on first rebased MR with running pipeline.
       - insist=False: skip running, merge first green MR.

    Rebase phase:
    - Classify all MRs for active slots, compute budget.
    - Rebase up to budget MRs by priority to feed the pool.
    """
    state = get_state(sim_url)
    target_head = state["target_head"]

    all_mrs = get_all_open_mrs(sim_url, project_id)
    preprocessed = preprocess_mrs(sim_url, project_id, all_mrs)

    log.info(f"  Open MRs: {len(all_mrs)}, preprocessed: {len(preprocessed)}")

    merge_count = 0
    rebase_count = 0

    # Phase 1: Build same-root success pool
    same_root_pool: list[dict] = []
    for mr in preprocessed:
        if mr["state"] != "opened":
            continue
        if needs_rebase(sim_url, project_id, mr["sha"], target_head):
            continue
        if has_successful_pipeline(sim_url, project_id, mr):
            same_root_pool.append(mr)

    log.info(f"  Same-root success pool: {len(same_root_pool)} MRs")

    if same_root_pool:
        # Select non-overlapping batch from pool (greedy by priority)
        merge_batch: list[dict] = []
        used_domains: set[str] = set()
        merge_limit = limit

        for mr in same_root_pool:
            if len(merge_batch) >= merge_limit:
                break
            mr_domains = get_tenant_domains(mr)

            if not merge_batch:
                merge_batch.append(mr)
                used_domains.update(mr_domains)
            else:
                if not mr_domains:
                    continue
                if mr_domains & used_domains:
                    log.debug(
                        f"    SKIP MR !{mr['iid']}"
                        f" (overlap: {mr_domains & used_domains})"
                    )
                    continue
                merge_batch.append(mr)
                used_domains.update(mr_domains)

        # Execute the batch merge
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
    else:
        # Fallback: no same-root pool — serial merge with insist control
        for mr in preprocessed:
            if mr["state"] != "opened":
                continue
            if needs_rebase(sim_url, project_id, mr["sha"], target_head):
                continue
            pipelines = get_mr_pipelines(sim_url, project_id, mr["iid"])
            if not pipelines:
                continue
            latest_status = pipelines[0]["status"]
            if latest_status in ("running", "pending"):
                if insist:
                    log.info(
                        f"  INSIST MR !{mr['iid']}"
                        f" (pipeline {latest_status}) [fallback]"
                    )
                    break
                log.info(
                    f"  SKIP MR !{mr['iid']} (pipeline {latest_status}) [fallback]"
                )
                continue
            if latest_status == "success":
                log.info(f"  MERGE MR !{mr['iid']} ({mr['title']}) [fallback-single]")
                merge_mr(sim_url, project_id, mr["iid"])
                merge_count += 1
                break
            continue

    # Refresh state after merge(s)
    if merge_count > 0:
        state = get_state(sim_url)
        target_head = state["target_head"]

    # Rebase phase: classify and fill budget
    already_active = 0
    needs_rebase_mrs: list[dict] = []
    failed_mrs: list[dict] = []

    for mr in preprocessed:
        if mr["state"] != "opened":
            continue
        if is_consuming_slot(sim_url, project_id, mr, target_head):
            already_active += 1
        elif has_failed_pipeline(sim_url, project_id, mr):
            failed_mrs.append(mr)
        elif needs_rebase(sim_url, project_id, mr["sha"], target_head):
            needs_rebase_mrs.append(mr)

    budget = max(0, limit - already_active)
    log.info(f"  Already active: {already_active}, budget: {budget} (limit={limit})")

    for mr in needs_rebase_mrs:
        if rebase_count >= budget:
            break
        log.info(f"  REBASE MR !{mr['iid']} ({mr['title']})")
        rebase_mr(sim_url, project_id, mr["iid"])
        rebase_count += 1

    # Retry failed pipelines within remaining budget
    for mr in failed_mrs:
        if rebase_count >= budget:
            break
        log.info(f"  RETRY MR !{mr['iid']} ({mr['title']}) [pipeline failed]")
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
    "top-k": partial(run_cycle_top_k, insist=True),
    "top-k-no-insist": partial(run_cycle_top_k, insist=False),
    "active-cap": partial(run_cycle_active_cap, insist=True),
    "active-cap-no-insist": partial(run_cycle_active_cap, insist=False),
    "old-burst": partial(run_cycle_old_burst, insist=True),
    "old-burst-no-insist": partial(run_cycle_old_burst, insist=False),
    "cap+phase1": partial(run_cycle_active_cap_phase1, insist=True),
    "cap+phase1-NI": partial(run_cycle_active_cap_phase1, insist=False),
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
    total_mrs = initial_state.get("total_mrs", initial_state["open_mrs"])

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
        round(mrs_merged / total_mrs * 100, 1) if total_mrs > 0 else 0
    )
    metrics["merge_cycles"] = merge_cycles
    metrics["avg_mrs_per_merge_cycle"] = round(avg_mrs_per_merge_cycle, 2)

    # Starvation tracking: fetch per-MR wait times
    resp = requests.get(f"{sim_url}/__sim/merged_mrs")
    resp.raise_for_status()
    merged_mrs = resp.json()

    wait_times = sorted(m["wait_ticks"] for m in merged_mrs)
    if wait_times:
        metrics["wait_p50"] = _percentile(wait_times, 50)
        metrics["wait_p95"] = _percentile(wait_times, 95)
        metrics["wait_max"] = wait_times[-1]
        metrics["starved_mrs"] = sum(1 for w in wait_times if w > 100)
    else:
        metrics["wait_p50"] = 0
        metrics["wait_p95"] = 0
        metrics["wait_max"] = 0
        metrics["starved_mrs"] = 0

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
    log.info(
        f"  wait times: p50={metrics['wait_p50']}"
        f" p95={metrics['wait_p95']} max={metrics['wait_max']}"
    )
    log.info(f"  starved MRs (>100 ticks): {metrics['starved_mrs']}")

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

    policies = [
        "top-k",
        "top-k-no-insist",
        "active-cap",
        "active-cap-no-insist",
        "old-burst",
        "old-burst-no-insist",
        "cap+phase1",
        "cap+phase1-NI",
    ]

    timestamp = datetime.now().strftime("%m-%d-%y_%I-%M-%p")
    base_reports = os.path.join(
        os.path.abspath(os.path.dirname(__file__) or "."), "reports", "comparisons"
    )
    reports_dir = os.path.join(base_reports, timestamp)
    os.makedirs(reports_dir, exist_ok=True)

    latest_link = os.path.join(base_reports, "latest")
    if os.path.islink(latest_link):
        os.unlink(latest_link)
    os.symlink(timestamp, latest_link)

    log.info(f"Reports directory: {reports_dir}")

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
    col_w = 12
    header_policies = [
        "top-k",
        "top-k-NI",
        "act-cap",
        "act-cap-NI",
        "burst",
        "burst-NI",
        "cap+ph1",
        "ph1-NI",
    ]
    table_width = 30 + col_w * len(header_policies) + len(header_policies) + 1
    print("\n")
    print("=" * table_width)
    print("POLICY COMPARISON")
    print("=" * table_width)
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
        # --- Priority Starvation ---
        (None, "── Priority Starvation ──"),
        ("wait_p50", "Wait Time p50 (ticks)"),
        ("wait_p95", "Wait Time p95 (ticks)"),
        ("wait_max", "Max Wait (ticks)"),
        ("starved_mrs", "Starved MRs (>100 ticks)"),
    ]

    # Header
    print(
        f"| {'Metric':<28} |"
        + "|".join(f" {p:>{col_w - 2}} " for p in header_policies)
        + "|"
    )
    print(f"|{'-' * 30}|" + "|".join("-" * col_w for _ in header_policies) + "|")

    policy_keys = policies
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
    print("  NI = no-insist (skip running MRs, merge first available green)")
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
    print(
        "  - insist vs NI: insist respects priority but may idle; NI maximizes merges"
    )
    print()

    # Save comparison table to file
    comparison_file = os.path.join(reports_dir, "8hour-comparison.txt")
    with open(comparison_file, "w") as f:
        f.write("\n\n")
        f.write("=" * table_width + "\n")
        f.write("POLICY COMPARISON\n")
        f.write("=" * table_width + "\n\n")
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
        f.write("  NI = no-insist (skip running MRs, merge first available green)\n")
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
        f.write("  - Higher same-root pool = more Phase 1 multi-merge candidates\n")
        f.write(
            "  - insist vs NI: insist respects priority but may idle;"
            " NI maximizes merges\n\n"
        )
    log.info(f"Comparison saved to {comparison_file}")
    log.info(f"Per-policy NDJSON files saved to {reports_dir}/")


# ---------------------------------------------------------------------------
# Monte Carlo runner
# ---------------------------------------------------------------------------


def _start_server(
    venv_python: str,
    sim_dir: str,
    scenario: str,
    port: int,
    seed: int,
    metrics_out: str,
    log_path: str,
) -> subprocess.Popen:
    """Start a sim server and return the Popen handle."""
    log_file = open(log_path, "w")  # noqa: SIM115
    proc = subprocess.Popen(
        [
            venv_python,
            "-m",
            "gitlab_hk_sim.cli",
            "serve",
            "--scenario",
            scenario,
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
            "--seed",
            str(seed),
            "--metrics-out",
            metrics_out,
        ],
        stdout=log_file,
        stderr=log_file,
        cwd=sim_dir,
        env={**os.environ, "PYTHONPATH": sim_dir},
    )
    return proc


def _wait_for_server(url: str, retries: int = 10, delay: float = 0.5) -> bool:
    """Poll until server responds or retries exhausted."""
    for _ in range(retries):
        try:
            requests.get(f"{url}/api/v4/user", timeout=2).raise_for_status()
            return True
        except Exception:
            time.sleep(delay)
    return False


def _kill_server(proc: subprocess.Popen) -> None:
    """Terminate server process gracefully."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def run_monte_carlo(args: argparse.Namespace) -> None:
    """Run N Monte Carlo trials with per-trial policy parallelism."""
    if not args.scenario:
        print("ERROR: --monte-carlo requires --scenario")
        sys.exit(1)

    log = logging.getLogger("monte-carlo")
    n_trials = args.monte_carlo
    base_seed = args.base_seed
    base_port = args.base_port

    if args.policies:
        policies = [p.strip() for p in args.policies.split(",")]
    else:
        policies = POLICY_SETS[args.policy_set]

    for p in policies:
        if p not in POLICY_RUNNERS:
            log.error(f"Unknown policy: {p}")
            log.error(f"Available: {list(POLICY_RUNNERS.keys())}")
            sys.exit(1)

    venv_python = os.path.join(os.path.dirname(__file__), ".venv", "bin", "python")
    sim_dir = os.path.abspath(os.path.dirname(__file__) or ".")
    scenario_path = os.path.abspath(args.scenario)

    timestamp = datetime.now().strftime("%m-%d-%y_%I-%M-%p")
    base_reports = os.path.join(sim_dir, "reports", "monte-carlo")
    reports_dir = os.path.join(base_reports, timestamp)
    os.makedirs(reports_dir, exist_ok=True)

    latest_link = os.path.join(base_reports, "latest")
    if os.path.islink(latest_link):
        os.unlink(latest_link)
    os.symlink(timestamp, latest_link)

    log.info(f"Monte Carlo: {n_trials} trials, policies={policies}")
    log.info(f"Base seed: {base_seed}, base port: {base_port}")
    log.info(f"Reports: {reports_dir}")

    all_results: dict[str, list[dict[str, Any]]] = {p: [] for p in policies}

    for trial in range(n_trials):
        trial_seed = base_seed + trial
        trial_dir = os.path.join(reports_dir, f"trial-{trial:02d}")
        os.makedirs(trial_dir, exist_ok=True)

        log.info(f"\n{'=' * 60}")
        log.info(f"TRIAL {trial + 1}/{n_trials} (seed={trial_seed})")
        log.info(f"{'=' * 60}")

        servers: list[tuple[str, subprocess.Popen, int]] = []
        for i, policy in enumerate(policies):
            port = base_port + i
            metrics_out = os.path.join(trial_dir, f"{policy}-metrics.ndjson")
            log_path = os.path.join(trial_dir, f"{policy}-server.log")
            proc = _start_server(
                venv_python,
                sim_dir,
                scenario_path,
                port,
                trial_seed,
                metrics_out,
                log_path,
            )
            servers.append((policy, proc, port))

        time.sleep(2)

        healthy_policies: list[tuple[str, int]] = []
        for policy, proc, port in servers:
            url = f"http://127.0.0.1:{port}"
            if _wait_for_server(url):
                healthy_policies.append((policy, port))
            else:
                log.error(f"  Server for {policy} on port {port} failed to start")
                _kill_server(proc)

        def _run_one(policy: str, port: int, _trial: int = trial) -> tuple[str, dict]:
            url = f"http://127.0.0.1:{port}"
            policy_log = logging.getLogger(f"mc.t{_trial}.{policy}")
            result = run_policy(
                url,
                policy,
                args.limit,
                args.cycles,
                args.ticks_per_cycle,
                policy_log,
            )
            return policy, result

        trial_results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(healthy_policies)) as pool:
            futures = {
                pool.submit(_run_one, policy, port): policy
                for policy, port in healthy_policies
            }
            for future in as_completed(futures):
                policy_name = futures[future]
                try:
                    name, result = future.result()
                    trial_results[name] = result
                    log.info(f"  {name}: {result.get('mrs_merged', 0)} merged")
                except Exception as e:
                    log.error(f"  {policy_name} failed: {e}")

        for _, proc, _ in servers:
            _kill_server(proc)
        time.sleep(0.5)

        for policy in policies:
            if policy in trial_results:
                all_results[policy].append(trial_results[policy])

    _write_monte_carlo_output(all_results, policies, n_trials, reports_dir, log)


def _write_monte_carlo_output(
    all_results: dict[str, list[dict[str, Any]]],
    policies: list[str],
    n_trials: int,
    reports_dir: str,
    log: logging.Logger,
) -> None:
    """Aggregate statistics and write CSV + comparison table."""
    metrics_keys = [
        ("total_time_ticks", "Total Time (ticks)"),
        ("mrs_merged", "MRs Merged"),
        ("throughput_merges_per_tick", "Throughput (m/tick)"),
        ("time_to_first_merge", "First Merge (ticks)"),
        ("time_to_merge_10", "Merge 10 (ticks)"),
        ("avg_merge_interval_ticks", "Avg Interval"),
        ("queue_drain_pct", "Queue Drain %"),
        ("rebase_calls", "Rebases"),
        ("pipelines_created", "Pipelines"),
        ("peak_active_pipelines", "Peak Active"),
        ("duplicate_rebase_total", "Dup Rebases"),
        ("wait_p50", "Wait p50"),
        ("wait_p95", "Wait p95"),
        ("wait_max", "Wait Max"),
        ("starved_mrs", "Starved MRs"),
    ]

    # --- CSV output ---
    csv_path = os.path.join(reports_dir, "monte-carlo-summary.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["trial", "policy"] + [k for k, _ in metrics_keys]
        writer.writerow(header)
        for policy in policies:
            for trial_idx, result in enumerate(all_results[policy]):
                row = [trial_idx, policy]
                for key, _ in metrics_keys:
                    row.append(result.get(key, ""))
                writer.writerow(row)
    log.info(f"CSV written: {csv_path}")

    # --- Aggregate stats ---
    stats: dict[str, dict[str, dict[str, float]]] = {}
    for policy in policies:
        stats[policy] = {}
        results = all_results[policy]
        n = len(results)
        if n == 0:
            continue
        for key, _ in metrics_keys:
            values = [float(r[key]) for r in results if key in r and r[key] is not None]
            if not values:
                stats[policy][key] = {
                    "mean": 0,
                    "stddev": 0,
                    "ci95": 0,
                    "min": 0,
                    "max": 0,
                }
                continue
            mean = sum(values) / len(values)
            variance = (
                sum((v - mean) ** 2 for v in values) / (len(values) - 1)
                if len(values) > 1
                else 0
            )
            stddev = math.sqrt(variance)
            ci95 = 1.96 * stddev / math.sqrt(len(values)) if len(values) > 1 else 0
            stats[policy][key] = {
                "mean": mean,
                "stddev": stddev,
                "ci95": ci95,
                "min": min(values),
                "max": max(values),
            }

    # --- Comparison table ---
    col_w = 16
    table_width = 24 + col_w * len(policies) + len(policies) + 1
    lines: list[str] = []
    lines.append("")
    lines.append("=" * table_width)
    lines.append(f"MONTE CARLO COMPARISON ({n_trials} trials, seed={policies})")
    lines.append("=" * table_width)
    lines.append("Format: mean +/- CI95")
    lines.append("")

    hdr = f"| {'Metric':<22} |"
    hdr += "|".join(f" {p:^{col_w - 2}} " for p in policies) + "|"
    lines.append(hdr)
    lines.append(f"|{'-' * 24}|" + "|".join("-" * col_w for _ in policies) + "|")

    for key, label in metrics_keys:
        cells = []
        for policy in policies:
            s = stats[policy].get(key, {})
            if not s:
                cells.append("N/A")
                continue
            mean = s["mean"]
            ci = s["ci95"]
            if mean == int(mean) and ci == int(ci):
                cells.append(f"{int(mean)}+/-{int(ci)}")
            else:
                cells.append(f"{mean:.2f}+/-{ci:.2f}")
        line = f"| {label:<22} |"
        line += "|".join(f" {c:^{col_w - 2}} " for c in cells) + "|"
        lines.append(line)

    lines.append("")
    lines.append("Legend: mean +/- 95% confidence interval half-width")
    lines.append("  Non-overlapping CIs → statistically significant difference")
    lines.append("")

    output = "\n".join(lines)
    print(output)

    comparison_file = os.path.join(reports_dir, "monte-carlo-comparison.txt")
    with open(comparison_file, "w") as f:
        f.write(output)
    log.info(f"Comparison: {comparison_file}")
    log.info(f"CSV: {csv_path}")
    log.info(f"Trial data: {reports_dir}/trial-*/")


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    log = logging.getLogger("standalone")

    if args.monte_carlo > 0:
        run_monte_carlo(args)
        return

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
