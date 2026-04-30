# GitLab Housekeeping Policy Simulator

A policy lab for comparing merge-queue strategies under controlled GitLab-like conditions.

## Purpose

```
Compare merge-queue policies under controlled GitLab-like conditions:
old per-run limit, top-K eligibility, active-cap CI inventory, and Phase 1 optimistic multi-merge.
```

Primary question:

```
What does each policy optimize, and under which queue conditions does it fail?
```

## Architecture

```
scenario YAML
    ↓
stateful fake GitLab API server (FastAPI)
    ↓
real unmodified gitlab-housekeeping integration
    ↓
state mutations: rebase / merge / pipeline / target head
    ↓
metrics NDJSON
    ↓
single-run + policy comparison reports
```

No modification to `run()`. No monkeypatching. No reverse proxy.

The fake GitLab server is compatible with the real `python-gitlab` path used by `qontract-reconcile`.

## Quick Start

```bash
# Install
cd tools/gitlab_housekeeping_perf_sim
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Validate a scenario
python -m gitlab_hk_sim.cli validate --scenario scenarios/mvp-active-cap.yaml

# Start the fake GitLab server
python -m gitlab_hk_sim.cli serve \
  --scenario scenarios/top-k-poisoned-window.yaml \
  --host 127.0.0.1 \
  --port 8080 \
  --metrics-out reports/active-cap/metrics.ndjson

# Point gitlab-housekeeping at the fake server:
#   GITLAB_URL=http://127.0.0.1:8080
#   GITLAB_TOKEN=sim-token

# Generate a report
python -m gitlab_hk_sim.cli report \
  --metrics reports/active-cap/metrics.ndjson \
  --out reports/active-cap/summary.md

# Compare multiple policy runs
python -m gitlab_hk_sim.cli compare \
  --run old=reports/old/metrics.ndjson \
  --run top-k=reports/top-k/metrics.ndjson \
  --run active-cap=reports/active-cap/metrics.ndjson \
  --out reports/comparison.md
```

## Simulator Control API

While the server is running, advance simulation with:

```bash
# Advance pipeline state by one tick
curl -X POST http://127.0.0.1:8080/__sim/tick

# Get computed metrics
curl http://127.0.0.1:8080/__sim/metrics

# Get current state
curl http://127.0.0.1:8080/__sim/state

# Reset to initial scenario
curl -X POST http://127.0.0.1:8080/__sim/reset
```

## Policy Families

| Policy | Controls | Optimizes | Weakness |
|--------|----------|-----------|----------|
| **Old Burst** | Per-run rebase limit | Simple | Active CI can exceed cap over runs |
| **Top-K** | First K eligible | Queue-position purity | Poisoned top window starves CI |
| **Active-Cap** | In-flight CI budget | Steady-state concurrency | No preemption for new high-prio |
| **Phase 1** | Same-root batch merge | MRs per target advance | Requires non-overlapping domains |

## Core Invariant (ADR-019)

```
limit controls steady-state CI concurrency, not per-run rebase bursts.
```

Equivalent framing:
- top-K controls eligibility
- active-cap controls useful in-flight CI inventory
- Phase 1 multi-merge consumes same-root green inventory

## Scenarios

| Scenario | Purpose | Expected Winner |
|----------|---------|-----------------|
| `top-k-favorable.yaml` | Clean top window | Top-K ≈ Active-Cap |
| `top-k-poisoned-window.yaml` | Slow/stuck/failed top window | Active-Cap |
| `high-priority-arrival.yaml` | New critical MR arrives | Top-K (preemption) |
| `long-running-ci.yaml` | All slots occupied by slow CI | Tradeoff visible |
| `external-target-advance.yaml` | Target moves externally | Active-Cap limits blast |
| `clean-nonoverlap-phase1.yaml` | Non-overlapping success pool | Phase 1 |
| `overlap-conflict-phase1.yaml` | Shared tenant domains | Phase 1 limited |

## Key Metrics

- **peak_active_pipelines**: Maximum concurrent CI at any tick
- **same_root_success_pool**: Open MRs with valid green pipelines on current target
- **stale_successes**: Pipelines that succeeded on an old target
- **duplicate_rebases**: MRs rebased more than once before merge
- **average_mrs_per_target_advance**: >1 means Phase 1 batching is working

## Repository Compare Semantics

Critical for integration compatibility:

```
GET /api/v4/projects/:id/repository/compare?from=<mr_sha>&to=<target_head>

commits == []  → housekeeping considers MR rebased
commits != []  → housekeeping considers MR not rebased
```

## Queue visualization (NDJSON)

A single-page UI is meant for **walkthroughs in meetings**:
- A **metrics comparison table** (throughput, CI waste, same-root pool, etc.) for every file you load, with the **active** column highlighted
- A **kanban board**: one card per merge request, moving through **Waiting → Rebase / CI / Ready → Merged** as you scrub or play
- **Playback** with **Play / Pause / Reset**, **speed (0.25×–4×)**, **Loop** (optional), and a **step scrubber**
- A plain-language **“at this time step”** and **“what housekeeping did”** readout
- **Optional (collapsed) detail** sections for the **stacked bar** chart and **swimlane** (brush and swim range controls only inside that section, so they stay tied to the swimlane)

Durations in the main copy are **time steps** (simulator ticks), not raw log field names.

```bash
cd tools/gitlab_housekeeping_perf_sim
make ui
```

In the browser, open or drag-and-drop `metrics.ndjson` (e.g. from `--metrics-out` or `reports/…/metrics.ndjson`). Multiple files open in **tabs** for side-by-side policy comparisons. The brush under the chart only adjusts the **swimlane** window. Chart.js and D3 load from a CDN (first open needs network).

- **File**: [ui/index.html](ui/index.html) (no build step)

## Running Tests

```bash
cd tools/gitlab_housekeeping_perf_sim
PYTHONPATH=. .venv/bin/pytest tests/ -v
```

