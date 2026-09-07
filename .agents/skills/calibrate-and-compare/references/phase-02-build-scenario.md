---
name: phase-02-build-scenario
description: "Turn measured targets into a scenario the simulator can run"
parent: calibrate-and-compare
---

# Phase 2 — Build the scenario

**Carry forward from prior phases**: the log path, the project filter, and the
measured targets from phase 1.

## Step 1 — Read the schema from a real scenario

**Inspect**: `scenarios/large-mixed-queue-advanced.yaml` is the richest shipped
example — arrivals, per-MR CI durations, failures, force-merges. `docs/scenario-schema.md`
documents the fields. Top level is `metadata`, `project`, `merge_requests`,
`sha_pools`.

Do not hand-write a scenario for this phase. The generator below produces one
that already matches the schema.

## Step 2 — Generate from the logs

**Generate**:

```bash
make calibrate-scenario LOGS="<path> [<path> ...]"
```

Write to: `scenarios/generated/`

This reads the same log phase 1 measured, through the same adapter. If it exits
with `no parseable log lines`, no adapter matched the file — see
[docs/logs-to-scenario.md](../../../../docs/logs-to-scenario.md), which also
lists what the generator derives from the log and what it fabricates.

If phase 1 reported `cycle: 0`, check `tick_seconds` in the emitted scenario
before continuing: with no cycle marker the loop interval is inferred from
event spacing and usually lands on the 30s floor, which shortens the scenario
window by the same ratio. Pass `--tick-seconds-override` with the interval the
queue actually reconciles at.

The generator emits `metadata.calibration_targets` into the scenario. That block
is what phase 3 tunes against and what the UI later shows as "calibration target
vs this run". A scenario without it can still be simulated but cannot be scored.

## Step 3 — Validate

**Generate**:

```bash
make validate
```

| Status | Action |
|---|---|
| All scenarios valid | Continue |
| The new scenario fails | Report the loader's message; it names the field |

## Step 4 — Sanity-check the shape

**Inspect**: load the scenario and compare against phase 1's measurements.

| Check | Why it matters |
|---|---|
| Total MRs vs merges seen in the log window | An order-of-magnitude gap means the window and the queue depth disagree |
| Arrivals spread across the window | All arrivals at t=0 models a drain, not a queue |
| CI duration range vs measured pipeline durations | CI length dominates every policy's behaviour |

## Checkpoint

**Artifacts**:
- `scenarios/generated/<name>.yaml` (local; move it to `scenarios/` only if it
  is synthetic or you intend to publish it)

**Next**: @phase-03-tune-and-gate.md
