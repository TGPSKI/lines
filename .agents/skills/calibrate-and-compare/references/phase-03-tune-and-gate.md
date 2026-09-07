---
name: phase-03-tune-and-gate
description: "Tune scenario knobs until the simulator reproduces the measured queue, or report that it cannot"
parent: calibrate-and-compare
---

# Phase 3 — Tune, and let the gates decide

**Carry forward**: the generated scenario, the measured targets, the policy the
targets were measured from.

This phase is the repo's trust mechanism. It grid-searches knobs, validates the
best candidates over a longer run, and accepts or rejects against explicit
gates. A rejection is the honest output when the model cannot reach your queue.

## Step 1 — Pick the calibrated policy

**Decide**: `--policy` defaults to `old-burst`. Calibrate against whichever
policy produced the logs — calibrating against a policy you were not running
makes the targets unreachable by construction.

## Step 2 — Tune

**Generate**:

```bash
make tune-calibration LOGS="<path>" CALIBRATION_POLICY=<policy>
```

Write to: `reports/calibration/<run>/`

## Step 3 — Read the decision, not just the score

**Inspect**: `reports/calibration/<run>/metadata.json`, key `context.decision_gates`.

| Outcome on disk | Meaning | Action |
|---|---|---|
| `selected-scenario.yaml` present | Every gate passed | Continue to phase 4 |
| `rejected-scenario.yaml` present | A gate failed | Read `decision_reason_codes` before anything else |
| Neither | The run did not finish | Check the run log |

For a rejection, report which dimension failed and by how much, quoting
`context.best_*.dimension_error_pct`. The named dimension tells you what the
model cannot reproduce — usually arrival shape or CI duration, not throughput.

**Decide**: a rejection leaves three honest options — widen the knob grid,
accept a scenario that is deliberately not calibrated and say so downstream, or
conclude the queue's behaviour is outside what the model expresses. Loosening
the gates to manufacture a pass is not among them.

## Step 4 — Check the accepted candidate is not degenerate

**Inspect**: compare `context.best_480` against the targets.

| Check | Why |
|---|---|
| Throughput error within tolerance | The headline claim |
| Max single-dimension error | One dimension can hide behind a good weighted score |
| p50 and p95 merge interval both close | Matching the mean while missing the tail models a different queue |

## Step 5 — Promote it, with the proof attached

The tuner's output is in a gitignored report directory. Copying the YAML by hand
leaves the evidence behind.

**Generate**:

```bash
python scripts/promote_scenario.py reports/calibration/<run> --name <name>
```

Write to: `scenarios/<name>.yaml`, carrying a `metadata.proof` block with the
gates, the thresholds and the worst single-dimension error.

**Decide**: read the worst dimension it prints. A scenario that passed on a
relaxed threshold is usable, and every result quoted from it should name that
number. See [docs/golden-scenarios.md](../../../../docs/golden-scenarios.md).

## Checkpoint

**Artifacts**:
- `scenarios/<name>.yaml` — the promoted scenario, proof attached. Committable
- `reports/calibration/<run>/selected-scenario.yaml` — the proofed scenario
- `reports/calibration/<run>/metadata.json` — the gates and their thresholds
- `reports/calibration/<run>/calibration-{grid,validation}.csv` — load these in
  the UI's Experiments tab

**Next**: @phase-04-compare-policies.md
