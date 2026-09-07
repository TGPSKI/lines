---
name: calibrate-and-compare
description: "Walk a merge queue from real housekeeping logs to a policy recommendation: measure, build a scenario, tune it against the measurements, compare policies, read the tradeoff. Use when someone wants to know which merge-queue policy to run, or wants to reproduce the calibration arc on their own logs."
metadata:
  author: lines
  version: "1.0"
compatibility: "Python 3.14, a checkout with `pip install -e \".[dev]\"` done, and gitlab-housekeeping logs you can read"
---

# Calibrate and compare

The simulator answers one question: **which merge-queue policy should I run, and
why should I believe the answer?** That takes five phases. Each produces an
artifact the next one consumes, so a phase can be re-run without redoing the
ones before it.

Phases 1–3 build a scenario that behaves like your queue. Phases 4–5 use it to
separate policies. Skipping to phase 4 with a stock scenario is legitimate — you
just get an answer about *that* scenario rather than about your queue.

## Prerequisites

| Check | Command | If it fails |
|---|---|---|
| Environment installed | `python -c "import mqsim, glab_api"` | `pip install -e ".[dev]"` from the repo root |
| Tests pass | `make test` | Stop. A broken checkout makes every later number suspect |
| Logs readable | `head -1 <your log>` | Phases 1–3 need them. Jump to phase 4 with a shipped scenario instead |

Reports land in `reports/`, which is gitignored. Scenarios land in
`scenarios/generated/`, also gitignored, until you decide one is worth keeping.
Nothing in this workflow writes to a branch on its own.

## Entry point

Ask the user one thing:

> **What are we calibrating against — a log file you have, or one of the shipped
> scenarios?**

Everything else is derived from the repo or carried forward between phases.

## Progress detection

Inspect the working tree. File existence is the state machine.

| Evidence on disk | Phase complete |
|---|---|
| `reports/log-analysis/**/*.json` | 1 — logs measured |
| `scenarios/generated/*.yaml` | 2 — scenario built |
| `reports/calibration/*/selected-scenario.yaml` | 3 — tuned and accepted |
| `reports/comparisons/*/comparison-extended.json` | 4 — policies compared |
| `reports/discrimination/*/discrimination-summary.csv` | 5 — separation tested |

A `rejected-scenario.yaml` in a calibration directory means phase 3 ran and its
gates failed. That is a result, not an error: read it before re-running.

## Determine phase

| Detected state | Recommended action |
|---|---|
| Nothing on disk, user has logs | Phase 1 |
| Nothing on disk, no logs | Phase 4 with a shipped scenario |
| Logs measured, no scenario | Phase 2 |
| Scenario built, never tuned | Phase 3 |
| `rejected-scenario.yaml` present | Phase 3, reading the rejection first |
| Accepted scenario, no comparison | Phase 4 |
| Comparison done, policies look tied | Phase 5 |
| Comparison done, a policy clearly wins | Phase 5 to check the win survives variance |

## Route to phase

Present the recommended phase and the reason the repo state implies it. Offer
the neighbours: a user who disagrees with the detection is usually right about
their own intent.

## Design principles

1. **The scenario is the claim.** Phases 1–3 exist to make one defensible.
   A comparison run on an uncalibrated scenario answers a question nobody asked.
2. **Source of truth is the repo.** Scenario shape comes from
   `scenarios/*.yaml`, metric names and directions from `src/mqsim/metrics.json`,
   commands from the `Makefile`. Never invent a flag; read `--help`.
3. **Ask less, infer more.** Tick length, policy set and window all come from
   the scenario or the prior phase's metadata. Ask only for the log path and the
   judgement calls the gates surface.
4. **A failed gate is an output.** Calibration that rejects its candidate has
   told you the model cannot reach your queue's behaviour with those knobs.
   Report it; do not retry with looser gates to get a pass.

## Phase files

| Phase | File | Produces |
|---|---|---|
| 1 | @references/phase-01-measure-logs.md | measured targets from your logs |
| 2 | @references/phase-02-build-scenario.md | a scenario YAML |
| 3 | @references/phase-03-tune-and-gate.md | a tuned, gate-checked scenario |
| 4 | @references/phase-04-compare-policies.md | a policy comparison |
| 5 | @references/phase-05-interpret.md | the tradeoff, stated |
