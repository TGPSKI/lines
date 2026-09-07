---
name: phase-05-interpret
description: "State the tradeoff, and check it survives variance"
parent: calibrate-and-compare
---

# Phase 5 — Say what it means

**Carry forward**: the comparison from phase 4 and the scenario it ran against.

A comparison table is not an answer. This phase turns it into one sentence you
would defend, or into the honest statement that the policies did not separate.

## Step 1 — Find where they actually differ

**Inspect**: the Statistics table, reading only rows where the policies differ.
Rows every policy shares describe the scenario, not the policies.

| Pattern | Reading |
|---|---|
| Throughput ties, rebases differ | Same work, different CI cost. The cheaper policy wins on waste |
| Throughput differs, tail latency differs the other way | A real tradeoff. Name both sides |
| Everything ties | The scenario does not separate these policies. Go to step 3 |

## Step 2 — Draft the tradeoff

State it with the numbers and the condition attached:

> At limit=2 on `<scenario>`, cap+phase1 merges the same 59 MRs as old-burst
> while issuing 143 rebases against 243 — 41% less CI work — at a merge-interval
> p95 of 2070s against 1110s.

Rules: quote values from `run_summary`, never recompute them; name the scenario
and the limit, because both change the answer; state the cost as well as the win.

## Step 3 — Check it survives variance

A single run is one sample of a stochastic scenario.

| Question | Tool |
|---|---|
| Does the difference survive repeated trials? | `make monte-carlo-small`, then the Monte Carlo tab |
| Does it hold across limits? | the Sweep tab |
| Does it hold under stress? | `make discrimination-pass` |

**Decide**: if Monte Carlo's confidence intervals overlap on the metric your
tradeoff rests on, the tradeoff is not established. Say that. An honest
"these did not separate" is a finding.

## Step 4 — Check against the real integration

Optional and the strongest evidence available: `make run-harness` drives the
unmodified upstream `gitlab_housekeeping` against the fake server, so the policy
under test is the real implementation rather than this repo's model of it.

## Checkpoint

**Artifacts**:
- the tradeoff sentence, with scenario and limit named
- `reports/monte-carlo/<run>/monte-carlo-summary.csv` if variance was checked
- `reports/discrimination/<run>/discrimination-summary.csv` if stress was checked

This is the end of the arc. What you have is a claim about which policy to run,
the scenario it holds on, and the evidence a skeptic would ask for.
