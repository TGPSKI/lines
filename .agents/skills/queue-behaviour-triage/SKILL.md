---
name: queue-behaviour-triage
description: "Investigate a simulated merge queue that behaved unexpectedly — throughput collapsed, the policy that should win didn't, calibration gates rejected, or policies tied when they shouldn't. Use when a run's numbers contradict what you expected."
metadata:
  author: lines
  version: "1.0"
compatibility: "A run directory under reports/, and Python with the repo installed"
---

# Queue behaviour triage

Most surprising results here are not bugs. They are a mismatch between the
coordinates you read and the coordinates the run used — a different window, a
different tick length, a different policy than the one you meant. Tier 1 settles
that before anything else, because it resolves more of these than analysis does.

Only enter Tier 2 once Tier 1 says `COORDINATES VERIFIED`.

## Evidence hierarchy

| Label | Source here | Trust |
|---|---|---|
| **DEFINITIVE** | the `run_summary` event in a run's NDJSON, an event stream line, a gate in `metadata.json` | act on it |
| **CONFIG** | the scenario YAML, `metrics.json`, Makefile defaults | what *should* happen |
| **ANECDOTAL** | "I ran it at limit 6", "throughput used to be higher" | verify before using |

The `run_summary` is definitive because the runner wrote it from the events and
nothing recomputes it. A number you derived yourself is not definitive — say so.

---

# Tier 1 — Intake and coordinates

## Intake

Capture the claim exactly, then split it.

| Fact | Source | Tag |
|---|---|---|
| {the claim, quoted} | {who} | STATED / INFERRED / MISSING |

For each INFERRED fact, ask what simplest alternative makes it wrong. For each
MISSING fact, score 1–5 on whether it alone would resolve the case. Anything
scoring 4–5 must be resolved before continuing.

The MISSING fact that scores 5 most often here: **which run directory**.

## Coordinate systems in this domain

These are the mismatches that actually occur. Check them in order.

| Coordinate | Values that get confused | How to resolve |
|---|---|---|
| **Window** | scenario window vs run window | Simulation tab shows both: "Scenario window 24.0h" and per-policy "Run window 10.0h". A 24h scenario run for 10h has 14 idle hours dragging every 24h-normalised rate down |
| **Tick length** | 30s vs 60s | `scenario_meta.tick_seconds`. Every duration doubles or halves with it. Runs predating it carried none and default to 60 |
| **Which run** | the policy you meant vs `filesData[0]` | The Simulation tab's CI Pipeline section names its source run. Statistics compares all loaded runs; Simulation describes one |
| **Limit** | the limit you ran vs the limit you meant | `metadata.json` → `context.limit`. Limit moves results more than any other flag |
| **Which tree** | `reports/`, `reports-migrated/`, a load set | Comparing a run from one tree against remembered numbers from another |
| **Metric name** | `queue_drain` vs `queue_drain_pct`, `trial` vs `run` | `src/mqsim/metrics.json` maps the UI's short names to the canonical ones |
| **Policy name** | `cap+phase1` vs `omm` | Two policies, not one. Both model optimistic multi-merge, but `cap+phase1` batches same-root successes within a cycle and `omm` implements the shipped lead/pending protocol. Check `context.policies` |

## Resolve the reader

What did the reporter actually look at? Not what the system computes — what
they read. If the report does not show it, ask:

> Which run directory, which tab, and which policy column?

## Resolve the system path

```bash
ls -t reports/comparisons | head -3          # which runs exist
python -c "import json;print(json.load(open('reports/comparisons/<run>/metadata.json'))['context'])"
```

That prints the scenario, policies, cycles, limit and ticks per cycle the run
actually used.

## Compare (gate)

```
REPORTER read: {run, tab, policy, window}
RUN used:      {from metadata.json context}
```

| Result | Verdict | Next |
|---|---|---|
| Any coordinate differs | `COORDINATE MISMATCH` | Resolved. Re-read at the right coordinates; skip to Artifact |
| All match | `COORDINATES VERIFIED` | Tier 2 |
| Cannot determine | Stop and ask | — |

## Ground truth

Before closing the gate, read the run's own summary rather than any rendering
of it:

```bash
PYTHONPATH=src python -c "
import json,sys; sys.path.insert(0,'src')
from mqsim.summary import read_run_summary
ev=[json.loads(l) for l in open('<run>/<policy>-metrics.ndjson') if l.strip()]
m=read_run_summary(ev)
print({k:m[k] for k in ('total_time_ticks','tick_seconds','mrs_merged','queue_drain_pct','rebase_calls')})"
```

If this disagrees with what the UI showed, the run predates the summary — run
`make backfill-summaries TREE=<tree>` and re-read.

---

# Tier 2 — Investigation

## Timeline

Build it from the event stream before arguing cause. Events carry ticks, not
wall clock; convert with `tick_seconds`.

| Tick | Event | Source |
|---|---|---|

```bash
python -c "
import json
ev=[json.loads(l) for l in open('<run>/<policy>-metrics.ndjson') if l.strip()]
for e in ev:
    if e.get('event') in ('merge','rebase') : print(e['tick'], e['event'], e.get('mr_iid'))" | head -40
```

## Standing hypotheses

Start from these five. They cover most of what actually happens.

| Hypothesis | When to suspect | Discriminating check | Cost |
|---|---|---|---|
| **Coordinate mismatch** | any surprising number | Tier 1 | cheapest |
| **The scenario does not discriminate** | policies tie on every row | Do the rows that differ differ *anywhere*? Run `make discrimination-pass` | low |
| **Variance, not signal** | one run, small difference | `make monte-carlo-small`; overlapping CIs mean no established difference | medium |
| **CI duration dominates** | throughput identical regardless of policy | Compare `ci_avg` against `avg_merge_interval`. If CI is the bottleneck, scheduling cannot help | low |
| **The limit is the binding constraint** | peak active pipelines pinned at the limit | `peak_active_pipelines` vs `context.limit`; then the Sweep tab | low |

Add domain-specific ones as the evidence suggests. Always carry at least two.

## Discriminate

Name the single cheapest check that eliminates the most hypotheses, its expected
outcomes, and its cost — before running it.

The cheapest check here is almost always **read another metric from the same
`run_summary`**, because it costs nothing and cannot introduce a new coordinate.

| Symptom | Cheapest discriminating read |
|---|---|
| Throughput collapsed | `mrs_merged` vs `total_time_ticks`, then `peak_active_pipelines` vs limit |
| Expected policy lost | `rebase_calls` and `duplicate_rebase_total` — it may have won on waste, not speed |
| Policies tied | `same_root_success_pool_p95` — no overlap pool means multi-merge had nothing to batch |
| Calibration rejected | `context.best_*.dimension_error_pct` — which dimension, and by how much |
| Wait times implausible | `wait_p95` against `total_time_ticks`; a wait longer than the run means arrival ticks are wrong |

## Narrow

Execute, update the table, collapse. One survivor with DEFINITIVE evidence is a
root cause. None surviving means the mental model is wrong — generate outside it.

A calibration rejection is a *result*, not a failure to explain away: it says
the model cannot reach the measured queue with those knobs. Report which
dimension and by how much before proposing anything.

## Contain

There is no production to protect here, but evidence is destructible:

- Do not re-run into the same output directory; runs are timestamped for this reason
- Capture the failing run's `metadata.json` and NDJSON before changing a scenario
- `make clean` deletes reports. Never run it mid-investigation

---

# Artifact

Write it down; the terminal scrolls away.

**File**: `reports/triage/{date}-{slug}.md` (gitignored, like every report)

```markdown
# {symptom}, {date}

## Summary
{2–3 sentences: what was claimed, what was true, why}

## Coordinates
| | Reporter | Run |
|---|---|---|
| run | | |
| policy | | |
| window | | |
| limit | | |
Verdict: COORDINATE MISMATCH | COORDINATES VERIFIED

## Findings
| Claim | Evidence | Label |
|---|---|---|
| | `run_summary.<key>` = value | DEFINITIVE |

## Hypotheses
| Hypothesis | Verdict | Killed by |
|---|---|---|

## Open actions
| Action | Priority |
|---|---|
```

A finding with no DEFINITIVE line under it is a hypothesis. Label it
speculative and say what would settle it.
