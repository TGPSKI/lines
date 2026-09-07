---
name: phase-04-compare-policies
description: "Run every policy against one scenario and collect the comparison"
parent: calibrate-and-compare
---

# Phase 4 — Compare the policies

**Carry forward**: the accepted scenario from phase 3, or a shipped scenario if
phases 1–3 were skipped.

## Step 1 — Choose the scenario and say which it is

| Source | Consequence for the claim |
|---|---|
| `selected-scenario.yaml` from phase 3 | Results describe your queue, within the gate tolerances |
| A shipped `scenarios/*.yaml` | Results describe that scenario. Say so whenever you quote them |

## Step 2 — Choose the policy set and the limit

**Inspect**: `run_standalone.py --help` lists the policy sets. `--limit` is the
reconcile-cycle concurrency budget and changes the answer more than any other
flag — it is the axis the Sweep tab exists to explore.

**Decide**: ask for the limit your queue actually runs. Comparing at a limit
nobody uses produces a true statement about a hypothetical.

## Step 3 — Run

**Generate**:

```bash
make compare SCENARIO=<path> POLICY_SET=<set> LIMIT=<n>
```

Write to: `reports/comparisons/<run>/`

Each policy's `*-metrics.ndjson` ends with a `run_summary` event carrying its
~50 metrics. That event is what the UI reads; nothing recomputes it.

## Step 4 — Load it

**Generate**:

```bash
make ui
```

Use **Folder** on the Load tab and pick the run directory. Statistics shows the
comparison; Kanban and Swimlane show what happened.

## Checkpoint

**Artifacts**:
- `reports/comparisons/<run>/*-metrics.ndjson` — one per policy, each with its summary
- `reports/comparisons/<run>/comparison-extended.json`
- `reports/comparisons/<run>/8hour-comparison.txt` — the text table

**Next**: @phase-05-interpret.md
