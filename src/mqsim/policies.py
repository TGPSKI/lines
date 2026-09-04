"""Policy definitions for the simulator.

Each policy describes how the housekeeping integration decides what to rebase/merge.
These are NOT used to modify the real integration — they describe the behavior shape
for scenario design and metric interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PolicyType(StrEnum):
    OLD_BURST = "old_burst"
    TOP_K = "top_k"
    ACTIVE_CAP = "active_cap"
    PHASE1_MULTI_MERGE = "phase1"


@dataclass
class PolicyConfig:
    """Configuration that describes a policy's parameters.

    This is metadata for scenarios and reports — the actual behavior
    comes from which branch of gitlab-housekeeping is under test.
    """

    name: str
    policy_type: PolicyType
    limit: int = 5
    description: str = ""

    # Policy A: old burst
    # limit resets each run; active CI can exceed intended cap

    # Policy B: top-K
    # only first K sorted MRs are eligible

    # Policy C: active-cap
    # count active, rebase to fill remaining budget

    # Policy D: Phase 1 multi-merge
    # batch non-overlapping same-root successes


# Pre-defined policy configs for common scenarios
POLICY_OLD_BURST = PolicyConfig(
    name="old",
    policy_type=PolicyType.OLD_BURST,
    limit=5,
    description=(
        "Per-run burst: each reconcile run can rebase up to"
        " limit MRs. Limit resets every run."
    ),
)

POLICY_TOP_K = PolicyConfig(
    name="top-k",
    policy_type=PolicyType.TOP_K,
    limit=5,
    description=(
        "Top-K eligibility: only first K sorted MRs are eligible for rebase/CI work."
    ),
)

POLICY_ACTIVE_CAP = PolicyConfig(
    name="active-cap",
    policy_type=PolicyType.ACTIVE_CAP,
    limit=5,
    description=(
        "Active-cap CI inventory: count active candidates,"
        " rebase to fill remaining budget."
    ),
)

POLICY_PHASE1 = PolicyConfig(
    name="phase1",
    policy_type=PolicyType.PHASE1_MULTI_MERGE,
    limit=5,
    description=(
        "Phase 1 optimistic multi-merge: batch non-overlapping same-root successes."
    ),
)
